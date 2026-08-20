"""
test_incident_mode.py
Couvre security/incident_mode.py — le bouton d'urgence.

Ce que ces tests vérifient POUR DE VRAI, sans mock :

- le gel et le dégel de processus (SIGSTOP/SIGCONT via psutil), sur des
  processus jetables créés par le test lui-même — jamais sur un processus
  qu'il n'a pas lancé ;
- la liste noire, y compris le cas retors où le PLAN annonce un nom anodin
  alors que le processus derrière le PID s'appelle `csrss.exe` (le test crée
  un vrai exécutable portant ce nom) ;
- l'état persistant : fichier réellement écrit, réellement relu par une
  seconde instance, réellement conservé quand le rétablissement échoue ;
- le rapport : fichiers réellement écrits, contenu vérifié ;
- le repérage des fichiers modifiés récemment, sur une arborescence réelle.

Ce qui est MOCKÉ, faute de Windows : `netsh` et `vssadmin`. Le module reçoit
un `runner` injecté, et les tests vérifient les commandes émises, leur ordre,
et le comportement du module face à leurs codes de retour. Aucune règle de
pare-feu, aucun cliché VSS n'est créé nulle part.

Aucun test ne touche au dossier de données de l'utilisateur : `etat_path` et
`dossier_rapports` sont dirigés vers `tmp_path`, et le test qui vérifie
l'emplacement PAR DÉFAUT passe par ANTIZEEVIRIUS_DATA_DIR.
"""

import contextlib
import json
import os
import shutil
import subprocess
import sys
import time
from pathlib import Path
from unittest.mock import patch

import pytest

psutil = pytest.importorskip("psutil", reason="psutil requis pour le mode incident")
incident_mode = pytest.importorskip("security.incident_mode")

IncidentMode = incident_mode.IncidentMode
NOM_REGLE = incident_mode.NOM_REGLE
PROCESSUS_CRITIQUES = incident_mode.PROCESSUS_CRITIQUES
PROCESSUS_SENSIBLES = incident_mode.PROCESSUS_SENSIBLES


# ── Outillage ───────────────────────────────────────────────────────────────

def _resultat(commande, code=0, sortie="", erreur=""):
    return subprocess.CompletedProcess(list(commande), code, sortie, erreur)


class RunnerFactice:
    """Remplace netsh / vssadmin / powershell. Journalise tout, ne lance rien.

    `reponses` associe un fragment de commande à un code de retour et une
    sortie : c'est ainsi qu'on simule « pas de droits admin », « règle déjà
    absente » ou « vssadmin ne sait pas créer sur édition client ».
    """

    def __init__(self, journal=None, reponses=None, defaut=(0, "Ok.")):
        self.journal = journal if journal is not None else []
        self.reponses = reponses or {}
        self.defaut = defaut

    def __call__(self, commande, timeout=None):
        ligne = " ".join(commande)
        self.journal.append(ligne)
        for fragment, (code, sortie) in self.reponses.items():
            if fragment in ligne:
                return _resultat(commande, code, sortie)
        return _resultat(commande, self.defaut[0], self.defaut[1])

    @property
    def commandes(self):
        return [c for c in self.journal if not c.startswith("#")]


class ShieldEspion:
    """Double de RansomwareShield : journalise les appels au vrai mécanisme
    de gel, pour vérifier qu'on l'appelle bien LUI (et pas un second
    mécanisme maison), sans figer quoi que ce soit."""

    def __init__(self, journal=None, candidats=(), suspend_ok=True, resume_ok=True):
        self.journal = journal if journal is not None else []
        self.candidats = list(candidats)
        self.suspend_ok = suspend_ok
        self.resume_ok = resume_ok

    def find_suspicious_processes(self, top_n=5):
        self.journal.append(f"# candidats top_n={top_n}")
        return list(self.candidats)

    def suspend_process(self, pid):
        self.journal.append(f"# suspend {pid}")
        return self.suspend_ok

    def resume_process(self, pid):
        self.journal.append(f"# resume {pid}")
        return self.resume_ok


@pytest.fixture
def proc_jetable():
    """Fabrique des processus JETABLES, créés et détruits par le test.

    Règle absolue : on ne gèle jamais un processus qu'on n'a pas lancé
    soi-même. Tous sont relancés puis tués en fin de test, y compris si
    l'assertion a échoué au milieu.
    """
    crees = []

    def _creer(nom_executable=None, duree=60):
        if nom_executable is None:
            proc = subprocess.Popen(
                [sys.executable, "-c", f"import time; time.sleep({duree})"],
                stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL)
        else:
            source = shutil.which("sleep")
            if not source:
                pytest.skip("/bin/sleep introuvable : impossible de fabriquer "
                            "un processus portant un nom choisi")
            copie = Path(nom_executable)
            shutil.copy2(source, copie)
            copie.chmod(0o755)
            proc = subprocess.Popen([str(copie), str(duree)],
                                    stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL)
        crees.append(proc)
        # On laisse le processus démarrer : psutil doit pouvoir lire son nom.
        for _ in range(100):
            try:
                if psutil.Process(proc.pid).name():
                    break
            except psutil.Error:
                pass
            time.sleep(0.01)
        return proc

    yield _creer

    for proc in crees:
        try:
            p = psutil.Process(proc.pid)
            if p.status() == psutil.STATUS_STOPPED:
                p.resume()
        except psutil.Error:
            pass
        try:
            proc.kill()
            proc.wait(timeout=5)
        except Exception:
            pass


