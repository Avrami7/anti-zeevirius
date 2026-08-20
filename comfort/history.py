"""
history.py — historique unifié et réversible (conception V2, §4.3).

Le produit promet « tout est réversible », et c'est vrai. Mais la
réversibilité est aujourd'hui éparpillée dans quatre mécanismes qui ne se
connaissent pas, chacun avec son index, son format et sa commande
d'annulation :

    quarantine/quarantine_manager.py   restore_file(id)
    optimizer/file_triage.py           restore_from_staging(id)
    optimizer/folder_organizer.py      undo_session(session_id)
    optimizer/startup_manager.py       restore_registry_item(hive, name)

L'utilisateur devait donc savoir DANS QUEL module chercher pour annuler
quelque chose — et une action oubliée est une action jamais annulée.

Ce module n'ajoute aucun mécanisme : il AGRÈGE les quatre en une seule vue
chronologique, avec un bouton « annuler » par entrée.

── Architecture : des adaptateurs, pas des modifications ───────────────
Les quatre modules sont en LECTURE SEULE ici. Chacun est vu à travers un
adaptateur qui lit son index dans son format d'origine et le traduit vers
l'entrée d'historique commune. Ajouter un mécanisme plus tard (pare-feu,
services Windows, mode incident) = ajouter un adaptateur, ou simplement
appeler `enregistrer()` — sans toucher au reste.

── Robustesse : un index cassé n'a pas le droit de casser la vue ───────
On consulte l'historique précisément au moment où quelque chose s'est mal
passé. Un JSON tronqué, un dossier absent, un champ manquant, un adaptateur
qui lève : le problème est RAPPORTÉ dans `problemes`, et les autres sources
continuent de répondre. Aucune exception ne remonte de `lister()`.

── Ce que ce module ne fait pas ────────────────────────────────────────
Il ne supprime rien, jamais. Il lit et il annule. La purge du sas, la
suppression définitive d'une quarantaine et la purge du journal restent la
responsabilité des modules d'origine, avec leurs confirmations.
"""

from __future__ import annotations

import json
import os
import threading
import uuid
from datetime import datetime
from pathlib import Path
from typing import Any, Callable, Dict, Iterable, List, Optional

import paths as _paths

__all__ = [
    "SOURCE_QUARANTAINE", "SOURCE_SAS", "SOURCE_RANGEMENT",
    "SOURCE_DEMARRAGE", "SOURCE_JOURNAL",
    "Adaptateur", "AdaptateurQuarantaine", "AdaptateurSas",
    "AdaptateurRangement", "AdaptateurDemarrage", "AdaptateurJournal",
    "HistoriqueUnifie", "historique_par_defaut",
    "lister", "annuler", "enregistrer",
    "enregistrer_annulateur", "annulateurs_enregistres",
]

# ── Noms de sources (préfixe des identifiants d'entrée) ──────────────────
SOURCE_QUARANTAINE = "quarantaine"
SOURCE_SAS = "sas"
SOURCE_RANGEMENT = "rangement"
SOURCE_DEMARRAGE = "demarrage"
SOURCE_JOURNAL = "journal"

# Repli si `optimizer.startup_manager` ne peut pas être importé : la valeur
# est recopiée telle quelle depuis ce module, qui reste la référence.
_BACKUP_KEY_PATH_REPLI = r"Software\AntiZeevirius\DisabledStartupBackup"

# Garde-fou d'énumération du registre : une clé ne contient jamais autant de
# valeurs, mais un module winreg simulé mal écrit pourrait boucler sans fin.
_MAX_VALEURS_REGISTRE = 10000

# 1601-01-01 → 1970-01-01, en secondes (conversion FILETIME Windows).
_EPOCH_FILETIME = 11644473600


# ── Enveloppes de réponse ────────────────────────────────────────────────
# Même forme que gui/bridge.py (contrat figé). Volontairement redéfinies ici
# plutôt qu'importées : gui/bridge.py importe main.py et tout le moteur, ce
# qui ferait dépendre l'historique de l'interface. L'historique doit rester
# utilisable en console, dans un test, ou depuis un module V2 isolé.
def ok(data: Any = None) -> Dict:
    return {"ok": True, "data": data if data is not None else {}}


def err(message: str) -> Dict:
    return {"ok": False, "error": str(message), "unavailable": False}


def unavailable(reason: str) -> Dict:
    return {"ok": False, "unavailable": True, "reason": str(reason), "error": str(reason)}


# ── Utilitaires de lecture tolérante ─────────────────────────────────────
def _lire_json(chemin: Path) -> tuple:
    """Lit un index JSON sans jamais lever. Retourne (contenu, probleme).

    Distinction volontaire :
      - fichier ABSENT ou VIDE  → (None, None) : c'est l'état normal d'une
        installation neuve, pas une anomalie à signaler à l'utilisateur ;
      - fichier ILLISIBLE ou CORROMPU → (None, "…") : anomalie rapportée,
        car elle signifie qu'un historique existe mais n'est plus lisible.
    """
    try:
        if not chemin.exists():
            return None, None
        texte = chemin.read_text(encoding="utf-8")
    except OSError as exc:
        return None, f"index illisible ({type(exc).__name__}: {exc})"
    except Exception as exc:  # décodage exotique, chemin invalide…
        return None, f"index illisible ({type(exc).__name__}: {exc})"

    if not texte.strip():
        return None, None

    try:
        return json.loads(texte), None
    except (ValueError, UnicodeDecodeError) as exc:
        return None, f"index corrompu, contenu ignoré ({type(exc).__name__}: {exc})"


