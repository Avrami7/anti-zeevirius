# Didacticiels — fonctions de sécurité avancée

Un didacticiel par fonction, avec la même structure : à quoi ça sert, comment
s'en servir, ce que ça risque, quand l'utiliser, ce que ça ne fait pas, et un
exemple concret.

Ces textes servent à deux choses : t'expliquer les fonctions, et alimenter
directement l'aide affichée dans l'interface.

---

## 1. Sandbox locale — exécuter un fichier douteux sous cloche

### À quoi ça sert
Tu as téléchargé un programme dont tu n'es pas sûr. Tu veux voir ce qu'il fait
**sans qu'il touche à ta machine**. La sandbox l'exécute dans un espace isolé,
jeté après usage.

### Comment s'en servir
1. Panneau **Protection** → **Analyser dans la sandbox**
2. Choisis le fichier
3. L'outil affiche d'abord **le niveau d'isolement disponible sur ta machine** —
   lis-le, c'est le point important
4. Lance, observe le rapport : fichiers créés, clés de registre écrites,
   connexions tentées, processus lancés
5. Ferme : tout ce que le programme a produit disparaît

### Les risques
- **Un isolement de niveau 2 n'est pas une machine virtuelle.** Un logiciel
  malveillant très élaboré peut en sortir en exploitant une faille de Windows.
- Certains logiciels malveillants **détectent qu'ils sont observés** et
  restent inertes : un rapport vierge ne prouve pas l'innocuité.
- Un fichier vraiment dangereux reste dangereux : la sandbox aide à décider,
  elle n'autorise pas l'imprudence.

### Cas d'usage
- Un installeur reçu par courriel
- Un utilitaire trouvé sur un site inconnu
- Une pièce jointe `.docm` avec macros
- Un vieux programme dont tu ne connais plus la provenance

### Limites techniques
| Niveau | Condition | Isolement |
|---|---|---|
| Windows Sandbox | Windows Pro/Entreprise, virtualisation activée dans le BIOS | Fort |
| AppContainer | Toutes éditions | Moyen : pas d'accès à tes documents, pas de réseau, arrêt automatique |
| Analyse statique | Toujours | Nul — le fichier n'est pas exécuté |

Si ton Windows est en édition Famille, tu seras en niveau 2. C'est utile, mais
ce n'est pas hermétique. **L'outil te le dira avant de lancer.**

### Exemple concret
Tu télécharges `convertisseur-pdf-gratuit.exe`. En sandbox, le rapport montre :

```
Fichiers créés     : %APPDATA%\svchost32\runner.exe
Registre           : HKCU\...\Run\WindowsUpdate  ← démarrage automatique
Réseau             : 185.xx.xx.xx:4444  ← port de porte dérobée classique
Processus lancés   : powershell -enc <base64>
```

Un convertisseur PDF n'a aucune raison de s'installer au démarrage ni d'ouvrir
une connexion sur le port 4444. Verdict clair, et ta machine n'a rien subi.

---

## 2. Analyse comportementale — surveiller ce qu'un programme *fait*

### À quoi ça sert
Un antivirus classique demande « ce fichier ressemble-t-il à un malware connu ? ».
L'analyse comportementale demande **« ce programme se comporte-t-il normalement ? »**.
C'est ce qui attrape les menaces jamais vues.

### Comment s'en servir
1. Panneau **Protection** → **Surveiller un processus**
2. Choisis le processus dans la liste
3. Laisse tourner : le score monte si le comportement devient anormal
4. Au dépassement du seuil, l'outil propose de **geler** le processus

### Les risques
- **Faux positifs.** Un logiciel de sauvegarde lit et écrit massivement — comme
  un rançongiciel. Un compilateur crée des dizaines de processus. Un
  gestionnaire de téléchargement ouvre beaucoup de connexions.
- Geler un processus légitime peut faire perdre un travail non enregistré.
- La détection est **réactive** : elle intervient après les premières écritures,
  pas avant.

### Cas d'usage
- Un programme inconnu que tu dois vraiment exécuter
- Une machine qui rame sans raison apparente
- Un doute sur un logiciel après une mise à jour
- Après avoir cliqué sur quelque chose que tu regrettes

### Limites techniques
Observation en mode utilisateur : on voit ce que le système veut bien montrer.
Un programme lancé avec plus de privilèges que l'outil peut lui échapper.
Aucune interception : on constate, on n'empêche pas.

