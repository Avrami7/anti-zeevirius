"""
app_manager.py
Gestion et tri des applications installées — équivalent de la page
Windows "Paramètres > Applications" (voir capture d'écran fournie par
Julz : "Applications — Désinstaller, par défaut"), avec en plus :
- Tri par taille / nom / bloatware en premier
- Détection automatique du bloatware Microsoft/OEM préinstallé connu
- Désinstallation pilotée (Win32 via désinstalleur natif, UWP via
  Remove-AppxPackage)

DEUX FAMILLES D'APPLICATIONS, DEUX MÉCANISMES DE DÉSINSTALLATION :
1. Win32 classiques (registre "Uninstall") → on lance leur DÉSINSTALLEUR
   NATIF (UninstallString déjà présent dans le registre). On ne réinvente
   jamais un désinstalleur maison : celui de l'éditeur sait nettoyer ses
   propres clés de registre, services, tâches planifiées, raccourcis —
   une suppression de fichiers à la main laisserait des résidus.
2. UWP/Store (Get-AppxPackage) → Remove-AppxPackage, seule méthode fiable
   et silencieuse pour ce type de paquet.

RÈGLE DE SÉCURITÉ CENTRALE :
Aucune application n'est JAMAIS désinstallée sans confirmation explicite
(voir main.py, options 25-27). Le tri "bloatware connu en premier" sert
UNIQUEMENT à proposer un ordre de priorité à l'utilisateur — jamais à agir
tout seul. Les composants système/frameworks (IsFramework, NonRemovable,
+ liste de mots-clés en dur ci-dessous) sont EXCLUS dès la détection —
jamais même affichés comme candidats, par défense en profondeur (même si
Remove-AppxPackage refuserait de toute façon de les supprimer).
"""

import json
import subprocess
from typing import Dict, List, Optional

try:
    import winreg
    WINREG_AVAILABLE = True
except ImportError:
    WINREG_AVAILABLE = False


# ── Bloatware UWP connu (OEM/Microsoft), couramment supprimé sans risque
# pour un usage bureautique standard. Liste volontairement PRUDENTE :
# mieux vaut rater un candidat que proposer par erreur un composant utile.
KNOWN_BLOATWARE_PACKAGES: Dict[str, str] = {
    "Microsoft.3DBuilder": "Constructeur 3D (rarement utilisé)",
    "Microsoft.BingFinance": "Finance (widget Bing)",
    "Microsoft.BingNews": "Actualités (widget Bing)",
    "Microsoft.BingSports": "Sport (widget Bing)",
    "Microsoft.BingWeather": "Météo (widget Bing)",
    "Microsoft.GetHelp": "Obtenir de l'aide",
    "Microsoft.Getstarted": "Prise en main Windows",
    "Microsoft.Messaging": "Messagerie (obsolète)",
    "Microsoft.Microsoft3DViewer": "Visionneuse 3D",
    "Microsoft.MicrosoftOfficeHub": "Accueil Office (pas Office lui-même)",
    "Microsoft.MicrosoftSolitaireCollection": "Solitaire",
    "Microsoft.MixedReality.Portal": "Réalité mixte (inutile sans casque VR)",
    "Microsoft.NetworkSpeedTest": "Test de vitesse réseau",
    "Microsoft.News": "Actualités",
    "Microsoft.Office.Sway": "Sway",
    "Microsoft.OneConnect": "Facturation mobile (Mobile Plans)",
    "Microsoft.People": "Contacts (People)",
    "Microsoft.Print3D": "Impression 3D",
    "Microsoft.SkypeApp": "Skype (version Store)",
    "Microsoft.Wallet": "Wallet",
    "Microsoft.WindowsAlarms": "Alarmes et horloge",
    "Microsoft.WindowsFeedbackHub": "Hub de commentaires",
    "Microsoft.WindowsMaps": "Cartes",
    "Microsoft.WindowsSoundRecorder": "Magnétophone (Enregistreur vocal)",
    "Microsoft.Xbox.TCUI": "Composant Xbox (chat)",
    "Microsoft.XboxApp": "Application Xbox",
    "Microsoft.XboxGameOverlay": "Overlay Xbox",
    "Microsoft.XboxGamingOverlay": "Xbox Game Bar",
    "Microsoft.XboxIdentityProvider": "Identité Xbox",
    "Microsoft.XboxSpeechToTextOverlay": "Xbox Speech-to-Text",
    "Microsoft.YourPhone": "Votre téléphone (si non utilisé)",
    "Microsoft.ZuneMusic": "Musique Groove",
    "Microsoft.ZuneVideo": "Films et TV",
    "Microsoft.GamingApp": "Xbox (nouvelle version)",
    "Microsoft.Todos": "Microsoft To Do (si non utilisé)",
    "Clipchamp.Clipchamp": "Clipchamp (éditeur vidéo, si non utilisé)",
    "Microsoft.549981C3F5F10": "Cortana",
    "MicrosoftTeams": "Teams (version grand public préinstallée, pas Teams pro)",
}