@pytest.fixture
def fabrique_mode(tmp_path):
    """IncidentMode configuré comme s'il tournait sous Windows avec les
    droits administrateur — la seule façon d'exercer la séquence complète
    depuis Linux."""
    compteur = {"n": 0}

    def _creer(runner=None, shield=None, plateforme="Windows", admin=True,
               dossier=None, partage_etat=False):
        if dossier is None:
            if partage_etat:
                dossier = tmp_path / "incident"
            else:
                compteur["n"] += 1
                dossier = tmp_path / f"incident{compteur['n']}"
        dossier.mkdir(parents=True, exist_ok=True)
        return IncidentMode(
            runner=runner if runner is not None else RunnerFactice(),
            etat_path=dossier / "incident_state.json",
            dossier_rapports=dossier,
            shield=shield,
            plateforme=plateforme,
            admin=admin,
        )

    return _creer


@contextlib.contextmanager
def volume_factice(lettre="C:"):
    """Un chemin POSIX n'a pas de lettre de lecteur, et VSS travaille par
    volume : sans ce mensonge minimal, `vssadmin` ne serait jamais atteint
    depuis Linux. C'est la SEULE chose que ce contexte simule — les dossiers
    passés au module restent de vrais dossiers de tmp_path."""
    with patch.object(IncidentMode, "_lecteurs", staticmethod(lambda dossiers: [lettre])):
        yield


def _plan(processus=(), dossiers=()):
    """Plan minimal, comme celui que produit preparer() une fois filtré par
    l'utilisateur dans l'interface."""
    return {
        "processus": list(processus),
        "sauvegarde": {"dossiers": [str(d) for d in dossiers]},
    }


# ── 1. Plan avant action ────────────────────────────────────────────────────

class TestPreparer:

    def test_le_plan_ne_touche_a_rien(self, fabrique_mode):
        """preparer() est en lecture seule : aucune commande d'écriture."""
        runner = RunnerFactice(reponses={"show rule": (1, "No rules match the specified criteria.")})
        mode = fabrique_mode(runner=runner, shield=ShieldEspion())
        mode.preparer()
        for commande in runner.commandes:
            assert "add rule" not in commande
            assert "delete rule" not in commande
            assert "vssadmin" not in commande
        assert not mode.etat_path.exists(), "preparer() ne doit rien écrire"

    def test_le_plan_annonce_les_quatre_etapes(self, fabrique_mode):
        mode = fabrique_mode(shield=ShieldEspion())
        plan = mode.preparer()
        assert plan["reseau"]["etape"] == 1
        assert plan["gel"]["etape"] == 2
        assert plan["sauvegarde"]["etape"] == 3
        assert plan["rapport"]["etape"] == 4
        assert plan["reseau"]["regle"] == "AZ_INCIDENT"
        # La règle, pas la carte réseau — le point de conception de §2.6.
        assert "carte réseau n'est PAS désactivée" in plan["reseau"]["note"]

    def test_le_plan_liste_les_processus_retenus_et_les_ecartes(self, fabrique_mode, proc_jetable):
        """L'utilisateur doit voir que lsass a été VU puis écarté, sinon il
        croit l'outil aveugle."""
        proc = proc_jetable()
        shield = ShieldEspion(candidats=[
            {"pid": proc.pid, "name": psutil.Process(proc.pid).name(), "write_bytes": 999},
            {"pid": 999_001, "name": "lsass.exe", "write_bytes": 500},
            {"pid": 999_002, "name": "explorer.exe", "write_bytes": 400},
        ])
        mode = fabrique_mode(shield=shield)
        plan = mode.preparer()

        retenus = {p["pid"] for p in plan["processus"]}
        exclus = {p["nom"]: p["raison"] for p in plan["processus_exclus"]}
        assert proc.pid in retenus
        assert 999_001 not in retenus and 999_002 not in retenus
        assert "plante le système" in exclus["lsass.exe"]
        assert "bureau inutilisable" in exclus["explorer.exe"]

    def test_le_plan_avertit_hors_windows_sans_faire_echouer(self, fabrique_mode):
        mode = fabrique_mode(plateforme="Linux", admin=False, shield=ShieldEspion())
        plan = mode.preparer()
        assert plan["ok"] is True
        assert plan["reseau"]["disponible"] is False
        assert plan["sauvegarde"]["disponible"] is False
        assert any("Linux" in a for a in plan["avertissements"])

    def test_le_plan_avertit_quand_le_mode_est_deja_actif(self, fabrique_mode, proc_jetable):
        mode = fabrique_mode(shield=ShieldEspion(), partage_etat=True)
        mode.activer(_plan())
        plan = mode.preparer()
        assert plan["deja_actif"] is True
        assert any("DÉJÀ actif" in a for a in plan["avertissements"])


