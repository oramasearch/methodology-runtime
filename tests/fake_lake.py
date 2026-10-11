"""A standard-library fake of Lake's two runner capabilities, for the transfer tests."""

import base64
import json
import threading
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer

CREDENTIAL = "lmr.00000000-0000-4000-8000-000000000001.secret"


class FakeLake:
    def __init__(self, spec: dict, rows: list[dict], fail_first: int = 0) -> None:
        self.spec = spec
        self.rows = rows
        self.outputs: list[dict] = []
        self.requests: list[tuple[str, dict, dict]] = []
        self.fail_first = fail_first
        fake = self

        class Handler(BaseHTTPRequestHandler):
            def log_message(self, *_):
                pass

            def _answer(self, status: int, body: dict, headers: dict | None = None) -> None:
                payload = json.dumps(body).encode()
                self.send_response(status)
                self.send_header("Content-Type", "application/json")
                self.send_header("Content-Length", str(len(payload)))
                for name, value in (headers or {}).items():
                    self.send_header(name, value)
                self.end_headers()
                self.wfile.write(payload)

            def _refuse(self, status: int, code: str, message: str) -> None:
                self._answer(status, {"ok": False, "error": {"code": code, "message": message, "path": ""}})

            def do_POST(self):  # noqa: N802
                length = int(self.headers.get("Content-Length", "0"))
                body = json.loads(self.rfile.read(length) or b"{}")
                fake.requests.append((self.path, dict(self.headers), body))
                if fake.fail_first > 0:
                    fake.fail_first -= 1
                    return self._answer(503, {"ok": False, "error": {"code": "store_unavailable", "message": "later"}},
                                        {"Retry-After": "0"})
                if self.headers.get("Authorization") != f"Bearer {CREDENTIAL}":
                    return self._refuse(401, "unauthenticated", "no credential was accepted")
                if body.get("run_id") != fake.spec["run_id"]:
                    return self._refuse(404, "not_found", "no such run")
                if self.path == "/v1/methodology_run_input":
                    offset = body.get("offset", 0)
                    limit = body.get("limit", 5000)
                    page = fake.rows[offset:offset + limit]
                    following = offset + limit if offset + limit < len(fake.rows) else None
                    value = {"spec": fake.spec, "rows": page, "next_offset": following, "total_rows": len(fake.rows)}
                    return self._answer(200, {"ok": True, "value": value, "duration_ms": 0.1})
                if self.path == "/v1/methodology_run_output":
                    if fake.outputs:
                        return self._refuse(409, "conflict", "this run's output is already recorded")
                    if "artifact_base64" in body:
                        base64.b64decode(body["artifact_base64"], validate=True)
                    fake.outputs.append(body)
                    return self._answer(200, {"ok": True, "value": {"recorded": True, "model_id": "model-1"}})
                return self._refuse(404, "unknown_capability", "no capability has this name")

        self.server = ThreadingHTTPServer(("127.0.0.1", 0), Handler)
        self.url = f"http://127.0.0.1:{self.server.server_address[1]}"
        self.thread = threading.Thread(target=self.server.serve_forever, daemon=True)

    def __enter__(self):
        self.thread.start()
        return self

    def __exit__(self, *_):
        self.server.shutdown()
        self.server.server_close()
