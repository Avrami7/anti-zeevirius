"""
gui/server.py — Serveur HTTP local de l'interface web ANTI-ZEEVIRIUS.

Stdlib uniquement (`http.server`, `json`, `secrets`, `threading`, `socketserver`).
Aucune dépendance nouvelle : l'outil doit fonctionner sur une machine hors
ligne, potentiellement infectée.

Garanties appliquées ici (voir gui/API_CONTRACT.md) :

* **Bind 127.0.0.1 exclusivement**, jamais 0.0.0.0. Port 8777 par défaut,
  `--port` pour changer.
* **Jeton de session** `secrets.token_urlsafe(32)` généré au démarrage,
  injecté dans la page servie, exigé sur tout `/api/*` via l'en-tête
  `X-AZ-Token` et comparé avec `secrets.compare_digest` (temps constant).
  Jeton absent ou invalide → HTTP 403.
* **Import paresseux** : ce fichier n'importe aucun module métier. Le
  serveur démarre même si watchdog / yara / winreg manquent ; les actions
  concernées répondent `unavailable` en HTTP 200.
* **Service statique verrouillé** sur `gui/web/` : toute tentative de
  traversée de répertoire (`..`, chemin absolu, lien sortant) est refusée.
  Si `gui/web/index.html` n'existe pas encore (frontend en cours de
  développement), une page d'attente minimale est servie à la place.

Usage :
    python -m gui.server [--port 8777]
"""

import argparse
import json
import mimetypes
import os
import posixpath
import secrets
import sys
import threading
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from pathlib import Path
from typing import Dict, Optional, Tuple
from urllib.parse import unquote, urlparse, parse_qs

BASE_DIR = Path(__file__).resolve().parent.parent
WEB_DIR = (Path(__file__).resolve().parent / "web")

DEFAULT_PORT = 8777
HOST = "127.0.0.1"  # NE JAMAIS remplacer par 0.0.0.0
TOKEN_HEADER = "X-AZ-Token"
MAX_BODY_BYTES = 8 * 1024 * 1024  # garde-fou contre un corps JSON démesuré

# Marqueur remplacé par le jeton de session dans la page servie.
TOKEN_PLACEHOLDER = "__AZ_TOKEN__"

_WAITING_PAGE = """<!doctype html>
<html lang="fr"><head><meta charset="utf-8">
<title>ANTI-ZEEVIRIUS — interface en cours de construction</title>
<style>
 body{background:#0e1116;color:#e6edf3;font-family:system-ui,sans-serif;
      display:flex;align-items:center;justify-content:center;height:100vh;margin:0}
 main{max-width:36rem;padding:2rem;border:1px solid #30363d;border-radius:12px}
 h1{margin:0 0 .5rem;font-size:1.3rem} code{color:#7ee787}
 p{line-height:1.6;color:#9da7b3}
</style></head>
<body><main>
 <h1>Backend ANTI-ZEEVIRIUS opérationnel</h1>
 <p>L'API locale répond, mais <code>gui/web/index.html</code> n'a pas encore
 été déposé par l'interface. Le jeton de session est disponible dans
 <code>window.AZ_TOKEN</code>.</p>
 <p>Testez l'API : <code>POST /api/status</code> avec l'en-tête
 <code>X-AZ-Token</code>.</p>
</main>
<script>window.AZ_TOKEN = "__AZ_TOKEN__";</script>
</body></html>
"""


def _inject_token(html: str, token: str) -> str:
    """Injecte le jeton de session dans la page servie.

    Deux mécanismes, pour ne rien imposer au frontend :
      1. tout `__AZ_TOKEN__` présent dans le HTML est remplacé ;
      2. si le marqueur est absent, un `<script>window.AZ_TOKEN=...</script>`
         est inséré juste après `<head>` (ou en tête de document).
    """
    if TOKEN_PLACEHOLDER in html:
        return html.replace(TOKEN_PLACEHOLDER, token)

    snippet = f'<script>window.AZ_TOKEN = "{token}";</script>'
    lowered = html.lower()
    idx = lowered.find("<head>")
    if idx != -1:
        cut = idx + len("<head>")
        return html[:cut] + snippet + html[cut:]
    idx = lowered.find("<html")
    if idx != -1:
        cut = html.find(">", idx) + 1
        return html[:cut] + snippet + html[cut:]
    return snippet + html


