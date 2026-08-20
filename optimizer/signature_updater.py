"""
signature_updater.py — alimenter les bases de détection.

ANTI-ZEEVIRIUS dispose de trois couches de détection, mais il est livré avec
des bases quasi vides : une poignée d'empreintes d'exemple et quelques règles
YARA de démonstration. Un antivirus dont les bases sont vides est une
architecture, pas une protection. Ce module comble l'écart en récupérant des
sources publiques gratuites.

Deux sources, deux natures de problème :

  * **Empreintes** — MalwareBazaar (abuse.ch). Un fichier texte d'empreintes
    SHA-256, une par ligne. Le risque est le fichier tronqué ou la page
    d'erreur HTML servie à la place des données.

  * **Règles YARA** — dépôt `Neo23x0/signature-base` (Florian Roth). Le risque
    est tout autre et bien plus vicieux : **YARA compile un fichier en bloc**.
    Une seule règle invalide — module absent, variable externe non définie,
    syntaxe d'une version plus récente — fait échouer la compilation du fichier
    ENTIER, donc désarme toute la couche YARA. Et ces dépôts communautaires
    contiennent forcément de telles règles.

D'où le principe directeur : **chaque règle est compilée isolément avant d'être
retenue**, et le fichier final n'est écrit que s'il compile lui aussi. Mieux
vaut 900 règles qui fonctionnent que 1000 qui ne chargent pas.

Rien n'est jamais écrasé sans validation : téléchargement, vérification,
sauvegarde de l'ancienne base, puis remplacement atomique. Hors ligne ou en
cas d'échec, la base en place reste intacte et utilisable — beaucoup de
machines à désinfecter n'ont pas Internet.
"""

from __future__ import annotations

import json
import os
import re
import tempfile
import time
from pathlib import Path
from typing import Dict, List, Optional, Tuple

import requests

import paths

__all__ = ["SignatureUpdater", "split_yara_rules", "YARA_SOURCES", "HASH_SOURCE"]


# ── Sources ────────────────────────────────────────────────────────────────
HASH_SOURCE = "https://bazaar.abuse.ch/export/txt/sha256/recent/"

# Sélection de fichiers de règles à large couverture. Volontairement courte :
# ces dépôts sont bénévoles, on ne rapatrie pas tout à chaque exécution.
_NEO = "https://raw.githubusercontent.com/Neo23x0/signature-base/master/yara/"
YARA_SOURCES = [
    _NEO + "gen_webshells.yar",
    _NEO + "gen_powershell_susp.yar",
    _NEO + "crime_ransom_generic.yar",
    _NEO + "gen_susp_obfuscation.yar",
    _NEO + "gen_metasploit_payloads.yar",
    _NEO + "gen_mimikatz.yar",
    _NEO + "expl_log4j_cve_2021_44228.yar",
]

# On s'annonce honnêtement : ces services sont gratuits, un User-Agent
# identifiable permet à leurs opérateurs de nous joindre en cas d'abus.
USER_AGENT = "ANTI-ZEEVIRIUS/1.0 (antivirus personnel; mise a jour de signatures)"

MIN_UPDATE_INTERVAL_SECONDS = 6 * 3600   # pas plus d'une mise à jour / 6 h
REQUEST_TIMEOUT = 60

_SHA256_RE = re.compile(r"^[a-fA-F0-9]{64}$")

# Variables externes couramment utilisées par les règles de signature-base.
# Sans elles, des règles parfaitement valides refusent de compiler ; les
# fournir en fait retenir sensiblement plus.
YARA_EXTERNALS = {
    "filename": "", "filepath": "", "extension": "",
    "filetype": "", "owner": "",
}


