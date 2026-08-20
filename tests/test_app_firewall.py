"""
Tests de security/app_firewall.py.

Ce module POSE des règles sur le pare-feu de Windows. Ses fautes possibles ne
sont pas symétriques : bloquer à tort est bien plus grave que ne pas bloquer.

  * bloquer `svchost.exe` ou un composant de Windows Update coupe les
    correctifs de sécurité — l'outil rendrait alors la machine PLUS vulnérable
    qu'avant, ce qui est le pire résultat possible pour un outil de sécurité ;
  * supprimer une règle qu'il n'a pas posée rétablirait silencieusement un
    accès que l'utilisateur croit coupé, y compris celle du Mode Incident ;
  * créer un doublon ferait survivre le blocage au déblocage.

Les tests portent d'abord sur ces trois garde-fous.

`netsh` n'existe pas ici : il est remplacé par un exécuteur injecté qui
journalise les commandes, ce qui permet de vérifier ce qui AURAIT été lancé.
"""

import pytest

from security.app_firewall import (
    AppFirewall, PREFIXE, PROGRAMMES_A_NE_PAS_BLOQUER, NOM_REGLE_INCIDENT,
)


def _runner(reponses=None, journal=None):
    """Exécuteur factice. `reponses` : liste de (motif, code, sortie)."""
    reponses = reponses or []
    journal = journal if journal is not None else []

    def executer(commande, timeout=None):
        joint = " ".join(commande)
        journal.append(joint)
        for motif, code, sortie in reponses:
            if motif in joint:
                return {"code": code, "sortie": sortie, "erreur": ""}
        return {"code": 0, "sortie": "", "erreur": ""}

    executer.journal = journal
    return executer


SORTIE_REGLES_FR = """
Nom de la règle:                      AZ_APP_espion.exe_sortant_abc1234567
----------------------------------------------------------------------
Activée:                              Oui
Direction:                            Sortant
Programme:                            C:\\Temp\\espion.exe
Action:                               Bloquer

Nom de la règle:                      AZ_INCIDENT
----------------------------------------------------------------------
Activée:                              Oui
Direction:                            Sortant
Action:                               Bloquer

Nom de la règle:                      Core Networking
----------------------------------------------------------------------
Activée:                              Oui
Direction:                            Sortant
Programme:                            C:\\Windows\\System32\\svchost.exe
Action:                               Autoriser

"""

SORTIE_REGLES_EN = """
Rule Name:                            AZ_APP_agent.exe_sortant_def7654321
----------------------------------------------------------------------
Enabled:                              Yes
Direction:                            Out
Program:                              C:\\Temp\\agent.exe
Action:                               Block

"""


# ═══════════════════════════════════════════════════════════════════
# Garde-fou n°1 : ne jamais casser Windows
# ═══════════════════════════════════════════════════════════════════
class TestProgrammesProteges:
    @pytest.mark.parametrize("programme", [
        r"C:\Windows\System32\svchost.exe",
        r"C:\Windows\System32\lsass.exe",
        r"C:\Windows\System32\wuauclt.exe",
        r"C:\Program Files\Windows Defender\MsMpEng.exe",
    ])
    def test_le_plan_refuse_un_composant_essentiel(self, programme):
        r = AppFirewall(runner=_runner()).preparer_blocage(programme)
        assert r["ok"] is False
        assert "essentiel" in r["error"]

    def test_le_refus_explique_la_consequence_reelle(self):
        """Un refus sans explication pousse à chercher un contournement."""
        r = AppFirewall(runner=_runner()).preparer_blocage(
            r"C:\Windows\System32\svchost.exe")
        assert "vulnérable" in r["error"], "la conséquence doit être dite"

    def test_le_garde_fou_est_reverifie_a_l_application(self):
        """Le plan transite par l'API web, où l'appelant contrôle le
        dictionnaire : un plan fabriqué à la main ne doit pas passer."""
        plan_falsifie = {"programme": r"C:\Windows\System32\svchost.exe",
                         "sens": "sortant"}
        journal = []
        r = AppFirewall(runner=_runner(journal=journal)).bloquer(plan_falsifie)

        assert r["ok"] is False
        assert journal == [], "aucune commande ne doit avoir été lancée"

    def test_la_casse_du_nom_n_est_pas_un_contournement(self):
        r = AppFirewall(runner=_runner()).preparer_blocage(
            r"C:\Windows\System32\SVCHOST.EXE")
        assert r["ok"] is False


