"""
Tests de assistant/proactive.py.

Ce module est le seul de la couche à avoir le droit de dire « tu devrais ».
Trois propriétés le rendent acceptable, et chacune est testée ici :

  * **il ne fait rien** — `suggerer()` est pure : pas de disque, pas de
    réseau, pas d'exécution. La pureté est vérifiée en interdisant l'accès au
    système de fichiers pendant l'appel, pas seulement en la déclarant ;
  * **il dit toujours la même chose** — même état, mêmes suggestions, même
    ordre. Le test rejoue la fonction dans des interpréteurs séparés avec des
    `PYTHONHASHSEED` différents : c'est exactement le défaut trouvé jadis dans
    `security/network_watch.py` ;
  * **il se tait quand on le lui a dit deux fois** — une suggestion refusée
    deux fois passe en sourdine.

Les capacités proposées doivent exister : le test de résolution vérifie que
le nom vient du registre quand celui-ci connaît l'action.
"""

import subprocess
import sys
import textwrap
from pathlib import Path

import pytest

from assistant import proactive
from assistant.proactive import (
    NOMS_PAR_DEFAUT, SEUIL_SOURDINE, Suggestion, est_en_sourdine, noter_refus,
    oublier_refus, suggerer,
)

RACINE = Path(__file__).resolve().parent.parent


class MemoireFactice:
    """Mémoire conforme au contrat, entièrement en RAM.

    Les chantiers avancent en parallèle : ce substitut permet de tester le
    comportement de `proactive` sans dépendre de l'avancement du chantier A.
    La vraie `Memoire` est utilisée dès qu'elle est présente (voir la fixture).
    """

    def __init__(self):
        self._preferences = {}
        self.journal = []

    def preference(self, cle, defaut=None):
        return self._preferences.get(cle, defaut)

    def definir_preference(self, cle, valeur):
        self._preferences[cle] = valeur

    def journaliser(self, evenement, detail):
        self.journal.append({"evenement": evenement, "detail": detail})

    def decisions(self, limite=50):
        return list(reversed(self.journal))[:limite]


@pytest.fixture
def memoire(tmp_path, monkeypatch):
    """La vraie mémoire si le chantier A l'a livrée, sinon le substitut."""
    monkeypatch.setenv("ANTIZEEVIRIUS_DATA_DIR", str(tmp_path))
    try:
        from assistant.memory import Memoire
        return Memoire()
    except Exception:
        return MemoireFactice()


class _Capacite:
    def __init__(self, nom, action_bridge):
        self.nom, self.action_bridge = nom, action_bridge


class RegistreFactice:
    def __init__(self, capacites=()):
        self._capacites = tuple(sorted(capacites, key=lambda c: c.nom))

    def toutes(self):
        return self._capacites

    def obtenir(self, nom):
        for c in self._capacites:
            if c.nom == nom:
                return c
        return None


@pytest.fixture
def registre():
    return RegistreFactice()


def _etat(**champs):
    """État minimal et silencieux : aucune règle ne se déclenche."""
    base = {
        "maintenant": 1_700_000_000.0,
        "telemetrie": {"cpu": 5.0, "memoire": 30.0, "disque": 40.0},
        "temps_reel": {"actif": True},
        "bouclier": {"actif": True},
        "signatures": {"empreintes": 1200, "derniere_maj": 1_700_000_000.0 - 3600},
        "dernier_scan": 1_700_000_000.0 - 3600,
        "quarantaine": {"total": 0},
        "sas": {"total": 0},
        "camera": {"alertes": []},
        "reseau": {"connexions_suspectes": 0},
        "incident": {"actif": False},
        "menaces": {"en_attente": 0},
    }
    base.update(champs)
    return base


def _capacites(suggestions):
    return [s.capacite for s in suggestions]


# ═══════════════════════════════════════════════════════════════════
# Rien à signaler
# ═══════════════════════════════════════════════════════════════════
class TestSilence:
    def test_machine_saine_ne_propose_rien(self, memoire, registre):
        """Un assistant qui trouve toujours quelque chose à dire ment."""
        assert suggerer(_etat(), memoire, registre) == ()

    def test_etat_vide_ne_fabrique_pas_d_alerte(self, memoire, registre):
        """Sans information, on ne DEVINE pas l'état de la machine : seules
        les absences franches (aucun scan connu) donnent une suggestion."""
        s = suggerer({}, memoire, registre)
        assert _capacites(s) == ["scan.dossier"]