def _liste_de(contenu: Any) -> tuple:
    """Normalise un contenu d'index en liste de dictionnaires.

    Les quatre index existants sont des listes JSON. Un contenu d'un autre
    type (dict, chaîne, nombre) est un format inattendu : on le signale au
    lieu de planter. Les éléments non-dictionnaires sont comptés et ignorés.
    """
    if contenu is None:
        return [], None
    if not isinstance(contenu, list):
        return [], f"format inattendu : liste JSON attendue, {type(contenu).__name__} trouvé"
    entrees = [e for e in contenu if isinstance(e, dict)]
    ignorees = len(contenu) - len(entrees)
    probleme = f"{ignorees} entrée(s) ignorée(s) : format inattendu" if ignorees else None
    return entrees, probleme


def _horodatage(valeur: Any) -> Optional[str]:
    """Normalise une date d'index en chaîne ISO, ou None si inexploitable."""
    if isinstance(valeur, str) and valeur.strip():
        return valeur.strip()
    if isinstance(valeur, (int, float)) and valeur > 0:
        try:
            return datetime.fromtimestamp(float(valeur)).isoformat()
        except (OverflowError, OSError, ValueError):
            return None
    return None


def _instant(iso: Optional[str]) -> Optional[float]:
    """Convertit une date ISO en secondes epoch, pour le tri. None si illisible.

    Tolère le suffixe « Z » (UTC) que les modules V2 pourraient produire :
    aucun des quatre index actuels n'en écrit, mais accepter coûte deux lignes
    et évite qu'une entrée future se retrouve silencieusement en fin de liste.
    """
    if not iso:
        return None
    texte = iso.strip()
    if texte.endswith("Z"):
        texte = texte[:-1] + "+00:00"
    try:
        return datetime.fromisoformat(texte).timestamp()
    except (ValueError, OverflowError, OSError):
        return None


def _depuis_filetime(valeur: Any) -> Optional[str]:
    """Convertit un FILETIME Windows (100 ns depuis 1601) en date ISO."""
    try:
        secondes = float(valeur) / 10_000_000 - _EPOCH_FILETIME
    except (TypeError, ValueError):
        return None
    if secondes <= 0:
        return None
    try:
        return datetime.fromtimestamp(secondes).isoformat()
    except (OverflowError, OSError, ValueError):
        return None


def _nom(chemin: Any) -> str:
    """Nom de fichier lisible à partir d'un chemin, sans lever."""
    if not isinstance(chemin, str) or not chemin.strip():
        return "(chemin inconnu)"
    try:
        return Path(chemin).name or chemin
    except Exception:
        return chemin


def _existe(chemin: Path) -> bool:
    """`Path.exists()` qui ne lève pas (chemin trop long, droits, disque absent)."""
    try:
        return chemin.exists()
    except OSError:
        return False


def _entree(
    source: str,
    identifiant_natif: Any,
    type_action: str,
    description: str,
    horodatage: Optional[str],
    annulable: bool,
    raison_non_annulable: Optional[str] = None,
    details: Optional[Dict] = None,
) -> Dict:
    """Construit une entrée d'historique — la forme commune aux 4 mécanismes.

    `id` = "<source>:<identifiant natif>". Le préfixe dit à `annuler()` quel
    adaptateur appeler ; l'identifiant natif est celui du module d'origine et
    n'est jamais réinterprété (il peut contenir « : », d'où le split limité).
    """
    return {
        "id": f"{source}:{identifiant_natif}",
        "source": source,
        "id_natif": str(identifiant_natif),
        "type_action": type_action,
        "description": description,
        "horodatage": horodatage,
        "annulable": bool(annulable),
        "raison_non_annulable": None if annulable else (raison_non_annulable or "action non annulable"),
        "details": details or {},
    }


# ── Adaptateur : contrat commun ──────────────────────────────────────────
class Adaptateur:
    """Traduit l'index d'un mécanisme existant vers l'entrée commune.

    Deux méthodes seulement :
      collecter() → {"entrees": [...], "probleme": str|None, "indisponible": bool}
      annuler(id_natif, entree) → dict (enveloppe ok/err/unavailable)

    `collecter()` ne DOIT pas lever — mais `HistoriqueUnifie` l'entoure quand
    même d'un try/except : un adaptateur tiers mal écrit ne doit pas pouvoir
    faire tomber la vue entière.
    """

    source = "?"
    libelle = "?"

    def collecter(self) -> Dict:
        raise NotImplementedError

    def annuler(self, identifiant_natif: str, entree: Dict) -> Dict:
        raise NotImplementedError