# ── 2. Ordre des étapes ─────────────────────────────────────────────────────

class TestOrdreDesEtapes:

    def test_reseau_puis_gel_puis_sauvegarde_puis_rapport(self, fabrique_mode, proc_jetable, tmp_path):
        """La coupure réseau passe AVANT tout le reste : c'est elle qui arrête
        la propagation et l'exfiltration. Le cliché VSS vient après le gel,
        pour ne pas photographier un chiffrement en cours."""
        proc = proc_jetable()
        journal = []
        runner = RunnerFactice(journal=journal,
                               reponses={"show rule": (1, "No rules match the specified criteria.")})
        shield = ShieldEspion(journal=journal)
        mode = fabrique_mode(runner=runner, shield=shield)

        with volume_factice():
            resultat = mode.activer(_plan(
                processus=[{"pid": proc.pid, "nom": psutil.Process(proc.pid).name()}],
                dossiers=[tmp_path]))

        assert resultat["ordre"] == ["reseau", "processus", "sauvegarde", "rapport"]
        i_add = next(i for i, l in enumerate(journal) if "add rule" in l)
        i_suspend = next(i for i, l in enumerate(journal) if l.startswith("# suspend"))
        i_vss = next(i for i, l in enumerate(journal) if "vssadmin" in l)
        assert i_add < i_suspend < i_vss, journal
        assert resultat["etapes"]["rapport"]["ok"] is True

    def test_les_deux_sens_de_la_regle_sont_poses(self, fabrique_mode, tmp_path):
        runner = RunnerFactice(reponses={"show rule": (1, "No rules match")})
        mode = fabrique_mode(runner=runner, shield=ShieldEspion())
        mode.activer(_plan(dossiers=[tmp_path]))
        ajouts = [c for c in runner.commandes if "add rule" in c]
        assert len(ajouts) == 2
        assert any("dir=out" in c for c in ajouts)
        assert any("dir=in" in c for c in ajouts)
        assert all("action=block" in c and f"name={NOM_REGLE}" in c for c in ajouts)
        # On ne désactive JAMAIS l'interface réseau.
        assert not any("interface set interface" in c for c in runner.commandes)


# ── 3. Idempotence ──────────────────────────────────────────────────────────

class TestIdempotence:

    def test_activer_deux_fois_ne_fait_rien_la_seconde_fois(self, fabrique_mode, proc_jetable, tmp_path):
        proc = proc_jetable()
        runner = RunnerFactice(reponses={"show rule": (1, "No rules match")})
        shield = ShieldEspion()
        mode = fabrique_mode(runner=runner, shield=shield, partage_etat=True)
        plan = _plan(processus=[{"pid": proc.pid, "nom": psutil.Process(proc.pid).name()}],
                     dossiers=[tmp_path])

        premier = mode.activer(plan)
        commandes_apres_un = list(runner.commandes)
        suspends_apres_un = [l for l in shield.journal if l.startswith("# suspend")]

        second = mode.activer(plan)

        assert premier["deja_actif"] is False
        assert second["deja_actif"] is True and second["ok"] is True
        assert runner.commandes == commandes_apres_un, "aucune commande supplémentaire"
        assert [l for l in shield.journal if l.startswith("# suspend")] == suspends_apres_un

    def test_la_regle_deja_presente_nest_pas_reposee(self, fabrique_mode):
        """Un doublon de règle survivrait au premier delete : l'utilisateur
        resterait sans réseau après un rétablissement annoncé réussi."""
        runner = RunnerFactice(reponses={"show rule": (0, f"Rule Name: {NOM_REGLE}")})
        mode = fabrique_mode(runner=runner, shield=ShieldEspion())
        res = mode.couper_reseau()
        assert res["ok"] is True and res["deja_presente"] is True
        assert not any("add rule" in c for c in runner.commandes)

    def test_le_meme_processus_nest_pas_gele_deux_fois(self, fabrique_mode, proc_jetable):
        proc = proc_jetable()
        shield = ShieldEspion()
        mode = fabrique_mode(shield=shield)
        entree = {"pid": proc.pid, "nom": psutil.Process(proc.pid).name()}
        res = mode.geler_processus([entree], deja_geles=[{"pid": proc.pid}])
        assert res["geles"] == []
        assert "déjà gelé" in res["ignores"][0]["raison"]
        assert not [l for l in shield.journal if l.startswith("# suspend")]

    def test_retirer_une_regle_absente_est_un_succes(self, fabrique_mode):
        runner = RunnerFactice(reponses={"delete rule": (1, "No rules match the specified criteria.")})
        mode = fabrique_mode(runner=runner, shield=ShieldEspion())
        res = mode.retablir_reseau()
        assert res["ok"] is True and res["deja_absente"] is True


