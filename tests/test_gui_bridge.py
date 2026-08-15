"""
test_gui_bridge.py
Tests du backend de l'interface web locale (gui/server.py, gui/bridge.py,
gui/jobs.py), centrés sur les quatre garanties du contrat d'API :

1. **Jeton de session** — tout `/api/*` sans en-tête `X-AZ-Token` valide → 403.
2. **Cycle de vie des confirm_tokens** — usage unique, expiration 5 min,
   liaison à l'action ET aux paramètres : c'est la garantie de sécurité
   centrale du projet (aucune destruction sans double validation).
3. **Refus de traversée de répertoire** — le service statique ne sort
   jamais de `gui/web/`.
4. **Dégradation propre d'un module absent** — une dépendance plateforme
   manquante (winreg, watchdog, yara) produit
   `{"ok": false, "unavailable": true, ...}` en HTTP 200, jamais une
   exception ni un 500.

Ces tests n'exigent NI Windows, NI watchdog, NI yara : tout ce qui dépend
de la plateforme est simulé en injectant une indisponibilité dans le
bridge, afin que la suite se comporte identiquement partout.
"""

import http.client
import json
import threading
import time

import pytest

from gui.bridge import Bridge, ConfirmTokenStore, ModuleUnavailable, fingerprint
from gui.jobs import JobManager
from gui.server import HOST, TOKEN_HEADER, create_server, safe_web_path


# ── Serveur de test : port éphémère, arrêté en fin de session ────────
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


def request(port, method, path, token=None, body=None):
    """Petit client HTTP stdlib. Retourne (statut, corps JSON ou brut)."""
    conn = http.client.HTTPConnection(HOST, port, timeout=10)
    headers = {}
    payload = None
    if token is not None:
        headers[TOKEN_HEADER] = token
    if body is not None:
        payload = json.dumps(body).encode("utf-8")
        headers["Content-Type"] = "application/json"
    try:
        conn.request(method, path, body=payload, headers=headers)
        response = conn.getresponse()
        raw = response.read().decode("utf-8", errors="replace")
        try:
            return response.status, json.loads(raw)
        except ValueError:
            return response.status, raw
    finally:
        conn.close()


# ═══════════════════════════════════════════════════════════════════
# 1. Validation du jeton de session
# ═══════════════════════════════════════════════════════════════════
class TestSessionToken:

    def test_le_serveur_ecoute_uniquement_sur_la_boucle_locale(self, live_server):
        httpd, _token, _port = live_server
        assert httpd.server_address[0] == "127.0.0.1", (
            "Le serveur ne doit JAMAIS être exposé au-delà de la boucle locale."
        )

    def test_api_sans_jeton_est_refusee(self, live_server):
        _httpd, _token, port = live_server
        status, payload = request(port, "POST", "/api/status", token=None, body={})
        assert status == 403
        assert payload["ok"] is False

    def test_api_avec_mauvais_jeton_est_refusee(self, live_server):
        _httpd, token, port = live_server
        status, _ = request(port, "POST", "/api/status", token="jeton-bidon", body={})
        assert status == 403
        # Un jeton de même longueur ne doit pas passer davantage.
        status, _ = request(port, "POST", "/api/status", token="x" * len(token), body={})
        assert status == 403

    def test_api_avec_bon_jeton_repond_le_contrat(self, live_server):
        _httpd, token, port = live_server
        status, payload = request(port, "POST", "/api/status", token=token, body={})
        assert status == 200
        assert payload["ok"] is True
        data = payload["data"]
        for champ in ("platform", "is_admin", "modules", "signatures",
                      "quarantine_count", "staging_count", "realtime_active",
                      "shield_active", "vt_configured"):
            assert champ in data, f"Champ `{champ}` absent de la réponse status."
        assert set(data["signatures"]) >= {"hashes", "yara_rules", "last_update"}
        for nom, etat in data["modules"].items():
            assert "available" in etat and "reason" in etat

    def test_le_jeton_est_verifie_avant_toute_lecture_du_corps(self, live_server):
        """Un corps illisible ne doit pas être analysé sans jeton valide."""
        _httpd, _token, port = live_server
        conn = http.client.HTTPConnection(HOST, port, timeout=10)
        try:
            conn.request("POST", "/api/status", body=b"{ ceci n'est pas du JSON",
                         headers={"Content-Type": "application/json"})
            assert conn.getresponse().status == 403
        finally:
            conn.close()

    def test_action_inconnue_donne_404(self, live_server):
        _httpd, token, port = live_server
        status, payload = request(port, "POST", "/api/action_qui_nexiste_pas", token=token, body={})
        assert status == 404
        assert payload["ok"] is False

    def test_la_page_servie_contient_le_jeton_injecte(self, live_server):
        """Le frontend reçoit le jeton par injection, pas par une route API."""
        _httpd, token, port = live_server
        status, body = request(port, "GET", "/")
        assert status == 200
        assert token in body


