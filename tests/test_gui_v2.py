"""
test_gui_v2.py
Tests du branchement des cinq modules V2 sur l'interface graphique
(gui/bridge.py + gui/web/*), c'est-à-dire des seules garanties qui ne sont
vérifiables ni par les tests des modules eux-mêmes, ni à l'œil :

1. **Routage complet** — chaque fonction publique des cinq modules est
   atteignable par une action du contrat, et aucune action ne crashe.
2. **Transmission sans réécriture** — les modules rendent déjà l'enveloppe du
   contrat ; le pont ne doit ni la ré-emballer, ni perdre un champ. En
   particulier, un résultat PARTIEL (sources indisponibles) doit arriver
   intact jusqu'à l'interface : c'est le point non négociable du cahier des
   charges.
3. **Double validation** — l'activation de l'audit d'accès aux fichiers, seule
   action destructive des modules V2, et le Mode Incident, brutal quoique
   réversible, exigent tous deux le couple `dry_run` → `confirm_token`.
4. **Dégradation propre** — un module indisponible produit
   `{"ok": false, "unavailable": true}` en HTTP 200, jamais une exception.
5. **Cohérence du frontend** — aucune URL externe, aucun identifiant appelé
   par app.js qui n'existe pas dans index.html, aucune vue orpheline. Une
   seule erreur de ce genre suffit à rendre toute l'interface morte.

Aucun test n'exige Windows : les modules sont remplacés par des doubles qui
rendent exactement les formes documentées.
"""

import json
import re
from pathlib import Path

import pytest

from gui.bridge import Bridge, ModuleUnavailable

WEB = Path(__file__).resolve().parent.parent / "gui" / "web"

ACTIONS_V2 = [
    "network_connections", "network_apps",
    "intrusion_report", "intrusion_audit_enable",
    "camera_state", "camera_recent", "camera_allow", "camera_revoke",
    "camera_watch_start", "camera_watch_stop",
    "incident_state", "incident_plan", "incident_activate", "incident_restore",
    "history_list", "history_undo",
]


# ── Doubles des modules V2 ──────────────────────────────────────────
class FauxReseau:
    """Rend la forme exacte de security.network_watch."""

    def lister_connexions(self):
        return {"ok": True, "data": {
            "connexions": [
                {"pid": 42, "processus": "winlogin.exe", "chemin": r"C:\Temp\winlogin.exe",
                 "adresse_distante": "91.0.0.1", "port_distant": 4444, "score": 95,
                 "raisons": ["nom imitant winlogon.exe", "port de porte dérobée"],
                 "niveau": "a_examiner"},
                {"pid": 7, "processus": "chrome.exe", "chemin": r"C:\chrome.exe",
                 "adresse_distante": "142.0.0.1", "port_distant": 443, "score": 0,
                 "raisons": [], "niveau": "normal"},
            ],
            "total": 2, "a_examiner": 1, "suspects": 0}}

    def resumer_par_application(self):
        return {"ok": True, "data": {"applications": [], "total": 0}}


class FauxReseauIndisponible:
    def lister_connexions(self):
        return {"ok": False, "unavailable": True,
                "reason": "droits insuffisants — relancer en administrateur",
                "error": "droits insuffisants pour l'inventaire complet"}

    def resumer_par_application(self):
        return self.lister_connexions()


class FauxIntrusion:
    def __init__(self):
        self.audit_applique = []

    def rapport(self, jours=7):
        self.jours = jours
        return {"ok": True, "data": {
            "constats": [{"categorie": "session", "niveau": "important",
                          "titre": "Session Bureau à distance ouverte",
                          "detail": "Quelqu'un est connecté à distance.", "donnees": {}}],
            "importants": 1, "a_verifier": 0,
            # Deux sources muettes : elles DOIVENT survivre au trajet.
            "sources": {"sessions": "ok", "journal": "PowerShell indisponible (Windows uniquement)",
                        "comptes": "droits insuffisants"},
            "avertissement": "Ce rapport ne dit pas QUI, au sens d'une personne."}}

    def preparer_audit_fichiers(self, dossiers):
        return {"ok": True, "data": {
            "action": "activer_audit_fichiers", "dossiers": list(dossiers),
            "etapes": ["activer la stratégie d'audit", "poser une règle"],
            "reversible": True,
            "avertissements": ["N'ENREGISTRE RIEN DU PASSÉ.", "Exige les droits administrateur."]}}

    def activer_audit_fichiers(self, plan):
        self.audit_applique.append(plan)
        return {"ok": True, "data": {"dossiers_traces": plan.get("dossiers", []),
                                     "echecs": [], "rappel": "Seuls les accès à partir de maintenant."}}