# ── 4. Liste noire — la faute critique à ne jamais commettre ────────────────

class TestListeNoire:

    @pytest.mark.parametrize("nom", ["lsass.exe", "csrss.exe", "smss.exe", "wininit.exe",
                                     "winlogon.exe", "services.exe", "System", "LSASS.EXE"])
    def test_les_processus_critiques_sont_refuses(self, fabrique_mode, nom):
        mode = fabrique_mode(shield=ShieldEspion())
        raison = mode.raison_protection(999_100, nom)
        assert raison is not None, f"{nom} DOIT être protégé (gel = écran bleu)"

    def test_explorer_est_refuse(self, fabrique_mode):
        """Décision assumée : geler explorer fige le bureau, donc le bouton
        « Rétablir » devient inatteignable."""
        mode = fabrique_mode(shield=ShieldEspion())
        assert mode.raison_protection(999_101, "explorer.exe") is not None

    def test_le_processus_courant_est_refuse(self, fabrique_mode):
        mode = fabrique_mode(shield=ShieldEspion())
        raison = mode.raison_protection(os.getpid(), "python")
        assert raison is not None and "courant" in raison

    def test_les_ancetres_sont_refuses(self, fabrique_mode):
        """Geler la console qui nous a lancés rendrait le rétablissement
        impossible."""
        mode = fabrique_mode(shield=ShieldEspion())
        parent = psutil.Process(os.getpid()).parent()
        if parent is None:
            pytest.skip("pas de processus parent visible")
        assert mode.raison_protection(parent.pid, parent.name()) is not None

    def test_un_plan_qui_reclame_lsass_est_refuse_a_lexecution(self, fabrique_mode):
        """La liste noire est revérifiée dans geler_processus() : un plan peut
        venir de l'extérieur (interface, appel direct à l'API)."""
        shield = ShieldEspion()
        mode = fabrique_mode(shield=shield)
        res = mode.geler_processus([{"pid": 999_102, "nom": "lsass.exe"}])
        assert res["geles"] == []
        assert res["ignores"][0]["protege"] is True
        assert not [l for l in shield.journal if l.startswith("# suspend")]

    def test_le_nom_reel_prime_sur_le_nom_du_plan(self, fabrique_mode, proc_jetable, tmp_path):
        """Cas retors, et vrai : le plan annonce « facture.exe » pour un PID
        dont le processus s'appelle en réalité csrss.exe. Le gel doit être
        refusé — sinon écran bleu. Le test crée un VRAI processus portant ce
        nom (copie de /bin/sleep) : rien n'est simulé ici."""
        proc = proc_jetable(nom_executable=tmp_path / "csrss.exe")
        assert psutil.Process(proc.pid).name() == "csrss.exe"
        shield = ShieldEspion()
        mode = fabrique_mode(shield=shield)

        res = mode.geler_processus([{"pid": proc.pid, "nom": "facture.exe"}])

        assert res["geles"] == []
        assert res["ignores"][0]["nom_reel"] == "csrss.exe"
        assert res["ignores"][0]["protege"] is True
        assert not [l for l in shield.journal if l.startswith("# suspend")]
        assert psutil.Process(proc.pid).status() != psutil.STATUS_STOPPED


# ── 5. Gel et dégel RÉELS (pas de mock ici) ─────────────────────────────────