# ═══════════════════════════════════════════════════════════════════
# 2. Cycle de vie des confirm_tokens (double validation destructive)
# ═══════════════════════════════════════════════════════════════════
class TestConfirmTokenStore:

    def test_un_jeton_valide_est_accepte_une_fois(self):
        store = ConfirmTokenStore()
        fp = fingerprint({"id": "abc"})
        token = store.issue("quarantine_delete", fp)
        accepte, motif = store.consume(token, "quarantine_delete", fp)
        assert accepte is True and motif == ""

    def test_le_rejeu_du_meme_jeton_est_refuse(self):
        """Usage unique : c'est ce qui empêche une double suppression."""
        store = ConfirmTokenStore()
        fp = fingerprint({"id": "abc"})
        token = store.issue("quarantine_delete", fp)
        assert store.consume(token, "quarantine_delete", fp)[0] is True
        accepte, motif = store.consume(token, "quarantine_delete", fp)
        assert accepte is False
        assert "utilis" in motif or "invalide" in motif

    def test_un_jeton_expire_est_refuse(self):
        store = ConfirmTokenStore(ttl=0.05)
        fp = fingerprint({"id": "abc"})
        token = store.issue("staging_purge", fp)
        time.sleep(0.15)
        accepte, motif = store.consume(token, "staging_purge", fp)
        assert accepte is False
        assert "expir" in motif

    def test_un_jeton_absent_ou_vide_est_refuse(self):
        store = ConfirmTokenStore()
        fp = fingerprint({})
        for mauvais in (None, "", "  ", 12345, [], {}):
            assert store.consume(mauvais, "clean_full", fp)[0] is False

    def test_un_jeton_inconnu_est_refuse(self):
        store = ConfirmTokenStore()
        assert store.consume("jeton-jamais-emis", "clean_full", fingerprint({}))[0] is False

    def test_un_jeton_ne_vaut_que_pour_son_action(self):
        store = ConfirmTokenStore()
        fp = fingerprint({"id": "abc"})
        token = store.issue("quarantine_delete", fp)
        accepte, motif = store.consume(token, "staging_purge", fp)
        assert accepte is False
        assert "action" in motif

    def test_un_jeton_ne_vaut_que_pour_ses_parametres(self):
        """Empêche de valider la suppression de A puis de rejouer sur B."""
        store = ConfirmTokenStore()
        token = store.issue("quarantine_delete", fingerprint({"id": "cible-A"}))
        accepte, motif = store.consume(token, "quarantine_delete", fingerprint({"id": "cible-B"}))
        assert accepte is False
        assert "param" in motif

    def test_un_jeton_rate_reste_consomme(self):
        """Un jeton présenté pour la mauvaise cible est brûlé : pas de
        seconde chance pour deviner les bons paramètres."""
        store = ConfirmTokenStore()
        fp_a = fingerprint({"id": "A"})
        token = store.issue("quarantine_delete", fp_a)
        assert store.consume(token, "quarantine_delete", fingerprint({"id": "B"}))[0] is False
        assert store.consume(token, "quarantine_delete", fp_a)[0] is False

    def test_dry_run_et_confirm_token_hors_empreinte(self):
        """L'empreinte doit être identique entre l'appel dry_run et
        l'appel d'exécution, qui ne diffèrent que par ces deux champs."""
        base = {"id": "abc", "older_than_days": 30}
        assert fingerprint({**base, "dry_run": True}) == \
               fingerprint({**base, "dry_run": False, "confirm_token": "zzz"})
        assert fingerprint(base) != fingerprint({**base, "id": "autre"})


