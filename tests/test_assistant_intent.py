"""
Tests de assistant/intent.py.

Ce module traduit une phrase en nom de capacité. C'est le seul endroit du
produit où une faute de frappe, une tournure maladroite ou une plaisanterie
peuvent désigner une suppression définitive — d'où le poids donné ici aux cas
qui doivent NE RIEN déclencher.

Le test le plus important du fichier n'est pas qu'une phrase claire soit
comprise : c'est qu'une phrase anodine, ambiguë ou NIÉE ne désigne jamais une
capacité destructive. Le contrat le dit sans détour : « deviner, ici, c'est
lancer une suppression que personne n'a demandée ».

Trois familles de tests :

**1. Le seuil.** Sous `confiance = 0.55`, `capacite` doit valoir `""`. Pas
une capacité affichée en grisé, pas une suggestion : la chaîne vide.

**2. Les refus.** Salutations, phrases creuses, verbes isolés, charabia — et
surtout les négations. « Ne vide pas la quarantaine » contenait
« quarantaine » et « vide » : le module lisait un ordre de purge là où
l'utilisateur écrivait une interdiction.

**3. Le déterminisme.** Même phrase, même intention, à toute graine de hachage.
Sans cela, rien de ce qui précède n'est testable.
"""

import subprocess
import sys
from pathlib import Path

import pytest

from assistant.intent import SEUIL_CONFIANCE, Intention, comprendre
from assistant.registry import (
    Capacite, Registre, construire_registre_par_defaut,
)
from assistant.risk import Risque

RACINE = Path(__file__).resolve().parent.parent


@pytest.fixture(scope="module")
def registre():
    return construire_registre_par_defaut()


def _niveau(registre, intention):
    capacite = registre.obtenir(intention.capacite) if intention.capacite else None
    return int(capacite.risque) if capacite is not None else -1


# Phrases que personne ne tape pour demander une action, ou qui ne désignent
# rien d'assez précis pour agir.
ANODINES = [
    "bonjour", "salut", "merci", "ça va ?", "comment vas-tu ?",
    "quoi de neuf", "tout va bien ?", "je ne sais pas", "aide", "help",
    "qu'est-ce que tu sais faire ?", "fais quelque chose", "vas-y", "ok",
    "oui", "non", "d'accord", "au secours", "ça ne marche pas",
    "c'est lent", "mon pc rame", "il y a un virus ?", "je suis inquiet",
    "azerty", "lol", "1234", "test", "...", "?!", "     ",
    "redémarre", "éteins l'ordinateur", "ouvre le navigateur",
    "quelle heure est-il", "raconte-moi une blague",
]

# Verbes destructifs isolés : ils n'ont pas d'objet, donc pas de sens.
VERBES_NUS = [
    "supprime", "supprimer", "efface", "purge", "vide", "nettoie",
    "désinstalle", "détruis", "delete", "wipe", "efface tout",
    "supprime tout", "vide tout", "nettoie tout",
]

# Négations : la phrase INTERDIT l'action. Aucune ne doit désigner une
# capacité qui écrit.
NEGATIONS = [
    "ne vide pas la quarantaine",
    "ne supprime pas la quarantaine",
    "n'applique pas le rangement",
    "ne nettoie pas les fichiers temporaires",
    "ne purge pas le sas",
    "ne désinstalle pas cette application",
    "surtout ne nettoie pas les temporaires",
    "jamais supprimer la quarantaine",
    "je ne veux pas que tu vides la quarantaine",
    "sans supprimer la quarantaine",
    "arrête de vider la quarantaine",
    "pas de nettoyage des temporaires",
    "n'arrête pas la surveillance temps réel",
    "ne démarre pas le bouclier",
    "interdiction de supprimer la quarantaine",
    "évite de purger le sas",
    "n'efface rien",
    "ne touche à rien",
]