class FauxCamera:
    def __init__(self):
        self.autorisees = []
        self._active = False

    def etat(self, appareils=("webcam", "microphone")):
        return {"ok": True, "data": {
            "acces": [{"appareil": "webcam", "appareil_lisible": "caméra", "cle": "x",
                       "application": "inconnu32.exe", "chemin": "x", "en_cours": True,
                       "debut": "2026-08-20T14:31:12", "fin": None, "autorisee": False}],
            "en_cours": [{"application": "inconnu32.exe"}],
            "alertes": [{"appareil": "webcam", "appareil_lisible": "caméra",
                         "application": "inconnu32.exe", "en_cours": True,
                         "debut": "2026-08-20T14:31:12", "autorisee": False}],
            "sources": {"webcam": "ok", "microphone": "PowerShell indisponible (Windows uniquement)"},
            "autorisees": list(self.autorisees),
            "rappel": "La diode de la caméra est câblée sur l'alimentation du capteur."}}

    def utilisations_recentes(self, heures=24):
        self.heures = heures
        d = self.etat()["data"]
        return {"ok": True, "data": {"acces": d["acces"], "total": 1,
                                     "periode_heures": heures, "sources": d["sources"]}}

    def autoriser(self, application):
        if not application:
            return {"ok": False, "error": "nom d'application vide", "unavailable": False}
        self.autorisees.append(application)
        return {"ok": True, "data": {"autorisees": list(self.autorisees)}}

    def retirer_autorisation(self, application):
        self.autorisees = [a for a in self.autorisees if a != application]
        return {"ok": True, "data": {"autorisees": list(self.autorisees)}}

    def surveiller(self):
        self._active = True
        return {"ok": True, "data": {"actif": True, "intervalle": 5.0}}

    def arreter(self):
        self._active = False
        return {"ok": True, "data": {"actif": False}}

    @property
    def surveillance_active(self):
        return self._active