def safe_web_path(url_path: str) -> Optional[Path]:
    """Traduit un chemin d'URL en fichier de `gui/web/`, ou None si refusé.

    Refuse : traversée `..`, chemins absolus, séparateurs Windows, octets
    nuls, et tout résultat qui, une fois résolu, sort de `gui/web/` (ce qui
    couvre aussi les liens symboliques pointant à l'extérieur).
    """
    path = unquote(url_path or "/")
    if "\0" in path:
        return None
    path = path.replace("\\", "/")

    # Refus EXPLICITE de toute traversée. On ne se contente pas de laisser
    # normpath ramener '/../../etc/passwd' à '/etc/passwd' : un segment '..'
    # dans l'URL est une tentative caractérisée, elle est rejetée telle
    # quelle plutôt que silencieusement réécrite.
    if any(segment == ".." for segment in path.split("/")):
        return None

    normalized = posixpath.normpath(path)
    if normalized in (".", "/"):
        normalized = "/index.html"
    if not normalized.startswith("/"):
        normalized = "/" + normalized

    parts = [p for p in normalized.split("/") if p not in ("", ".")]
    if any(p == ".." for p in parts):
        return None
    if not parts:
        parts = ["index.html"]

    candidate = WEB_DIR.joinpath(*parts)

    # Contrôle final sur les chemins résolus : la seule autorité qui compte.
    try:
        root = WEB_DIR.resolve()
        resolved = candidate.resolve()
    except (OSError, RuntimeError):
        return None
    if resolved != root and root not in resolved.parents:
        return None
    return resolved


class AZRequestHandler(BaseHTTPRequestHandler):
    server_version = "AntiZeevirius/1.0"
    protocol_version = "HTTP/1.1"

    # Injectés par create_server()
    token: str = ""
    bridge = None
    verbose: bool = False

    # ── Journalisation ───────────────────────────────────────────
    def log_message(self, fmt: str, *args) -> None:
        if self.verbose:
            sys.stderr.write("[gui] %s - %s\n" % (self.address_string(), fmt % args))

    # ── Réponses ─────────────────────────────────────────────────
    def _send(self, status: int, body: bytes, content_type: str) -> None:
        self.send_response(status)
        self.send_header("Content-Type", content_type)
        self.send_header("Content-Length", str(len(body)))
        self.send_header("Cache-Control", "no-store")
        self.send_header("X-Content-Type-Options", "nosniff")
        # Interface strictement locale : aucune ressource distante permise.
        self.send_header(
            "Content-Security-Policy",
            "default-src 'self'; script-src 'self' 'unsafe-inline'; "
            "style-src 'self' 'unsafe-inline'; img-src 'self' data:; connect-src 'self'",
        )
        self.send_header("Referrer-Policy", "no-referrer")
        self.end_headers()
        if self.command != "HEAD":
            self.wfile.write(body)

    def _send_json(self, payload: Dict, status: int = 200) -> None:
        body = json.dumps(payload, ensure_ascii=False, default=str).encode("utf-8")
        self._send(status, body, "application/json; charset=utf-8")

    def _send_html(self, html: str, status: int = 200) -> None:
        self._send(status, html.encode("utf-8"), "text/html; charset=utf-8")

    # ── Authentification ─────────────────────────────────────────
    def _token_ok(self) -> bool:
        """Comparaison à temps constant du jeton de session."""
        provided = self.headers.get(TOKEN_HEADER) or ""
        if not provided:
            # Repli toléré pour les EventSource/liens qui ne peuvent pas
            # porter d'en-tête personnalisé : ?token=... dans l'URL.
            provided = (parse_qs(urlparse(self.path).query).get("token") or [""])[0]
        if not provided or not self.token:
            return False
        return secrets.compare_digest(str(provided), str(self.token))

    # ── Lecture du corps ─────────────────────────────────────────
    def _read_json_body(self) -> Tuple[Optional[Dict], Optional[str]]:
        try:
            length = int(self.headers.get("Content-Length") or 0)
        except ValueError:
            return None, "En-tête Content-Length invalide."
        if length < 0 or length > MAX_BODY_BYTES:
            return None, "Corps de requête trop volumineux."
        if length == 0:
            return {}, None
        try:
            raw = self.rfile.read(length)
        except OSError as exc:
            return None, f"Lecture du corps impossible : {exc}"
        if not raw.strip():
            return {}, None
        try:
            parsed = json.loads(raw.decode("utf-8"))
        except (ValueError, UnicodeDecodeError) as exc:
            return None, f"Corps JSON invalide : {exc}"
        if not isinstance(parsed, dict):
            return None, "Le corps JSON doit être un objet."
        return parsed, None

    # ── Routage ──────────────────────────────────────────────────
    def do_GET(self) -> None:
        parsed = urlparse(self.path)
        path = parsed.path

        if path == "/api" or path.startswith("/api/"):
            if not self._token_ok():
                self._send_json({"ok": False, "error": "Jeton de session absent ou invalide.",
                                 "unavailable": False}, status=403)
                return
            action = path[len("/api/"):].strip("/")
            params = {k: (v[0] if len(v) == 1 else v) for k, v in parse_qs(parsed.query).items()}
            params.pop("token", None)
            self._dispatch(action, params)
            return

        self._serve_static(path)

    def do_HEAD(self) -> None:
        self.do_GET()

    def do_POST(self) -> None:
        parsed = urlparse(self.path)
        path = parsed.path

        if not (path == "/api" or path.startswith("/api/")):
            self._send_json({"ok": False, "error": "Seules les routes /api/* acceptent POST.",
                             "unavailable": False}, status=404)
            return

        # Le jeton est vérifié AVANT toute lecture/interprétation du corps.
        if not self._token_ok():
            self._send_json({"ok": False, "error": "Jeton de session absent ou invalide.",
                             "unavailable": False}, status=403)
            return

        params, error = self._read_json_body()
        if error is not None:
            self._send_json({"ok": False, "error": error, "unavailable": False}, status=200)
            return

        action = path[len("/api/"):].strip("/")
        self._dispatch(action, params or {})

    def _dispatch(self, action: str, params: Dict) -> None:
        if not action:
            self._send_json({"ok": True, "data": {"actions": self.bridge.known_actions()}})
            return
        result = self.bridge.dispatch(action, params)
        if result is None:
            self._send_json({"ok": False, "error": f"Action inconnue : {action}",
                             "unavailable": False}, status=404)
            return
        self._send_json(result, status=200)

    # ── Fichiers statiques ───────────────────────────────────────
    def _serve_static(self, path: str) -> None:
        target = safe_web_path(path)
        if target is None:
            self._send_json({"ok": False, "error": "Chemin refusé.", "unavailable": False}, status=403)
            return

        # Page d'accueil : servie avec le jeton injecté. Si le frontend
        # n'est pas encore déposé, on sert une page d'attente plutôt que 404.
        if target.name == "index.html" and not target.is_file():
            self._send_html(_inject_token(_WAITING_PAGE, self.token))
            return

        if not target.is_file():
            self._send_json({"ok": False, "error": "Fichier introuvable.", "unavailable": False}, status=404)
            return

        try:
            data = target.read_bytes()
        except OSError as exc:
            self._send_json({"ok": False, "error": f"Lecture impossible : {exc}",
                             "unavailable": False}, status=500)
            return

        if target.suffix.lower() in (".html", ".htm"):
            html = _inject_token(data.decode("utf-8", errors="replace"), self.token)
            self._send_html(html)
            return

        ctype, _ = mimetypes.guess_type(target.name)
        self._send(200, data, ctype or "application/octet-stream")