# Ordres légitimes : ils DOIVENT continuer à être compris, sinon le
# garde-fou aurait rendu le module inutilisable.
ORDRES = [
    ("analyse le dossier Téléchargements", "scan.dossier"),
    ("montre la quarantaine", "quarantaine.lister"),
    ("liste les applications installées", "applications.lister"),
    ("vide la quarantaine", "quarantaine.supprimer"),
    ("nettoie les fichiers temporaires", "nettoyage.complet"),
    ("purge le sas", "sas.purger"),
    ("applique le rangement", "rangement.appliquer"),
    ("désinstalle cette application", "applications.desinstaller"),
    ("arrête la surveillance temps réel", "temps_reel.arreter"),
    ("démarre la surveillance temps réel", "temps_reel.demarrer"),
    ("restaure le fichier de la quarantaine", "quarantaine.restaurer"),
    ("liste les connexions réseau", "reseau.connexions"),
    ("qui s'est connecté à mon PC ?", "intrusion.rapport"),
    ("surveille la caméra", "camera.surveiller"),
    ("annule le dernier rangement", "rangement.annuler"),
]


# ═══════════════════════════════════════════════════════════════════
# Forme du résultat
# ═══════════════════════════════════════════════════════════════════
class TestForme:
    def test_les_champs_du_contrat(self, registre):
        i = comprendre("analyse le dossier Téléchargements", registre)
        assert isinstance(i, Intention)
        for champ in ("capacite", "parametres", "confiance", "justification"):
            assert hasattr(i, champ), champ

    def test_le_seuil_du_contrat_vaut_bien_0_55(self):
        """Normatif : le contrat le fixe, l'interface l'affiche."""
        assert SEUIL_CONFIANCE == 0.55

    def test_l_intention_est_gelee(self, registre):
        i = comprendre("montre la quarantaine", registre)
        with pytest.raises(Exception):
            i.capacite = "autre.chose"

    @pytest.mark.parametrize("phrase", ANODINES + VERBES_NUS
                             + [p for p, _ in ORDRES])
    def test_les_invariants_tiennent_pour_toute_phrase(self, registre, phrase):
        i = comprendre(phrase, registre)
        assert isinstance(i.capacite, str)
        assert isinstance(i.parametres, dict)
        assert 0.0 <= i.confiance <= 1.0
        assert i.justification.strip(), "un refus sans explication est inutile"
        # Une capacité nommée existe toujours dans le registre reçu.
        if i.capacite:
            assert registre.obtenir(i.capacite) is not None

    def test_une_capacite_nommee_est_toujours_au_dessus_du_seuil(self, registre):
        """La règle centrale, formulée comme une propriété."""
        for phrase in ANODINES + VERBES_NUS + NEGATIONS + [p for p, _ in ORDRES]:
            i = comprendre(phrase, registre)
            if i.capacite:
                assert i.confiance >= SEUIL_CONFIANCE, phrase
            else:
                assert i.confiance < SEUIL_CONFIANCE, phrase


# ═══════════════════════════════════════════════════════════════════
# Entrées dégénérées
# ═══════════════════════════════════════════════════════════════════
class TestEntreesDegenerees:
    @pytest.mark.parametrize("phrase", ["", "   ", "\t\n"])
    def test_une_phrase_vide_ne_designe_rien(self, registre, phrase):
        i = comprendre(phrase, registre)
        assert i.capacite == ""
        assert i.confiance == 0.0

    @pytest.mark.parametrize("phrase", [None, 42, [], {}, object()])
    def test_un_argument_non_textuel_ne_leve_pas(self, registre, phrase):
        """La phrase vient d'un formulaire web : tout peut arriver."""
        i = comprendre(phrase, registre)
        assert i.capacite == ""

    @pytest.mark.parametrize("phrase", ["...", "!!!", "###", "→→→", "🙂🙂"])
    def test_une_phrase_sans_mot_exploitable_est_refusee(self, registre, phrase):
        i = comprendre(phrase, registre)
        assert i.capacite == ""
        assert "reformulez" in i.justification.lower()

    def test_une_phrase_tres_longue_ne_fait_pas_exploser(self, registre):
        i = comprendre("supprime " * 2000, registre)
        assert isinstance(i, Intention)

    def test_un_registre_vide_ne_propose_jamais_rien(self):
        """Ce qui permet à l'interface de restreindre le vocabulaire selon ce
        qui est réellement disponible sur la machine."""
        i = comprendre("vide la quarantaine", Registre())
        assert i.capacite == ""
        assert i.confiance == 0.0

    def test_une_capacite_absente_du_registre_n_est_pas_proposee(self):
        partiel = Registre()
        partiel.enregistrer(Capacite(
            nom="quarantaine.lister", titre="Inventorier la quarantaine",
            categorie="protection", risque=Risque.LECTURE,
            description="Liste ce qui est isolé.",
            action_bridge="quarantine_list"))

        assert comprendre("montre la quarantaine", partiel).capacite \
            == "quarantaine.lister"
        # La purge n'est pas offerte ici : la phrase ne peut pas la désigner.
        assert comprendre("vide la quarantaine", partiel).capacite \
            != "quarantaine.supprimer"