# ═══════════════════════════════════════════════════════════════════
# Garde-fou n°2 : ne toucher qu'à ses propres règles
# ═══════════════════════════════════════════════════════════════════
class TestPerimetreDesRegles:
    def test_refus_de_supprimer_une_regle_de_windows(self):
        journal = []
        r = AppFirewall(runner=_runner(journal=journal)).debloquer("Core Networking")

        assert r["ok"] is False
        assert PREFIXE in r["error"]
        assert journal == [], "aucune suppression ne doit être tentée"

    def test_refus_de_supprimer_la_regle_du_mode_incident(self):
        """Supprimer AZ_INCIDENT rétablirait le réseau alors que
        l'utilisateur le croit coupé — et sans passer par le rétablissement,
        donc en laissant l'état incohérent."""
        journal = []
        r = AppFirewall(runner=_runner(journal=journal)).debloquer(NOM_REGLE_INCIDENT)

        assert r["ok"] is False
        assert journal == []

    def test_la_liste_ignore_les_regles_etrangeres(self):
        r = _runner([("show rule name=all", 0, SORTIE_REGLES_FR)])
        d = AppFirewall(runner=r).lister_regles()["data"]

        noms = [x["nom"] for x in d["regles"]]
        assert len(noms) == 1
        assert noms[0].startswith(PREFIXE)
        assert NOM_REGLE_INCIDENT not in noms
        assert "Core Networking" not in noms


# ═══════════════════════════════════════════════════════════════════
# Garde-fou n°3 : idempotence
# ═══════════════════════════════════════════════════════════════════
class TestIdempotence:
    def test_bloquer_deux_fois_ne_cree_pas_de_doublon(self):
        """Un doublon survivrait au déblocage : un seul `delete` n'en retire
        qu'un, et le blocage resterait actif alors qu'on le croit levé."""
        journal = []
        r = _runner([("show rule", 0, "Nom de la règle: AZ_APP_x\nActivée: Oui\n")],
                    journal=journal)
        af = AppFirewall(runner=r)

        res = af.bloquer({"programme": r"C:\Temp\x.exe", "sens": "sortant"})

        assert res["ok"] is True
        assert res["data"]["deja_presente"] is True
        assert not any("add rule" in c for c in journal)

    def test_supprimer_une_regle_absente_est_un_succes(self):
        """L'état souhaité — la règle n'existe plus — est atteint."""
        r = _runner([("delete rule", 1, "No rules match the specified criteria.")])
        res = AppFirewall(runner=r).debloquer(f"{PREFIXE}inconnue")

        assert res["ok"] is True
        assert res["data"]["deja_absente"] is True

    def test_nom_de_regle_stable(self):
        af = AppFirewall(runner=_runner())
        a = af.nom_de_regle(r"C:\Temp\x.exe", "sortant")
        b = af.nom_de_regle(r"C:\Temp\x.exe", "sortant")
        assert a == b

    def test_deux_homonymes_dans_des_dossiers_differents_sont_distincts(self):
        """Sinon bloquer l'un débloquerait l'autre."""
        af = AppFirewall(runner=_runner())
        a = af.nom_de_regle(r"C:\Program Files\App\update.exe", "sortant")
        b = af.nom_de_regle(r"C:\Temp\update.exe", "sortant")
        assert a != b

    def test_les_deux_sens_ont_des_regles_distinctes(self):
        af = AppFirewall(runner=_runner())
        assert (af.nom_de_regle(r"C:\Temp\x.exe", "sortant")
                != af.nom_de_regle(r"C:\Temp\x.exe", "entrant"))


