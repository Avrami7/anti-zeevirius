"""
Tests de packaging/lint_iss.py.

Deux exigences, également importantes :

  * il doit ATTRAPER la faute qui a réellement cassé le premier build ;
  * il ne doit PAS crier au loup sur le fichier qui compile — un vérificateur
    qui fait échouer un build sain est pire que pas de vérificateur du tout.
    Une première version signalait `AppId={{GUID}` et les chaînes `{cm:...}` :
    ces tests figent la correction.
"""

import sys
from pathlib import Path

import pytest

RACINE = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(RACINE / "packaging"))

import lint_iss  # noqa: E402


def _ecrire(tmp_path: Path, contenu: str) -> Path:
    f = tmp_path / "installer.iss"
    f.write_text(contenu, encoding="utf-8")
    return f


def _regles(problemes):
    return {p.regle for p in problemes}


EN_TETE = """[Setup]
AppName=ANTI-ZEEVIRIUS
AppId={{A0457777-A576-4ECF-B72C-93188E638F14}
DefaultDirName={autopf}\\ANTI-ZEEVIRIUS

[Files]

[Code]
"""


class TestLeVraiBug:
    def test_accolade_imbriquee_dans_un_commentaire_est_detectee(self, tmp_path):
        """La faute exacte du premier build : un commentaire Pascal citant une
        constante Inno se referme sur l'accolade de la constante, et le texte
        qui suit devient du code — « Unknown identifier 'est' »."""
        f = _ecrire(tmp_path, EN_TETE + """
procedure Test();
begin
  { {localappdata} est résolu dans le profil de l'utilisateur. }
  Exit;
end;
""")
        assert "accolade-imbriquee" in _regles(lint_iss.lint(f))

    def test_commentaire_non_ferme_detecte(self, tmp_path):
        f = _ecrire(tmp_path, EN_TETE + """
procedure Test();
begin
  { ce commentaire ne se referme jamais
  Exit;
end;
""")
        assert "commentaire-non-ferme" in _regles(lint_iss.lint(f))

    def test_apostrophe_francaise_non_doublee_detectee(self, tmp_path):
        f = _ecrire(tmp_path, EN_TETE + """
procedure Test();
begin
  MsgBox('L'application va se fermer', mbInformation, MB_OK);
end;
""")
        assert "apostrophe-impaire" in _regles(lint_iss.lint(f))


class TestAucuneFausseAlerte:
    """Le fichier réel du projet compile : il doit passer sans une seule alerte."""

    def test_le_script_du_projet_est_propre(self):
        f = RACINE / "packaging" / "installer.iss"
        if not f.is_file():
            pytest.skip("installer.iss absent")
        problemes = lint_iss.lint(f)
        assert problemes == [], "\n".join(str(p) for p in problemes)

    def test_appid_avec_accolade_doublee_accepte(self, tmp_path):
        """`{{` note une accolade littérale dans une directive, ce n'est pas
        un commentaire. Signalé à tort par la première version."""
        f = _ecrire(tmp_path, EN_TETE + """
procedure Test();
begin
  Exit;
end;
""")
        assert "accolade-imbriquee" not in _regles(lint_iss.lint(f))

    def test_constantes_inno_hors_section_code_acceptees(self, tmp_path):
        """Hors de [Code], une accolade est une constante, jamais un
        commentaire : {app}, {cm:...}, {#preprocesseur}."""
        f = _ecrire(tmp_path, """[Setup]
AppName=Test
DefaultDirName={autopf}\\Test

[Files]
Source: "..\\dist\\Test.exe"; DestDir: "{app}"

[Run]
Description: "{cm:LaunchProgram,{#StringChange(AppName, '&', '&&')}}"

[Code]
""")
        assert _regles(lint_iss.lint(f)) <= set(), \
            "aucune alerte attendue hors de [Code]"

    def test_apostrophes_dans_un_commentaire_de_bloc_ignorees(self, tmp_path):
        """Dans un commentaire, les apostrophes françaises sont inoffensives —
        c'est du texte, pas une chaîne Pascal."""
        f = _ecrire(tmp_path, EN_TETE + """
{ Un désinstalleur d'antivirus qui efface la quarantaine détruit
  des fichiers que l'utilisateur croyait à l'abri. }
procedure Test();
begin
  Exit;
end;
""")
        assert "apostrophe-impaire" not in _regles(lint_iss.lint(f))

    def test_apostrophe_doublee_correctement_acceptee(self, tmp_path):
        f = _ecrire(tmp_path, EN_TETE + """
procedure Test();
begin
  MsgBox('L''application va se fermer', mbInformation, MB_OK);
end;
""")
        assert "apostrophe-impaire" not in _regles(lint_iss.lint(f))


class TestFichiersReferences:
    def test_source_introuvable_detectee(self, tmp_path):
        f = _ecrire(tmp_path, """[Setup]
AppName=Test

[Files]
Source: "fichier-qui-nexiste-pas.txt"; DestDir: "{app}"

[Code]
""")
        assert "fichier-absent" in _regles(lint_iss.lint(f))

    def test_artefact_du_build_non_signale(self, tmp_path):
        """`..\\dist\\...` n'existe pas dans le dépôt : il est produit par
        PyInstaller pendant le build. Le signaler serait une fausse alerte."""
        f = _ecrire(tmp_path, """[Setup]
AppName=Test

[Files]
Source: "..\\dist\\ANTI-ZEEVIRIUS.exe"; DestDir: "{app}"

[Code]
""")
        assert "fichier-absent" not in _regles(lint_iss.lint(f))


class TestStructure:
    def test_sections_obligatoires_verifiees(self, tmp_path):
        f = _ecrire(tmp_path, "; script vide\n")
        regles = _regles(lint_iss.lint(f))
        assert "section-manquante" in regles and "directive-manquante" in regles

    def test_code_retour_non_nul_si_probleme(self, tmp_path, capsys):
        f = _ecrire(tmp_path, "; script vide\n")
        assert lint_iss.main([str(f)]) == 1

    def test_code_retour_nul_si_propre(self, capsys):
        f = RACINE / "packaging" / "installer.iss"
        if not f.is_file():
            pytest.skip("installer.iss absent")
        assert lint_iss.main([str(f)]) == 0
