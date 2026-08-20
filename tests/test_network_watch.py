"""
Tests de security/network_watch.py.

Ce module note des connexions comme suspectes. Ses deux façons d'échouer sont
symétriques et aussi graves l'une que l'autre :

  * **manquer une vraie menace** — un faux négatif, et l'outil ne sert à rien ;
  * **crier au loup** — un faux positif, et l'utilisateur cesse de lire les
    alertes, ce qui revient au même en pire.

Les tests couvrent donc autant les scénarios malveillants que les scénarios
parfaitement banals qui ne DOIVENT PAS déclencher d'alerte.

Aucun test ne dépend des connexions réelles de la machine : `psutil` est
mocké, les scénarios sont construits de toutes pièces.
"""

from types import SimpleNamespace
from unittest.mock import Mock, patch

import pytest

from security.network_watch import (
    NetworkWatch, PORTS_SUSPECTS, PROCESSUS_SANS_RESEAU,
    SEUIL_SUSPECT, SEUIL_A_EXAMINER, _distance_edition,
)


def _adr(ip, port):
    return SimpleNamespace(ip=ip, port=port)


def _conn(pid, ip_distant, port_distant, statut="ESTABLISHED"):
    return SimpleNamespace(
        pid=pid, status=statut,
        laddr=_adr("192.168.1.20", 51000),
        raddr=_adr(ip_distant, port_distant),
    )


def _faux_processus(nom, chemin=""):
    p = Mock()
    p.name.return_value = nom
    p.exe.return_value = chemin
    return p


def _scenario(connexions, processus_par_pid, monkeypatch):
    """Installe un faux état réseau et retourne le résultat de l'inventaire."""
    import security.network_watch as nw

    monkeypatch.setattr(nw.psutil, "net_connections", lambda kind=None: connexions)
    monkeypatch.setattr(nw.psutil, "CONN_ESTABLISHED", "ESTABLISHED", raising=False)

    def faux_process(pid):
        if pid in processus_par_pid:
            return processus_par_pid[pid]
        raise nw.psutil.NoSuchProcess(pid)

    monkeypatch.setattr(nw.psutil, "Process", faux_process)
    return NetworkWatch(resoudre_noms=False).lister_connexions()


# ═══════════════════════════════════════════════════════════════════
# Distance d'édition — socle de la détection d'imitation
# ═══════════════════════════════════════════════════════════════════
class TestDistanceEdition:
    @pytest.mark.parametrize("a,b,attendu", [
        ("svchost.exe", "svchost.exe", 0),
        ("svch0st.exe", "svchost.exe", 1),      # zéro à la place du o
        ("winlogin.exe", "winlogon.exe", 1),    # i à la place du o
        ("1sass.exe", "lsass.exe", 1),          # chiffre 1 à la place du L
        ("chrome.exe", "svchost.exe", 5),
    ])
    def test_valeurs_de_reference(self, a, b, attendu):
        assert _distance_edition(a, b) == attendu

    def test_symetrique(self):
        assert _distance_edition("abc", "abcd") == _distance_edition("abcd", "abc")


# ═══════════════════════════════════════════════════════════════════
# Détection d'imitation de composants Windows
# ═══════════════════════════════════════════════════════════════════
class TestImitation:
    @pytest.mark.parametrize("faux,vrai", [
        ("svch0st.exe", "svchost.exe"),
        ("winlogin.exe", "winlogon.exe"),
        ("1sass.exe", "lsass.exe"),
        ("explorer1.exe", "explorer.exe"),
    ])
    def test_les_imitations_sont_reperees(self, faux, vrai):
        assert NetworkWatch()._nom_imite(faux) == vrai

    @pytest.mark.parametrize("vrai", [
        "svchost.exe", "csrss.exe", "lsass.exe", "explorer.exe",
    ])
    def test_les_vrais_noms_ne_sont_pas_signales(self, vrai):
        """Un composant authentique ne doit JAMAIS être pris pour son imitation."""
        assert NetworkWatch()._nom_imite(vrai) is None

    @pytest.mark.parametrize("normal", [
        "firefox.exe", "chrome.exe", "code.exe", "python.exe", "steam.exe",
    ])
    def test_aucun_faux_positif_sur_des_programmes_courants(self, normal):
        assert NetworkWatch()._nom_imite(normal) is None


