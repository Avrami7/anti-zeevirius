#!/usr/bin/env python3
"""
lint_iss.py — vérifier le script Inno Setup sans Windows.

Inno Setup ne se compile que sous Windows. Sur ce projet, développé sous Linux,
la moindre faute dans `installer.iss` n'apparaissait donc qu'après trois minutes
de build sur un runner GitHub — et tout à la fin, une fois les tests passés et
l'exécutable produit. Le premier build a échoué exactement ainsi.

Ce vérificateur attrape statiquement les fautes qui ont réellement cassé le
build, ou qui le casseront ensuite. Il ne remplace pas le compilateur : il
supprime la boucle de retour de trois minutes pour les erreurs les plus bêtes.

Usage :
    python packaging/lint_iss.py [chemin/vers/installer.iss]

Code de retour 0 si tout va bien, 1 sinon.
"""

from __future__ import annotations

import re
import sys
from pathlib import Path
from typing import List, Tuple

RACINE = Path(__file__).resolve().parent.parent


class Probleme:
    def __init__(self, ligne: int, regle: str, message: str, extrait: str = ""):
        self.ligne, self.regle, self.message, self.extrait = ligne, regle, message, extrait

    def __str__(self) -> str:
        s = f"  L{self.ligne:<4} [{self.regle}] {self.message}"
        if self.extrait:
            s += f"\n         > {self.extrait.strip()[:88]}"
        return s


def _sections(lignes: List[str]) -> List[Tuple[int, str]]:
    """Associe chaque ligne à sa section ([Files], [Code]...)."""
    courante = ""
    out = []
    for i, l in enumerate(lignes, 1):
        m = re.match(r"^\s*\[([A-Za-z]+)\]\s*$", l)
        if m:
            courante = m.group(1).lower()
        out.append((i, courante))
    return out


def verifier_commentaires_pascal(lignes: List[str], sections) -> List[Probleme]:
    """Un commentaire Pascal { ... } se termine à la PREMIÈRE accolade fermante.

    Les accolades ne s'imbriquent pas. Un commentaire qui cite une constante
    Inno — {app}, {localappdata}, {autopf} — se referme donc au milieu, et le
    texte qui suit est interprété comme du code.

    C'est exactement ce qui a fait échouer le premier build :
    « Unknown identifier 'est' », le mot français qui suivait {localappdata}.
    """
    problemes = []
    dans_bloc = False
    for (i, section), brute in zip(sections, lignes):
        # HORS de [Code], une accolade n'est PAS un commentaire : c'est une
        # constante Inno ({app}, {cm:...}), ou `{{` qui note une accolade
        # littérale. Analyser tout le fichier produisait de fausses alertes sur
        # AppId={{GUID} et sur les chaînes {cm:...} — un vérificateur qui fait
        # échouer un build sain est pire que pas de vérificateur du tout.
        if section != "code":
            continue
        l = brute
        if not dans_bloc:
            pos = -1
            for j, c in enumerate(l):
                if c != "{":
                    continue
                suivant = l[j + 1] if j + 1 < len(l) else ""
                if suivant in ("#", "{"):      # préprocesseur, ou accolade littérale
                    continue
                pos = j
                break
            if pos == -1:
                continue
            reste = l[pos + 1:]
            fin = reste.find("}")
            corps = reste if fin == -1 else reste[:fin]
            if "{" in corps:
                problemes.append(Probleme(
                    i, "accolade-imbriquee",
                    "commentaire Pascal contenant une accolade ouvrante : il se "
                    "refermera à la première `}` et la suite deviendra du code. "
                    "Utiliser un commentaire `//`.",
                    brute))
            if fin == -1:
                dans_bloc = True
        else:
            if "}" in l:
                dans_bloc = False
    if dans_bloc:
        problemes.append(Probleme(len(lignes), "commentaire-non-ferme",
                                  "un commentaire { ... } n'est jamais refermé"))
    return problemes


def _hors_commentaire(l: str) -> str:
    """Retire le commentaire de ligne `//` situé hors d'une chaîne."""
    dans_chaine = False
    for i, c in enumerate(l):
        if c == "'":
            dans_chaine = not dans_chaine
        elif c == "/" and not dans_chaine and i + 1 < len(l) and l[i + 1] == "/":
            return l[:i]
    return l


