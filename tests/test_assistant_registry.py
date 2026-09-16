"""
Tests de assistant/registry.py.

Le registre est la liste, lisible par du code, de ce qu'ANTI-ZEEVIRIUS sait
faire. Tout ce qui *choisit* s'appuie dessus : la compréhension d'une phrase,
les suggestions, le mode autonome. Trois familles de défauts y coûtent cher,
et ce sont celles que ces tests traquent.

**1. Une capacité imaginaire.** Une capacité qui décrit une fonctionnalité
inexistante est pire qu'une capacité absente : l'assistant la proposera, et
l'utilisateur cliquera. `action_bridge` est donc confrontée à
`Bridge.known_actions()` — la vraie liste, pas une copie.

**2. Un niveau de risque déclassé.** Si `quarantaine.supprimer` passait un
jour en `LECTURE`, le mode autonome l'exécuterait tout seul. Ces tests
verrouillent l'inverse : toute capacité dont l'action est gardée par
`_guarded()` dans le pont doit être au minimum `DESTRUCTIF`.

**3. Un ordre qui bouge.** `toutes()` et `par_categorie()` doivent trier. Ce
dépôt a déjà perdu du temps sur un `set` parcouru tel quel dans
`security/network_watch.py` ; on vérifie ici que l'ordre ne dépend ni de
`PYTHONHASHSEED`, ni de l'ordre d'insertion.
"""

import re
import subprocess
import sys
import threading
from pathlib import Path

import pytest

from assistant.registry import (
    CATEGORIES, Capacite, Registre, construire_registre_par_defaut,
)
from assistant.risk import Risque

RACINE = Path(__file__).resolve().parent.parent


def _capacite(nom="test.exemple", **champs):
    """Capacité valide par défaut ; chaque test ne surcharge que son sujet."""
    base = dict(
        nom=nom, titre="Exemple", categorie="systeme", risque=Risque.LECTURE,
        description="Ne fait rien, sert de témoin.", action_bridge="status",
    )
    base.update(champs)
    return Capacite(**base)


@pytest.fixture
def registre():
    return Registre()


@pytest.fixture(scope="module")
def defaut():
    return construire_registre_par_defaut()


@pytest.fixture(scope="module")
def actions_du_pont():
    """La VRAIE liste d'actions du pont, pas une copie tenue à la main."""
    from gui.bridge import Bridge
    return set(Bridge().known_actions())


# ═══════════════════════════════════════════════════════════════════
# La dataclasse Capacite
# ═══════════════════════════════════════════════════════════════════
class TestCapacite:
    def test_les_champs_du_contrat_sont_tous_la(self):
        c = _capacite()
        for champ in ("nom", "titre", "categorie", "risque", "description",
                      "action_bridge", "parametres"):
            assert hasattr(c, champ), champ

    def test_parametres_vaut_un_tuple_vide_par_defaut(self):
        assert _capacite().parametres == ()

    def test_elle_est_gelee(self):
        """Le registre est partagé entre l'interface, le mode autonome et la
        compréhension des phrases. Un niveau de risque modifiable en place,
        c'est un risque qui peut changer entre l'affichage et l'exécution."""
        c = _capacite()
        with pytest.raises(Exception):
            c.risque = Risque.LECTURE

    def test_deux_capacites_identiques_sont_egales(self):
        assert _capacite() == _capacite()

    def test_elle_est_hachable(self):
        """Conséquence de `frozen=True`, et utile : les appelants s'en servent
        comme clé de dictionnaire."""
        assert len({_capacite(), _capacite()}) == 1


