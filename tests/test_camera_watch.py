"""
Tests de security/camera_watch.py.

Le cœur du module tient dans une règle : `LastUsedTimeStop` à zéro signifie
que l'application tient l'appareil EN CE MOMENT. Se tromper là-dessus rend le
module soit aveugle, soit hurlant.

Les tests couvrent donc les deux erreurs symétriques :

  * **manquer une activation** — la caméra s'allume et rien ne prévient ;
  * **alerter sans cesse** — une visioconférence d'une heure qui déclencherait
    une notification toutes les cinq secondes, jusqu'à ce que l'utilisateur
    n'y prête plus attention.

Le registre Windows et les notifications sont remplacés par un exécuteur
injecté : rien ne dépend de la plateforme.
"""

import json
import threading
import time
from datetime import datetime, timedelta

import pytest

from security.camera_watch import (
    CameraWatch, FILETIME_EPOCH, FILETIME_PAR_SECONDE,
    _filetime_vers_datetime, _nom_lisible, APPAREILS,
)


def _filetime(dt: datetime) -> int:
    return int((dt.timestamp() + FILETIME_EPOCH) * FILETIME_PAR_SECONDE)


def _entree(cle, debut=None, fin=0, racine="HKCU:"):
    return {"Cle": cle, "Racine": racine,
            "Debut": _filetime(debut) if debut else 0,
            "Fin": _filetime(fin) if isinstance(fin, datetime) else fin}


def _runner(par_appareil, notification_ok=True):
    """Exécuteur factice : rend le registre demandé, journalise les toasts."""
    journal = []

    def executer(commande, timeout=None):
        joint = " ".join(commande)
        journal.append(joint)
        if "ToastNotification" in joint:
            return ({"code": 0, "sortie": "", "erreur": ""} if notification_ok
                    else {"code": 1, "sortie": "", "erreur": "pas de bureau"})
        if commande and commande[0] == "msg":
            return {"code": 1, "sortie": "", "erreur": "indisponible"}
        for appareil, entrees in par_appareil.items():
            if f"ConsentStore\\{appareil}" in joint or f"ConsentStore\\\\{appareil}" in joint:
                return {"code": 0, "sortie": json.dumps(entrees), "erreur": ""}
        return {"code": 0, "sortie": "[]", "erreur": ""}

    executer.journal = journal
    return executer


@pytest.fixture
def fichier_autorisations(tmp_path):
    return str(tmp_path / "autorisations.json")


# ═══════════════════════════════════════════════════════════════════
# Conversion des dates Windows
# ═══════════════════════════════════════════════════════════════════
class TestFiletime:
    def test_aller_retour(self):
        maintenant = datetime.now().replace(microsecond=0)
        assert _filetime_vers_datetime(_filetime(maintenant)) == maintenant

    @pytest.mark.parametrize("valeur", [0, None, "", -1, "pas un nombre"])
    def test_valeurs_invalides_rendent_none(self, valeur):
        assert _filetime_vers_datetime(valeur) is None


class TestNomLisible:
    @pytest.mark.parametrize("cle,attendu", [
        (r"C:#Program Files#Zoom#bin#Zoom.exe", "Zoom.exe"),
        (r"C:#Windows#System32#cmd.exe", "cmd.exe"),
        ("Microsoft.Teams_8wekyb3d8bbwe", "Microsoft.Teams"),
        ("AppSimple", "AppSimple"),
    ])
    def test_noms(self, cle, attendu):
        assert _nom_lisible(cle) == attendu


