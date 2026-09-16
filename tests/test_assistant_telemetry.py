"""
Tests de assistant/telemetry.py.

Deux dangers, et ils tirent dans des directions opposées :

  * **manger la mémoire qu'on surveille** — un tampon non borné transforme la
    surveillance en fuite ; les tests poussent donc plus d'échantillons que la
    capacité et vérifient la borne ;
  * **tomber quand psutil manque** — sur une machine à désinfecter, la
    dépendance peut être absente. Une exception à cet endroit empêcherait
    l'analyse d'un fichier, qui n'a pourtant aucun besoin de la télémétrie.

Le fil de fond est vérifié pour de bon : un `arreter()` qui laisse un fil
vivant ne se voit pas, jusqu'au jour où l'application refuse de se fermer.
"""

import sys
import threading
import time

import pytest

from assistant import telemetry
from assistant.telemetry import Echantillon, Telemetrie


class _PsutilFactice:
    """Un psutil minimal et prévisible : les valeurs sont posées, pas mesurées."""

    def __init__(self, cpu=10.0, memoire=20.0, disque=30.0, temperatures=None):
        self._cpu, self._memoire, self._disque = cpu, memoire, disque
        self._temperatures = temperatures
        self.appels_cpu = 0

    def cpu_percent(self, interval=None):
        self.appels_cpu += 1
        return self._cpu

    def virtual_memory(self):
        return type("M", (), {"percent": self._memoire})()

    def disk_usage(self, chemin):
        return type("D", (), {"percent": self._disque})()

    def sensors_temperatures(self):
        if self._temperatures is None:
            raise AttributeError("pas de capteur ici")
        return self._temperatures


class _Sonde:
    def __init__(self, current):
        self.current = current


@pytest.fixture
def faux_psutil(monkeypatch):
    ps = _PsutilFactice()
    monkeypatch.setattr(telemetry, "_importer_psutil", lambda: ps)
    return ps


# ═══════════════════════════════════════════════════════════════════
# Échantillonnage
# ═══════════════════════════════════════════════════════════════════
class TestEchantillonnage:
    def test_les_valeurs_viennent_de_psutil(self, faux_psutil):
        e = Telemetrie().echantillonner()
        assert (e.cpu, e.memoire, e.disque) == (10.0, 20.0, 30.0)
        assert e.horodatage > 0

    def test_l_echantillon_est_conserve(self, faux_psutil):
        t = Telemetrie()
        e = t.echantillonner()
        assert t.historique() == (e,)

    def test_une_sonde_qui_casse_ne_fait_pas_tomber_le_releve(self, monkeypatch):
        """Un volume système inaccessible ne doit pas coûter la charge CPU."""
        ps = _PsutilFactice()
        ps.disk_usage = lambda chemin: (_ for _ in ()).throw(OSError("refusé"))
        monkeypatch.setattr(telemetry, "_importer_psutil", lambda: ps)

        e = Telemetrie().echantillonner()

        assert e.disque == 0.0
        assert e.cpu == 10.0, "les autres mesures restent valides"

    def test_temperature_absente_rend_none(self, faux_psutil):
        assert Telemetrie().echantillonner().temperature is None

    def test_temperature_retient_la_sonde_la_plus_chaude(self, monkeypatch):
        """C'est le point chaud qui déclenche les protections, pas la moyenne."""
        ps = _PsutilFactice(temperatures={"coretemp": [_Sonde(41.0), _Sonde(78.5)],
                                          "acpi": [_Sonde(35.0)]})
        monkeypatch.setattr(telemetry, "_importer_psutil", lambda: ps)

        assert Telemetrie().echantillonner().temperature == 78.5


# ═══════════════════════════════════════════════════════════════════
# Absence de psutil
# ═══════════════════════════════════════════════════════════════════
class TestSansPsutil:
    def test_module_absent_degrade_sans_exception(self, monkeypatch):
        """Simule une machine où psutil n'est pas installé du tout."""
        monkeypatch.setitem(sys.modules, "psutil", None)   # import → ImportError

        t = Telemetrie()
        e = t.echantillonner()

        assert t.disponible is False
        assert (e.cpu, e.memoire, e.disque) == (0.0, 0.0, 0.0)
        assert e.temperature is None

    def test_la_surveillance_tourne_quand_meme(self, monkeypatch):
        """Sans psutil, la boucle doit vivre : c'est l'interface qui décidera
        d'afficher « indisponible », pas la télémétrie de s'arrêter."""
        monkeypatch.setitem(sys.modules, "psutil", None)

        t = Telemetrie()
        t.demarrer(intervalle=0.01)
        time.sleep(0.1)
        actif = t.actif
        t.arreter()

        assert actif is True
        assert len(t.historique()) >= 1

    def test_disponible_est_vrai_avec_psutil(self, faux_psutil):
        assert Telemetrie().disponible is True


