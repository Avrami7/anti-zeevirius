"""
test_gui_assistant.py
Tests du branchement de la COUCHE ASSISTANT sur l'interface web
(`gui/bridge.py` + `gui/web/*`), c'est-à-dire des seules garanties qui ne se
voient ni dans les tests des modules `assistant/*`, ni à l'œil sur une
capture d'écran :

1. **Routage** — les neuf actions de la section 10 du contrat sont
   atteignables, et aucune ne lève quoi qu'il arrive.
2. **Comprendre n'exécute rien** — la garantie centrale de la section.
   `assistant.comprendre` ne doit toucher ni `dispatch()`, ni `_guarded()`,
   ni aucune action destructive, même quand la phrase en réclame une.
3. **Forme des réponses** — l'interface lit des champs précis
   (`comprise`, `detail.exige_confirmation`, `plan.items`, `disponible`…).
   Un champ renommé côté pont ne casse rien de visible : la page affiche
   simplement du vide, ou « Rien à faire » là où il y avait du travail.
   Ces tests figent donc les noms que `app.js` lit réellement.
4. **Double validation** — les trois actions qui écrivent exigent le couple
   `dry_run` → `confirm_token`, et le frontend emprunte le mécanisme déjà en
   place au lieu d'en écrire un second.
5. **Cohérence du frontend** — section, entrée de menu, identifiants,
   actions appelées, dégradation propre sans `psutil`, et intégrité de
   l'IIFE : une parenthèse manquante y tue TOUTE l'interface sans afficher
   la moindre erreur.

Aucun test n'exige Windows ni `psutil` : l'absence de la télémétrie est
elle-même un cas couvert.
"""

import re
import shutil
import subprocess
from pathlib import Path

import pytest

from gui.bridge import ASSISTANT_ACTIONS, Bridge

WEB = Path(__file__).resolve().parent.parent / "gui" / "web"

# Les actions dont la section Assistant de l'interface a besoin pour
# fonctionner. Les trois autres (`capacites`, `profil`, `resume`) sont
# exposées par le pont mais ne sont pas encore branchées sur un panneau.
ACTIONS_UTILISEES = (
    "assistant.comprendre", "assistant.telemetrie", "assistant.notifications",
    "assistant.acquitter", "assistant.suggestions", "assistant.autonomie",
)


# ── Doubles ──────────────────────────────────────────────────────────
class FauxEchantillon:
    def __init__(self):
        self.horodatage, self.cpu, self.memoire = 1.0, 0.0, 0.0
        self.disque, self.temperature = 0.0, None

    def to_dict(self):
        return {"horodatage": self.horodatage, "cpu": self.cpu, "memoire": self.memoire,
                "disque": self.disque, "temperature": self.temperature}


class FausseTelemetrieAbsente:
    """`psutil` manquant : la couche se déclare indisponible et rend des
    zéros. L'interface doit afficher « n. d. », jamais « 0 % »."""

    disponible = False

    def echantillonner(self):
        return FauxEchantillon()

    def moyennes(self, secondes):
        return None

    def historique(self):
        return []


@pytest.fixture
def pont():
    return Bridge()


def enveloppe_valide(rep):
    """Toute réponse du contrat, succès ou échec, a la même forme."""
    assert isinstance(rep, dict), "réponse absente : action inconnue du pont"
    assert isinstance(rep["ok"], bool)
    if rep["ok"]:
        assert isinstance(rep["data"], dict)
    else:
        assert isinstance(rep["error"], str) and rep["error"]
        assert isinstance(rep.get("unavailable", False), bool)
    return rep


@pytest.fixture(scope="module")
def sources():
    return {n: (WEB / n).read_text(encoding="utf-8")
            for n in ("index.html", "app.js", "app.css")}


