"""
comfort/ — couche « confort et automatisation » de la V2.

Contient les modules qui ne protègent ni n'optimisent, mais qui rendent
l'outil utilisable : historique unifié, mode Zen, règles « si… alors… »,
Grand Ménage, assistant vocal.

Ce paquet ne fait aucun import lourd au chargement : chaque module est
importé explicitement par celui qui en a besoin (`from comfort.history
import HistoriqueUnifie`), pour qu'un module indisponible sur une
plateforme n'empêche pas les autres de se charger.
"""
