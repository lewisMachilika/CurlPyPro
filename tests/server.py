"""A small local HTTP server the test suite sends real requests to."""
import base64
import json
import threading
import time
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from urllib.parse import parse_qsl, urlparse

PNG_1PX = base64.b64decode(
    "iVBORw0KGgoAAAANSUhEUgAAAAEAAAABCAYAAAAfFcSJAAAADUlEQVR42mNk+M9QDwADhgGAWjR9awAAAABJRU5ErkJggg=="
)


class Server(ThreadingHTTPServer):
    daemon_threads = True

    def handle_error(self, request, client_address):
        pass  # clients that time out on purpose close the socket early


class Handler(BaseHTTPRequestHandler):
    protocol_version = "HTTP/1.1"
    flaky_counts = {}

    def log_message(self, *args):
        pass

    def _send(self, status=200, body=b"", content_type="application/json", headers=None):
        if isinstance(body, (dict, list)):
            body = json.dumps(body).encode()
        elif isinstance(body, str):
            body = body.encode()
        self.send_response(status)
        self.send_header("Content-Type", content_type)
        self.send_header("Content-Length", str(len(body)))
        for k, v in (headers or {}).items():
            self.send_header(k, v)
        self.end_headers()
        if self.command != "HEAD":
            self.wfile.write(body)

    def _body(self):
        length = int(self.headers.get("Content-Length") or 0)
        return self.rfile.read(length) if length else b""

    def _route(self):
        url = urlparse(self.path)
        path, query = url.path, dict(parse_qsl(url.query, keep_blank_values=True))
        body = self._body()

        if path == "/echo":
            try:
                text = body.decode("utf-8")
            except UnicodeDecodeError:
                text = None
            return self._send(200, {
                "method": self.command, "path": path, "query": query,
                "headers": {k: v for k, v in self.headers.items()},
                "body": text, "body_b64": base64.b64encode(body).decode(), "body_len": len(body),
            })
        if path.startswith("/status/"):
            code = int(path.rsplit("/", 1)[1])
            return self._send(code, {"status": code})
        if path == "/login":
            data = json.loads(body or b"{}")
            if data.get("user") == "ann" and data.get("password") == "pw":
                return self._send(200, {"token": "tok-123", "user": {"id": 7, "name": "ann"}},
                                  headers={"X-Request-Id": "req-1"})
            return self._send(401, {"error": "bad credentials"})
        if path == "/me":
            if self.headers.get("Authorization") != "Bearer tok-123":
                return self._send(401, {"error": "unauthorized"})
            return self._send(200, {"id": 7, "name": "ann", "roles": ["admin", "dev"]})
        if path == "/basic":
            expected = "Basic " + base64.b64encode(b"ann:pw").decode()
            if self.headers.get("Authorization") != expected:
                return self._send(401, {"error": "unauthorized"}, headers={"WWW-Authenticate": "Basic"})
            return self._send(200, {"ok": True})
        if path == "/oauth/token":
            form = dict(parse_qsl(body.decode()))
            return self._send(200, {"access_token": f"oauth-{form.get('grant_type')}", "expires_in": 3600})
        if path == "/oauth/check":
            return self._send(200, {"auth": self.headers.get("Authorization")})
        if path == "/set-cookie":
            return self._send(200, {"ok": True}, headers={"Set-Cookie": "session=abc; Path=/"})
        if path == "/read-cookie":
            return self._send(200, {"cookie": self.headers.get("Cookie")})
        if path == "/redirect":
            return self._send(302, b"", headers={"Location": "/echo?redirected=1"})
        if path == "/slow":
            time.sleep(float(query.get("s", "1")))
            return self._send(200, {"slow": True})
        if path == "/flaky":
            key = query.get("key", "default")
            n = Handler.flaky_counts.get(key, 0) + 1
            Handler.flaky_counts[key] = n
            if n < int(query.get("fail", "2")) + 1:
                return self._send(503, {"try": n})
            return self._send(200, {"try": n})
        if path == "/html":
            return self._send(200, "<!doctype html><html><body><h1>Hi</h1><p>x</p></body></html>", "text/html")
        if path == "/xml":
            return self._send(200, "<?xml version='1.0'?><a><b>1</b></a>", "application/xml")
        if path == "/image.png":
            return self._send(200, PNG_1PX, "image/png")
        if path == "/pdf":
            return self._send(200, b"%PDF-1.4 fake", "application/pdf")
        if path == "/sse-unterminated":
            return self._send(200, "data: last-event", "text/event-stream")
        if path == "/sse":
            payload = "event: greet\nid: 1\ndata: hello\n\ndata: {\"n\": 2}\n\n"
            return self._send(200, payload, "text/event-stream")
        if path == "/graphql":
            data = json.loads(body or b"{}")
            if "__schema" in data.get("query", ""):
                return self._send(200, {"data": {"__schema": GRAPHQL_SCHEMA}})
            return self._send(200, {"data": {"echo": data}})
        return self._send(404, {"error": "not found", "path": path})

    do_GET = do_POST = do_PUT = do_PATCH = do_DELETE = do_HEAD = do_OPTIONS = _route