def split_yara_rules(source: str) -> Tuple[List[str], List[Tuple[str, str]]]:
    """Découpe un fichier .yar en règles individuelles.

    Retourne `(imports, [(nom_de_regle, texte_de_la_regle), ...])`.

    Le découpage va d'un début de règle au début de la suivante, plutôt que de
    compter les accolades. Ce choix n'est pas une facilité : le comptage
    d'accolades échoue sur les règles réelles. Une règle YARA contient des
    expressions régulières telles que `/https?:\\/\\//`, dont le `\\/\\/` est pris
    pour un début de commentaire — la fin de ligne est alors sautée, une
    accolade fermante est perdue, et le bloc avale les règles suivantes.
    Mesuré sur `gen_webshells.yar` : 11 blocs sur 57 étaient fusionnés à tort,
    l'un d'eux absorbant 17 règles, ce qui produisait des identifiants
    dupliqués dans l'assemblage final.

    Découper aux frontières a un mode d'échec bien plus sûr : si un faux
    départ de règle était détecté (le mot `rule` en début de ligne à
    l'intérieur d'une chaîne), le bloc produit ne compilerait pas et serait
    simplement écarté par le filtre.
    """
    imports = re.findall(r'^\s*import\s+"[^"]+"', source, re.MULTILINE)

    debuts = [(m.start(), m.group(1)) for m in re.finditer(
        r'^\s*(?:private\s+|global\s+)*rule\s+([A-Za-z_]\w*)', source, re.MULTILINE)]

    regles: List[Tuple[str, str]] = []
    for i, (pos, nom) in enumerate(debuts):
        fin = debuts[i + 1][0] if i + 1 < len(debuts) else len(source)
        texte = source[pos:fin].rstrip()
        if texte:
            regles.append((nom, texte))
    return imports, regles