# ═══════════════════════════════════════════════════════════════════
# Fonctionnement nominal
# ═══════════════════════════════════════════════════════════════════
class TestBlocage:
    def test_plan_puis_application(self):
        journal = []
        af = AppFirewall(runner=_runner(journal=journal))

        plan = af.preparer_blocage(r"C:\Temp\espion.exe")["data"]
        assert plan["reversible"] is True
        assert plan["application"] == "espion.exe"
        # Le plan interroge l'état (show rule) pour rapporter `deja_bloquee` :
        # c'est de la LECTURE. L'invariant est qu'il ne MODIFIE rien.
        assert all("show rule" in c for c in journal), \
            "préparer un plan ne doit lancer que des commandes de lecture"
        assert not any(("add rule" in c or "delete rule" in c) for c in journal)

        res = af.bloquer(plan)

        assert res["ok"] is True
        commande = [c for c in journal if "add rule" in c][0]
        assert "action=block" in commande
        assert "dir=out" in commande
        assert r"program=C:\Temp\espion.exe" in commande

    def test_sens_entrant(self):
        journal = []
        af = AppFirewall(runner=_runner(journal=journal))
        plan = af.preparer_blocage(r"C:\Temp\x.exe", sens="entrant")["data"]
        af.bloquer(plan)

        assert "dir=in" in [c for c in journal if "add rule" in c][0]

    def test_sens_invalide_refuse(self):
        r = AppFirewall(runner=_runner()).preparer_blocage(r"C:\x.exe", sens="lateral")
        assert r["ok"] is False

    def test_programme_vide_refuse(self):
        assert AppFirewall(runner=_runner()).preparer_blocage("  ")["ok"] is False

    def test_droits_insuffisants_expliques(self):
        r = _runner([("add rule", 1, "")])
        res = AppFirewall(runner=r).bloquer({"programme": r"C:\Temp\x.exe",
                                             "sens": "sortant"})
        assert res["ok"] is False
        assert "administrateur" in res["error"]

    def test_plan_vide_refuse(self):
        assert AppFirewall(runner=_runner()).bloquer({})["ok"] is False


# ═══════════════════════════════════════════════════════════════════
# Lecture des règles, en français ET en anglais
# ═══════════════════════════════════════════════════════════════════
class TestLectureDesRegles:
    def test_analyse_de_la_sortie_francaise(self):
        r = _runner([("show rule name=all", 0, SORTIE_REGLES_FR)])
        regle = AppFirewall(runner=r).lister_regles()["data"]["regles"][0]

        assert regle["programme"] == r"C:\Temp\espion.exe"
        assert regle["application"] == "espion.exe"
        assert regle["sens"] == "sortant"
        assert regle["active"] is True

    def test_analyse_de_la_sortie_anglaise(self):
        """Supposer l'anglais rendrait la liste VIDE sur un Windows français,
        sans message d'erreur — personne ne s'en apercevrait."""
        r = _runner([("show rule name=all", 0, SORTIE_REGLES_EN)])
        regles = AppFirewall(runner=r).lister_regles()["data"]["regles"]

        assert len(regles) == 1
        assert regles[0]["application"] == "agent.exe"

    def test_hors_windows_degrade_proprement(self):
        def absent(commande, timeout=None):
            return {"code": -1, "sortie": "", "erreur": "commande introuvable"}

        res = AppFirewall(runner=absent).lister_regles()
        assert res["ok"] is False and res["unavailable"] is True

    def test_est_bloque(self):
        r = _runner([("show rule", 0, "Nom de la règle: AZ_APP_x\n")])
        assert AppFirewall(runner=r).est_bloque(r"C:\Temp\x.exe") is True


# ═══════════════════════════════════════════════════════════════════
# Garde-fous d'intention
# ═══════════════════════════════════════════════════════════════════
class TestGardeFousDIntention:
    def test_aucun_shell(self):
        """Les chemins de programmes viennent de l'utilisateur ou du système :
        un shell=True en ferait une injection de commande."""
        import inspect
        import security.app_firewall as m

        source = inspect.getsource(m)
        assert "shell=True" not in source
        assert "os.system" not in source

    def test_le_module_ne_coupe_pas_tout_le_reseau(self):
        """Couper l'ensemble du réseau relève du Mode Incident. Ce module
        n'agit que par programme — mélanger les deux rendrait les états
        incohérents."""
        import inspect
        import security.app_firewall as m

        source = inspect.getsource(m)
        # Une règle sans `program=` bloquerait tout le trafic.
        assert "dir=out action=block" not in source.replace('"', "").replace("'", "")

    def test_toutes_les_regles_portent_le_prefixe(self):
        af = AppFirewall(runner=_runner())
        for programme in (r"C:\a\b.exe", "x.exe", r"D:\Dossier avec espaces\y.exe"):
            for sens in ("sortant", "entrant"):
                assert af.nom_de_regle(programme, sens).startswith(PREFIXE)