### Exemple concret
```
14:22:01  Notepad++.exe    score 0    normal
14:31:47  facture.exe      score 35   écrit 60 fichiers en 8 s dans Documents
14:31:49  facture.exe      score 70   supprime les clichés instantanés
14:31:49  facture.exe      score 95   ALERTE — gel proposé
```

La suppression des clichés instantanés est le signal le plus caractéristique
d'un rançongiciel : il détruit les sauvegardes de Windows pour t'empêcher de
revenir en arrière. **Gèle immédiatement.**

---

## 3. Pare-feu applicatif — décider qui a le droit de sortir

### À quoi ça sert
Empêcher une application précise de communiquer avec Internet. Utile pour un
logiciel qui « téléphone » sans raison, ou pour couper un programme suspect.

### Comment s'en servir
1. Panneau **Protection** → **Connexions et pare-feu**
2. La liste montre ce qui communique en ce moment, application par application
3. **Bloquer** pose une règle dans le pare-feu de Windows
4. La règle apparaît dans l'historique et se retire d'un clic

### Les risques
- **Bloquer la mauvaise application casse quelque chose** : couper un service
  Windows peut empêcher les mises à jour de sécurité.
- Certains logiciels échouent silencieusement une fois bloqués.
- Exige les droits administrateur.

### Cas d'usage
- Un logiciel gratuit qui envoie des statistiques
- Un vieux programme qu'on veut garder hors ligne
- Isoler une application le temps de l'examiner

### Limites techniques
**À lire absolument.** Ce module **ne remplace pas** un pare-feu commercial. Il
ne peut pas afficher « telle application veut se connecter, autoriser ? » au
moment où ça arrive : cela demande un composant en mode noyau, que ce projet
s'interdit.

Ce qu'il fait : rendre lisible et pilotable le pare-feu de Windows, qui est
déjà là et efficace. Tu poses des règles à froid, en connaissance de cause,
plutôt que de répondre à une fenêtre surgissante dans l'urgence.

### Exemple concret
```
Application                   Destination        Port    État
Lecteur-Video-Pro.exe         51.xx.xx.xx (RU)   443     ← 240 Mo envoyés
Firefox.exe                   multiples          443     normal
Windows Update                microsoft.com      443     normal
```

Un lecteur vidéo qui **envoie** 240 Mo n'a aucune justification. Blocage
sortant, et la règle reste annulable si tu constates une régression.

---

## 4. Détection d'anomalies — repérer ce qui se cache

### À quoi ça sert
Certains logiciels malveillants se dissimulent : processus absent du
gestionnaire des tâches, fichier invisible dans l'explorateur, service qui
n'apparaît nulle part. Ce module compare **plusieurs façons de poser la même
question** au système : si les réponses divergent, quelque chose ment.

### Comment s'en servir
1. Panneau **Protection** → **Recherche d'anomalies**
2. Lance l'analyse (une à deux minutes)
3. Lis le rapport : chaque divergence est expliquée, avec sa cause probable

### Les risques
- **Beaucoup de faux positifs.** Les antivirus tiers, les machines virtuelles
  et certains outils de jeu créent des divergences parfaitement légitimes.
- Ne supprime rien, ne répare rien : c'est un instrument de mesure.

### Cas d'usage
- Après une infection traitée, pour vérifier qu'il ne reste rien
- Une machine au comportement inexplicable
- Un doute sur un logiciel « nettoyeur » installé jadis

### Limites techniques
**Le point le plus important de tout ce document.**

Ce module s'appelle « détection d'anomalies » et non « anti-rootkit », et c'est
volontaire. Un rootkit installé dans le noyau de Windows **contrôle les réponses
que le système donne aux programmes**. Un outil en mode utilisateur, comme
celui-ci, ne peut donc pas le voir — pas par manque de soin, mais par
construction : il pose des questions à quelqu'un qui décide des réponses.

Ce qu'on attrape : les dissimulations naïves, de loin les plus répandues.
Ce qu'on n'attrape pas : un rootkit noyau bien fait.

**Un rapport vierge ne signifie pas « machine saine ».** En cas de soupçon
sérieux, analyse le disque depuis un support de démarrage externe.

### Exemple concret
```
ANOMALIE — Processus
  PID 4820 « svch0st.exe » visible via WMI, absent de la liste standard
  → nom imitant svchost.exe, avec un zéro. Très suspect.

ANOMALIE — Flux alternatif
  C:\Users\Zeev\Downloads\photo.jpg:payload.exe (240 Ko)
  → un exécutable caché dans un flux annexe d'une image.

INFORMATION — Pilote non signé
  vboxdrv.sys → VirtualBox. Cause légitime connue.
```

