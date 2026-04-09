from __future__ import annotations

import argparse
import os
import re
import time
from pathlib import Path
from importlib.machinery import SourceFileLoader
from importlib.util import module_from_spec, spec_from_loader

from playwright.sync_api import sync_playwright, TimeoutError as PlaywrightTimeoutError


_ROOT = Path(__file__).resolve().parent


class CancelledError(Exception):
    pass


def _load_module(module_name: str, file_path: Path):
    loader = SourceFileLoader(module_name, str(file_path))
    spec = spec_from_loader(module_name, loader)
    module = module_from_spec(spec)
    loader.exec_module(module)
    return module


_downloader = _load_module("_downloader", _ROOT / "2.image_downloader.py")
_infosaver = _load_module("_infosaver", _ROOT / "4.info_saver.py")
_uploader = _load_module("_uploader", _ROOT / "5.xianyu_uploader.py")

def _generate_description(title: str, image_paths: list[str], logger=None) -> str:
    mod = _load_module(f"_descgen_{time.time_ns()}", _ROOT / "3.description_generator.py")
    try:
        return mod.generate_description(title, image_paths=image_paths, target_chars=40, logger=logger)
    except TypeError:
        return mod.generate_description(title, image_paths=image_paths, target_chars=40)


def _infer_product_dir_name(product_url: str) -> str:
    m = re.search(r"id=(\d+)", product_url)
    if m:
        return f"商品ID_{m.group(1)}"
    safe = re.sub(r"[^a-zA-Z0-9]", "_", product_url.split(".com/")[-1])
    return safe[:50] or "unknown"


def _collect_images(product_dir: Path) -> list[str]:
    if not product_dir.exists():
        return []

    def key(p: Path):
        m = re.search(r"(\d+)", p.stem)
        return int(m.group(1)) if m else 10**9

    files: list[Path] = []
    for ext in (".jpg", ".jpeg", ".png", ".webp"):
        files.extend(product_dir.glob(f"*{ext}"))
    files = sorted({p.resolve() for p in files}, key=key)
    return [str(p) for p in files]


def crawl(listing_url: str, *, max_items: int | None = None, timeout_ms: int = 60000, should_stop=None) -> list[str]:
    with sync_playwright() as p:
        headless = os.getenv("WSY_HEADLESS", "").strip() == "1"
        browser = p.chromium.launch(headless=headless)
        state_path = Path("wsy_state.json")
        if state_path.exists():
            context = browser.new_context(storage_state=str(state_path))
        else:
            context = browser.new_context()
        page = context.new_page()
        page.goto(listing_url, wait_until="load", timeout=timeout_ms)
        try:
            page.wait_for_selector('a[href*="item.htm?id="]', timeout=5000)
        except PlaywrightTimeoutError:
            pass

        login_timeout_ms = int(os.getenv("WSY_LOGIN_TIMEOUT_MS", "300000"))
        try:
            page.wait_for_selector('a[href*="item.htm?id="]', timeout=login_timeout_ms)
        except PlaywrightTimeoutError:
            pass

        try:
            context.storage_state(path=str(state_path.resolve()))
        except Exception:
            pass

        hrefs: list[str] = []
        seen: set[str] = set()
        stable_rounds = 0
        for _ in range(60):
            if should_stop and should_stop():
                browser.close()
                raise CancelledError("cancelled")
            batch = page.evaluate(
                """() => {
                  const anchors = Array.from(document.querySelectorAll('a[href*="item.htm?id="]'));
                  return anchors.map(a => a.getAttribute('href') || '').filter(Boolean);
                }"""
            )
            for href in batch:
                if href not in seen:
                    seen.add(href)
                    hrefs.append(href)
                    if max_items is not None and len(hrefs) >= max_items:
                        break
            if max_items is not None and len(hrefs) >= max_items:
                break
            prev_len = len(hrefs)
            page.evaluate("() => window.scrollBy(0, Math.max(600, window.innerHeight))")
            page.wait_for_timeout(800)
            if len(hrefs) == prev_len:
                stable_rounds += 1
                if stable_rounds >= 3:
                    break
            else:
                stable_rounds = 0

        browser.close()

    seen = set()
    urls: list[str] = []
    for href in hrefs:
        if href.startswith("//"):
            href = "https:" + href
        if href.startswith("/"):
            href = "https://www.wsy.com" + href
        if not href.startswith("http"):
            continue
        if href in seen:
            continue
        seen.add(href)
        urls.append(href)
        if max_items is not None and len(urls) >= max_items:
            break
    return urls