# ── 1. Quarantaine ───────────────────────────────────────────────────────
class AdaptateurQuarantaine(Adaptateur):
    """quarantine/quarantine_manager.py — index `quarantine_index.json`.

    Format lu : liste d'objets {id, original_path, quarantined_name,
    quarantine_date, reason, detection_details, restored[, restore_date,
    restored_to]}.

    Particularité : une entrée restaurée RESTE dans l'index avec
    `restored: True` — la trace survit à l'annulation. C'est le seul des
    quatre mécanismes à le faire (le sas supprime la ligne, le rangement
    marque `undone`, le démarrage supprime la valeur de registre).
    """

    source = SOURCE_QUARANTAINE
    libelle = "Quarantaine"

    def __init__(self, dossier: Optional[str] = None, fabrique: Optional[Callable] = None):
        self._dossier = Path(dossier) if dossier else None
        self._fabrique = fabrique

    def dossier(self) -> Path:
        # Résolution PARESSEUSE : jamais mise en cache à l'import, pour que
        # l'emplacement des donnees (ANTIZEEVIRIUS_DATA_DIR, %LOCALAPPDATA%
        # une fois gelé) soit celui du moment de l'appel.
        return self._dossier if self._dossier is not None else Path(_paths.quarantine_dir())

    def chemin_index(self) -> Path:
        return self.dossier() / "quarantine_index.json"

    def collecter(self) -> Dict:
        contenu, probleme = _lire_json(self.chemin_index())
        if probleme:
            return {"entrees": [], "probleme": probleme}
        brutes, probleme = _liste_de(contenu)

        dossier = self.dossier()
        entrees = []
        for brute in brutes:
            identifiant = brute.get("id")
            if not isinstance(identifiant, str) or not identifiant:
                continue  # entrée inutilisable : sans id, rien n'est annulable
            nom_isole = brute.get("quarantined_name")
            restaure = bool(brute.get("restored"))
            fichier = dossier / nom_isole if isinstance(nom_isole, str) and nom_isole else None

            if restaure:
                annulable, raison = False, (
                    f"déjà restauré le {brute.get('restore_date')}"
                    if brute.get("restore_date") else "déjà restauré"
                )
            elif fichier is None:
                annulable, raison = False, "entrée incomplète : nom du fichier isolé absent de l'index"
            elif not _existe(fichier):
                annulable, raison = False, "fichier isolé introuvable dans la quarantaine (supprimé définitivement ?)"
            else:
                annulable, raison = True, None

            origine = brute.get("original_path")
            motif = brute.get("reason") or "motif non précisé"
            entrees.append(_entree(
                self.source, identifiant, "mise_en_quarantaine",
                f"Fichier mis en quarantaine : {_nom(origine)} — {motif}",
                _horodatage(brute.get("quarantine_date")),
                annulable, raison,
                {
                    "chemin_origine": origine,
                    "fichier_isole": str(fichier) if fichier else None,
                    "motif": motif,
                    "restaure": restaure,
                    "restaure_vers": brute.get("restored_to"),
                    "detection": brute.get("detection_details"),
                    "annulation": "quarantine_manager.restore_file()",
                },
            ))
        return {"entrees": entrees, "probleme": probleme}

    def annuler(self, identifiant_natif: str, entree: Dict) -> Dict:
        try:
            from quarantine.quarantine_manager import QuarantineManager
        except Exception as exc:
            return unavailable(f"quarantine_manager indisponible ({type(exc).__name__}: {exc})")

        fabrique = self._fabrique or (lambda: QuarantineManager(str(self.dossier())))
        gestionnaire = fabrique()
        if gestionnaire.restore_file(identifiant_natif):
            cible = entree.get("details", {}).get("chemin_origine")
            return ok({"message": f"Fichier restauré vers {cible}" if cible else "Fichier restauré"})
        return err("restauration refusée par quarantine_manager (fichier absent, droits, ou déjà restauré)")


# ── 2. Sas de tri ────────────────────────────────────────────────────────
class AdaptateurSas(Adaptateur):
    """optimizer/file_triage.py — index `staging_index.json`.

    Format lu : liste d'objets {id, original_path, staged_name, date, reason}.

    Particularité — et limite qu'il faut afficher honnêtement :
    `restore_from_staging()` SUPPRIME la ligne de l'index, et `purge_staging()`
    aussi. Une entrée récupérée ne laisse donc aucune trace : l'historique
    peut montrer ce qui attend dans le sas, jamais ce qui en est sorti. Une
    entrée toujours présente mais dont le fichier a disparu du sas est le seul
    résidu observable d'une purge manuelle.
    """

    source = SOURCE_SAS
    libelle = "Sas de tri"

    def __init__(self, dossier: Optional[str] = None, fabrique: Optional[Callable] = None):
        self._dossier = Path(dossier) if dossier else None
        self._fabrique = fabrique

    def dossier(self) -> Path:
        return self._dossier if self._dossier is not None else Path(_paths.staging_dir())

    def chemin_index(self) -> Path:
        return self.dossier() / "staging_index.json"

    def collecter(self) -> Dict:
        contenu, probleme = _lire_json(self.chemin_index())
        if probleme:
            return {"entrees": [], "probleme": probleme}
        brutes, probleme = _liste_de(contenu)

        dossier = self.dossier()
        entrees = []
        for brute in brutes:
            identifiant = brute.get("id")
            if not isinstance(identifiant, str) or not identifiant:
                continue
            nom_stocke = brute.get("staged_name")
            fichier = dossier / nom_stocke if isinstance(nom_stocke, str) and nom_stocke else None

            if fichier is None:
                annulable, raison = False, "entrée incomplète : nom du fichier mis de côté absent de l'index"
            elif not _existe(fichier):
                annulable, raison = False, "fichier absent du sas (déjà récupéré, purgé, ou supprimé à la main)"
            else:
                annulable, raison = True, None

            origine = brute.get("original_path")
            motif = brute.get("reason") or "motif non précisé"
            entrees.append(_entree(
                self.source, identifiant, "mise_de_cote",
                f"Fichier mis de côté : {_nom(origine)} — {motif}",
                _horodatage(brute.get("date")),
                annulable, raison,
                {
                    "chemin_origine": origine,
                    "fichier_sas": str(fichier) if fichier else None,
                    "motif": motif,
                    "annulation": "file_triage.restore_from_staging()",
                },
            ))
        return {"entrees": entrees, "probleme": probleme}

    def annuler(self, identifiant_natif: str, entree: Dict) -> Dict:
        try:
            from optimizer.file_triage import FileTriage
        except Exception as exc:
            return unavailable(f"file_triage indisponible ({type(exc).__name__}: {exc})")

        fabrique = self._fabrique or (lambda: FileTriage(str(self.dossier())))
        triage = fabrique()
        if triage.restore_from_staging(identifiant_natif):
            cible = entree.get("details", {}).get("chemin_origine")
            return ok({"message": f"Fichier récupéré vers {cible}" if cible else "Fichier récupéré"})
        return err("récupération refusée par file_triage (fichier absent du sas, ou droits insuffisants)")