class FauxIncident:
    """security.incident_mode rend son enveloppe À PLAT (`ok` + champs métier)."""

    def __init__(self, actif=False):
        self._actif = actif
        self.activations = 0
        self.retablissements = 0

    def etat(self):
        return {"ok": True, "actif": self._actif, "restauration_requise": self._actif,
                "depuis": "2026-08-20T14:32:07" if self._actif else None,
                "reseau_coupe": self._actif, "regle": "AZ_INCIDENT",
                "processus_geles": [{"pid": 8821, "nom": "facture.exe"}] if self._actif else [],
                "nb_geles": 1 if self._actif else 0, "corrompu": False,
                "etat_persistant": "/tmp/incident_state.json",
                "retrait_manuel": "netsh advfirewall firewall delete rule name=AZ_INCIDENT",
                "message": "MODE INCIDENT ACTIF" if self._actif else "Mode incident inactif."}

    def preparer(self):
        return {"ok": True, "genere_le": "2026-08-20T14:30:00", "plateforme": "Linux",
                "administrateur": False, "deja_actif": self._actif,
                "etat_persistant": "/tmp/incident_state.json",
                "reseau": {"etape": 1, "action": "règle de pare-feu", "regle": "AZ_INCIDENT",
                           "disponible": False, "commandes": []},
                "processus": [{"pid": 8821, "nom": "facture.exe", "chemin": r"C:\facture.exe",
                               "raison": "écrit 60 fichiers en 8 s"}],
                "gel": {"etape": 2, "action": "suspension", "disponible": True},
                "sauvegarde": {"etape": 3, "action": "cliché VSS", "dossiers": [], "disponible": False},
                "rapport": {"etape": 4, "action": "rapport horodaté", "dossier": "/tmp"},
                "avertissements": ["Coupure réseau immédiate.", "Plateforme Linux : VSS indisponible."]}

    def activer(self, plan=None):
        self.activations += 1
        self._actif = True
        return {"ok": True, "actif": True, "deja_actif": False, "horodatage": "2026-08-20T14:32:07",
                "duree_s": 1.2, "etapes": {"reseau": {"ok": False, "unavailable": True,
                                                      "reason": "Windows uniquement"},
                                           "processus": {"ok": True, "geles": [{"pid": 8821}]},
                                           "sauvegarde": {"ok": False, "unavailable": True, "reason": "VSS"},
                                           "rapport": {"ok": True}},
                "ordre": ["reseau", "processus", "sauvegarde", "rapport"],
                "degrade": True, "etapes_en_echec": ["reseau", "sauvegarde"], "nb_geles": 1,
                "etat_persistant": "/tmp/incident_state.json",
                "conseils": ["Ne redémarre pas : la clé peut être en mémoire."]}

    def retablir(self):
        self.retablissements += 1
        if not self._actif:
            return {"ok": True, "actif": False, "rien_a_faire": True,
                    "message": "Aucun mode incident enregistré : rien à rétablir."}
        self._actif = False
        return {"ok": True, "actif": False, "complet": True, "relances": [{"pid": 8821}],
                "restants": [], "message": "Mode incident levé."}


class FauxIncidentPartiel(FauxIncident):
    """Rétablissement incomplet : `ok` faux, aucun champ `error`."""

    def retablir(self):
        return {"ok": False, "actif": True, "complet": False, "restants": [{"pid": 8821}],
                "message": "Rétablissement PARTIEL — 1 processus toujours gelé(s).",
                "etat_persistant": "/tmp/incident_state.json"}


class FauxHistorique:
    def __init__(self):
        self.annulations = []

    def lister(self, limite=50, filtre=None):
        self.dernier_appel = (limite, filtre)
        return {"ok": True, "data": {
            "entrees": [{"id": "sas:abc", "source": "sas", "id_natif": "abc",
                         "type_action": "mise_de_cote", "description": "Fichier mis de côté",
                         "horodatage": "2026-08-19T10:00:00", "annulable": True,
                         "raison_non_annulable": None, "details": {}},
                        {"id": "demarrage:xyz", "source": "demarrage", "id_natif": "xyz",
                         "type_action": "desactivation", "description": "Entrée désactivée",
                         "horodatage": None, "annulable": False,
                         "raison_non_annulable": "registre indisponible", "details": {}}],
            "total": 2, "affichees": 2, "annulables": 1,
            "problemes": [{"source": "demarrage", "libelle": "Démarrage Windows",
                           "message": "registre Windows indisponible", "indisponible": True}],
            "sources": [{"source": "sas", "libelle": "Sas de tri"}]}}

    def annuler(self, entry_id):
        self.annulations.append(entry_id)
        if entry_id == "sas:abc":
            return {"ok": True, "data": {"id": entry_id, "source": "sas", "restaure": True}}
        return {"ok": False, "error": "entrée introuvable", "unavailable": False}


@pytest.fixture(scope="module")
def sources():
    """Les trois fichiers de l'interface, lus une fois."""
    return {n: (WEB / n).read_text(encoding="utf-8")
            for n in ("index.html", "app.js", "app.css")}


@pytest.fixture
def pont():
    """Pont avec les cinq modules V2 remplacés par leurs doubles."""
    b = Bridge()
    doubles = {
        "network_watch": FauxReseau(), "intrusion_check": FauxIntrusion(),
        "camera_watch": FauxCamera(), "incident_mode": FauxIncident(),
        "history": FauxHistorique(),
    }
    b._instances.update(doubles)
    return b, doubles


