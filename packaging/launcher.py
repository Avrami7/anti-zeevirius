"""
launcher.py — point d'entrée de l'application installée.

`python -m gui.server` convient à un développeur : il affiche une URL et un
jeton dans un terminal, et attend. Pour quelqu'un qui a double-cliqué sur une
icône, ce n'est pas une application — c'est un message d'erreur en puissance.

Ce lanceur comble l'écart :

  - il prépare l'arborescence de données au premier lancement ;
  - il choisit un port LIBRE au lieu d'un port fixe (8777 peut être occupé,
    et l'utilisateur n'a aucun moyen d'y remédier depuis une icône) ;
  - il démarre le serveur dans un fil d'exécution secondaire ;
  - il ouvre une vraie fenêtre applicative (pywebview / WebView2) quand c'est
    possible, sinon le navigateur par défaut ;
  - il refuse de démarrer deux fois : relancer l'icône ramène la fenêtre de
    l'instance existante au lieu d'ouvrir un second serveur ;
  - il s'arrête proprement quand la fenêtre est fermée.
"""

from __future__ import annotations

import json
import os
import socket
import sys
import threading
import time
import webbrowser
from pathlib import Path

# Gelé, PyInstaller place la racine du projet dans sys.path ; depuis les
# sources, ce fichier est dans packaging/, il faut remonter d'un cran.
if not getattr(sys, "frozen", False):
    sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

import paths  # noqa: E402

APP_TITLE = "ANTI-ZEEVIRIUS — Poste de commandement"
RUNTIME_FILE = "runtime.json"


# ── Instance unique ────────────────────────────────────────────────────────
def _runtime_path() -> Path:
    return paths.data_path(RUNTIME_FILE)


def _instance_deja_active() -> str | None:
    """Retourne l'URL de l'instance en cours, ou None.

    On ne se fie pas au seul PID : après un arrêt brutal, le fichier survit et
    le PID peut avoir été réattribué à un processus sans rapport. La preuve
    retenue est qu'un serveur réponde effectivement sur le port annoncé.
    """
    f = _runtime_path()
    if not f.is_file():
        return None
    try:
        info = json.loads(f.read_text(encoding="utf-8"))
        port = int(info["port"])
        token = str(info["token"])
    except (ValueError, KeyError, OSError):
        return None

    with socket.socket(socket.AF_INET, socket.SOCK_STREAM) as s:
        s.settimeout(0.4)
        if s.connect_ex(("127.0.0.1", port)) != 0:
            return None       # personne n'écoute : fichier périmé
    return f"http://127.0.0.1:{port}/?t={token}"


def _ecrire_runtime(port: int, token: str) -> None:
    f = _runtime_path()
    f.parent.mkdir(parents=True, exist_ok=True)
    f.write_text(json.dumps({"pid": os.getpid(), "port": port, "token": token}),
                 encoding="utf-8")
    # Le jeton est un secret de session : lisible par son seul propriétaire.
    try:
        os.chmod(f, 0o600)
    except OSError:
        pass


def _effacer_runtime() -> None:
    try:
        _runtime_path().unlink()
    except OSError:
        pass


# ── Port libre ─────────────────────────────────────────────────────────────
def _port_libre(prefere: int) -> int:
    """Le port préféré s'il est disponible, sinon un port attribué par l'OS."""
    for candidat in (prefere, 0):
        with socket.socket(socket.AF_INET, socket.SOCK_STREAM) as s:
            try:
                s.bind(("127.0.0.1", candidat))
                return s.getsockname()[1]
            except OSError:
                continue
    return 0


# ── Fenêtre ────────────────────────────────────────────────────────────────
def _ouvrir_fenetre(url: str, arret) -> bool:
    """Fenêtre applicative native via pywebview. False si indisponible.

    pywebview s'appuie sur WebView2, présent d'origine sur Windows 10/11. En
    son absence (ou hors Windows), on retombe sur le navigateur par défaut :
    l'application reste utilisable, elle a juste l'apparence d'un onglet.
    """
    try:
        import webview  # type: ignore
    except ImportError:
        return False

    try:
        webview.create_window(APP_TITLE, url, width=1380, height=900,
                              min_size=(900, 620), background_color="#08050a")
        webview.start()      # bloquant jusqu'à fermeture de la fenêtre
    except Exception as exc:  # pragma: no cover - dépend de l'environnement
        print(f"[AVERTISSEMENT] Fenêtre native indisponible ({exc}) — "
              f"repli sur le navigateur.", file=sys.stderr)
        return False
    finally:
        arret.set()
    return True


# ── Entrée ─────────────────────────────────────────────────────────────────
def main(argv=None) -> int:
    deja = _instance_deja_active()
    if deja:
        # Deuxième double-clic : on ne démarre pas un second serveur, on
        # ramène l'utilisateur devant celui qui tourne déjà.
        webbrowser.open(deja)
        return 0

    paths.ensure_user_data()

    from gui.server import create_server, DEFAULT_PORT

    port = _port_libre(DEFAULT_PORT)
    try:
        httpd, token = create_server(port)
    except OSError as exc:
        print(f"[ERREUR] Démarrage impossible : {exc}", file=sys.stderr)
        return 1

    port = httpd.server_address[1]
    url = f"http://127.0.0.1:{port}/"
    _ecrire_runtime(port, token)

    arret = threading.Event()
    fil = threading.Thread(target=httpd.serve_forever, name="az-http", daemon=True)
    fil.start()

    # Laisse au serveur le temps d'accepter avant d'ouvrir la vue : une page
    # ouverte trop tôt affiche une erreur de connexion.
    time.sleep(0.25)

    try:
        if not _ouvrir_fenetre(url, arret):
            webbrowser.open(url)
            print(f"ANTI-ZEEVIRIUS est ouvert dans votre navigateur : {url}")
            print("Fermez cette fenêtre pour quitter l'application.")
            try:
                while not arret.is_set():
                    time.sleep(0.5)
            except KeyboardInterrupt:
                pass
    finally:
        httpd.shutdown()
        httpd.server_close()
        _effacer_runtime()

    return 0


if __name__ == "__main__":
    raise SystemExit(main())
