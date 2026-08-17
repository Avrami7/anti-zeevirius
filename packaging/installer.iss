; ============================================================================
;  packaging/installer.iss — installeur Windows d'ANTI-ZEEVIRIUS (Inno Setup 6)
;
;  Produit ANTI-ZEEVIRIUS-Setup.exe : un fichier que l'on double-clique, qui
;  installe l'application, crée les raccourcis, et s'annonce proprement dans
;  « Applications et fonctionnalités ».
;
;  Compilation (Inno Setup 6.3 ou plus récent — voir ArchitecturesAllowed) :
;      iscc packaging\installer.iss
;      → dist\ANTI-ZEEVIRIUS-Setup.exe
;
;  Prérequis : dist\ANTI-ZEEVIRIUS.exe doit exister, c'est-à-dire que
;  PyInstaller doit avoir tourné AVANT (voir packaging/README.md).
;
;  La version peut être imposée depuis la ligne de commande, ce dont se sert
;  la chaîne d'intégration continue pour la tirer du tag Git :
;      iscc /DAppVersion=1.2.0 packaging\installer.iss
; ============================================================================

#ifndef AppVersion
  #define AppVersion "1.0.0"     ; à garder synchronisé avec anti-zeevirius.spec
#endif

#define AppName      "ANTI-ZEEVIRIUS"
#define AppExeName   "ANTI-ZEEVIRIUS.exe"
#define AppPublisher "ANTI-ZEEVIRIUS"
#define SourceDir    "..\dist"