def enveloppe_valide(rep):
    """Toute réponse doit respecter la forme figée par le contrat."""
    assert isinstance(rep, dict), "réponse non JSON"
    assert "ok" in rep and isinstance(rep["ok"], bool)
    if rep["ok"]:
        assert "data" in rep and isinstance(rep["data"], dict)
    else:
        assert rep.get("error"), "un échec doit porter un message lisible"
        assert "unavailable" in rep
        if rep["unavailable"]:
            assert rep.get("reason")
    return rep


# ═══════════════════════════════════════════════════════════════════
# 1. Routage : les cinq modules sont réellement atteignables
# ═══════════════════════════════════════════════════════════════════
class TestRoutage:

    def test_toutes_les_actions_v2_existent(self, pont):
        b, _ = pont
        connues = set(b.known_actions())
        manquantes = [a for a in ACTIONS_V2 if a not in connues]
        assert not manquantes, f"Actions absentes du pont : {manquantes}"

    @pytest.mark.parametrize("action,params", [
        ("network_connections", {}), ("network_apps", {}),
        ("intrusion_report", {"jours": 3}),
        ("camera_state", {}), ("camera_recent", {"heures": 12}),
        ("camera_allow", {"app": "Teams.exe"}), ("camera_revoke", {"app": "Teams.exe"}),
        ("camera_watch_start", {}), ("camera_watch_stop", {}),
        ("incident_state", {}), ("incident_plan", {}),
        ("incident_activate", {}), ("incident_restore", {}),
        ("history_list", {}), ("history_undo", {"id": "sas:abc"}),
        ("intrusion_audit_enable", {"folders": ["."]}),
    ])
    def test_aucune_action_ne_leve_et_rend_le_contrat(self, pont, action, params):
        b, _ = pont
        enveloppe_valide(b.dispatch(action, params))

    def test_une_action_inconnue_reste_inconnue(self, pont):
        b, _ = pont
        assert b.dispatch("incident_tout_casser", {}) is None


# ═══════════════════════════════════════════════════════════════════
# 2. Transmission sans réécriture — et surtout sans perte
# ═══════════════════════════════════════════════════════════════════
class TestTransmission:

    def test_une_enveloppe_deja_conforme_est_transmise_telle_quelle(self, pont):
        b, doubles = pont
        attendu = doubles["network_watch"].lister_connexions()
        assert b.dispatch("network_connections", {}) == attendu

    def test_les_champs_a_plat_du_mode_incident_passent_sous_data(self, pont):
        """incident_mode rend `ok` + des champs à plat : rien ne doit être perdu."""
        b, doubles = pont
        brut = doubles["incident_mode"].etat()
        rep = enveloppe_valide(b.dispatch("incident_state", {}))
        for cle, valeur in brut.items():
            if cle == "ok":
                continue
            assert rep["data"][cle] == valeur, f"champ `{cle}` perdu ou modifié"

    def test_les_sources_indisponibles_arrivent_intactes(self, pont):
        """Point non négociable : un rapport partiel ne doit jamais être
        présenté comme complet, donc le pont ne filtre aucune source muette."""
        b, _ = pont
        rep = enveloppe_valide(b.dispatch("intrusion_report", {}))
        sources = rep["data"]["sources"]
        assert sources["journal"].startswith("PowerShell indisponible")
        assert sources["comptes"] == "droits insuffisants"
        assert sources["sessions"] == "ok"

    def test_les_sources_camera_indisponibles_arrivent_intactes(self, pont):
        b, _ = pont
        rep = enveloppe_valide(b.dispatch("camera_state", {}))
        assert rep["data"]["sources"]["microphone"].startswith("PowerShell indisponible")
        assert rep["data"]["alertes"], "un accès non autorisé en cours doit remonter"

    def test_les_problemes_de_l_historique_arrivent_intacts(self, pont):
        b, _ = pont
        rep = enveloppe_valide(b.dispatch("history_list", {}))
        assert rep["data"]["problemes"][0]["source"] == "demarrage"
        assert rep["data"]["entrees"][1]["raison_non_annulable"] == "registre indisponible"

    def test_un_echec_sans_champ_error_recoit_un_message_lisible(self):
        """Le rétablissement partiel ne renseigne que `message` : le contrat
        exige `error`, et le détail (ce qui reste gelé) doit survivre."""
        b = Bridge()
        b._instances["incident_mode"] = FauxIncidentPartiel(actif=True)
        rep = enveloppe_valide(b.dispatch("incident_restore", {}))
        assert rep["ok"] is False
        assert "PARTIEL" in rep["error"]
        assert rep["data"]["restants"] == [{"pid": 8821}]
        assert rep["unavailable"] is False

    def test_une_indisponibilite_de_module_est_transmise_telle_quelle(self):
        b = Bridge()
        b._instances["network_watch"] = FauxReseauIndisponible()
        rep = enveloppe_valide(b.dispatch("network_connections", {}))
        assert rep["unavailable"] is True
        assert "droits insuffisants" in rep["reason"]

    def test_une_reponse_sans_enveloppe_est_refusee_proprement(self):
        """Un module qui ne respecterait pas le contrat ne doit pas faire
        passer n'importe quoi pour un succès."""
        assert Bridge._v2(["pas", "un", "dict"])["ok"] is False
        assert Bridge._v2({"data": {}})["ok"] is False