# ── 3. Sessions de réorganisation ────────────────────────────────────────
class AdaptateurRangement(Adaptateur):
    """optimizer/folder_organizer.py — journal `organizer_logs/reorg_index.json`.

    Format lu : liste de DÉPLACEMENTS {id, session_id, original_path,
    new_path, type, date, undone}.

    Particularité : l'unité d'annulation n'est pas le déplacement mais la
    SESSION (`undo_session`). L'adaptateur regroupe donc les déplacements par
    `session_id` et produit une entrée par session, datée du premier
    déplacement — c'est la seule des quatre sources dont une entrée
    d'historique recouvre plusieurs opérations.
    """

    source = SOURCE_RANGEMENT
    libelle = "Rangement de dossiers"

    def __init__(self, journal: Optional[str] = None, fabrique: Optional[Callable] = None):
        self._journal = Path(journal) if journal else None
        self._fabrique = fabrique

    def chemin_index(self) -> Path:
        return self._journal if self._journal is not None else Path(_paths.organizer_log())

    def collecter(self) -> Dict:
        contenu, probleme = _lire_json(self.chemin_index())
        if probleme:
            return {"entrees": [], "probleme": probleme}
        brutes, probleme = _liste_de(contenu)

        sessions: Dict[str, List[Dict]] = {}
        orphelins = 0
        for brute in brutes:
            sid = brute.get("session_id")
            if not isinstance(sid, str) or not sid:
                orphelins += 1
                continue
            sessions.setdefault(sid, []).append(brute)

        if orphelins:
            supplement = f"{orphelins} déplacement(s) sans session_id ignoré(s)"
            probleme = f"{probleme} ; {supplement}" if probleme else supplement

        entrees = []
        for sid, deplacements in sessions.items():
            restants = [d for d in deplacements if not d.get("undone")]
            dates = [i for i in (_instant(_horodatage(d.get("date"))) for d in deplacements) if i is not None]
            debut = min(dates) if dates else None
            horodatage = datetime.fromtimestamp(debut).isoformat() if debut is not None else None

            introuvables = [
                d for d in restants
                if isinstance(d.get("new_path"), str) and not _existe(Path(d["new_path"]))
            ]

            if not restants:
                annulable, raison = False, "session déjà annulée (tous les déplacements ont été remis en place)"
            else:
                annulable, raison = True, None

            depuis = ""
            premier = deplacements[0].get("original_path")
            if isinstance(premier, str) and premier:
                try:
                    depuis = f" depuis {Path(premier).parent}"
                except Exception:
                    depuis = ""

            description = f"Rangement de {len(deplacements)} élément(s){depuis}"
            if restants and len(restants) != len(deplacements):
                description += f" — {len(deplacements) - len(restants)} déjà remis en place"
            if introuvables:
                description += f" — attention : {len(introuvables)} élément(s) introuvable(s) à leur nouvel emplacement"

            entrees.append(_entree(
                self.source, sid, "rangement",
                description, horodatage, annulable, raison,
                {
                    "deplacements": len(deplacements),
                    "restants": len(restants),
                    "introuvables": len(introuvables),
                    "annulation": "folder_organizer.undo_session()",
                },
            ))
        return {"entrees": entrees, "probleme": probleme}

    def annuler(self, identifiant_natif: str, entree: Dict) -> Dict:
        try:
            from optimizer.folder_organizer import FolderOrganizer
        except Exception as exc:
            return unavailable(f"folder_organizer indisponible ({type(exc).__name__}: {exc})")

        fabrique = self._fabrique or (lambda: FolderOrganizer(str(self.chemin_index())))
        organiseur = fabrique()
        resultat = organiseur.undo_session(identifiant_natif) or {}
        remis = resultat.get("restored", 0)
        erreurs = list(resultat.get("errors") or [])

        if not remis and erreurs:
            return err(f"annulation du rangement échouée : {'; '.join(str(e) for e in erreurs[:3])}")
        if not remis:
            return err("aucun déplacement à annuler dans cette session")
        # Nuance honnête : folder_organizer marque un déplacement « annulé »
        # même quand le fichier a disparu de son nouvel emplacement. Le compte
        # rendu dit donc « traité », et remonte les erreurs éventuelles.
        return ok({
            "message": f"{remis} déplacement(s) traité(s)"
                       + (f", {len(erreurs)} en erreur" if erreurs else ""),
            "restaures": remis,
            "erreurs": erreurs,
        })