# ═══════════════════════════════════════════════════════════════════
# Scénarios malveillants
# ═══════════════════════════════════════════════════════════════════
class TestScenariosMalveillants:
    def test_composant_systeme_qui_communique(self, monkeypatch):
        """`lsass.exe` qui ouvre une connexion sortante : sur une machine de
        particulier, c'est une usurpation quasi certaine."""
        nom = sorted(PROCESSUS_SANS_RESEAU)[0]
        res = _scenario([_conn(100, "45.33.32.156", 443)],
                        {100: _faux_processus(nom)}, monkeypatch)

        c = res["data"]["connexions"][0]
        assert c["niveau"] == "a_examiner"
        assert any("aucune raison de communiquer" in r for r in c["raisons"])

    def test_port_de_porte_derobee(self, monkeypatch):
        port = sorted(PORTS_SUSPECTS)[0]
        res = _scenario([_conn(200, "45.33.32.156", port)],
                        {200: _faux_processus("updater.exe")}, monkeypatch)

        c = res["data"]["connexions"][0]
        assert c["score"] >= SEUIL_SUSPECT
        assert any(str(port) in r for r in c["raisons"])

    def test_executable_dans_un_dossier_temporaire(self, monkeypatch):
        res = _scenario(
            [_conn(300, "45.33.32.156", 443)],
            {300: _faux_processus("setup.exe", r"C:\Users\Zeev\AppData\Local\Temp\setup.exe")},
            monkeypatch)

        c = res["data"]["connexions"][0]
        assert any("temporaire" in r for r in c["raisons"])

    def test_les_signaux_se_cumulent(self, monkeypatch):
        """Le cœur de la méthode : un signal isolé ne prouve rien, leur
        accumulation sur une même connexion mérite un examen."""
        port = sorted(PORTS_SUSPECTS)[0]
        res = _scenario(
            [_conn(400, "45.33.32.156", port)],
            {400: _faux_processus("svch0st.exe", r"C:\Windows\Temp\svch0st.exe")},
            monkeypatch)

        c = res["data"]["connexions"][0]
        assert c["niveau"] == "a_examiner"
        assert len(c["raisons"]) >= 3, "les trois signaux doivent être rapportés"

    def test_tri_par_gravite_decroissante(self, monkeypatch):
        port = sorted(PORTS_SUSPECTS)[0]
        res = _scenario(
            [_conn(1, "45.33.32.156", 443), _conn(2, "45.33.32.157", port)],
            {1: _faux_processus("firefox.exe", r"C:\Program Files\Mozilla\firefox.exe"),
             2: _faux_processus("svch0st.exe", r"C:\Windows\Temp\x.exe")},
            monkeypatch)

        scores = [c["score"] for c in res["data"]["connexions"]]
        assert scores == sorted(scores, reverse=True)
        assert res["data"]["connexions"][0]["processus"] == "svch0st.exe"


# ═══════════════════════════════════════════════════════════════════
# Scénarios banals — ne doivent RIEN déclencher
# ═══════════════════════════════════════════════════════════════════
class TestAucunFauxPositif:
    def test_navigateur_vers_le_web(self, monkeypatch):
        res = _scenario(
            [_conn(500, "142.250.75.206", 443)],
            {500: _faux_processus("firefox.exe", r"C:\Program Files\Mozilla\firefox.exe")},
            monkeypatch)

        assert res["data"]["connexions"][0]["niveau"] == "normal"

    def test_connexion_vers_le_reseau_local_hors_perimetre(self, monkeypatch):
        """Une imprimante, un NAS, un routeur : ça ne sort pas du domicile.
        Même un port bizarre n'y est pas un signal."""
        port = sorted(PORTS_SUSPECTS)[0]
        res = _scenario([_conn(600, "192.168.1.50", port)],
                        {600: _faux_processus("appli.exe")}, monkeypatch)

        c = res["data"]["connexions"][0]
        assert c["score"] == 0
        assert c["niveau"] == "normal"

    def test_boucle_locale_ignoree(self, monkeypatch):
        res = _scenario([_conn(700, "127.0.0.1", 8777)],
                        {700: _faux_processus("python.exe")}, monkeypatch)
        assert res["data"]["connexions"][0]["score"] == 0

    def test_connexions_non_etablies_exclues(self, monkeypatch):
        """Une connexion en attente n'est pas une communication en cours."""
        res = _scenario([_conn(800, "45.33.32.156", 443, statut="LISTEN")],
                        {800: _faux_processus("serveur.exe")}, monkeypatch)
        assert res["data"]["total"] == 0