class TestDoubleValidationDestructive:
    """Le parcours complet dry_run → exécution, sur un cas réel."""

    @pytest.fixture
    def bridge(self, tmp_path, monkeypatch):
        import gui.bridge as bridge_mod
        # Isole totalement l'état (staging, cache) du dépôt réel.
        monkeypatch.setattr(bridge_mod, "STAGING_DIR", tmp_path / "staging")
        monkeypatch.setattr(bridge_mod, "CACHE_DIR", tmp_path / "cache")
        return Bridge()

    def test_dry_run_est_le_defaut_et_ne_touche_a_rien(self, bridge, tmp_path):
        cible = tmp_path / "a_supprimer.txt"
        cible.write_text("contenu précieux", encoding="utf-8")

        reponse = bridge.dispatch("triage_apply", {"files": [str(cible)]})

        assert reponse["ok"] is True
        assert reponse["data"]["dry_run"] is True
        assert reponse["data"]["confirm_token"]
        assert reponse["data"]["expires_in"] == 300
        assert cible.exists(), "Un dry_run ne doit RIEN déplacer ni supprimer."
        assert cible.read_text(encoding="utf-8") == "contenu précieux"

    def test_execution_sans_confirm_token_est_refusee(self, bridge, tmp_path):
        cible = tmp_path / "b.txt"
        cible.write_text("x", encoding="utf-8")

        reponse = bridge.dispatch("triage_apply", {"files": [str(cible)], "dry_run": False})

        assert reponse["ok"] is False
        assert "confirm_token" in reponse["error"]
        assert cible.exists()

    def test_cycle_complet_puis_rejeu_refuse(self, bridge, tmp_path):
        cible = tmp_path / "c.txt"
        cible.write_text("x", encoding="utf-8")
        params = {"files": [str(cible)]}

        plan = bridge.dispatch("triage_apply", dict(params))
        token = plan["data"]["confirm_token"]
        assert cible.exists()

        execution = bridge.dispatch("triage_apply", {**params, "dry_run": False, "confirm_token": token})
        assert execution["ok"] is True
        assert execution["data"]["dry_run"] is False
        assert execution["data"]["result"]["staged_count"] == 1
        assert not cible.exists(), "L'exécution réelle aurait dû déplacer le fichier."

        rejeu = bridge.dispatch("triage_apply", {**params, "dry_run": False, "confirm_token": token})
        assert rejeu["ok"] is False, "Un confirm_token consommé doit être refusé."

    def test_valeur_inattendue_de_dry_run_retombe_en_simulation(self, bridge, tmp_path):
        """Seul `false` booléen déclenche l'exécution : une chaîne, None ou
        un entier retombent côté sûr."""
        cible = tmp_path / "d.txt"
        cible.write_text("x", encoding="utf-8")
        for valeur in ("false", None, 0, "", "no"):
            reponse = bridge.dispatch("triage_apply", {"files": [str(cible)], "dry_run": valeur})
            assert reponse["data"]["dry_run"] is True, f"dry_run={valeur!r} n'a pas été traité comme une simulation"
            assert cible.exists()