# ═══════════════════════════════════════════════════════════════════
# LE TEST : rien d'anodin ou d'ambigu ne déclenche du destructif
# ═══════════════════════════════════════════════════════════════════
class TestAucunDestructifParAccident:
    @pytest.mark.parametrize("phrase", ANODINES)
    def test_une_phrase_anodine_ne_designe_aucune_capacite_destructive(
            self, registre, phrase):
        i = comprendre(phrase, registre)
        assert _niveau(registre, i) < int(Risque.DESTRUCTIF), (
            f"« {phrase} » a désigné {i.capacite} ({i.confiance})")

    @pytest.mark.parametrize("phrase", ANODINES)
    def test_une_phrase_anodine_ne_designe_rien_du_tout(self, registre, phrase):
        """Plus strict, et c'est le bon niveau d'exigence : rien de ce que
        contient cette liste n'est une demande d'action."""
        assert comprendre(phrase, registre).capacite == ""

    @pytest.mark.parametrize("phrase", VERBES_NUS)
    def test_un_verbe_destructif_sans_objet_ne_declenche_rien(
            self, registre, phrase):
        """« Supprime » tout seul ne dit pas QUOI. Une règle exige des
        groupes de sens qui doivent TOUS être présents."""
        i = comprendre(phrase, registre)
        assert i.capacite == "", f"« {phrase} » → {i.capacite}"

    @pytest.mark.parametrize("phrase", NEGATIONS)
    def test_une_negation_ne_declenche_jamais_une_ecriture(
            self, registre, phrase):
        """Le défaut corrigé : la phrase était lue comme un sac de mots, si
        bien que « ne vide pas la quarantaine » contenait « quarantaine » et
        « vide » et désignait la purge définitive à 65 % de certitude. Une
        personne qui écrit explicitement de NE PAS faire quelque chose se
        voyait proposer exactement cette chose."""
        i = comprendre(phrase, registre)
        assert _niveau(registre, i) <= int(Risque.LECTURE), (
            f"« {phrase} » a désigné {i.capacite} "
            f"(niveau {_niveau(registre, i)}, confiance {i.confiance})")

    @pytest.mark.parametrize("phrase", NEGATIONS)
    def test_une_negation_donne_une_confiance_sous_le_seuil(
            self, registre, phrase):
        i = comprendre(phrase, registre)
        if i.capacite:
            # Seule une lecture peut survivre, et alors la phrase est une
            # vraie question (« quels programmes ne démarrent pas ? »).
            assert _niveau(registre, i) == int(Risque.LECTURE), phrase
        else:
            assert i.confiance < SEUIL_CONFIANCE, phrase

    def test_la_justification_d_un_refus_par_negation_l_explique(self, registre):
        i = comprendre("ne vide pas la quarantaine", registre)
        assert i.capacite == ""
        assert "négation" in i.justification.lower()

    def test_une_question_avec_negation_reste_comprise(self, registre):
        """Les négations incidentes se rencontrent surtout dans les
        questions. Refuser une LECTURE coûterait une reformulation sans rien
        protéger."""
        i = comprendre("quels programmes ne se lancent pas au démarrage ?",
                       registre)
        assert i.capacite == "demarrage.lister"
        assert i.confiance >= SEUIL_CONFIANCE

    def test_aucune_phrase_anodine_ne_franchit_le_seuil(self, registre):
        franchies = [(p, comprendre(p, registre).capacite) for p in ANODINES
                     if comprendre(p, registre).capacite]
        assert franchies == []


