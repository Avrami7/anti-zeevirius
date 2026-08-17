# Empaquetage Windows d'ANTI-ZEEVIRIUS

Comment on passe d'un dossier de sources Python à un `ANTI-ZEEVIRIUS-Setup.exe`
que l'on double-clique — et ce que cet installeur ne fera pas.

```
sources Python
      │
      │  PyInstaller  (anti-zeevirius.spec)
      ▼
dist/ANTI-ZEEVIRIUS.exe          un seul fichier, fenêtré, icône, métadonnées
      │
      │  Inno Setup  (installer.iss)
      ▼
dist/ANTI-ZEEVIRIUS-Setup.exe    installeur : Program Files, raccourcis,
                                 désinstalleur, question sur les données
```

| Fichier | Rôle |
|---|---|
| `make_icon.py` | dessine `anti-zeevirius.ico` (7 résolutions) depuis le logo du projet |
| `anti-zeevirius.ico` | l'icône, versionnée pour ne pas dépendre de Pillow à la construction |
| `icon-preview.png` | planche de contrôle du rendu de l'icône, de 16 à 256 px |
| `anti-zeevirius.spec` | recette PyInstaller : quoi embarquer, quoi exclure, métadonnées |
| `installer.iss` | script Inno Setup : où installer, quels raccourcis, quoi désinstaller |
| `launcher.py` | point d'entrée de l'application gelée (port libre, fenêtre, instance unique) |
| `../.github/workflows/build-windows.yml` | la même chaîne, automatisée |

---

## 1. La voie normale : laisser l'intégration continue construire

C'est la méthode recommandée, et pas seulement par confort : elle part d'une
machine propre, elle lance les tests avant de construire, et elle refuse de
publier si quoi que ce soit échoue.

* **Essai** : onglet *Actions* → *Installeur Windows* → *Run workflow*.
  L'installeur est déposé en artefact, téléchargeable pendant 30 jours.
* **Version publiée** : poser un tag et le pousser.

  ```bash
  git tag v1.0.0
  git push origin v1.0.0
  ```

  Le numéro du tag est gravé dans l'exécutable (`AZ_VERSION`) et dans
  l'installeur (`/DAppVersion`), et l'installeur est attaché à la Release,
  accompagné de son empreinte SHA-256.

## 2. Construire à la main, sur une machine Windows

**Il faut une machine Windows.** PyInstaller n'est pas un compilateur croisé :
il fabrique un exécutable pour le système sur lequel il tourne, en y copiant
l'interpréteur Python de cette machine. Depuis Linux, on obtient un binaire
Linux, jamais un `.exe`.

```powershell
# 1. Dépendances (Python 3.11)
pip install -r requirements-dev.txt
pip install "pyinstaller>=6.0" pillow

# 2. Les tests d'abord. Un installeur d'antivirus dont la quarantaine est
#    cassée est plus nuisible que pas d'installeur du tout.
python -m pytest tests/ -q

# 3. L'icône (facultatif : elle est versionnée)
python packaging/make_icon.py --preview

# 4. L'exécutable  →  dist\ANTI-ZEEVIRIUS.exe
pyinstaller --noconfirm --clean packaging/anti-zeevirius.spec

# 5. L'installeur  →  dist\ANTI-ZEEVIRIUS-Setup.exe
#    Inno Setup 6.3 minimum (le script utilise ArchitecturesAllowed=x64compatible)
& "C:\Program Files (x86)\Inno Setup 6\ISCC.exe" packaging\installer.iss
```

Inno Setup s'installe depuis <https://jrsoftware.org/isdl.php> ou par
`choco install innosetup`.

## 3. Vérifier ce qu'on vient de produire

Construire n'est pas vérifier. La liste ci-dessous est courte et couvre les
pannes réellement observées avec ce genre de paquet.

**L'exécutable**

1. Clic droit sur `dist\ANTI-ZEEVIRIUS.exe` → *Propriétés* → onglet *Détails* :
   nom du produit, description et version doivent être renseignés. S'ils sont
   vides, la ressource de version n'a pas été écrite.