class TestGelReel:

    def test_le_processus_est_reellement_gele_puis_relance(self, fabrique_mode, proc_jetable, tmp_path):
        """Vérification de bout en bout sur un processus jetable : état système
        réel avant, pendant et après. C'est le vrai RansomwareShield qui est
        utilisé, pas un double."""
        from optimizer.ransomware_shield import RansomwareShield

        proc = proc_jetable()
        cible = psutil.Process(proc.pid)
        assert cible.status() != psutil.STATUS_STOPPED

        runner = RunnerFactice(reponses={"show rule": (1, "No rules match")})
        mode = fabrique_mode(runner=runner, shield=RansomwareShield, partage_etat=True)
        res = mode.activer(_plan(processus=[{"pid": proc.pid, "nom": cible.name()}],
                                 dossiers=[tmp_path]))

        assert res["nb_geles"] == 1
        assert cible.status() == psutil.STATUS_STOPPED, "le processus doit être GELÉ"
        assert proc.poll() is None, "gel, PAS arrêt : le processus est toujours vivant"

        retour = mode.retablir()

        assert retour["ok"] is True and retour["complet"] is True
        assert cible.status() != psutil.STATUS_STOPPED, "le processus doit être relancé"
        assert proc.poll() is None

    def test_le_module_reutilise_le_mecanisme_du_bouclier(self):
        """Pas de second mécanisme de gel : c'est bien la fonction du bouclier
        anti-rançongiciel qui est appelée."""
        from optimizer import ransomware_shield
        assert incident_mode.RansomwareShield is ransomware_shield.RansomwareShield

    def test_un_processus_deja_suspendu_par_un_tiers_est_laisse_tranquille(
            self, fabrique_mode, proc_jetable):
        """Sinon on le relancerait au rétablissement alors qu'on ne l'a jamais
        gelé — on modifierait l'état de la machine à l'insu de l'utilisateur."""
        proc = proc_jetable()
        cible = psutil.Process(proc.pid)
        cible.suspend()
        try:
            mode = fabrique_mode(shield=ShieldEspion())
            res = mode.geler_processus([{"pid": proc.pid, "nom": cible.name()}])
            assert res["geles"] == []
            assert "pas par nous" in res["ignores"][0]["raison"]
        finally:
            cible.resume()

    def test_un_gel_refuse_est_rapporte_sans_faire_echouer(self, fabrique_mode, proc_jetable):
        proc = proc_jetable()
        mode = fabrique_mode(shield=ShieldEspion(suspend_ok=False))
        res = mode.geler_processus([{"pid": proc.pid, "nom": psutil.Process(proc.pid).name()}])
        assert res["ok"] is True
        assert res["geles"] == []
        assert "gel refusé" in res["ignores"][0]["raison"]

    def test_un_pid_recycle_nest_pas_degele(self, fabrique_mode, proc_jetable):
        """Les PID sont recyclés : dégeler à l'aveugle un PID noté dix minutes
        plus tôt, c'est risquer de toucher un processus innocent."""
        proc = proc_jetable()
        cible = psutil.Process(proc.pid)
        shield = ShieldEspion()
        mode = fabrique_mode(shield=shield)

        res = mode.degeler_processus([{
            "pid": proc.pid, "nom": cible.name(),
            "create_time": cible.create_time() - 3600,  # créé une heure plus tôt : ce n'est plus lui
        }])

        assert res["relances"] == []
        assert "recyclé" in res["disparus"][0]["raison"]
        assert not [l for l in shield.journal if l.startswith("# resume")]

    def test_un_processus_disparu_ne_bloque_pas_le_retablissement(self, fabrique_mode, proc_jetable):
        proc = proc_jetable()
        pid = proc.pid
        nom = psutil.Process(pid).name()
        ct = psutil.Process(pid).create_time()
        proc.kill()
        proc.wait(timeout=5)
        mode = fabrique_mode(shield=ShieldEspion())
        res = mode.degeler_processus([{"pid": pid, "nom": nom, "create_time": ct}])
        assert res["ok"] is True
        assert res["restants"] == []
        assert "disparu" in res["disparus"][0]["raison"]


# ── 6. État persistant ──────────────────────────────────────────────────────