class SignatureUpdater:
    """Met à jour les bases de détection depuis les sources publiques."""

    def __init__(self, signatures_dir: Optional[Path] = None,
                 session: Optional["requests.Session"] = None):
        self.dir = Path(signatures_dir) if signatures_dir else paths.signatures_dir()
        self.dir.mkdir(parents=True, exist_ok=True)
        self.hashes_path = self.dir / "malicious_hashes.txt"
        self.rules_path = self.dir / "rules.yar"
        self.state_path = self.dir / "update_state.json"
        self.session = session or requests.Session()

    # ── État (cache conditionnel, intervalle minimal) ──────────────────────
    def _state(self) -> Dict:
        try:
            return json.loads(self.state_path.read_text(encoding="utf-8"))
        except (OSError, ValueError):
            return {}

    def _save_state(self, state: Dict) -> None:
        try:
            self.state_path.write_text(json.dumps(state, indent=2), encoding="utf-8")
        except OSError:
            pass

    def _trop_recent(self, cle: str) -> bool:
        dernier = self._state().get(cle, {}).get("last_success", 0)
        return (time.time() - dernier) < MIN_UPDATE_INTERVAL_SECONDS

    # ── Écriture atomique avec sauvegarde ──────────────────────────────────
    def _remplacer(self, cible: Path, contenu: str) -> None:
        """Remplace un fichier de façon atomique, en gardant l'ancien.

        L'écriture passe par un fichier temporaire du MÊME dossier (donc du
        même système de fichiers) avant `os.replace`, qui est atomique : à
        aucun instant la base n'existe à l'état tronqué. L'ancienne version est
        conservée en `.bak` pour permettre un retour en arrière.
        """
        if cible.exists():
            try:
                cible.replace(cible.with_suffix(cible.suffix + ".bak"))
            except OSError:
                pass
        fd, tmp = tempfile.mkstemp(dir=str(self.dir), suffix=".tmp")
        try:
            with os.fdopen(fd, "w", encoding="utf-8") as f:
                f.write(contenu)
            os.replace(tmp, cible)
        except BaseException:
            try:
                os.unlink(tmp)
            except OSError:
                pass
            raise

    def _telecharger(self, url: str, cle_etat: str) -> Tuple[Optional[str], Dict]:
        """Télécharge une ressource en cache conditionnel.

        Retourne `(texte, entetes)`. `texte` vaut None si le serveur répond
        304 (contenu inchangé) — ce n'est pas une erreur.
        """
        etat = self._state().get(cle_etat, {})
        entetes = {"User-Agent": USER_AGENT}
        if etat.get("etag"):
            entetes["If-None-Match"] = etat["etag"]
        if etat.get("last_modified"):
            entetes["If-Modified-Since"] = etat["last_modified"]

        r = self.session.get(url, headers=entetes, timeout=REQUEST_TIMEOUT)
        if r.status_code == 304:
            return None, dict(r.headers)
        r.raise_for_status()
        return r.text, dict(r.headers)

    # ── Empreintes ─────────────────────────────────────────────────────────
    def update_hashes(self, force: bool = False) -> Dict:
        if not force and self._trop_recent("hashes"):
            return {"status": "ignore", "raison": "mise à jour trop récente",
                    "conservees": self._compter_hashes()}
        try:
            texte, entetes = self._telecharger(HASH_SOURCE, "hashes")
        except requests.RequestException as e:
            # Hors ligne ou source indisponible : la base en place reste
            # utilisable, c'est le comportement attendu sur une machine à
            # désinfecter qui n'a pas de réseau.
            return {"status": "erreur", "raison": f"source injoignable : {e}",
                    "conservees": self._compter_hashes()}

        if texte is None:
            return {"status": "inchange", "conservees": self._compter_hashes()}

        empreintes = self._extraire_hashes(texte)
        # Garde-fou : une page d'erreur HTML, une réponse vide ou un fichier
        # tronqué ne doivent JAMAIS écraser une base saine.
        if len(empreintes) < 10:
            return {"status": "erreur",
                    "raison": f"réponse invalide ou tronquée ({len(empreintes)} empreinte(s) "
                              f"exploitable(s)) — base existante conservée",
                    "conservees": self._compter_hashes()}

        entete = (f"# Base d'empreintes ANTI-ZEEVIRIUS\n"
                  f"# Source : {HASH_SOURCE}\n"
                  f"# Mise à jour : {time.strftime('%Y-%m-%d %H:%M:%S')}\n")
        self._remplacer(self.hashes_path, entete + "\n".join(sorted(empreintes)) + "\n")

        etat = self._state()
        etat["hashes"] = {"last_success": time.time(),
                          "etag": entetes.get("ETag"),
                          "last_modified": entetes.get("Last-Modified"),
                          "count": len(empreintes)}
        self._save_state(etat)
        return {"status": "ok", "ajoutees": len(empreintes)}

    @staticmethod
    def _extraire_hashes(texte: str) -> List[str]:
        out = []
        for ligne in texte.splitlines():
            ligne = ligne.strip().strip('"')
            if not ligne or ligne.startswith("#"):
                continue
            if _SHA256_RE.match(ligne):
                out.append(ligne.lower())
        return out

    def _compter_hashes(self) -> int:
        try:
            return len(self._extraire_hashes(self.hashes_path.read_text(encoding="utf-8")))
        except OSError:
            return 0

    # ── Règles YARA ────────────────────────────────────────────────────────
    def update_yara_rules(self, force: bool = False,
                          sources: Optional[List[str]] = None) -> Dict:
        """Rapatrie, filtre règle par règle, puis installe les règles YARA."""
        try:
            import yara
        except ImportError:
            return {"status": "erreur",
                    "raison": "yara-python absent — couche YARA indisponible"}

        if not force and self._trop_recent("yara"):
            return {"status": "ignore", "raison": "mise à jour trop récente"}

        sources = sources or YARA_SOURCES
        imports: List[str] = []
        candidates: List[Tuple[str, str]] = []
        echecs_reseau: List[str] = []

        for url in sources:
            try:
                texte, _ = self._telecharger(url, f"yara:{url}")
            except requests.RequestException as e:
                echecs_reseau.append(f"{url.rsplit('/', 1)[-1]} : {e}")
                continue
            if texte is None:
                continue
            imp, regles = split_yara_rules(texte)
            imports.extend(imp)
            candidates.extend(regles)

        if not candidates:
            return {"status": "erreur",
                    "raison": "aucune règle récupérée — base existante conservée",
                    "echecs_reseau": echecs_reseau}

        imports = sorted(set(i.strip() for i in imports))
        prefixe = "\n".join(imports) + ("\n\n" if imports else "")

        def compile_ok(texte_source: str) -> bool:
            try:
                yara.compile(source=texte_source, externals=YARA_EXTERNALS)
                return True
            except Exception:
                return False

        # Étape 1 — chaque règle est compilée SEULE. Une règle invalide est
        # écartée sans emporter les autres avec elle : c'est la raison d'être
        # de ce module, puisque YARA compile un fichier en bloc.
        retenues: List[Tuple[str, str]] = []
        vues = set()
        ecartees: List[Tuple[str, str]] = []
        for nom, texte_regle in candidates:
            if nom in vues:            # doublon entre deux fichiers sources
                continue
            try:
                yara.compile(source=prefixe + texte_regle, externals=YARA_EXTERNALS)
            except Exception as e:     # yara.SyntaxError et apparentés
                ecartees.append((nom, str(e).split("\n")[0][:120]))
                continue
            vues.add(nom)
            retenues.append((nom, texte_regle))

        if not retenues:
            return {"status": "erreur",
                    "raison": "aucune règle n'a compilé — base existante conservée",
                    "ecartees": len(ecartees), "echecs_reseau": echecs_reseau}

        # Étape 2 — l'assemblage complet doit compiler lui aussi.
        #
        # Valider chaque règle isolément NE SUFFIT PAS : mesuré sur les sources
        # réelles, certaines règles compilent seules et cassent pourtant
        # l'assemblage (interaction entre règles, identifiants de chaînes,
        # constructions que le contexte rend ambiguës). Une règle correcte peut
        # donc désarmer tout le fichier — exactement ce que ce module existe
        # pour empêcher.
        #
        # Si l'assemblage échoue, on le reconstruit règle par règle en ne
        # gardant que celles qui préservent la compilation. Ce coût n'est payé
        # qu'en cas de problème réel.
        rejetees_assemblage: List[str] = []
        corps = "\n\n".join(t for _, t in retenues)
        if not compile_ok(prefixe + corps + "\n"):
            gardees: List[Tuple[str, str]] = []
            for nom, texte_regle in retenues:
                essai = gardees + [(nom, texte_regle)]
                if compile_ok(prefixe + "\n\n".join(t for _, t in essai) + "\n"):
                    gardees = essai
                else:
                    rejetees_assemblage.append(nom)
            if not gardees:
                return {"status": "erreur",
                        "raison": "aucune règle ne compile en assemblage — base conservée",
                        "echecs_reseau": echecs_reseau}
            retenues = gardees
            corps = "\n\n".join(t for _, t in retenues)

        entete = (f"// Règles YARA ANTI-ZEEVIRIUS\n"
                  f"// Sources : {', '.join(u.rsplit('/', 1)[-1] for u in sources)}\n"
                  f"// Mise à jour : {time.strftime('%Y-%m-%d %H:%M:%S')}\n"
                  f"// {len(retenues)} règle(s) retenue(s) ; "
                  f"{len(ecartees)} écartée(s) à la compilation isolée, "
                  f"{len(rejetees_assemblage)} à l'assemblage\n\n")
        contenu = entete + prefixe + corps + "\n"

        # Garantie finale : on n'écrit jamais un fichier qui ne compile pas.
        if not compile_ok(contenu):
            return {"status": "erreur",
                    "raison": "l'assemblage final ne compile pas — base conservée",
                    "echecs_reseau": echecs_reseau}

        self._remplacer(self.rules_path, contenu)
        etat = self._state()
        etat["yara"] = {"last_success": time.time(), "count": len(retenues)}
        self._save_state(etat)

        return {"status": "ok", "retenues": len(retenues), "ecartees": len(ecartees),
                "rejetees_assemblage": rejetees_assemblage,
                "detail_ecartees": ecartees[:10], "echecs_reseau": echecs_reseau}

    # ── Tout ───────────────────────────────────────────────────────────────
    def update_all(self, force: bool = False) -> Dict:
        return {"hashes": self.update_hashes(force=force),
                "yara": self.update_yara_rules(force=force)}