# ═══════════════════════════════════════════════════════════════════
# 1. Routage des neuf actions
# ═══════════════════════════════════════════════════════════════════
class TestRoutage:

    def test_les_neuf_actions_du_contrat_sont_annoncees(self, pont):
        connues = set(pont.known_actions())
        manquantes = [a for a in ASSISTANT_ACTIONS if a not in connues]
        assert not manquantes, f"actions absentes du pont : {manquantes}"
        assert len(ASSISTANT_ACTIONS) == 9

    @pytest.mark.parametrize("action,params", [
        ("assistant.capacites", {}),
        ("assistant.capacites", {"categorie": "protection"}),
        ("assistant.comprendre", {"phrase": "état du système"}),
        ("assistant.comprendre", {}),
        ("assistant.profil", {}),
        ("assistant.telemetrie", {}),
        ("assistant.notifications", {}),
        ("assistant.notifications", {"non_acquittees_seulement": True}),
        ("assistant.acquitter", {"identifiant": "inexistant"}),
        ("assistant.suggestions", {}),
        ("assistant.resume", {}),
        ("assistant.autonomie", {}),
        ("assistant.autonomie", {"etat": "n'importe quoi"}),
    ])
    def test_aucune_action_ne_leve(self, pont, action, params):
        enveloppe_valide(pont.dispatch(action, params))

    def test_les_deux_ecritures_menent_au_meme_endroit(self, pont):
        """`assistant.capacites` et `assistant_capacites` : une seule
        méthode, deux écritures — sinon le frontend appelle un nom que le
        serveur répond en 404."""
        a = pont.dispatch("assistant.capacites", {})
        b = pont.dispatch("assistant_capacites", {})
        assert a["ok"] and b["ok"]
        assert a["data"]["total"] == b["data"]["total"]


# ═══════════════════════════════════════════════════════════════════
# 2. Comprendre n'exécute rien — la garantie centrale
# ═══════════════════════════════════════════════════════════════════
class TestComprendreNExecuteRien:

    def test_une_phrase_destructive_ne_declenche_aucune_action(self, pont):
        """Le point non négociable : une phrase qui réclame une purge doit
        rendre une LECTURE, et rien d'autre."""
        def piege(params):
            pytest.fail("`assistant.comprendre` a exécuté une capacité destructive")

        pont.a_quarantine_delete = piege
        pont.a_clean_full = piege
        pont._guarded = piege

        rep = enveloppe_valide(pont.dispatch(
            "assistant.comprendre", {"phrase": "vide definitivement la quarantaine"}))
        assert rep["ok"] is True
        assert rep["data"]["execute"] is False

    def test_la_lecture_porte_sa_justification_son_risque_et_sa_confiance(self, pont):
        """Les trois éléments que l'interface AFFICHE avant de proposer
        l'exécution. Sans eux, l'utilisateur valide à l'aveugle."""
        rep = pont.dispatch("assistant.comprendre", {"phrase": "état du système"})
        d = rep["data"]
        assert d["comprise"] is True
        assert d["justification"].strip(), "une lecture sans justification est un ordre, pas un avis"
        assert 0.0 <= d["confiance"] <= 1.0
        detail = d["detail"]
        assert detail is not None
        assert detail["risque_libelle"], "le niveau de risque doit être lisible par un humain"
        assert isinstance(detail["exige_confirmation"], bool)
        assert isinstance(detail["risque"], int)

    def test_une_phrase_incomprise_ne_propose_aucune_capacite(self, pont):
        """Sous le seuil, l'interface demande une reformulation : le pont
        doit donc répondre `comprise: false` ET `detail: null`, sinon la
        page aurait de quoi peindre un bouton d'exécution."""
        rep = pont.dispatch("assistant.comprendre", {"phrase": "occupe-toi de mes trucs"})
        d = rep["data"]
        assert d["comprise"] is False
        assert d["capacite"] == ""
        assert d["detail"] is None
        assert d["justification"].strip()

    def test_une_phrase_vide_est_refusee_proprement(self, pont):
        rep = enveloppe_valide(pont.dispatch("assistant.comprendre", {"phrase": "   "}))
        assert rep["ok"] is False
        assert rep["unavailable"] is False


