"""
digest.py — ce qui s'est passé pendant que vous ne regardiez pas.

Un antivirus passe l'essentiel de son temps à ne rien signaler, et c'est
heureux. Le revers est qu'on finit par ne plus savoir s'il travaille. Le
résumé répond à cette question une fois par jour, en une page : voilà les
faits, voilà les alertes, voilà ce que je propose.

Le module n'ouvre aucune surveillance et ne relance aucune analyse : il
**agrège ce qui existe déjà**. Un résumé qui déclencherait un scan complet
chaque matin serait une charge périodique déguisée en information, et
l'utilisateur la désactiverait au bout d'une semaine.

`sources` est volontairement un dictionnaire souple — chaque clé est
facultative, et chaque entrée peut être un objet du projet (`Notification`,
`Echantillon`, `Suggestion`) ou le dictionnaire équivalent. Les appelants
(interface web, mode autonome, ligne de commande) n'ont pas tous les mêmes
objets sous la main, et exiger une conversion préalable ne ferait que
déplacer le travail :

    notifications  itérable de Notification ou de dicts équivalents
    telemetrie     itérable d'Echantillon ou de dicts équivalents
    suggestions    itérable de Suggestion ou de dicts équivalents
    decisions      journal rendu par `Memoire.decisions()`
    evenements     faits libres : {"horodatage", "libelle", "categorie"}

Tout ce qui tombe hors de la période est écarté sans bruit : un résumé
quotidien qui ressortirait l'alerte d'avant-hier ferait douter de la date de
tout le reste.
"""

from __future__ import annotations

import datetime
from typing import Any, Dict, List, Mapping, Optional

__all__ = ["construire_resume", "GRAVITES_ALERTE"]


# Ce qui mérite d'apparaître en tête du résumé plutôt que dans le corps.
GRAVITES_ALERTE = ("alerte", "critique")


def _champ(source: Any, nom: str, defaut: Any = None) -> Any:
    """Lit un champ, que la source soit un dataclass ou un dictionnaire."""
    if isinstance(source, Mapping):
        return source.get(nom, defaut)
    return getattr(source, nom, defaut)


def _horodatage(source: Any) -> Optional[float]:
    """Retrouve la date d'un enregistrement, quel que soit son nom de champ.

    `Memoire.journaliser()` n'impose pas le nom de sa clé de date ; accepter
    les variantes courantes évite qu'un résumé perde silencieusement tout le
    journal des décisions à cause d'un mot.
    """
    for nom in ("horodatage", "date", "ts", "timestamp"):
        valeur = _champ(source, nom)
        if valeur is None:
            continue
        try:
            return float(valeur)
        except (TypeError, ValueError):
            continue
    return None


def _iterer(sources: Mapping, cle: str) -> List[Any]:
    valeur = sources.get(cle) if isinstance(sources, Mapping) else None
    if valeur is None or isinstance(valeur, (str, bytes, Mapping)):
        return []
    try:
        return list(valeur)
    except TypeError:
        return []


def _dans_periode(quand: Optional[float], depuis: float, jusqu_a: float) -> bool:
    # Un enregistrement sans date est écarté : le dater d'office reviendrait
    # à affirmer quelque chose qu'on ne sait pas.
    return quand is not None and depuis <= quand <= jusqu_a


def _iso(horodatage: float) -> str:
    try:
        return datetime.datetime.fromtimestamp(horodatage).isoformat(
            timespec="seconds")
    except (OverflowError, OSError, ValueError):
        return ""


def _fait(categorie: str, libelle: str, horodatage: Optional[float] = None,
          detail: Optional[Dict] = None) -> Dict:
    return {"categorie": categorie, "libelle": libelle,
            "horodatage": horodatage, "detail": detail or {}}