class TestEtatPersistant:

    def test_letat_est_ecrit_et_relu_par_une_autre_instance(self, fabrique_mode, proc_jetable,
                                                            tmp_path):
        """Simule la fermeture de l'application : une NOUVELLE instance relit
        l'état sur disque et sait que le réseau est coupé."""
        proc = proc_jetable()
        nom = psutil.Process(proc.pid).name()
        runner = RunnerFactice(reponses={"show rule": (1, "No rules match")})
        mode = fabrique_mode(runner=runner, shield=ShieldEspion(), partage_etat=True)
        mode.activer(_plan(processus=[{"pid": proc.pid, "nom": nom}], dossiers=[tmp_path]))

        # « Redémarrage » : nouvelle instance, même emplacement d'état.
        apres = IncidentMode(runner=RunnerFactice(), etat_path=mode.etat_path,
                             dossier_rapports=mode.dossier, shield=ShieldEspion(),
                             plateforme="Windows", admin=True)
        etat = apres.etat()

        assert etat["actif"] is True
        assert etat["restauration_requise"] is True
        assert etat["reseau_coupe"] is True
        assert etat["nb_geles"] == 1
        assert etat["processus_geles"][0]["pid"] == proc.pid
        assert NOM_REGLE in etat["message"]
        assert "Rétablir" in etat["message"]
        assert etat["retrait_manuel"].startswith("netsh advfirewall firewall delete rule")

    def test_letat_survit_a_un_plantage_pendant_la_sauvegarde(self, fabrique_mode, proc_jetable,
                                                              tmp_path):
        """L'étape VSS est la plus lente : une coupure de courant pendant le
        cliché ne doit pas laisser une machine sans réseau et sans trace.
        KeyboardInterrupt simule une mort brutale du processus."""
        proc = proc_jetable()
        nom = psutil.Process(proc.pid).name()
        runner = RunnerFactice(reponses={"show rule": (1, "No rules match")})
        mode = fabrique_mode(runner=runner, shield=ShieldEspion(), partage_etat=True)

        with patch.object(IncidentMode, "sauvegarder", side_effect=KeyboardInterrupt):
            with pytest.raises(KeyboardInterrupt):
                mode.activer(_plan(processus=[{"pid": proc.pid, "nom": nom}], dossiers=[tmp_path]))

        etat = json.loads(mode.etat_path.read_text(encoding="utf-8"))
        assert etat["actif"] is True
        assert etat["reseau_coupe"] is True
        assert etat["etape_atteinte"] == "processus"
        assert [p["pid"] for p in etat["processus_geles"]] == [proc.pid]

    def test_letat_par_defaut_vit_dans_le_dossier_de_donnees(self, tmp_path, monkeypatch):
        """Jamais Path(__file__) : gelé en exécutable, ce chemin pointe sur un
        dossier temporaire effacé à la fermeture (voir paths.py)."""
        monkeypatch.setenv("ANTIZEEVIRIUS_DATA_DIR", str(tmp_path / "donnees"))
        mode = IncidentMode()
        assert mode.etat_path == tmp_path / "donnees" / "incident" / "incident_state.json"
        assert str(Path(incident_mode.__file__).parent) not in str(mode.etat_path)

    def test_un_etat_illisible_est_signale_et_non_ignore(self, fabrique_mode):
        mode = fabrique_mode(shield=ShieldEspion())
        mode.etat_path.write_text("{ ceci n'est pas du JSON", encoding="utf-8")
        etat = mode.etat()
        assert etat["corrompu"] is True
        assert NOM_REGLE in etat["message"]

    def test_un_etat_illisible_tente_quand_meme_le_retrait_de_la_regle(self, fabrique_mode):
        """La règle est peut-être posée : une suppression inutile ne coûte rien,
        une règle oubliée coûte le réseau de l'utilisateur."""
        runner = RunnerFactice(reponses={"delete rule": (0, "Ok.")})
        mode = fabrique_mode(runner=runner, shield=ShieldEspion())
        mode.etat_path.write_text("tronqué", encoding="utf-8")
        res = mode.retablir()
        assert any("delete rule" in c for c in runner.commandes)
        assert res["ok"] is True

    def test_lecriture_de_letat_est_atomique(self, fabrique_mode, tmp_path):
        """Écriture par fichier temporaire puis remplacement : jamais de JSON
        tronqué sur disque."""
        mode = fabrique_mode(shield=ShieldEspion())
        mode.activer(_plan(dossiers=[tmp_path]))
        assert mode.etat_path.exists()
        assert not list(mode.dossier.glob("*.tmp")), "aucun fichier temporaire résiduel"
        json.loads(mode.etat_path.read_text(encoding="utf-8"))


# ── 7. Rétablissement ───────────────────────────────────────────────────────

class TestRetablissement:

    def test_retablir_retire_la_regle_et_relance_les_processus(self, fabrique_mode,
                                                               proc_jetable, tmp_path):
        proc = proc_jetable()
        nom = psutil.Process(proc.pid).name()
        journal = []
        runner = RunnerFactice(journal=journal, reponses={"show rule": (1, "No rules match")})
        shield = ShieldEspion(journal=journal)
        mode = fabrique_mode(runner=runner, shield=shield, partage_etat=True)
        mode.activer(_plan(processus=[{"pid": proc.pid, "nom": nom}], dossiers=[tmp_path]))

        res = mode.retablir()

        assert res["ok"] is True and res["complet"] is True and res["actif"] is False
        assert any("delete rule" in c and f"name={NOM_REGLE}" in c for c in runner.commandes)
        assert f"# resume {proc.pid}" in journal
        etat = json.loads(mode.etat_path.read_text(encoding="utf-8"))
        assert etat["actif"] is False
        assert etat["dernier_incident"]["nb_geles"] == 1

    def test_retablir_sans_incident_ne_fait_rien(self, fabrique_mode):
        runner = RunnerFactice()
        mode = fabrique_mode(runner=runner, shield=ShieldEspion())
        res = mode.retablir()
        assert res["ok"] is True and res["rien_a_faire"] is True
        assert runner.commandes == []

    def test_un_retablissement_partiel_conserve_letat(self, fabrique_mode, proc_jetable, tmp_path):
        """Échec du retrait de la règle : l'état DOIT rester, sinon
        l'utilisateur se retrouve sans réseau et sans trace du pourquoi."""
        proc = proc_jetable()
        nom = psutil.Process(proc.pid).name()
        runner = RunnerFactice(reponses={"show rule": (1, "No rules match")})
        mode = fabrique_mode(runner=runner, shield=ShieldEspion(), partage_etat=True)
        mode.activer(_plan(processus=[{"pid": proc.pid, "nom": nom}], dossiers=[tmp_path]))

        runner.reponses["delete rule"] = (1, "The requested operation requires elevation.")
        res = mode.retablir()

        assert res["ok"] is False and res["actif"] is True
        assert "PARTIEL" in res["message"]
        etat = json.loads(mode.etat_path.read_text(encoding="utf-8"))
        assert etat["actif"] is True
        assert etat["reseau_coupe"] is True
        # Le processus, lui, a bien été relancé : on ne le garde pas en attente.
        assert etat["processus_geles"] == []

    def test_un_processus_qui_refuse_de_repartir_reste_dans_letat(self, fabrique_mode,
                                                                  proc_jetable, tmp_path):
        proc = proc_jetable()
        nom = psutil.Process(proc.pid).name()
        shield = ShieldEspion(resume_ok=False)
        runner = RunnerFactice(reponses={"show rule": (1, "No rules match")})
        mode = fabrique_mode(runner=runner, shield=shield, partage_etat=True)
        mode.activer(_plan(processus=[{"pid": proc.pid, "nom": nom}], dossiers=[tmp_path]))

        res = mode.retablir()

        assert res["ok"] is False
        assert [p["pid"] for p in res["restants"]] == [proc.pid]
        etat = json.loads(mode.etat_path.read_text(encoding="utf-8"))
        assert etat["actif"] is True
        assert [p["pid"] for p in etat["processus_geles"]] == [proc.pid]

    def test_hors_windows_le_retablissement_reste_complet(self, fabrique_mode, proc_jetable,
                                                          tmp_path):
        """Aucune règle n'a jamais été posée sous Linux : le rétablissement ne
        doit pas rester bloqué en « partiel » pour autant."""
        proc = proc_jetable()
        nom = psutil.Process(proc.pid).name()
        mode = fabrique_mode(plateforme="Linux", admin=False, shield=ShieldEspion(),
                             partage_etat=True)
        mode.activer(_plan(processus=[{"pid": proc.pid, "nom": nom}], dossiers=[tmp_path]))
        res = mode.retablir()
        assert res["ok"] is True and res["complet"] is True
        assert res["etapes"]["reseau"]["rien_a_faire"] is True