# ═══════════════════════════════════════════════════════════════════
# Tampon borné
# ═══════════════════════════════════════════════════════════════════
class TestTamponBorne:
    def test_la_capacite_n_est_jamais_depassee(self, faux_psutil):
        """Une semaine de relevés ne doit pas peser plus qu'une heure."""
        t = Telemetrie(capacite_historique=5)
        for _ in range(500):
            t.echantillonner()

        assert len(t.historique()) == 5

    def test_les_plus_anciens_sortent_en_premier(self, faux_psutil):
        t = Telemetrie(capacite_historique=3)
        rendus = [t.echantillonner() for _ in range(10)]

        assert list(t.historique()) == rendus[-3:]

    def test_capacite_nulle_refusee(self):
        with pytest.raises(ValueError):
            Telemetrie(capacite_historique=0)


# ═══════════════════════════════════════════════════════════════════
# Moyennes
# ═══════════════════════════════════════════════════════════════════
class TestMoyennes:
    def _remplir(self, t, valeurs, maintenant=None):
        maintenant = maintenant or time.time()
        for decalage, cpu in valeurs:
            t._historique.append(Echantillon(
                horodatage=maintenant - decalage, cpu=cpu, memoire=cpu,
                disque=cpu, temperature=None))

    def test_moyenne_sur_la_fenetre(self, faux_psutil):
        t = Telemetrie()
        self._remplir(t, [(1, 10.0), (2, 30.0)])

        m = t.moyennes(60)

        assert m.cpu == 20.0 and m.memoire == 20.0

    def test_hors_fenetre_ignore(self, faux_psutil):
        """Un pic d'il y a deux heures ne doit pas polluer la dernière minute."""
        t = Telemetrie()
        self._remplir(t, [(1, 10.0), (7200, 100.0)])

        assert t.moyennes(60).cpu == 10.0

    def test_fenetre_vide_rend_none(self, faux_psutil):
        """None ≠ zéro : « je n'ai rien mesuré » n'est pas « tout est calme »."""
        t = Telemetrie()
        self._remplir(t, [(7200, 100.0)])

        assert t.moyennes(60) is None

    def test_historique_vide_rend_none(self, faux_psutil):
        assert Telemetrie().moyennes(60) is None

    def test_temperature_moyennee_sur_les_seules_sondes_publiees(self, faux_psutil):
        t = Telemetrie()
        maintenant = time.time()
        t._historique.append(Echantillon(maintenant, 0, 0, 0, None))
        t._historique.append(Echantillon(maintenant, 0, 0, 0, 40.0))
        t._historique.append(Echantillon(maintenant, 0, 0, 0, 60.0))

        assert t.moyennes(60).temperature == 50.0

    def test_duree_negative_rend_none(self, faux_psutil):
        t = Telemetrie()
        t.echantillonner()
        assert t.moyennes(0) is None


# ═══════════════════════════════════════════════════════════════════
# Fil de surveillance
# ═══════════════════════════════════════════════════════════════════
class TestSurveillance:
    def test_les_releves_s_accumulent(self, faux_psutil):
        t = Telemetrie()
        t.demarrer(intervalle=0.01)
        time.sleep(0.15)
        t.arreter()

        assert len(t.historique()) >= 3

    def test_double_demarrage_ne_cree_qu_un_fil(self, faux_psutil):
        avant = len(threading.enumerate())
        t = Telemetrie()
        t.demarrer(intervalle=0.05)
        t.demarrer(intervalle=0.05)

        assert len(threading.enumerate()) == avant + 1
        t.arreter()

    def test_arreter_ne_laisse_aucun_fil_vivant(self, faux_psutil):
        """Un fil oublié empêche l'application de se fermer proprement."""
        t = Telemetrie()
        t.demarrer(intervalle=0.01)
        time.sleep(0.05)
        t.arreter()

        assert t.actif is False
        assert not [f for f in threading.enumerate() if f.name == "az-telemetrie"]

    def test_arreter_est_idempotent(self, faux_psutil):
        t = Telemetrie()
        t.arreter()                      # jamais démarrée
        t.demarrer(intervalle=0.01)
        t.arreter()
        t.arreter()                      # deux fois de suite
        assert t.actif is False

    def test_redemarrage_possible_apres_arret(self, faux_psutil):
        t = Telemetrie()
        t.demarrer(intervalle=0.01)
        t.arreter()
        t.demarrer(intervalle=0.01)
        actif = t.actif
        t.arreter()

        assert actif is True

    def test_intervalle_nul_refuse(self, faux_psutil):
        """Un intervalle de zéro ferait tourner la boucle à plein régime — la
        surveillance deviendrait elle-même la charge à surveiller."""
        t = Telemetrie()
        with pytest.raises(ValueError):
            t.demarrer(intervalle=0)