def create_server(port: int = DEFAULT_PORT, verbose: bool = False):
    """Construit le serveur (sans le démarrer) et retourne (httpd, token)."""
    if str(BASE_DIR) not in sys.path:
        sys.path.insert(0, str(BASE_DIR))

    # Import du bridge ici : garde ce module léger et permet aux tests
    # d'importer `safe_web_path` sans rien construire.
    from gui.bridge import Bridge

    token = secrets.token_urlsafe(32)
    bridge = Bridge()

    handler = type("AZBoundHandler", (AZRequestHandler,), {
        "token": token, "bridge": bridge, "verbose": verbose,
    })

    httpd = ThreadingHTTPServer((HOST, port), handler)
    httpd.daemon_threads = True
    httpd.az_token = token
    httpd.az_bridge = bridge
    return httpd, token


def main(argv=None) -> int:
    parser = argparse.ArgumentParser(
        prog="gui.server",
        description="Interface web locale ANTI-ZEEVIRIUS (127.0.0.1 uniquement).",
    )
    parser.add_argument("--port", type=int, default=DEFAULT_PORT,
                        help=f"port d'écoute sur 127.0.0.1 (défaut : {DEFAULT_PORT})")
    parser.add_argument("--verbose", action="store_true", help="journalise chaque requête HTTP")
    args = parser.parse_args(argv)

    try:
        httpd, token = create_server(args.port, args.verbose)
    except OSError as exc:
        print(f"[ERREUR] Impossible d'écouter sur {HOST}:{args.port} — {exc}", file=sys.stderr)
        return 1

    host, port = httpd.server_address[0], httpd.server_address[1]
    print("ANTI-ZEEVIRIUS — interface web locale")
    print(f"  URL      : http://{host}:{port}/")
    print(f"  Jeton    : {token}")
    print("  Le jeton est injecté automatiquement dans la page (window.AZ_TOKEN).")
    print("  Ctrl+C pour arrêter.")
    sys.stdout.flush()

    try:
        httpd.serve_forever()
    except KeyboardInterrupt:
        print("\nArrêt demandé.")
    finally:
        httpd.shutdown()
        httpd.server_close()
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
