from __future__ import annotations

import json
import os
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


def _load_batch_module():
    file_path = _ROOT / "8.fianl.py"
    loader = SourceFileLoader("_batch", str(file_path))
    spec = spec_from_loader("_batch", loader)
    module = module_from_spec(spec)
    loader.exec_module(module)
    return module


_batch = _load_batch_module()


@dataclass
class Job:
    id: str
    status: str = "queued"
    created_at: float = field(default_factory=time.time)
    updated_at: float = field(default_factory=time.time)
    result: Any | None = None
    error: str | None = None
    logs: list[str] = field(default_factory=list)
    cancel: threading.Event = field(default_factory=threading.Event)

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


def _read_index_html() -> str:
    html_path = _ROOT / "8.fianl.html"
    return html_path.read_text(encoding="utf-8")


def _run_job(job: Job, payload: dict[str, Any]):
    job.status = "running"
    job.updated_at = time.time()
    try:
        listing_url = str(payload.get("listing_url") or "").strip()
        if not listing_url:
            raise ValueError("listing_url 不能为空")
        max_items = int(payload.get("max_items") or 0)
        price = float(payload.get("price") or 60.0)
        sleep_s = float(payload.get("sleep_s") or 1.0)
        job.log(f"列表页: {listing_url}")
        job.log(f"max_items: {max_items}")
        job.log(f"price: {price}")
        job.log(f"sleep_s: {sleep_s}")
        job.result = _batch.run_batch(
            listing_url,
            max_items=max_items if max_items > 0 else None,
            price=price,
            sleep_s=sleep_s,
            logger=job.log,
            should_stop=job.cancel.is_set,
        )
        if job.cancel.is_set():
            job.status = "cancelled"
            job.log("批量执行已停止")
        else:
            job.status = "done"
            job.log("批量执行完成")
    except Exception as e:
        job.error = str(e)
        job.status = "error"
        job.log(f"批量执行失败: {e}")
    finally:
        job.updated_at = time.time()


class Handler(BaseHTTPRequestHandler):
    def _send(self, status: int, body: bytes, content_type: str):
        self.send_response(status)
        self.send_header("Content-Type", content_type)
        self.send_header("Cache-Control", "no-store")
        self.send_header("Pragma", "no-cache")
        self.send_header("Expires", "0")
        self.send_header("X-UI-Source", "8.fianl.html")
        self.send_header("Content-Length", str(len(body)))
        self.end_headers()
        self.wfile.write(body)

    def do_GET(self):
        parsed = urlparse(self.path)
        if parsed.path == "/":
            self._send(200, _read_index_html().encode("utf-8"), "text/html; charset=utf-8")
            return
        if parsed.path == "/version":
            html_path = _ROOT / "8.fianl.html"
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
            payload = {"id": job.id, "status": job.status, "logs": job.logs, "result": job.result, "error": job.error}
            self._send(200, json.dumps(payload, ensure_ascii=False).encode("utf-8"), "application/json; charset=utf-8")
            return
        self._send(404, b"not found", "text/plain; charset=utf-8")

    def do_POST(self):
        parsed = urlparse(self.path)
        if parsed.path not in ("/run", "/stop"):
            self._send(404, b"not found", "text/plain; charset=utf-8")
            return
        length = int(self.headers.get("Content-Length", "0"))
        raw = self.rfile.read(length) if length > 0 else b"{}"
        try:
            payload = json.loads(raw.decode("utf-8"))
        except Exception:
            self._send(400, b"invalid json", "text/plain; charset=utf-8")
            return
        if parsed.path == "/stop":
            job_id = str(payload.get("job_id") or "").strip()
            job = _get_job(job_id)
            if not job:
                self._send(404, b'{"error":"not_found"}', "application/json; charset=utf-8")
                return
            job.cancel.set()
            if job.status == "running":
                job.status = "stopping"
            job.log("收到停止请求")
            self._send(200, json.dumps({"ok": True}).encode("utf-8"), "application/json; charset=utf-8")
            return

        job = _new_job()
        job.log("任务已创建")
        t = threading.Thread(target=_run_job, args=(job, payload), daemon=True)
        t.start()
        self._send(200, json.dumps({"job_id": job.id}).encode("utf-8"), "application/json; charset=utf-8")

    def log_message(self, format, *args):
        return


def main(host: str = "127.0.0.1", port: int = 5176):
    env_port = os.getenv("BATCH_UI_PORT")
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