def verifier_apostrophes(lignes: List[str], sections) -> List[Probleme]:
    """En Pascal, une apostrophe dans une chaîne doit être doublée.

    Le français en est truffé — « l'utilisateur », « n'existe » — et une seule
    apostrophe non doublée referme la chaîne, transformant la suite en code.
    Seules les lignes de code sont examinées : dans un commentaire de bloc,
    les apostrophes sont inoffensives.
    """
    problemes = []
    dans_bloc = False
    for (i, section), brute in zip(sections, lignes):
        l = brute
        if dans_bloc:
            if "}" in l:
                dans_bloc = False
            continue
        # Détection sommaire d'ouverture de commentaire de bloc non refermé.
        pos = l.find("{")
        if pos != -1 and "}" not in l[pos:] and not l[pos:pos + 2] == "{#":
            dans_bloc = True
            continue
        if section != "code":
            continue
        code = _hors_commentaire(l)
        if code.count("'") % 2 != 0:
            problemes.append(Probleme(
                i, "apostrophe-impaire",
                "nombre impair d'apostrophes : une apostrophe française non "
                "doublée referme la chaîne (écrire '' dans le texte).",
                brute))
    return problemes


def verifier_fichiers_references(lignes: List[str], sections) -> List[Probleme]:
    """Chaque `Source:` de [Files] et chaque icône doit exister, ou être produit
    par le build. Un chemin erroné ne se voit qu'au moment de la compilation."""
    problemes = []
    produits_par_le_build = ("dist\\", "dist/", "{app}", "{tmp}")
    for (i, section), l in zip(sections, lignes):
        for cle in ("Source:", "SetupIconFile=", "UninstallDisplayIcon="):
            if cle not in l:
                continue
            m = re.search(re.escape(cle) + r'\s*"?([^";]+)"?', l)
            if not m:
                continue
            brut = m.group(1).strip().strip('"')
            if brut.startswith("{") or any(p in brut for p in produits_par_le_build):
                continue          # constante Inno, ou artefact du build
            chemin = (RACINE / "packaging" / brut.replace("\\", "/")).resolve()
            if not chemin.exists():
                problemes.append(Probleme(
                    i, "fichier-absent",
                    f"chemin introuvable dans le dépôt : {brut}", l))
    return problemes


def verifier_sections_obligatoires(lignes: List[str]) -> List[Probleme]:
    texte = "\n".join(lignes)
    problemes = []
    for section in ("[Setup]", "[Files]"):
        if section not in texte:
            problemes.append(Probleme(0, "section-manquante",
                                      f"section {section} absente"))
    if "AppName" not in texte:
        problemes.append(Probleme(0, "directive-manquante", "AppName absent de [Setup]"))
    return problemes


def lint(chemin: Path) -> List[Probleme]:
    lignes = chemin.read_text(encoding="utf-8-sig").split("\n")
    sections = _sections(lignes)
    return (verifier_commentaires_pascal(lignes, sections)
            + verifier_apostrophes(lignes, sections)
            + verifier_fichiers_references(lignes, sections)
            + verifier_sections_obligatoires(lignes))


def main(argv=None) -> int:
    argv = argv if argv is not None else sys.argv[1:]
    chemin = Path(argv[0]) if argv else (RACINE / "packaging" / "installer.iss")
    if not chemin.is_file():
        print(f"[ERREUR] Fichier introuvable : {chemin}", file=sys.stderr)
        return 1

    problemes = lint(chemin)
    print(f"Vérification de {chemin.name} — {len(chemin.read_text(encoding='utf-8-sig').splitlines())} lignes")
    if not problemes:
        print("  Aucun problème détecté.")
        return 0

    print(f"\n{len(problemes)} problème(s) :\n")
    for p in problemes:
        print(p)
    print("\nCes fautes feraient échouer la compilation Inno Setup sur le runner "
          "Windows, après plusieurs minutes de build.")
    return 1


if __name__ == "__main__":
    raise SystemExit(main())