# ═══════════════════════════════════════════════════════════════════
# 3. Refus de traversée de répertoire
# ═══════════════════════════════════════════════════════════════════
class TestTraverseeDeRepertoire:

    CHEMINS_INTERDITS = [
        "/../../etc/passwd",
        "/../gui/bridge.py",
        "/..%2f..%2fetc/passwd",
        "/%2e%2e/%2e%2e/etc/passwd",
        "/sous/../../secret",
        "/..\\..\\windows\\win.ini",
        "/a/b/../../../../../etc/shadow",
    ]

    @pytest.mark.parametrize("chemin", CHEMINS_INTERDITS)
    def test_les_chemins_traversants_sont_refuses(self, chemin):
        assert safe_web_path(chemin) is None, f"{chemin} aurait dû être refusé."

    @pytest.mark.parametrize("chemin", ["/", "/index.html", "/app.js", "/css/style.css"])
    def test_les_chemins_legitimes_restent_dans_gui_web(self, chemin):
        from gui.server import WEB_DIR
        resolu = safe_web_path(chemin)
        assert resolu is not None
        racine = WEB_DIR.resolve()
        assert resolu == racine or racine in resolu.parents

    def test_octet_nul_refuse(self):
        assert safe_web_path("/index.html\0.png") is None

    @pytest.mark.parametrize("chemin", CHEMINS_INTERDITS)
    def test_le_serveur_repond_403_sur_traversee(self, live_server, chemin):
        _httpd, _token, port = live_server
        status, _ = request(port, "GET", chemin)
        assert status == 403, f"{chemin} n'a pas été bloqué par le serveur."

    def test_aucun_fichier_du_projet_nest_servi(self, live_server):
        """Même sans '..', on ne doit jamais atteindre le code source."""
        _httpd, _token, port = live_server
        for chemin in ("/bridge.py", "/server.py", "/main.py", "/API_CONTRACT.md"):
            status, _ = request(port, "GET", chemin)
            assert status in (403, 404), f"{chemin} ne doit pas être servi (statut {status})."

    def test_page_dattente_si_le_frontend_est_absent(self, live_server, monkeypatch, tmp_path):
        """Le backend doit rester utilisable avant l'arrivée de gui/web/."""
        import gui.server as server_mod
        vide = tmp_path / "web_vide"
        vide.mkdir()
        monkeypatch.setattr(server_mod, "WEB_DIR", vide)
        _httpd, token, port = live_server
        status, body = request(port, "GET", "/")
        assert status == 200
        assert token in body, "La page d'attente doit elle aussi porter le jeton."


# ═══════════════════════════════════════════════════════════════════
# 4. Dégradation propre d'un module absent
# ═══════════════════════════════════════════════════════════════════
class TestDegradationModuleAbsent:
    """Simulée par injection, pour que le test vaille sur TOUTE plateforme."""

    MOTIF = "winreg absent (simulé pour le test)"

    @pytest.fixture
    def bridge_ampute(self):
        bridge = Bridge()
        for cle in ("startup_manager", "app_manager", "residue_cleaner",
                    "task_scheduler", "realtime_monitor"):
            bridge._module_errors[cle] = self.MOTIF
        return bridge

    @pytest.mark.parametrize("action,params", [
        ("startup_list", {}),
        ("startup_restore", {"hive": "HKCU", "name": "Truc"}),
        ("apps_list", {"sort_by": "size"}),
        ("residue_shortcuts", {}),
        ("residue_registry", {}),
        ("residue_folders", {}),
        ("schedule_remove", {}),
        ("schedule_cleanup", {"day": "SUN", "time": "09:00"}),
        ("guardian_unschedule", {}),
        ("realtime_start", {"folders": ["."]}),
    ])
    def test_action_indisponible_renvoie_le_contrat(self, bridge_ampute, action, params):
        reponse = bridge_ampute.dispatch(action, params)
        assert reponse is not None, f"{action} doit exister dans le bridge."
        assert reponse["ok"] is False
        assert reponse["unavailable"] is True
        assert reponse["reason"], "Le motif d'indisponibilité doit être renseigné."

    def test_action_destructive_indisponible_nemet_pas_de_jeton(self, bridge_ampute):
        """Un module absent ne doit pas produire de confirm_token utilisable."""
        reponse = bridge_ampute.dispatch("startup_disable", {
            "hive": "HKCU", "key_path": r"Software\Test", "name": "Truc"})
        assert reponse["unavailable"] is True
        assert "confirm_token" not in reponse.get("data", {})
        assert bridge_ampute.confirm.pending_count() == 0

    def test_status_reste_disponible_et_signale_les_manques(self, bridge_ampute):
        reponse = bridge_ampute.dispatch("status", {})
        assert reponse["ok"] is True
        modules = reponse["data"]["modules"]
        assert modules["startup_manager"]["available"] is False
        assert modules["startup_manager"]["reason"] == self.MOTIF

    def test_une_exception_metier_ne_remonte_jamais_en_500(self):
        """Le bridge convertit toute erreur inattendue en enveloppe `ok:false`."""
        bridge = Bridge()

        def exploser(_params):
            raise RuntimeError("panne simulée du module métier")

        bridge.a_action_test = exploser
        reponse = bridge.dispatch("action_test", {})
        assert reponse["ok"] is False
        assert reponse["unavailable"] is False
        assert "panne simulée" in reponse["error"]

    def test_parametres_manquants_donnent_une_erreur_lisible(self):
        bridge = Bridge()
        for action in ("scan_file", "quarantine_restore", "phishing_check", "organize_undo"):
            reponse = bridge.dispatch(action, {})
            assert reponse["ok"] is False
            assert reponse["unavailable"] is False

    def test_toutes_les_actions_du_contrat_sont_implementees(self):
        attendues = {
            "status", "scan_file", "scan_directory", "realtime_start", "realtime_stop",
            "quarantine_list", "quarantine_restore", "quarantine_delete", "clean_full",
            "startup_list", "startup_disable", "startup_restore", "disk_analyze",
            "schedule_cleanup", "schedule_remove", "triage_scan", "triage_apply",
            "staging_list", "staging_restore", "staging_purge", "shield_start",
            "shield_status", "shield_processes", "shield_stop", "reputation_check",
            "reputation_configured", "phishing_check", "organize_plan", "organize_apply",
            "organize_move_folder", "organize_least_used", "organize_sessions",
            "organize_undo", "guardian_run", "guardian_pending", "guardian_confirm",
            "guardian_schedule", "guardian_unschedule", "apps_list", "apps_uninstall",
            "apps_debloat", "residue_shortcuts", "residue_registry", "residue_folders",
            "residue_clean", "job", "job_cancel",
        }
        manquantes = attendues - set(Bridge().known_actions())
        assert not manquantes, f"Actions du contrat non implémentées : {sorted(manquantes)}"


