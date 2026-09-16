"""
Tests de assistant/memory.py.

La mémoire est le seul module de la couche assistant qui ÉCRIT. C'est donc le
seul dont un défaut se paie en données perdues, et ce dépôt a déjà connu
quatre pertes silencieuses (écrasement à la restauration de quarantaine,
notamment). Les tests sont écrits avec cette histoire en tête.

Quatre propriétés dominent :

**1. Rien n'est jamais écrasé sans avoir été lu.** La classe garde un état en
cache et un chemin résolu paresseusement. Si l'emplacement change entre deux
appels — clé USB, `ANTIZEEVIRIUS_DATA_DIR` déplacé, test qui isole ses
données — l'ancien contenu ne doit pas partir par-dessus le nouveau fichier.
C'est le défaut que `TestChangementDEmplacement` verrouille.

**2. Écriture atomique.** Fichier temporaire dans le même dossier, `fsync`,
puis `os.replace`. Une coupure de courant laisse l'ancienne mémoire intacte,
jamais une moitié des deux.

**3. Une mémoire illisible ne bloque rien.** Le fichier part en `.corrompu`,
on repart d'un neuf. Un assistant qui refuse de démarrer parce que son
carnet est abîmé serait un antivirus qui ne scanne plus.

**4. Rien n'est écrit ailleurs que sous `paths.data_path()`.** Aucun test
d'ici ne touche au dossier réel de l'utilisateur : `ANTIZEEVIRIUS_DATA_DIR`
est redirigé vers `tmp_path` pour ceux qui n'imposent pas un chemin explicite.
"""

import json
import os
import threading
import time

import pytest

import paths
from assistant.memory import JOURNAL_MAX, Memoire, Profil


@pytest.fixture
def donnees(tmp_path, monkeypatch):
    """Isole TOUTES les écritures de ce fichier dans un dossier temporaire."""
    monkeypatch.setenv("ANTIZEEVIRIUS_DATA_DIR", str(tmp_path))
    return tmp_path


@pytest.fixture
def memoire(donnees):
    return Memoire()


def _lire(chemin):
    return json.loads(chemin.read_text(encoding="utf-8"))


# ═══════════════════════════════════════════════════════════════════
# Emplacement — jamais dans le dossier d'installation
# ═══════════════════════════════════════════════════════════════════
class TestEmplacement:
    def test_le_chemin_passe_par_data_path(self, memoire, donnees):
        """Installé dans Program Files, le dossier du programme est en
        lecture seule, et la virtualisation UAC peut rediriger l'écriture
        vers un VirtualStore où l'utilisateur ne retrouvera rien."""
        assert memoire.chemin == paths.data_path("assistant", "memoire.json")
        assert str(memoire.chemin).startswith(str(donnees))

    def test_aucune_ecriture_hors_du_dossier_de_donnees(self, memoire, donnees):
        memoire.definir_profil(nom_utilisateur="Zeev")
        memoire.definir_preference("theme", "sombre")
        memoire.journaliser("test", {})

        ecrits = [p for p in donnees.rglob("*") if p.is_file()]
        assert ecrits, "rien n'a été écrit : le test ne prouve rien"
        for p in ecrits:
            assert str(p).startswith(str(donnees))

    def test_un_chemin_explicite_est_respecte(self, tmp_path):
        cible = tmp_path / "ailleurs" / "m.json"
        m = Memoire(cible)
        m.definir_preference("x", 1)
        assert cible.is_file()

    def test_le_dossier_parent_est_cree_au_besoin(self, tmp_path):
        m = Memoire(tmp_path / "a" / "b" / "c" / "m.json")
        m.definir_preference("x", 1)
        assert (tmp_path / "a" / "b" / "c" / "m.json").is_file()

    def test_aucun_fichier_avant_la_premiere_ecriture(self, memoire):
        """Lire une mémoire absente ne doit pas la créer : un utilisateur qui
        ouvre l'application sans rien faire ne laisse pas de trace."""
        assert memoire.profil() == Profil()
        assert not memoire.chemin.exists()


