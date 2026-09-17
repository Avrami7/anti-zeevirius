# ADR-001 — La Triarchie : épreuve avant construction

**Statut :** acceptée, avec un correctif à passer avant d'écrire les tests.
**Méthode :** épreuve empirique du module déjà écrit, pas revue sur papier.
**Mesures :** 9 882 délibérations sur les 61 capacités réelles du produit,
croisées avec 162 états plausibles (menaces actives, remplissage disque,
ancienneté du dernier scan, annulations passées, opération en cours).

---

## La question posée

Le produit dispose déjà d'un garde-fou : le cycle `dry_run` → plan affiché →
`confirm_token` de `gui/bridge.py::_guarded()`. **La Triarchie apporte-t-elle
quelque chose, ou est-ce 977 lignes de complexité pour rien ?**

C'est la question qu'il fallait trancher avant de lancer les tests, pas après.

---

## Ce que l'épreuve confirme

### La règle 1 tient sur la totalité du registre réel

| Risque | Capacités | Sans exigence humaine |
|---|---|---|
| LECTURE | 28 | 28 |
| REVERSIBLE | 18 | **0** |
| DESTRUCTIF | 11 | **0** |
| IRREVERSIBLE | 4 | **0** |

**Aucune fuite.** Les 45,9 % de délibérations où `exige_humain` est faux sont
exactement les capacités en lecture seule — là où décider seul est sans
conséquence. Vérifié sur les 61 capacités, pas sur un échantillon choisi.

### Ce n'est pas du théâtre

**69,4 %** des délibérations comportent au moins un désaccord entre les trois
instances. Verdicts : majorité 53,3 %, unanimité 30,6 %, partage 16,1 %.

Trois instances qui raisonneraient pareil voteraient pareil — c'était le risque
principal de cette architecture. Les critères divergent réellement.

### Le coût est négligeable

**73 µs** par délibération. Sans effet mesurable, même sur une action fréquente.

### La mémoire d'Épiméthée fonctionne

C'est ce qui justifie son existence, et l'épreuve le confirme :

| Annulations passées | Position | Confiance |
|---|---|---|
| 0 | abstention | 0,25 |
| 1 | contre | 0,60 |
| 3 | contre | 0,95 |
| passé illisible | abstention | 0,20 |

Une action annulée trois fois n'est plus proposée à l'identique. Et l'abstention
quand il n'y a aucun précédent est une vraie abstention, pas un vote par défaut.

### Les invariants sont tenus par le code, pas par la convention

Refusés **à la construction** : un avis publié dans une délibération incomplète,
un motif vide ou blanc, et tout texte contenant `confirm_token`.

---

## Le défaut trouvé — à corriger avant les tests

**Une instance qui lève une exception fait échouer tout l'appel.**

Mesuré : en remplaçant `_JURY["promethee"]` par une fonction qui lève,
`deliberer()` propage le `RuntimeError`.

C'est incohérent avec la règle 2 du contrat. Cette règle dit qu'une triarchie
incomplète ne décide pas — elle remonte à l'humain. Une instance en panne est
exactement une triarchie incomplète : elle devrait donner `verdict =
"incomplete"`, pas casser l'appelant.

Conséquence concrète si on n'y touche pas : **un défaut dans la couche
consultative peut bloquer une action de protection.** Dans un antivirus, c'est
le mauvais sens de défaillance. La couche qui conseille ne doit jamais pouvoir
empêcher la couche qui protège.

Correctif : capturer l'exception par instance, traiter l'instance fautive comme
inactive, et laisser la règle 2 produire `incomplete`. Journaliser la panne —
une instance muette ne doit jamais passer pour une abstention, sinon on ne
distingue plus « je n'ai pas d'avis » de « je suis cassée ».

**Ce correctif passe AVANT l'écriture des tests.** Écrire les tests d'abord
figerait le défaut — c'est exactement le mode d'échec consigné dans `AGENTS.md`,
où un correctif de sécurité avait cassé la désinstallation et où son propre test
validait le comportement cassé.

---

## La réponse honnête à la question posée

**La Triarchie n'apporte AUCUN pouvoir de blocage.** Par construction, 0 % des
actions conséquentes peuvent être autorisées ou refusées par elle seule. Le
garde-fou reste entièrement `_guarded()`.

Son apport est **informationnel**, et il est réel :

1. **Moins de mauvaises propositions** atteignent l'utilisateur.
2. **Trois motifs explicités** : lire les trois suffit à comprendre pourquoi ils
   divergent. C'est de l'aide à la décision, pas de la décision.
3. **La mémoire des refus** : ne pas reproposer ce qui a déjà été refusé. Aucun
   antivirus grand public ne fait ça.

Il faut donc l'assumer pour ce qu'elle est : **977 lignes d'aide à la décision,
pas de sécurité.** Quiconque la présenterait comme un garde-fou supplémentaire
se tromperait, et c'est précisément la confusion qui transformerait cette
fonctionnalité en régression.

## Ce qu'il faudrait mesurer ensuite, sur une machine réelle

L'épreuve ci-dessus est synthétique. Trois mesures ne peuvent venir que de
l'usage :

1. **Le taux de propositions refusées par l'utilisateur**, avant et après. Si la
   Triarchie sert, ce taux baisse : elle filtre ce qui aurait été refusé.
2. **Le taux d'abstention d'Épiméthée.** S'il s'abstient presque toujours, c'est
   que l'historique n'est pas alimenté et qu'il ne sert à rien.
3. **Le taux d'unanimité.** S'il monte vers 100 % à l'usage, les critères se
   sont alignés et la délibération est redevenue du théâtre.

Sans ces trois mesures, on ne saura pas si la Triarchie fonctionne ou si elle
mime. Elles devraient être exposées dans le Dashboard.
