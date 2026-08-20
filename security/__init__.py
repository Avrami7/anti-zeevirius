"""
security/ — couche défensive avancée (V2).

Voir docs/CONCEPTION-V2.md §2. Chaque module de cette couche respecte le
contrat déjà en place dans le projet :

1. plan d'abord (fonction de préparation en lecture seule), action ensuite ;
2. tout ce qui est posé peut être retiré ;
3. un module indisponible répond {"ok": False, "unavailable": True,
   "reason": "..."} au lieu de lever une exception.

Ce paquet n'importe rien au chargement : chaque module se prend
individuellement (`from security.incident_mode import IncidentMode`), pour
qu'une dépendance manquante dans l'un n'empêche pas les autres de servir.
"""

__all__ = ["incident_mode"]
