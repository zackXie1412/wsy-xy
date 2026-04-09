from __future__ import annotations

import json
import os
import re
import threading
import time
import uuid
from dataclasses import dataclass, field
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from pathlib import Path
from typing import Any
from urllib.parse import parse_qs, urlparse

from importlib.machinery import SourceFileLoader
from importlib.util import module_from_spec, spec_from_loader


_ROOT = Path(__file__).resolve().parent


def _load_module(module_name: str, file_path: Path):
    loader = SourceFileLoader(module_name, str(file_path))
    spec = spec_from_loader(module_name, loader)
    module = module_from_spec(spec)
    loader.exec_module(module)
    return module


_crawler = _load_module("_crawler", _ROOT / "1.crawler.py")
_downloader = _load_module("_downloader", _ROOT / "2.image_downloader.py")
_descgen = _load_module("_descgen", _ROOT / "3.description_generator.py")
_infosaver = _load_module("_infosaver", _ROOT / "4.info_saver.py")
_uploader = _load_module("_uploader", _ROOT / "5.xianyu_uploader.py")


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


def _run_pipeline(product_url: str) -> dict[str, Any]:
    product_dir = _ROOT / "products" / _infer_product_dir_name(product_url)

    _downloader.download_images(product_url)

    images = _collect_images(product_dir)
    if not images:
        raise RuntimeError(f"未在 {product_dir} 找到任何图片文件")

    title = product_dir.name
    description = _descgen.generate_description(title)
    info = {"title": title, "price": 60.0, "original_price": None}

    product_dir.mkdir(parents=True, exist_ok=True)
    old_cwd = os.getcwd()
    try:
        os.chdir(str(product_dir))
        _infosaver.save_product_info(info)
    finally:
        os.chdir(old_cwd)

    product = {
        "title": title,
        "description": description,
        "price": info["price"],
        "images": str(product_dir),
        "category_path": [],
        "auto_publish": True,
    }
    _uploader.upload_to_xianyu(product)
    return {"product_dir": str(product_dir), "images": images, "info": info}


@dataclass
class Job:
    id: str
    status: str = "queued"
    created_at: float = field(default_factory=time.time)
    updated_at: float = field(default_factory=time.time)
    result: dict[str, Any] | None = None
    error: str | None = None
    logs: list[str] = field(default_factory=list)

    def log(self, msg: str):
        ts = time.strftime("%H:%M:%S", time.localtime())
        self.logs.append(f"[{ts}] {msg}")
        self.updated_at = time.time()


_jobs: dict[str, Job] = {}
_jobs_lock = threading.Lock()


def _new_job() -> Job:
    job_id = uuid.uuid4().hex
    job = Job(id=job_id)
    with _jobs_lock:
        _jobs[job_id] = job
    return job


def _get_job(job_id: str) -> Job | None:
    with _jobs_lock:
        return _jobs.get(job_id)


def _run_job(job: Job, payload: dict[str, Any]):
    job.status = "running"
    job.updated_at = time.time()

    try:
        product_url = str(payload.get("product_url") or "").strip()
        if not product_url:
            products = _crawler.crawl_products()
            if not products:
                raise ValueError("product_url 不能为空")
            product_url = str(products[0].get("url") or "").strip()
            if not product_url:
                raise ValueError("product_url 不能为空")

        job.log("开始执行流水线")
        job.log(f"URL: {product_url}")
        result = _run_pipeline(product_url)

        job.result = result
        job.status = "done"
        job.log("流水线执行完成")
    except Exception as e:
        job.error = str(e)
        job.status = "error"
        job.log(f"流水线执行失败: {e}")
    finally:
        job.updated_at = time.time()


def _read_index_html() -> str:
    html_path = _ROOT / "7.web_ui.html"
    return html_path.read_text(encoding="utf-8")


class Handler(BaseHTTPRequestHandler):
    def _send(self, status: int, body: bytes, content_type: str):
        self.send_response(status)
        self.send_header("Content-Type", content_type)
        self.send_header("Cache-Control", "no-store")
        self.send_header("Pragma", "no-cache")
        self.send_header("Expires", "0")
        self.send_header("X-UI-Source", "7.web_ui.html")
        self.send_header("Content-Length", str(len(body)))
        self.end_headers()
        self.wfile.write(body)

    def do_GET(self):
        parsed = urlparse(self.path)
        if parsed.path == "/":
            self._send(200, _read_index_html().encode("utf-8"), "text/html; charset=utf-8")
            return
        if parsed.path == "/version":
            html_path = _ROOT / "7.web_ui.html"
            try:
                st = html_path.stat()
                payload = {"html": str(html_path), "size": st.st_size, "mtime": st.st_mtime}
            except Exception as e:
                payload = {"html": str(html_path), "error": str(e)}
            self._send(200, json.dumps(payload, ensure_ascii=False).encode("utf-8"), "application/json; charset=utf-8")
            return
        if parsed.path == "/status":
            qs = parse_qs(parsed.query)
            job_id = (qs.get("id") or [""])[0]
            job = _get_job(job_id)
            if not job:
                self._send(404, b'{"error":"not_found"}', "application/json")
                return
            payload = {
                "id": job.id,
                "status": job.status,
                "logs": job.logs,
                "result": job.result,
                "error": job.error,
            }
            self._send(200, json.dumps(payload, ensure_ascii=False).encode("utf-8"), "application/json; charset=utf-8")
            return
        self._send(404, b"not found", "text/plain; charset=utf-8")

    def do_POST(self):
        parsed = urlparse(self.path)
        if parsed.path != "/run":
            self._send(404, b"not found", "text/plain; charset=utf-8")
            return
        length = int(self.headers.get("Content-Length", "0"))
        raw = self.rfile.read(length) if length > 0 else b"{}"
        try:
            payload = json.loads(raw.decode("utf-8"))
        except Exception:
            self._send(400, b"invalid json", "text/plain; charset=utf-8")
            return

        job = _new_job()
        job.log("任务已创建")
        t = threading.Thread(target=_run_job, args=(job, payload), daemon=True)
        t.start()
        self._send(200, json.dumps({"job_id": job.id}).encode("utf-8"), "application/json; charset=utf-8")

    def log_message(self, format, *args):
        return


def main(host: str = "127.0.0.1", port: int = 5173):
    env_port = os.getenv("WEB_UI_PORT")
    if env_port:
        try:
            port = int(env_port)
        except Exception:
            pass
    server = ThreadingHTTPServer((host, port), Handler)
    print(f"Open http://{host}:{port}/")
    server.serve_forever()


if __name__ == "__main__":
    main()
