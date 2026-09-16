"""
memory.py — ce que l'assistant retient entre deux lancements.

Un fichier JSON unique, sous `paths.data_path("assistant", "memoire.json")`.
Trois raisons à ce choix minimal, dans l'ordre d'importance :

1. **Jamais dans le dossier d'installation.** Une fois l'application posée
   dans `C:\\Program Files`, ce dossier est en lecture seule pour un processus
   non élevé — et la virtualisation UAC peut rediriger silencieusement
   l'écriture vers `VirtualStore`, où l'utilisateur ne retrouvera jamais rien.
   `paths.data_path()` règle la question une fois pour toutes ; aucun chemin
   n'est écrit en dur ici.

2. **Un seul fichier, pas une base.** Le contenu tient en quelques kilo-octets
   et se relit dans un éditeur de texte. Un utilisateur qui se demande ce que
   son antivirus a retenu de lui doit pouvoir ouvrir le fichier et le lire.

3. **Rien de confidentiel dedans.** Un prénom, une langue, des préférences,
   un journal de décisions. Pas de mot de passe, pas de chemin scanné, pas de
   contenu de fichier — on ne garde pas ce dont on n'a pas besoin.

Deux propriétés non négociables, testées :

  * **Écriture atomique.** Fichier temporaire dans le MÊME dossier (os.replace
    n'est atomique qu'à l'intérieur d'un système de fichiers), puis
    remplacement. Une coupure de courant pendant l'écriture laisse l'ancienne
    mémoire intacte, jamais une moitié des deux.
  * **Une mémoire illisible ne bloque rien.** Le fichier est mis de côté sous
    `.corrompu` et remplacé par un neuf. Un assistant qui refuse de démarrer
    parce que son carnet de notes est abîmé serait un antivirus qui ne scanne
    plus : la mémoire est un confort, l'analyse est la mission.

Corollaire du même raisonnement, pour l'écriture cette fois : si le disque
refuse l'écriture (plein, verrouillé, droits), l'état reste correct EN MÉMOIRE
et l'appel ne lève pas. On perd le souvenir, on ne perd pas la session.
"""

from __future__ import annotations

import dataclasses
import json
import os
import tempfile
import threading
import time
from pathlib import Path
from typing import Any, Dict, List, Optional, Union

import paths as _paths

__all__ = ["Profil", "Memoire", "JOURNAL_MAX"]

# Le journal est borné : c'est un aide-mémoire pour l'assistant et un droit de
# regard pour l'utilisateur, pas un journal d'audit — `comfort/history.py`
# tient ce rôle-là. Mille entrées, c'est plusieurs mois d'usage normal, et le
# fichier reste lisible d'un coup d'œil.
JOURNAL_MAX = 1000

_VERSION = 1


@dataclasses.dataclass
class Profil:
    """Qui parle à qui. Volontairement pauvre.

    `nom_utilisateur` vide signifie « on ne sait pas » et doit le rester tant
    que l'utilisateur ne s'est pas nommé : inventer un prénom, ou aller le
    chercher dans les variables d'environnement Windows, donnerait à
    l'assistant l'air d'en savoir plus qu'il n'en sait.
    """

    nom_assistant: str = "ANTI-ZEEVIRIUS"
    nom_utilisateur: str = ""
    langue: str = "fr"