GRAPHQL_SCHEMA = {
    "queryType": {"name": "Query"}, "mutationType": None, "subscriptionType": None,
    "types": [
        {"kind": "OBJECT", "name": "Query", "description": None, "fields": [
            {"name": "user", "description": "Find a user", "args": [
                {"name": "id", "type": {"kind": "NON_NULL", "name": None, "ofType": {"kind": "SCALAR", "name": "ID", "ofType": None}}, "defaultValue": None}],
             "type": {"kind": "OBJECT", "name": "User", "ofType": None}},
        ]},
        {"kind": "OBJECT", "name": "User", "description": None, "fields": [
            {"name": "id", "description": None, "args": [], "type": {"kind": "SCALAR", "name": "ID", "ofType": None}},
            {"name": "name", "description": None, "args": [], "type": {"kind": "SCALAR", "name": "String", "ofType": None}},
        ]},
        {"kind": "SCALAR", "name": "ID", "description": None, "fields": None},
        {"kind": "SCALAR", "name": "String", "description": None, "fields": None},
    ],
}


def start_server():
    server = Server(("127.0.0.1", 0), Handler)
    thread = threading.Thread(target=server.serve_forever, daemon=True)
    thread.start()
    return server, f"http://127.0.0.1:{server.server_address[1]}"


def start_ws_echo_server():
    """Minimal WebSocket echo server (text frames only, unmasked replies)."""
    import hashlib
    import socket
    import struct

    sock = socket.socket()
    sock.bind(("127.0.0.1", 0))
    sock.listen(5)

    def recv_exact(conn, n):
        buf = b""
        while len(buf) < n:
            chunk = conn.recv(n - len(buf))
            if not chunk:
                raise ConnectionError
            buf += chunk
        return buf

    def handle(conn):
        try:
            request = b""
            while b"\r\n\r\n" not in request:
                request += conn.recv(1024)
            headers = dict(
                line.split(": ", 1) for line in request.decode().split("\r\n")[1:] if ": " in line
            )
            accept = base64.b64encode(hashlib.sha1(
                (headers["Sec-WebSocket-Key"] + "258EAFA5-E914-47DA-95CA-C5AB0DC85B11").encode()).digest()).decode()
            conn.sendall((
                "HTTP/1.1 101 Switching Protocols\r\nUpgrade: websocket\r\nConnection: Upgrade\r\n"
                f"Sec-WebSocket-Accept: {accept}\r\n\r\n").encode())
            conn.sendall(b"\x81" + bytes([len(b'{"hello": "client"}')]) + b'{"hello": "client"}')
            while True:
                b1, b2 = recv_exact(conn, 2)
                opcode, length = b1 & 0x0F, b2 & 0x7F
                if length == 126:
                    length = struct.unpack(">H", recv_exact(conn, 2))[0]
                elif length == 127:
                    length = struct.unpack(">Q", recv_exact(conn, 8))[0]
                mask = recv_exact(conn, 4) if b2 & 0x80 else b"\0\0\0\0"
                payload = bytes(c ^ mask[i % 4] for i, c in enumerate(recv_exact(conn, length)))
                if opcode == 8:
                    conn.sendall(b"\x88\x00")
                    return
                reply = b"echo:" + payload
                conn.sendall(b"\x81" + (bytes([len(reply)]) if len(reply) < 126
                                        else b"\x7e" + struct.pack(">H", len(reply))) + reply)
        except Exception:
            pass
        finally:
            conn.close()

    def serve():
        while True:
            try:
                conn, _ = sock.accept()
            except OSError:
                return
            threading.Thread(target=handle, args=(conn,), daemon=True).start()

    threading.Thread(target=serve, daemon=True).start()
    return sock, f"ws://127.0.0.1:{sock.getsockname()[1]}"