# ═══════════════════════════════════════════════════════════════════
# Profil
# ═══════════════════════════════════════════════════════════════════
class TestProfil:
    def test_les_valeurs_par_defaut_du_contrat(self):
        p = Profil()
        assert p.nom_assistant == "ANTI-ZEEVIRIUS"
        assert p.nom_utilisateur == ""
        assert p.langue == "fr"

    def test_le_nom_utilisateur_reste_vide_tant_qu_on_ne_le_sait_pas(self, memoire):
        """Inventer un prénom, ou aller le chercher dans l'environnement
        Windows, donnerait à l'assistant l'air d'en savoir plus qu'il n'en
        sait."""
        assert memoire.profil().nom_utilisateur == ""

    def test_definir_puis_relire(self, memoire):
        memoire.definir_profil(nom_utilisateur="Zeev", langue="en")
        p = memoire.profil()
        assert (p.nom_utilisateur, p.langue) == ("Zeev", "en")

    def test_le_profil_survit_a_un_nouvel_objet(self, memoire, donnees):
        memoire.definir_profil(nom_utilisateur="Zeev")
        assert Memoire().profil().nom_utilisateur == "Zeev"

    def test_profil_rend_une_copie(self, memoire):
        """Modifier l'objet rendu ne doit rien changer sur le disque, sinon
        `definir_profil` cesse d'être la seule porte d'écriture."""
        p = memoire.profil()
        p.nom_utilisateur = "Intrus"
        assert memoire.profil().nom_utilisateur == ""

    def test_un_champ_inconnu_leve_valueerror(self, memoire):
        """Une faute de frappe avalée en silence donnerait un profil qui ne
        change jamais, et le bogue serait cherché ailleurs."""
        with pytest.raises(ValueError, match="champ de profil inconnu"):
            memoire.definir_profil(nom_utilsateur="Zeev")

    def test_un_champ_inconnu_n_ecrit_rien(self, memoire):
        with pytest.raises(ValueError):
            memoire.definir_profil(langue="en", inexistant=1)
        assert memoire.profil().langue == "fr"
        assert not memoire.chemin.exists()

    def test_definir_profil_sans_argument_rend_le_profil(self, memoire):
        assert memoire.definir_profil() == Profil()

    def test_none_signifie_je_ne_sais_plus(self, memoire):
        """Écrire la chaîne « None » dans la mémoire ferait saluer
        l'utilisateur par ce nom."""
        memoire.definir_profil(nom_utilisateur="Zeev")
        memoire.definir_profil(nom_utilisateur=None)
        assert memoire.profil().nom_utilisateur == "Zeev"

    def test_une_valeur_non_textuelle_est_normalisee(self, memoire):
        memoire.definir_profil(nom_utilisateur=42)
        assert memoire.profil().nom_utilisateur == "42"


