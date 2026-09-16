"""
registry.py — ce qu'ANTI-ZEEVIRIUS sait faire, dit une bonne fois.

Le produit expose une soixantaine d'actions, réparties entre le menu en ligne
de commande de `main.py` et le pont web `gui/bridge.py`. Tant qu'un humain
choisissait dans un menu, cette liste n'avait pas besoin d'exister ailleurs
que dans sa tête. Dès que quelque chose doit *choisir* — comprendre une
phrase, proposer une action, décider ce qu'un mode autonome a le droit de
lancer — il faut une description des capacités, lisible par du code.

C'est ce fichier. Il ne réimplémente rien : chaque `Capacite` nomme l'action
du pont qui fait le travail (`action_bridge`), et s'arrête là. Le jour où une
action change de comportement, c'est le pont qui change, pas ce registre.

Deux principes de rédaction, à respecter pour toute capacité ajoutée :

1. **Aucune action inventée.** `action_bridge` doit nommer une action qui
   existe vraiment dans `gui/bridge.py` (méthode `a_<action>`), et
   `tests/test_assistant_registry.py` le vérifie en confrontant ce registre
   à `Bridge.known_actions()`. Une capacité qui décrit une fonctionnalité
   imaginaire est pire qu'une capacité absente : l'assistant la proposera.

2. **Le niveau de risque se lit côté conséquence, pas côté intention.** La
   règle appliquée ici, plus stricte que la simple lecture de l'échelle :

   * tout ce qui passe par `_guarded()` dans le pont est au minimum
     `DESTRUCTIF`, même quand le module sait annuler (`triage_apply`,
     `organize_apply`, `startup_disable`). Le pont a déjà jugé que ces
     actions méritaient une double validation ; les déclasser ici
     reviendrait à contredire la seule porte d'exécution du projet.
   * une action qui **désarme une protection** (`realtime_stop`,
     `shield_stop`, `camera_watch_stop`) n'écrit rien et serait donc
     `LECTURE` à la lettre de l'échelle. Elle est classée `REVERSIBLE`,
     parce que le mode autonome n'exécute que du `LECTURE` : la classer plus
     bas autoriserait la machine à couper sa propre surveillance sans que
     personne ne l'ait demandé. Le contrat ne tranche pas ce cas ; c'est le
     choix le plus sûr des deux.

Les actions `job` et `job_cancel` du pont n'apparaissent pas ici : ce sont des
rouages de transport pour les opérations longues, pas des capacités qu'un
utilisateur puisse demander ni qu'un assistant puisse proposer.

Déterminisme : `toutes()` et `par_categorie()` trient par nom. Un `dict`
Python conserve l'ordre d'insertion, mais le registre est aussi construit et
complété par d'autres chantiers — se reposer sur l'ordre d'insertion ferait
dépendre l'affichage de l'ordre des imports. Le tri est explicite, et un test
le vérifie sous plusieurs `PYTHONHASHSEED` : ce dépôt a déjà perdu une
journée sur un `set` parcouru tel quel (`security/network_watch.py`).
"""

from __future__ import annotations

import dataclasses
import threading
from typing import Dict, Optional, Tuple

from assistant.risk import Risque

__all__ = ["Capacite", "Registre", "CATEGORIES", "construire_registre_par_defaut"]


# Les cinq rayons du magasin. Fermé volontairement : une sixième catégorie
# inventée au fil de l'eau ne serait affichée nulle part dans l'interface, et
# la capacité deviendrait invisible sans que rien ne signale l'erreur.
CATEGORIES: Tuple[str, ...] = (
    "protection", "nettoyage", "securite", "systeme", "rangement",
)


@dataclasses.dataclass(frozen=True)
class Capacite:
    """Une chose que le produit sait faire, décrite pour être choisie.

    `frozen=True` : le registre est partagé entre l'interface, le mode
    autonome et la compréhension des commandes, potentiellement depuis
    plusieurs fils. Une capacité modifiable en place, c'est un niveau de
    risque qui peut changer entre le moment où on l'affiche et celui où on
    l'exécute.
    """

    nom: str              # identifiant stable, ex. "scan.rapide"
    titre: str            # libellé humain, français
    categorie: str        # protection | nettoyage | securite | systeme | rangement
    risque: Risque
    description: str      # une phrase : ce que ça fait, pas comment
    action_bridge: str    # action du contrat d'API web, ou "" si interne
    parametres: Tuple[str, ...] = ()


