"""
Tests de security/intrusion_check.py.

Ce module s'adresse à quelqu'un d'inquiet. Ses deux fautes possibles sont donc
particulièrement coûteuses :

  * **affoler pour rien** — signaler comme intrusion un logiciel installé
    volontairement, ou une connexion locale banale ;
  * **rassurer à tort** — laisser croire qu'on saurait dire QUI, ou quels
    documents ont été lus, alors que ces informations n'existent pas.

Les tests vérifient donc autant les détections que les non-détections, et que
les limites sont bien annoncées dans le rapport.

Rien ne dépend de Windows : `quser`, PowerShell et `auditpol` sont remplacés
par un exécuteur injecté.
"""

import json
from types import SimpleNamespace
from unittest.mock import Mock

import pytest

from security.intrusion_check import (
    IntrusionCheck, LOGICIELS_ACCES_DISTANT, PORTS_ACCES_DISTANT,
    TYPES_DE_CONNEXION, TYPES_A_DISTANCE,
)


def _runner(reponses):
    """Exécuteur factice. `reponses` : liste de (motif, code, sortie)."""
    def executer(commande, timeout=None):
        joint = " ".join(commande).lower()
        for motif, code, sortie in reponses:
            if motif.lower() in joint:
                return {"code": code, "sortie": sortie, "erreur": ""}
        return {"code": -1, "sortie": "", "erreur": "commande introuvable"}
    return executer


def _adr(ip, port):
    return SimpleNamespace(ip=ip, port=port)


def _conn(statut, lport, rip=None, rport=None, pid=None):
    return SimpleNamespace(
        status=statut, pid=pid,
        laddr=_adr("0.0.0.0", lport),
        raddr=_adr(rip, rport) if rip else None,
    )


def _evenement(ident, type_connexion, compte="zeev", adresse="", source=""):
    return {"Id": ident, "Date": "2026-08-20T14:30:00", "Compte": compte,
            "Domaine": "MACHINE", "Type": str(type_connexion),
            "Source": source, "Adresse": adresse, "Processus": ""}


# ═══════════════════════════════════════════════════════════════════
# Sessions à distance ouvertes
# ═══════════════════════════════════════════════════════════════════
class TestSessionsActives:
    SORTIE = (
        " UTILISATEUR      SESSION     ID  ÉTAT   TEMPS INACTIF\n"
        ">zeev             console      1  Actif        .\n"
        " intrus           rdp-tcp#3    2  Actif       12\n"
    )

    def test_une_session_bureau_a_distance_est_reperee(self):
        ic = IntrusionCheck(runner=_runner([("quser", 0, self.SORTIE)]))
        d = ic.sessions_actives()["data"]

        assert d["distantes"] == 1
        distante = [s for s in d["sessions"] if s["distante"]][0]
        assert distante["utilisateur"] == "intrus"

    def test_la_session_locale_n_est_pas_signalee_comme_distante(self):
        ic = IntrusionCheck(runner=_runner([("quser", 0, self.SORTIE)]))
        locale = [s for s in ic.sessions_actives()["data"]["sessions"]
                  if s["utilisateur"] == "zeev"][0]
        assert locale["distante"] is False

    def test_hors_windows_degrade_proprement(self):
        ic = IntrusionCheck(runner=_runner([]))
        r = ic.sessions_actives()
        assert r["ok"] is False and r["unavailable"] is True


# ═══════════════════════════════════════════════════════════════════
# Logiciels d'accès à distance
# ═══════════════════════════════════════════════════════════════════
class TestLogicielsAccesDistant:
    def test_un_logiciel_connu_est_repere(self, monkeypatch):
        import security.intrusion_check as ic_mod
        faux = SimpleNamespace(info={"pid": 1234, "name": "AnyDesk.exe",
                                     "exe": r"C:\Program Files\AnyDesk\AnyDesk.exe",
                                     "create_time": 1_700_000_000,
                                     "username": "MACHINE\\zeev"})
        monkeypatch.setattr(ic_mod.psutil, "process_iter", lambda attrs=None: [faux])

        d = IntrusionCheck().logiciels_acces_distant()["data"]

        assert d["total"] == 1
        assert d["logiciels"][0]["produit"] == "AnyDesk"

    def test_un_programme_ordinaire_n_est_pas_signale(self, monkeypatch):
        """Ne pas affoler : un navigateur n'est pas un outil de prise en main."""
        import security.intrusion_check as ic_mod
        faux = SimpleNamespace(info={"pid": 1, "name": "firefox.exe", "exe": "",
                                     "create_time": 0, "username": ""})
        monkeypatch.setattr(ic_mod.psutil, "process_iter", lambda attrs=None: [faux])

        assert IntrusionCheck().logiciels_acces_distant()["data"]["total"] == 0

    def test_la_detection_est_insensible_a_la_casse(self, monkeypatch):
        import security.intrusion_check as ic_mod
        faux = SimpleNamespace(info={"pid": 2, "name": "TeamViewer.EXE", "exe": "",
                                     "create_time": 0, "username": ""})
        monkeypatch.setattr(ic_mod.psutil, "process_iter", lambda attrs=None: [faux])

        assert IntrusionCheck().logiciels_acces_distant()["data"]["total"] == 1