[Setup]
; AppId identifie l'application pour TOUTES ses versions : c'est lui qui permet
; à une mise à jour de remplacer l'installation existante au lieu d'en créer
; une deuxième à côté. Il ne doit JAMAIS changer d'une version à l'autre.
AppId={{A0457777-A576-4ECF-B72C-93188E638F14}
AppName={#AppName}
AppVersion={#AppVersion}
AppVerName={#AppName} {#AppVersion}
AppPublisher={#AppPublisher}
VersionInfoVersion={#AppVersion}
VersionInfoDescription=Installeur d'{#AppName}
UninstallDisplayName={#AppName}
UninstallDisplayIcon={app}\{#AppExeName}

DefaultDirName={autopf}\{#AppName}
DefaultGroupName={#AppName}
AllowNoIcons=yes

; ── Élévation : pourquoi admin, et pas « le moins possible » ───────────────
; PrivilegesRequired=admin n'est pas du confort de développeur, c'est ce dont
; l'application a besoin pour faire ce qu'elle annonce :
;   * installation dans {autopf} = C:\Program Files, qui est en écriture
;     réservée aux administrateurs ;
;   * nettoyage des résidus et gestion du démarrage : lecture ET écriture sous
;     HKEY_LOCAL_MACHINE (optimizer/startup_manager.py, residue_cleaner.py) ;
;   * tâches planifiées de maintenance : schtasks refuse la création d'une
;     tâche système à un utilisateur non élevé (optimizer/task_scheduler.py) ;
;   * nettoyage de C:\Windows\Temp et des profils autres que le sien.
; Installer sans droits (dans %LOCALAPPDATA%) donnerait une application qui
; démarre et échoue sur la moitié de ses fonctions — pire qu'un refus franc.
PrivilegesRequired=admin

; x64compatible couvre le x86-64 ET l'ARM64 en émulation, ce que ne fait pas
; l'ancien « x64 ». Exige Inno Setup 6.3+ (voir l'en-tête).
ArchitecturesAllowed=x64compatible
ArchitecturesInstallIn64BitMode=x64compatible
MinVersion=10.0

OutputDir=..\dist
OutputBaseFilename={#AppName}-Setup
SetupIconFile=anti-zeevirius.ico
Compression=lzma2/max
SolidCompression=yes
WizardStyle=modern

; Si l'application tourne au moment de l'installation, ses fichiers sont
; verrouillés. Le Gestionnaire de redémarrage de Windows la repère et propose
; de la fermer, plutôt que d'exiger un redémarrage de la machine.
CloseApplications=yes
RestartApplications=no

; PAS de SignTool ici : l'exécutable n'est pas signé numériquement, et
; l'installeur non plus. Conséquences détaillées dans packaging/README.md
; (section SmartScreen). Le jour où un certificat de signature de code est
; acquis, c'est ici que se branche l'outil de signature.

[Languages]
; Le français d'abord : c'est la langue par défaut proposée. L'anglais reste
; disponible dans la liste — repli pour une machine non francophone.
Name: "francais"; MessagesFile: "compiler:Languages\French.isl"
Name: "english";  MessagesFile: "compiler:Default.isl"

[Tasks]
; Raccourci Bureau : coché par défaut (c'est ce qu'attend quelqu'un qui vient
; d'installer un antivirus et veut le retrouver), mais décochable — d'où une
; [Tasks] plutôt qu'une entrée [Icons] inconditionnelle.
Name: "desktopicon"; Description: "{cm:CreateDesktopIcon}"; \
    GroupDescription: "{cm:AdditionalIcons}"

[Files]
Source: "{#SourceDir}\{#AppExeName}"; DestDir: "{app}"; Flags: ignoreversion
; Documentation à côté de l'exécutable : l'application en embarque déjà une
; copie, mais un README lisible sans lancer le programme rend service quand
; justement le programme ne se lance pas.
; Pas de drapeau « isreadme » : il ferait ouvrir le fichier à la fin de
; l'installation, or une machine Windows neuve n'a aucune application associée
; au .md — l'installation se terminerait sur un message d'erreur de Windows.
Source: "..\README.md"; DestDir: "{app}"; Flags: ignoreversion

[Icons]
Name: "{group}\{#AppName}";      Filename: "{app}\{#AppExeName}"
Name: "{autodesktop}\{#AppName}"; Filename: "{app}\{#AppExeName}"; Tasks: desktopicon

[Run]
; runasoriginaluser est essentiel : l'installeur tourne élevé, et sans ce
; drapeau l'application hériterait de ses privilèges d'administrateur pour
; toute la session. Elle sert un serveur HTTP local — même limité à
; 127.0.0.1, on ne le fait pas tourner en administrateur par accident. C'est
; aussi ce qui garantit que %LOCALAPPDATA%\ANTI-ZEEVIRIUS est créé dans le
; profil de l'utilisateur réel, et non dans celui du compte élevé.
Filename: "{app}\{#AppExeName}"; \
    Description: "{cm:LaunchProgram,{#StringChange(AppName, '&', '&&')}}"; \
    Flags: nowait postinstall skipifsilent runasoriginaluser

[UninstallDelete]
; Rien ici : voir [Code]. Les données utilisateur ne sont PAS supprimées
; automatiquement, et surtout pas par une règle silencieuse.

[Messages]
francais.BeveledLabel=ANTI-ZEEVIRIUS
english.BeveledLabel=ANTI-ZEEVIRIUS

[CustomMessages]
; Le texte de la question posée à la désinstallation. Il doit être précis :
; l'utilisateur décide ici du sort de fichiers qu'il croyait avoir sauvés.
francais.PurgerDonnees=Supprimer aussi les données d'ANTI-ZEEVIRIUS ?%n%nLe dossier suivant va rester sur le disque :%n%1%n%nIl contient la QUARANTAINE, le sas de fichiers mis de côté et les journaux. Des fichiers parfaitement légitimes peuvent s'y trouver : un document mis en quarantaine par erreur n'existe plus qu'à cet endroit, et le supprimer ici le supprime définitivement.%n%nRépondez Non pour conserver ces données (recommandé). Vous pourrez les effacer vous-même plus tard, ou les retrouver en réinstallant le programme.%n%nSupprimer définitivement ces données ?
english.PurgerDonnees=Also delete ANTI-ZEEVIRIUS data?%n%nThe following folder will otherwise be left on disk:%n%1%n%nIt holds the QUARANTINE, the set-aside file staging area and the logs. Perfectly legitimate files may be in there: a document quarantined by mistake exists nowhere else, and deleting it here deletes it for good.%n%nAnswer No to keep this data (recommended). You can remove it yourself later, or find it again by reinstalling.%n%nPermanently delete this data?
francais.PurgeIncomplete=Certaines données n'ont pas pu être supprimées (fichiers verrouillés ?). Le dossier %1 existe peut-être encore.
english.PurgeIncomplete=Some data could not be deleted (locked files?). The folder %1 may still exist.

[Code]

{ ───────────────────────────────────────────────────────────────────────────
  Désinstallation et données utilisateur.

  Un désinstalleur d'antivirus qui efface silencieusement sa quarantaine
  détruit des fichiers que l'utilisateur croyait mis à l'abri : la quarantaine
  contient par construction des fichiers ARRACHÉS à leur emplacement d'origine
  — dont, régulièrement, des faux positifs. Ils n'existent plus ailleurs.

  On ne supprime donc rien sans poser la question, la réponse par défaut est
  « non », et en mode silencieux (/VERYSILENT, déploiement automatisé) on ne
  supprime jamais : personne n'est là pour répondre.
  ─────────────────────────────────────────────────────────────────────────── }

procedure CurUninstallStepChanged(CurUninstallStep: TUninstallStep);
var
  DossierDonnees: String;
  Question: String;
begin
  if CurUninstallStep <> usPostUninstall then
    Exit;

  { {localappdata} est résolu dans le profil de l'utilisateur qui désinstalle.
    Note d'honnêteté : si plusieurs comptes Windows ont utilisé l'application,
    seules les données de CE compte sont concernées — les autres profils
    gardent les leurs, ce qui est le comportement souhaitable. }
  DossierDonnees := ExpandConstant('{localappdata}\ANTI-ZEEVIRIUS');

  if not DirExists(DossierDonnees) then
    Exit;

  if UninstallSilent then
    Exit;

  Question := FmtMessage(CustomMessage('PurgerDonnees'), [DossierDonnees]);

  { MB_DEFBUTTON2 : le bouton présélectionné est « Non ». Une validation
    machinale conserve les données ; il faut un geste délibéré pour effacer. }
  if MsgBox(Question, mbConfirmation, MB_YESNO or MB_DEFBUTTON2) = IDYES then
  begin
    if not DelTree(DossierDonnees, True, True, True) then
      MsgBox(FmtMessage(CustomMessage('PurgeIncomplete'), [DossierDonnees]),
             mbError, MB_OK);
  end;
end;
