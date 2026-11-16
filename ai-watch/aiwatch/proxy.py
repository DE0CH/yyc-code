"""YYC's residential proxy for the browser (CLAUDE.md: every YYC job uses the one IPRoyal sub-user `yycstream`).

RESI_PROXY is the sub-user's bare login, http://yycstream:<password>@geo.iproyal.com:12321 (no suffix); the exit country
and a sticky session are a suffix on the password (IPRoyal's syntax, as news-stream/resi.py). Chrome sends proxy
credentials only after a 407 and IPRoyal answers an unauthenticated CONNECT with 402, so the browser goes through a local
forwarder that adds the login to every connection (news-stream/resi.py `_serve`, the same code).
"""
import base64
import os
import socket
import socketserver
import threading
import urllib.parse

_forwarders, _lock = {}, threading.Lock()


def parts(country="hk", session=None, lifetime=None):
    """→ (host, port, user, password with the suffix), or None when RESI_PROXY is not set"""
    base = os.environ.get("RESI_PROXY")
    if not base:
        return None
    u = urllib.parse.urlsplit(base)
    pw = urllib.parse.unquote(u.password or "").split("_")[0] + f"_country-{country}"
    if session:
        pw += f"_session-{session}" + (f"_lifetime-{lifetime}" if lifetime else "")
    return u.hostname, u.port or 80, urllib.parse.unquote(u.username or ""), pw


def _serve(host, port, user, pw):
    auth = b"Proxy-Authorization: Basic " + base64.b64encode(f"{user}:{pw}".encode()) + b"\r\n"

    def pipe(a, b):
        try:
            while True:
                d = a.recv(65536)
                if not d:
                    break
                b.sendall(d)
        except OSError:
            pass
        finally:
            for x in (a, b):
                try:
                    x.shutdown(socket.SHUT_RDWR)
                except OSError:
                    pass

    class Handler(socketserver.BaseRequestHandler):
        def handle(self):
            c, head = self.request, b""
            while b"\r\n\r\n" not in head:
                d = c.recv(65536)
                if not d:
                    return
                head += d
                if len(head) > 65536:
                    return
            top, rest = head.split(b"\r\n\r\n", 1)
            lines = [x for x in top.split(b"\r\n") if not x.lower().startswith(b"proxy-authorization:")]
            if not lines[0].upper().startswith(b"CONNECT "):
                lines = [x for x in lines if not x.lower().startswith((b"connection:", b"proxy-connection:"))]
                lines.append(b"Connection: close")
            out = b"\r\n".join(lines[:1]) + b"\r\n" + auth + b"\r\n".join(lines[1:]) + b"\r\n\r\n" + rest
            try:
                u = socket.create_connection((host, port), timeout=30)
            except OSError:
                c.sendall(b"HTTP/1.1 502 Bad Gateway\r\nContent-Length: 0\r\n\r\n")
                return
            u.settimeout(None)
            u.sendall(out)
            t = threading.Thread(target=pipe, args=(u, c), daemon=True)
            t.start()
            pipe(c, u)
            t.join(300)
            u.close()

    class Server(socketserver.ThreadingTCPServer):
        daemon_threads = allow_reuse_address = True

    srv = Server(("127.0.0.1", 0), Handler)
    threading.Thread(target=srv.serve_forever, daemon=True).start()
    return srv


def browser_proxy(country="hk", session=None, lifetime=None):
    """{server} for Patchright: a local forwarder (one per login and process) with the login added; None without
    RESI_PROXY"""
    p = parts(country, session, lifetime)
    if not p:
        return None
    with _lock:
        if p not in _forwarders:
            _forwarders[p] = _serve(*p).server_address[1]
        return {"server": "http://127.0.0.1:%d" % _forwarders[p]}