Deux vraies découvertes, une explication rassurante : c'est le rendu attendu.

---

## 5. Connexions réseau suspectes — voir qui parle à qui

### À quoi ça sert
Savoir, à un instant donné, quelles applications communiquent, avec qui, et
lesquelles n'ont aucune raison de le faire.

### Comment s'en servir
1. Panneau **Protection** → **Connexions réseau**
2. La liste est triée par niveau de suspicion
3. Chaque ligne peut mener à un blocage (module 3) ou à une analyse du
   programme concerné

### Les risques
- Une adresse à l'étranger n'est pas une preuve : la plupart des services
  légitimes utilisent des hébergeurs répartis dans le monde.
- Un réseau de diffusion de contenu affiche des adresses sans nom de domaine
  clair, sans que ce soit anormal.

### Cas d'usage
- Ordinateur lent alors que rien ne tourne
- Consommation de données inexpliquée
- Vérification après un fichier suspect ouvert

### Limites techniques
Lecture des tables de connexion, sans interception du contenu : on voit **qui
parle à qui**, jamais ce qui est dit. Le trafic chiffré reste chiffré. Une
connexion très brève entre deux relevés passe inaperçue.

### Exemple concret
```
SUSPECT   updater.exe (non signé)    45.xx.xx.xx:4444    IP brute, port de porte dérobée
SUSPECT   winlogin.exe               91.xx.xx.xx:443     domaine créé il y a 3 jours
NORMAL    chrome.exe                 google.com:443
```

`winlogin.exe` — le vrai s'appelle `winlogon.exe` — communiquant avec un
domaine créé trois jours plus tôt : deux signaux qui se renforcent.

---

## 6. Mode Incident — le bouton d'urgence

### À quoi ça sert
Tu penses être infecté **maintenant**. Chaque seconde compte. Un seul bouton
coupe la propagation et préserve les preuves.

### Comment s'en servir
1. Bouton rouge **Mode Incident**, en haut de l'interface
2. L'outil affiche ce qu'il va faire, tu confirmes
3. En quelques secondes : réseau coupé, processus suspects gelés, sauvegarde
   lancée, rapport produit
4. **Sortie du mode** : un seul clic remet tout en place

### Les risques
- **Coupure réseau immédiate** : travail en ligne non enregistré perdu, appels
  interrompus, téléchargements arrêtés.
- Un processus légitime gelé peut perdre des données non enregistrées.
- La sauvegarde par cliché instantané prend du temps et de l'espace disque.

### Cas d'usage
- Des fichiers changent de nom ou d'extension sous tes yeux
- Une demande de rançon s'affiche
- Une activité disque ou réseau intense et inexplicable
- Tu viens d'ouvrir une pièce jointe et tu comprends que c'était une erreur

### Limites techniques
- **Gel, pas arrêt.** Un processus gelé peut être examiné ; un processus tué
  perd ses preuves, et certains rançongiciels réagissent à leur propre arrêt.
- Un chiffrement déjà terminé ne sera pas annulé : ce mode limite les dégâts,
  il ne les répare pas.
- Exige les droits administrateur pour le pare-feu et les clichés VSS.
- Un logiciel malveillant plus privilégié que l'outil peut résister au gel.

### Exemple concret
```
MODE INCIDENT — 14:32:07

[1/4] Réseau            coupé (règle pare-feu AZ_INCIDENT)
[2/4] Processus gelés   facture.exe (PID 8821), runner.exe (PID 9002)
[3/4] Sauvegarde        cliché VSS de Documents, Images, Bureau — 2,1 Go
[4/4] Rapport           142 fichiers modifiés en 6 minutes
                        clichés instantanés supprimés à 14:31:49

À FAIRE MAINTENANT
  • Ne redémarre pas : la mémoire contient peut-être la clé de chiffrement
  • Examine les 142 fichiers listés
  • Sortie du mode : bouton « Rétablir »
```

Le conseil de ne pas redémarrer est important : pour plusieurs familles de
rançongiciels, la clé se trouve encore en mémoire vive tant que la machine
reste allumée.

---

## Rappel valable pour tout ce document

ANTI-ZEEVIRIUS est un **complément** à Windows Defender, pas un remplacement.
Il n'a pas de composant en mode noyau : sa surveillance est plus lente et
moins exhaustive que celle d'un antivirus certifié. Garde Defender actif.

Et la règle qui gouverne l'application entière : **rien n'est supprimé sans
que tu aies vu le plan, et tout ce qui est retiré peut revenir.**