# ── 8. Dégradation propre ───────────────────────────────────────────────────

class TestDegradationPropre:

    def test_hors_windows_chaque_etape_se_declare_indisponible(self, fabrique_mode, tmp_path):
        mode = fabrique_mode(plateforme="Linux", admin=False, shield=ShieldEspion())
        for res in (mode.couper_reseau(), mode.sauvegarder([str(tmp_path)]),
                    mode.retablir_reseau()):
            assert res["ok"] is False
            assert res["unavailable"] is True
            assert "Windows" in res["reason"]

    def test_sans_droits_administrateur_chaque_etape_le_dit(self, fabrique_mode, tmp_path):
        mode = fabrique_mode(admin=False, shield=ShieldEspion())
        for res in (mode.couper_reseau(), mode.sauvegarder([str(tmp_path)])):
            assert res["unavailable"] is True
            assert "administrateur" in res["reason"]

    def test_une_sauvegarde_impossible_nempeche_pas_la_coupure_reseau(self, fabrique_mode,
                                                                      proc_jetable, tmp_path):
        """Exigence explicite de §2.6 : les étapes sont indépendantes."""
        proc = proc_jetable()
        runner = RunnerFactice(reponses={
            "show rule": (1, "No rules match"),
            "vssadmin": (1, "Error: Invalid command."),
            "Win32_ShadowCopy": (1, "Access denied"),
        })
        mode = fabrique_mode(runner=runner, shield=ShieldEspion())
        with volume_factice():
            res = mode.activer(_plan(processus=[{"pid": proc.pid, "nom": psutil.Process(proc.pid).name()}],
                                     dossiers=[tmp_path]))

        assert any("vssadmin" in c for c in runner.commandes)
        assert res["etapes"]["sauvegarde"]["ok"] is False
        assert res["etapes"]["reseau"]["ok"] is True
        assert res["etapes"]["processus"]["ok"] is True
        assert res["etapes"]["rapport"]["ok"] is True
        assert res["degrade"] is True and res["etapes_en_echec"] == ["sauvegarde"]
        assert res["actif"] is True

    def test_une_coupure_reseau_impossible_nempeche_pas_le_reste(self, fabrique_mode,
                                                                 proc_jetable, tmp_path):
        proc = proc_jetable()
        runner = RunnerFactice(reponses={
            "show rule": (1, "No rules match"),
            "add rule": (1, "The requested operation requires elevation."),
        })
        mode = fabrique_mode(runner=runner, shield=ShieldEspion())
        with volume_factice():
            res = mode.activer(_plan(processus=[{"pid": proc.pid, "nom": psutil.Process(proc.pid).name()}],
                                     dossiers=[tmp_path]))

        assert res["etapes"]["reseau"]["ok"] is False
        assert res["etapes"]["reseau"]["unavailable"] is False, "échec, pas indisponibilité"
        assert res["etapes"]["processus"]["nb_geles"] == 1
        assert res["etapes"]["sauvegarde"]["ok"] is True
        etat = json.loads(mode.etat_path.read_text(encoding="utf-8"))
        assert etat["reseau_coupe"] is False

    def test_vssadmin_refuse_bascule_sur_le_repli_wmi(self, fabrique_mode, tmp_path):
        """Sur les éditions client de Windows, `vssadmin create shadow` n'existe
        pas. Le repli Win32_ShadowCopy doit prendre le relais et le dire."""
        runner = RunnerFactice(reponses={
            "vssadmin": (1, "Error: Invalid command."),
            "Win32_ShadowCopy": (0, "ReturnValue : 0"),
        })
        mode = fabrique_mode(runner=runner, shield=ShieldEspion())
        with volume_factice():
            res = mode.sauvegarder([str(tmp_path)])
        assert res["ok"] is True
        assert res["cliches"][0]["methode"] == "Win32_ShadowCopy"
        assert "édition client" in res["cliches"][0]["note"]

    def test_une_commande_absente_ne_leve_jamais(self, fabrique_mode):
        """netsh n'existe pas sous Linux : FileNotFoundError doit devenir un
        code de retour, pas une exception qui casse la séquence d'urgence."""
        def runner_qui_explose(commande, timeout=None):
            raise FileNotFoundError("netsh")
        mode = fabrique_mode(runner=runner_qui_explose, shield=ShieldEspion())
        res = mode.couper_reseau()
        assert res["ok"] is False and "netsh" in res["reason"]