2. Double-clic : l'interface doit s'ouvrir dans le navigateur en quelques
   secondes, **sans fenêtre de console noire**.
3. Dans l'interface, ouvrir l'état du système : chaque module doit répondre.
   Un module qui répond « indisponible » alors qu'il fonctionne depuis les
   sources = import caché manquant dans le `.spec` (voir §6).
4. Lancer un scan sur un dossier d'essai, mettre un fichier en quarantaine,
   le restaurer. C'est le chemin qui touche le plus de couches à la fois.

**L'installeur**

5. L'installer sur une machine (ou une machine virtuelle) qui n'a jamais vu le
   projet — c'est le seul moyen de détecter une dépendance qu'on croyait
   embarquée et qui venait en réalité de l'environnement de développement.
6. Vérifier les deux raccourcis (menu Démarrer, et Bureau si la case était
   cochée), et l'entrée dans *Applications installées*.
7. **Désinstaller, et lire la question posée.** Elle doit proposer de
   conserver `%LOCALAPPDATA%\ANTI-ZEEVIRIUS`, avec « Non » présélectionné.
   Répondre Non, puis vérifier que le dossier est toujours là : la quarantaine
   ne doit jamais partir par surprise.
8. Comparer l'empreinte SHA-256 du fichier téléchargé avec celle publiée à
   côté de lui.

## 4. Limites assumées — à lire avant de distribuer

### L'exécutable n'est pas signé numériquement

Ni l'application, ni l'installeur ne portent de signature de code. Ce n'est
pas un oubli : c'est un choix de coût, et il a des conséquences visibles.

**Ce que verra l'utilisateur.** Au premier lancement de
`ANTI-ZEEVIRIUS-Setup.exe`, Windows SmartScreen affiche un écran bleu :

> **Windows a protégé votre ordinateur**
> Microsoft Defender SmartScreen a empêché le démarrage d'une application non
> reconnue. L'exécution de cette application peut mettre votre ordinateur en
> danger.

Le bouton *Exécuter quand même* n'apparaît qu'après avoir cliqué sur
**Informations complémentaires**. Beaucoup d'utilisateurs s'arrêtent là — et
ont raison de se méfier : cet avertissement dit exactement la vérité, à savoir
que **l'éditeur n'est pas vérifié**. Il ne dit pas que le fichier est
malveillant ; il dit que personne ne peut prouver qui l'a produit.

La boîte UAC affichera de même « Éditeur inconnu » au lieu d'un nom.

**Ce que coûterait la signature.** Un certificat de signature de code ne
s'achète plus comme un simple fichier : depuis juin 2023, les autorités de
certification exigent que la clé privée réside dans un matériel certifié
(jeton USB ou HSM). Ordres de grandeur, à vérifier au moment de l'achat :

