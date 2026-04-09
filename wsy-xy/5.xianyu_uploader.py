from pathlib import Path
import json
import re
from playwright.sync_api import sync_playwright
from typing import Optional

def upload_to_xianyu(product):
    images_input = product.get("images", [])
    if isinstance(images_input, (str, Path)):
        images_input = [images_input]

    images: list[str] = []
    base_dir: Path | None = None
    for p in images_input:
        path = Path(p)
        if path.is_dir():
            if base_dir is None:
                base_dir = path
            for ext in (".jpg", ".jpeg", ".png", ".webp"):
                images.extend([str(x.resolve()) for x in sorted(path.glob(f"*{ext}"))])
        elif path.exists():
            images.append(str(path.resolve()))

    title = str(product.get("title", "")).strip()
    description = str(product.get("description", "")).strip()
    price_value = product.get("price", 60)
    price = str(price_value).strip()
    category_path = product.get("category_path") or []
    auto_publish = bool(product.get("auto_publish", True))
    close_after_publish = bool(product.get("close_after_publish", False))
    keep_open_ms = int(product.get("keep_open_ms", 600000))

    if base_dir is not None and (not title or not description or not str(product.get("price", "")).strip()):
        info_path = base_dir / "info.json"
        if info_path.exists():
            try:
                info = json.loads(info_path.read_text(encoding="utf-8"))
                if not title:
                    title = str(info.get("title", "")).strip() or title
                if not description:
                    description = str(info.get("description", "")).strip() or description
                if (not str(product.get("price", "")).strip()) and info.get("price") is not None:
                    price = str(info.get("price")).strip() or price
            except Exception:
                pass

    def get_active_publish_page(ctx) -> Optional[object]:
        if not ctx.pages:
            return None
        pg = ctx.pages[-1]
        try:
            pg.wait_for_url(re.compile(r"publish"), timeout=5000)
        except Exception:
            pass
        return pg

    def ensure_page(ctx, current):
        if current is not None and not current.is_closed():
            return current
        new_page = get_active_publish_page(ctx)
        if new_page:
            return new_page
        if ctx.pages:
            return ctx.pages[-1]
        return current

    uploaded_count_expr = """() => {
      const imgs = Array.from(document.querySelectorAll('div.upload-item--VvK_FTdU img'));
      const ok = imgs.filter(img => {
        const src = (img.getAttribute('src') || '').trim();
        if (!src) return false;
        if (src.includes('tps-36-36')) return false;
        if (src.includes('tps-48-48')) return false;
        if (src.includes('110x10000')) return false;
        return true;
      });
      return ok.length;
    }"""

    def wait_images_ready(pg, min_count=1, timeout_ms=90000):
        try:
            pg.wait_for_function(f"() => ({uploaded_count_expr})() >= {min_count}", timeout=timeout_ms)
            return True
        except Exception:
            return False

    def ensure_images_uploaded(pg, imgs):
        if not imgs:
            return True
        pg = ensure_page(context, pg)
        try:
            existing = pg.evaluate(uploaded_count_expr)
        except Exception:
            existing = 0
        if existing and existing > 0:
            return True
        try:
            file_input_selector = 'input[type="file"][name="file"], input[type="file"]'
            pg.wait_for_selector(file_input_selector, state="attached", timeout=20000)
            pg.locator(file_input_selector).first.set_input_files(imgs)
        except Exception:
            try:
                with pg.expect_file_chooser(timeout=10000) as fc_info:
                    pg.locator(upload_entry_selector).first.click()
                fc_info.value.set_files(imgs)
            except Exception:
                return False
        return wait_images_ready(pg, min_count=1, timeout_ms=90000)

    with sync_playwright() as p:
        browser = p.chromium.launch(headless=False)
        state_path = Path("xianyu_state.json")
        if state_path.exists():
            context = browser.new_context(storage_state=str(state_path))
        else:
            context = browser.new_context()
        page = context.new_page()
        page.goto("https://www.goofish.com/publish", wait_until="load")

        upload_entry_selector = 'div.upload-item--VvK_FTdU span:has-text("添加首图")'
        try:
            page.wait_for_selector(upload_entry_selector, timeout=20000)
        except Exception:
            print("请在浏览器中完成登录，待进入发布页后自动继续")
            # 登录过程中页面可能重定向/打开新页，保持 page 引用最新
            try:
                context.wait_for_event("page", timeout=300000)
            except Exception:
                pass
            new_page = get_active_publish_page(context)
            if new_page:
                page = new_page
            page.wait_for_selector(upload_entry_selector, timeout=300000)
        # 登录成功，持久化登录态
        try:
            context.storage_state(path=str(state_path.resolve()))
        except Exception:
            pass

        if images:
            try:
                page.keyboard.press("Escape")
            except Exception:
                pass
            ok = ensure_images_uploaded(page, images)
            if not ok:
                print("图片上传失败: 无法确认上传完成")
        # 若页面在上传或其他操作时被 SPA 重载，尝试恢复最新页面引用
        page = ensure_page(context, page)

        try:
            page = ensure_page(context, page)
            ti = page.locator('input[placeholder*="标题"], input[placeholder*="宝贝标题"], input[placeholder*="起个标题"]').first
            if ti.count() == 0:
                ti = page.get_by_role("textbox").first
            ti.fill(title)
            try:
                page.wait_for_timeout(300)
            except Exception:
                pass
        except Exception as e:
            print(f"标题填写失败: {e}")

        try:
            page = ensure_page(context, page)
            ed = page.locator('[contenteditable="true"][data-placeholder*="描述"], .editor--MtHPS94K').first
            ed.fill(description)
            try:
                page.wait_for_timeout(300)
            except Exception:
                pass
        except Exception as e:
            print(f"描述填写失败: {e}")

        price_filled = False
        page = ensure_page(context, page)
        try:
            pr = page.locator('input[placeholder="0.00"], input[placeholder*="价格"], input[placeholder*="¥"], input[type="number"]').first
            pr.scroll_into_view_if_needed()
            pr.click()
            try:
                pr.fill(price)
            except Exception:
                try:
                    pr.press("Control+A")
                except Exception:
                    pass
                pr.type(price)
            price_filled = True
        except Exception:
            pass
        if not price_filled:
            try:
                pr2 = page.get_by_placeholder(re.compile(r"^0\.00$|价|¥|￥")).first
                pr2.scroll_into_view_if_needed()
                pr2.click()
                try:
                    pr2.fill(price)
                except Exception:
                    try:
                        pr2.press("Control+A")
                    except Exception:
                        pass
                    pr2.type(price)
                price_filled = True
            except Exception:
                pass
        if not price_filled:
            try:
                pr3 = page.locator('label:has-text("价格")').locator('input').first
                pr3.scroll_into_view_if_needed()
                pr3.click()
                try:
                    pr3.fill(price)
                except Exception:
                    try:
                        pr3.press("Control+A")
                    except Exception:
                        pass
                    pr3.type(price)
                price_filled = True
            except Exception:
                pass
        if not price_filled:
            try:
                container = page.locator('div:has-text("价格")').filter(has=page.locator("input")).first
                container.scroll_into_view_if_needed()
                inp = container.locator("input").first
                inp.click()
                try:
                    inp.fill(price)
                except Exception:
                    try:
                        inp.press("Control+A")
                    except Exception:
                        pass
                    inp.type(price)
                price_filled = True
            except Exception as e:
                print(f"价格填写失败: {e}")
        try:
            page.wait_for_timeout(300)
        except Exception:
            pass

        for cat in category_path:
            try:
                page.locator(f'span:has-text("{cat}")').first.click(timeout=3000)
            except Exception:
                break

        page = ensure_page(context, page)
        try:
            # 优先读取地址容器 addressWrap--qO_6KVXf（你提供的 DOM）
            addr_wrap = page.locator('div.addressWrap--qO_6KVXf').first
            addr_wrap.scroll_into_view_if_needed()
            addr_text = ""
            try:
                addr_text = (addr_wrap.locator('div.address--WJCNfSkh').first.inner_text(timeout=2000) or "").strip()
            except Exception:
                pass
            if addr_text:
                pass
            else:
                # 兼容旧结构：点击展开位置选择面板，选中附近地址第一条
                loc = page.locator('div.loactionWrap--Dzyomb_b, div.addressWrap--qO_6KVXf').first
                loc.scroll_into_view_if_needed()
                loc.click()
                page.wait_for_selector('div.addressList--kPzQ8sU1 .addressItem--aP7Ndnv8, div.addressListWrap--VWoDYwdj .addressItem--aP7Ndnv8', timeout=20000)
                addr = page.locator('div.addressList--kPzQ8sU1 .addressItem--aP7Ndnv8, div.addressListWrap--VWoDYwdj .addressItem--aP7Ndnv8').first
                addr.scroll_into_view_if_needed()
                addr.click()
        except Exception as e:
            print(f"宝贝所在地选择失败: {e}")

        # 自动点击“发布”
        if auto_publish:
            try:
                page = ensure_page(context, page)
                if images:
                    ensure_images_uploaded(page, images)
                pub_btn = page.locator("button:has-text('发布')").first
                pub_btn.scroll_into_view_if_needed()
                try:
                    page.wait_for_function(
                        """(btn) => !btn.disabled && !btn.className.includes('publish-button-disabled')""",
                        pub_btn,
                        timeout=30000,
                    )
                except Exception:
                    pass
                pub_btn.click()
                try:
                    page.wait_for_load_state("networkidle", timeout=20000)
                except Exception:
                    pass
            except Exception as e:
                print(f"发布按钮点击失败: {e}")

        try:
            context.storage_state(path=str(state_path.resolve()))
        except Exception:
            pass

        if auto_publish:
            print("已尝试自动发布，如未成功请在浏览器中手动确认")
        else:
            print("已完成信息填充，请在浏览器中手动点击发布")
        try:
            if keep_open_ms > 0:
                page.wait_for_timeout(keep_open_ms)
        except Exception:
            pass
        if close_after_publish:
            try:
                browser.close()
            except Exception:
                pass

if __name__ == "__main__":
    sample = {
        "title": "示例标题",
        "description": "aaa",
        "price": 99.0,
        "images": r"products\商品ID_25015699",
        #"category_path": []
    }
    upload_to_xianyu(sample)