# ═══════════════════════════════════════════════════════════════════
# Entrant contre sortant — la distinction centrale
# ═══════════════════════════════════════════════════════════════════
class TestConnexionsEntrantes:
    def _preparer(self, connexions, monkeypatch, noms=None):
        import security.intrusion_check as ic_mod
        monkeypatch.setattr(ic_mod.psutil, "net_connections",
                            lambda kind=None: connexions)
        monkeypatch.setattr(ic_mod.psutil, "CONN_ESTABLISHED", "ESTABLISHED",
                            raising=False)
        monkeypatch.setattr(ic_mod.psutil, "CONN_LISTEN", "LISTEN", raising=False)
        monkeypatch.setattr(ic_mod.IntrusionCheck, "_nom_du_processus",
                            staticmethod(lambda pid: (noms or {}).get(pid, "inconnu")))
        return IntrusionCheck().connexions_entrantes()["data"]

    def test_connexion_vers_un_port_ecoute_est_entrante(self, monkeypatch):
        """Quelqu'un s'est connecté À NOUS sur le port RDP."""
        d = self._preparer([
            _conn("LISTEN", 3389),
            _conn("ESTABLISHED", 3389, "203.0.113.7", 51000, pid=900),
        ], monkeypatch, noms={900: "svchost.exe"})

        assert d["total_entrantes"] == 1
        assert d["entrantes"][0]["depuis"] == "203.0.113.7"
        assert d["entrantes"][0]["service"] == "Bureau à distance (RDP)"

    def test_connexion_sortante_n_est_pas_comptee(self, monkeypatch):
        """Un navigateur qui sort par un port éphémère : banal, hors sujet.
        Confondre les deux sens rendrait le module inutilisable."""
        d = self._preparer([
            _conn("ESTABLISHED", 51234, "142.250.75.206", 443, pid=100),
        ], monkeypatch)

        assert d["total_entrantes"] == 0

    def test_service_expose_signale_meme_sans_connexion(self, monkeypatch):
        """Un port d'accès distant en écoute est une porte ouverte, même si
        personne ne l'utilise à cet instant."""
        d = self._preparer([_conn("LISTEN", 5900, pid=300)], monkeypatch,
                           noms={300: "winvnc.exe"})

        assert len(d["services_exposes"]) == 1
        assert d["services_exposes"][0]["service"] == "VNC"

    def test_droits_insuffisants_annonces(self, monkeypatch):
        import security.intrusion_check as ic_mod

        def refuse(kind=None):
            raise ic_mod.psutil.AccessDenied()

        monkeypatch.setattr(ic_mod.psutil, "net_connections", refuse)
        r = IntrusionCheck().connexions_entrantes()
        assert r["unavailable"] is True and "administrateur" in r["reason"]