# ═══════════════════════════════════════════════════════════════════
# Règles
# ═══════════════════════════════════════════════════════════════════
class TestRegles:
    def test_disque_plein_propose_le_nettoyage(self, memoire, registre):
        s = suggerer(_etat(telemetrie={"disque": 94.0, "cpu": 1, "memoire": 1}),
                     memoire, registre)
        assert "nettoyage.temporaires" in _capacites(s)
        assert "94 %" in s[0].motif

    def test_disque_charge_propose_seulement_l_analyse(self, memoire, registre):
        s = suggerer(_etat(telemetrie={"disque": 85.0, "cpu": 1, "memoire": 1}),
                     memoire, registre)
        assert _capacites(s) == ["systeme.analyse_disque"]
        assert s[0].urgence == 1

    def test_camera_espionnee_est_la_plus_urgente(self, memoire, registre):
        """Une caméra allumée maintenant prime sur un disque plein."""
        s = suggerer(_etat(camera={"alertes": [{"application": "rat.exe"}]},
                           telemetrie={"disque": 99.0, "cpu": 1, "memoire": 1}),
                     memoire, registre)
        assert s[0].capacite == "securite.camera" and s[0].urgence == 3

    def test_temps_reel_arrete_est_signale(self, memoire, registre):
        s = suggerer(_etat(temps_reel={"actif": False}), memoire, registre)
        assert _capacites(s) == ["protection.temps_reel"]

    def test_bouclier_absent_est_signale(self, memoire, registre):
        s = suggerer(_etat(bouclier={"actif": False}), memoire, registre)
        assert _capacites(s) == ["protection.bouclier_rancongiciel"]

    def test_signatures_vides_urgence_deux(self, memoire, registre):
        s = suggerer(_etat(signatures={"empreintes": 0}), memoire, registre)
        assert s[0].capacite == "systeme.etat" and s[0].urgence == 2

    def test_signatures_agees_urgence_un(self, memoire, registre):
        vieux = 1_700_000_000.0 - 20 * 86400
        s = suggerer(_etat(signatures={"empreintes": 10, "derniere_maj": vieux}),
                     memoire, registre)
        assert s[0].capacite == "systeme.etat" and s[0].urgence == 1
        assert "20 jours" in s[0].motif

    def test_date_iso_acceptee_pour_les_signatures(self, memoire, registre):
        """`status` publie la date des bases au format ISO, la mémoire en
        temps Unix : refuser l'une reviendrait à ne jamais signaler des
        signatures périmées."""
        import datetime as _dt

        maintenant = 1_700_000_000.0
        vieille = _dt.datetime.fromtimestamp(maintenant - 30 * 86400).isoformat()
        s = suggerer(_etat(maintenant=maintenant,
                           signatures={"empreintes": 10, "derniere_maj": vieille}),
                     memoire, registre)

        assert s[0].capacite == "systeme.etat" and "30 jours" in s[0].motif

    def test_date_illisible_ne_declenche_rien(self, memoire, registre):
        s = suggerer(_etat(signatures={"empreintes": 10,
                                       "derniere_maj": "bientôt"}),
                     memoire, registre)
        assert s == ()

    def test_aucun_scan_connu(self, memoire, registre):
        s = suggerer(_etat(dernier_scan=None), memoire, registre)
        assert _capacites(s) == ["scan.dossier"] and s[0].urgence == 2

    def test_scan_ancien(self, memoire, registre):
        s = suggerer(_etat(dernier_scan=1_700_000_000.0 - 30 * 86400),
                     memoire, registre)
        assert s[0].urgence == 1 and "30 jours" in s[0].motif

    def test_connexions_suspectes(self, memoire, registre):
        s = suggerer(_etat(reseau={"connexions_suspectes": 3}), memoire, registre)
        assert _capacites(s) == ["securite.reseau"] and "3 connexion" in s[0].motif

    def test_mode_incident_oublie(self, memoire, registre):
        """Le mode incident coupe le réseau : le laisser actif sans le dire
        transforme une protection en panne inexplicable."""
        s = suggerer(_etat(incident={"actif": True}), memoire, registre)
        assert _capacites(s) == ["securite.incident"]

    def test_menaces_en_attente(self, memoire, registre):
        s = suggerer(_etat(menaces={"en_attente": 2}), memoire, registre)
        assert s[0].urgence == 3 and s[0].capacite == "protection.quarantaine"

    def test_memoire_saturee(self, memoire, registre):
        s = suggerer(_etat(telemetrie={"memoire": 95.0, "cpu": 1, "disque": 1}),
                     memoire, registre)
        assert _capacites(s) == ["systeme.demarrage"]

    def test_processeur_au_plafond(self, memoire, registre):
        s = suggerer(_etat(telemetrie={"cpu": 97.0, "memoire": 1, "disque": 1}),
                     memoire, registre)
        assert _capacites(s) == ["protection.processus_suspects"]

    def test_quarantaine_encombree_reste_du_confort(self, memoire, registre):
        s = suggerer(_etat(quarantaine={"total": 40}), memoire, registre)
        assert s[0].urgence == 0

    def test_sas_non_vide(self, memoire, registre):
        s = suggerer(_etat(sas={"total": 3}), memoire, registre)
        assert _capacites(s) == ["rangement.sas"]

    def test_valeurs_aberrantes_ignorees(self, memoire, registre):
        """Un état mal formé ne doit ni lever, ni inventer une alerte."""
        s = suggerer(_etat(telemetrie={"disque": "beaucoup", "cpu": None,
                                       "memoire": []}), memoire, registre)
        assert s == ()