class Registre:
    """Collection de capacités indexée par nom, à lecture déterministe."""

    def __init__(self) -> None:
        self._capacites: Dict[str, Capacite] = {}
        # Le registre par défaut est construit une fois au démarrage, mais rien
        # n'interdit à un module de s'enregistrer plus tard, depuis un autre
        # fil (surveillance, tâche planifiée). Le verrou coûte un temps nul et
        # évite un enregistrement perdu.
        self._verrou = threading.Lock()

    def enregistrer(self, c: Capacite) -> None:
        """Ajoute une capacité. Nom déjà pris → `ValueError`.

        Le refus du doublon n'est pas une coquetterie : deux capacités de même
        nom, c'est une phrase utilisateur qui déclenche l'une ou l'autre selon
        l'ordre des imports. On préfère une erreur au démarrage.
        """
        if not isinstance(c, Capacite):
            raise ValueError(f"capacité attendue, reçu {type(c).__name__}")
        if not isinstance(c.nom, str) or not c.nom.strip():
            raise ValueError("une capacité doit avoir un nom non vide")
        if c.categorie not in CATEGORIES:
            raise ValueError(
                f"catégorie inconnue « {c.categorie} » pour « {c.nom} » ; "
                f"attendu l'une de {', '.join(CATEGORIES)}"
            )
        try:
            Risque(int(c.risque))
        except (TypeError, ValueError):
            raise ValueError(f"niveau de risque illisible pour « {c.nom} » : {c.risque!r}") from None
        with self._verrou:
            if c.nom in self._capacites:
                raise ValueError(f"capacité déjà enregistrée : « {c.nom} »")
            self._capacites[c.nom] = c

    def obtenir(self, nom: str) -> Optional[Capacite]:
        """La capacité de ce nom, ou None. Jamais d'exception : un nom inconnu
        vient souvent d'une phrase mal comprise, ce n'est pas une panne."""
        if not isinstance(nom, str):
            return None
        return self._capacites.get(nom)

    def toutes(self) -> Tuple[Capacite, ...]:
        """Toutes les capacités, triées par nom. Ordre stable, garanti."""
        return tuple(sorted(self._capacites.values(), key=lambda c: c.nom))

    def par_categorie(self, categorie: str) -> Tuple[Capacite, ...]:
        """Les capacités d'une catégorie, triées par nom.

        Une catégorie inconnue rend un tuple vide plutôt qu'une erreur :
        l'appelant est le plus souvent une boucle d'affichage, et un onglet
        vide vaut mieux qu'une interface qui ne se charge pas.
        """
        return tuple(c for c in self.toutes() if c.categorie == categorie)


def construire_registre_par_defaut() -> Registre:
    """Le registre réel d'ANTI-ZEEVIRIUS, aligné sur `gui/bridge.py`.

    Chaque appel rend un registre NEUF. Un registre unique partagé serait
    modifiable par n'importe quel appelant — et un test qui ajoute une
    capacité en polluerait tous les suivants.
    """
    r = Registre()
    for c in _CAPACITES_PAR_DEFAUT:
        r.enregistrer(c)
    return r


# ── Le catalogue ───────────────────────────────────────────────────────────
# Ordre de lecture : protection, nettoyage, rangement, système, sécurité. Cet
# ordre n'a aucune conséquence (tout est trié à la sortie), il n'est là que
# pour qu'un humain relise la liste à côté du contrat d'API sans sauter de
# ligne.