# ═══════════════════════════════════════════════════════════════════
# enregistrer() — le refus du doublon et des valeurs fausses
# ═══════════════════════════════════════════════════════════════════
class TestEnregistrer:
    def test_une_capacite_enregistree_se_retrouve(self, registre):
        c = _capacite()
        registre.enregistrer(c)
        assert registre.obtenir("test.exemple") is c

    def test_un_nom_deja_pris_leve_valueerror(self, registre):
        """Deux capacités de même nom, c'est une phrase utilisateur qui
        déclenche l'une ou l'autre selon l'ordre des imports."""
        registre.enregistrer(_capacite())
        with pytest.raises(ValueError, match="déjà enregistrée"):
            registre.enregistrer(_capacite(titre="Autre"))

    def test_le_doublon_refuse_ne_remplace_pas_l_original(self, registre):
        original = _capacite(titre="Original")
        registre.enregistrer(original)
        with pytest.raises(ValueError):
            registre.enregistrer(_capacite(titre="Imposteur"))
        assert registre.obtenir("test.exemple").titre == "Original"

    @pytest.mark.parametrize("nom", ["", "   ", "\t\n"])
    def test_un_nom_vide_est_refuse(self, registre, nom):
        with pytest.raises(ValueError, match="nom non vide"):
            registre.enregistrer(_capacite(nom=nom))

    def test_un_nom_non_textuel_est_refuse(self, registre):
        with pytest.raises(ValueError):
            registre.enregistrer(_capacite(nom=42))

    def test_une_categorie_inconnue_est_refusee(self, registre):
        """Une sixième catégorie inventée au fil de l'eau ne serait affichée
        nulle part : la capacité deviendrait invisible en silence."""
        with pytest.raises(ValueError, match="catégorie inconnue"):
            registre.enregistrer(_capacite(categorie="divers"))

    def test_le_message_d_erreur_nomme_les_categories_attendues(self, registre):
        with pytest.raises(ValueError) as capture:
            registre.enregistrer(_capacite(categorie="divers"))
        for attendue in CATEGORIES:
            assert attendue in str(capture.value)

    @pytest.mark.parametrize("risque", [None, "destructif", 9, object()])
    def test_un_risque_illisible_est_refuse(self, registre, risque):
        with pytest.raises(ValueError, match="risque illisible"):
            registre.enregistrer(_capacite(risque=risque))

    def test_un_risque_entier_valide_est_accepte(self, registre):
        registre.enregistrer(_capacite(risque=2))
        assert registre.obtenir("test.exemple").risque == 2

    def test_un_objet_qui_n_est_pas_une_capacite_est_refuse(self, registre):
        with pytest.raises(ValueError, match="capacité attendue"):
            registre.enregistrer({"nom": "test.faux"})

    def test_un_refus_ne_laisse_rien_derriere_lui(self, registre):
        """Une validation à moitié appliquée laisserait un registre incohérent
        et l'erreur serait cherchée très loin de sa cause."""
        for mauvaise in (_capacite(categorie="divers"), _capacite(nom=""),
                         _capacite(risque=None)):
            with pytest.raises(ValueError):
                registre.enregistrer(mauvaise)
        assert registre.toutes() == ()


# ═══════════════════════════════════════════════════════════════════
# obtenir() — un nom inconnu n'est pas une panne
# ═══════════════════════════════════════════════════════════════════
class TestObtenir:
    def test_un_nom_inconnu_rend_none(self, registre):
        assert registre.obtenir("jamais.enregistre") is None

    @pytest.mark.parametrize("nom", [None, 42, [], object()])
    def test_un_nom_non_textuel_rend_none_sans_lever(self, registre, nom):
        """Le nom vient souvent d'une phrase mal comprise : ce n'est pas une
        panne, c'est un cas ordinaire."""
        assert registre.obtenir(nom) is None

    def test_la_casse_n_est_pas_devinee(self, registre):
        """Les noms sont des identifiants stables, pas du texte libre."""
        registre.enregistrer(_capacite(nom="scan.rapide"))
        assert registre.obtenir("Scan.Rapide") is None