# ── 4. Démarrage Windows ─────────────────────────────────────────────────
class AdaptateurDemarrage(Adaptateur):
    """optimizer/startup_manager.py — sauvegarde dans le REGISTRE, pas en JSON.

    Il n'existe aucun index sur disque : `disable_registry_item()` recopie la
    valeur dans HKCU\\Software\\AntiZeevirius\\DisabledStartupBackup sous le
    nom « HIVE|Nom », puis la retire de la clé Run. L'adaptateur énumère donc
    cette clé de sauvegarde.

    Deux limites qui viennent du mécanisme d'origine, pas d'ici :
      - le registre ne date pas ses VALEURS. La seule date disponible est
        celle de dernière écriture de la CLÉ : elle est donc identique pour
        toutes les entrées et ne vaut que pour la plus récente. Elle est
        marquée `horodatage_approximatif` et ne doit pas être présentée comme
        la date de désactivation de chaque programme.
      - les raccourcis du dossier Démarrage (`disable_startup_folder_item`,
        renommage en .disabled) ne laissent AUCUNE trace : ni index, ni
        sauvegarde registre. Ils sont invisibles ici — voir le rapport.
    """

    source = SOURCE_DEMARRAGE
    libelle = "Démarrage Windows"

    def __init__(self, winreg_module: Any = None, fabrique: Optional[Callable] = None,
                 chemin_cle: Optional[str] = None):
        self._winreg = winreg_module
        self._fabrique = fabrique
        self._chemin_cle = chemin_cle

    def _module_registre(self):
        if self._winreg is not None:
            return self._winreg
        try:
            import winreg  # noqa: F401  (absent hors Windows, c'est prévu)
            return winreg
        except ImportError:
            return None

    def chemin_cle(self) -> str:
        if self._chemin_cle:
            return self._chemin_cle
        try:
            from optimizer.startup_manager import BACKUP_KEY_PATH
            return BACKUP_KEY_PATH
        except Exception:
            return _BACKUP_KEY_PATH_REPLI

    def collecter(self) -> Dict:
        registre = self._module_registre()
        if registre is None:
            return {
                "entrees": [],
                "probleme": "registre Windows indisponible sur cette plateforme (module winreg absent)",
                "indisponible": True,
            }

        chemin = self.chemin_cle()
        try:
            cle = registre.OpenKey(registre.HKEY_CURRENT_USER, chemin, 0, registre.KEY_READ)
        except FileNotFoundError:
            # Clé absente = aucun programme n'a jamais été désactivé. État
            # normal, pas une anomalie.
            return {"entrees": [], "probleme": None}
        except OSError as exc:
            return {"entrees": [], "probleme": f"clé de sauvegarde illisible ({type(exc).__name__}: {exc})"}

        entrees: List[Dict] = []
        probleme = None
        try:
            date_cle = None
            try:
                infos = registre.QueryInfoKey(cle)
                date_cle = _depuis_filetime(infos[2]) if len(infos) > 2 else None
            except Exception:
                date_cle = None

            index = 0
            while index < _MAX_VALEURS_REGISTRE:
                try:
                    nom_valeur, valeur, _type = registre.EnumValue(cle, index)
                except OSError:
                    break
                except Exception as exc:
                    probleme = f"énumération interrompue ({type(exc).__name__}: {exc})"
                    break
                index += 1

                if not isinstance(nom_valeur, str) or not nom_valeur:
                    continue
                if "|" in nom_valeur:
                    ruche, nom = nom_valeur.split("|", 1)
                    annulable, raison = True, None
                else:
                    # Format inattendu : on l'affiche quand même (l'utilisateur
                    # doit savoir que la sauvegarde existe) mais on refuse de
                    # deviner la ruche pour restore_registry_item().
                    ruche, nom = "?", nom_valeur
                    annulable, raison = False, (
                        "nom de sauvegarde inattendu : ruche indéterminable, "
                        "restauration manuelle via l'éditeur du registre"
                    )

                entrees.append(_entree(
                    self.source, nom_valeur, "demarrage_desactive",
                    f"Programme désactivé au démarrage : {nom} ({ruche})",
                    date_cle, annulable, raison,
                    {
                        "ruche": ruche,
                        "nom": nom,
                        "commande": valeur if isinstance(valeur, str) else None,
                        "cle_sauvegarde": chemin,
                        "horodatage_approximatif": bool(date_cle),
                        "annulation": "startup_manager.restore_registry_item()",
                    },
                ))
        finally:
            try:
                registre.CloseKey(cle)
            except Exception:
                pass

        return {"entrees": entrees, "probleme": probleme}

    def annuler(self, identifiant_natif: str, entree: Dict) -> Dict:
        try:
            from optimizer.startup_manager import StartupManager
        except Exception as exc:
            return unavailable(f"startup_manager indisponible ({type(exc).__name__}: {exc})")

        details = entree.get("details", {})
        ruche = details.get("ruche")
        nom = details.get("nom")
        if not nom or ruche in (None, "?"):
            return err("entrée de démarrage incomplète : ruche ou nom manquant")

        fabrique = self._fabrique or StartupManager
        gestionnaire = fabrique()
        if gestionnaire.restore_registry_item(ruche, nom):
            return ok({"message": f"« {nom} » réactivé au démarrage ({ruche})"})
        return err("réactivation refusée par startup_manager (sauvegarde absente ou droits insuffisants)")


# ── 5. Journal propre à l'historique (API des modules V2) ────────────────
# Registre des fonctions d'annulation. Un module V2 (mode incident, pare-feu,
# services Windows) s'inscrit une fois à l'import :
#
#     history.enregistrer_annulateur("pare_feu", _retirer_regle)
#
# puis journalise chacune de ses actions :
#
#     history.enregistrer("regle_pare_feu", "Sortie bloquée pour jeu.exe",
#                         annulation={"gestionnaire": "pare_feu",
#                                     "parametres": {"regle": "AZ_jeu"}})
#
# Le journal ne stocke QUE le nom du gestionnaire et ses paramètres — jamais
# une fonction. Un JSON ne peut pas contenir de code, et surtout : un
# historique qui exécuterait du code lu depuis un fichier serait une porte
# d'entrée, pas une fonctionnalité.
_GESTIONNAIRES: Dict[str, Callable] = {}
_VERROU_GESTIONNAIRES = threading.Lock()


def enregistrer_annulateur(nom: str, fonction: Callable) -> None:
    """Déclare la fonction qui sait annuler les actions journalisées sous `nom`.

    Elle reçoit le dictionnaire `parametres` de l'entrée et retourne soit un
    booléen, soit une enveloppe {"ok": ...}. Toute exception qu'elle lève est
    interceptée et transformée en réponse d'erreur.
    """
    if not isinstance(nom, str) or not nom:
        raise ValueError("nom de gestionnaire invalide")
    if not callable(fonction):
        raise ValueError("gestionnaire non appelable")
    with _VERROU_GESTIONNAIRES:
        _GESTIONNAIRES[nom] = fonction


def annulateurs_enregistres() -> List[str]:
    with _VERROU_GESTIONNAIRES:
        return sorted(_GESTIONNAIRES)