_CAPACITES_PAR_DEFAUT: Tuple[Capacite, ...] = (

    # ── Protection ─────────────────────────────────────────────────────────
    Capacite(
        nom="scan.fichier",
        titre="Analyser un fichier",
        categorie="protection",
        risque=Risque.LECTURE,
        description="Passe un fichier au crible des signatures, des règles YARA et de l'heuristique.",
        action_bridge="scan_file",
        parametres=("path",),
    ),
    Capacite(
        nom="scan.dossier",
        titre="Analyser un dossier",
        categorie="protection",
        risque=Risque.LECTURE,
        description="Analyse récursivement un dossier et rend la liste des fichiers jugés malveillants.",
        action_bridge="scan_directory",
        parametres=("path",),
    ),
    # Démarrer la surveillance temps réel n'est pas une simple lecture : le
    # moteur qu'elle branche peut mettre un fichier en quarantaine de sa propre
    # initiative. C'est réversible (l'Historique restaure), pas anodin.
    Capacite(
        nom="temps_reel.demarrer",
        titre="Démarrer la surveillance en temps réel",
        categorie="protection",
        risque=Risque.REVERSIBLE,
        description="Surveille les dossiers indiqués et analyse chaque fichier qui y apparaît.",
        action_bridge="realtime_start",
        parametres=("folders",),
    ),
    Capacite(
        nom="temps_reel.arreter",
        titre="Arrêter la surveillance en temps réel",
        categorie="protection",
        risque=Risque.REVERSIBLE,
        description="Coupe la surveillance des dossiers en cours.",
        action_bridge="realtime_stop",
    ),
    Capacite(
        nom="quarantaine.lister",
        titre="Lister la quarantaine",
        categorie="protection",
        risque=Risque.LECTURE,
        description="Énumère les fichiers mis à l'isolement, avec leur origine et leur date.",
        action_bridge="quarantine_list",
    ),
    Capacite(
        nom="quarantaine.restaurer",
        titre="Restaurer un fichier en quarantaine",
        categorie="protection",
        risque=Risque.REVERSIBLE,
        description="Remet un fichier isolé à son emplacement d'origine.",
        action_bridge="quarantine_restore",
        parametres=("id",),
    ),
    # Le pont l'annonce en toutes lettres : « suppression DÉFINITIVE et
    # irréversible ». Rien ne la reconstruit, pas même l'Historique.
    Capacite(
        nom="quarantaine.supprimer",
        titre="Supprimer définitivement un fichier en quarantaine",
        categorie="protection",
        risque=Risque.IRREVERSIBLE,
        description="Détruit sans retour un fichier isolé, y compris sa copie de sauvegarde.",
        action_bridge="quarantine_delete",
        parametres=("id",),
    ),
    # Le bouclier écrit de vrais fichiers leurres sur le disque : ce n'est pas
    # une lecture, même si l'intention est protectrice.
    Capacite(
        nom="bouclier.demarrer",
        titre="Déployer le bouclier anti-rançongiciel",
        categorie="protection",
        risque=Risque.REVERSIBLE,
        description="Sème des fichiers leurres dans les dossiers indiqués pour détecter un chiffrement de masse.",
        action_bridge="shield_start",
        parametres=("folders",),
    ),
    Capacite(
        nom="bouclier.etat",
        titre="État du bouclier anti-rançongiciel",
        categorie="protection",
        risque=Risque.LECTURE,
        description="Indique si les leurres sont intacts et à quel seuil l'alerte se déclenchera.",
        action_bridge="shield_status",
    ),
    Capacite(
        nom="bouclier.processus",
        titre="Processus suspects d'écriture massive",
        categorie="protection",
        risque=Risque.LECTURE,
        description="Classe les processus qui écrivent le plus, signature habituelle d'un chiffrement en cours.",
        action_bridge="shield_processes",
        parametres=("top_n",),
    ),
    Capacite(
        nom="bouclier.arreter",
        titre="Retirer le bouclier anti-rançongiciel",
        categorie="protection",
        risque=Risque.REVERSIBLE,
        description="Supprime les fichiers leurres et cesse la surveillance du chiffrement.",
        action_bridge="shield_stop",
    ),
    # Interroger VirusTotal n'écrit rien : c'est du LECTURE au sens de
    # l'échelle. À savoir tout de même, parce que l'échelle ne le dit pas :
    # cette capacité fait SORTIR une empreinte de la machine et consomme un
    # quota journalier. Un appelant automatique doit s'en souvenir — la
    # description le dit à l'utilisateur, faute de champ prévu pour cela.
    Capacite(
        nom="reputation.verifier",
        titre="Vérifier la réputation d'un fichier",
        categorie="protection",
        risque=Risque.LECTURE,
        description="Interroge VirusTotal en ligne sur l'empreinte du fichier ; le fichier lui-même n'est jamais envoyé.",
        action_bridge="reputation_check",
        parametres=("path", "sha256"),
    ),
    Capacite(
        nom="reputation.configuree",
        titre="Vérifier la configuration de VirusTotal",
        categorie="protection",
        risque=Risque.LECTURE,
        description="Dit si une clé d'API VirusTotal est en place, sans la divulguer.",
        action_bridge="reputation_configured",
    ),
    Capacite(
        nom="hameconnage.verifier",
        titre="Vérifier un lien suspect",
        categorie="protection",
        risque=Risque.LECTURE,
        description="Confronte une adresse web à une liste noire d'hameçonnage et à des heuristiques locales.",
        action_bridge="phishing_check",
        parametres=("url",),
    ),

    # ── Nettoyage ──────────────────────────────────────────────────────────
    Capacite(
        nom="nettoyage.complet",
        titre="Nettoyer les fichiers temporaires",
        categorie="nettoyage",
        risque=Risque.DESTRUCTIF,
        description="Vide les dossiers temporaires, la corbeille et les caches des navigateurs.",
        action_bridge="clean_full",
        parametres=("include_admin",),
    ),
    Capacite(
        nom="disque.analyser",
        titre="Analyser l'occupation du disque",
        categorie="nettoyage",
        risque=Risque.LECTURE,
        description="Repère les plus gros fichiers, les dossiers les plus lourds et les doublons.",
        action_bridge="disk_analyze",
        parametres=("path",),
    ),
    Capacite(
        nom="residus.raccourcis",
        titre="Lister les raccourcis orphelins",
        categorie="nettoyage",
        risque=Risque.LECTURE,
        description="Trouve les raccourcis qui pointent vers un programme désinstallé.",
        action_bridge="residue_shortcuts",
    ),
    Capacite(
        nom="residus.registre",
        titre="Lister les entrées de registre orphelines",
        categorie="nettoyage",
        risque=Risque.LECTURE,
        description="Trouve les entrées de désinstallation dont le programme n'existe plus.",
        action_bridge="residue_registry",
    ),
    Capacite(
        nom="residus.dossiers",
        titre="Lister les dossiers résiduels",
        categorie="nettoyage",
        risque=Risque.LECTURE,
        description="Trouve les dossiers de programmes désinstallés restés sur le disque.",
        action_bridge="residue_folders",
    ),
    # Réversible par le sas, mais gardée par `_guarded()` : au minimum
    # DESTRUCTIF, comme toutes les actions à double validation.
    Capacite(
        nom="residus.nettoyer",
        titre="Nettoyer les résidus de désinstallation",
        categorie="nettoyage",
        risque=Risque.DESTRUCTIF,
        description="Met de côté les raccourcis, clés de registre et dossiers laissés par des programmes disparus.",
        action_bridge="residue_clean",
        parametres=("kind", "items"),
    ),
    Capacite(
        nom="applications.lister",
        titre="Lister les applications installées",
        categorie="nettoyage",
        risque=Risque.LECTURE,
        description="Inventorie les programmes installés, classés par taille ou par date d'usage.",
        action_bridge="apps_list",
        parametres=("sort_by",),
    ),
    # Une désinstallation ne s'annule pas : il faut retrouver l'installeur, la
    # licence, et les données du programme sont perdues avec lui.
    Capacite(
        nom="applications.desinstaller",
        titre="Désinstaller une application",
        categorie="nettoyage",
        risque=Risque.IRREVERSIBLE,
        description="Lance la désinstallation d'un programme ; seule une réinstallation le rétablit.",
        action_bridge="apps_uninstall",
        parametres=("app",),
    ),
    # Moins grave que la précédente : les paquets préinstallés se réinstallent
    # depuis le Microsoft Store. Destructif tout de même, et gardé.
    Capacite(
        nom="applications.degraisser",
        titre="Retirer les applications préinstallées superflues",
        categorie="nettoyage",
        risque=Risque.DESTRUCTIF,
        description="Supprime les paquets Windows préinstallés reconnus comme superflus, réinstallables depuis le Store.",
        action_bridge="apps_debloat",
        parametres=("apps",),
    ),

    # ── Rangement ──────────────────────────────────────────────────────────
    Capacite(
        nom="tri.analyser",
        titre="Repérer les fichiers à trier",
        categorie="rangement",
        risque=Risque.LECTURE,
        description="Liste les fichiers d'un dossier qui méritent d'être mis de côté, sans rien déplacer.",
        action_bridge="triage_scan",
        parametres=("path",),
    ),
    Capacite(
        nom="tri.appliquer",
        titre="Mettre des fichiers de côté",
        categorie="rangement",
        risque=Risque.DESTRUCTIF,
        description="Déplace les fichiers désignés vers le sas, d'où ils peuvent être restaurés.",
        action_bridge="triage_apply",
        parametres=("files",),
    ),
    Capacite(
        nom="sas.lister",
        titre="Lister le sas",
        categorie="rangement",
        risque=Risque.LECTURE,
        description="Énumère les fichiers mis de côté et leur date d'entrée.",
        action_bridge="staging_list",
    ),
    Capacite(
        nom="sas.restaurer",
        titre="Restaurer un fichier du sas",
        categorie="rangement",
        risque=Risque.REVERSIBLE,
        description="Remet un fichier mis de côté à son emplacement d'origine.",
        action_bridge="staging_restore",
        parametres=("id",),
    ),
    Capacite(
        nom="sas.purger",
        titre="Vider le sas",
        categorie="rangement",
        risque=Risque.IRREVERSIBLE,
        description="Détruit sans retour les fichiers du sas plus anciens qu'un nombre de jours donné.",
        action_bridge="staging_purge",
        parametres=("older_than_days",),
    ),
    Capacite(
        nom="rangement.plan",
        titre="Proposer un plan de rangement",
        categorie="rangement",
        risque=Risque.LECTURE,
        description="Calcule où iraient les fichiers d'un dossier, par catégorie, application ou importance.",
        action_bridge="organize_plan",
        parametres=("path", "mode"),
    ),
    Capacite(
        nom="rangement.appliquer",
        titre="Appliquer un plan de rangement",
        categorie="rangement",
        risque=Risque.DESTRUCTIF,
        description="Déplace les fichiers selon le plan proposé ; la session est annulable.",
        action_bridge="organize_apply",
        parametres=("plan",),
    ),
    Capacite(
        nom="rangement.deplacer_dossier",
        titre="Déplacer un dossier dans un autre",
        categorie="rangement",
        risque=Risque.DESTRUCTIF,
        description="Déplace un dossier entier à l'intérieur d'un dossier cible.",
        action_bridge="organize_move_folder",
        parametres=("source", "target"),
    ),
    Capacite(
        nom="rangement.peu_utilises",
        titre="Ranger les fichiers peu utilisés",
        categorie="rangement",
        risque=Risque.DESTRUCTIF,
        description="Regroupe dans un sous-dossier dédié les fichiers non ouverts depuis un certain temps.",
        action_bridge="organize_least_used",
        parametres=("path", "days"),
    ),
    Capacite(
        nom="rangement.sessions",
        titre="Lister les sessions de rangement",
        categorie="rangement",
        risque=Risque.LECTURE,
        description="Énumère les rangements effectués, chacun annulable individuellement.",
        action_bridge="organize_sessions",
    ),
    Capacite(
        nom="rangement.annuler",
        titre="Annuler un rangement",
        categorie="rangement",
        risque=Risque.REVERSIBLE,
        description="Remet en place tous les fichiers déplacés par une session de rangement.",
        action_bridge="organize_undo",
        parametres=("session_id",),
    ),

    # ── Système ────────────────────────────────────────────────────────────
    Capacite(
        nom="etat.systeme",
        titre="État général du système",
        categorie="systeme",
        risque=Risque.LECTURE,
        description="Résume la plateforme, les modules disponibles, les signatures et les protections actives.",
        action_bridge="status",
    ),
    Capacite(
        nom="demarrage.lister",
        titre="Lister les programmes au démarrage",
        categorie="systeme",
        risque=Risque.LECTURE,
        description="Énumère ce que Windows lance à l'ouverture de session, registre et dossier de démarrage.",
        action_bridge="startup_list",
    ),
    Capacite(
        nom="demarrage.desactiver",
        titre="Désactiver un programme au démarrage",
        categorie="systeme",
        risque=Risque.DESTRUCTIF,
        description="Déplace une entrée de démarrage vers une clé de sauvegarde, d'où elle peut être rétablie.",
        action_bridge="startup_disable",
        parametres=("hive", "key_path", "name"),
    ),
    Capacite(
        nom="demarrage.restaurer",
        titre="Rétablir un programme au démarrage",
        categorie="systeme",
        risque=Risque.REVERSIBLE,
        description="Remet en place une entrée de démarrage précédemment désactivée.",
        action_bridge="startup_restore",
        parametres=("hive", "name"),
    ),
    Capacite(
        nom="planification.nettoyage",
        titre="Planifier un nettoyage hebdomadaire",
        categorie="systeme",
        risque=Risque.REVERSIBLE,
        description="Crée une tâche Windows qui lancera le nettoyage au jour et à l'heure choisis.",
        action_bridge="schedule_cleanup",
        parametres=("day", "time"),
    ),
    Capacite(
        nom="planification.retirer",
        titre="Retirer le nettoyage planifié",
        categorie="systeme",
        risque=Risque.REVERSIBLE,
        description="Supprime la tâche planifiée de nettoyage.",
        action_bridge="schedule_remove",
    ),
    Capacite(
        nom="gardien.executer",
        titre="Lancer une passe du gardien",
        categorie="systeme",
        risque=Risque.DESTRUCTIF,
        description="Enchaîne nettoyage, mise de côté et rangement en une passe ; rien n'est supprimé définitivement.",
        action_bridge="guardian_run",
        parametres=("folders", "unused_threshold_days"),
    ),
    Capacite(
        nom="gardien.en_attente",
        titre="Lister ce que le gardien a mis de côté",
        categorie="systeme",
        risque=Risque.LECTURE,
        description="Énumère les éléments en attente de suppression définitive, avec leur âge.",
        action_bridge="guardian_pending",
    ),
    Capacite(
        nom="gardien.confirmer",
        titre="Confirmer les suppressions du gardien",
        categorie="systeme",
        risque=Risque.IRREVERSIBLE,
        description="Détruit sans retour les éléments mis de côté depuis plus d'un nombre de jours donné.",
        action_bridge="guardian_confirm",
        parametres=("older_than_days",),
    ),
    Capacite(
        nom="gardien.planifier",
        titre="Planifier le gardien quotidien",
        categorie="systeme",
        risque=Risque.REVERSIBLE,
        description="Crée une tâche Windows qui lancera le gardien chaque jour à l'heure choisie.",
        action_bridge="guardian_schedule",
        parametres=("time",),
    ),
    Capacite(
        nom="gardien.deplanifier",
        titre="Retirer le gardien planifié",
        categorie="systeme",
        risque=Risque.REVERSIBLE,
        description="Supprime la tâche planifiée du gardien.",
        action_bridge="guardian_unschedule",
    ),
    Capacite(
        nom="historique.lister",
        titre="Consulter l'historique des actions",
        categorie="systeme",
        risque=Risque.LECTURE,
        description="Rassemble en une seule liste tout ce que le produit a déplacé, isolé ou supprimé.",
        action_bridge="history_list",
        parametres=("limite", "filtre"),
    ),
    Capacite(
        nom="historique.annuler",
        titre="Annuler une action de l'historique",
        categorie="systeme",
        risque=Risque.REVERSIBLE,
        description="Remet en place ce qu'une entrée de l'historique avait retiré.",
        action_bridge="history_undo",
        parametres=("id",),
    ),

    # ── Sécurité avancée (extension V2) ────────────────────────────────────
    Capacite(
        nom="reseau.connexions",
        titre="Lister les connexions réseau",
        categorie="securite",
        risque=Risque.LECTURE,
        description="Photographie les connexions établies, leur processus propriétaire et ce qui y est anormal.",
        action_bridge="network_connections",
    ),
    Capacite(
        nom="reseau.applications",
        titre="Résumer le réseau par application",
        categorie="securite",
        risque=Risque.LECTURE,
        description="Regroupe les connexions par programme pour voir d'un coup d'œil qui parle à l'extérieur.",
        action_bridge="network_apps",
    ),
    Capacite(
        nom="intrusion.rapport",
        titre="Qui s'est connecté à cette machine",
        categorie="securite",
        risque=Risque.LECTURE,
        description="Relit les journaux d'ouverture de session : réussites, échecs, accès à distance.",
        action_bridge="intrusion_report",
        parametres=("jours",),
    ),
    # Modifie une stratégie d'audit du système et passe par `_guarded()`.
    Capacite(
        nom="intrusion.audit",
        titre="Activer l'audit des accès aux dossiers",
        categorie="securite",
        risque=Risque.DESTRUCTIF,
        description="Active la journalisation Windows des accès aux dossiers indiqués, en modifiant la stratégie d'audit.",
        action_bridge="intrusion_audit_enable",
        parametres=("folders",),
    ),
    Capacite(
        nom="camera.etat",
        titre="État de la caméra et du micro",
        categorie="securite",
        risque=Risque.LECTURE,
        description="Dit quelles applications ont accès à la caméra et si elle est utilisée en ce moment.",
        action_bridge="camera_state",
    ),
    Capacite(
        nom="camera.recentes",
        titre="Utilisations récentes de la caméra",
        categorie="securite",
        risque=Risque.LECTURE,
        description="Retrace les accès à la caméra sur les dernières heures, application par application.",
        action_bridge="camera_recent",
        parametres=("heures",),
    ),
    Capacite(
        nom="camera.autoriser",
        titre="Autoriser une application à utiliser la caméra",
        categorie="securite",
        risque=Risque.REVERSIBLE,
        description="Ajoute une application à la liste de celles dont l'accès à la caméra n'alerte plus.",
        action_bridge="camera_allow",
        parametres=("app",),
    ),
    Capacite(
        nom="camera.retirer",
        titre="Retirer l'autorisation caméra d'une application",
        categorie="securite",
        risque=Risque.REVERSIBLE,
        description="Retire une application de la liste des accès caméra tolérés.",
        action_bridge="camera_revoke",
        parametres=("app",),
    ),
    Capacite(
        nom="camera.surveiller",
        titre="Surveiller la caméra",
        categorie="securite",
        risque=Risque.REVERSIBLE,
        description="Alerte dès qu'une application non autorisée ouvre la caméra.",
        action_bridge="camera_watch_start",
    ),
    Capacite(
        nom="camera.arreter",
        titre="Arrêter la surveillance de la caméra",
        categorie="securite",
        risque=Risque.REVERSIBLE,
        description="Cesse d'alerter sur les accès à la caméra.",
        action_bridge="camera_watch_stop",
    ),
    Capacite(
        nom="incident.etat",
        titre="État du mode incident",
        categorie="securite",
        risque=Risque.LECTURE,
        description="Dit si la machine est en confinement, depuis quand, et ce qui est gelé.",
        action_bridge="incident_state",
    ),
    Capacite(
        nom="incident.plan",
        titre="Plan du mode incident",
        categorie="securite",
        risque=Risque.LECTURE,
        description="Montre ce que le confinement couperait et gèlerait, sans rien exécuter.",
        action_bridge="incident_plan",
    ),
    # Entièrement réversible — rien n'est supprimé, les processus sont
    # suspendus et non arrêtés — mais gardé par `_guarded()` et brutal :
    # couper le réseau d'une machine ne se décide pas tout seul.
    Capacite(
        nom="incident.activer",
        titre="Activer le mode incident",
        categorie="securite",
        risque=Risque.DESTRUCTIF,
        description="Coupe le réseau, gèle les processus suspects et prend un cliché des dossiers personnels.",
        action_bridge="incident_activate",
    ),
    Capacite(
        nom="incident.retablir",
        titre="Sortir du mode incident",
        categorie="securite",
        risque=Risque.REVERSIBLE,
        description="Rétablit le réseau et relance les processus gelés ; c'est la porte de secours.",
        action_bridge="incident_restore",
    ),
)