# ═══════════════════════════════════════════════════════════════════
# Noms de capacités
# ═══════════════════════════════════════════════════════════════════
class TestResolutionDesNoms:
    def test_le_nom_vient_du_registre_quand_il_le_connait(self, memoire):
        """Suggérer une capacité qui n'existe pas dans le registre n'aurait
        aucun sens : l'interface n'aurait rien à proposer de cliquable."""
        registre = RegistreFactice([_Capacite("nettoyage.rapide", "clean_full")])
        s = suggerer(_etat(telemetrie={"disque": 95.0, "cpu": 1, "memoire": 1}),
                     memoire, registre)

        assert _capacites(s) == ["nettoyage.rapide"]

    def test_repli_si_le_registre_ignore_l_action(self, memoire):
        s = suggerer(_etat(telemetrie={"disque": 95.0, "cpu": 1, "memoire": 1}),
                     memoire, RegistreFactice())
        assert _capacites(s) == ["nettoyage.temporaires"]

    def test_toutes_les_actions_proposees_existent_dans_le_registre(self):
        """Une suggestion qui nomme une capacité imaginaire est pire que pas
        de suggestion : l'interface n'a rien à proposer de cliquable."""
        registry = pytest.importorskip("assistant.registry",
                                       reason="chantier A non encore livré")
        reel = registry.construire_registre_par_defaut()
        actions = {c.action_bridge for c in reel.toutes() if c.action_bridge}

        manquantes = sorted(a for a in NOMS_PAR_DEFAUT if a not in actions)
        assert manquantes == [], f"actions inconnues du registre : {manquantes}"

    def test_registre_cassé_ne_fait_pas_tomber_les_suggestions(self, memoire):
        class Casse:
            def toutes(self):
                raise RuntimeError("registre indisponible")

        s = suggerer(_etat(telemetrie={"disque": 95.0, "cpu": 1, "memoire": 1}),
                     memoire, Casse())
        assert _capacites(s) == ["nettoyage.temporaires"]


# ═══════════════════════════════════════════════════════════════════
# Pureté et déterminisme
# ═══════════════════════════════════════════════════════════════════
class TestPurete:
    def test_aucun_acces_disque_pendant_l_appel(self, memoire, registre,
                                                monkeypatch):
        """La frontière entre proposer et faire doit être structurelle."""
        import builtins
        import os

        def interdit(*a, **k):
            raise AssertionError("suggerer() a touché au système de fichiers")

        monkeypatch.setattr(builtins, "open", interdit)
        monkeypatch.setattr(os, "listdir", interdit)
        monkeypatch.setattr(os, "stat", interdit)

        suggerer(_etat(telemetrie={"disque": 95.0, "cpu": 95.0, "memoire": 95.0},
                       temps_reel={"actif": False}), memoire, registre)

    def test_aucun_reseau_pendant_l_appel(self, memoire, registre, monkeypatch):
        import socket

        def interdit(*a, **k):
            raise AssertionError("suggerer() a ouvert une connexion")

        monkeypatch.setattr(socket, "socket", interdit)
        monkeypatch.setattr(socket, "create_connection", interdit)

        suggerer(_etat(reseau={"connexions_suspectes": 5}), memoire, registre)

    def test_deux_appels_donnent_le_meme_resultat(self, memoire, registre):
        etat = _etat(telemetrie={"disque": 95.0, "cpu": 95.0, "memoire": 95.0},
                     temps_reel={"actif": False}, bouclier={"actif": False})
        assert suggerer(etat, memoire, registre) == suggerer(etat, memoire, registre)

    def test_l_etat_n_est_pas_modifie(self, memoire, registre):
        etat = _etat(sas={"total": 3})
        copie = {k: (dict(v) if isinstance(v, dict) else v)
                 for k, v in etat.items()}
        suggerer(etat, memoire, registre)
        assert etat == copie

    def test_ordre_stable_sous_plusieurs_pythonhashseed(self):
        """Le défaut déjà rencontré dans `security/network_watch.py` : un
        ensemble parcouru tel quel change d'ordre d'un lancement à l'autre, et
        les propositions dansent sous le curseur de l'utilisateur."""
        programme = textwrap.dedent("""
            import sys
            sys.path.insert(0, %r)
            from assistant.proactive import suggerer

            class M:
                def preference(self, cle, defaut=None): return {}

            etat = {
                "maintenant": 1_700_000_000.0,
                "telemetrie": {"cpu": 99.0, "memoire": 99.0, "disque": 99.0},
                "temps_reel": {"actif": False}, "bouclier": {"actif": False},
                "signatures": {"empreintes": 0},
                "dernier_scan": None,
                "quarantaine": {"total": 50}, "sas": {"total": 4},
                "camera": {"alertes": [1, 2]},
                "reseau": {"connexions_suspectes": 7},
                "incident": {"actif": True}, "menaces": {"en_attente": 1},
            }
            print("|".join(f"{s.urgence}:{s.capacite}"
                           for s in suggerer(etat, M(), None)))
        """) % str(RACINE)

        sorties = set()
        for graine in ("0", "1", "42", "12345"):
            r = subprocess.run([sys.executable, "-c", programme],
                               capture_output=True, text=True,
                               env={"PYTHONHASHSEED": graine, "PATH": "/usr/bin"})
            assert r.returncode == 0, r.stderr
            sorties.add(r.stdout.strip())

        assert len(sorties) == 1, f"ordre instable : {sorties}"
        unique = sorties.pop()
        assert unique.count("|") >= 9, f"trop peu de suggestions : {unique}"
        urgences = [int(x.split(":")[0]) for x in unique.split("|")]
        assert urgences == sorted(urgences, reverse=True), "urgence décroissante"


