"""
assistant/ — la couche qui décide, par-dessus les couches qui font.

ANTI-ZEEVIRIUS savait déjà analyser, nettoyer, ranger et surveiller ; il ne
savait pas *décider* d'en faire quelque chose. Cette couche lui donne une
mémoire, un jugement du risque, une compréhension du langage ordinaire et,
plus tard, une capacité d'initiative.

Elle n'exécute rien elle-même. Elle décrit ce que le produit sait faire
(`registry`), à quel point c'est dangereux (`risk`), ce que l'utilisateur
vient de demander (`intent`), et ce qu'on a retenu de lui (`memory`).
L'exécution reste la propriété exclusive de `gui/bridge.py` et de son
`_guarded()` : dupliquer cette porte ici aurait créé un second chemin vers
la suppression de fichiers, c'est-à-dire une seconde occasion de se tromper.

Aucun import automatique de sous-module ici, et c'est délibéré : plusieurs
modules de cette couche (télémétrie, notifications, autonomie) arrivent par
chantiers séparés, et un paquet qui les importe tous à l'ouverture échouerait
tant que le dernier n'est pas écrit. On importe explicitement ce dont on a
besoin — `from assistant.registry import construire_registre_par_defaut`.
"""

__all__ = []