# ═══════════════════════════════════════════════════════════════════
# 3. Paramètres : bornés, jamais dangereux
# ═══════════════════════════════════════════════════════════════════
class TestParametres:

    @pytest.mark.parametrize("brut,attendu", [
        (3, 3), ("12", 12), (0, 1), (9999, 365), (None, 7), ("abc", 7)])
    def test_les_jours_du_rapport_sont_bornes(self, pont, brut, attendu):
        b, doubles = pont
        b.dispatch("intrusion_report", {"jours": brut})
        assert doubles["intrusion_check"].jours == attendu

    @pytest.mark.parametrize("brut,attendu", [(6, 6), ("48", 48), (0, 1), (99999, 720), (None, 24)])
    def test_les_heures_de_la_camera_sont_bornees(self, pont, brut, attendu):
        b, doubles = pont
        b.dispatch("camera_recent", {"heures": brut})
        assert doubles["camera_watch"].heures == attendu

    def test_un_filtre_d_historique_non_declaratif_est_ignore(self, pont):
        """`lister()` accepte un prédicat exécutable : il ne doit JAMAIS
        pouvoir venir du réseau."""
        b, doubles = pont
        b.dispatch("history_list", {"filtre": 12345, "limite": 10})
        assert doubles["history"].dernier_appel == (10, None)
        b.dispatch("history_list", {"filtre": "quarantaine"})
        assert doubles["history"].dernier_appel[1] == "quarantaine"
        b.dispatch("history_list", {"filtre": {"source": "sas"}})
        assert doubles["history"].dernier_appel[1] == {"source": "sas"}

    def test_la_limite_de_l_historique_est_bornee(self, pont):
        b, doubles = pont
        b.dispatch("history_list", {"limite": 10 ** 9})
        assert doubles["history"].dernier_appel[0] == 1000

    def test_autoriser_sans_application_est_refuse(self, pont):
        b, _ = pont
        rep = b.dispatch("camera_allow", {})
        assert rep["ok"] is False and rep["unavailable"] is False

    def test_l_audit_refuse_un_dossier_inexistant(self, pont):
        b, _ = pont
        rep = b.dispatch("intrusion_audit_enable", {"folders": ["/dossier/qui/nexiste/pas"]})
        assert rep["ok"] is False
        assert "dossier" in rep["error"].lower()