# ═══════════════════════════════════════════════════════════════════
# Déterminisme de toutes() et par_categorie()
# ═══════════════════════════════════════════════════════════════════
class TestDeterminisme:
    def test_toutes_trie_par_nom_quel_que_soit_l_ordre_d_insertion(self):
        montant, descendant = Registre(), Registre()
        noms = ["systeme.a", "systeme.m", "systeme.z"]
        for n in noms:
            montant.enregistrer(_capacite(nom=n))
        for n in reversed(noms):
            descendant.enregistrer(_capacite(nom=n))
        assert [c.nom for c in montant.toutes()] == noms
        assert [c.nom for c in descendant.toutes()] == noms

    def test_toutes_rend_un_tuple(self, registre):
        """Un tuple ne peut pas être modifié par l'appelant : le registre
        reste la seule porte d'écriture."""
        registre.enregistrer(_capacite())
        assert isinstance(registre.toutes(), tuple)

    def test_par_categorie_est_triee_et_filtree(self):
        r = Registre()
        r.enregistrer(_capacite(nom="sys.z", categorie="systeme"))
        r.enregistrer(_capacite(nom="net.a", categorie="securite"))
        r.enregistrer(_capacite(nom="sys.a", categorie="systeme"))
        assert [c.nom for c in r.par_categorie("systeme")] == ["sys.a", "sys.z"]

    def test_une_categorie_inconnue_rend_un_tuple_vide(self, defaut):
        """L'appelant est une boucle d'affichage : un onglet vide vaut mieux
        qu'une interface qui ne se charge pas."""
        assert defaut.par_categorie("inexistante") == ()

    def test_les_categories_partitionnent_le_registre(self, defaut):
        """Aucune capacité ne doit être invisible dans l'interface, faute
        d'appartenir à l'un des cinq rayons."""
        reparties = sum(len(defaut.par_categorie(c)) for c in CATEGORIES)
        assert reparties == len(defaut.toutes())

    def test_l_ordre_ne_depend_pas_de_pythonhashseed(self):
        """Le défaut déjà rencontré dans `security/network_watch.py`. Un
        sous-processus par graine : c'est le seul moyen honnête de le voir,
        `PYTHONHASHSEED` étant figé au démarrage de l'interpréteur."""
        programme = (
            "import sys; sys.path.insert(0, %r)\n"
            "from assistant.registry import construire_registre_par_defaut as f\n"
            "r = f()\n"
            "print('|'.join(c.nom for c in r.toutes()))\n"
            "print('|'.join(c.nom for c in r.par_categorie('securite')))\n"
        ) % str(RACINE)

        sorties = set()
        for graine in ("0", "1", "2", "3", "42"):
            resultat = subprocess.run(
                [sys.executable, "-c", programme], capture_output=True,
                text=True, env={"PYTHONHASHSEED": graine, "PATH": "/usr/bin:/bin"},
                cwd=str(RACINE), timeout=120)
            assert resultat.returncode == 0, resultat.stderr
            sorties.add(resultat.stdout)
        assert len(sorties) == 1, "l'ordre du registre dépend de PYTHONHASHSEED"


# ═══════════════════════════════════════════════════════════════════
# construire_registre_par_defaut()
# ═══════════════════════════════════════════════════════════════════
class TestRegistreParDefaut:
    def test_chaque_appel_rend_un_registre_neuf(self):
        """Un registre partagé serait modifiable par n'importe quel appelant,
        et un test qui ajoute une capacité polluerait tous les suivants."""
        a, b = construire_registre_par_defaut(), construire_registre_par_defaut()
        assert a is not b
        a.enregistrer(_capacite(nom="ajout.local"))
        assert b.obtenir("ajout.local") is None

    def test_il_n_est_pas_vide(self, defaut):
        assert len(defaut.toutes()) >= 40

    def test_les_noms_sont_uniques(self, defaut):
        noms = [c.nom for c in defaut.toutes()]
        assert len(noms) == len(set(noms))

    def test_chaque_capacite_est_completement_renseignee(self, defaut):
        for c in defaut.toutes():
            assert c.titre.strip(), c.nom
            assert c.description.strip(), c.nom
            assert c.categorie in CATEGORIES, c.nom
            assert isinstance(c.parametres, tuple), c.nom

    def test_les_noms_suivent_la_convention_pointee(self, defaut):
        """`domaine.action`, en minuscules sans accent : ces noms voyagent
        dans le JSON de l'API et servent de clés de préférence."""
        motif = re.compile(r"^[a-z][a-z0-9_]*\.[a-z][a-z0-9_]*$")
        fautifs = [c.nom for c in defaut.toutes() if not motif.match(c.nom)]
        assert fautifs == []

    def test_les_descriptions_sont_en_francais_et_courtes(self, defaut):
        """Une phrase : ce que ça fait, pas comment. Elle est lue telle quelle
        dans l'interface."""
        for c in defaut.toutes():
            assert len(c.description) <= 160, c.nom