# ═══════════════════════════════════════════════════════════════════
# Préférences
# ═══════════════════════════════════════════════════════════════════
class TestPreferences:
    def test_une_cle_absente_rend_le_defaut(self, memoire):
        assert memoire.preference("jamais_posee") is None
        assert memoire.preference("jamais_posee", "repli") == "repli"

    @pytest.mark.parametrize("valeur", [
        0, 1, -3, 2.5, True, False, None, "texte", [], [1, "deux"],
        {}, {"a": [1, {"b": None}]},
    ])
    def test_toute_valeur_json_fait_l_aller_retour(self, memoire, donnees, valeur):
        memoire.definir_preference("cle", valeur)
        assert Memoire().preference("cle") == valeur

    def test_une_valeur_non_serialisable_est_refusee_a_l_entree(self, memoire):
        """Vérifiée AVANT d'être acceptée : admise ici, elle ferait échouer
        TOUTES les écritures suivantes, très loin de sa cause."""
        with pytest.raises(ValueError, match="non enregistrable"):
            memoire.definir_preference("cle", {1, 2, 3})

    def test_le_refus_ne_corrompt_pas_le_reste(self, memoire, donnees):
        memoire.definir_preference("bonne", 1)
        with pytest.raises(ValueError):
            memoire.definir_preference("mauvaise", object())
        memoire.definir_preference("autre", 2)
        relu = Memoire()
        assert relu.preference("bonne") == 1
        assert relu.preference("autre") == 2
        assert relu.preference("mauvaise") is None

    @pytest.mark.parametrize("cle", ["", "   "])
    def test_une_cle_vide_est_refusee(self, memoire, cle):
        with pytest.raises(ValueError, match="clé de préférence vide"):
            memoire.definir_preference(cle, 1)

    def test_une_cle_non_textuelle_est_convertie(self, memoire):
        memoire.definir_preference(42, "x")
        assert memoire.preference("42") == "x"

    def test_reecrire_une_cle_remplace_sa_valeur(self, memoire, donnees):
        memoire.definir_preference("cle", "avant")
        memoire.definir_preference("cle", "apres")
        assert Memoire().preference("cle") == "apres"


# ═══════════════════════════════════════════════════════════════════
# Journal de décisions
# ═══════════════════════════════════════════════════════════════════
class TestJournal:
    def test_decisions_rend_les_plus_recentes_d_abord(self, memoire):
        for i in range(3):
            memoire.journaliser(f"evt{i}", {"i": i})
        assert [d["evenement"] for d in memoire.decisions()] == ["evt2", "evt1", "evt0"]

    def test_chaque_entree_porte_un_horodatage_et_son_detail(self, memoire):
        avant = time.time()
        memoire.journaliser("scan", {"chemin": "C:/Temp"})
        entree = memoire.decisions()[0]
        assert entree["detail"] == {"chemin": "C:/Temp"}
        assert avant <= entree["horodatage"] <= time.time()

    def test_la_limite_est_respectee(self, memoire):
        for i in range(7):
            memoire.journaliser(f"e{i}", {})
        assert len(memoire.decisions(3)) == 3

    @pytest.mark.parametrize("limite", [0, -1, -100])
    def test_une_limite_nulle_ou_negative_rend_une_liste_vide(self, memoire, limite):
        memoire.journaliser("e", {})
        assert memoire.decisions(limite) == []

    @pytest.mark.parametrize("limite", [None, "beaucoup", object()])
    def test_une_limite_illisible_retombe_sur_le_defaut(self, memoire, limite):
        memoire.journaliser("e", {})
        assert len(memoire.decisions(limite)) == 1

    def test_decisions_rend_des_copies(self, memoire):
        """Le journal en mémoire ne doit pas pouvoir être réécrit par un
        appelant qui croit manipuler son propre résultat."""
        memoire.journaliser("e", {"a": 1})
        d = memoire.decisions()[0]
        d["evenement"] = "falsifie"
        assert memoire.decisions()[0]["evenement"] == "e"

    @pytest.mark.parametrize("vide", ["", "   ", "\t\n"])
    def test_un_evenement_vide_est_refuse(self, memoire, vide):
        with pytest.raises(ValueError, match="événement vide"):
            memoire.journaliser(vide, {})

    @pytest.mark.parametrize("mauvais", [None, 0, 42, [], {}, object()])
    def test_un_evenement_non_textuel_est_refuse(self, memoire, mauvais):
        """`str(None)` donnait l'entrée « None » : un bogue d'appelant écrit
        dans la mémoire, puis relu comme un fait par le résumé quotidien."""
        with pytest.raises(ValueError, match="chaîne"):
            memoire.journaliser(mauvais, {})
        assert memoire.decisions() == []

    def test_detail_none_vaut_dictionnaire_vide(self, memoire):
        memoire.journaliser("e", None)
        assert memoire.decisions()[0]["detail"] == {}

    @pytest.mark.parametrize("detail", ["texte", 42, ["liste"]])
    def test_un_detail_non_dictionnaire_est_refuse(self, memoire, detail):
        with pytest.raises(ValueError, match="dictionnaire"):
            memoire.journaliser("e", detail)

    def test_un_detail_non_serialisable_est_refuse(self, memoire):
        with pytest.raises(ValueError, match="non représentable"):
            memoire.journaliser("e", {"objet": object()})

    def test_le_detail_est_copie_a_l_entree(self, memoire):
        detail = {"a": 1}
        memoire.journaliser("e", detail)
        detail["a"] = 999
        assert memoire.decisions()[0]["detail"] == {"a": 1}

    def test_le_journal_est_borne(self, memoire, donnees):
        """Une mémoire qui grossit indéfiniment finit par coûter plus cher à
        relire qu'elle ne rapporte."""
        for i in range(JOURNAL_MAX + 25):
            memoire.journaliser(f"e{i}", {})
        disque = _lire(memoire.chemin)
        assert len(disque["journal"]) == JOURNAL_MAX
        # Ce sont bien les plus ANCIENNES qui sont sorties.
        assert disque["journal"][0]["evenement"] == "e25"
        assert memoire.decisions(1)[0]["evenement"] == f"e{JOURNAL_MAX + 24}"