# ═══════════════════════════════════════════════════════════════════
# 4. Double validation : audit des fichiers et Mode Incident
# ═══════════════════════════════════════════════════════════════════
class TestDoubleValidation:

    def test_l_audit_en_dry_run_ne_touche_a_rien(self, pont, tmp_path):
        b, doubles = pont
        rep = enveloppe_valide(b.dispatch("intrusion_audit_enable", {"folders": [str(tmp_path)]}))
        assert rep["data"]["dry_run"] is True
        assert rep["data"]["confirm_token"]
        assert doubles["intrusion_check"].audit_applique == [], "le dry_run a modifié le système"

    def test_le_plan_de_l_audit_porte_les_avertissements_du_module(self, pont, tmp_path):
        b, _ = pont
        plan = b.dispatch("intrusion_audit_enable", {"folders": [str(tmp_path)]})["data"]["plan"]
        assert plan["dossiers"] == [str(tmp_path)]
        assert any("PASSÉ" in a for a in plan["avertissements"])
        # Clés de présentation attendues par la modale de confirmation.
        assert plan["count"] == 1 and plan["items"] and plan["steps"]

    def test_l_audit_sans_jeton_est_refuse(self, pont, tmp_path):
        b, doubles = pont
        rep = b.dispatch("intrusion_audit_enable", {"folders": [str(tmp_path)], "dry_run": False})
        assert rep["ok"] is False
        assert "confirm_token" in rep["error"]
        assert doubles["intrusion_check"].audit_applique == []

    def test_l_audit_avec_jeton_s_execute_une_seule_fois(self, pont, tmp_path):
        b, doubles = pont
        params = {"folders": [str(tmp_path)]}
        jeton = b.dispatch("intrusion_audit_enable", params)["data"]["confirm_token"]
        reel = dict(params, dry_run=False, confirm_token=jeton)
        rep = enveloppe_valide(b.dispatch("intrusion_audit_enable", reel))
        assert rep["data"]["dry_run"] is False
        assert doubles["intrusion_check"].audit_applique, "l'audit n'a pas été appliqué"
        # Rejeu : refusé.
        assert b.dispatch("intrusion_audit_enable", reel)["ok"] is False
        assert len(doubles["intrusion_check"].audit_applique) == 1

    def test_un_jeton_d_audit_ne_vaut_que_pour_ses_dossiers(self, pont, tmp_path):
        autre = tmp_path / "autre"
        autre.mkdir()
        b, doubles = pont
        jeton = b.dispatch("intrusion_audit_enable",
                           {"folders": [str(tmp_path)]})["data"]["confirm_token"]
        rep = b.dispatch("intrusion_audit_enable",
                         {"folders": [str(autre)], "dry_run": False, "confirm_token": jeton})
        assert rep["ok"] is False
        assert doubles["intrusion_check"].audit_applique == []

    def test_le_mode_incident_ne_se_declenche_pas_au_premier_appel(self, pont):
        """Réversible, mais brutal : il passe par la même double validation."""
        b, doubles = pont
        rep = enveloppe_valide(b.dispatch("incident_activate", {}))
        assert rep["data"]["dry_run"] is True
        assert rep["data"]["confirm_token"]
        assert doubles["incident_mode"].activations == 0, "le réseau a été coupé sans confirmation"

    def test_le_plan_du_mode_incident_decrit_les_quatre_etapes(self, pont):
        b, _ = pont
        plan = b.dispatch("incident_activate", {})["data"]["plan"]
        assert plan["count"] == 4 and len(plan["steps"]) == 4
        assert "indisponible" in plan["steps"][0], "une étape impossible ici doit le dire"
        assert plan["items"][0]["path"].startswith("facture.exe")
        assert "Coupure réseau immédiate." in plan["note"]
        # Le plan du module est conservé entier à côté des clés de présentation.
        assert plan["sequence"]["avertissements"]

    def test_le_mode_incident_sans_jeton_est_refuse(self, pont):
        b, doubles = pont
        rep = b.dispatch("incident_activate", {"dry_run": False})
        assert rep["ok"] is False
        assert doubles["incident_mode"].activations == 0

    def test_le_mode_incident_avec_jeton_s_execute(self, pont):
        b, doubles = pont
        jeton = b.dispatch("incident_activate", {})["data"]["confirm_token"]
        rep = enveloppe_valide(b.dispatch("incident_activate",
                                          {"dry_run": False, "confirm_token": jeton}))
        assert doubles["incident_mode"].activations == 1
        resultat = rep["data"]["result"]
        assert resultat["ok"] is True
        assert resultat["data"]["degrade"] is True
        assert resultat["data"]["etapes_en_echec"] == ["reseau", "sauvegarde"]

    def test_le_retablissement_ne_demande_aucun_jeton(self, pont):
        """C'est la porte de secours : elle doit rester à un clic."""
        b, doubles = pont
        doubles["incident_mode"]._actif = True
        rep = enveloppe_valide(b.dispatch("incident_restore", {}))
        assert rep["data"]["complet"] is True
        assert doubles["incident_mode"].retablissements == 1

    def test_l_annulation_d_une_entree_ne_demande_aucun_jeton(self, pont):
        """Annuler REMET en place : ce n'est pas une action destructive."""
        b, doubles = pont
        assert b.dispatch("history_undo", {"id": "sas:abc"})["ok"] is True
        assert doubles["history"].annulations == ["sas:abc"]