class Memoire:
    """Profil, préférences et journal de décisions, persistés en JSON."""

    def __init__(self, chemin: Optional[Union[str, Path]] = None) -> None:
        # Le chemin explicite sert aux tests et à une éventuelle mémoire
        # séparée ; sinon il est résolu PARESSEUSEMENT, à chaque accès, pour
        # que `ANTIZEEVIRIUS_DATA_DIR` posé après la construction soit
        # respecté. Le figer ici rendrait l'objet insensible à un changement
        # d'emplacement, ce qui est précisément le piège que `paths.py` évite.
        self._chemin_explicite = Path(chemin) if chemin is not None else None
        self._verrou = threading.RLock()
        self._etat: Optional[Dict[str, Any]] = None
        # D'OÙ vient l'état en cache, et pas seulement où il ira. Le chemin
        # était résolu paresseusement alors que l'état, lui, restait celui du
        # premier fichier lu : un changement d'emplacement entre deux appels
        # (clé USB branchée, `ANTIZEEVIRIUS_DATA_DIR` déplacé, test qui isole
        # les données) faisait écrire l'ancien contenu PAR-DESSUS le nouveau
        # fichier, sans l'avoir lu ni mis de côté. Profil et préférences de
        # la destination disparaissaient sans un mot — ni exception, ni
        # `.corrompu`, ni trace. Retenir la provenance suffit à invalider le
        # cache au bon moment.
        self._chemin_charge: Optional[Path] = None

    # ── Emplacement ────────────────────────────────────────────────────────
    @property
    def chemin(self) -> Path:
        if self._chemin_explicite is not None:
            return self._chemin_explicite
        return Path(_paths.data_path("assistant", "memoire.json"))

    # ── Lecture / écriture du fichier ──────────────────────────────────────
    def _neuf(self) -> Dict[str, Any]:
        return {
            "version": _VERSION,
            "profil": dataclasses.asdict(Profil()),
            "preferences": {},
            "journal": [],
        }

    def _mettre_de_cote(self, chemin: Path) -> None:
        """Renomme une mémoire illisible en `.corrompu` au lieu de l'effacer.

        On ne détruit pas ce qu'on ne comprend pas : le fichier contient
        peut-être des préférences récupérables à la main, et son existence
        explique à l'utilisateur pourquoi l'assistant l'a oublié.
        """
        cible = chemin.with_name(chemin.name + ".corrompu")
        try:
            # `os.replace` et non `rename` : une deuxième corruption doit
            # écraser la première, sinon l'appel échoue sous Windows et on
            # repart pour un tour de mémoire illisible à chaque démarrage.
            os.replace(chemin, cible)
        except OSError:
            # Même le rangement peut échouer (fichier verrouillé). On repart
            # d'une mémoire neuve en RAM ; la prochaine écriture écrasera.
            pass

    def _charger(self) -> Dict[str, Any]:
        with self._verrou:
            chemin = self.chemin
            # Le cache ne vaut QUE pour le fichier dont il provient. Si
            # l'emplacement a changé depuis, on le jette et on relit : la
            # fusion doit porter sur le contenu réellement présent à la
            # destination, sans quoi la première écriture l'effacerait.
            if self._etat is not None and self._chemin_charge == chemin:
                return self._etat
            self._etat = None
            etat = self._neuf()
            try:
                brut = chemin.read_text(encoding="utf-8")
            except FileNotFoundError:
                self._etat, self._chemin_charge = etat, chemin
                return etat
            except (OSError, UnicodeDecodeError):
                # Illisible pour une autre raison (droits, octets invalides) :
                # même traitement que le JSON cassé, pour la même raison.
                self._mettre_de_cote(chemin)
                self._etat, self._chemin_charge = etat, chemin
                return etat

            try:
                contenu = json.loads(brut)
            except (json.JSONDecodeError, ValueError):
                contenu = None

            if not isinstance(contenu, dict):
                self._mettre_de_cote(chemin)
                self._etat, self._chemin_charge = etat, chemin
                return etat

            # Fusion tolérante : un fichier écrit par une version plus ancienne
            # (ou amputé d'une clé à la main) ne doit pas faire perdre le reste.
            profil_lu = contenu.get("profil")
            if isinstance(profil_lu, dict):
                for champ in etat["profil"]:
                    if champ in profil_lu:
                        etat["profil"][champ] = _texte(profil_lu[champ], etat["profil"][champ])
            prefs = contenu.get("preferences")
            if isinstance(prefs, dict):
                etat["preferences"] = dict(prefs)
            journal = contenu.get("journal")
            if isinstance(journal, list):
                etat["journal"] = [e for e in journal if isinstance(e, dict)][-JOURNAL_MAX:]

            self._etat, self._chemin_charge = etat, chemin
            return etat

    def _ecrire(self) -> bool:
        """Écrit l'état courant de façon atomique. Rend False en cas d'échec.

        Le temporaire est créé dans le dossier de destination : `os.replace`
        n'est atomique qu'au sein d'un même système de fichiers, et `/tmp` peut
        très bien être ailleurs. `flush` + `fsync` avant le renommage, sinon
        « atomique » ne veut dire que « atomique pour le nom du fichier » et
        l'on remplace l'ancienne mémoire par un fichier encore vide.
        """
        with self._verrou:
            etat = self._charger()
            chemin = self.chemin
            temporaire = None
            try:
                chemin.parent.mkdir(parents=True, exist_ok=True)
                fd, temporaire = tempfile.mkstemp(
                    dir=str(chemin.parent), prefix=chemin.name + ".", suffix=".tmp")
                with os.fdopen(fd, "w", encoding="utf-8") as f:
                    json.dump(etat, f, ensure_ascii=False, indent=2, sort_keys=True)
                    f.flush()
                    os.fsync(f.fileno())
                os.replace(temporaire, chemin)
                return True
            except (OSError, TypeError, ValueError):
                if temporaire:
                    try:
                        os.unlink(temporaire)
                    except OSError:
                        pass
                return False

    def recharger(self) -> None:
        """Oublie l'état en cache et relira le fichier au prochain accès.

        Utile quand le fichier a changé sous nos pieds — un autre processus,
        ou un test qui déplace `ANTIZEEVIRIUS_DATA_DIR`.
        """
        with self._verrou:
            self._etat = None
            self._chemin_charge = None

    # ── Profil ─────────────────────────────────────────────────────────────
    def profil(self) -> Profil:
        """Rend une COPIE : modifier l'objet rendu ne doit rien changer sur le
        disque, sinon la seule porte d'écriture cesse d'être `definir_profil`."""
        return Profil(**self._charger()["profil"])

    def definir_profil(self, **champs) -> Profil:
        """Modifie un ou plusieurs champs du profil et rend le profil à jour.

        Un champ inconnu lève `ValueError` plutôt que d'être ignoré : une
        faute de frappe silencieusement avalée donnerait un profil qui ne
        change jamais, et le bogue serait cherché ailleurs. Les préférences
        libres, elles, ont leur propre porte : `definir_preference`.
        """
        if not champs:
            return self.profil()
        connus = {f.name for f in dataclasses.fields(Profil)}
        inconnus = sorted(set(champs) - connus)
        if inconnus:
            raise ValueError(
                "champ de profil inconnu : " + ", ".join(inconnus)
                + " (attendus : " + ", ".join(sorted(connus)) + ")"
            )
        with self._verrou:
            etat = self._charger()
            for cle, valeur in sorted(champs.items()):
                etat["profil"][cle] = _texte(valeur, etat["profil"][cle])
            self._ecrire()
            return self.profil()

    # ── Préférences ────────────────────────────────────────────────────────
    def preference(self, cle: str, defaut=None):
        return self._charger()["preferences"].get(str(cle), defaut)

    def definir_preference(self, cle: str, valeur) -> None:
        """Enregistre une préférence quelconque, du moment qu'elle est JSON.

        La sérialisabilité est vérifiée AVANT d'accepter la valeur : un objet
        non sérialisable accepté ici ferait échouer toutes les écritures
        suivantes, et l'erreur se manifesterait très loin de sa cause.
        """
        cle = str(cle)
        if not cle.strip():
            raise ValueError("clé de préférence vide")
        try:
            json.dumps(valeur, ensure_ascii=False)
        except (TypeError, ValueError):
            raise ValueError(
                f"valeur non enregistrable pour « {cle} » : {type(valeur).__name__} "
                "n'est pas représentable en JSON"
            ) from None
        with self._verrou:
            self._charger()["preferences"][cle] = valeur
            self._ecrire()

    # ── Journal de décisions ───────────────────────────────────────────────
    def journaliser(self, evenement: str, detail: dict) -> None:
        """Consigne un fait daté. C'est la matière première des suggestions.

        Les entrées sont conservées dans l'ordre chronologique (la plus
        ancienne d'abord) : c'est l'ordre naturel d'un journal, et `decisions()`
        l'inverse à la lecture. Au-delà de `JOURNAL_MAX`, on coupe par le
        début — une mémoire qui grossit indéfiniment finit par coûter plus
        cher à relire qu'elle ne rapporte.

        Le nom de l'événement doit être une CHAÎNE, et le refus est franc.
        `str(evenement)` écrivait tranquillement « None », « 0 » ou « [] »
        dans la mémoire quand l'appelant s'était trompé d'argument : le
        journal est relu par `digest.construire_resume()` et présenté à
        l'utilisateur comme un fait constaté, si bien qu'un bogue d'appelant
        devenait une ligne de rapport persistée, à l'endroit exact où on
        cherche à comprendre ce que l'assistant a fait. `detail` est déjà
        validé strictement juste en dessous — les deux arguments méritent la
        même rigueur.
        """
        if not isinstance(evenement, str):
            raise ValueError(
                f"evenement doit être une chaîne, reçu {type(evenement).__name__}")
        evenement = evenement.strip()
        if not evenement:
            raise ValueError("événement vide")
        if detail is None:
            detail = {}
        if not isinstance(detail, dict):
            raise ValueError(f"detail doit être un dictionnaire, reçu {type(detail).__name__}")
        try:
            json.dumps(detail, ensure_ascii=False)
        except (TypeError, ValueError):
            raise ValueError("detail contient une valeur non représentable en JSON") from None

        entree = {"horodatage": time.time(), "evenement": evenement, "detail": dict(detail)}
        with self._verrou:
            etat = self._charger()
            etat["journal"].append(entree)
            if len(etat["journal"]) > JOURNAL_MAX:
                del etat["journal"][:-JOURNAL_MAX]
            self._ecrire()

    def decisions(self, limite: int = 50) -> List[dict]:
        """Les entrées les plus récentes d'abord.

        Rend des copies : le journal en mémoire ne doit pas pouvoir être
        réécrit par un appelant qui croit manipuler son propre résultat.
        """
        try:
            limite = int(limite)
        except (TypeError, ValueError):
            limite = 50
        if limite <= 0:
            return []
        journal = self._charger()["journal"]
        return [dict(e) for e in reversed(journal[-limite:])]


def _texte(valeur, defaut: str) -> str:
    """Normalise un champ de profil en texte.

    None retombe sur la valeur par défaut : effacer `nom_utilisateur` avec
    None doit vouloir dire « je ne sais plus », pas écrire la chaîne "None"
    dans la mémoire et saluer l'utilisateur par ce nom.
    """
    if valeur is None:
        return defaut
    return str(valeur)
