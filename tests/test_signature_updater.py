"""
Tests de optimizer/signature_updater.py.

Aucun test ne touche au réseau : `requests` est mocké et les jeux d'essai sont
locaux. Ce qui est vérifié ici n'est pas « le téléchargement fonctionne » mais
« une source défaillante ne peut pas désarmer l'antivirus » — c'est le risque
réel d'un module de mise à jour automatique.
"""

import json
from pathlib import Path
from unittest.mock import Mock

import pytest

from optimizer.signature_updater import SignatureUpdater, split_yara_rules

yara = pytest.importorskip("yara", reason="yara-python requis pour la couche YARA")


REGLE_VALIDE = '''rule Regle_Valide
{
    strings:
        $a = "quelque chose de suspect"
    condition:
        $a
}'''

REGLE_INVALIDE = '''rule Regle_Invalide
{
    strings:
        $a = "x"
    condition:
        cette_fonction_nexiste_pas($a)
}'''

# Reproduit le piège rencontré sur les sources réelles : une expression
# régulière contenant `\\/\\/`, que tout découpage à base de détection de
# commentaires prend pour un début de `//`.
REGLE_AVEC_REGEX_SLASHES = r'''rule Regle_Avec_Regex
{
    strings:
        $u = /https?:\/\/[a-z]+\.example\.com/
    condition:
        $u
}'''


def _reponse(texte, status=200, headers=None):
    r = Mock()
    r.status_code = status
    r.text = texte
    r.headers = headers or {}
    r.raise_for_status = Mock()
    return r


def _session(reponses):
    """Session mockée : rend les réponses dans l'ordre, ou une seule en boucle."""
    s = Mock()
    if isinstance(reponses, list):
        s.get = Mock(side_effect=reponses)
    else:
        s.get = Mock(return_value=reponses)
    return s


@pytest.fixture
def updater(tmp_path):
    return SignatureUpdater(signatures_dir=tmp_path, session=Mock())


# ═══════════════════════════════════════════════════════════════════
# Découpage des règles
# ═══════════════════════════════════════════════════════════════════
class TestDecoupageDesRegles:
    def test_chaque_regle_est_isolee(self):
        source = "\n\n".join([REGLE_VALIDE, REGLE_AVEC_REGEX_SLASHES])
        _, regles = split_yara_rules(source)
        assert [n for n, _ in regles] == ["Regle_Valide", "Regle_Avec_Regex"]

    def test_une_regex_contenant_des_slashes_ne_fusionne_pas_les_blocs(self):
        """Non-régression du défaut mesuré sur gen_webshells.yar : le `\\/\\/`
        d'une expression régulière était pris pour un début de commentaire,
        la fin de ligne était sautée, une accolade fermante perdue, et le bloc
        avalait les règles suivantes — 11 blocs sur 57 étaient fusionnés."""
        source = "\n\n".join([REGLE_AVEC_REGEX_SLASHES, REGLE_VALIDE])
        _, regles = split_yara_rules(source)
        assert len(regles) == 2
        for _, texte in regles:
            assert texte.count("rule ") == 1, "un bloc a avalé la règle suivante"

    def test_les_imports_sont_extraits(self):
        source = 'import "pe"\nimport "math"\n\n' + REGLE_VALIDE
        imports, regles = split_yara_rules(source)
        assert len(imports) == 2 and len(regles) == 1