# ═══════════════════════════════════════════════════════════════════
# 5. Dégradation propre
# ═══════════════════════════════════════════════════════════════════
class TestDegradation:

    @pytest.mark.parametrize("cle,action", [
        ("network_watch", "network_connections"),
        ("intrusion_check", "intrusion_report"),
        ("camera_watch", "camera_state"),
        ("incident_mode", "incident_state"),
        ("history", "history_list"),
    ])
    def test_un_module_absent_repond_unavailable(self, cle, action):
        b = Bridge()
        b._module_errors[cle] = f"{cle} indisponible (test)"
        rep = enveloppe_valide(b.dispatch(action, {}))
        assert rep["ok"] is False and rep["unavailable"] is True
        assert cle in rep["reason"]

    def test_une_exception_interne_ne_traverse_jamais_le_pont(self):
        class Explosif:
            def etat(self):
                raise RuntimeError("boum")

        b = Bridge()
        b._instances["camera_watch"] = Explosif()
        rep = enveloppe_valide(b.dispatch("camera_state", {}))
        assert rep["ok"] is False and "boum" in rep["error"]

    def test_le_tableau_de_bord_expose_l_etat_du_mode_incident(self, pont):
        """Un mode incident actif doit être visible depuis n'importe quel
        panneau, donc dès la réponse `status`."""
        b, doubles = pont
        doubles["incident_mode"]._actif = True
        data = enveloppe_valide(b.dispatch("status", {}))["data"]
        assert data["incident"]["actif"] is True
        assert data["incident"]["reseau_coupe"] is True
        assert data["camera_watch_active"] is False

    def test_le_tableau_de_bord_survit_a_un_module_v2_absent(self):
        b = Bridge()
        for cle in ("incident_mode", "camera_watch", "network_watch", "history", "intrusion_check"):
            b._module_errors[cle] = "absent (test)"
        data = enveloppe_valide(b.dispatch("status", {}))["data"]
        assert data["incident"]["actif"] is False
        assert data["modules"]["incident_mode"]["available"] is False