class AdaptateurJournal(Adaptateur):
    """Journal propre à l'historique : la porte d'entrée des modules V2.

    Les quatre mécanismes existants ont chacun leur index et n'ont pas à en
    changer. Les modules V2, eux, n'ont pas d'index du tout : ils écrivent
    ici, avec `enregistrer()`.

    Écriture atomique (fichier temporaire + os.replace), même convention que
    QuarantineManager et FileTriage : un journal tronqué par une coupure de
    courant serait précisément le fichier qu'on ne peut pas se permettre de
    perdre.
    """

    source = SOURCE_JOURNAL
    libelle = "Actions journalisées"

    def __init__(self, chemin: Optional[str] = None):
        self._chemin = Path(chemin) if chemin else None
        self._verrou = threading.Lock()

    def chemin_index(self) -> Path:
        return self._chemin if self._chemin is not None else Path(_paths.data_path("history", "journal.json"))

    # -- écriture ---------------------------------------------------------
    def _ecrire(self, entrees: List[Dict]) -> Optional[str]:
        chemin = self.chemin_index()
        try:
            chemin.parent.mkdir(parents=True, exist_ok=True)
            temporaire = chemin.with_suffix(".json.tmp")
            temporaire.write_text(json.dumps(entrees, indent=2, ensure_ascii=False), encoding="utf-8")
            os.replace(temporaire, chemin)
            return None
        except OSError as exc:
            return f"écriture du journal impossible ({type(exc).__name__}: {exc})"

    def _brutes(self) -> tuple:
        contenu, probleme = _lire_json(self.chemin_index())
        if probleme:
            return [], probleme
        return _liste_de(contenu)

    def enregistrer(
        self,
        type_action: str,
        description: str,
        details: Optional[Dict] = None,
        annulation: Optional[Dict] = None,
        horodatage: Optional[str] = None,
    ) -> Dict:
        if not isinstance(type_action, str) or not type_action.strip():
            return err("type_action manquant")
        if not isinstance(description, str) or not description.strip():
            return err("description manquante")

        if annulation is not None:
            if not isinstance(annulation, dict) or not annulation.get("gestionnaire"):
                return err("annulation invalide : {'gestionnaire': str, 'parametres': dict} attendu")

        entree = {
            "id": str(uuid.uuid4()),
            "type_action": type_action.strip(),
            "description": description.strip(),
            "horodatage": _horodatage(horodatage) or datetime.now().isoformat(),
            "details": details if isinstance(details, dict) else {},
            "annulation": annulation,
            "annulee": False,
            "date_annulation": None,
        }

        with self._verrou:
            # Un journal illisible ne bloque pas l'enregistrement de la suite,
            # mais on ne l'écrase pas non plus en silence : le fichier abîmé
            # est laissé tel quel et le problème est remonté à l'appelant.
            brutes, probleme = self._brutes()
            if probleme:
                return err(f"journal non écrit : {probleme}")
            brutes.append(entree)
            echec = self._ecrire(brutes)
        if echec:
            return err(echec)
        return ok({"id": f"{self.source}:{entree['id']}", "id_natif": entree["id"],
                   "horodatage": entree["horodatage"]})

    def _marquer_annulee(self, identifiant: str) -> Optional[str]:
        with self._verrou:
            brutes, probleme = self._brutes()
            if probleme:
                return probleme
            trouvee = False
            for brute in brutes:
                if brute.get("id") == identifiant:
                    brute["annulee"] = True
                    brute["date_annulation"] = datetime.now().isoformat()
                    trouvee = True
            if not trouvee:
                return "entrée absente du journal au moment de l'écriture"
            return self._ecrire(brutes)

    # -- lecture ----------------------------------------------------------
    def collecter(self) -> Dict:
        brutes, probleme = self._brutes()
        connus = set(annulateurs_enregistres())

        entrees = []
        for brute in brutes:
            identifiant = brute.get("id")
            if not isinstance(identifiant, str) or not identifiant:
                continue
            annulation = brute.get("annulation")
            gestionnaire = annulation.get("gestionnaire") if isinstance(annulation, dict) else None

            if brute.get("annulee"):
                annulable, raison = False, (
                    f"déjà annulée le {brute.get('date_annulation')}"
                    if brute.get("date_annulation") else "déjà annulée"
                )
            elif not gestionnaire:
                annulable, raison = False, "action non réversible (aucune annulation déclarée par le module)"
            elif gestionnaire not in connus:
                annulable, raison = False, (
                    f"aucun gestionnaire d'annulation enregistré pour « {gestionnaire} » "
                    f"(module non chargé ?)"
                )
            else:
                annulable, raison = True, None

            entrees.append(_entree(
                self.source, identifiant,
                brute.get("type_action") or "action",
                brute.get("description") or "(sans description)",
                _horodatage(brute.get("horodatage")),
                annulable, raison,
                {
                    **(brute.get("details") if isinstance(brute.get("details"), dict) else {}),
                    "gestionnaire": gestionnaire,
                    "annulee": bool(brute.get("annulee")),
                },
            ))
        return {"entrees": entrees, "probleme": probleme}

    def annuler(self, identifiant_natif: str, entree: Dict) -> Dict:
        brutes, probleme = self._brutes()
        if probleme:
            return err(f"journal illisible : {probleme}")
        brute = next((b for b in brutes if b.get("id") == identifiant_natif), None)
        if brute is None:
            return err("entrée absente du journal")

        annulation = brute.get("annulation") or {}
        nom = annulation.get("gestionnaire")
        parametres = annulation.get("parametres")
        with _VERROU_GESTIONNAIRES:
            fonction = _GESTIONNAIRES.get(nom)
        if fonction is None:
            return unavailable(f"aucun gestionnaire d'annulation enregistré pour « {nom} »")

        try:
            resultat = fonction(parametres if isinstance(parametres, dict) else {})
        except Exception as exc:
            return err(f"gestionnaire « {nom} » en échec ({type(exc).__name__}: {exc})")

        if isinstance(resultat, dict):
            reussi = bool(resultat.get("ok"))
            message = resultat.get("error") or resultat.get("reason") or ""
        else:
            reussi = bool(resultat)
            message = ""

        if not reussi:
            return err(message or f"le gestionnaire « {nom} » a refusé l'annulation")

        echec = self._marquer_annulee(identifiant_natif)
        donnees = {"message": f"Action annulée par « {nom} »", "gestionnaire": nom}
        if isinstance(resultat, dict) and isinstance(resultat.get("data"), dict):
            donnees["resultat"] = resultat["data"]
        if echec:
            # L'action EST annulée ; seule la trace n'a pas pu être mise à
            # jour. On le dit franchement plutôt que d'annoncer un échec qui
            # pousserait l'utilisateur à réessayer une annulation déjà faite.
            donnees["avertissement"] = f"annulation effectuée mais journal non mis à jour : {echec}"
        return ok(donnees)