# ═══════════════════════════════════════════════════════════════════
# Écriture atomique
# ═══════════════════════════════════════════════════════════════════
class TestEcritureAtomique:
    def test_le_fichier_est_du_json_relisible_a_la_main(self, memoire):
        """Un utilisateur qui se demande ce que son antivirus a retenu de lui
        doit pouvoir ouvrir le fichier et le lire."""
        memoire.definir_profil(nom_utilisateur="Zeev")
        contenu = _lire(memoire.chemin)
        assert contenu["profil"]["nom_utilisateur"] == "Zeev"
        assert "\n" in memoire.chemin.read_text(encoding="utf-8")

    def test_les_accents_ne_sont_pas_echappes(self, memoire):
        memoire.definir_profil(nom_utilisateur="Zoé")
        assert "Zoé" in memoire.chemin.read_text(encoding="utf-8")

    def test_aucun_temporaire_ne_survit(self, memoire, donnees):
        for i in range(5):
            memoire.definir_preference(f"c{i}", i)
        restes = [p.name for p in memoire.chemin.parent.iterdir()
                  if p.name.endswith(".tmp")]
        assert restes == []

    def test_le_temporaire_est_cree_dans_le_dossier_cible(self, memoire, monkeypatch):
        """`os.replace` n'est atomique qu'au sein d'un même système de
        fichiers : un temporaire dans /tmp ne garantit rien."""
        import tempfile as _tf
        vus = []
        vrai = _tf.mkstemp

        def espion(*a, **k):
            vus.append(k.get("dir"))
            return vrai(*a, **k)

        monkeypatch.setattr("assistant.memory.tempfile.mkstemp", espion)
        memoire.definir_preference("x", 1)
        assert vus == [str(memoire.chemin.parent)]

    def test_un_disque_qui_refuse_l_ecriture_ne_leve_pas(self, memoire, monkeypatch):
        """On perd le souvenir, on ne perd pas la session."""
        def refuse(*a, **k):
            raise OSError(28, "No space left on device")

        monkeypatch.setattr("assistant.memory.tempfile.mkstemp", refuse)
        memoire.definir_preference("x", 1)
        memoire.journaliser("e", {})
        memoire.definir_profil(nom_utilisateur="Zeev")
        # L'état reste correct EN MÉMOIRE.
        assert memoire.preference("x") == 1
        assert memoire.profil().nom_utilisateur == "Zeev"

    def test_un_echec_de_remplacement_n_abime_pas_l_ancien_fichier(
            self, memoire, monkeypatch):
        memoire.definir_preference("precieuse", "gardee")
        avant = memoire.chemin.read_text(encoding="utf-8")

        def refuse(*a, **k):
            raise OSError("remplacement impossible")

        monkeypatch.setattr("assistant.memory.os.replace", refuse)
        memoire.definir_preference("nouvelle", 1)

        assert memoire.chemin.read_text(encoding="utf-8") == avant
        restes = [p.name for p in memoire.chemin.parent.iterdir()
                  if p.name.endswith(".tmp")]
        assert restes == [], "le temporaire n'a pas été nettoyé après l'échec"


