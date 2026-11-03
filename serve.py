#!/usr/bin/env python3
"""在本機預覽 docs/ 的靜態網站。

用法：
    python serve.py               # http://127.0.0.1:8000
    python serve.py 8080 --open   # 指定埠號並開啟瀏覽器
"""

from __future__ import annotations

import argparse
import http.server
import socket
import sys
import threading
import webbrowser
from pathlib import Path

ROOT = Path(__file__).resolve().parent
DOCS = ROOT / "docs"


def make_handler(directory: Path):
    class Handler(http.server.SimpleHTTPRequestHandler):
        def __init__(self, *args, **kwargs):
            super().__init__(*args, directory=str(directory), **kwargs)

        def end_headers(self):
            self.send_header("Cache-Control", "no-store, max-age=0")
            super().end_headers()

        def log_message(self, format, *args):
            print(f"  {self.address_string()} — {format % args}")

    return Handler


def choose_port(host: str, preferred: int) -> int:
    for port in range(preferred, preferred + 20):
        with socket.socket(socket.AF_INET, socket.SOCK_STREAM) as probe:
            probe.setsockopt(socket.SOL_SOCKET, socket.SO_REUSEADDR, 1)
            try:
                probe.bind((host, port))
            except OSError:
                continue
            return port
    raise SystemExit(f"找不到可用的埠號（已嘗試 {preferred}–{preferred + 19}）")


def main(argv: list[str] | None = None) -> int:
    for stream in (sys.stdout, sys.stderr):
        if hasattr(stream, "reconfigure") and not stream.isatty():
            try:
                stream.reconfigure(encoding="utf-8", errors="replace")
            except (AttributeError, OSError):
                pass

    parser = argparse.ArgumentParser(description="預覽 build.py 產生的 docs/ 網站。")
    parser.add_argument("port", nargs="?", type=int, default=8000, help="偏好埠號（預設 8000）")
    parser.add_argument("--host", default="127.0.0.1", help="監聽位址（預設 127.0.0.1）")
    parser.add_argument("--open", action="store_true", help="啟動後自動開啟瀏覽器")
    args = parser.parse_args(argv)

    index = DOCS / "index.html"
    if not index.exists():
        print("找不到 docs/index.html。", file=sys.stderr)
        print("請先執行：python build.py", file=sys.stderr)
        return 2

    port = choose_port(args.host, args.port)
    server = http.server.ThreadingHTTPServer(
        (args.host, port), make_handler(DOCS)
    )
    server.daemon_threads = True
    url = f"http://{args.host}:{port}/"

    print("平行世界角色年表 — 本機預覽")
    print(f"  {url}")
    print("  按 Ctrl+C 停止。\n")

    if args.open:
        threading.Timer(0.4, lambda: webbrowser.open(url)).start()

    try:
        server.serve_forever()
    except KeyboardInterrupt:
        print("\n已停止預覽伺服器。")
    finally:
        server.server_close()
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