# ═══════════════════════════════════════════════════════════════════
# 3. Forme exacte des réponses que lit app.js
# ═══════════════════════════════════════════════════════════════════
class TestFormeDesReponses:
    """Le code fait foi sur la forme des réponses : ces tests figent les
    noms de champs que `app.js` lit, pour qu'un renommage casse ici plutôt
    que silencieusement à l'écran."""

    def test_la_telemetrie_rend_les_champs_des_quatre_jauges(self, pont):
        d = pont.dispatch("assistant.telemetrie", {})["data"]
        assert set(["disponible", "echantillon", "moyennes", "fenetre", "historique"]) <= set(d)
        for cle in ("horodatage", "cpu", "memoire", "disque", "temperature"):
            assert cle in d["echantillon"], f"jauge sans champ `{cle}`"
        assert isinstance(d["historique"], list)

    def test_l_absence_de_psutil_se_declare_au_lieu_de_mentir(self, pont):
        """Sans `psutil`, les valeurs valent zéro : l'interface ne doit
        surtout pas les présenter comme une machine au repos. Elle a besoin
        du drapeau `disponible` pour afficher « n. d. »."""
        pont._instances["assistant_telemetrie"] = FausseTelemetrieAbsente()
        d = pont.dispatch("assistant.telemetrie", {})["data"]
        assert d["disponible"] is False
        assert d["historique"] == []
        assert d["moyennes"] is None
        assert d["echantillon"]["cpu"] == 0.0

    def test_les_notifications_portent_gravite_identifiant_et_acquittement(self, pont):
        centre = pont._centre()
        centre.pousser("critique", "Accès caméra", "inconnu32.exe filme.", "camera_watch",
                       systeme=False)
        centre.pousser("info", "Analyse finie", "Rien trouvé.", "scanner", systeme=False)

        d = pont.dispatch("assistant.notifications", {})["data"]
        assert d["total"] == 2 and d["non_acquittees"] == 2
        n = d["notifications"][0]
        for cle in ("identifiant", "horodatage", "gravite", "titre", "corps", "source", "acquittee"):
            assert cle in n, f"notification sans champ `{cle}`"
        assert n["gravite"] in ("info", "alerte", "critique"), "l'interface timbre les trois gravités"

        filtre = pont.dispatch(
            "assistant.notifications", {"non_acquittees_seulement": True})["data"]
        assert len(filtre["notifications"]) == 2

    def test_les_suggestions_portent_urgence_motif_et_capacite_decrite(self, pont):
        d = pont.dispatch("assistant.suggestions", {})["data"]
        assert isinstance(d["suggestions"], list) and d["suggestions"], \
            "une machine sans signatures ni temps réel a forcément quelque chose à proposer"
        for s in d["suggestions"]:
            assert s["motif"].strip(), "une suggestion sans le fait qui la motive est une injonction"
            assert isinstance(s["urgence"], int)
            # `detail` est ce qui permet de savoir, SANS second appel, si un
            # clic ouvrira la modale de confirmation ou lancera une lecture.
            if s["detail"] is not None:
                assert "action_bridge" in s["detail"]
                assert "exige_confirmation" in s["detail"]

    def test_l_etat_du_mode_autonome_porte_son_journal_et_ses_bornes(self, pont):
        d = pont.dispatch("assistant.autonomie", {})["data"]
        for cle in ("actif", "budget", "intervalle", "cycles", "executeur", "journal"):
            assert cle in d, f"état du mode autonome sans champ `{cle}`"
        assert d["actif"] is False
        assert isinstance(d["journal"], list)


