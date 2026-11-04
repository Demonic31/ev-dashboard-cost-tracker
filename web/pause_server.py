"""Page « PREPROD en pause » : remplace le dashboard de PREPROD quand il est arrêté.

Écoute sur le même port que le dashboard pour que le reverse proxy du Synology affiche cette page
(503, à réessayer plus tard) plutôt que sa propre page d'erreur. Aucune donnée, aucun secret :
seulement la page, le logo et le script de thème. PAUSE_LINK (facultatif) ajoute un bouton vers la PROD.
"""
import logging
import os
from html import escape
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer

STATIC_DIR = os.path.join(os.path.dirname(os.path.abspath(__file__)), "static")
FILES = {"/static/favicon-test.svg": "image/svg+xml", "/static/theme.js": "text/javascript; charset=utf-8"}
SECURITY_HEADERS = {
    "Content-Security-Policy": "default-src 'self'; script-src 'self'; style-src 'self' 'unsafe-inline'; "
                               "img-src 'self'; object-src 'none'; base-uri 'none'; frame-ancestors 'none'",
    "X-Frame-Options": "DENY",
    "X-Content-Type-Options": "nosniff",
    "Referrer-Policy": "same-origin",
}
log = logging.getLogger("ev-pause")


def pause_page(link):
    with open(os.path.join(STATIC_DIR, "pause.html"), encoding="utf-8") as f:
        html = f.read()
    button = f'<a class="button" href="{escape(link)}">Ouvrir le dashboard</a>' if link else ""
    return html.replace("{{link}}", button).encode()


def make_handler(link):
    page = pause_page(link)

    class Handler(BaseHTTPRequestHandler):
        server_version = "ev-pause"

        def log_message(self, fmt, *args):
            log.debug("%s %s", self.address_string(), fmt % args)

        def send(self, code, body, ctype, headers=None):
            self.send_response(code)
            self.send_header("Content-Type", ctype)
            self.send_header("Content-Length", str(len(body)))
            self.send_header("Cache-Control", "no-store")
            for k, v in {**SECURITY_HEADERS, **(headers or {})}.items():
                self.send_header(k, v)
            self.end_headers()
            self.wfile.write(body)

        def do_GET(self):  # noqa: N802
            path = self.path.split("?")[0]
            if (self.headers.get("X-Forwarded-Proto") or "").lower() == "http":
                self.send(301, b"", "text/plain", {"Location": f"https://{self.headers.get('Host', '')}{self.path}"})
            elif path in FILES:
                with open(os.path.join(STATIC_DIR, path[len("/static/"):]), "rb") as f:
                    self.send(200, f.read(), FILES[path])
            else:
                self.send(503, page, "text/html; charset=utf-8", {"Retry-After": "3600"})

    return Handler


if __name__ == "__main__":
    logging.basicConfig(level=logging.INFO, format="%(asctime)s %(levelname)s %(message)s")
    port = int(os.environ.get("PORT", "8080"))
    log.info("page « PREPROD en pause » sur le port %d", port)
    ThreadingHTTPServer(("0.0.0.0", port), make_handler(os.environ.get("PAUSE_LINK", "").strip())).serve_forever()
