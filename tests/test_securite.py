"""
test_securite.py — tests d'audit adversarial (poste AUDITEUR SÉCURITÉ).

Chaque test correspond à une faille prouvée pendant l'audit et à sa
correction. Convention : le test échouerait sur le code d'avant, passe
sur le code corrigé.

Failles couvertes :
  1. DNS rebinding — l'en-tête `Host` doit désigner la boucle locale, sinon
     403 (sans quoi une page rebindée sur 127.0.0.1 pilote toute l'API).
  2. Injection de commande Win32 — `uninstall_win32` ne doit JAMAIS lancer
     de shell (`shell=True`) sur une chaîne contrôlée par l'appelant.
  3. Injection PowerShell UWP — `uninstall_uwp` doit refuser un
     PackageFullName porteur de métacaractères (apostrophe, `;`, espace...).

Ces tests ne dépendent ni de Windows, ni de winreg, ni de PowerShell :
tout appel système réel est intercepté.
"""

import http.client
import json
import threading

import pytest

from gui.server import HOST, TOKEN_HEADER, create_server


# ── Serveur de test réel (port éphémère) ─────────────────────────────
@pytest.fixture(scope="module")
def live_server():
    httpd, token = create_server(port=0)
    thread = threading.Thread(target=httpd.serve_forever, daemon=True)
    thread.start()
    try:
        yield httpd, token, httpd.server_address[1]
    finally:
        httpd.shutdown()
        httpd.server_close()
        thread.join(timeout=5)


def _request(port, method, path, token=None, body=None, host_header=None):
    """Client HTTP stdlib permettant de forger l'en-tête Host."""
    conn = http.client.HTTPConnection(HOST, port, timeout=10)
    headers = {}
    if token is not None:
        headers[TOKEN_HEADER] = token
    payload = None
    if body is not None:
        payload = json.dumps(body).encode("utf-8")
        headers["Content-Type"] = "application/json"
    # skip_host + en-tête Host manuel : c'est exactement ce qu'envoie une
    # requête issue d'un domaine rebindé sur 127.0.0.1.
    conn.putrequest(method, path, skip_host=(host_header is not None))
    if host_header is not None:
        conn.putheader("Host", host_header)
    for k, v in headers.items():
        conn.putheader(k, v)
    conn.endheaders()
    if payload is not None:
        conn.send(payload)
    try:
        response = conn.getresponse()
        raw = response.read().decode("utf-8", errors="replace")
        try:
            return response.status, json.loads(raw)
        except ValueError:
            return response.status, raw
    finally:
        conn.close()


# ═══════════════════════════════════════════════════════════════════
# 1. Anti-DNS-rebinding : validation de l'en-tête Host
# ═══════════════════════════════════════════════════════════════════
class TestDnsRebinding:

    def test_host_boucle_locale_accepte(self, live_server):
        _httpd, token, port = live_server
        status, body = _request(port, "POST", "/api/status", token=token,
                                 body={}, host_header=f"127.0.0.1:{port}")
        assert status == 200 and body["ok"] is True

    def test_host_localhost_accepte(self, live_server):
        _httpd, token, port = live_server
        status, _ = _request(port, "POST", "/api/status", token=token,
                             body={}, host_header=f"localhost:{port}")
        assert status == 200

    def test_host_etranger_rejete_sur_api(self, live_server):
        """Cœur de la défense : Host attaquant → 403, même jeton valide."""
        _httpd, token, port = live_server
        status, body = _request(port, "POST", "/api/status", token=token,
                                 body={}, host_header="evil.attacker.com")
        assert status == 403
        assert "Host" in body.get("error", "")

    def test_host_etranger_ne_livre_pas_la_page_ni_le_jeton(self, live_server):
        """La page (qui contient le jeton) ne doit pas fuiter vers un Host
        étranger : sans ça, le rebinding lit le jeton puis pilote l'API."""
        _httpd, _token, port = live_server
        status, body = _request(port, "GET", "/", host_header="evil.attacker.com")
        assert status == 403
        assert "AZ_TOKEN" not in str(body)