# ═══════════════════════════════════════════════════════════════════
# 4. Double validation des trois actions qui écrivent
# ═══════════════════════════════════════════════════════════════════
class TestDoubleValidation:

    @pytest.mark.parametrize("action,params", [
        ("assistant.acquitter", {"identifiant": "x"}),
        ("assistant.autonomie", {"etat": "demarrer"}),
        ("assistant.profil", {"nom_utilisateur": "Zeev"}),
    ])
    def test_le_dry_run_est_le_defaut_et_rend_un_jeton(self, pont, action, params):
        d = enveloppe_valide(pont.dispatch(action, params))["data"]
        assert d["dry_run"] is True
        assert d["confirm_token"], "sans jeton, l'interface refuse d'exécuter"
        assert d["expires_in"] == 300
        # Ce que lit `normalizePlan()` dans app.js : le plan est sous `plan`,
        # et son décompte sous `plan.count`. Le lire ailleurs afficherait
        # « Rien à faire » sur une opération qui a du travail.
        assert isinstance(d["plan"], dict)
        assert "count" in d["plan"] and "items" in d["plan"]

    def test_l_execution_sans_jeton_est_refusee(self, pont):
        rep = enveloppe_valide(pont.dispatch(
            "assistant.autonomie", {"etat": "demarrer", "dry_run": False}))
        assert rep["ok"] is False
        assert "confirm_token" in rep["error"]
        assert pont._autonomie().actif() is False, "un refus ne doit rien avoir démarré"

    def test_le_plan_d_acquittement_decrit_la_notification_visee(self, pont):
        """Le plan doit être MONTRABLE : l'interface y lit le titre et le
        corps de l'alerte pour que l'utilisateur sache ce qu'il acquitte."""
        n = pont._centre().pousser("alerte", "Disque plein", "Volume à 91 %.",
                                   "telemetry", systeme=False)
        d = pont.dispatch("assistant.acquitter", {"identifiant": n.identifiant})["data"]
        assert d["plan"]["count"] == 1
        assert d["plan"]["items"][0]["path"] == "Disque plein"
        assert d["plan"]["note"].strip()

        rep = pont.dispatch("assistant.acquitter", {
            "identifiant": n.identifiant, "dry_run": False,
            "confirm_token": d["confirm_token"]})
        # L'interface lit le résultat sous `data.result`.
        assert rep["data"]["result"]["acquittee"] is True
        assert pont.dispatch("assistant.notifications", {})["data"]["non_acquittees"] == 0

    def test_un_identifiant_inconnu_donne_un_plan_vide_et_non_une_erreur(self, pont):
        """Un plan à zéro élément : c'est ce qui permet à l'interface de dire
        « rien à acquitter » au lieu d'inventer une réussite."""
        d = pont.dispatch("assistant.acquitter", {"identifiant": "jamais-vu"})["data"]
        assert d["plan"]["count"] == 0
        rep = pont.dispatch("assistant.acquitter", {
            "identifiant": "jamais-vu", "dry_run": False,
            "confirm_token": d["confirm_token"]})
        assert rep["data"]["result"]["acquittee"] is False

    def test_le_plan_d_activation_enonce_la_limite_du_mode(self, pont):
        """C'est le seul endroit où le backend écrit la limite du mode
        autonome. La modale l'affiche telle quelle : elle doit y être."""
        d = pont.dispatch("assistant.autonomie", {"etat": "demarrer"})["data"]
        note = d["plan"]["note"].lower()
        assert "lecture" in note
        assert d["plan"]["vers"] == "actif"
        assert d["plan"]["avant"]["actif"] is False

    def test_demarrage_puis_arret_par_le_cycle_complet(self, pont):
        """Le chemin exact que suit l'interface : dry_run, jeton, exécution."""
        d = pont.dispatch("assistant.autonomie", {"etat": "demarrer"})["data"]
        etat = pont.dispatch("assistant.autonomie", {
            "etat": "demarrer", "dry_run": False,
            "confirm_token": d["confirm_token"]})["data"]["result"]
        assert etat["actif"] is True
        assert etat["journal"], "l'activation doit laisser une trace dans le journal"

        d2 = pont.dispatch("assistant.autonomie", {"etat": "arreter"})["data"]
        etat2 = pont.dispatch("assistant.autonomie", {
            "etat": "arreter", "dry_run": False,
            "confirm_token": d2["confirm_token"]})["data"]["result"]
        assert etat2["actif"] is False

    def test_le_mode_autonome_refuse_ce_qui_ecrit_sur_le_disque(self, pont):
        """La promesse affichée dans le panneau (« il n'exécute que des
        actions en lecture seule ») doit être vraie côté pont, sinon
        l'interface mentirait à l'utilisateur."""
        registre = pont._registre()
        ecrivaines = [c for c in registre.toutes() if int(c.risque) > 0]
        assert ecrivaines, "le registre doit contenir des capacités qui écrivent"
        for capacite in ecrivaines[:6]:
            rep = pont._executer_capacite(capacite)
            assert rep["ok"] is False
            assert "lecture" in rep["error"].lower()