# ═══════════════════════════════════════════════════════════════════
# Sourdine
# ═══════════════════════════════════════════════════════════════════
class TestSourdine:
    def _etat_bruyant(self):
        return _etat(sas={"total": 3})

    def test_un_seul_refus_ne_suffit_pas(self, memoire, registre):
        """Le premier « non » peut être un « pas maintenant »."""
        noter_refus(memoire, "rangement.sas")
        s = suggerer(self._etat_bruyant(), memoire, registre)
        assert _capacites(s) == ["rangement.sas"]

    def test_deux_refus_mettent_en_sourdine(self, memoire, registre):
        for _ in range(SEUIL_SOURDINE):
            noter_refus(memoire, "rangement.sas")

        assert est_en_sourdine(memoire, "rangement.sas") is True
        assert suggerer(self._etat_bruyant(), memoire, registre) == ()

    def test_la_sourdine_ne_touche_que_la_capacite_refusee(self, memoire, registre):
        for _ in range(SEUIL_SOURDINE):
            noter_refus(memoire, "rangement.sas")

        s = suggerer(_etat(sas={"total": 3}, temps_reel={"actif": False}),
                     memoire, registre)
        assert _capacites(s) == ["protection.temps_reel"]

    def test_le_refus_est_journalise(self, memoire, registre):
        noter_refus(memoire, "rangement.sas")
        evenements = [d.get("evenement") for d in memoire.decisions()]
        assert "suggestion.refusee" in evenements

    def test_noter_refus_rend_le_total(self, memoire):
        assert noter_refus(memoire, "x") == 1
        assert noter_refus(memoire, "x") == 2

    def test_oublier_refus_leve_la_sourdine(self, memoire, registre):
        for _ in range(SEUIL_SOURDINE):
            noter_refus(memoire, "rangement.sas")
        oublier_refus(memoire, "rangement.sas")

        assert est_en_sourdine(memoire, "rangement.sas") is False
        assert _capacites(suggerer(self._etat_bruyant(), memoire, registre)) \
            == ["rangement.sas"]

    def test_preference_corrompue_ne_fait_pas_taire_l_assistant(self, registre):
        """Mieux vaut une proposition déjà écartée qu'un silence dû à un
        fichier abîmé."""
        class MemoireAbimee(MemoireFactice):
            def preference(self, cle, defaut=None):
                return "ceci n'est pas un dictionnaire"

        s = suggerer(_etat(sas={"total": 3}), MemoireAbimee(), registre)
        assert _capacites(s) == ["rangement.sas"]

    def test_memoire_qui_leve_ne_bloque_pas(self, registre):
        class MemoireHorsService(MemoireFactice):
            def preference(self, cle, defaut=None):
                raise OSError("mémoire illisible")

        s = suggerer(_etat(sas={"total": 3}), MemoireHorsService(), registre)
        assert _capacites(s) == ["rangement.sas"]
