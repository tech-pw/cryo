"""#260: the web nginx sends security headers and a cache policy on every response."""

from __future__ import annotations

import re
import shutil
import socket
import subprocess
import threading
import time
import urllib.error
import urllib.request
from collections.abc import Iterator
from http.server import BaseHTTPRequestHandler, HTTPServer
from pathlib import Path

import pytest

from tests.test_web_chart import _web_container, render

ROOT = Path(__file__).resolve().parents[1]
NGINX_CONF = ROOT / "web/nginx.conf"
DOCKERFILE = ROOT / "web/Dockerfile.prod"

REQUIRED_HEADERS = {
    "Content-Security-Policy",
    "X-Frame-Options",
    "X-Content-Type-Options",
    "Referrer-Policy",
    "Strict-Transport-Security",
    "Cache-Control",
}


def _conf_without_comments() -> str:
    return "\n".join(line.split("#", 1)[0] for line in NGINX_CONF.read_text().splitlines())


def _location_blocks(conf: str) -> list[str]:
    blocks = []
    for match in re.finditer(r"location\b[^{]*\{", conf):
        depth, index = 1, match.end()
        while depth:
            depth += {"{": 1, "}": -1}.get(conf[index], 0)
            index += 1
        blocks.append(conf[match.start() : index])
    return blocks


def test_server_block_sets_every_header_with_always() -> None:
    conf = _conf_without_comments()
    found = dict(re.findall(r"add_header\s+(\S+)\s+(.+?)\s+always;", conf))
    assert REQUIRED_HEADERS <= set(found)
    csp = found["Content-Security-Policy"]
    assert "frame-ancestors 'none'" in csp
    assert "https://accounts.google.com/gsi/client" in csp  # Google sign-in still loads
    assert "object-src 'none'" in csp
    assert found["X-Frame-Options"] == '"DENY"'
    assert found["X-Content-Type-Options"] == '"nosniff"'


def test_no_location_overrides_server_headers() -> None:
    # One add_header inside a location silently drops every server-level add_header there.
    for block in _location_blocks(_conf_without_comments()):
        assert "add_header" not in block, block.splitlines()[0]


def test_hsts_variable_is_always_defined_for_envsubst() -> None:
    dockerfile = DOCKERFILE.read_text()
    assert 'ENV CRYO_WEB_HSTS=""' in dockerfile
    assert "CRYO_WEB_HSTS" in re.search(r'NGINX_ENVSUBST_FILTER="([^"]+)"', dockerfile).group(1)


def test_chart_hsts_off_by_default() -> None:
    env = {
        e["name"]: e["value"] for e in _web_container(render("--set", "web.enabled=true"))["env"]
    }
    assert env["CRYO_WEB_HSTS"] == ""
    assert "CRYO_WEB_HSTS" in env["NGINX_ENVSUBST_FILTER"].split("|")


def test_chart_hsts_on() -> None:
    docs = render(
        "--set",
        "web.enabled=true",
        "--set",
        "web.hsts.enabled=true",
        "--set",
        "web.hsts.includeSubDomains=true",
        "--set",
        "web.hsts.maxAgeSeconds=600",
    )
    env = {e["name"]: e["value"] for e in _web_container(docs)["env"]}
    assert env["CRYO_WEB_HSTS"] == "max-age=600; includeSubDomains"


# ---- live check against a real nginx, when one is installed -------------------------------


def _free_port() -> int:
    with socket.socket() as sock:
        sock.bind(("127.0.0.1", 0))
        return int(sock.getsockname()[1])


@pytest.fixture()
def live_nginx(tmp_path: Path) -> Iterator[int]:
    nginx = shutil.which("nginx") or (
        "/usr/sbin/nginx" if Path("/usr/sbin/nginx").exists() else None
    )
    if nginx is None:
        pytest.skip("nginx is required for the live header check")

    class _Api(BaseHTTPRequestHandler):
        def do_GET(self) -> None:
            self.send_response(200)
            self.send_header("Content-Type", "application/json")
            self.send_header("Cache-Control", "private, max-age=5")
            self.end_headers()
            self.wfile.write(b"{}")

        def log_message(self, *_args: object) -> None:
            return

    api = HTTPServer(("127.0.0.1", 0), _Api)
    threading.Thread(target=api.serve_forever, daemon=True).start()
    html = tmp_path / "html"
    (html / "assets").mkdir(parents=True)
    (html / "index.html").write_text("<html></html>")
    (html / "assets/app-abc123.js").write_text("console.log(1)")
    port = _free_port()
    conf = (
        NGINX_CONF.read_text()
        .replace("${NGINX_LOCAL_RESOLVERS}", "127.0.0.1")
        .replace("${CRYO_API_UPSTREAM}", f"http://127.0.0.1:{api.server_port}")
        .replace("${CRYO_WEB_HSTS}", "")
        .replace("listen 8080;", f"listen 127.0.0.1:{port};")
        .replace("/usr/share/nginx/html", str(html))
    )
    (tmp_path / "site.conf").write_text(conf)
    temp = tmp_path / "tmp"
    temp.mkdir()
    mime = "include /etc/nginx/mime.types;" if Path("/etc/nginx/mime.types").exists() else ""
    (tmp_path / "nginx.conf").write_text(
        f"worker_processes 1; daemon off; pid {tmp_path}/nginx.pid; error_log {tmp_path}/err.log;\n"
        "events {}\n"
        f"http {{ {mime} access_log off; client_body_temp_path {temp}; proxy_temp_path {temp};"
        f" fastcgi_temp_path {temp}; uwsgi_temp_path {temp}; scgi_temp_path {temp};"
        f" include {tmp_path}/site.conf; }}\n"
    )
    proc = subprocess.Popen(
        [
            nginx,
            "-p",
            str(tmp_path),
            "-e",
            str(tmp_path / "err.log"),
            "-c",
            str(tmp_path / "nginx.conf"),
        ]
    )
    try:
        for _ in range(50):
            try:
                socket.create_connection(("127.0.0.1", port), timeout=0.1).close()
                break
            except OSError:
                time.sleep(0.1)
        yield port
    finally:
        proc.terminate()
        proc.wait(timeout=5)
        api.shutdown()


def _headers(port: int, path: str) -> dict[str, str]:
    try:
        response = urllib.request.urlopen(f"http://127.0.0.1:{port}{path}")
    except urllib.error.HTTPError as error:
        response = error
    return {key.lower(): value for key, value in response.headers.items()}


def test_live_headers_and_cache_policy(live_nginx: int) -> None:
    for path in ("/", "/tables/x", "/assets/app-abc123.js", "/metrics", "/api/v1/ping"):
        headers = _headers(live_nginx, path)
        assert headers["x-frame-options"] == "DENY", path
        assert headers["x-content-type-options"] == "nosniff", path
        assert "frame-ancestors 'none'" in headers["content-security-policy"], path
        assert "strict-transport-security" not in headers, path
    assert _headers(live_nginx, "/")["cache-control"] == "no-cache"
    assert _headers(live_nginx, "/tables/x")["cache-control"] == "no-cache"
    assert (
        _headers(live_nginx, "/assets/app-abc123.js")["cache-control"]
        == "public, max-age=31536000, immutable"
    )
    # Proxied API responses keep the API's own Cache-Control, not a second one from nginx.
    assert _headers(live_nginx, "/api/v1/ping")["cache-control"] == "private, max-age=5"