| Option | Coût annuel indicatif | Effet sur SmartScreen |
|---|---|---|
| Certificat OV (validation d'organisation) | 200 à 400 € + le jeton matériel | la réputation se construit progressivement, au fil des téléchargements |
| Certificat EV (validation étendue) | 400 à 700 € + jeton | réputation immédiate, l'avertissement disparaît dès la première version |
| Service de signature en nuage (type Azure Trusted Signing) | ~10 $/mois | équivalent OV, sans jeton à gérer ; exige une organisation avec plusieurs années d'existence vérifiable |

Toutes ces options exigent une **identité vérifiée** — une entreprise
enregistrée, ou une personne physique avec justificatifs. C'est le fond du
sujet : la signature n'atteste pas que le logiciel est sûr, elle atteste qu'on
sait à qui s'adresser s'il ne l'est pas.

**En attendant**, la seule garantie qu'on peut offrir est l'empreinte SHA-256
publiée avec chaque Release : elle prouve que le fichier téléchargé est bien
celui qu'a produit la chaîne de construction, pas qu'il vient de nous.

### Windows Defender peut signaler l'application elle-même

Il faut le dire franchement : **ANTI-ZEEVIRIUS a le profil de comportement
d'un logiciel indésirable**, et il serait étonnant qu'aucun moteur ne le
relève. Vu de l'extérieur, sans le code sous les yeux, le programme :

* lit et modifie le registre, y compris les clés de démarrage automatique
  (`HKLM\...\Run`) — technique de persistance de manuel ;
* crée des tâches planifiées via `schtasks` — même remarque ;
* déplace des fichiers hors de leur emplacement d'origine vers un dossier
  privé, en les renommant (la quarantaine) — indiscernable d'un rançongiciel
  qui met des fichiers de côté ;
* énumère les processus, surveille des dossiers en temps réel, calcule
  l'entropie de fichiers exécutables ;
* est un binaire PyInstaller non signé, forme sous laquelle circule une
  grande partie des logiciels malveillants écrits en Python.

Chacun de ces points est légitime dans un antivirus, et tous ensemble
constituent une signature comportementale. Conséquences concrètes :

* Defender peut mettre l'exécutable en quarantaine, parfois **pendant la
  construction** sur la machine de développement ;
* certains moteurs de VirusTotal renverront des détections génériques
  (`Trojan.Generic`, `Wacatac`, `Python/Agent`) : ce sont des heuristiques,
  pas des analyses ;
* un antivirus tiers déjà installé peut empêcher la surveillance temps réel
  de fonctionner — deux programmes qui verrouillent les mêmes fichiers.

Ce qu'on peut faire : signer le binaire (voir ci-dessus, c'est le levier
principal), **ne pas** compresser avec UPX (déjà désactivé dans le `.spec`,
c'est un aggravateur majeur), et soumettre le faux positif à Microsoft via le
portail *Security Intelligence — Submit a file for analysis*, qui traite ces
demandes en quelques jours.

### Autres limites, plus banales

* **Démarrage plus lent.** L'exécutable « un seul fichier » se décompresse à
  chaque lancement dans `%TEMP%\_MEIxxxxxx` : comptez une à trois secondes
  avant l'affichage, davantage si un antivirus inspecte l'extraction. C'est le
  prix du fichier unique ; un paquet en dossier (`--onedir`) démarrerait plus
  vite mais ne serait plus « un exécutable ».
* **L'application ne se lance pas en administrateur.** L'installeur, lui,
  l'exige. Les fonctions qui touchent `HKLM`, les tâches planifiées ou les
  dossiers système signaleront un manque de privilèges ; il faut alors lancer
  l'application par *Exécuter en tant qu'administrateur*. C'est délibéré :
  faire tourner en permanence en administrateur un programme qui sert un
  serveur HTTP local n'est pas un compromis acceptable.
* **Les données utilisateur survivent à la désinstallation**, sauf réponse
  explicite à la question posée. Volontaire : voir la section `[Code]` de
  `installer.iss`.
* **Windows 10 minimum**, 64 bits (ou ARM64 en émulation).

## 5. L'icône

`make_icon.py` redessine avec Pillow le trou noir de `gui/web/favicon.svg` —
mêmes proportions (disque `rx=27`, horizon `r=12`, arc `ry=17`), mêmes
couleurs — et exporte un `.ico` contenant **sept images distinctes** : 16, 24,
32, 48, 64, 128 et 256 pixels.

Sept dessins et non un seul redimensionné, parce que Windows choisit dans le
fichier l'image correspondant au contexte d'affichage : 16 px dans la vue
Détails et la barre des tâches, 32 px sur le Bureau, 256 px pour l'aperçu et
la fiche de l'installeur. Les petites tailles sont donc dessinées avec leurs
propres épaisseurs de trait (voir `TUNING` dans le script) : sous 32 px, l'arc
de lentille gravitationnelle fait moins d'un pixel de large et ne produit plus
qu'un voile gris, on le retire ; l'ellipse du disque est ouverte pour que
l'anneau enferme encore une zone noire reconnaissable.

```bash
python packaging/make_icon.py --preview   # écrit aussi icon-preview.png
```

`icon-preview.png` montre chaque taille agrandie au plus proche voisin puis à
l'échelle 1:1, sur fond clair et sur fond sombre : c'est la planche à regarder
avant de valider une modification de l'icône.

Si un outil ancien refuse le fichier (les images y sont compressées en PNG,
ce que Windows accepte depuis Vista) :

```bash
python packaging/make_icon.py --bitmap-format bmp   # ~350 Kio au lieu de 60
```

## 6. Pièges connus

**« Module indisponible » partout dans l'interface, alors que tout marche
depuis les sources.** C'est *le* piège de ce projet. `gui/bridge.py` importe
ses modules métier par `importlib.import_module()` à partir de chaînes de
caractères ; PyInstaller, qui suit les imports en lisant le bytecode, ne les
voit pas. Ils sont donc listés à la main dans `hiddenimports`
(`anti-zeevirius.spec`). **Toute entrée ajoutée à `_MODULE_SPECS` doit être
ajoutée à `hiddenimports`.** Un oubli ne casse pas la construction : il
produit un exécutable qui démarre normalement et refuse chaque action. Le
workflow relit pour cela le rapport `build/**/warn-*.txt` et échoue si une
dépendance critique y figure.

**La construction réussit mais l'interface reste blanche.** Les fichiers de
`gui/web/` n'ont pas été embarqués, ou pas à la bonne destination. Les
chemins du `.spec` doivent correspondre à ce qu'attend `paths.resource_path()`
(`gui/web`, `signatures`).

**Defender fait échouer la construction.** Sur la machine de développement
comme sur un agent d'intégration continue, l'antivirus peut supprimer le
`.exe` entre sa création et son utilisation par Inno Setup. Symptôme : Inno
Setup se plaint que `..\dist\ANTI-ZEEVIRIUS.exe` est introuvable alors que
PyInstaller a annoncé un succès. Remède local : exclure le dossier `dist\` de
l'analyse en temps réel.

**`yara-python` ne s'installe pas.** C'est une extension native. Sous Windows
et Python 3.11, il existe des roues précompilées ; sur une version de Python
plus récente que la bibliothèque, `pip` tentera une compilation qui échouera
faute d'outillage C. D'où le choix de Python **3.11** dans le workflow. Sans
`yara`, l'application démarre quand même — mais la couche de détection par
règles est absente, ce qui est une régression silencieuse.

**Le dossier `packaging/` et le paquet PyPI `packaging`.** Le projet contient
un dossier nommé `packaging/`, et l'outillage Python dépend d'une bibliothèque
du même nom. Il n'y a pas de conflit tant que ce dossier reste un simple
dossier : sans `__init__.py`, Python le traite comme un espace de noms de
moindre priorité et continue de chercher, jusqu'à trouver la vraie
bibliothèque. **Ne pas y ajouter de `__init__.py`** : il deviendrait un paquet
régulier et masquerait la bibliothèque `packaging`, cassant PyInstaller
lui-même.

**Les tests passent sous Linux mais pas sous Windows.** La suite tourne
aujourd'hui sur les deux, mais elle manipule des chemins, des permissions et
des dossiers temporaires : c'est le genre de code qui diverge entre systèmes.
Le workflow lance les tests **sur Windows**, ce qui est justement le but —
mais attendez-vous à ce que le premier passage en révèle.

**ISCC.exe introuvable.** Le chemin dépend de l'installation ; par défaut,
`C:\Program Files (x86)\Inno Setup 6\ISCC.exe`, y compris sur un Windows
64 bits (le compilateur est un binaire 32 bits).

**`ArchitecturesAllowed=x64compatible` refusé.** Version d'Inno Setup
antérieure à 6.3. Mettre à jour, ou revenir à `x64` — moins bon : cela exclut
les machines ARM64, qui exécutent pourtant très bien un binaire x64 en
émulation.
