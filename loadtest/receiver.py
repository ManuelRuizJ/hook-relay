"""Minimal webhook receiver for load tests: accepts any POST with 200.

Kept trivial on purpose so the numbers measure HookRelay, not the receiver.
Counts requests and exposes the total on GET /count.
"""

import threading
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer

_lock = threading.Lock()
_received = 0


class Handler(BaseHTTPRequestHandler):
    protocol_version = "HTTP/1.1"  # keep-alive, like a real receiver
    # http.server writes headers and body in separate sends. With Nagle's
    # algorithm on, the second send waits for the client's delayed ACK and
    # every response takes ~40 ms. Production servers set TCP_NODELAY;
    # without it this receiver, not HookRelay, would dominate the results.
    disable_nagle_algorithm = True

    def do_POST(self) -> None:  # noqa: N802 (http.server API)
        global _received
        length = int(self.headers.get("Content-Length", 0))
        self.rfile.read(length)
        with _lock:
            _received += 1
        self._reply(b"ok")

    def do_GET(self) -> None:  # noqa: N802
        with _lock:
            body = str(_received).encode()
        self._reply(body)

    def _reply(self, body: bytes) -> None:
        self.send_response(200)
        self.send_header("Content-Length", str(len(body)))
        self.end_headers()
        self.wfile.write(body)

    def log_message(self, *args) -> None:  # silence per-request logging
        pass


if __name__ == "__main__":
    ThreadingHTTPServer(("0.0.0.0", 8080), Handler).serve_forever()