# ═══════════════════════════════════════════════════════════════════
# Confrontation au pont web — aucune capacité imaginaire
# ═══════════════════════════════════════════════════════════════════
class TestAlignementAvecLePont:
    def test_chaque_action_bridge_existe_vraiment(self, defaut, actions_du_pont):
        """Le test que le module lui-même annonce. Une capacité qui nomme une
        action inexistante sera proposée puis échouera sous les yeux de
        l'utilisateur."""
        inventees = sorted(c.nom for c in defaut.toutes()
                           if c.action_bridge and c.action_bridge not in actions_du_pont)
        assert inventees == [], f"actions absentes du pont : {inventees}"

    def test_aucune_capacite_ne_nomme_un_rouage_de_transport(self, defaut):
        """`job` et `job_cancel` suivent une opération longue ; ce ne sont pas
        des capacités qu'un utilisateur puisse demander."""
        assert {"job", "job_cancel"}.isdisjoint(
            {c.action_bridge for c in defaut.toutes()})

    def test_toute_action_gardee_est_au_moins_destructive(self, defaut):
        """Le pont a déjà jugé que ces actions méritaient une double
        validation. Les déclasser ici contredirait la seule porte
        d'exécution du projet — et surtout les rendrait exécutables par le
        mode autonome, qui ne regarde que le niveau déclaré."""
        source = (RACINE / "gui" / "bridge.py").read_text(encoding="utf-8")
        gardees = set(re.findall(r'_guarded\(\s*"([^"]+)"', source))
        assert gardees, "aucune action gardée trouvée : le motif a changé"

        declasses = sorted(
            c.nom for c in defaut.toutes()
            if c.action_bridge in gardees and int(c.risque) < int(Risque.DESTRUCTIF))
        assert declasses == [], f"capacités gardées mais déclassées : {declasses}"

    def test_aucune_capacite_destructive_n_est_executable_en_autonome(self, defaut):
        from assistant.risk import exige_confirmation
        libres = sorted(c.nom for c in defaut.toutes()
                        if not exige_confirmation(c.risque, True))
        ecrivent = [n for n in libres
                    if int(defaut.obtenir(n).risque) > int(Risque.LECTURE)]
        assert ecrivent == []

    def test_desarmer_une_protection_n_est_jamais_de_la_lecture(self, defaut):
        """Ces actions n'écrivent rien et seraient `LECTURE` à la lettre de
        l'échelle. Les y laisser autoriserait la machine à couper sa propre
        surveillance sans que personne ne l'ait demandé."""
        for action in ("realtime_stop", "shield_stop", "camera_watch_stop"):
            concernees = [c for c in defaut.toutes() if c.action_bridge == action]
            assert concernees, action
            for c in concernees:
                assert int(c.risque) >= int(Risque.REVERSIBLE), c.nom

    def test_une_suppression_definitive_est_irreversible(self, defaut):
        for action in ("quarantine_delete", "staging_purge", "apps_uninstall"):
            concernees = [c for c in defaut.toutes() if c.action_bridge == action]
            assert concernees, action
            for c in concernees:
                assert c.risque == Risque.IRREVERSIBLE, c.nom


# ═══════════════════════════════════════════════════════════════════
# Concurrence
# ═══════════════════════════════════════════════════════════════════
class TestConcurrence:
    def test_aucun_enregistrement_perdu_depuis_plusieurs_fils(self, registre):
        """Rien n'interdit à une surveillance de s'enregistrer depuis son
        propre fil. Un enregistrement perdu rendrait une capacité invisible
        au hasard du démarrage."""
        erreurs = []

        def travail(debut):
            for i in range(debut, debut + 40):
                try:
                    registre.enregistrer(_capacite(nom=f"systeme.c{i:04d}"))
                except Exception as exc:       # noqa: BLE001
                    erreurs.append(exc)

        fils = [threading.Thread(target=travail, args=(d,))
                for d in (0, 40, 80, 120)]
        for f in fils:
            f.start()
        for f in fils:
            f.join(timeout=30)

        assert erreurs == []
        assert len(registre.toutes()) == 160

    def test_un_doublon_concurrent_ne_passe_qu_une_fois(self, registre):
        """Le verrou doit couvrir le test d'existence ET l'insertion, sinon
        deux fils franchissent ensemble la vérification."""
        reussites = []

        def travail():
            try:
                registre.enregistrer(_capacite(nom="systeme.unique"))
                reussites.append(True)
            except ValueError:
                pass

        fils = [threading.Thread(target=travail) for _ in range(8)]
        for f in fils:
            f.start()
        for f in fils:
            f.join(timeout=30)

        assert len(reussites) == 1
        assert len(registre.toutes()) == 1
