# La Triarchie — contrat gelé

Le propriétaire a décidé : ANTI-ZEEVIRIUS est gouverné par **trois** instances
qui délibèrent, et non par une voix unique qui annonce.

| | Étymologie | Son temps | Ce qu'elle apporte à la délibération |
|---|---|---|---|
| **PROMÉTHÉE** | *pro-mētheus*, « celui qui pense avant » | l'AVANT | propose. Détecte, alerte, anticipe. |
| **JUANITA JCV** | — | le PENDANT | exécute et surveille. Accompagne l'opération, l'interrompt si elle dérive. |
| **ÉPIMÉTHÉE** | *epi-mētheus*, « celui qui pense après » | l'APRÈS | vérifie et conteste. Dresse le bilan, et peut dire que c'était une erreur. |

L'opposition Prométhée / Épiméthée n'est pas décorative : dans le mythe, l'un
prévoit et l'autre constate les dégâts. C'est exactement la tension d'un
antivirus, et c'est pourquoi les deux doivent exister séparément. Une instance
qui déciderait ET jugerait son propre résultat ne se contredirait jamais.

---

## Les deux règles qui rendent la triarchie saine plutôt que dangereuse

Ces règles priment sur toute considération d'élégance ou de confort. Elles
existent parce qu'une délibération entre agents automatiques peut très
facilement devenir une machine à fabriquer du consentement.

### Règle 1 — L'HUMAIN RESTE L'ARBITRE. La triarchie ne le remplace jamais.

Trois instances d'accord **ne valent pas** l'accord de l'utilisateur. La
doctrine du projet est inchangée : **rien de destructif sans confirmation
explicite.** La triarchie se place **avant** l'humain, comme un filtre qui
réduit ce qui lui est proposé — jamais **à sa place**.

Concrètement : l'unanimité de la triarchie ne déclenche AUCUNE suppression. Elle
produit une proposition mieux étayée, présentée à l'utilisateur avec les trois
avis. Le cycle `dry_run` → plan affiché → `confirm_token` de
`gui/bridge.py::_guarded()` reste la seule porte d'exécution.

Un consensus de trois agents qui autoriserait une suppression serait une
régression de sécurité déguisée en fonctionnalité. C'est précisément le piège de
cette architecture.

### Règle 2 — Une triarchie INCOMPLÈTE ne décide pas.

L'utilisateur peut désactiver une instance. Dans ce cas, **on ne passe pas à une
majorité de deux** : la délibération est déclarée incomplète et l'affaire remonte
directement à l'humain.

Raison : avec trois membres, une majorité de deux suffit. Si l'on accepte de
délibérer à deux, une majorité devient un seul avis — la contradiction disparaît
et le quorum n'a plus de sens. Un quorum qui s'adapte à l'absence n'est pas un
quorum.

---

## L'avis — `assistant/triarchie.py`

```python
class Position(enum.Enum):
    POUR       = "pour"        # j'approuve cette action
    CONTRE     = "contre"      # je m'y oppose, et je dis pourquoi
    ABSTENTION = "abstention"  # hors de ma compétence

@dataclasses.dataclass(frozen=True)
class Avis:
    instance: str        # "promethee" | "juanita" | "epimethee"
    position: Position
    motif: str           # UNE PHRASE, obligatoire et non vide, même pour une abstention
    confiance: float     # 0.0 à 1.0
```

`motif` non vide est une contrainte de code, pas une convention : un avis sans
motif est un vote à l'aveugle, et il rend la délibération inauditable.

```python
@dataclasses.dataclass(frozen=True)
class Deliberation:
    capacite: str
    avis: tuple[Avis, ...]           # TRIÉS par instance — déterminisme
    verdict: str                     # unanime | majorite | partage | incomplete
    exige_humain: bool               # VRAI dès que le risque dépasse LECTURE
    resume: str                      # les trois motifs, lisibles par l'utilisateur
```

```python
def deliberer(capacite: Capacite, etat: dict, instances_actives: frozenset[str]) -> Deliberation
```

**Fonction PURE** : même capacité, même état, mêmes instances → même
délibération, dans le même ordre. Elle ne lit pas le disque, n'exécute rien, ne
fait aucun appel réseau. C'est ce qui la rend testable et auditable.

### Ce qu'aucune délibération ne peut faire

- `exige_humain` est **VRAI** dès que `Capacite.risque` dépasse `Risque.LECTURE`,
  quel que soit le verdict. Ce champ n'est jamais calculé à partir des avis.
- Un verdict ne peut ni produire ni contenir un `confirm_token`.
- `verdict == "incomplete"` dès que `len(instances_actives) < 3`.

---

## Les critères propres à chaque instance

Ils doivent DIVERGER. Trois instances qui raisonnent pareil votent pareil, et la
triarchie devient un théâtre.

- **PROMÉTHÉE** juge le **risque encouru si l'on n'agit pas**. Biais assumé vers
  l'action : c'est la voix de la prévention.
- **JUANITA JCV** juge la **faisabilité et le coût** : droits disponibles,
  charge machine, opération déjà en cours, réversibilité effective. Biais assumé
  vers la prudence opérationnelle.
- **ÉPIMÉTHÉE** juge à partir du **passé** : cette action a-t-elle déjà échoué,
  a-t-elle déjà été annulée par l'utilisateur, ce fichier a-t-il déjà été
  restauré depuis la quarantaine ? Biais assumé vers la contestation. Il lit
  `comfort/history.py` et le journal de `assistant/memory.py`.

Ce dernier point est celui qui apporte le plus : **une action que l'utilisateur
a déjà annulée trois fois ne devrait plus être proposée de la même façon.**
Aucun antivirus grand public ne fait ça.

---

## Ce qui ne change pas

Les trois instances partagent **une seule file de parole** : deux voix ne
parlent jamais en même temps. PROMÉTHÉE peut interrompre ÉPIMÉTHÉE sur une
gravité critique, jamais l'inverse. Le bouton SILENCE est global et coupe les
trois. La rédaction des données personnelles et les heures de silence sont des
règles de la maison, pas des préférences par instance.

Voir `docs/PROMETHEE.md` pour le moteur de parole.