# ═══════════════════════════════════════════════════════════════════
# Mémoire illisible
# ═══════════════════════════════════════════════════════════════════
class TestMemoireIllisible:
    @pytest.mark.parametrize("brut", [
        "{ceci n'est pas du json",
        "",
        "[1, 2, 3]",          # du JSON valide, mais pas un objet
        "null",
        '"une chaîne"',
    ])
    def test_un_fichier_invalide_ne_bloque_pas_le_demarrage(self, donnees, brut):
        chemin = paths.data_path("assistant", "memoire.json")
        chemin.parent.mkdir(parents=True, exist_ok=True)
        chemin.write_text(brut, encoding="utf-8")

        m = Memoire()
        assert m.profil() == Profil()
        assert m.decisions() == []

    def test_le_fichier_abime_est_mis_de_cote_et_non_efface(self, donnees):
        """On ne détruit pas ce qu'on ne comprend pas : il contient peut-être
        des préférences récupérables à la main, et son existence explique à
        l'utilisateur pourquoi l'assistant l'a oublié."""
        chemin = paths.data_path("assistant", "memoire.json")
        chemin.parent.mkdir(parents=True, exist_ok=True)
        chemin.write_text("{casse", encoding="utf-8")

        Memoire().profil()

        corrompu = chemin.with_name(chemin.name + ".corrompu")
        assert corrompu.is_file()
        assert corrompu.read_text(encoding="utf-8") == "{casse"

    def test_une_seconde_corruption_ne_bloque_pas(self, donnees):
        chemin = paths.data_path("assistant", "memoire.json")
        chemin.parent.mkdir(parents=True, exist_ok=True)
        for contenu in ("{premier", "{second"):
            chemin.write_text(contenu, encoding="utf-8")
            Memoire().profil()
        corrompu = chemin.with_name(chemin.name + ".corrompu")
        assert corrompu.read_text(encoding="utf-8") == "{second"

    def test_apres_corruption_une_memoire_neuve_s_ecrit(self, donnees):
        chemin = paths.data_path("assistant", "memoire.json")
        chemin.parent.mkdir(parents=True, exist_ok=True)
        chemin.write_text("{casse", encoding="utf-8")

        m = Memoire()
        m.definir_profil(nom_utilisateur="Zeev")
        assert _lire(chemin)["profil"]["nom_utilisateur"] == "Zeev"

    def test_des_octets_non_utf8_sont_traites_comme_une_corruption(self, donnees):
        chemin = paths.data_path("assistant", "memoire.json")
        chemin.parent.mkdir(parents=True, exist_ok=True)
        chemin.write_bytes(b"\xff\xfe\x00 pas de l'utf-8 \xc3")

        assert Memoire().profil() == Profil()
        assert chemin.with_name(chemin.name + ".corrompu").is_file()