# ═══════════════════════════════════════════════════════════════════
# Détection de l'accès en cours
# ═══════════════════════════════════════════════════════════════════
class TestDetectionEnCours:
    def test_fin_a_zero_signifie_acces_en_cours(self, fichier_autorisations):
        """LA règle du module : Stop == 0 → l'appareil est pris maintenant."""
        r = _runner({"webcam": [_entree(r"C:#Apps#espion.exe",
                                        debut=datetime.now(), fin=0)]})
        d = CameraWatch(runner=r, fichier_autorisations=fichier_autorisations).etat()["data"]

        assert len(d["en_cours"]) == 1
        assert d["en_cours"][0]["application"] == "espion.exe"

    def test_session_terminee_n_est_pas_en_cours(self, fichier_autorisations):
        debut = datetime.now() - timedelta(hours=2)
        r = _runner({"webcam": [_entree(r"C:#Apps#zoom.exe", debut=debut,
                                        fin=debut + timedelta(minutes=30))]})
        d = CameraWatch(runner=r, fichier_autorisations=fichier_autorisations).etat()["data"]

        assert d["en_cours"] == []
        assert len(d["acces"]) == 1, "l'accès passé reste visible dans l'historique"

    def test_application_autorisee_ne_declenche_pas_d_alerte(self, fichier_autorisations):
        """Une visioconférence déclarée légitime ne doit pas alerter."""
        r = _runner({"webcam": [_entree(r"C:#Apps#Zoom.exe",
                                        debut=datetime.now(), fin=0)]})
        cw = CameraWatch(runner=r, fichier_autorisations=fichier_autorisations)
        cw.autoriser("Zoom.exe")

        d = cw.etat()["data"]

        assert len(d["en_cours"]) == 1
        assert d["alertes"] == [], "une application autorisée ne doit pas alerter"

    def test_application_inconnue_declenche_une_alerte(self, fichier_autorisations):
        r = _runner({"webcam": [_entree(r"C:#Temp#rat.exe",
                                        debut=datetime.now(), fin=0)]})
        cw = CameraWatch(runner=r, fichier_autorisations=fichier_autorisations)
        cw.autoriser("Zoom.exe")

        assert len(cw.etat()["data"]["alertes"]) == 1

    def test_microphone_aussi_surveille(self, fichier_autorisations):
        r = _runner({"microphone": [_entree(r"C:#Temp#ecoute.exe",
                                            debut=datetime.now(), fin=0)]})
        d = CameraWatch(runner=r, fichier_autorisations=fichier_autorisations).etat()["data"]

        assert d["alertes"][0]["appareil"] == "microphone"
        assert d["alertes"][0]["appareil_lisible"] == "microphone"

    def test_le_rappel_sur_la_diode_est_present(self, fichier_autorisations):
        """Le témoin le plus fiable reste matériel : il faut le dire."""
        d = CameraWatch(runner=_runner({}),
                        fichier_autorisations=fichier_autorisations).etat()["data"]
        assert "diode" in d["rappel"]


# ═══════════════════════════════════════════════════════════════════
# Autorisations
# ═══════════════════════════════════════════════════════════════════
class TestAutorisations:
    def test_ajout_et_persistance(self, fichier_autorisations):
        cw = CameraWatch(runner=_runner({}), fichier_autorisations=fichier_autorisations)
        cw.autoriser("Zoom.exe")

        relu = CameraWatch(runner=_runner({}),
                           fichier_autorisations=fichier_autorisations)
        assert relu.autorisations() == ["Zoom.exe"]

    def test_pas_de_doublon(self, fichier_autorisations):
        cw = CameraWatch(runner=_runner({}), fichier_autorisations=fichier_autorisations)
        cw.autoriser("Zoom.exe")
        cw.autoriser("zoom.exe")
        assert len(cw.autorisations()) == 1

    def test_retrait(self, fichier_autorisations):
        cw = CameraWatch(runner=_runner({}), fichier_autorisations=fichier_autorisations)
        cw.autoriser("Zoom.exe")
        cw.retirer_autorisation("Zoom.exe")
        assert cw.autorisations() == []

    def test_nom_vide_refuse(self, fichier_autorisations):
        cw = CameraWatch(runner=_runner({}), fichier_autorisations=fichier_autorisations)
        assert cw.autoriser("  ")["ok"] is False

    def test_fichier_corrompu_ne_fait_pas_planter(self, tmp_path):
        f = tmp_path / "autorisations.json"
        f.write_text("{{{ pas du json", encoding="utf-8")
        cw = CameraWatch(runner=_runner({}), fichier_autorisations=str(f))
        assert cw.autorisations() == []


# ═══════════════════════════════════════════════════════════════════
# Notification
# ═══════════════════════════════════════════════════════════════════
class TestNotification:
    def test_notification_windows(self, fichier_autorisations):
        r = _runner({}, notification_ok=True)
        res = CameraWatch(runner=r,
                          fichier_autorisations=fichier_autorisations).notifier(
            "Caméra activée", "rat.exe utilise votre caméra")
        assert res["ok"] is True

    def test_echec_de_notification_ne_leve_pas(self, fichier_autorisations):
        """Une notification impossible ne doit jamais casser la surveillance."""
        r = _runner({}, notification_ok=False)
        res = CameraWatch(runner=r,
                          fichier_autorisations=fichier_autorisations).notifier("a", "b")
        assert res["ok"] is False and res["unavailable"] is True

    def test_apostrophe_echappee(self, fichier_autorisations):
        """« L'application » casserait la chaîne PowerShell et pourrait faire
        exécuter la suite comme du code."""
        r = _runner({}, notification_ok=True)
        CameraWatch(runner=r, fichier_autorisations=fichier_autorisations).notifier(
            "L'alerte", "d'un logiciel")
        toast = [c for c in r.journal if "ToastNotification" in c][0]
        assert "L''alerte" in toast and "d''un logiciel" in toast