# ── 9. Rapport horodaté ─────────────────────────────────────────────────────

class TestRapport:

    def test_le_rapport_est_ecrit_en_json_et_en_texte(self, fabrique_mode, proc_jetable, tmp_path):
        proc = proc_jetable()
        nom = psutil.Process(proc.pid).name()
        runner = RunnerFactice(reponses={"show rule": (1, "No rules match")})
        mode = fabrique_mode(runner=runner, shield=ShieldEspion())
        res = mode.activer(_plan(processus=[{"pid": proc.pid, "nom": nom}], dossiers=[tmp_path]))

        rapport = res["etapes"]["rapport"]
        chemin_json, chemin_txt = Path(rapport["json"]), Path(rapport["texte"])
        assert chemin_json.is_file() and chemin_txt.is_file()
        assert chemin_json.name.startswith("incident_")

        donnees = json.loads(chemin_json.read_text(encoding="utf-8"))
        assert donnees["processus"]["geles"][0]["pid"] == proc.pid
        assert "connexions" in donnees and "fichiers_recents" in donnees

        texte = chemin_txt.read_text(encoding="utf-8")
        assert "[1/4] Réseau" in texte and "[2/4] Processus gelés" in texte
        assert "[3/4] Sauvegarde" in texte and "[4/4] Rapport" in texte
        assert nom in texte
        assert "NE REDÉMARRE PAS" in texte, "le conseil le plus important du didacticiel"
        assert f"delete rule name={NOM_REGLE}" in texte

    def test_le_rapport_liste_les_fichiers_recemment_modifies(self, fabrique_mode, tmp_path):
        """Sur une arborescence réelle : un fichier tout juste écrit est vu,
        un fichier vieux de deux heures ne l'est pas."""
        dossier = tmp_path / "Documents"
        (dossier / "sous").mkdir(parents=True)
        recent = dossier / "facture.docx.chiffre"
        recent.write_text("x", encoding="utf-8")
        imbrique = dossier / "sous" / "photo.jpg.chiffre"
        imbrique.write_text("y", encoding="utf-8")
        ancien = dossier / "vieux.txt"
        ancien.write_text("z", encoding="utf-8")
        vieille_date = time.time() - 7200
        os.utime(ancien, (vieille_date, vieille_date))

        mode = fabrique_mode(shield=ShieldEspion())
        res = mode.fichiers_recents([str(dossier)], minutes=15)
        chemins = {Path(f["chemin"]).name for f in res["fichiers"]}
        assert "facture.docx.chiffre" in chemins
        assert "photo.jpg.chiffre" in chemins
        assert "vieux.txt" not in chemins

    def test_le_rapport_dit_franchement_quand_une_etape_a_echoue(self, fabrique_mode, tmp_path):
        """Ne jamais annoncer « sauvegarde effectuée » quand rien n'a été créé."""
        runner = RunnerFactice(reponses={
            "show rule": (1, "No rules match"),
            "vssadmin": (1, "Error"), "Win32_ShadowCopy": (1, "Error"),
        })
        mode = fabrique_mode(runner=runner, shield=ShieldEspion())
        with volume_factice():
            res = mode.activer(_plan(dossiers=[tmp_path]))
        texte = Path(res["etapes"]["rapport"]["texte"]).read_text(encoding="utf-8")
        assert "[3/4] Sauvegarde       indisponible" in texte
        assert "Aucun cliché instantané" in texte

    def test_les_connexions_sont_lues_sans_interception(self, fabrique_mode):
        """Lecture de la table de connexions, rien de plus (didacticiel n°5)."""
        mode = fabrique_mode(shield=ShieldEspion())
        res = mode.connexions_actives()
        assert res["ok"] is True or res.get("unavailable") is True
        if res["ok"]:
            assert isinstance(res["connexions"], list)
            assert res["nb"] <= incident_mode.MAX_CONNEXIONS_RAPPORT