# ═══════════════════════════════════════════════════════════════════
# Fusion tolérante — un fichier amputé ne fait pas perdre le reste
# ═══════════════════════════════════════════════════════════════════
class TestFusionTolerante:
    def _ecrire(self, donnees, contenu):
        chemin = paths.data_path("assistant", "memoire.json")
        chemin.parent.mkdir(parents=True, exist_ok=True)
        chemin.write_text(json.dumps(contenu), encoding="utf-8")
        return chemin

    def test_une_cle_manquante_retombe_sur_le_defaut(self, donnees):
        self._ecrire(donnees, {"preferences": {"theme": "sombre"}})
        m = Memoire()
        assert m.profil() == Profil()
        assert m.preference("theme") == "sombre"
        assert m.decisions() == []

    def test_un_profil_partiel_garde_ses_champs_connus(self, donnees):
        self._ecrire(donnees, {"profil": {"langue": "en"}})
        p = Memoire().profil()
        assert p.langue == "en"
        assert p.nom_assistant == "ANTI-ZEEVIRIUS"

    def test_un_champ_de_profil_inventé_est_ignore_sans_erreur(self, donnees):
        self._ecrire(donnees, {"profil": {"langue": "en", "humeur": "bonne"}})
        assert Memoire().profil().langue == "en"

    def test_des_types_incoherents_ne_font_pas_tomber_la_lecture(self, donnees):
        self._ecrire(donnees, {"profil": "pas un dict",
                               "preferences": ["pas un dict"],
                               "journal": "pas une liste"})
        m = Memoire()
        assert m.profil() == Profil()
        assert m.preference("x") is None
        assert m.decisions() == []

    def test_les_entrees_de_journal_invalides_sont_ecartees(self, donnees):
        self._ecrire(donnees, {"journal": [
            {"horodatage": 1, "evenement": "bonne", "detail": {}},
            "pas un dict", 42, None,
        ]})
        decisions = Memoire().decisions()
        assert [d["evenement"] for d in decisions] == ["bonne"]