# ── Filtres ──────────────────────────────────────────────────────────────
def _en_ensemble(valeur: Any) -> Optional[set]:
    if valeur is None:
        return None
    if isinstance(valeur, str):
        return {valeur}
    if isinstance(valeur, (list, tuple, set, frozenset)):
        return {str(v) for v in valeur}
    return {str(valeur)}


def _filtrer(entrees: List[Dict], filtre: Any) -> tuple:
    """Applique `filtre` aux entrées. Retourne (entrées, probleme).

    Formes acceptées, de la plus simple à la plus complète :
      None                      → tout
      "quarantaine"             → cette source (ou ce type d'action)
      ["quarantaine", "sas"]    → ces sources
      {"source": …, "type_action": …, "annulable": bool,
       "depuis": iso, "jusqu_a": iso, "texte": "facture"}
      callable(entree) -> bool  → prédicat libre

    Un filtre incompréhensible est IGNORÉ (la vue reste complète) et signalé
    dans `problemes` : mieux vaut trop d'entrées qu'une vue silencieusement
    vide, qui laisserait croire qu'il n'y a rien à annuler.
    """
    if filtre is None:
        return entrees, None

    if callable(filtre):
        gardees = []
        for e in entrees:
            try:
                if filtre(e):
                    gardees.append(e)
            except Exception as exc:
                return entrees, f"filtre en échec ({type(exc).__name__}: {exc}), filtre ignoré"
        return gardees, None

    if isinstance(filtre, (str, list, tuple, set, frozenset)):
        voulus = _en_ensemble(filtre) or set()
        return [e for e in entrees
                if e.get("source") in voulus or e.get("type_action") in voulus], None

    if not isinstance(filtre, dict):
        return entrees, f"filtre de type {type(filtre).__name__} non reconnu, filtre ignoré"

    sources = _en_ensemble(filtre.get("source"))
    types = _en_ensemble(filtre.get("type_action"))
    annulable = filtre.get("annulable")
    depuis = _instant(_horodatage(filtre.get("depuis")))
    jusqu_a = _instant(_horodatage(filtre.get("jusqu_a")))
    texte = filtre.get("texte")
    texte = texte.lower() if isinstance(texte, str) and texte.strip() else None

    gardees = []
    for e in entrees:
        if sources is not None and e.get("source") not in sources:
            continue
        if types is not None and e.get("type_action") not in types:
            continue
        if annulable is not None and bool(e.get("annulable")) != bool(annulable):
            continue
        if depuis is not None or jusqu_a is not None:
            instant = _instant(e.get("horodatage"))
            # Une entrée sans date exploitable ne peut pas prouver qu'elle
            # est dans la fenêtre demandée : on l'exclut du filtre daté.
            if instant is None:
                continue
            if depuis is not None and instant < depuis:
                continue
            if jusqu_a is not None and instant > jusqu_a:
                continue
        if texte is not None:
            corpus = " ".join(str(v) for v in (
                e.get("description"), e.get("type_action"), e.get("source"),
                json.dumps(e.get("details", {}), ensure_ascii=False),
            )).lower()
            if texte not in corpus:
                continue
        gardees.append(e)
    return gardees, None


