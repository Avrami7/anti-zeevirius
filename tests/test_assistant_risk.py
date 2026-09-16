"""
Tests de assistant/risk.py.

L'échelle de risque est minuscule — quatre valeurs et deux fonctions — et
c'est justement pour cela qu'elle mérite des tests : tout le reste de la
couche assistant s'y règle. Ce que le mode autonome a le droit de lancer
seul, ce que l'interface fait confirmer, ce qu'une phrase mal comprise ne
doit surtout pas déclencher : trois décisions, une seule source.

Deux propriétés sont vérifiées plus que les autres, parce que ce sont celles
dont un contresens coûte des fichiers :

  * **L'ordre est croissant et comparable.** `risque >= DESTRUCTIF` est écrit
    partout ailleurs ; si un jour quelqu'un réordonne l'énumération « pour la
    lisibilité », ces tests tombent au lieu du disque de l'utilisateur.
  * **Face à l'inconnu, on demande.** Un niveau illisible — `None`, du texte,
    une valeur hors échelle, un booléen — doit exiger confirmation. L'erreur
    inverse, supposer inoffensif ce qu'on ne sait pas lire, est exactement
    celle qui supprime un fichier sans qu'on l'ait demandé.
"""

import enum

import pytest

from assistant.risk import Risque, exige_confirmation, libelle


# ═══════════════════════════════════════════════════════════════════
# L'échelle elle-même
# ═══════════════════════════════════════════════════════════════════
class TestEchelle:
    def test_les_quatre_niveaux_du_contrat_et_leurs_valeurs(self):
        """Les valeurs numériques sont normatives : elles voyagent dans le
        JSON de l'API web et sont relues par l'interface."""
        assert Risque.LECTURE == 0
        assert Risque.REVERSIBLE == 1
        assert Risque.DESTRUCTIF == 2
        assert Risque.IRREVERSIBLE == 3

    def test_aucun_niveau_en_trop(self):
        assert [r.name for r in Risque] == [
            "LECTURE", "REVERSIBLE", "DESTRUCTIF", "IRREVERSIBLE"]

    def test_l_ordre_est_croissant_et_comparable(self):
        assert (Risque.LECTURE < Risque.REVERSIBLE
                < Risque.DESTRUCTIF < Risque.IRREVERSIBLE)

    def test_c_est_bien_un_entier(self):
        """Le code appelant écrit `int(capacite.risque)` et `>=` un peu
        partout ; un `Enum` simple ferait échouer les deux."""
        assert issubclass(Risque, enum.IntEnum)
        assert isinstance(Risque.DESTRUCTIF, int)

    def test_une_valeur_hors_echelle_est_refusee(self):
        with pytest.raises(ValueError):
            Risque(4)


# ═══════════════════════════════════════════════════════════════════
# libelle()
# ═══════════════════════════════════════════════════════════════════
class TestLibelle:
    @pytest.mark.parametrize("niveau,attendu", [
        (Risque.LECTURE, "lecture seule"),
        (Risque.REVERSIBLE, "réversible"),
        (Risque.DESTRUCTIF, "destructif"),
        (Risque.IRREVERSIBLE, "irréversible"),
    ])
    def test_chaque_niveau_a_son_libelle_francais(self, niveau, attendu):
        assert libelle(niveau) == attendu

    def test_les_quatre_libelles_sont_distincts(self):
        """Deux niveaux qui s'affichent pareil donnent à l'utilisateur
        l'impression qu'il n'y a qu'une règle."""
        assert len({libelle(r) for r in Risque}) == 4

    def test_un_entier_nu_est_accepte(self):
        """L'interface web renvoie des entiers, pas des `Risque`."""
        assert libelle(2) == "destructif"

    @pytest.mark.parametrize("valeur", [None, "destructif", "2", 4, -1, 2.5, 1.9,
                                        object(), [], True, False])
    def test_une_valeur_illisible_ne_leve_jamais(self, valeur):
        assert libelle(valeur) == "inconnu"

    def test_un_booleen_n_est_pas_un_niveau(self):
        """`True` vaut 1 en Python. S'il passait pour REVERSIBLE ici alors que
        `exige_confirmation` le refuse, le même objet serait décrit comme
        anodin et traité comme suspect sur le même écran."""
        assert libelle(True) == "inconnu"
        assert libelle(False) == "inconnu"

    def test_un_flottant_entier_passe_comme_un_entier(self):
        assert libelle(3.0) == "irréversible"


# ═══════════════════════════════════════════════════════════════════
# exige_confirmation() — mode manuel
# ═══════════════════════════════════════════════════════════════════
class TestConfirmationManuelle:
    @pytest.mark.parametrize("niveau,attendu", [
        (Risque.LECTURE, False),
        (Risque.REVERSIBLE, False),
        (Risque.DESTRUCTIF, True),
        (Risque.IRREVERSIBLE, True),
    ])
    def test_le_seuil_est_destructif(self, niveau, attendu):
        """L'utilisateur a cliqué, il sait ce qu'il demande, et l'Historique
        rattrape le réversible."""
        assert exige_confirmation(niveau, False) is attendu

    def test_le_resultat_est_un_vrai_booleen(self):
        """L'enveloppe JSON du pont publie ce champ tel quel."""
        for niveau in Risque:
            assert exige_confirmation(niveau, False) in (True, False)
            assert isinstance(exige_confirmation(niveau, False), bool)


