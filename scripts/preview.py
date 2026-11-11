"""Local development only; never loaded by the AstrBot plugin.

Run: python scripts/preview.py --port 8765
Uses a separate database and a loopback-only, nonce-protected preview bridge.
"""

import argparse
import json
import secrets
import sys
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from pathlib import Path
from urllib.parse import parse_qs, urlsplit

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))

from core.calendar import CalendarService, today  # noqa: E402
from core.importing import MAX_REQUEST_BYTES, CalendarImporter  # noqa: E402
from core.store import CalendarStore, ConflictError, ValidationError  # noqa: E402


def create_server(database, port=8765):
    store = CalendarStore(database)
    service = CalendarService(store)
    importer = CalendarImporter(store)
    nonce = secrets.token_urlsafe(32)

    class Handler(BaseHTTPRequestHandler):
        def log_message(self, *_args):
            pass

        def reply(self, status, content, mime="application/json; charset=utf-8"):
            if isinstance(content, (dict, list)):
                content = json.dumps(content, ensure_ascii=False).encode("utf-8")
            elif isinstance(content, str):
                content = content.encode("utf-8")
            self.send_response(status)
            self.send_header("Content-Type", mime)
            self.send_header("Content-Length", str(len(content)))
            self.send_header("Cache-Control", "no-store")
            self.send_header("X-Content-Type-Options", "nosniff")
            self.end_headers()
            self.wfile.write(content)

        def authorized(self):
            host = f"127.0.0.1:{self.server.server_port}"
            return (
                self.headers.get("Host") == host
                and self.headers.get("X-Calendar-Preview") == nonce
            )

        def do_GET(self):
            url = urlsplit(self.path)
            if self.headers.get("Host") != f"127.0.0.1:{self.server.server_port}":
                return self.reply(403, {"message": "仅允许本机预览"})
            if url.path.startswith("/api/"):
                if not self.authorized():
                    return self.reply(403, {"message": "预览凭据无效"})
                try:
                    if url.path == "/api/month":
                        query = parse_qs(url.query)
                        current = today()
                        return self.reply(
                            200,
                            service.month(
                                query.get("year", [current.year])[0],
                                query.get("month", [current.month])[0],
                            ),
                        )
                    if url.path == "/api/export":
                        return self.reply(200, store.export())
                except ValidationError as exc:
                    return self.reply(400, {"message": str(exc)})
                return self.reply(404, {"message": "接口不存在"})
            if url.path == "/preview-bridge.js":
                script = """
const call = async (name, params, method='GET') => {
  const response = await fetch('/api/'+name+(method==='GET'?'?'+new URLSearchParams(params||{}):''), {
    method,headers:{'Content-Type':'application/json','X-Calendar-Preview':NONCE},
    ...(method==='POST'?{body:JSON.stringify(params)}:{})
  });
  const value=await response.json();if(!response.ok)throw new Error(value.message);return value;
};
window.AstrBotPluginView={ready:async()=>({isDark:false}),apiGet:(n,p)=>call(n,p),apiPost:(n,p)=>call(n,p,'POST')};
""".replace("NONCE", json.dumps(nonce))
                return self.reply(200, script, "text/javascript; charset=utf-8")
            paths = {
                "/": ("index.html", "text/html"),
                "/index.html": ("index.html", "text/html"),
                "/app.js": ("app.js", "text/javascript"),
                "/render.js": ("render.js", "text/javascript"),
                "/style.css": ("style.css", "text/css"),
            }
            if url.path not in paths:
                return self.reply(404, {"message": "文件不存在"})
            name, mime = paths[url.path]
            content = (ROOT / "pages" / "calendar" / name).read_text(encoding="utf-8")
            if name == "index.html":
                content = content.replace(
                    '<script type="module"',
                    '<script src="./preview-bridge.js"></script>\n<script type="module"',
                )
            return self.reply(200, content, mime + "; charset=utf-8")

        def do_POST(self):
            origin = self.headers.get("Origin")
            if not self.authorized() or origin not in (
                None,
                f"http://127.0.0.1:{self.server.server_port}",
            ):
                return self.reply(403, {"message": "预览凭据或来源无效"})
            if self.path not in {"/api/mutate", "/api/import/preview", "/api/import"}:
                return self.reply(404, {"message": "接口不存在"})
            try:
                length = int(self.headers.get("Content-Length", "0"))
                limit = 65536 if self.path == "/api/mutate" else MAX_REQUEST_BYTES
                if not 0 < length <= limit:
                    return self.reply(413, {"message": "请求内容过大或为空"})
                payload = json.loads(self.rfile.read(length))
                result = (
                    store.mutate(payload)
                    if self.path == "/api/mutate"
                    else importer.run(payload, commit=self.path == "/api/import")
                )
                return self.reply(200, result)
            except ConflictError as exc:
                return self.reply(409, {"message": str(exc)})
            except (ValidationError, ValueError) as exc:
                return self.reply(400, {"message": str(exc)})

    server = ThreadingHTTPServer(("127.0.0.1", port), Handler)
    server.daemon_threads = True
    return server


if __name__ == "__main__":
    parser = argparse.ArgumentParser(description="历记本地开发预览（独立测试数据）")
    parser.add_argument("--port", type=int, default=8765)
    parser.add_argument(
        "--database", type=Path, default=ROOT / ".preview" / "calendar.sqlite3"
    )
    args = parser.parse_args()
    server = create_server(args.database, args.port)
    print(
        f"Preview: http://127.0.0.1:{server.server_port} (development data only)",
        flush=True,
    )
    try:
        server.serve_forever()
    except KeyboardInterrupt:
        pass
    finally:
        server.server_close()