# ═══════════════════════════════════════════════════════════════════
# 2. Injection de commande — désinstallation Win32 (registre, NON fiable)
# ═══════════════════════════════════════════════════════════════════
class TestInjectionWin32:

    def test_pas_de_shell_true(self, monkeypatch):
        """`uninstall_win32` ne doit jamais passer par un shell : la chaîne
        vient du registre / de l'appelant web, un `shell=True` transformerait
        `& commande` en exécution arbitraire."""
        from optimizer import app_manager

        captured = {}

        class _FakeProc:
            pass

        def fake_popen(argv, *args, **kwargs):
            captured["argv"] = argv
            captured["shell"] = kwargs.get("shell", False)
            return _FakeProc()

        monkeypatch.setattr(app_manager.subprocess, "Popen", fake_popen)

        evil = {
            "name": "Faux",
            "type": "win32",
            "uninstall_string": r'"C:\Windows\System32\calc.exe" & del C:\important',
        }
        res = app_manager.AppManager.uninstall_win32(evil)
        assert res["status"] == "ok"
        # Jamais de shell.
        assert captured["shell"] is False
        # La commande est passée en LISTE d'arguments, pas en chaîne unique
        # livrée au shell : le métacaractère `&` reste un argument inerte.
        assert isinstance(captured["argv"], list)
        # Les guillemets encadrants sont retirés : CreateProcess doit recevoir
        # le chemin réel, pas un nom de fichier contenant des guillemets (voir
        # TestDecoupageLigneDeCommande — c'est ce qui casserait la
        # désinstallation de toute application dans Program Files).
        assert captured["argv"][0] == r"C:\Windows\System32\calc.exe"
        assert "&" in captured["argv"]  # présent, mais comme simple argument


# ═══════════════════════════════════════════════════════════════════
# 3. Injection PowerShell — désinstallation UWP
# ═══════════════════════════════════════════════════════════════════
class TestInjectionUwp:

    def test_nom_de_paquet_malveillant_refuse_avant_tout_subprocess(self, monkeypatch):
        """Un PackageFullName porteur d'une apostrophe casse le littéral
        PowerShell. Il doit être refusé AVANT le moindre appel système."""
        from optimizer import app_manager

        appele = {"v": False}

        def fake_run(*args, **kwargs):
            appele["v"] = True
            raise AssertionError("subprocess.run ne doit pas être atteint")

        monkeypatch.setattr(app_manager.subprocess, "run", fake_run)

        evil = {
            "name": "x",
            "type": "uwp",
            "package_full_name": "x_1'; Remove-Item C:\\Users -Recurse; '",
        }
        res = app_manager.AppManager.uninstall_uwp(evil)
        assert res["status"] == "erreur"
        assert appele["v"] is False  # aucun subprocess lancé

    def test_nom_de_paquet_legitime_accepte(self, monkeypatch):
        """Un PackageFullName conforme passe la validation et atteint bien
        le subprocess (non-régression de la fonctionnalité)."""
        from optimizer import app_manager

        captured = {}

        class _R:
            returncode = 0
            stdout = ""
            stderr = ""

        def fake_run(cmd, *args, **kwargs):
            captured["cmd"] = cmd
            return _R()

        monkeypatch.setattr(app_manager.subprocess, "run", fake_run)

        legit = {
            "name": "Groove",
            "type": "uwp",
            "package_full_name": "Microsoft.ZuneMusic_10.0.0.0_x64__8wekyb3d8bbwe",
        }
        res = app_manager.AppManager.uninstall_uwp(legit)
        assert res["status"] == "ok"
        assert "Microsoft.ZuneMusic_10.0.0.0_x64__8wekyb3d8bbwe" in captured["cmd"][-1]


class TestDecoupageLigneDeCommande:
    """Le passage de `shell=True` à `shell=False` ferme la faille d'injection,
    mais il déplace la difficulté sur le découpage de la ligne de commande.

    `shlex.split(posix=False)` CONSERVE les guillemets encadrants dans le jeton.
    Passer `'"C:\\Program Files\\App\\x.exe"'` à CreateProcess fait chercher un
    fichier dont le nom contient littéralement les guillemets : la
    désinstallation échouerait pour toute application installée dans Program
    Files — la quasi-totalité d'entre elles. Le correctif de sécurité aurait
    donc cassé la fonctionnalité qu'il protège.
    """

    def test_chemin_avec_espaces_perd_ses_guillemets_encadrants(self):
        from optimizer.app_manager import _split_command_line
        argv = _split_command_line(r'"C:\Program Files\App\uninstall.exe" /S')
        assert argv == [r"C:\Program Files\App\uninstall.exe", "/S"]
        assert not argv[0].startswith('"'), "CreateProcess ne trouverait pas ce fichier"

    def test_chemin_sans_espace_inchange(self):
        from optimizer.app_manager import _split_command_line
        assert _split_command_line(r"C:\App\unins000.exe") == [r"C:\App\unins000.exe"]

    def test_msiexec_conserve_ses_accolades(self):
        from optimizer.app_manager import _split_command_line
        argv = _split_command_line("MsiExec.exe /X{12345678-1234-1234-1234-123456789012}")
        assert argv == ["MsiExec.exe", "/X{12345678-1234-1234-1234-123456789012}"]

    def test_les_metacaracteres_restent_de_simples_arguments(self):
        """Le point de sécurité : découper ne doit jamais interpréter. Les
        opérateurs de shell doivent survivre comme texte inerte, puisque
        aucun shell ne les verra."""
        from optimizer.app_manager import _split_command_line
        argv = _split_command_line(r'"C:\App\x.exe" "&" "|" "&&calc.exe"')
        assert argv == [r"C:\App\x.exe", "&", "|", "&&calc.exe"]