# ═══════════════════════════════════════════════════════════════════
# 5. Cohérence du frontend
# ═══════════════════════════════════════════════════════════════════
class TestFrontend:

    def test_la_section_et_son_entree_de_menu_existent(self, sources):
        html = sources["index.html"]
        assert 'id="view-assistant"' in html
        assert 'data-view="assistant"' in html
        # L'entrée est dans la barre latérale, groupe « Supervision » : la
        # veille et la mémoire ne sont pas une opération ponctuelle.
        rail = html.split('<nav class="rail"')[1].split("</nav>")[0]
        supervision = rail.split('<div class="rail-grp">')[1]
        assert 'data-view="assistant"' in supervision

    def test_la_vue_est_declaree_dans_le_routeur(self, sources):
        vues = re.search(r"var VIEWS = \[(.*?)\];", sources["app.js"], re.S).group(1)
        assert "'assistant'" in vues

    def test_les_cinq_panneaux_attendus_sont_presents(self, sources):
        for panneau in ("pAssistCmd", "pAssistTele", "pAssistNotifs",
                        "pAssistSugg", "pAssistAuto"):
            assert f'id="{panneau}"' in sources["index.html"], f"panneau {panneau} absent"

    def test_tous_les_identifiants_appeles_existent(self, sources):
        """Un `$('idInexistant')` rend null, et le premier appel de méthode
        qui suit tue TOUTE l'interface."""
        ids_html = set(re.findall(r'id="([A-Za-z0-9_-]+)"', sources["index.html"]))
        ids_js = set(re.findall(r"\$\('([A-Za-z0-9_-]+)'\)", sources["app.js"]))
        manquants = sorted(ids_js - ids_html)
        assert not manquants, f"identifiants absents de index.html : {manquants}"

    def test_les_actions_assistant_appelees_existent_dans_le_pont(self, sources, pont):
        """Le contrôle générique de `test_gui_v2.py` ne voit pas les actions
        pointées (`assistant.comprendre`) : son motif s'arrête au premier
        point. Celui-ci les attrape."""
        js = sources["app.js"]
        appelees = set(re.findall(r"'(assistant\.[a-z_]+)'", js))
        assert appelees, "aucune action assistant appelée par l'interface"
        inconnues = sorted(appelees - set(pont.known_actions()))
        assert not inconnues, f"actions appelées mais absentes du pont : {inconnues}"
        manquantes = [a for a in ACTIONS_UTILISEES if a not in appelees]
        assert not manquantes, f"la section n'utilise pas : {manquantes}"

    def test_l_execution_destructive_reemprunte_la_modale_existante(self, sources):
        """La section ne réécrit pas le cycle de confirmation : elle appelle
        `destructive()`, et c'est le pont qui dit ce qui doit être confirmé."""
        js = sources["app.js"]
        bloc = js.split("function asExecuterCapacite")[1].split("\n$('btnAsComprendre')")[0]
        assert "detail.exige_confirmation" in bloc
        assert "destructive({" in bloc
        # …et le déclenchement direct d'une exécution réelle n'existe pas
        # dans ce bloc : aucun `dry_run: false` écrit à la main.
        assert "dry_run: false" not in bloc and "dry_run:false" not in bloc

    def test_le_cycle_sans_modale_respecte_le_contrat(self, sources):
        """Acquittement et arrêt du mode autonome passent par `cycleGuarde`,
        qui fait dry_run PUIS exécution avec le jeton — jamais l'inverse,
        jamais sans jeton."""
        js = sources["app.js"]
        bloc = js.split("function cycleGuarde")[1].split("\n/* ══")[0]
        assert "dry.dry_run = true" in bloc
        assert "reel.confirm_token = token" in bloc
        assert "reel.dry_run = false" in bloc
        assert bloc.index("dry.dry_run = true") < bloc.index("reel.dry_run = false")

    def test_la_section_masque_par_l_attribut_hidden(self, sources):
        """`[hidden]{display:none !important}` existe déjà : les panneaux ne
        doivent pas inventer un second mécanisme de masquage."""
        html = sources["index.html"]
        bloc = html.split('id="view-assistant"')[1].split("</section>")[0]
        assert 'id="asLecture"' in bloc and "hidden>" in bloc
        # Aucun `style.display` nulle part dans le script de l'interface.
        assert "style.display" not in sources["app.js"]

    def test_la_limite_du_mode_autonome_est_ecrite_dans_la_page(self, sources):
        """« Mode autonome » laisse croire qu'une machine va agir seule sur
        le disque. Le panneau doit dire le contraire, en clair."""
        html = sources["index.html"]
        bloc = html.split('id="pAssistAuto"')[1].split("</article>")[0]
        assert "lecture seule" in bloc
        assert "suggestion" in bloc.lower()
        assert "Arrêter immédiatement" in bloc

    def test_l_indisponibilite_de_la_telemetrie_a_un_rendu_propre(self, sources):
        """Sans `psutil`, ni écran vide ni « 0 % » : des jauges « n. d. » et
        la raison écrite."""
        js = sources["app.js"]
        assert "asJaugesMuettes" in js
        assert "n. d." in js
        assert "psutil" in js
        bloc = js.split("function asPeindreTele")[1].split("\nfunction ")[0]
        assert "d.disponible" in bloc

    def test_le_seuil_de_confiance_n_est_pas_une_decision_de_l_interface(self, sources):
        """La page dessine la barre du seuil, mais c'est le champ `comprise`
        du pont qui décide. Deux vérités finiraient par diverger."""
        js = sources["app.js"]
        assert "AS_SEUIL" in js
        bloc = js.split("function asPeindreLecture")[1].split("\nfunction ")[0]
        assert "d.comprise" in bloc
        assert "AS_SEUIL" not in bloc, \
            "la lecture ne doit pas être requalifiée côté interface"

    def test_le_libelle_impose_par_le_proprietaire_est_respecte(self, sources):
        """« Dashboard » est le libellé du produit ; « Poste de commandement »
        est proscrit."""
        for nom, texte in sources.items():
            assert "Poste de commandement" not in texte, f"{nom} emploie un libellé proscrit"
        assert "Dashboard" in sources["index.html"]

    def test_le_logo_n_est_defini_qu_une_fois(self, sources):
        """La section réutilise `<use href="#blackhole">` ; elle ne redéfinit
        jamais le symbole, qui appartient au sprite."""
        assert sources["index.html"].count('<symbol id="blackhole"') == 1

    def test_les_regles_css_de_la_section_existent(self, sources):
        for classe in (".as-step", ".as-read", ".as-conf-seuil", ".as-urg",
                       ".as-jrow", ".as-gauges", ".as-chart"):
            assert classe + "{" in sources["app.css"] or classe + " " in sources["app.css"], \
                f"règle {classe} absente de app.css"

    def test_aucune_ressource_externe_ni_emoji(self, sources):
        motif = re.compile(r"""(?:https?:)?//([A-Za-z0-9.\-]+)""")
        emoji = re.compile("[\U0001F000-\U0001FAFF☀-➿]")
        for nom, texte in sources.items():
            nettoye = re.sub(r'xmlns(:\w+)?="[^"]*"', "", texte)
            for hote in motif.findall(nettoye):
                assert hote.startswith("127.0.0.1") or hote == "localhost", \
                    f"{nom} référence l'hôte externe {hote}"
            assert not emoji.findall(texte), f"{nom} contient des emoji"

    def test_l_iife_est_refermee(self, sources):
        """Le piège déjà rencontré : une parenthèse fermante manquante tue
        toute l'interface SANS aucune erreur visible à l'écran."""
        js = sources["app.js"].rstrip()
        assert js.endswith("})();"), "l'IIFE de app.js n'est pas refermée"

    @pytest.mark.skipif(shutil.which("node") is None, reason="node absent de cette machine")
    def test_app_js_est_syntaxiquement_valide(self):
        """Le seul contrôle qui attrape vraiment la parenthèse manquante."""
        r = subprocess.run([shutil.which("node"), "--check", str(WEB / "app.js")],
                           capture_output=True, text=True)
        assert r.returncode == 0, f"app.js invalide :\n{r.stderr}"