# ═══════════════════════════════════════════════════════════════════
# Changement d'emplacement — le défaut de perte silencieuse
# ═══════════════════════════════════════════════════════════════════
class TestChangementDEmplacement:
    def _semer(self, racine, marque):
        chemin = racine / "assistant" / "memoire.json"
        chemin.parent.mkdir(parents=True, exist_ok=True)
        chemin.write_text(json.dumps({
            "version": 1,
            "profil": {"nom_assistant": "ANTI-ZEEVIRIUS",
                       "nom_utilisateur": marque, "langue": "fr"},
            "preferences": {"precieuse": marque},
            "journal": [{"horodatage": 1.0, "evenement": marque, "detail": {}}],
        }), encoding="utf-8")
        return chemin

    def test_l_objet_suit_un_changement_de_dossier(self, tmp_path, monkeypatch):
        """Le chemin est résolu paresseusement pour que
        `ANTIZEEVIRIUS_DATA_DIR` posé après la construction soit respecté."""
        a, b = tmp_path / "A", tmp_path / "B"
        monkeypatch.setenv("ANTIZEEVIRIUS_DATA_DIR", str(a))
        m = Memoire()
        assert str(m.chemin).startswith(str(a))
        monkeypatch.setenv("ANTIZEEVIRIUS_DATA_DIR", str(b))
        assert str(m.chemin).startswith(str(b))

    def test_l_ancien_contenu_n_ecrase_pas_la_nouvelle_destination(
            self, tmp_path, monkeypatch):
        """LE test de non-régression. L'état était mis en cache et le chemin
        résolu à chaque accès : l'objet écrivait donc l'état chargé de A
        par-dessus le fichier de B, sans l'avoir lu, sans `.corrompu`, sans
        exception. Profil, préférences et journal de B disparaissaient."""
        a, b = tmp_path / "A", tmp_path / "B"
        self._semer(a, "venu_de_A")
        chemin_b = self._semer(b, "venu_de_B")

        monkeypatch.setenv("ANTIZEEVIRIUS_DATA_DIR", str(a))
        m = Memoire()
        assert m.preference("precieuse") == "venu_de_A"   # cache amorcé sur A

        monkeypatch.setenv("ANTIZEEVIRIUS_DATA_DIR", str(b))
        m.definir_preference("ajoutee_apres", 1)

        apres = _lire(chemin_b)
        assert apres["preferences"]["precieuse"] == "venu_de_B"
        assert apres["preferences"]["ajoutee_apres"] == 1
        assert apres["profil"]["nom_utilisateur"] == "venu_de_B"
        assert [e["evenement"] for e in apres["journal"]] == ["venu_de_B"]

    def test_l_ancien_fichier_reste_intact(self, tmp_path, monkeypatch):
        a, b = tmp_path / "A", tmp_path / "B"
        chemin_a = self._semer(a, "venu_de_A")
        self._semer(b, "venu_de_B")

        monkeypatch.setenv("ANTIZEEVIRIUS_DATA_DIR", str(a))
        m = Memoire()
        m.preference("precieuse")
        monkeypatch.setenv("ANTIZEEVIRIUS_DATA_DIR", str(b))
        m.definir_preference("ajoutee_apres", 1)

        avant = _lire(chemin_a)
        assert avant["preferences"] == {"precieuse": "venu_de_A"}
        assert "ajoutee_apres" not in avant["preferences"]

    def test_la_lecture_suit_aussi_le_changement(self, tmp_path, monkeypatch):
        a, b = tmp_path / "A", tmp_path / "B"
        self._semer(a, "venu_de_A")
        self._semer(b, "venu_de_B")

        monkeypatch.setenv("ANTIZEEVIRIUS_DATA_DIR", str(a))
        m = Memoire()
        assert m.profil().nom_utilisateur == "venu_de_A"
        monkeypatch.setenv("ANTIZEEVIRIUS_DATA_DIR", str(b))
        assert m.profil().nom_utilisateur == "venu_de_B"

    def test_une_destination_vierge_ne_perd_pas_pour_autant(
            self, tmp_path, monkeypatch):
        """Cas symétrique : si B n'existe pas, on repart d'un neuf en B — et
        A ne doit pas être vidé au passage."""
        a, b = tmp_path / "A", tmp_path / "B"
        chemin_a = self._semer(a, "venu_de_A")

        monkeypatch.setenv("ANTIZEEVIRIUS_DATA_DIR", str(a))
        m = Memoire()
        m.preference("precieuse")
        monkeypatch.setenv("ANTIZEEVIRIUS_DATA_DIR", str(b))
        m.definir_preference("neuve", 1)

        assert _lire(b / "assistant" / "memoire.json")["preferences"] == {"neuve": 1}
        assert _lire(chemin_a)["preferences"] == {"precieuse": "venu_de_A"}

    def test_recharger_oublie_le_cache(self, memoire, donnees):
        memoire.definir_preference("x", 1)
        # Modification par un « autre processus ».
        contenu = _lire(memoire.chemin)
        contenu["preferences"]["x"] = 2
        memoire.chemin.write_text(json.dumps(contenu), encoding="utf-8")

        assert memoire.preference("x") == 1      # encore en cache
        memoire.recharger()
        assert memoire.preference("x") == 2


# ═══════════════════════════════════════════════════════════════════
# Concurrence
# ═══════════════════════════════════════════════════════════════════
class TestConcurrence:
    def test_aucune_entree_de_journal_perdue(self, memoire, donnees):
        """Les surveillances journalisent depuis leurs propres fils."""
        def travail(debut):
            for i in range(debut, debut + 30):
                memoire.journaliser(f"e{i:04d}", {"i": i})

        fils = [threading.Thread(target=travail, args=(d,))
                for d in (0, 30, 60, 90)]
        for f in fils:
            f.start()
        for f in fils:
            f.join(timeout=60)

        assert len(memoire.decisions(500)) == 120

    def test_le_fichier_reste_du_json_valide_sous_concurrence(self, memoire, donnees):
        def travail(debut):
            for i in range(debut, debut + 20):
                memoire.definir_preference(f"c{i:03d}", i)

        fils = [threading.Thread(target=travail, args=(d,)) for d in (0, 20, 40)]
        for f in fils:
            f.start()
        for f in fils:
            f.join(timeout=60)

        contenu = _lire(memoire.chemin)
        assert len(contenu["preferences"]) == 60