def construire_resume(depuis: float, jusqu_a: float, sources: dict) -> dict:
    """Agrège la période en un rapport lisible, sans rien déclencher.

    Rend `{"periode", "faits", "alertes", "suggestions"}`. Les faits sont
    triés du plus ancien au plus récent (un récit se lit dans l'ordre), les
    alertes du plus récent au plus ancien (on veut d'abord savoir ce qui
    brûle), les suggestions par urgence décroissante.
    """
    depuis = float(depuis)
    jusqu_a = float(jusqu_a)
    if depuis > jusqu_a:
        # Une période à l'envers est une erreur d'appel ; rendre un rapport
        # vide laisserait croire qu'il ne s'est rien passé, ce qui est le
        # mensonge le plus coûteux qu'un résumé puisse produire.
        depuis, jusqu_a = jusqu_a, depuis

    sources = sources if isinstance(sources, Mapping) else {}
    faits: List[Dict] = []
    alertes: List[Dict] = []

    # ── Notifications ──────────────────────────────────────────────────────
    for n in _iterer(sources, "notifications"):
        quand = _horodatage(n)
        if not _dans_periode(quand, depuis, jusqu_a):
            continue
        entree = {
            "identifiant": _champ(n, "identifiant", ""),
            "horodatage": quand,
            "gravite": _champ(n, "gravite", "info"),
            "titre": _champ(n, "titre", ""),
            "corps": _champ(n, "corps", ""),
            "source": _champ(n, "source", ""),
            "acquittee": bool(_champ(n, "acquittee", False)),
        }
        if entree["gravite"] in GRAVITES_ALERTE:
            alertes.append(entree)
        else:
            faits.append(_fait("notification", entree["titre"], quand,
                               {"source": entree["source"],
                                "corps": entree["corps"]}))

    # ── Charge de la machine ───────────────────────────────────────────────
    echantillons = [e for e in _iterer(sources, "telemetrie")
                    if _dans_periode(_horodatage(e), depuis, jusqu_a)]
    if echantillons:
        faits.append(_synthese_telemetrie(echantillons))

    # ── Journal des décisions ──────────────────────────────────────────────
    for d in _iterer(sources, "decisions"):
        quand = _horodatage(d)
        if not _dans_periode(quand, depuis, jusqu_a):
            continue
        libelle = str(_champ(d, "evenement", "") or _champ(d, "event", "")
                      or "décision")
        detail = _champ(d, "detail", {})
        faits.append(_fait("decision", libelle, quand,
                           detail if isinstance(detail, Mapping) else {}))

    # ── Faits libres fournis par l'appelant ────────────────────────────────
    for e in _iterer(sources, "evenements"):
        quand = _horodatage(e)
        if not _dans_periode(quand, depuis, jusqu_a):
            continue
        faits.append(_fait(str(_champ(e, "categorie", "evenement")),
                           str(_champ(e, "libelle", "")), quand,
                           _champ(e, "detail", {}) or {}))

    # ── Suggestions ────────────────────────────────────────────────────────
    suggestions = []
    for s in _iterer(sources, "suggestions"):
        try:
            urgence = int(_champ(s, "urgence", 0))
        except (TypeError, ValueError):
            urgence = 0
        suggestions.append({"capacite": str(_champ(s, "capacite", "")),
                            "motif": str(_champ(s, "motif", "")),
                            "urgence": urgence})

    # Tris totaux : deux résumés construits sur les mêmes données doivent être
    # identiques au caractère près, sinon on ne peut pas les comparer d'un
    # jour sur l'autre.
    faits.sort(key=lambda f: (f["horodatage"] or 0.0, f["categorie"],
                              f["libelle"]))
    alertes.sort(key=lambda a: (-(a["horodatage"] or 0.0), a["titre"],
                                a["identifiant"]))
    suggestions.sort(key=lambda s: (-s["urgence"], s["capacite"], s["motif"]))

    return {
        "periode": {
            "depuis": depuis, "jusqu_a": jusqu_a,
            "depuis_iso": _iso(depuis), "jusqu_a_iso": _iso(jusqu_a),
            "duree_heures": round((jusqu_a - depuis) / 3600.0, 2),
        },
        "faits": faits,
        "alertes": alertes,
        "suggestions": suggestions,
    }


def _synthese_telemetrie(echantillons: List[Any]) -> Dict:
    """Résume la charge en une phrase : moyenne, et surtout pic.

    La moyenne seule est trompeuse — un chiffrement de rançongiciel d'un quart
    d'heure disparaît dans une moyenne sur vingt-quatre heures. C'est le
    maximum qui porte l'information.
    """
    def serie(nom: str) -> List[float]:
        valeurs = []
        for e in echantillons:
            try:
                valeurs.append(float(_champ(e, nom, 0.0)))
            except (TypeError, ValueError):
                continue
        return valeurs or [0.0]

    cpu, memoire, disque = serie("cpu"), serie("memoire"), serie("disque")
    detail = {
        "echantillons": len(echantillons),
        "cpu_moyen": round(sum(cpu) / len(cpu), 1),
        "cpu_max": round(max(cpu), 1),
        "memoire_moyenne": round(sum(memoire) / len(memoire), 1),
        "memoire_max": round(max(memoire), 1),
        "disque_moyen": round(sum(disque) / len(disque), 1),
        "disque_max": round(max(disque), 1),
    }
    libelle = (f"charge moyenne : processeur {detail['cpu_moyen']:.0f} %, "
               f"mémoire {detail['memoire_moyenne']:.0f} %, "
               f"disque {detail['disque_moyen']:.0f} % "
               f"(pic processeur {detail['cpu_max']:.0f} %)")
    horodatages = [h for h in (_horodatage(e) for e in echantillons)
                   if h is not None]
    return _fait("telemetrie", libelle,
                 max(horodatages) if horodatages else None, detail)
