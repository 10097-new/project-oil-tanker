"""在局域网提供 briefs/ 静态页，方便手机与 PC 同步打开知识库。

用法:
  python serve.py
  python serve.py --port 8080
然后本机打开 http://127.0.0.1:8080/library/index.html
同一 Wi-Fi 下手机打开 http://<电脑局域网IP>:8080/library/index.html
"""

from __future__ import annotations

import argparse
import socket
from http.server import SimpleHTTPRequestHandler, ThreadingHTTPServer

import config


def lan_ip() -> str:
    try:
        sock = socket.socket(socket.AF_INET, socket.SOCK_DGRAM)
        sock.connect(("8.8.8.8", 80))
        ip = sock.getsockname()[0]
        sock.close()
        return ip
    except OSError:
        return "127.0.0.1"


def main() -> None:
    parser = argparse.ArgumentParser(description="提供油轮行业知识库静态页")
    parser.add_argument("--host", default="0.0.0.0")
    parser.add_argument("--port", type=int, default=8080)
    args = parser.parse_args()
    config.BRIEFS_DIR.mkdir(parents=True, exist_ok=True)

    class Handler(SimpleHTTPRequestHandler):
        def __init__(self, *a, **kw):
            super().__init__(*a, directory=str(config.BRIEFS_DIR), **kw)

    server = ThreadingHTTPServer((args.host, args.port), Handler)
    ip = lan_ip()
    print(f"知识库: http://127.0.0.1:{args.port}/library/index.html")
    print(f"手机访问: http://{ip}:{args.port}/library/index.html")
    print(f"日报目录: http://127.0.0.1:{args.port}/index.html")
    print("按 Ctrl+C 结束")
    server.serve_forever()


if __name__ == "__main__":
    main()