# JAMAIS proposés à la suppression, même s'ils apparaissent dans la liste
# des paquets installés — composants système critiques dont la suppression
# peut casser la connexion, la sécurité, ou l'interface Windows elle-même.
NEVER_REMOVE_PACKAGE_KEYWORDS = [
    "sechealthui", "windowsstore", "vclibs", "net.native", "ui.xaml",
    "desktopappinstaller", "storepurchaseapp", "accountscontrol",
    "creddialoghost", "webviewhost", "aad.brokerplugin", "lockapp",
    "shellexperiencehost", "startmenuexperiencehost", "securityhealth",
    "windows.search", "windows.photos", "appinstaller", "npmodulehost",
]


class AppManager:
    # ── Win32 (registre Uninstall) ───────────────────────────────
    @staticmethod
    def list_win32_apps() -> List[Dict]:
        """Lit les clés de registre 'Uninstall' (64 bits, 32 bits via
        Wow6432Node, et utilisateur courant) — exactement la même source
        de données que la page Windows Paramètres > Applications."""
        if not WINREG_AVAILABLE:
            return []

        apps: List[Dict] = []
        hives = [
            (winreg.HKEY_LOCAL_MACHINE, r"SOFTWARE\Microsoft\Windows\CurrentVersion\Uninstall"),
            (winreg.HKEY_LOCAL_MACHINE, r"SOFTWARE\WOW6432Node\Microsoft\Windows\CurrentVersion\Uninstall"),
            (winreg.HKEY_CURRENT_USER, r"SOFTWARE\Microsoft\Windows\CurrentVersion\Uninstall"),
        ]

        for hive, path in hives:
            try:
                key = winreg.OpenKey(hive, path)
            except OSError:
                continue

            count = winreg.QueryInfoKey(key)[0]
            for i in range(count):
                try:
                    subkey_name = winreg.EnumKey(key, i)
                    with winreg.OpenKey(key, subkey_name) as subkey:
                        def _get(name, default=None):
                            try:
                                return winreg.QueryValueEx(subkey, name)[0]
                            except OSError:
                                return default

                        display_name = _get("DisplayName")
                        if not display_name:
                            continue
                        if _get("SystemComponent") == 1:
                            continue  # composant système — jamais dans la liste

                        estimated_size = _get("EstimatedSize", 0) or 0
                        apps.append({
                            "name": display_name,
                            "version": _get("DisplayVersion", ""),
                            "publisher": _get("Publisher", ""),
                            "install_date": _get("InstallDate", ""),
                            "size_mb": round(estimated_size / 1024, 1),
                            "uninstall_string": _get("UninstallString", ""),
                            "registry_key": subkey_name,
                            "type": "win32",
                        })
                except OSError:
                    continue
            key.Close()

        return apps

    # ── UWP / Store ───────────────────────────────────────────────
    @staticmethod
    def list_uwp_apps() -> List[Dict]:
        """Liste les applications UWP/Store via PowerShell Get-AppxPackage.
        Exclut dès la détection les frameworks (IsFramework) et composants
        non supprimables (NonRemovable), ainsi que tout paquet correspondant
        à NEVER_REMOVE_PACKAGE_KEYWORDS (défense en profondeur)."""
        try:
            ps_command = (
                "Get-AppxPackage | Where-Object { -not $_.IsFramework -and -not $_.NonRemovable } | "
                "Select-Object Name, PackageFullName, Version, InstallDate, Publisher | ConvertTo-Json"
            )
            result = subprocess.run(
                ["powershell", "-NoProfile", "-Command", ps_command],
                capture_output=True, text=True, timeout=30,
            )
            if result.returncode != 0 or not result.stdout.strip():
                return []
            data = json.loads(result.stdout)
            if isinstance(data, dict):
                data = [data]
        except (subprocess.SubprocessError, OSError, json.JSONDecodeError, ValueError):
            return []

        apps: List[Dict] = []
        for pkg in data:
            name = pkg.get("Name", "") or ""
            if AppManager._is_never_remove(name):
                continue
            apps.append({
                "name": name,
                "version": str(pkg.get("Version", "")),
                "publisher": pkg.get("Publisher", ""),
                "install_date": pkg.get("InstallDate", ""),
                "package_full_name": pkg.get("PackageFullName", ""),
                "type": "uwp",
                "known_bloatware": AppManager._is_known_bloatware(name),
                "bloatware_reason": KNOWN_BLOATWARE_PACKAGES.get(name, ""),
            })
        return apps

    # ── Logique pure (testable sans registre/PowerShell) ─────────
    @staticmethod
    def _is_never_remove(package_name: str) -> bool:
        lowered = package_name.lower()
        return any(kw in lowered for kw in NEVER_REMOVE_PACKAGE_KEYWORDS)

    @staticmethod
    def _is_known_bloatware(package_name: str) -> bool:
        return package_name in KNOWN_BLOATWARE_PACKAGES

    @staticmethod
    def sort_apps(apps: List[Dict], sort_by: str = "size") -> List[Dict]:
        """sort_by : 'size' (défaut), 'name', ou 'bloatware_first'
        (bloatware connu remonté en tête, puis alphabétique)."""
        apps = list(apps)
        if sort_by == "size":
            apps.sort(key=lambda a: a.get("size_mb", 0), reverse=True)
        elif sort_by == "name":
            apps.sort(key=lambda a: a["name"].lower())
        elif sort_by == "bloatware_first":
            apps.sort(key=lambda a: (not a.get("known_bloatware", False), a["name"].lower()))
        else:
            raise ValueError(f"Tri inconnu : {sort_by}")
        return apps

    # ── Vue combinée ───────────────────────────────────────────────
    def list_all_sorted(self, sort_by: str = "size") -> Dict:
        """Combine Win32 + UWP, triés selon `sort_by`."""
        combined = self.list_win32_apps() + self.list_uwp_apps()
        combined = self.sort_apps(combined, sort_by=sort_by)
        bloatware_count = sum(1 for a in combined if a.get("known_bloatware"))
        return {"apps": combined, "total": len(combined), "known_bloatware_count": bloatware_count}

    # ── Désinstallation ────────────────────────────────────────────
    @staticmethod
    def uninstall_win32(app: Dict) -> Dict:
        """Lance le désinstalleur NATIF de l'application — comportement
        identique à un clic sur 'Désinstaller' dans les Paramètres Windows.
        Une fenêtre d'assistant peut s'ouvrir : c'est normal, on ne force
        aucun argument silencieux (chaque installeur a sa propre syntaxe,
        deviner un flag risquerait de casser une désinstallation)."""
        uninstall_string = app.get("uninstall_string", "")
        if not uninstall_string:
            return {"status": "erreur", "message": "Aucune commande de désinstallation trouvée pour cette application."}
        try:
            subprocess.Popen(uninstall_string, shell=True)
            return {
                "status": "ok",
                "message": f"Désinstalleur de '{app['name']}' lancé — suis l'assistant qui vient de s'ouvrir.",
            }
        except OSError as e:
            return {"status": "erreur", "message": str(e)}

    @staticmethod
    def uninstall_uwp(app: Dict) -> Dict:
        """Désinstalle un paquet UWP/Store via Remove-AppxPackage —
        silencieux, pas de fenêtre, contrairement au Win32."""
        package_full_name = app.get("package_full_name", "")
        if not package_full_name:
            return {"status": "erreur", "message": "Nom de paquet introuvable."}
        try:
            result = subprocess.run(
                ["powershell", "-NoProfile", "-Command",
                 f"Remove-AppxPackage -Package '{package_full_name}'"],
                capture_output=True, text=True, timeout=60,
            )
            if result.returncode == 0:
                return {"status": "ok", "message": f"'{app['name']}' désinstallé."}
            return {"status": "erreur", "message": result.stderr.strip() or "Échec de la désinstallation."}
        except (subprocess.SubprocessError, OSError) as e:
            return {"status": "erreur", "message": str(e)}

    def uninstall(self, app: Dict) -> Dict:
        """Point d'entrée unique : redirige vers le bon mécanisme selon
        le type d'application. Toujours appelé APRÈS confirmation
        explicite côté menu — jamais automatiquement."""
        if app.get("type") == "uwp":
            return self.uninstall_uwp(app)
        return self.uninstall_win32(app)

    def remove_known_bloatware(self, apps: Optional[List[Dict]] = None) -> Dict:
        """Désinstalle EN LOT le bloatware UWP connu détecté (jamais les
        Win32 en lot : lancer plusieurs assistants de désinstallation
        d'un coup serait confus et risqué — le traitement en lot est
        réservé aux UWP, où Remove-AppxPackage est fiable et silencieux).
        Appelé uniquement après confirmation explicite (voir main.py,
        option 27) — jamais automatiquement, y compris depuis le Mode
        Gardien."""
        apps = apps if apps is not None else self.list_uwp_apps()
        targets = [a for a in apps if a.get("known_bloatware")]
        removed, errors = [], []
        for app in targets:
            result = self.uninstall_uwp(app)
            if result["status"] == "ok":
                removed.append(app["name"])
            else:
                errors.append(f"{app['name']} : {result['message']}")
        return {"removed": removed, "errors": errors, "total_candidates": len(targets)}