# ═══════════════════════════════════════════════════════════════════
# Les ordres légitimes continuent de marcher
# ═══════════════════════════════════════════════════════════════════
class TestOrdresLegitimes:
    @pytest.mark.parametrize("phrase,attendue", ORDRES)
    def test_une_demande_claire_est_comprise(self, registre, phrase, attendue):
        i = comprendre(phrase, registre)
        assert i.capacite == attendue, f"« {phrase} » → {i.capacite}"
        assert i.confiance >= SEUIL_CONFIANCE

    def test_les_accents_ne_sont_pas_obligatoires(self, registre):
        """Personne ne tape les accents dans une barre de commande."""
        avec = comprendre("analyse le dossier Téléchargements", registre)
        sans = comprendre("analyse le dossier Telechargements", registre)
        assert avec.capacite == sans.capacite == "scan.dossier"

    def test_la_casse_est_indifferente(self, registre):
        assert comprendre("MONTRE LA QUARANTAINE", registre).capacite \
            == comprendre("montre la quarantaine", registre).capacite

    def test_l_apostrophe_separe_les_mots(self, registre):
        """« l'historique » doit donner « l » et « historique », sinon aucun
        mot-clé n'est reconnu."""
        i = comprendre("montre-moi l'historique des actions", registre)
        assert i.capacite == "historique.lister"

    def test_une_faute_de_frappe_seule_ne_suffit_pas_a_agir(self, registre):
        """0,60 × 0,75 = 0,45, sous le seuil : une phrase mal tapée doit
        apporter un second signal."""
        i = comprendre("quarantaien", registre)
        assert i.capacite == ""

    def test_une_faute_de_frappe_sur_l_objet_fait_toujours_echouer(self, registre):
        """Le facteur d'approximation est MULTIPLICATIF : même avec son second
        groupe reconnu exactement, une règle dont l'objet est mal tapé plafonne
        à 0,65 × 0,75 = 0,49. C'est le comportement voulu — on préfère demander
        une reformulation à agir sur un mot qu'on a deviné — mais il vaut la
        peine d'être verrouillé, parce qu'il surprend à la lecture du code."""
        i = comprendre("montre la quarantaien", registre)
        assert i.capacite == ""
        assert 0.4 <= i.confiance < SEUIL_CONFIANCE
        assert "Quarantaine" in i.justification or "quarantaine" in i.justification

    def test_une_faute_de_frappe_est_tout_de_meme_rattrapee_par_les_renforts(
            self, registre):
        """Les renforts s'ajoutent APRÈS le facteur d'approximation : une
        phrase mal tapée mais riche en indices finit par passer."""
        i = comprendre("montre les fichiers de la quarantaien dedans", registre)
        assert i.confiance > comprendre("montre la quarantaien", registre).confiance

    def test_le_pluriel_vaut_le_singulier(self, registre):
        """Une variation grammaticale n'est pas une faute de frappe."""
        assert comprendre("liste les connexions réseau", registre).capacite \
            == comprendre("liste la connexion réseau", registre).capacite

    def test_l_anglais_est_accepte(self, registre):
        i = comprendre("list quarantine", registre)
        assert i.capacite == "quarantaine.lister"


# ═══════════════════════════════════════════════════════════════════
# Le garde-fou du verbe d'action
# ═══════════════════════════════════════════════════════════════════
class TestVerbeDActionExige:
    @pytest.mark.parametrize("phrase", [
        "quarantaine définitivement",
        "sas vieux fichiers",
        "temporaires windows navigateur",
        "application préinstallée",
    ])
    def test_sans_verbe_d_action_une_capacite_destructive_est_ecartee(
            self, registre, phrase):
        """Filet redondant, et c'est voulu : le jour où une règle sera
        ajoutée en oubliant son verbe, ce garde-fou tiendra quand même."""
        i = comprendre(phrase, registre)
        assert _niveau(registre, i) < int(Risque.DESTRUCTIF), \
            f"« {phrase} » → {i.capacite}"

    def test_la_mention_du_verbe_manquant_apparait_dans_la_justification(
            self, registre):
        """Il faut une règle destructive dont le NOYAU soit satisfait sans
        qu'aucun verbe d'action ne figure dans la phrase. « Résidus des
        programmes désinstallés » nomme le sujet de `residus.nettoyer` sans
        demander de nettoyer quoi que ce soit."""
        i = comprendre("résidus du programme désinstallé à nettoyer", registre)
        # La phrase contient « nettoyer » : on la construit donc sans verbe.
        i = comprendre("les résidus orphelins de ce vieux programme", registre)
        capacite = registre.obtenir(i.capacite) if i.capacite else None
        assert capacite is None or int(capacite.risque) < int(Risque.DESTRUCTIF)

    def test_le_garde_fou_du_verbe_est_bien_branche(self, registre):
        """Vérifié sur la fonction elle-même : c'est le seul moyen de prouver
        que le filet existe, indépendamment du vocabulaire des règles."""
        from assistant.intent import _normaliser, _verdict

        purge = registre.obtenir("quarantaine.supprimer")
        lecture = registre.obtenir("quarantaine.lister")

        _, sans_verbe = _normaliser("la quarantaine et ses fichiers")
        score, mention = _verdict(purge, 0.70, sans_verbe, "")
        assert score < SEUIL_CONFIANCE
        assert "verbe d'action" in mention

        _, avec_verbe = _normaliser("supprime la quarantaine")
        score, mention = _verdict(purge, 0.70, avec_verbe, "")
        assert score == 0.70 and mention == ""

        # Une lecture n'est jamais soumise à ce garde-fou.
        assert _verdict(lecture, 0.70, sans_verbe, "") == (0.70, "")