def process_one(product_url: str, *, price: float = 60.0, logger=None, should_stop=None) -> dict:
    if logger is None:
        logger = print
    if should_stop and should_stop():
        raise CancelledError("cancelled")
    product_dir = _ROOT / "products" / _infer_product_dir_name(product_url)
    logger(f"下载图片: {product_url}")

    meta = _downloader.download_images(product_url) or {}
    if should_stop and should_stop():
        raise CancelledError("cancelled")

    images = _collect_images(product_dir)
    if not images:
        raise RuntimeError(f"未在 {product_dir} 找到任何图片文件")

    title = str(meta.get("title") or product_dir.name).strip()
    logger(f"生成描述: {title}")
    description = _generate_description(title, images, logger=logger)
    if should_stop and should_stop():
        raise CancelledError("cancelled")
    extracted_price = meta.get("price")
    final_price = float(price)
    try:
        if extracted_price is not None and str(extracted_price).strip() != "":
            final_price = float(extracted_price)
            logger(f"提取到价格: {final_price}")
    except Exception:
        pass
    info = {
        "title": title,
        "price": final_price,
        "original_price": None,
        "description": description,
        "product_url": product_url,
        "images": images,
    }

    product_dir.mkdir(parents=True, exist_ok=True)
    old_cwd = os.getcwd()
    try:
        os.chdir(str(product_dir))
        logger(f"保存 info.json: {product_dir}")
        _infosaver.save_product_info(info)
    finally:
        os.chdir(old_cwd)

    product = {
        "title": title,
        "description": info["description"],
        "price": info["price"],
        "images": str(product_dir),
        "category_path": [],
        "auto_publish": True,
        "keep_open_ms": 2000,
        "close_after_publish": True,
    }
    logger(f"上传并发布: {title}")
    _uploader.upload_to_xianyu(product)

    return {"product_url": product_url, "product_dir": str(product_dir), "images": images}


def run_batch(listing_url: str, *, max_items: int | None = None, price: float = 60.0, sleep_s: float = 1.0, logger=None, should_stop=None) -> list[dict]:
    if logger is None:
        logger = print
    product_urls = crawl(listing_url, max_items=max_items, should_stop=should_stop)
    results: list[dict] = []
    for idx, url in enumerate(product_urls, start=1):
        if should_stop and should_stop():
            results.append({"status": "cancelled"})
            logger("已停止任务")
            return results
        logger(f"[{idx}/{len(product_urls)}] 开始处理: {url}")
        try:
            result = process_one(url, price=price, logger=logger, should_stop=should_stop)
            results.append({"status": "done", **result})
            logger(f"[{idx}/{len(product_urls)}] 完成: {url}")
        except CancelledError:
            results.append({"status": "cancelled"})
            logger("已停止任务")
            return results
        except Exception as e:
            results.append({"status": "error", "product_url": url, "error": str(e)})
            logger(f"[{idx}/{len(product_urls)}] 失败: {url} -> {e}")
        if sleep_s > 0:
            time.sleep(sleep_s)
    return results


def main():
    parser = argparse.ArgumentParser()
    parser.add_argument("--listing-url", required=True)
    parser.add_argument("--max-items", type=int, default=0)
    parser.add_argument("--price", type=float, default=60.0)
    parser.add_argument("--sleep", type=float, default=1.0)
    args = parser.parse_args()

    max_items = args.max_items if args.max_items > 0 else None
    run_batch(args.listing_url, max_items=max_items, price=args.price, sleep_s=args.sleep)


if __name__ == "__main__":
    main()