# ═══════════════════════════════════════════════════════════════════
# Règles YARA : le filtrage doit sauver la couche entière
# ═══════════════════════════════════════════════════════════════════
class TestFiltrageYara:
    def test_une_regle_invalide_n_emporte_pas_les_valides(self, tmp_path):
        """LE point du module : YARA compile un fichier en bloc, donc une seule
        règle cassée désarmerait toute la couche. Elle doit être écartée."""
        source = "\n\n".join([REGLE_VALIDE, REGLE_INVALIDE, REGLE_AVEC_REGEX_SLASHES])
        u = SignatureUpdater(signatures_dir=tmp_path, session=_session(_reponse(source)))

        res = u.update_yara_rules(force=True, sources=["http://exemple/test.yar"])

        assert res["status"] == "ok"
        assert res["retenues"] == 2
        assert res["ecartees"] == 1
        ecrit = (tmp_path / "rules.yar").read_text(encoding="utf-8")
        assert "Regle_Valide" in ecrit and "Regle_Invalide" not in ecrit

    def test_le_fichier_ecrit_compile_toujours(self, tmp_path):
        source = "\n\n".join([REGLE_VALIDE, REGLE_INVALIDE])
        u = SignatureUpdater(signatures_dir=tmp_path, session=_session(_reponse(source)))
        u.update_yara_rules(force=True, sources=["http://exemple/test.yar"])

        yara.compile(source=(tmp_path / "rules.yar").read_text(encoding="utf-8"),
                     externals={"filename": "", "filepath": "", "extension": "",
                                "filetype": "", "owner": ""})

    def test_aucune_regle_valide_laisse_la_base_intacte(self, tmp_path):
        base = tmp_path / "rules.yar"
        base.write_text(REGLE_VALIDE, encoding="utf-8")
        u = SignatureUpdater(signatures_dir=tmp_path, session=_session(_reponse(REGLE_INVALIDE)))

        res = u.update_yara_rules(force=True, sources=["http://exemple/test.yar"])

        assert res["status"] == "erreur"
        assert base.read_text(encoding="utf-8") == REGLE_VALIDE, "base écrasée !"

    def test_hors_ligne_la_base_reste_utilisable(self, tmp_path):
        import requests as _rq
        base = tmp_path / "rules.yar"
        base.write_text(REGLE_VALIDE, encoding="utf-8")
        s = Mock()
        s.get = Mock(side_effect=_rq.RequestException("pas de réseau"))
        u = SignatureUpdater(signatures_dir=tmp_path, session=s)

        res = u.update_yara_rules(force=True, sources=["http://exemple/test.yar"])

        assert res["status"] == "erreur"
        assert base.read_text(encoding="utf-8") == REGLE_VALIDE
        yara.compile(source=base.read_text(encoding="utf-8"))


# ═══════════════════════════════════════════════════════════════════
# Empreintes : une réponse douteuse ne doit rien écraser
# ═══════════════════════════════════════════════════════════════════
class TestEmpreintes:
    EMPREINTES = "\n".join(f"{i:064x}" for i in range(1, 51))

    def test_mise_a_jour_nominale(self, tmp_path):
        u = SignatureUpdater(signatures_dir=tmp_path, session=_session(_reponse(self.EMPREINTES)))
        res = u.update_hashes(force=True)
        assert res["status"] == "ok" and res["ajoutees"] == 50

    def test_page_html_ne_remplace_pas_la_base(self, tmp_path):
        """Cas très concret : le service répond une page d'erreur ou une page
        de maintenance. Sans garde-fou, la base d'empreintes serait remplacée
        par du HTML et l'antivirus ne détecterait plus rien."""
        base = tmp_path / "malicious_hashes.txt"
        base.write_text(self.EMPREINTES, encoding="utf-8")
        html = "<!doctype html><html><body>503 Service Unavailable</body></html>"
        u = SignatureUpdater(signatures_dir=tmp_path, session=_session(_reponse(html)))

        res = u.update_hashes(force=True)

        assert res["status"] == "erreur"
        assert base.read_text(encoding="utf-8") == self.EMPREINTES

    def test_reponse_tronquee_refusee(self, tmp_path):
        base = tmp_path / "malicious_hashes.txt"
        base.write_text(self.EMPREINTES, encoding="utf-8")
        tronque = "\n".join(f"{i:064x}" for i in range(1, 4))   # 3 seulement
        u = SignatureUpdater(signatures_dir=tmp_path, session=_session(_reponse(tronque)))

        assert u.update_hashes(force=True)["status"] == "erreur"
        assert base.read_text(encoding="utf-8") == self.EMPREINTES

    def test_lignes_malformees_ignorees(self, tmp_path):
        melange = self.EMPREINTES + "\n# commentaire\npas-une-empreinte\n\nzz\n"
        u = SignatureUpdater(signatures_dir=tmp_path, session=_session(_reponse(melange)))
        assert u.update_hashes(force=True)["ajoutees"] == 50

    def test_304_non_modifie_n_est_pas_une_erreur(self, tmp_path):
        base = tmp_path / "malicious_hashes.txt"
        base.write_text(self.EMPREINTES, encoding="utf-8")
        u = SignatureUpdater(signatures_dir=tmp_path,
                             session=_session(_reponse("", status=304)))
        res = u.update_hashes(force=True)
        assert res["status"] == "inchange"
        assert base.read_text(encoding="utf-8") == self.EMPREINTES