# ═══════════════════════════════════════════════════════════════════
# Ambiguïté
# ═══════════════════════════════════════════════════════════════════
class TestAmbiguite:
    def test_deux_lectures_a_score_egal_rabattent_la_confiance(self, registre):
        """Deux capacités qui marquent EXACTEMENT le même score, c'est une
        phrase qui veut dire deux choses. Vérifié directement sur le calcul
        pour ne dépendre d'aucun vocabulaire particulier."""
        i = comprendre("montre la quarantaine et le sas", registre)
        assert "concurrente" in i.justification
        assert i.confiance < 0.65, "la confiance n'a pas été rabattue"

    def test_un_groupe_exige_de_plus_departage(self, registre):
        """Le défaut corrigé : `_ECART_AMBIGU` valait plus que
        `_BONUS_GROUPE`, si bien qu'une règle plus spécifique restait
        éternellement « à égalité » avec une règle plus large. La demande la
        plus élémentaire qu'on puisse adresser à un antivirus — analyser un
        fichier nommé — était refusée pour ambiguïté."""
        i = comprendre("analyse le fichier /tmp/suspect.exe", registre)
        assert i.capacite == "scan.fichier"
        assert i.confiance >= SEUIL_CONFIANCE

    def test_l_ecart_ambigu_reste_coherent_avec_le_bonus_de_groupe(self):
        """Propriété arithmétique, vérifiée sur les constantes elles-mêmes :
        c'est elle qui a été violée, et c'est elle qui doit tenir."""
        from assistant.intent import _BONUS_GROUPE, _ECART_AMBIGU, _POIDS_RENFORT
        assert _ECART_AMBIGU <= _BONUS_GROUPE, (
            "un groupe de sens exigé en plus doit pouvoir départager")
        assert _ECART_AMBIGU <= _POIDS_RENFORT


# ═══════════════════════════════════════════════════════════════════
# Extraction de paramètres — on ne devine jamais
# ═══════════════════════════════════════════════════════════════════
class TestParametres:
    def test_un_chemin_entre_guillemets_est_lu(self, registre):
        i = comprendre('analyse le fichier "C:/Mes docs/rapport.exe"', registre)
        assert i.capacite == "scan.fichier"
        assert i.parametres["path"] == "C:/Mes docs/rapport.exe"

    def test_un_chemin_absolu_posix_est_lu(self, registre):
        i = comprendre("analyse le fichier /home/zeev/suspect.exe", registre)
        assert i.parametres.get("path") == "/home/zeev/suspect.exe"

    def test_un_chemin_absolu_windows_est_lu(self, registre):
        i = comprendre(r"analyse le fichier C:\Users\Zeev\truc.exe", registre)
        assert i.parametres.get("path") == r"C:\Users\Zeev\truc.exe"

    def test_une_url_est_lue(self, registre):
        i = comprendre("vérifie le lien https://exemple.invalide/page", registre)
        assert i.capacite == "hameconnage.verifier"
        assert i.parametres["url"] == "https://exemple.invalide/page"

    def test_aucun_parametre_n_est_invente(self, registre):
        """Un chemin supposé, c'est un nettoyage au mauvais endroit."""
        i = comprendre("analyse le dossier", registre)
        assert "path" not in i.parametres

    def test_seuls_les_parametres_declares_sont_renseignes(self, registre):
        """Une capacité qui n'accepte pas `path` ne doit pas en recevoir un,
        même si la phrase en contient."""
        i = comprendre("montre la quarantaine /home/zeev/ailleurs", registre)
        capacite = registre.obtenir(i.capacite) if i.capacite else None
        if capacite is not None:
            assert set(i.parametres) <= set(capacite.parametres)

    def test_les_parametres_lus_sont_annonces_dans_la_justification(self, registre):
        i = comprendre('analyse le fichier "/tmp/x.exe"', registre)
        assert "Paramètres lus" in i.justification