# ═══════════════════════════════════════════════════════════════════
# Robustesse
# ═══════════════════════════════════════════════════════════════════
class TestRobustesse:
    def test_processus_disparu_entre_temps(self, monkeypatch):
        """Un processus peut se terminer entre le relevé et l'interrogation.
        La connexion doit rester listée, pas faire échouer l'inventaire."""
        res = _scenario([_conn(999, "45.33.32.156", 443)], {}, monkeypatch)

        assert res["ok"] is True
        c = res["data"]["connexions"][0]
        assert c["processus"] == "inconnu"
        assert any("non identifiable" in r for r in c["raisons"])

    def test_droits_insuffisants_signales_proprement(self, monkeypatch):
        """Sans élévation, Windows ne montre qu'une partie des connexions.
        Rendre une liste tronquée sans le dire serait trompeur."""
        import security.network_watch as nw

        def refuse(kind=None):
            raise nw.psutil.AccessDenied()

        monkeypatch.setattr(nw.psutil, "net_connections", refuse)
        res = NetworkWatch().lister_connexions()

        assert res["ok"] is False
        assert res["unavailable"] is True
        assert "administrateur" in res["reason"]

    def test_psutil_absent(self, monkeypatch):
        import security.network_watch as nw
        monkeypatch.setattr(nw, "PSUTIL_AVAILABLE", False)
        res = NetworkWatch().lister_connexions()
        assert res["ok"] is False and res["unavailable"] is True

    def test_le_module_ne_bloque_ni_ne_modifie_rien(self):
        """Garde-fou d'intention : ce module OBSERVE. Toute apparition de
        subprocess ou d'une règle de pare-feu ici est une faute de conception —
        le blocage appartient à app_firewall.py."""
        import inspect
        import security.network_watch as nw

        source = inspect.getsource(nw)
        for interdit in ("subprocess", "netsh", "os.system", "NetFirewall"):
            assert interdit not in source, f"{interdit} n'a rien à faire dans un observateur"


# ═══════════════════════════════════════════════════════════════════
# Vue par application
# ═══════════════════════════════════════════════════════════════════
class TestVueParApplication:
    def test_regroupement_et_score_maximal(self, monkeypatch):
        """On ne bloque pas une connexion, on bloque une application : la vue
        agrégée doit retenir le PIRE score de ses connexions."""
        import security.network_watch as nw
        port = sorted(PORTS_SUSPECTS)[0]
        connexions = [_conn(1, "45.33.32.156", 443), _conn(1, "45.33.32.157", port)]
        proc = _faux_processus("agent.exe", r"C:\Temp\agent.exe")

        monkeypatch.setattr(nw.psutil, "net_connections", lambda kind=None: connexions)
        monkeypatch.setattr(nw.psutil, "CONN_ESTABLISHED", "ESTABLISHED", raising=False)
        monkeypatch.setattr(nw.psutil, "Process", lambda pid: proc)

        res = NetworkWatch(resoudre_noms=False).resumer_par_application()

        apps = res["data"]["applications"]
        assert len(apps) == 1
        assert apps[0]["connexions"] == 2
        assert len(apps[0]["destinations"]) == 2
        assert apps[0]["score_max"] >= SEUIL_SUSPECT


class TestDeterminisme:
    """Un antivirus doit désigner LE MÊME coupable à chaque exécution.

    `_nom_imite` parcourait l'ensemble `NOMS_SYSTEME_IMITES` et renvoyait la
    première correspondance sous le seuil. Un `set` Python n'ayant pas d'ordre
    stable d'une exécution à l'autre (aléa de hachage), « 1sass.exe » était
    attribué tantôt à « lsass.exe » (distance 1), tantôt à « csrss.exe »
    (distance 2). Le défaut échappait aux tests, qui passaient une fois sur
    deux selon PYTHONHASHSEED.
    """

    def test_la_reference_la_plus_proche_gagne(self):
        """« 1sass.exe » est à distance 1 de lsass et 2 de csrss : lsass doit
        gagner, quel que soit l'ordre de parcours."""
        w = NetworkWatch()
        assert _distance_edition("1sass.exe", "lsass.exe") == 1
        assert _distance_edition("1sass.exe", "csrss.exe") == 2
        assert w._nom_imite("1sass.exe") == "lsass.exe"

    def test_stabilite_sous_permutation_de_l_ensemble(self):
        """Simule l'ordre d'itération variable d'un set en remplaçant la
        source par des séquences ordonnées différemment."""
        import security.network_watch as nw
        original = nw.NOMS_SYSTEME_IMITES
        try:
            resultats = set()
            for ordre in (sorted(original), sorted(original, reverse=True),
                          list(original)):
                nw.NOMS_SYSTEME_IMITES = list(ordre)
                resultats.add(NetworkWatch()._nom_imite("1sass.exe"))
            assert resultats == {"lsass.exe"}, f"résultat instable : {resultats}"
        finally:
            nw.NOMS_SYSTEME_IMITES = original