# ═══════════════════════════════════════════════════════════════════
# Remplacement atomique et retour en arrière
# ═══════════════════════════════════════════════════════════════════
class TestRemplacementAtomique:
    def test_l_ancienne_base_est_conservee_en_bak(self, tmp_path):
        base = tmp_path / "malicious_hashes.txt"
        base.write_text("ancienne base\n", encoding="utf-8")
        empreintes = "\n".join(f"{i:064x}" for i in range(1, 51))
        u = SignatureUpdater(signatures_dir=tmp_path, session=_session(_reponse(empreintes)))

        u.update_hashes(force=True)

        bak = tmp_path / "malicious_hashes.txt.bak"
        assert bak.exists() and bak.read_text(encoding="utf-8") == "ancienne base\n"

    def test_aucun_fichier_temporaire_ne_subsiste(self, tmp_path):
        empreintes = "\n".join(f"{i:064x}" for i in range(1, 51))
        u = SignatureUpdater(signatures_dir=tmp_path, session=_session(_reponse(empreintes)))
        u.update_hashes(force=True)
        assert list(tmp_path.glob("*.tmp")) == []


# ═══════════════════════════════════════════════════════════════════
# Respect des sources publiques
# ═══════════════════════════════════════════════════════════════════
class TestRespectDesSources:
    def test_intervalle_minimal_respecte(self, tmp_path):
        """Ces services sont gratuits et bénévoles : on ne les sollicite pas
        à chaque lancement de l'application."""
        empreintes = "\n".join(f"{i:064x}" for i in range(1, 51))
        s = _session(_reponse(empreintes))
        u = SignatureUpdater(signatures_dir=tmp_path, session=s)

        assert u.update_hashes(force=True)["status"] == "ok"
        appels_apres_premiere = s.get.call_count
        res = u.update_hashes(force=False)          # tout de suite après

        assert res["status"] == "ignore"
        assert s.get.call_count == appels_apres_premiere, "source resollicitée inutilement"

    def test_user_agent_identifiable_et_cache_conditionnel(self, tmp_path):
        empreintes = "\n".join(f"{i:064x}" for i in range(1, 51))
        s = _session(_reponse(empreintes, headers={"ETag": '"abc"'}))
        u = SignatureUpdater(signatures_dir=tmp_path, session=s)
        u.update_hashes(force=True)

        entetes = s.get.call_args.kwargs["headers"]
        assert "ANTI-ZEEVIRIUS" in entetes["User-Agent"]

        # Deuxième passage : l'ETag mémorisé doit être renvoyé.
        u.update_hashes(force=True)
        assert s.get.call_args.kwargs["headers"].get("If-None-Match") == '"abc"'

    def test_etat_persiste_sur_disque(self, tmp_path):
        empreintes = "\n".join(f"{i:064x}" for i in range(1, 51))
        u = SignatureUpdater(signatures_dir=tmp_path, session=_session(_reponse(empreintes)))
        u.update_hashes(force=True)
        etat = json.loads((tmp_path / "update_state.json").read_text(encoding="utf-8"))
        assert etat["hashes"]["count"] == 50