# ═══════════════════════════════════════════════════════════════════
# Journal de sécurité — la seule source rétrospective
# ═══════════════════════════════════════════════════════════════════
class TestJournalConnexions:
    def test_connexion_bureau_a_distance_marquee(self):
        evts = [_evenement(4624, 10, compte="intrus", adresse="203.0.113.7",
                           source="PC-INCONNU")]
        ic = IntrusionCheck(runner=_runner([("powershell", 0, json.dumps(evts))]))

        d = ic.journal_connexions()["data"]

        assert d["connexions_a_distance"] == 1
        e = d["evenements"][0]
        assert e["a_distance"] is True
        assert "BUREAU À DISTANCE" in e["type"]
        assert e["machine_source"] == "PC-INCONNU"

    def test_connexion_locale_non_marquee_a_distance(self):
        """Type 2 : quelqu'un devant le clavier. Ce n'est pas une intrusion
        réseau, et le confondre serait une fausse alerte grave."""
        evts = [_evenement(4624, 2)]
        ic = IntrusionCheck(runner=_runner([("powershell", 0, json.dumps(evts))]))

        assert ic.journal_connexions()["data"]["connexions_a_distance"] == 0

    def test_echecs_comptes_separement(self):
        evts = [_evenement(4625, 3, compte="admin") for _ in range(12)]
        ic = IntrusionCheck(runner=_runner([("powershell", 0, json.dumps(evts))]))

        assert ic.journal_connexions()["data"]["echecs_de_connexion"] == 12

    def test_creation_de_compte_libellee_clairement(self):
        evts = [_evenement(4720, 0, compte="backdoor")]
        ic = IntrusionCheck(runner=_runner([("powershell", 0, json.dumps(evts))]))

        assert "COMPTE UTILISATEUR CRÉÉ" in ic.journal_connexions()["data"]["evenements"][0]["libelle"]

    def test_un_seul_evenement_json_objet_est_accepte(self):
        """PowerShell renvoie un objet, pas un tableau, quand il n'y a qu'un
        résultat. Ne pas le gérer perdrait silencieusement l'événement."""
        ic = IntrusionCheck(runner=_runner([("powershell", 0,
                                             json.dumps(_evenement(4624, 10)))]))
        assert ic.journal_connexions()["data"]["total"] == 1

    def test_tri_du_plus_recent_au_plus_ancien(self):
        a = _evenement(4624, 10); a["Date"] = "2026-08-18T09:00:00"
        b = _evenement(4624, 10); b["Date"] = "2026-08-20T09:00:00"
        ic = IntrusionCheck(runner=_runner([("powershell", 0, json.dumps([a, b]))]))

        dates = [e["date"] for e in ic.journal_connexions()["data"]["evenements"]]
        assert dates == sorted(dates, reverse=True)

    def test_reponse_illisible_ne_fait_pas_planter(self):
        ic = IntrusionCheck(runner=_runner([("powershell", 0, "<<pas du json>>")]))
        r = ic.journal_connexions()
        assert r["ok"] is False and "illisible" in r["error"]

    def test_hors_windows_degrade_proprement(self):
        r = IntrusionCheck(runner=_runner([])).journal_connexions()
        assert r["ok"] is False and r["unavailable"] is True


# ═══════════════════════════════════════════════════════════════════
# Rapport d'ensemble
# ═══════════════════════════════════════════════════════════════════
class TestRapport:
    def test_une_source_en_panne_n_empeche_pas_les_autres(self, monkeypatch):
        """Le point le plus important du module : quelqu'un d'inquiet ne doit
        pas se retrouver devant un écran vide parce qu'une commande a échoué."""
        import security.intrusion_check as ic_mod
        monkeypatch.setattr(ic_mod.psutil, "process_iter", lambda attrs=None: [])
        monkeypatch.setattr(ic_mod.psutil, "net_connections", lambda kind=None: [])

        evts = [_evenement(4624, 10, compte="intrus", adresse="203.0.113.7")]
        # quser absent (hors Windows), journal disponible
        ic = IntrusionCheck(runner=_runner([("powershell", 0, json.dumps(evts))]))

        d = ic.rapport()["data"]

        assert d["sources"]["sessions"] != "ok", "quser doit être signalé indisponible"
        assert d["sources"]["journal"] == "ok"
        assert d["importants"] >= 1, "le constat du journal doit remonter"

    def test_les_constats_graves_sont_en_tete(self, monkeypatch):
        import security.intrusion_check as ic_mod
        faux = SimpleNamespace(info={"pid": 1, "name": "anydesk.exe", "exe": "",
                                     "create_time": 0, "username": ""})
        monkeypatch.setattr(ic_mod.psutil, "process_iter", lambda attrs=None: [faux])
        monkeypatch.setattr(ic_mod.psutil, "net_connections", lambda kind=None: [])

        evts = [_evenement(4624, 10, compte="intrus")]
        ic = IntrusionCheck(runner=_runner([
            ("quser", 0, " UTILISATEUR SESSION ID\n>zeev console 1\n"),
            ("powershell", 0, json.dumps(evts)),
        ]))

        niveaux = [c["niveau"] for c in ic.rapport()["data"]["constats"]]
        ordre = {"important": 0, "a_verifier": 1, "information": 2}
        assert niveaux == sorted(niveaux, key=lambda n: ordre[n])

    def test_le_rapport_annonce_ses_limites(self, monkeypatch):
        """Rassurer à tort est aussi grave qu'affoler : le rapport doit dire
        qu'il ne sait pas QUI, ni quels documents ont été lus."""
        import security.intrusion_check as ic_mod
        monkeypatch.setattr(ic_mod.psutil, "process_iter", lambda attrs=None: [])
        monkeypatch.setattr(ic_mod.psutil, "net_connections", lambda kind=None: [])

        avertissement = IntrusionCheck(runner=_runner([])).rapport()["data"]["avertissement"]

        assert "QUI" in avertissement
        assert "document" in avertissement.lower()

    def test_un_logiciel_de_prise_en_main_pose_la_bonne_question(self, monkeypatch):
        """Il ne faut pas l'annoncer comme une infection : c'est un outil
        légitime. La bonne formulation est une question à l'utilisateur."""
        import security.intrusion_check as ic_mod
        faux = SimpleNamespace(info={"pid": 1, "name": "teamviewer.exe", "exe": "",
                                     "create_time": 0, "username": ""})
        monkeypatch.setattr(ic_mod.psutil, "process_iter", lambda attrs=None: [faux])
        monkeypatch.setattr(ic_mod.psutil, "net_connections", lambda kind=None: [])

        constats = IntrusionCheck(runner=_runner([])).rapport()["data"]["constats"]
        logiciel = [c for c in constats if c["categorie"] == "logiciel"][0]

        assert logiciel["niveau"] == "a_verifier", "ni anodin, ni accusation"
        assert "légitime" in logiciel["detail"]
        assert "installé toi-même" in logiciel["detail"]