# ═══════════════════════════════════════════════════════════════════
# exige_confirmation() — mode autonome
# ═══════════════════════════════════════════════════════════════════
class TestConfirmationAutonome:
    @pytest.mark.parametrize("niveau,attendu", [
        (Risque.LECTURE, False),
        (Risque.REVERSIBLE, True),
        (Risque.DESTRUCTIF, True),
        (Risque.IRREVERSIBLE, True),
    ])
    def test_le_seuil_descend_a_reversible(self, niveau, attendu):
        """Le cœur de la doctrine : une machine qui agit de sa propre
        initiative ne touche pas au disque sans un « oui » humain."""
        assert exige_confirmation(niveau, True) is attendu

    def test_seule_la_lecture_reste_libre_en_autonome(self):
        libres = [r for r in Risque if not exige_confirmation(r, True)]
        assert libres == [Risque.LECTURE]

    def test_le_mode_autonome_n_est_jamais_plus_permissif(self):
        """Propriété de monotonie : passer en autonome ne peut qu'ajouter des
        confirmations, jamais en retirer."""
        for niveau in Risque:
            if exige_confirmation(niveau, False):
                assert exige_confirmation(niveau, True)


# ═══════════════════════════════════════════════════════════════════
# Face à l'inconnu, on demande
# ═══════════════════════════════════════════════════════════════════
class TestValeursIllisibles:
    @pytest.mark.parametrize("valeur", [
        None, "", "DESTRUCTIF", "2", [], {}, object(), float("nan"),
    ])
    @pytest.mark.parametrize("autonome", [False, True])
    def test_un_niveau_illisible_exige_confirmation(self, valeur, autonome):
        assert exige_confirmation(valeur, autonome) is True

    @pytest.mark.parametrize("valeur", [True, False])
    @pytest.mark.parametrize("autonome", [False, True])
    def test_un_booleen_exige_confirmation(self, valeur, autonome):
        """`True` vaudrait 1, donc REVERSIBLE, donc exécutable hors autonome.
        Un booléen n'est pas un niveau de risque : on refuse de le deviner."""
        assert exige_confirmation(valeur, autonome) is True

    @pytest.mark.parametrize("valeur", [4, 99, -1, -99])
    def test_une_valeur_hors_echelle_est_traitee_comme_dangereuse(self, valeur):
        """Un niveau inventé au-dessus d'IRREVERSIBLE doit rester bloqué, et
        surtout un niveau NÉGATIF ne doit pas se glisser sous le seuil de
        lecture : `-1 >= 2` est faux, donc la comparaison brute répondait
        « aucune confirmation nécessaire » pour une valeur qu'elle ne
        comprenait pas."""
        assert exige_confirmation(valeur, False) is True
        assert exige_confirmation(valeur, True) is True

    @pytest.mark.parametrize("valeur", [1.9, 2.5, 0.9, 2.0001])
    def test_un_niveau_non_entier_est_refuse(self, valeur):
        """`int(1.9)` vaut 1 : la troncature arrondit le risque VERS LE BAS,
        c'est-à-dire du côté permissif. C'est la mauvaise direction."""
        assert exige_confirmation(valeur, False) is True
        assert exige_confirmation(valeur, True) is True
        assert libelle(valeur) == "inconnu"

    @pytest.mark.parametrize("valeur", [0.0, 1.0, 2.0, 3.0])
    def test_un_flottant_exactement_entier_reste_lisible(self, valeur):
        """Le JSON de l'interface web ne distingue pas 2 de 2.0 : refuser le
        second casserait le pont pour rien."""
        assert libelle(valeur) != "inconnu"
        assert exige_confirmation(valeur, False) is (valeur >= 2.0)

    def test_libelle_et_exige_confirmation_ne_se_contredisent_jamais(self):
        """Les deux fonctions lisent la même échelle par la même porte : tout
        ce que l'une déclare « inconnu », l'autre doit faire confirmer."""
        echantillons = [None, "", "2", "DESTRUCTIF", True, False, -1, 4, 1.9,
                        2.5, [], {}, object(), float("nan"),
                        Risque.LECTURE, Risque.REVERSIBLE,
                        Risque.DESTRUCTIF, Risque.IRREVERSIBLE, 0, 1, 2, 3]
        for valeur in echantillons:
            if libelle(valeur) == "inconnu":
                assert exige_confirmation(valeur, False) is True, valeur
                assert exige_confirmation(valeur, True) is True, valeur

    def test_un_objet_qui_explose_a_la_conversion_ne_propage_pas(self):
        class Piege:
            def __int__(self):
                raise ValueError("je refuse de dire mon niveau")

        assert exige_confirmation(Piege(), False) is True
        assert libelle(Piege()) == "inconnu"