# ═══════════════════════════════════════════════════════════════════
# 6. Cohérence du frontend (aucune erreur JS ne doit être possible)
# ═══════════════════════════════════════════════════════════════════
class TestFrontend:

    def test_aucune_url_externe(self, sources):
        """Zéro CDN, zéro requête réseau : l'outil doit fonctionner sur une
        machine hors ligne, potentiellement infectée.

        Seule tolérance : l'espace de noms SVG `xmlns="http://www.w3.org/..."`,
        qui est un identifiant, jamais une adresse appelée."""
        motif = re.compile(r"""(?:https?:)?//([A-Za-z0-9.\-]+)""")
        for nom, texte in sources.items():
            # On retire les déclarations d'espace de noms avant l'inspection.
            nettoye = re.sub(r'xmlns(:\w+)?="[^"]*"', "", texte)
            for hote in motif.findall(nettoye):
                assert hote.startswith("127.0.0.1") or hote == "localhost", (
                    f"{nom} référence l'hôte externe {hote}")
            for attribut in ("src=", "href="):
                for valeur in re.findall(attribut + r'"([^"]*)"', nettoye):
                    assert not valeur.startswith(("http", "//")), (
                        f"{nom} charge une ressource distante : {valeur}")
            assert "cdn." not in texte.lower()

    def test_aucun_emoji_decoratif(self, sources):
        """Les pictogrammes sont des SVG en ligne, jamais des emoji."""
        emoji = re.compile("[\U0001F000-\U0001FAFF\u2600-\u27BF]")
        for nom, texte in sources.items():
            trouve = emoji.findall(texte)
            assert not trouve, f"{nom} contient des emoji : {trouve[:5]}"

    def test_tous_les_identifiants_appeles_par_app_js_existent(self, sources):
        """Un `$('idInexistant')` renvoie null, et le premier appel de méthode
        qui suit tue toute l'interface. On vérifie donc le lien statique."""
        ids_html = set(re.findall(r'id="([A-Za-z0-9_-]+)"', sources["index.html"]))
        ids_js = set(re.findall(r"\$\('([A-Za-z0-9_-]+)'\)", sources["app.js"]))
        manquants = sorted(ids_js - ids_html)
        assert not manquants, f"identifiants absents de index.html : {manquants}"

    def test_les_vues_declarees_ont_toutes_une_section(self, sources):
        vues = re.search(r"var VIEWS = \[(.*?)\];", sources["app.js"], re.S).group(1)
        noms = re.findall(r"'([a-z\-]+)'", vues)
        assert "securite" in noms and "historique" in noms
        for nom in noms:
            assert f'id="view-{nom}"' in sources["index.html"], f"section view-{nom} absente"
            assert f'data-view="{nom}"' in sources["index.html"], f"entrée de menu {nom} absente"

    def test_les_panneaux_v2_declarent_leur_module(self, sources):
        """`data-mod` doit nommer une clé connue du backend, sinon le panneau
        ne sera jamais désactivé quand le module manque."""
        html = sources["index.html"]
        attendus = {
            "pIncident": "incident_mode", "pCamera": "camera_watch",
            "pNetwork": "network_watch", "pIntrusion": "intrusion_check",
            "pAudit": "intrusion_check", "pHistory": "history",
        }
        for panneau, module in attendus.items():
            bloc = re.search(r'<article[^>]*id="%s"[^>]*>' % panneau, html)
            assert bloc, f"panneau {panneau} absent"
            assert f'data-mod="{module}"' in bloc.group(0), (
                f"{panneau} ne déclare pas data-mod={module}")

    def test_le_bouton_d_urgence_est_dans_la_barre_du_haut(self, sources):
        """Le Mode Incident est un bouton d'urgence : il n'a aucun sens
        enterré dans un sous-menu."""
        html = sources["index.html"]
        barre = html.split('<nav class="rail"')[0]
        assert 'id="btnIncident"' in barre
        assert 'id="incidentBanner"' in html

    def test_les_actions_appelees_par_app_js_existent_dans_le_pont(self, sources):
        appels = set(re.findall(r"call\('([a-z_]+)'", sources["app.js"]))
        appels |= set(re.findall(r"action: '([a-z_]+)'", sources["app.js"]))
        connues = set(Bridge().known_actions())
        inconnues = sorted(appels - connues)
        assert not inconnues, f"actions appelées mais absentes du pont : {inconnues}"

    def test_le_contrat_documente_les_nouvelles_actions(self):
        contrat = (WEB.parent / "API_CONTRACT.md").read_text(encoding="utf-8")
        for action in ACTIONS_V2:
            assert f"`{action}`" in contrat, f"action {action} non documentée au contrat"

    def test_le_mouvement_reduit_neutralise_les_alertes_animees(self, sources):
        """Les deux signaux d'urgence pulsent : ils doivent rester lisibles
        sans mouvement."""
        css = sources["app.css"]
        reduit = css.split("@media (prefers-reduced-motion:reduce)")[1]
        assert ".alarm{animation:none" in reduit.replace(" ", "")
        assert '.btn-incident[data-actif="true"]{animation:none' in reduit.replace(" ", "")
