"""
risk.py — combien coûte une erreur.

Tout le reste de la couche assistant se règle sur cette échelle : ce que le
mode autonome a le droit de lancer seul, ce que l'interface doit faire
confirmer, ce qu'une phrase mal comprise ne doit surtout pas déclencher. Elle
ne mesure donc pas la « gravité » d'une action au sens vague du mot, mais une
seule chose, précise : **ce qu'il en coûte de se tromper**.

D'où l'ordre choisi — croissant, et volontairement comparable avec `<` et
`>=`. Un `IntEnum` plutôt qu'un `Enum` parce que la question posée au code
appelant est presque toujours « est-ce au moins aussi grave que… ? ».

Ce que cette échelle ne dit PAS, et il faut le savoir en la lisant :

  * elle ne mesure pas la confidentialité. Une capacité qui envoie une
    empreinte à un service tiers n'écrit rien sur le disque et reste donc en
    `LECTURE` — voir le commentaire de `reputation.verifier` dans
    `registry.py`, qui traite le cas.
  * elle ne remplace pas `_guarded()` de `gui/bridge.py`. Le contrat est
    formel : cette couche *décrit* la porte de confirmation, elle ne la
    duplique pas et ne la contourne jamais. `exige_confirmation()` sert à
    prévenir l'utilisateur et à brider le mode autonome, jamais à autoriser
    quoi que ce soit.
"""

from __future__ import annotations

import enum

__all__ = ["Risque", "exige_confirmation", "libelle"]


class Risque(enum.IntEnum):
    """Échelle de conséquence, du sans-regret à l'irréparable."""

    LECTURE = 0       # n'écrit rien : analyse, inventaire, lecture de journal
    REVERSIBLE = 1    # écrit, mais annulable par l'Historique (quarantaine, rangement)
    DESTRUCTIF = 2    # perte de données possible (purge quarantaine, nettoyage)
    IRREVERSIBLE = 3  # ni annulable ni reconstructible (suppression définitive)


# Libellés destinés à l'utilisateur. Ils sont ici et pas dans l'interface pour
# que la phrase affichée par la modale web, par le résumé quotidien et par la
# justification d'une intention soit rigoureusement la même : un même niveau
# décrit de trois façons différentes donne l'impression de trois règles.
_LIBELLES = {
    Risque.LECTURE: "lecture seule",
    Risque.REVERSIBLE: "réversible",
    Risque.DESTRUCTIF: "destructif",
    Risque.IRREVERSIBLE: "irréversible",
}


def _niveau(valeur):
    """Rend le `Risque` correspondant, ou None si la valeur n'en est pas un.

    Porte d'entrée UNIQUE de l'échelle, et c'est le point important : tant que
    `libelle()` et `exige_confirmation()` interprétaient chacune leur argument,
    elles pouvaient décrire le même objet différemment — « réversible » à
    l'écran à côté d'un « on demande confirmation » dans le code, ou
    l'inverse. Un seul lecteur, une seule réponse.

    Trois refus, et chacun a coûté quelque chose avant d'être écrit :

    * **Les booléens.** `True` vaut 1 en Python, donc `REVERSIBLE` : un
      drapeau passé par erreur à la place d'un niveau serait exécutable sans
      confirmation.
    * **Les valeurs non entières.** `int(1.9)` vaut 1 : la troncature arrondit
      le risque VERS LE BAS, c'est-à-dire du côté permissif. Un `1.9` — quoi
      qu'il veuille dire — ne doit pas devenir « réversible, allez-y ».
    * **Les valeurs hors échelle.** `-1` se glissait sous `LECTURE` et
      ressortait comme « aucune confirmation nécessaire », alors que la
      docstring promettait exactement le contraire. Au-dessus de
      `IRREVERSIBLE`, la comparaison `>=` sauvait la mise par accident ; en
      dessous, rien ne la sauvait.

    Le seul élargissement toléré est le flottant exactement entier (`2.0`),
    parce que le JSON de l'interface web ne distingue pas `2` de `2.0`.
    """
    if isinstance(valeur, Risque):
        return valeur
    if isinstance(valeur, bool):
        return None
    try:
        entier = int(valeur)
    except (TypeError, ValueError, OverflowError):
        return None
    # `entier != valeur` élimine « 2 » (chaîne) et 2.5 (troncature), tout en
    # laissant passer 2 et 2.0.
    if entier != valeur:
        return None
    try:
        return Risque(entier)
    except ValueError:
        return None


def libelle(niveau) -> str:
    """Nom français du niveau, ou « inconnu » pour tout le reste.

    Ne lève pas : un libellé manquant ne doit pas faire échouer l'affichage
    d'un rapport par ailleurs correct.
    """
    reel = _niveau(niveau)
    return _LIBELLES[reel] if reel is not None else "inconnu"


def exige_confirmation(niveau: Risque, mode_autonome: bool) -> bool:
    """Faut-il un « oui » explicite de l'utilisateur avant d'agir ?

    Hors mode autonome, le seuil est `DESTRUCTIF` : l'utilisateur a cliqué,
    il sait ce qu'il demande, et l'Historique rattrape le reste.

    En mode autonome le seuil descend à `REVERSIBLE`, et ce décalage est le
    cœur de la doctrine : une machine qui agit de sa propre initiative n'a pas
    le droit de toucher au disque sans qu'un humain ait dit oui au moins une
    fois. « Réversible » suppose que quelqu'un s'aperçoive qu'il faut annuler
    — ce qui n'est vrai que si quelqu'un a vu passer l'action.

    Tout ce qui n'est pas un niveau de l'échelle retourne True — voir
    `_niveau()` pour la liste et la raison de chaque refus. Face à l'inconnu,
    on demande : l'erreur inverse, supposer inoffensif ce qu'on ne sait pas
    lire, est exactement celle qui supprime un fichier.
    """
    reel = _niveau(niveau)
    if reel is None:
        return True
    seuil = Risque.REVERSIBLE if mode_autonome else Risque.DESTRUCTIF
    return int(reel) >= int(seuil)
