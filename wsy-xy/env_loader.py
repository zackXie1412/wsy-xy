from __future__ import annotations

import os
from pathlib import Path


def load_env(file_name: str = ".env") -> None:
    path = Path(__file__).resolve().parent / file_name
    if not path.exists():
        return

    try:
        content = path.read_text(encoding="utf-8")
    except Exception:
        return

    for raw_line in content.splitlines():
        line = raw_line.strip()
        if not line:
            continue
        if line.startswith("#"):
            continue
        if "=" not in line:
            continue
        key, value = line.split("=", 1)
        key = key.strip()
        value = value.strip().strip("'").strip('"')
        if not key:
            continue
        if key in os.environ and os.environ[key].strip():
            continue
        os.environ[key] = value