# ═══════════════════════════════════════════════════════════════════
# 5. Tâches asynchrones (job_id / progression / annulation)
# ═══════════════════════════════════════════════════════════════════
class TestJobs:

    def test_progression_et_resultat(self):
        manager = JobManager()

        def worker(job):
            job.set_total(4)
            for i in range(4):
                job.check_cancel()
                job.step(f"element-{i}")
            return {"traites": 4}

        job_id = manager.submit("test", worker)
        for _ in range(50):
            snap = manager.snapshot(job_id)
            if snap["state"] != "running":
                break
            time.sleep(0.02)

        assert snap["state"] == "done"
        assert snap["progress"] == 1.0
        assert snap["done"] == 4 and snap["total"] == 4
        assert snap["result"] == {"traites": 4}
        assert snap["error"] is None

    def test_annulation(self):
        manager = JobManager()
        demarre = threading.Event()

        def worker(job):
            demarre.set()
            for _ in range(1000):
                job.check_cancel()
                time.sleep(0.01)
            return "jamais atteint"

        job_id = manager.submit("test", worker)
        demarre.wait(timeout=5)
        assert manager.cancel(job_id) is True

        for _ in range(100):
            snap = manager.snapshot(job_id)
            if snap["state"] != "running":
                break
            time.sleep(0.02)

        assert snap["state"] == "error"
        assert snap["cancelled"] is True
        assert snap["result"] is None

    def test_une_exception_dans_un_job_ne_tue_pas_le_serveur(self):
        manager = JobManager()

        def worker(_job):
            raise ValueError("boum")

        job_id = manager.submit("test", worker)
        for _ in range(50):
            snap = manager.snapshot(job_id)
            if snap["state"] != "running":
                break
            time.sleep(0.02)
        assert snap["state"] == "error"
        assert "boum" in snap["error"]

    def test_job_inconnu(self):
        manager = JobManager()
        assert manager.snapshot("id-inexistant") is None
        assert manager.cancel("id-inexistant") is False

    def test_snapshot_respecte_le_contrat(self):
        manager = JobManager()
        job_id = manager.submit("test", lambda job: "ok")
        time.sleep(0.1)
        snap = manager.snapshot(job_id)
        for champ in ("state", "progress", "current", "done", "total", "result", "error"):
            assert champ in snap
        assert snap["state"] in ("running", "done", "error")
        assert 0.0 <= snap["progress"] <= 1.0