# ═══════════════════════════════════════════════════════════════════
# Audit des accès futurs
# ═══════════════════════════════════════════════════════════════════
class TestAuditFichiers:
    def test_le_plan_ne_modifie_rien_et_dit_ce_qu_il_fera(self):
        plan = IntrusionCheck(runner=_runner([])).preparer_audit_fichiers(
            [r"C:\Users\Zeev\Documents"])["data"]

        assert plan["reversible"] is True
        assert any("PASSÉ" in a for a in plan["avertissements"]), \
            "l'utilisateur doit comprendre que le passé est perdu"

    def test_plan_vide_refuse(self):
        r = IntrusionCheck(runner=_runner([])).preparer_audit_fichiers([])
        assert r["ok"] is False

    def test_activation_refusee_sans_droits(self):
        ic = IntrusionCheck(runner=_runner([("auditpol", 1, "")]))
        r = ic.activer_audit_fichiers({"dossiers": [r"C:\Docs"]})
        assert r["ok"] is False and "administrateur" in r["error"]

    def test_activation_nominale(self):
        ic = IntrusionCheck(runner=_runner([("auditpol", 0, ""),
                                            ("powershell", 0, "ok")]))
        d = ic.activer_audit_fichiers({"dossiers": [r"C:\Docs"]})["data"]

        assert d["dossiers_traces"] == [r"C:\Docs"]
        assert "MAINTENANT" in d["rappel"]

    def test_apostrophe_dans_le_chemin_echappee(self):
        """Un dossier « Dossier d'Éric » casserait la chaîne PowerShell et
        pourrait faire exécuter la suite comme du code."""
        assert IntrusionCheck._echapper(r"C:\Dossier d'Eric") == r"C:\Dossier d''Eric"


# ═══════════════════════════════════════════════════════════════════
# Garde-fous d'intention
# ═══════════════════════════════════════════════════════════════════
class TestGardeFous:
    def test_aucun_shell(self):
        """Toutes les commandes passent par une LISTE d'arguments sans shell :
        les chemins et noms de comptes viennent du système, donc de tiers."""
        import inspect
        import security.intrusion_check as m

        source = inspect.getsource(m)
        assert "shell=True" not in source
        assert "os.system" not in source

    def test_le_module_ne_supprime_ni_ne_bloque_rien(self):
        """Ce module CONSTATE. Bloquer relève du Mode Incident, supprimer de
        la quarantaine. Une action destructive ici serait une faute."""
        import inspect
        import security.intrusion_check as m

        source = inspect.getsource(m)
        for interdit in ("netsh advfirewall", "suspend_process", "unlink(", "rmtree"):
            assert interdit not in source, f"{interdit} n'a rien à faire ici"

    def test_types_a_distance_coherents(self):
        assert TYPES_A_DISTANCE <= set(TYPES_DE_CONNEXION)
        assert 10 in TYPES_A_DISTANCE, "RDP doit compter comme accès à distance"
        assert 2 not in TYPES_A_DISTANCE, "le clavier local n'est pas à distance"