# ═══════════════════════════════════════════════════════════════════
# Surveillance continue
# ═══════════════════════════════════════════════════════════════════
class TestSurveillance:
    def test_une_activation_declenche_un_rappel(self, fichier_autorisations):
        r = _runner({"webcam": [_entree(r"C:#Temp#rat.exe",
                                        debut=datetime.now(), fin=0)]})
        cw = CameraWatch(runner=r, fichier_autorisations=fichier_autorisations)
        recus, signal = [], threading.Event()

        def rappel(a):
            recus.append(a)
            signal.set()

        cw.surveiller(rappel=rappel, intervalle=0.05, notifier=False)
        assert signal.wait(3), "aucune alerte reçue"
        cw.arreter()

        assert recus[0]["application"] == "rat.exe"

    def test_une_meme_session_n_alerte_qu_une_fois(self, fichier_autorisations):
        """Sans cette mémoire, une visioconférence d'une heure produirait une
        alerte toutes les cinq secondes et l'utilisateur cesserait de lire."""
        r = _runner({"webcam": [_entree(r"C:#Temp#rat.exe",
                                        debut=datetime.now(), fin=0)]})
        cw = CameraWatch(runner=r, fichier_autorisations=fichier_autorisations)
        recus = []

        cw.surveiller(rappel=recus.append, intervalle=0.05, notifier=False)
        time.sleep(0.6)                      # une dizaine de relevés
        cw.arreter()

        assert len(recus) == 1, f"{len(recus)} alertes pour une seule session"

    def test_arret_propre(self, fichier_autorisations):
        cw = CameraWatch(runner=_runner({}),
                         fichier_autorisations=fichier_autorisations)
        cw.surveiller(intervalle=0.05, notifier=False)
        assert cw.surveillance_active is True
        cw.arreter()
        assert cw.surveillance_active is False

    def test_double_demarrage_sans_effet(self, fichier_autorisations):
        cw = CameraWatch(runner=_runner({}),
                         fichier_autorisations=fichier_autorisations)
        cw.surveiller(intervalle=0.05, notifier=False)
        second = cw.surveiller(intervalle=0.05, notifier=False)
        cw.arreter()
        assert second["data"].get("deja_active") is True


# ═══════════════════════════════════════════════════════════════════
# Robustesse et garde-fous
# ═══════════════════════════════════════════════════════════════════
class TestRobustesse:
    def test_hors_windows_degrade_proprement(self, fichier_autorisations):
        def refuse(commande, timeout=None):
            return {"code": -1, "sortie": "", "erreur": "commande introuvable"}

        d = CameraWatch(runner=refuse,
                        fichier_autorisations=fichier_autorisations).etat()["data"]

        assert d["acces"] == []
        assert "Windows" in d["sources"]["webcam"]

    def test_registre_illisible_signale_sans_planter(self, fichier_autorisations):
        def casse(commande, timeout=None):
            return {"code": 0, "sortie": "<<pas du json>>", "erreur": ""}

        d = CameraWatch(runner=casse,
                        fichier_autorisations=fichier_autorisations).etat()["data"]
        assert "illisible" in d["sources"]["webcam"]

    def test_un_seul_resultat_json_objet_accepte(self, fichier_autorisations):
        """PowerShell renvoie un objet, pas un tableau, s'il n'y a qu'une clé.
        Ne pas le gérer perdrait silencieusement la seule application active."""
        def unique(commande, timeout=None):
            # Uniquement pour la caméra : sinon le même faux résultat serait
            # rendu aussi pour le micro, et le test compterait deux accès.
            if "ConsentStore\\webcam" in " ".join(commande):
                return {"code": 0, "erreur": "",
                        "sortie": json.dumps(_entree(r"C:#Temp#rat.exe",
                                                     debut=datetime.now(), fin=0))}
            return {"code": 0, "sortie": "[]", "erreur": ""}

        d = CameraWatch(runner=unique,
                        fichier_autorisations=fichier_autorisations).etat()["data"]
        assert len(d["en_cours"]) == 1
        assert d["en_cours"][0]["appareil"] == "webcam"

    def test_utilisations_recentes_filtre_sur_la_periode(self, fichier_autorisations):
        vieux = datetime.now() - timedelta(days=3)
        recent = datetime.now() - timedelta(hours=1)
        r = _runner({"webcam": [
            _entree(r"C:#A#vieux.exe", debut=vieux, fin=vieux),
            _entree(r"C:#A#recent.exe", debut=recent, fin=recent),
        ]})
        d = CameraWatch(runner=r,
                        fichier_autorisations=fichier_autorisations).utilisations_recentes(heures=24)["data"]

        assert [a["application"] for a in d["acces"]] == ["recent.exe"]

    def test_le_module_ne_coupe_ni_ne_supprime_rien(self):
        """Ce module CONSTATE et PRÉVIENT. Couper la caméra ou tuer un
        processus relève d'autres modules, avec confirmation."""
        import inspect
        import security.camera_watch as m

        source = inspect.getsource(m)
        for interdit in ("shell=True", "os.system", "terminate()", "kill()",
                         "Disable-PnpDevice"):
            assert interdit not in source, f"{interdit} n'a rien à faire ici"