# ── Vue unifiée ──────────────────────────────────────────────────────────
class HistoriqueUnifie:
    """Une seule vue chronologique de tout ce que l'application a fait.

    `adaptateurs` permet d'injecter des sources (tests, module V2 qui apporte
    la sienne). Un adaptateur de journal est toujours présent : `enregistrer()`
    doit fonctionner quelle que soit la configuration.
    """

    def __init__(self, adaptateurs: Optional[Iterable[Adaptateur]] = None,
                 journal: Optional[str] = None):
        if adaptateurs is None:
            self.adaptateurs: List[Adaptateur] = [
                AdaptateurQuarantaine(),
                AdaptateurSas(),
                AdaptateurRangement(),
                AdaptateurDemarrage(),
                AdaptateurJournal(journal),
            ]
        else:
            self.adaptateurs = list(adaptateurs)
            if not any(isinstance(a, AdaptateurJournal) for a in self.adaptateurs):
                self.adaptateurs.append(AdaptateurJournal(journal))

    # -- interne ----------------------------------------------------------
    def _journal(self) -> AdaptateurJournal:
        for adaptateur in self.adaptateurs:
            if isinstance(adaptateur, AdaptateurJournal):
                return adaptateur
        journal = AdaptateurJournal()
        self.adaptateurs.append(journal)
        return journal

    def _collecter(self, adaptateur: Adaptateur) -> tuple:
        """Interroge un adaptateur SANS jamais laisser passer d'exception."""
        try:
            resultat = adaptateur.collecter() or {}
        except Exception as exc:
            return [], {
                "source": getattr(adaptateur, "source", "?"),
                "libelle": getattr(adaptateur, "libelle", "?"),
                "message": f"adaptateur en échec ({type(exc).__name__}: {exc})",
                "indisponible": False,
            }
        entrees = resultat.get("entrees")
        entrees = [e for e in entrees if isinstance(e, dict)] if isinstance(entrees, list) else []
        probleme = resultat.get("probleme")
        if probleme:
            return entrees, {
                "source": getattr(adaptateur, "source", "?"),
                "libelle": getattr(adaptateur, "libelle", "?"),
                "message": str(probleme),
                "indisponible": bool(resultat.get("indisponible")),
            }
        return entrees, None

    def _adaptateur(self, source: str) -> Optional[Adaptateur]:
        for adaptateur in self.adaptateurs:
            if getattr(adaptateur, "source", None) == source:
                return adaptateur
        return None

    # -- API --------------------------------------------------------------
    def lister(self, limite: Optional[int] = 50, filtre: Any = None) -> Dict:
        """Vue chronologique, de la plus récente à la plus ancienne.

        Ne lève jamais. Les sources en panne sont listées dans
        `data["problemes"]` pendant que les autres répondent normalement.
        """
        entrees: List[Dict] = []
        problemes: List[Dict] = []
        for adaptateur in self.adaptateurs:
            lot, probleme = self._collecter(adaptateur)
            entrees.extend(lot)
            if probleme:
                problemes.append(probleme)

        entrees, probleme_filtre = _filtrer(entrees, filtre)
        if probleme_filtre:
            problemes.append({"source": "filtre", "libelle": "Filtre",
                              "message": probleme_filtre, "indisponible": False})

        # Tri décroissant. Les entrées sans date exploitable (le démarrage
        # Windows quand le registre ne donne rien) se rangent en fin de liste
        # au lieu de prétendre dater de 1970.
        entrees.sort(key=lambda e: (0, 0.0) if _instant(e.get("horodatage")) is None
                     else (1, _instant(e.get("horodatage"))), reverse=True)

        total = len(entrees)
        if isinstance(limite, int) and limite >= 0:
            entrees = entrees[:limite]

        return ok({
            "entrees": entrees,
            "total": total,
            "affichees": len(entrees),
            "annulables": sum(1 for e in entrees if e.get("annulable")),
            "problemes": problemes,
            "sources": [
                {"source": getattr(a, "source", "?"), "libelle": getattr(a, "libelle", "?")}
                for a in self.adaptateurs
            ],
        })

    def annuler(self, entry_id: str) -> Dict:
        """Annule une entrée en déléguant au module d'origine.

        L'état est TOUJOURS relu juste avant d'agir : entre l'affichage de la
        liste et le clic, le fichier a pu être restauré ailleurs, purgé, ou
        supprimé. On ne délègue jamais sur la foi d'un affichage périmé.
        """
        if not isinstance(entry_id, str) or ":" not in entry_id:
            return err("identifiant d'entrée invalide : forme « source:identifiant » attendue")

        source, identifiant_natif = entry_id.split(":", 1)
        if not identifiant_natif:
            return err("identifiant d'entrée invalide : identifiant natif vide")

        adaptateur = self._adaptateur(source)
        if adaptateur is None:
            connues = ", ".join(sorted(getattr(a, "source", "?") for a in self.adaptateurs))
            return err(f"source inconnue « {source} » (sources disponibles : {connues})")

        entrees, probleme = self._collecter(adaptateur)
        entree = next((e for e in entrees if e.get("id") == entry_id), None)
        if entree is None:
            if probleme and probleme.get("indisponible"):
                return unavailable(probleme["message"])
            if probleme:
                return err(f"entrée introuvable — {probleme['message']}")
            return err("entrée introuvable : elle a pu être purgée ou déjà traitée depuis l'affichage")

        if not entree.get("annulable"):
            return err(entree.get("raison_non_annulable") or "action non annulable")

        try:
            resultat = adaptateur.annuler(identifiant_natif, entree) or err("aucun résultat")
        except Exception as exc:
            return err(f"annulation impossible ({type(exc).__name__}: {exc})")

        if not isinstance(resultat, dict) or "ok" not in resultat:
            return err("réponse d'annulation invalide de l'adaptateur")

        # Compte rendu uniforme, quel que soit le mécanisme sous-jacent.
        if resultat.get("ok"):
            donnees = dict(resultat.get("data") or {})
            donnees.update({
                "id": entry_id,
                "source": source,
                "type_action": entree.get("type_action"),
                "description": entree.get("description"),
            })
            return ok(donnees)
        return resultat

    def enregistrer(self, type_action: str, description: str,
                    details: Optional[Dict] = None,
                    annulation: Optional[Dict] = None,
                    horodatage: Optional[str] = None) -> Dict:
        """Inscrit une action d'un module V2 dans l'historique.

        `annulation` décrit COMMENT annuler, sans embarquer de code :
            {"gestionnaire": "mode_incident", "parametres": {...}}
        Le gestionnaire doit avoir été déclaré via `enregistrer_annulateur()`.
        Sans `annulation`, l'action est tracée mais affichée non réversible.
        """
        return self._journal().enregistrer(type_action, description, details, annulation, horodatage)


# ── Accès direct (confort d'appel pour l'interface et le CLI) ────────────
_INSTANCE: Optional[HistoriqueUnifie] = None
_VERROU_INSTANCE = threading.Lock()


def historique_par_defaut() -> HistoriqueUnifie:
    """Instance partagée, construite au premier appel (jamais à l'import :
    les chemins de `paths.py` doivent être résolus au moment de l'usage)."""
    global _INSTANCE
    with _VERROU_INSTANCE:
        if _INSTANCE is None:
            _INSTANCE = HistoriqueUnifie()
        return _INSTANCE


def lister(limite: Optional[int] = 50, filtre: Any = None) -> Dict:
    return historique_par_defaut().lister(limite, filtre)


def annuler(entry_id: str) -> Dict:
    return historique_par_defaut().annuler(entry_id)


def enregistrer(type_action: str, description: str, details: Optional[Dict] = None,
                annulation: Optional[Dict] = None, horodatage: Optional[str] = None) -> Dict:
    return historique_par_defaut().enregistrer(type_action, description, details,
                                               annulation, horodatage)