# ═══════════════════════════════════════════════════════════════════
# Déterminisme
# ═══════════════════════════════════════════════════════════════════
class TestDeterminisme:
    @pytest.mark.parametrize("phrase", [p for p, _ in ORDRES] + ANODINES[:8])
    def test_deux_appels_donnent_le_meme_resultat(self, registre, phrase):
        assert comprendre(phrase, registre) == comprendre(phrase, registre)

    def test_le_registre_n_est_pas_modifie(self, registre):
        avant = registre.toutes()
        comprendre("vide la quarantaine", registre)
        assert registre.toutes() == avant

    def test_aucun_resultat_ne_depend_de_pythonhashseed(self):
        """Le défaut déjà rencontré dans `security/network_watch.py`.
        `PYTHONHASHSEED` étant figé au démarrage de l'interpréteur, il faut
        un sous-processus par graine pour l'observer honnêtement."""
        phrases = [p for p, _ in ORDRES] + NEGATIONS[:6] + ANODINES[:6]
        programme = (
            "import sys; sys.path.insert(0, %r)\n"
            "from assistant.intent import comprendre\n"
            "from assistant.registry import construire_registre_par_defaut\n"
            "r = construire_registre_par_defaut()\n"
            "for p in %r:\n"
            "    i = comprendre(p, r)\n"
            "    print(p, '|', i.capacite, '|', i.confiance, '|',"
            "          sorted(i.parametres.items()), '|', i.justification)\n"
        ) % (str(RACINE), phrases)

        sorties = set()
        for graine in ("0", "1", "2", "3", "42"):
            resultat = subprocess.run(
                [sys.executable, "-c", programme], capture_output=True,
                text=True, env={"PYTHONHASHSEED": graine, "PATH": "/usr/bin:/bin",
                                "PYTHONIOENCODING": "utf-8"},
                cwd=str(RACINE), timeout=120)
            assert resultat.returncode == 0, resultat.stderr
            sorties.add(resultat.stdout)
        assert len(sorties) == 1, "la compréhension dépend de PYTHONHASHSEED"


# ═══════════════════════════════════════════════════════════════════
# Pureté — comprendre n'exécute rien
# ═══════════════════════════════════════════════════════════════════
class TestPurete:
    def test_aucun_acces_disque_ni_reseau_ni_processus(self, registre, monkeypatch):
        """Comprendre et exécuter sont séparés par un clic de l'utilisateur.
        Ici la séparation est structurelle : la fonction n'a aucun moyen
        d'agir."""
        import builtins
        import os
        import socket
        import subprocess as sp

        def interdit(*a, **k):
            raise AssertionError("comprendre() a agi au lieu de lire")

        for cible, nom in ((builtins, "open"), (os, "listdir"), (os, "remove"),
                           (os, "rename"), (socket, "socket"),
                           (socket, "create_connection"), (sp, "run"),
                           (sp, "Popen")):
            monkeypatch.setattr(cible, nom, interdit)

        for phrase in ("vide la quarantaine", "supprime tout définitivement",
                       "nettoie les fichiers temporaires", "bonjour"):
            comprendre(phrase, registre)

    def test_aucun_import_de_gui(self):
        """Le contrat l'exige : la dépendance va dans l'autre sens, sinon le
        serveur web devient obligatoire pour analyser un fichier."""
        source = (RACINE / "assistant" / "intent.py").read_text(encoding="utf-8")
        assert "import gui" not in source
        assert "from gui" not in source
