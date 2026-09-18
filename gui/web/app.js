/* ==========================================================================
   ANTI-ZEEVIRIUS — dashboard
   HTML/CSS/JS pur. Aucune dépendance, aucun appel réseau hors 127.0.0.1.
   ========================================================================== */
(function () {
'use strict';

/* ── Contexte ───────────────────────────────────────────────────────────── */
var Q = new URLSearchParams(location.search);
var DEMO = Q.get('demo') === '1';
var SCENE = Q.get('scene') || '';
var FREEZE = Q.has('freeze') ? parseFloat(Q.get('freeze')) : null;

var TOKEN = (function () {
  var m = document.querySelector('meta[name="az-token"]');
  var t = m ? m.getAttribute('content') : '';
  if (t && t !== '__AZ_TOKEN__') return t;
  if (typeof window.AZ_TOKEN === 'string' && window.AZ_TOKEN) return window.AZ_TOKEN;
  if (document.body.dataset.azToken) return document.body.dataset.azToken;
  return '';
})();

var REDUCED = window.matchMedia('(prefers-reduced-motion: reduce)').matches;

/* Le module de démonstration est optionnel : sans lui, ?demo=1 retombe
   simplement sur le backend réel au lieu de lever une ReferenceError. */
var Demo = (typeof window.AZ_DEMO === 'object' && window.AZ_DEMO) ? window.AZ_DEMO : null;
if (DEMO && !Demo) DEMO = false;
if (DEMO) { var df = document.getElementById('demoFlag'); if (df) df.hidden = false; }

/* ── Petits outils ──────────────────────────────────────────────────────── */
var $ = function (id) { return document.getElementById(id); };
var $$ = function (sel, root) { return Array.prototype.slice.call((root || document).querySelectorAll(sel)); };
var nf = new Intl.NumberFormat('fr-FR');

function num(n) { return nf.format(Math.round(Number(n) || 0)); }

function bytes(b) {
  b = Number(b) || 0;
  if (b < 1024) return b + ' o';
  var u = ['Ko', 'Mo', 'Go', 'To'], i = -1, v = b;
  do { v /= 1024; i++; } while (v >= 1024 && i < u.length - 1);
  return (v >= 100 ? v.toFixed(0) : v.toFixed(1)).replace('.', ',') + ' ' + u[i];
}

function esc(s) {
  return String(s == null ? '' : s)
    .replace(/&/g, '&amp;').replace(/</g, '&lt;').replace(/>/g, '&gt;')
    .replace(/"/g, '&quot;').replace(/'/g, '&#39;');
}

/* Écrit un texte qui peut être un chemin : la troncature par la gauche
   n'est activée que si c'en est réellement un. */
var LRE = '\u202A', PDF = '\u202C';

function isPath(s) { return /[\\/]/.test(String(s == null ? '' : s)); }

function pathText(el, txt) {
  if (!el) return;
  var s = String(txt == null ? '' : txt);
  var p = isPath(s);
  el.classList.toggle('is-path', p);
  el.title = s;
  /* Le conteneur est en `direction:rtl` pour tronquer par la gauche et
     garder le nom du fichier ; l'incise LRE…PDF évite que la ponctuation
     (le « / » initial d'un chemin POSIX) ne saute à la fin. */
  el.textContent = p ? LRE + s + PDF : s;
}

/* Cellule de chemin prête à insérer dans une chaîne HTML. */
function pathCell(cls, txt) {
  var s = String(txt == null ? '' : txt);
  var p = isPath(s);
  return '<span class="' + cls + (p ? ' is-path' : '') + '" title="' + esc(s) + '">' +
    (p ? LRE + esc(s) + PDF : esc(s)) + '</span>';
}

function icon(name, cls) {
  return '<svg class="ic ' + (cls || '') + '"><use href="#i-' + name + '"/></svg>';
}

function hhmmss(d) {
  function p(n) { return String(n).padStart(2, '0'); }
  return p(d.getHours()) + ':' + p(d.getMinutes()) + ':' + p(d.getSeconds());
}

function shortDate(iso) {
  if (!iso) return 'inconnue';
  var d = new Date(iso);
  if (isNaN(d)) return String(iso);
  return d.toLocaleDateString('fr-FR', { day: '2-digit', month: 'short', year: 'numeric' }) +
         ' à ' + d.toLocaleTimeString('fr-FR', { hour: '2-digit', minute: '2-digit' });
}

function sleep(ms) { return new Promise(function (r) { setTimeout(r, ms); }); }

/* ── Trou noir : indicateur d'activité ──────────────────────────────────── */
var activeJobs = 0;

function bhState(el, state) {
  if (!el) return;
  el.classList.toggle('is-active', state === 'active');
  el.classList.toggle('is-idle', state === 'idle');
  el.classList.toggle('is-exec', state === 'exec');
}

/* Le trou noir de la console, et la légende qui le double.
   L'animation seule ne suffit pas : une pulsation plus rapide se remarque, mais
   ne se COMPREND pas. Le mot dit ce que l'image ne peut pas dire, et il reste
   lisible pour qui a désactivé les animations. */
var AS_ETATS = {
  idle:   'À l\'écoute',
  active: 'Je lis votre phrase…',
  exec:   'J\'exécute'
};

function asVisage(state) {
  var bh = $('asBh');
  if (!bh) return;
  bhState(bh, state);
  var face = bh.parentElement;
  if (face) face.dataset.etat = state;
  var mot = $('asFaceEtat');
  if (mot) mot.textContent = AS_ETATS[state] || AS_ETATS.idle;
}

function jobStarted() {
  activeJobs++;
  bhState($('heroBh'), 'active');
  $$('.jobbh').forEach(function (e) { bhState(e, 'active'); });
}

function jobEnded() {
  activeJobs = Math.max(0, activeJobs - 1);
  if (activeJobs === 0) {
    bhState($('heroBh'), 'idle');
    $$('.jobbh').forEach(function (e) { bhState(e, 'idle'); });
  }
}

/* ── Journal & notifications ────────────────────────────────────────────── */
function logLine(msg, tone) {
  var ol = $('log');
  if (!ol) return;
  var li = document.createElement('li');
  if (tone) li.dataset.tone = tone;
  li.innerHTML = '<time>' + hhmmss(new Date()) + '</time><span>' + esc(msg) + '</span>';
  ol.insertBefore(li, ol.firstChild);
  while (ol.children.length > 60) ol.removeChild(ol.lastChild);
}

var TONE_ICON = { ok: 'check', danger: 'alert', warn: 'alert', info: 'eye' };

function toast(title, msg, tone) {
  tone = tone || 'info';
  var wrap = $('toasts');
  var el = document.createElement('div');
  el.className = 'toast';
  el.dataset.tone = tone;
  el.innerHTML = icon(TONE_ICON[tone] || 'eye') +
    '<div><b>' + esc(title) + '</b>' + (msg ? '<p>' + esc(msg) + '</p>' : '') + '</div>';
  wrap.appendChild(el);
  setTimeout(function () {
    el.classList.add('is-out');
    setTimeout(function () { el.remove(); }, 320);
  }, tone === 'danger' ? 7000 : 4600);
  logLine(title + (msg ? ' — ' + msg : ''), tone);
}

/* ── Rendu des blocs de résultat ────────────────────────────────────────── */
function showResult(id, tone, title, html) {
  var el = $(id);
  if (!el) return;
  el.hidden = false;
  el.dataset.tone = tone;
  el.innerHTML = '<div class="result-head">' + icon(TONE_ICON[tone] || 'eye') +
    '<b>' + esc(title) + '</b></div>' + (html || '');
}

function kv(pairs) {
  return '<dl class="kv">' + pairs.map(function (p) {
    return '<div><dt>' + esc(p[0]) + '</dt><dd>' + p[1] + '</dd></div>';
  }).join('') + '</dl>';
}

/* ══════════════════════════════════════════════════════════════════════════
   COUCHE RÉSEAU — POST /api/<action> avec en-tête X-AZ-Token
   ══════════════════════════════════════════════════════════════════════════ */
function api(action, body) {
  if (DEMO) return Demo.call(action, body || {});
  return fetch('/api/' + action, {
    method: 'POST',
    headers: { 'Content-Type': 'application/json', 'X-AZ-Token': TOKEN },
    body: JSON.stringify(body || {})
  }).then(function (r) {
    if (r.status === 403) throw new Error('Jeton de session refusé (403). Rechargez la page servie par l\'outil.');
    if (r.status === 404) throw new Error('Action inconnue du backend : ' + action);
    return r.json();
  });
}

function apiJob(id) {
  if (DEMO) return Demo.job(id);
  return fetch('/api/job?id=' + encodeURIComponent(id), {
    headers: { 'X-AZ-Token': TOKEN }
  }).then(function (r) { return r.json(); });
}

/* Appel « sûr » : ne lève jamais, renvoie toujours une enveloppe normalisée. */
function call(action, body) {
  return api(action, body).then(function (res) {
    if (!res || typeof res !== 'object') return { ok: false, error: 'Réponse illisible du backend.' };
    return res;
  }).catch(function (e) {
    return { ok: false, error: e && e.message ? e.message : 'Backend injoignable.' };
  });
}

/* Signale proprement une réponse en échec ou un module indisponible. */
function handleFail(res, resultId, label) {
  if (res.unavailable) {
    var why = res.reason || res.error || 'Module indisponible sur cette plateforme.';
    if (resultId) showResult(resultId, 'warn', 'Action indisponible', '<p>' + esc(why) + '</p>');
    toast(label + ' — indisponible', why, 'warn');
  } else {
    var msg = res.error || 'Échec inattendu.';
    if (resultId) showResult(resultId, 'danger', 'Échec', '<p>' + esc(msg) + '</p>');
    toast(label + ' — échec', msg, 'danger');
  }
}

/* ── Sondage d'un job asynchrone ────────────────────────────────────────── */
var liveJobs = {};

/* Sonde un job déjà créé. Ne touche pas au compteur d'activité : c'est
   l'appelant (runJob / trackJob) qui l'ouvre et le referme. */
function pollJob(jid, hooks) {
  hooks = hooks || {};
  return new Promise(function (resolve) {
    var stopped = false;
    (function tick() {
      if (stopped) return;
      apiJob(jid).then(function (r) {
        var d = (r && r.data) || {};
        if (hooks.onProgress) hooks.onProgress(d);
        if (d.state === 'done') {
          stopped = true;
          if (hooks.onDone) hooks.onDone(d.result || {}, d);
          resolve(d.result || {});
        } else if (d.state === 'error' || (r && r.ok === false)) {
          stopped = true;
          if (hooks.onFail) hooks.onFail({ ok: false, error: d.error || (r && r.error) || 'Tâche interrompue.' });
          resolve(null);
        } else {
          setTimeout(tick, 420);
        }
      }).catch(function (e) {
        stopped = true;
        if (hooks.onFail) hooks.onFail({ ok: false, error: e.message });
        resolve(null);
      });
    })();
  });
}

/* Suit un job dont l'identifiant est déjà connu (cas du Mode gardien, dont
   le job naît de l'exécution destructive et non d'un appel direct). */
function trackJob(action, jid, hooks) {
  jobStarted();
  liveJobs[action] = jid;
  return pollJob(jid, hooks).then(function (r) {
    delete liveJobs[action];
    jobEnded();
    return r;
  });
}

function runJob(action, body, hooks) {
  hooks = hooks || {};
  jobStarted();
  return call(action, body).then(function (res) {
    if (!res.ok) { jobEnded(); if (hooks.onFail) hooks.onFail(res); return null; }
    var jid = res.data && res.data.job_id;
    if (!jid) { jobEnded(); if (hooks.onDone) hooks.onDone(res.data || {}); return res.data; }
    liveJobs[action] = jid;
    return pollJob(jid, hooks).then(function (r) {
      delete liveJobs[action];
      jobEnded();
      return r;
    });
  });
}


/* ══════════════════════════════════════════════════════════════════════════
   DOUBLE VALIDATION DESTRUCTIVE
   1. dry_run:true  → le backend renvoie le plan exact + un confirm_token
   2. lecture du plan par l'utilisateur + accusé de lecture explicite
   3. dry_run:false + confirm_token → exécution réelle
   ══════════════════════════════════════════════════════════════════════════ */

/* Normalise un plan quelle que soit la forme exacte renvoyée par le module. */
/* Le contrat renvoie { dry_run, action, plan: <libre>, confirm_token }.
   `plan` prend une forme différente par module : on la ramène ici à une
   liste homogène { path, target, size, note } + un décompte + une taille. */
function normalizePlan(d) {
  d = d || {};
  var src = d;
  if (d.plan != null) src = Array.isArray(d.plan) ? { items: d.plan } : d.plan;
  if (!src || typeof src !== 'object') src = {};

  var items = null;
  ['items', 'files', 'targets', 'moves', 'apps', 'entries', 'candidates', 'results', 'plan']
    .forEach(function (k) { if (items === null && Array.isArray(src[k])) items = src[k]; });

  /* Mode gardien : le plan décrit des ÉTAPES appliquées à des DOSSIERS, et
     non une liste de fichiers. On présente donc les dossiers, chacun annoté
     du nombre de fichiers candidats au rangement. */
  if (items === null && Array.isArray(src.folders) && Array.isArray(src.steps)) {
    var prev = {};
    (src.least_used_preview || []).forEach(function (pv) { prev[pv.folder] = pv; });
    items = src.folders.map(function (f) {
      var pv = prev[f] || {};
      return { path: f, note: pv.error ? 'dossier illisible' : num(pv.candidates || 0) + ' à ranger' };
    });
  }

  /* Plans à élément unique (déplacement de dossier, désinstallation, entrée
     de démarrage) : on les présente comme une liste d'un seul élément. */
  if (items === null) {
    if (src.source && (src.destination || src.target)) {
      items = [{ path: src.source, target: src.destination || src.target }];
    } else if (src.app && typeof src.app === 'object') {
      items = [src.app];
    } else if (src.name && src.hive) {
      items = [{ path: src.name, target: src.hive + ' · ' + (src.key_path || '') }];
    } else {
      items = [];
    }
  }

  var norm = items.map(function (it) {
    if (typeof it === 'string') return { path: it, size: null, target: null, note: null };
    var size = it.size != null ? it.size
             : (it.bytes != null ? it.bytes
             : (it.size_bytes != null ? it.size_bytes
             : (it.size_mb != null ? Number(it.size_mb) * 1024 * 1024 : null)));
    var note = null;
    if (it.admin_required) note = 'droits administrateur';
    else if (it.age_days != null) note = num(it.age_days) + ' j sans usage';
    else if (it.count != null && it.paths) note = num(it.count) + ' copies';
    else if (it.reason && size == null) note = String(it.reason);
    /* Un déplacement se lit « fichier → dossier d'arrivée » : quand le module
       fournit un libellé de destination, il est plus parlant que le chemin
       complet recopié en entier. */
    var target = it.target || it.dest || it.to || null;
    if (!target && it.destination) target = it.label || it.destination;
    return {
      path: it.path || it.source || it.file || it.original_path || it.name ||
            it.display_name || it.label || it.entry || it.subkey || '(élément)',
      size: size,
      target: target,
      note: note
    };
  });

  var total = src.count != null ? src.count
            : (src.total != null ? src.total
            : (src.total_files != null ? src.total_files : norm.length));
  var size = src.bytes != null ? src.bytes
           : (src.size != null ? src.size
           : (src.total_size != null ? src.total_size
           : (src.total_bytes != null ? src.total_bytes
           : (src.freed_bytes != null ? src.freed_bytes
           : (src.total_size_mb != null ? Number(src.total_size_mb) * 1024 * 1024
           : (src.space_freed != null ? src.space_freed : null))))));
  if (size == null) {
    var s = 0, any = false;
    norm.forEach(function (i) { if (i.size != null) { s += Number(i.size) || 0; any = true; } });
    size = any ? s : null;
  }
  return { items: norm, count: Number(total) || 0, size: size, raw: src };
}


var sheetCtx = null;
var sheetTimer = null;

function closeSheet(reason) {
  if (sheetTimer) { clearInterval(sheetTimer); sheetTimer = null; }
  var w = $('sheetWrap');
  w.hidden = true;
  $('sheetAck').checked = false;
  $('sheetGo').disabled = true;
  if (sheetCtx && sheetCtx.reject && reason === 'cancel') sheetCtx.reject();
  sheetCtx = null;
  if (document.body.dataset.lastFocus) {
    var el = $(document.body.dataset.lastFocus);
    if (el) el.focus();
    delete document.body.dataset.lastFocus;
  }
}

/**
 * opts = { action, body, title, lead, label, reversible, execLabel, kicker,
 *          onDone(data), resultId, planTitle }
 */
function destructive(opts) {
  var label = opts.label || opts.title;
  var dry = {};
  Object.keys(opts.body || {}).forEach(function (k) { dry[k] = opts.body[k]; });
  dry.dry_run = true;

  return call(opts.action, dry).then(function (res) {
    if (!res.ok) { handleFail(res, opts.resultId, label); return; }
    var data = res.data || {};
    var token = data.confirm_token || data.token;
    var plan = normalizePlan(data);

    if (!token) {
      toast(label, 'Le backend n\'a pas fourni de jeton de confirmation : opération refusée par sécurité.', 'danger');
      return;
    }
    if (plan.count === 0 && plan.items.length === 0) {
      showResult(opts.resultId || 'noop', 'ok', 'Rien à faire',
        '<p>L\'analyse à blanc n\'a trouvé aucun élément concerné. Aucune action n\'a été exécutée.</p>');
      toast(label, 'Aucun élément concerné, rien n\'a été touché.', 'ok');
      return;
    }
    return openSheet(opts, plan, token);
  });
}

function openSheet(opts, plan, token) {
  var wrap = $('sheetWrap');
  var reversible = !!opts.reversible;
  wrap.dataset.kind = reversible ? 'soft' : 'hard';
  wrap.hidden = false;

  $('sheetKicker').textContent = reversible ? 'Validation requise — opération réversible' : 'Validation requise — suppression définitive';
  $('sheetTitle').textContent = opts.title;
  $('sheetLead').textContent = opts.lead || 'Voici exactement ce qui sera modifié. Rien n\'a encore été touché.';

  var unit = opts.unit || 'élément';
  var figs = [[opts.countLabel || 'Éléments concernés', num(plan.count)]];
  if (plan.size != null) figs.push(['Espace concerné', bytes(plan.size)]);
  if (plan.raw.locations != null) figs.push(['Emplacements', num(plan.raw.locations)]);
  if (plan.raw.errors != null && plan.raw.errors) figs.push(['Inaccessibles', num(plan.raw.errors)]);
  if (figs.length === 1) figs.push(['Nature de l\'opération', reversible ? 'Réversible' : 'Définitive']);
  $('sheetFigs').innerHTML = figs.map(function (f, i) {
    return '<dl class="sfig' + (i === 0 && !reversible ? ' is-hot' : '') + '"><dt>' + esc(f[0]) + '</dt><dd>' + esc(f[1]) + '</dd></dl>';
  }).join('');

  var shown = plan.items.slice(0, 60);
  var planHtml = '';
  if (shown.length) {
    planHtml = '<div class="sheet-plan-h">' + icon('file') + (opts.planTitle || 'Plan exact') +
      ' — ' + num(plan.items.length) + ' élément' + (plan.items.length > 1 ? 's' : '') + ' listé' + (plan.items.length > 1 ? 's' : '') + '</div>' +
      '<ul class="sheet-plan-l">' + shown.map(function (it) {
        var right = it.target ? ('→ ' + esc(it.target))
                  : (it.size != null ? esc(bytes(it.size))
                  : (it.note ? esc(it.note) : ''));
        return '<li>' + pathCell('pp', it.path) +
               (right ? '<span class="ps">' + right + '</span>' : '') + '</li>';
      }).join('') + '</ul>';
    if (plan.items.length > shown.length) {
      planHtml += '<p class="sheet-plan-more">… et ' + num(plan.items.length - shown.length) + ' autre(s) élément(s) du même plan.</p>';
    }
  } else {
    planHtml = '<div class="sheet-plan-h">' + icon('file') + 'Plan</div>' +
      '<p class="sheet-plan-more">Le module n\'a pas détaillé la liste ; ' + num(plan.count) + ' élément(s) seront traités.</p>';
  }
  if (plan.raw && Array.isArray(plan.raw.steps) && plan.raw.steps.length) {
    planHtml = '<div class="sheet-plan-h">' + icon('arrow') + 'Déroulé de l\'opération</div>' +
      '<ol class="sheet-steps">' + plan.raw.steps.map(function (st) {
        return '<li>' + esc(st) + '</li>';
      }).join('') + '</ol>' + planHtml;
  }
  if (plan.raw && plan.raw.note) {
    planHtml += '<p class="sheet-plan-more">' + esc(plan.raw.note) + '</p>';
  }
  $('sheetPlan').innerHTML = planHtml;

  var rev = $('sheetRev');
  rev.dataset.rev = reversible ? 'true' : 'false';
  rev.innerHTML = icon(reversible ? 'undo' : 'alert') + '<span>' + (opts.revNote ? esc(opts.revNote) : reversible
    ? 'Opération réversible : les éléments sont déplacés dans un sas et restent récupérables depuis la section Rangement.'
    : '<b>Opération irréversible.</b> Les éléments listés seront supprimés définitivement et ne pourront pas être restaurés.') + '</span>';

  $('sheetAckTxt').textContent = reversible
    ? 'J\'ai lu le plan ci-dessus et je valide cette opération sur ' + num(plan.count) + ' ' + unit + '(s).'
    : 'J\'ai lu le plan ci-dessus et j\'accepte le traitement définitif de ces ' + num(plan.count) + ' ' + unit + '(s).';
  $('sheetGo').textContent = opts.execLabel || (reversible ? 'Appliquer' : 'Supprimer définitivement');

  /* Compte à rebours réel du jeton (5 minutes selon le contrat) */
  var left = 300;
  var tEl = $('sheetTimer'), tWrap = tEl.parentElement;
  function paint() {
    var m = Math.floor(left / 60), s = left % 60;
    tEl.textContent = m + ':' + String(s).padStart(2, '0');
    tWrap.classList.toggle('is-low', left <= 45);
  }
  paint();
  if (sheetTimer) clearInterval(sheetTimer);
  sheetTimer = setInterval(function () {
    left--;
    if (left <= 0) {
      clearInterval(sheetTimer); sheetTimer = null;
      closeSheet();
      toast(opts.title, 'Le jeton de confirmation a expiré. Relancez l\'analyse à blanc.', 'warn');
      return;
    }
    paint();
  }, 1000);

  setTimeout(function () { $('sheetAck').focus(); }, 60);

  return new Promise(function (resolve) {
    sheetCtx = {
      run: function () {
        var body = {};
        Object.keys(opts.body || {}).forEach(function (k) { body[k] = opts.body[k]; });
        body.dry_run = false;
        body.confirm_token = token;
        var go = $('sheetGo');
        go.disabled = true;
        go.textContent = 'Exécution…';
        call(opts.action, body).then(function (res) {
          closeSheet();
          if (!res.ok) { handleFail(res, opts.resultId, opts.title); resolve(null); return; }
          toast(opts.title, 'Opération exécutée : ' + num(plan.count) + ' élément(s) traité(s).', 'ok');
          if (opts.onDone) opts.onDone(res.data || {}, plan);
          resolve(res.data || {});
        });
      },
      reject: function () { resolve(null); }
    };
  });
}

$('sheetAck').addEventListener('change', function () { $('sheetGo').disabled = !this.checked; });
$('sheetGo').addEventListener('click', function () { if (sheetCtx) sheetCtx.run(); });
$('sheetCancel').addEventListener('click', function () { closeSheet('cancel'); });
$('sheetClose').addEventListener('click', function () { closeSheet('cancel'); });
$('sheetVeil').addEventListener('click', function () { closeSheet('cancel'); });
document.addEventListener('keydown', function (e) {
  if (e.key === 'Escape' && !$('sheetWrap').hidden) closeSheet('cancel');
});

/* ══════════════════════════════════════════════════════════════════════════
   NAVIGATION
   ══════════════════════════════════════════════════════════════════════════ */
var VIEWS = ['tableau-de-bord', 'assistant', 'historique', 'protection', 'securite', 'nettoyage', 'rangement', 'systeme'];

function route() {
  var name = (location.hash || '').replace(/^#\//, '');
  if (VIEWS.indexOf(name) === -1) name = 'tableau-de-bord';
  VIEWS.forEach(function (v) {
    var sec = $('view-' + v);
    if (sec) sec.hidden = (v !== name);
  });
  $$('.rail-i').forEach(function (a) { a.classList.toggle('is-on', a.dataset.view === name); });
  $('rail').classList.remove('is-open');
  $('btnNav').setAttribute('aria-expanded', 'false');
  window.scrollTo({ top: 0, behavior: REDUCED ? 'auto' : 'smooth' });
}
window.addEventListener('hashchange', route);

$('btnNav').addEventListener('click', function () {
  var open = $('rail').classList.toggle('is-open');
  this.setAttribute('aria-expanded', open ? 'true' : 'false');
});

$$('[data-goto]').forEach(function (b) {
  b.addEventListener('click', function () {
    location.hash = b.dataset.goto;
    var f = b.dataset.focus;
    if (f) setTimeout(function () {
      var p = $(f);
      if (p) {
        p.scrollIntoView({ behavior: REDUCED ? 'auto' : 'smooth', block: 'center' });
        var inp = p.querySelector('.inp');
        if (inp && !p.classList.contains('is-off')) inp.focus();
      }
    }, 120);
  });
});

/* Horloge du poste */
(function clock() {
  var el = $('clock');
  function t() { el.textContent = hhmmss(new Date()); }
  t(); setInterval(t, 1000);
})();

$('btnClearLog').addEventListener('click', function () { $('log').innerHTML = ''; });

/* ══════════════════════════════════════════════════════════════════════════
   ÉTAT GLOBAL / TABLEAU DE BORD
   ══════════════════════════════════════════════════════════════════════════ */
var MODULE_LABELS = {
  hash_scanner: 'Signatures (empreintes)', yara_scanner: 'Moteur YARA', heuristics: 'Analyse heuristique',
  quarantine: 'Quarantaine', realtime_monitor: 'Protection temps réel',
  ransomware_shield: 'Bouclier anti-rançongiciel', reputation_checker: 'Réputation cloud',
  phishing_checker: 'Vérification de liens', temp_cleaner: 'Nettoyage temporaire',
  disk_analyzer: 'Analyse disque', residue_cleaner: 'Résidus d\'applications',
  app_manager: 'Gestion des applications', file_triage: 'Tri de fichiers',
  folder_organizer: 'Réorganisation', startup_manager: 'Programmes au démarrage',
  task_scheduler: 'Planificateur de tâches', guardian: 'Mode gardien',
  network_watch: 'Connexions sortantes', intrusion_check: 'Accès à cette machine',
  camera_watch: 'Caméra et microphone', incident_mode: 'Mode Incident',
  history: 'Historique unifié'
};

/* Les panneaux portent une clé d'interface (data-mod) ; le backend, lui,
   nomme ses modules autrement. Une clé d'interface peut dépendre de
   plusieurs modules : elle est indisponible dès que l'un manque. */
var MOD_ALIAS = {
  scanner: ['hash_scanner', 'yara_scanner', 'heuristics'],
  realtime: ['realtime_monitor'],
  shield: ['ransomware_shield'],
  reputation: ['reputation_checker'],
  phishing: ['phishing_checker']
};

function modState(modules, key) {
  var keys = MOD_ALIAS[key] || [key];
  for (var i = 0; i < keys.length; i++) {
    var m = modules && modules[keys[i]];
    if (m && m.available === false) return m;
  }
  return null;
}

var STATUS = null;

function setRing(el, ratio) {
  var C = 766.5;
  el.style.strokeDashoffset = String(C * (1 - Math.max(0, Math.min(1, ratio))));
}

function tchip(id, tone, value) {
  var c = $(id);
  c.dataset.tone = tone;
  c.querySelector('.tchip-v').textContent = value;
}

/* Applique la disponibilité des modules à chaque panneau concerné. */
/* Applique la disponibilité des modules à chaque panneau concerné.
   Un panneau indisponible reste visible et lisible : il est simplement
   désactivé et porte la raison exacte renvoyée par le backend. */
function applyModules(modules) {
  $$('[data-mod]').forEach(function (panel) {
    var m = modState(modules, panel.dataset.mod);
    var off = !!m;
    panel.classList.toggle('is-off', off);
    var old = panel.querySelector('.off-note');
    if (old) old.remove();
    var tag = panel.querySelector('.off-tag');
    if (tag) tag.remove();

    if (off) {
      var head = panel.querySelector('.p-head');
      var t = document.createElement('span');
      t.className = 'off-tag';
      t.textContent = 'Indisponible';
      /* Juste après le titre : sinon l'étiquette passe à la ligne quand
         l'en-tête porte déjà des outils alignés à droite. */
      head.querySelector('h3').insertAdjacentElement('afterend', t);

      var note = document.createElement('p');
      note.className = 'off-note';
      note.innerHTML = icon('alert') + '<span><b>Indisponible sur cette plateforme.</b> ' +
        esc(m.reason || 'Le module requis n\'est pas présent.') +
        ' Les commandes de ce panneau sont désactivées, l\'interface reste navigable.</span>';
      head.insertAdjacentElement('afterend', note);

      $$('button, input, select', panel).forEach(function (c) {
        if (c.closest('.p-head') && c.classList.contains('linkbtn')) { c.disabled = true; c.dataset.offLock = '1'; return; }
        if (c.disabled) return;              // déjà désactivé pour une autre raison
        c.disabled = true;
        c.dataset.offLock = '1';
      });
    } else {
      /* Module redevenu disponible : on ne relâche QUE ce que l'on avait
         verrouillé, jamais les boutons désactivés par leur propre logique. */
      $$('[data-off-lock]', panel).forEach(function (c) {
        c.disabled = false;
        delete c.dataset.offLock;
      });
    }
  });

  var list = $('modList');
  var keys = Object.keys(modules || {});
  if (!keys.length) { list.innerHTML = '<li class="empty">Aucun module déclaré par le backend.</li>'; return; }
  list.innerHTML = keys.map(function (k) {
    var m = modules[k] || {};
    var ok = m.available !== false;
    return '<li><span class="mod-dot' + (ok ? '' : ' off') + '"></span><span class="mod-txt">' +
      '<b>' + esc(MODULE_LABELS[k] || k) + '</b>' +
      '<small title="' + esc(ok ? 'Opérationnel' : (m.reason || 'Indisponible')) + '">' +
      esc(ok ? 'Opérationnel' : (m.reason || 'Indisponible')) + '</small></span></li>';
  }).join('');
}


function paintStatus(d) {
  STATUS = d;
  var plat = d.platform || 'inconnue';
  tchip('chipPlatform', plat === 'Windows' ? 'ok' : 'warn', plat);
  tchip('chipAdmin', d.is_admin ? 'ok' : 'warn', d.is_admin ? 'administrateur' : 'standard');
  tchip('chipRealtime', d.realtime_active ? 'ok' : 'warn', d.realtime_active ? 'actif' : 'inactif');
  tchip('chipShield', d.shield_active ? 'ok' : 'warn', d.shield_active ? 'déployé' : 'inactif');

  $('rtLamp').dataset.on = String(!!d.realtime_active);
  $('shLamp').dataset.on = String(!!d.shield_active);

  var sig = d.signatures || {};
  document.querySelector('[data-f="hashes"]').textContent = num(sig.hashes || 0);
  document.querySelector('[data-f="yara"]').textContent = num(sig.yara_rules || 0);
  document.querySelector('[data-f="sigdate"]').textContent = shortDate(sig.last_update);
  document.querySelector('[data-f="quarantine"]').textContent = num(d.quarantine_count || 0);
  document.querySelector('[data-f="staging"]').textContent = num(d.staging_count || 0);

  $('vtBadge').textContent = d.vt_configured ? 'Clé configurée' : 'Clé absente';
  $('vtBadge').style.color = d.vt_configured ? 'var(--ok)' : 'var(--tx-3)';

  applyModules(d.modules || {});

  /* Indice de protection : composite lisible, jamais alarmiste sans raison. */
  var score = 40;
  if (d.realtime_active) score += 20;
  if (d.shield_active) score += 15;
  if ((sig.hashes || 0) > 0) score += 10;
  if ((sig.yara_rules || 0) > 0) score += 10;
  if (d.is_admin) score += 5;
  if ((d.quarantine_count || 0) > 0) score -= 10;
  score = Math.max(5, Math.min(100, score));

  setRing($('scoreRing'), score / 100);
  animateNumber($('scoreVal'), score);

  var tone, title, sub;
  if (score >= 85) { tone = 'ok'; title = 'Machine protégée'; sub = 'Toutes les défenses essentielles sont actives. Aucune action urgente n\'est requise.'; }
  else if (score >= 60) { tone = 'warn'; title = 'Protection partielle'; sub = 'Le socle fonctionne, mais certaines défenses restent inactives. Activez-les pour couvrir toute la surface.'; }
  else { tone = 'danger'; title = 'Protection insuffisante'; sub = 'Plusieurs défenses sont hors service. Lancez une analyse et activez la surveillance continue.'; }

  if ((d.quarantine_count || 0) > 0) {
    sub += ' ' + num(d.quarantine_count) + ' élément(s) attendent une décision en quarantaine.';
  }
  $('postureTitle').textContent = title;
  $('postureSub').textContent = sub;

  var tags = [
    ['Temps réel', d.realtime_active ? 'ok' : 'warn', d.realtime_active ? 'actif' : 'inactif'],
    ['Bouclier', d.shield_active ? 'ok' : 'warn', d.shield_active ? 'déployé' : 'inactif'],
    ['Réputation cloud', d.vt_configured ? 'ok' : 'info', d.vt_configured ? 'configurée' : 'non configurée'],
    ['Quarantaine', (d.quarantine_count || 0) > 0 ? 'danger' : 'ok', num(d.quarantine_count || 0) + ' élément(s)']
  ];
  if (d.incident && d.incident.actif) {
    tags.unshift(['Mode Incident', 'danger', 'ACTIF']);
  }
  $('postureTags').innerHTML = tags.map(function (t) {
    return '<span class="chip" data-tone="' + t[1] + '">' + esc(t[0]) + ' · ' + esc(t[2]) + '</span>';
  }).join('');

  /* Mode incident : l'état accompagne chaque rafraîchissement (toutes les
     45 s). Un mode resté actif ne peut donc pas passer inaperçu, même si
     l'utilisateur n'a jamais ouvert le panneau Sécurité avancée. */
  if (d.incident) paintIncident(d.incident);
  setCamLamp(!!d.camera_watch_active);

  /* La pastille décrit la LIAISON, pas la posture de sécurité : le backend a
     répondu, donc elle est verte même si l'indice de protection est bas. */
  $('sessDot').dataset.tone = 'ok';
  $('sessState').textContent = DEMO ? 'Démonstration' : 'Liaison établie';
  $('sessSub').textContent = DEMO ? 'aucun backend' : (TOKEN ? '127.0.0.1 · jeton actif' : '127.0.0.1 · jeton absent');
}

function animateNumber(el, target) {
  if (REDUCED) { el.textContent = num(target); return; }
  var start = 0, t0 = performance.now(), dur = 900;
  (function step(t) {
    var k = Math.min(1, (t - t0) / dur);
    var e = 1 - Math.pow(1 - k, 3);
    el.textContent = num(start + (target - start) * e);
    if (k < 1) requestAnimationFrame(step);
  })(t0);
}

function loadStatus(silent) {
  var btn = $('btnRefresh');
  btn.classList.add('is-spin');
  return call('status', {}).then(function (res) {
    btn.classList.remove('is-spin');
    if (!res.ok) {
      $('sessDot').dataset.tone = 'danger';
      $('sessState').textContent = 'Backend injoignable';
      $('sessSub').textContent = res.error || '';
      $('modList').innerHTML = '<li class="empty">' + esc(res.error || 'Aucune donnée.') + '</li>';
      $('postureTitle').textContent = 'État indéterminé';
      $('postureSub').textContent = res.error || 'Le service local ne répond pas. L\'interface reste utilisable en lecture.';
      if (!silent) toast('État', res.error || 'Backend injoignable.', 'danger');
      return;
    }
    paintStatus(res.data || {});
    if (!silent) logLine('État du système rafraîchi.', 'ok');
  });
}

$('btnRefresh').addEventListener('click', function () { loadStatus(); });

/* ══════════════════════════════════════════════════════════════════════════
   PROTECTION
   ══════════════════════════════════════════════════════════════════════════ */

/* ── Analyse d'un dossier (job asynchrone + poste de scan) ──────────────── */
function setScan(state, d) {
  var hud = $('scanHud');
  hud.dataset.state = state;
  bhState($('scanBh'), state === 'running' ? 'active' : 'idle');

  var chip = $('scanState');
  var map = {
    idle: ['En attente', ''], running: ['Analyse en cours', 'brand'],
    done: ['Analyse terminée', 'ok'], error: ['Analyse interrompue', 'danger']
  };
  chip.textContent = map[state][0];
  if (map[state][1]) chip.dataset.tone = map[state][1]; else delete chip.dataset.tone;
  $('btnScanCancel').disabled = (state !== 'running');
  $('scanBar').parentElement.classList.toggle('is-idle', state !== 'running');

  if (!d) return;
  var p = Math.max(0, Math.min(1, Number(d.progress) || 0));
  $('scanBar').style.width = (p * 100).toFixed(1) + '%';
  $('scanPct').textContent = Math.round(p * 100) + ' %';
  setRing($('scanRing'), p);
  $('scanDone').textContent = num(d.done || 0);
  $('scanTotal').textContent = num(d.total || 0);
  if (d.current) pathText($('scanCurrent'), d.current);
  var th = d.threats != null ? d.threats : (d.result && d.result.threats) || 0;
  $('scanThreats').textContent = num(th);
  $('scanThreats').parentElement.classList.toggle('is-zero', !th);
}

$('btnScanDir').addEventListener('click', function () {
  var path = $('scanDirPath').value.trim() || $('scanDirPath').placeholder;
  $('scanDirResult').hidden = true;
  setScan('running', { progress: 0, done: 0, total: 0, current: 'Initialisation du moteur…' });
  logLine('Analyse lancée sur ' + path);

  runJob('scan_directory', { path: path }, {
    onProgress: function (d) { setScan('running', d); },
    onDone: function (result, d) {
      var th = result.threat_count != null ? result.threat_count
             : (Array.isArray(result.threats) ? result.threats.length : 0);
      setScan('done', Object.assign({ progress: 1 }, d || {}, { threats: th }));
      var files = result.files_scanned != null ? result.files_scanned : (d && d.done) || 0;
      showResult('scanDirResult', th ? 'danger' : 'ok',
        th ? num(th) + ' menace(s) détectée(s)' : 'Aucune menace détectée',
        kv([['Fichiers analysés', num(files)],
            ['Menaces', num(th)],
            ['Dossier', pathCell('mono', result.path || path)]]) +
        (th && Array.isArray(result.threats) ? renderDetections(result.threats) : ''));
      toast('Analyse terminée', th ? num(th) + ' menace(s) trouvée(s) et isolée(s).' : 'Aucune menace sur ' + num(files) + ' fichier(s).', th ? 'danger' : 'ok');
      loadStatus(true);
    },
    onFail: function (res) {
      setScan('error');
      /* L'interruption est un choix de l'utilisateur, pas une panne. */
      if (/annul/i.test(res.error || '')) {
        showResult('scanDirResult', 'warn', 'Analyse interrompue',
          '<p>L\'analyse a été arrêtée à votre demande. Les fichiers déjà examinés ont été traités normalement.</p>');
        return;
      }
      handleFail(res, 'scanDirResult', 'Analyse');
    }
  });
});

function renderDetections(list) {
  return '<div class="tbl">' + list.slice(0, 12).map(function (x) {
    var f = x.file || x.path || 'Élément suspect';
    var why = [];
    if (x.hash_scan && x.hash_scan.matched) why.push('signature connue');
    if (x.yara_scan && x.yara_scan.clean === false) why.push('YARA : ' + (x.yara_scan.reason || 'règle déclenchée'));
    if (x.heuristic_scan && x.heuristic_scan.clean === false) why.push('heuristique : ' + (x.heuristic_scan.reason || 'comportement suspect'));
    return '<div class="trow"><span class="chip" data-tone="danger">' + esc(x.verdict || 'MENACE') + '</span>' +
      '<span class="tmain"><b>' + esc(f.split(/[\\/]/).pop()) + '</b><small>' + esc(why.join(' · ') || f) + '</small></span>' +
      (x.quarantined ? '<span class="chip" data-tone="ok">isolé</span>' : '') + '</div>';
  }).join('') + '</div>';
}

$('btnScanCancel').addEventListener('click', function () {
  var jid = liveJobs['scan_directory'];
  call('job_cancel', { id: jid }).then(function () {
    setScan('error');
    toast('Analyse', 'Interruption demandée.', 'warn');
  });
});

/* ── Analyse d'un fichier ───────────────────────────────────────────────── */
$('btnScanFile').addEventListener('click', function () {
  var path = $('scanFilePath').value.trim();
  if (!path) { toast('Analyse de fichier', 'Indiquez un chemin de fichier.', 'warn'); return; }
  call('scan_file', { path: path }).then(function (res) {
    if (!res.ok) { handleFail(res, 'scanFileResult', 'Analyse de fichier'); return; }
    var d = res.data || {};
    var hs = d.hash_scan || {}, ys = d.yara_scan || {}, he = d.heuristic_scan || {};
    var bad = String(d.verdict || '').toUpperCase() === 'MALVEILLANT';
    var err = String(d.verdict || '').toUpperCase() === 'ERREUR';
    var digest = hs.hash ? String(hs.hash).slice(0, 24) + '…' : '—';
    showResult('scanFileResult', bad ? 'danger' : (err ? 'warn' : 'ok'),
      bad ? 'Fichier malveillant' : (err ? 'Fichier illisible' : 'Fichier sain'),
      kv([['Verdict', esc(d.verdict || 'inconnu')],
          ['Empreinte SHA-256', '<span class="mono">' + esc(digest) + '</span>'],
          ['Règles YARA', esc(ys.matches && ys.matches.length
              ? ys.matches.map(function (m) { return m.rule; }).join(', ') : (ys.reason || 'aucune'))],
          ['Heuristique', esc(he.risk_score != null ? 'score ' + he.risk_score + ' — ' + (he.reason || '') : (he.reason || '—'))]]));
    toast('Analyse de fichier', bad ? 'Menace détectée dans ce fichier.' : 'Aucune menace détectée.', bad ? 'danger' : 'ok');
  });
});

/* ── Protection temps réel ──────────────────────────────────────────────── */
function splitPaths(v) {
  return v.split(',').map(function (s) { return s.trim(); }).filter(Boolean);
}

$('btnRtStart').addEventListener('click', function () {
  var folders = splitPaths($('rtFolders').value || $('rtFolders').placeholder);
  call('realtime_start', { folders: folders }).then(function (res) {
    if (!res.ok) { handleFail(res, 'rtResult', 'Temps réel'); return; }
    $('rtLamp').dataset.on = 'true';
    showResult('rtResult', 'ok', 'Surveillance active',
      '<p>' + num(folders.length) + ' dossier(s) sous surveillance continue. Le poste reste utilisable.</p>');
    toast('Temps réel', 'Surveillance démarrée.', 'ok');
    loadStatus(true);
  });
});

$('btnRtStop').addEventListener('click', function () {
  call('realtime_stop', {}).then(function (res) {
    if (!res.ok) { handleFail(res, 'rtResult', 'Temps réel'); return; }
    $('rtLamp').dataset.on = 'false';
    showResult('rtResult', 'warn', 'Surveillance arrêtée', '<p>Les dossiers ne sont plus surveillés en continu.</p>');
    toast('Temps réel', 'Surveillance arrêtée.', 'warn');
    loadStatus(true);
  });
});

/* ── Quarantaine ────────────────────────────────────────────────────────── */
function loadQuarantine() {
  call('quarantine_list', {}).then(function (res) {
    var t = $('qTable');
    if (!res.ok) { handleFail(res, null, 'Quarantaine'); t.innerHTML = '<p class="empty">' + esc(res.reason || res.error) + '</p>'; return; }
    var items = (res.data && (res.data.items || res.data)) || [];
    if (!items.length) { t.innerHTML = '<p class="empty">La quarantaine est vide.</p>'; return; }
    t.innerHTML = '<div class="tbl-scroll">' + items.map(function (it) {
      return '<div class="trow" data-qid="' + esc(it.id) + '">' +
        '<span class="chip" data-tone="danger">' + esc(it.verdict || 'isolé') + '</span>' +
        '<span class="tmain"><b>' + esc(it.name || it.id) + '</b><small>' + esc(it.original_path || '') + '</small></span>' +
        '<span class="tsize">' + esc(it.size != null ? bytes(it.size) : '') + '</span>' +
        '<span class="tact">' +
          '<button class="btn btn-ghost btn-sm" data-qrestore="' + esc(it.id) + '">Restaurer</button>' +
          '<button class="btn btn-danger btn-sm" data-qdelete="' + esc(it.id) + '">Supprimer</button>' +
        '</span></div>';
    }).join('') + '</div>';
  });
}

$('btnQList').addEventListener('click', loadQuarantine);

$('qTable').addEventListener('click', function (e) {
  var r = e.target.closest('[data-qrestore]');
  var d = e.target.closest('[data-qdelete]');
  if (r) {
    call('quarantine_restore', { id: r.dataset.qrestore }).then(function (res) {
      if (!res.ok) { handleFail(res, null, 'Restauration'); return; }
      toast('Quarantaine', 'Fichier restauré à son emplacement d\'origine.', 'ok');
      loadQuarantine(); loadStatus(true);
    });
  }
  if (d) {
    var row = d.closest('.trow');
    var name = row.querySelector('.tmain b').textContent;
    destructive({
      action: 'quarantine_delete',
      body: { id: d.dataset.qdelete },
      title: 'Supprimer définitivement un élément en quarantaine',
      lead: '« ' + name +' » sera effacé du coffre de quarantaine. Cette suppression ne peut pas être annulée.',
      label: 'Quarantaine',
      reversible: false,
      onDone: function () {
        row.classList.add('is-gone');
        setTimeout(function () { loadQuarantine(); loadStatus(true); }, 520);
      }
    });
  }
});

/* ── Bouclier anti-rançongiciel ─────────────────────────────────────────── */
$('btnShStart').addEventListener('click', function () {
  var folders = splitPaths($('shFolders').value || $('shFolders').placeholder);
  call('shield_start', { folders: folders }).then(function (res) {
    if (!res.ok) { handleFail(res, 'shResult', 'Bouclier'); return; }
    $('shLamp').dataset.on = 'true';
    var d = res.data || {};
    showResult('shResult', 'ok', 'Leurres déployés',
      kv([['Fichiers leurres', num(d.canaries || folders.length)], ['Dossiers couverts', num(folders.length)]]) +
      '<p>Toute modification d\'un leurre déclenchera une alerte immédiate.</p>');
    toast('Bouclier', 'Leurres déployés.', 'ok');
    loadStatus(true);
  });
});

$('btnShStatus').addEventListener('click', function () {
  call('shield_status', {}).then(function (res) {
    if (!res.ok) { handleFail(res, 'shResult', 'Bouclier'); return; }
    var d = res.data || {};
    var breached = d.breached || d.tampered || 0;
    showResult('shResult', breached ? 'danger' : 'ok',
      breached ? 'Leurres altérés — activité suspecte' : 'Leurres intacts',
      kv([['Leurres surveillés', num(d.canaries || 0)],
          ['Altérés', num(breached)],
          ['Seuil adaptatif', esc(d.threshold != null ? d.threshold : '—')]]));
  });
});

$('btnShProc').addEventListener('click', function () {
  call('shield_processes', {}).then(function (res) {
    if (!res.ok) { handleFail(res, 'shResult', 'Bouclier'); return; }
    var list = (res.data && (res.data.processes || res.data.items)) || [];
    showResult('shResult', list.length ? 'warn' : 'ok',
      list.length ? num(list.length) + ' processus à surveiller' : 'Aucun processus suspect',
      list.length ? '<div class="tbl">' + list.map(function (p) {
        return '<div class="trow"><span class="chip" data-tone="warn">' + esc(p.score != null ? p.score : '?') + '</span>' +
          '<span class="tmain"><b>' + esc(p.name) + '</b><small>PID ' + esc(p.pid) + ' · ' + esc(p.reason || '') + '</small></span></div>';
      }).join('') + '</div>' : '<p>Aucun processus n\'affiche de comportement de chiffrement massif.</p>');
  });
});

$('btnShStop').addEventListener('click', function () {
  call('shield_stop', {}).then(function (res) {
    if (!res.ok) { handleFail(res, 'shResult', 'Bouclier'); return; }
    $('shLamp').dataset.on = 'false';
    showResult('shResult', 'warn', 'Leurres retirés', '<p>Le bouclier n\'est plus déployé.</p>');
    loadStatus(true);
  });
});

/* ── Réputation cloud / anti-hameçonnage ────────────────────────────────── */
$('btnRep').addEventListener('click', function () {
  var path = $('repPath').value.trim();
  if (!path) { toast('Réputation', 'Indiquez un chemin de fichier.', 'warn'); return; }
  call('reputation_check', { path: path }).then(function (res) {
    if (!res.ok) { handleFail(res, 'repResult', 'Réputation'); return; }
    var d = res.data || {};
    var pos = d.positives || 0, tot = d.total || 0;
    showResult('repResult', pos > 3 ? 'danger' : (pos > 0 ? 'warn' : 'ok'),
      pos > 0 ? num(pos) + ' moteur(s) sur ' + num(tot) + ' signalent ce fichier' : 'Aucun moteur ne signale ce fichier',
      kv([['Détections', num(pos) + ' / ' + num(tot)],
          ['Première vue', esc(d.first_seen ? shortDate(d.first_seen) : '—')],
          ['Empreinte', '<span class="mono">' + esc((d.sha256 || '—')).slice(0, 20) + '…</span>']]));
  });
});

$('btnPh').addEventListener('click', function () {
  var url = $('phUrl').value.trim();
  if (!url) { toast('Vérification de lien', 'Indiquez une adresse.', 'warn'); return; }
  call('phishing_check', { url: url }).then(function (res) {
    if (!res.ok) { handleFail(res, 'phResult', 'Vérification de lien'); return; }
    var d = res.data || {};
    var risk = (d.risk || d.verdict || 'inconnu').toString();
    var bad = /haut|élev|dangereux|phish|malic/i.test(risk);
    var mid = /moyen|suspect/i.test(risk);
    showResult('phResult', bad ? 'danger' : (mid ? 'warn' : 'ok'),
      bad ? 'Lien dangereux' : (mid ? 'Lien suspect' : 'Aucun signal négatif'),
      kv([['Niveau de risque', esc(risk)], ['Domaine', '<span class="mono">' + esc(d.domain || url) + '</span>']]) +
      (d.reasons && d.reasons.length ? '<ul class="tbl">' + d.reasons.map(function (r) {
        return '<div class="trow"><span class="tmain"><b>' + esc(r) + '</b></span></div>';
      }).join('') + '</ul>' : ''));
  });
});


/* ══════════════════════════════════════════════════════════════════════════
   OUTILS PARTAGÉS PAR LES TABLEAUX D'ACTION
   ══════════════════════════════════════════════════════════════════════════ */

/* Le backend raisonne en mégaoctets ; l'affichage, lui, reste lisible. */
function mb(v) { return bytes((Number(v) || 0) * 1024 * 1024); }

function base(p) { return String(p || '').split(/[\\/]/).filter(Boolean).pop() || String(p || ''); }

function setEmpty(id, msg) {
  var el = $(id);
  if (el) el.innerHTML = '<p class="empty">' + esc(msg) + '</p>';
}

function showJobline(id, on) { var el = $(id); if (el) el.hidden = !on; }

function setJobline(prefix, d) {
  var p = Math.max(0, Math.min(1, Number(d && d.progress) || 0));
  var bar = $(prefix + 'Bar'), pct = $(prefix + 'Pct'), cur = $(prefix + 'Current');
  if (bar) bar.style.width = (p * 100).toFixed(1) + '%';
  if (pct) pct.textContent = Math.round(p * 100) + ' %';
  if (cur && d && d.current) pathText(cur, d.current);
}

/* Ligne cochable. Le DOM ne porte qu'un index : les objets métier restent
   dans un tableau JS, jamais sérialisés dans des attributs. */
function pickRow(i, opts) {
  return '<label class="trow trow-pick">' +
    '<span class="pick"><input type="checkbox" data-i="' + i + '"' + (opts.checked ? ' checked' : '') + '><span></span></span>' +
    (opts.chip ? '<span class="chip" data-tone="' + (opts.tone || 'info') + '">' + esc(opts.chip) + '</span>' : '') +
    '<span class="tmain"><b>' + esc(opts.title) + '</b><small title="' + esc(opts.sub || '') + '">' + esc(opts.sub || '') + '</small></span>' +
    (opts.right ? '<span class="tsize">' + esc(opts.right) + '</span>' : '') +
    '</label>';
}

function picked(tableId, store) {
  return $$('input[data-i]:checked', $(tableId)).map(function (c) {
    return store[Number(c.dataset.i)];
  }).filter(function (x) { return !!x; });
}

/* Un seul écouteur par tableau : les lignes sont réécrites en permanence. */
function onPick(tableId, fn) {
  var t = $(tableId);
  if (t) t.addEventListener('change', function (e) {
    if (e.target && e.target.matches && e.target.matches('input[data-i]')) fn();
  });
}

function scroller(html) { return '<div class="tbl-scroll">' + html + '</div>'; }

function subTable(title, rows) {
  if (!rows.length) return '';
  return '<div class="subtbl"><p class="subtbl-h">' + esc(title) + '</p>' +
         '<div class="tbl">' + scroller(rows.join('')) + '</div></div>';
}

/* Groupe de boutons segmentés (data-<attr>) → retourne un lecteur de valeur. */
function segGroup(attr, onChange) {
  var sel = { v: null };
  $$('[data-' + attr + ']').forEach(function (b, i) {
    if (i === 0) sel.v = b.dataset[attr];
    if (b.classList.contains('is-on')) sel.v = b.dataset[attr];
    b.addEventListener('click', function () {
      var group = b.parentElement;
      $$('[data-' + attr + ']', group).forEach(function (o) {
        var on = (o === b);
        o.classList.toggle('is-on', on);
        o.setAttribute('aria-selected', on ? 'true' : 'false');
      });
      sel.v = b.dataset[attr];
      if (onChange) onChange(sel.v);
    });
  });
  return sel;
}

/* ══════════════════════════════════════════════════════════════════════════
   NETTOYAGE — temporaires, disque, applications, résidus
   ══════════════════════════════════════════════════════════════════════════ */

/* ── Nettoyage complet (destructif) ─────────────────────────────────────── */
$('btnClean').addEventListener('click', function () {
  var admin = $('cleanAdmin').checked;
  destructive({
    action: 'clean_full',
    body: { include_admin: admin },
    title: 'Nettoyage complet des fichiers temporaires',
    lead: 'Le CONTENU des emplacements listés ci-dessous sera supprimé. Vos documents, images et téléchargements ne sont jamais concernés.',
    label: 'Nettoyage complet',
    reversible: false,
    unit: 'emplacement',
    countLabel: 'Emplacements visés',
    planTitle: 'Emplacements nettoyés',
    execLabel: 'Nettoyer maintenant',
    revNote: 'Les fichiers temporaires supprimés ne sont pas récupérables. Ils sont régénérés automatiquement par Windows et par vos applications.',
    resultId: 'cleanResult',
    onDone: function (data) {
      var rep = data.result || data;
      var rows = rep.results || [];
      showResult('cleanResult', 'ok', 'Nettoyage terminé',
        kv([['Espace libéré', mb(rep.total_freed_mb)],
            ['Emplacements traités', num(rows.length)],
            ['Droits du processus', rep.is_admin ? 'administrateur' : 'standard']]) +
        (rows.length ? '<div class="tbl">' + scroller(rows.map(function (r) {
          var freed = Number(r.freed_mb) || 0;
          return '<div class="trow">' +
            '<span class="chip" data-tone="' + (r.status === 'ok' ? 'ok' : 'warn') + '">' + esc(r.status || '—') + '</span>' +
            '<span class="tmain"><b>' + esc(r.label || '—') + '</b><small title="' + esc(r.path || '') + '">' + esc(r.path || '') + '</small></span>' +
            '<span class="tsize">' + esc(freed ? mb(freed) : '—') + '</span></div>';
        }).join('')) + '</div>' : ''));
      loadStatus(true);
    }
  });
});

/* ── Analyse du disque (job) ────────────────────────────────────────────── */
$('btnDisk').addEventListener('click', function () {
  var path = $('diskPath').value.trim() || $('diskPath').placeholder;
  $('diskResult').hidden = true;
  showJobline('diskJob', true);
  setJobline('disk', { progress: 0, current: 'Parcours de ' + path });
  logLine('Analyse du disque lancée sur ' + path);

  runJob('disk_analyze', { path: path }, {
    onProgress: function (d) { setJobline('disk', d); },
    onDone: function (r) {
      showJobline('diskJob', false);
      var files = r.largest_files || [], folders = r.largest_folders || [], dups = r.duplicates || [];
      var wasted = dups.reduce(function (s, d) { return s + (Number(d.wasted_mb) || 0); }, 0);
      var row = function (x) {
        return '<div class="trow"><span class="tmain"><b>' + esc(base(x.path)) + '</b>' +
          '<small title="' + esc(x.path) + '">' + esc(x.path) + '</small></span>' +
          '<span class="tsize">' + esc(mb(x.size_mb)) + '</span></div>';
      };
      showResult('diskResult', wasted > 0 ? 'warn' : 'info', 'Analyse terminée — ' + esc(path),
        kv([['Gros fichiers', num(files.length)],
            ['Dossiers pesants', num(folders.length)],
            ['Groupes de doublons', num(dups.length)],
            ['Espace gaspillé', mb(wasted)]]) +
        subTable('Fichiers les plus volumineux', files.slice(0, 10).map(row)) +
        subTable('Dossiers les plus volumineux', folders.slice(0, 8).map(row)) +
        subTable('Doublons', dups.slice(0, 8).map(function (d) {
          return '<div class="trow"><span class="chip" data-tone="warn">' + num(d.count) + ' copies</span>' +
            '<span class="tmain"><b>' + esc(base((d.paths || [])[0] || '')) + '</b>' +
            '<small title="' + esc((d.paths || []).join(' · ')) + '">' + esc((d.paths || []).slice(1).join(' · ')) + '</small></span>' +
            '<span class="tsize">' + esc(mb(d.wasted_mb)) + '</span></div>';
        })));
      toast('Analyse du disque', num(files.length) + ' gros fichier(s), ' + mb(wasted) + ' gaspillé(s) en doublons.', 'ok');
    },
    onFail: function (res) {
      showJobline('diskJob', false);
      handleFail(res, 'diskResult', 'Analyse du disque');
    }
  });
});

/* ── Applications installées (job) ──────────────────────────────────────── */
var APPS = [];

function renderApps() {
  var t = $('appsTable');
  if (!APPS.length) { setEmpty('appsTable', 'Aucune application listée.'); return; }
  t.innerHTML = scroller(APPS.slice(0, 300).map(function (a, i) {
    return '<div class="trow" data-app="' + i + '">' +
      '<span class="chip" data-tone="' + (a.known_bloatware ? 'warn' : 'info') + '">' + esc(a.type || 'app') + '</span>' +
      '<span class="tmain"><b>' + esc(a.name || '(sans nom)') + '</b><small>' +
        esc([a.publisher, a.version, a.known_bloatware ? 'bloatware présumé' : ''].filter(Boolean).join(' · ')) +
      '</small></span>' +
      '<span class="tsize">' + esc(a.size_mb ? mb(a.size_mb) : '—') + '</span>' +
      '<span class="tact"><button class="btn btn-danger btn-sm" type="button" data-uninstall="' + i + '">Désinstaller</button></span>' +
      '</div>';
  }).join(''));
}

$('btnApps').addEventListener('click', function () {
  showJobline('appsJob', true);
  setJobline('apps', { progress: 0, current: 'Inventaire des applications…' });
  runJob('apps_list', { sort_by: $('appsSort').value }, {
    onProgress: function (d) { setJobline('apps', d); },
    onDone: function (r) {
      showJobline('appsJob', false);
      APPS = r.apps || [];
      renderApps();
      toast('Applications', num(r.total || APPS.length) + ' application(s), dont ' +
        num(r.known_bloatware_count || 0) + ' bloatware présumé(s).', 'ok');
    },
    onFail: function (res) {
      showJobline('appsJob', false);
      handleFail(res, null, 'Applications');
      setEmpty('appsTable', res.reason || res.error || 'Inventaire indisponible.');
    }
  });
});

$('appsTable').addEventListener('click', function (e) {
  var b = e.target.closest('[data-uninstall]');
  if (!b) return;
  var app = APPS[Number(b.dataset.uninstall)];
  if (!app) return;
  destructive({
    action: 'apps_uninstall',
    body: { app: app },
    title: 'Désinstaller ' + (app.name || 'cette application'),
    lead: 'Le désinstalleur natif de l\'application sera lancé. Une fenêtre d\'assistant peut s\'ouvrir.',
    label: 'Désinstallation',
    reversible: false,
    unit: 'application',
    countLabel: 'Application visée',
    planTitle: 'Application concernée',
    execLabel: 'Désinstaller',
    revNote: 'L\'application sera retirée du système. Il faudra la réinstaller depuis sa source d\'origine pour la retrouver.',
    onDone: function (data) {
      var r = (data.result || data) || {};
      toast('Désinstallation', r.message || 'Commande de désinstallation transmise.', r.status === 'erreur' ? 'danger' : 'ok');
      $('btnApps').click();
    }
  });
});

$('btnDebloat').addEventListener('click', function () {
  destructive({
    action: 'apps_debloat',
    body: {},
    title: 'Retirer les applications préinstallées superflues',
    lead: 'Les paquets listés ci-dessous sont reconnus comme bloatware. Aucun composant système n\'est proposé.',
    label: 'Bloatware',
    reversible: false,
    unit: 'paquet',
    countLabel: 'Paquets visés',
    planTitle: 'Paquets retirés',
    execLabel: 'Retirer ces paquets',
    revNote: 'Ces paquets sont réinstallables gratuitement depuis le Microsoft Store si vous changez d\'avis.',
    onDone: function (data) {
      var r = (data.result || data) || {};
      var list = r.removed || r.results || [];
      showResult('cleanResult', 'ok', 'Bloatware retiré',
        kv([['Paquets retirés', num(Array.isArray(list) ? list.length : 0)]]));
      $('btnApps').click();
    }
  });
});

/* ── Résidus d'applications désinstallées ───────────────────────────────── */
var RESIDUE = [];
var residueKind = segGroup('residue', function () {
  RESIDUE = [];
  setEmpty('residueTable', 'Lancez une recherche pour afficher les résidus.');
  $('btnResidueClean').disabled = true;
});

var RESIDUE_LABELS = {
  shortcuts: { action: 'residue_shortcuts', title: 'Raccourcis orphelins', unit: 'raccourci' },
  registry: { action: 'residue_registry', title: 'Entrées de registre orphelines', unit: 'entrée' },
  folders: { action: 'residue_folders', title: 'Dossiers orphelins', unit: 'dossier' }
};

function renderResidue() {
  var kind = residueKind.v;
  if (!RESIDUE.length) {
    setEmpty('residueTable', 'Aucun résidu trouvé pour ce critère.');
    $('btnResidueClean').disabled = true;
    return;
  }
  $('residueTable').innerHTML = scroller(RESIDUE.map(function (it, i) {
    if (kind === 'registry') {
      return pickRow(i, {
        chip: it.hive_name || 'registre', tone: 'warn',
        title: it.display_name || it.subkey || '(entrée)',
        sub: (it.parent_path || '') + ' \\ ' + (it.subkey || '')
      });
    }
    return pickRow(i, {
      chip: kind === 'folders' ? 'dossier' : 'raccourci', tone: 'warn',
      title: base(it.path), sub: it.reason || it.path,
      right: it.size_mb != null ? mb(it.size_mb) : ''
    });
  }).join(''));
  $('btnResidueClean').disabled = true;
}

onPick('residueTable', function () {
  $('btnResidueClean').disabled = picked('residueTable', RESIDUE).length === 0;
});

$('btnResidueScan').addEventListener('click', function () {
  var meta = RESIDUE_LABELS[residueKind.v];
  call(meta.action, {}).then(function (res) {
    if (!res.ok) {
      handleFail(res, null, meta.title);
      RESIDUE = [];
      setEmpty('residueTable', res.reason || res.error || 'Recherche impossible.');
      $('btnResidueClean').disabled = true;
      return;
    }
    RESIDUE = (res.data && (res.data.items || res.data)) || [];
    renderResidue();
    toast(meta.title, num(RESIDUE.length) + ' élément(s) trouvé(s).', RESIDUE.length ? 'warn' : 'ok');
  });
});

$('btnResidueClean').addEventListener('click', function () {
  var kind = residueKind.v;
  var meta = RESIDUE_LABELS[kind];
  var items = picked('residueTable', RESIDUE);
  if (!items.length) { toast(meta.title, 'Cochez au moins un élément.', 'warn'); return; }
  destructive({
    action: 'residue_clean',
    body: { kind: kind, items: items },
    title: 'Nettoyer : ' + meta.title.toLowerCase(),
    lead: kind === 'registry'
      ? 'Chaque clé est sauvegardée dans un fichier .reg avant suppression : elle reste restaurable.'
      : 'Les éléments sélectionnés sont déplacés dans le sas, pas supprimés : ils restent récupérables depuis la section Rangement.',
    label: meta.title,
    reversible: true,
    unit: meta.unit,
    countLabel: 'Éléments sélectionnés',
    planTitle: 'Éléments traités',
    execLabel: 'Nettoyer la sélection',
    onDone: function () {
      $('btnResidueScan').click();
      loadStatus(true);
    }
  });
});

/* ══════════════════════════════════════════════════════════════════════════
   RANGEMENT — tri, sas, réorganisation, annulation
   ══════════════════════════════════════════════════════════════════════════ */

/* ── Tri d'un dossier (job) puis mise de côté (réversible) ──────────────── */
var TRIAGE = [];

function renderTriage(r) {
  var safe = r.safe || [], caution = r.caution || [];
  TRIAGE = safe.concat(caution);
  if (!TRIAGE.length) {
    setEmpty('triageTable', 'Aucun fichier jetable détecté : ce dossier est déjà propre.');
    $('triageActions').hidden = true;
    return;
  }
  var head = '<div class="trow is-head"><span class="tmain">' +
    num(safe.length) + ' sûr(s) · ' + num(caution.length) + ' à vérifier · ' +
    num(r.never_touch_count || 0) + ' protégé(s) · ' + num(r.neutral_count || 0) + ' laissé(s) tel(s) quel(s)' +
    '</span></div>';
  $('triageTable').innerHTML = head + scroller(TRIAGE.map(function (f, i) {
    var sure = f.category === 'safe';
    return pickRow(i, {
      checked: sure, chip: sure ? 'sûr' : 'à vérifier', tone: sure ? 'ok' : 'warn',
      title: base(f.path), sub: f.reason || f.path,
      right: f.size_mb != null ? mb(f.size_mb) : ''
    });
  }).join(''));
  $('triageActions').hidden = false;
  updateTriageBtn();
}

function updateTriageBtn() {
  var n = picked('triageTable', TRIAGE).length;
  var b = $('btnTriageApply');
  b.disabled = (n === 0);
  b.textContent = n ? 'Mettre de côté ' + num(n) + ' fichier(s)' : 'Mettre de côté la sélection';
}
onPick('triageTable', updateTriageBtn);

$('btnTriage').addEventListener('click', function () {
  var path = $('triagePath').value.trim() || $('triagePath').placeholder;
  showJobline('triageJob', true);
  setJobline('triage', { progress: 0, current: 'Examen de ' + path });
  $('triageActions').hidden = true;
  runJob('triage_scan', { path: path }, {
    onProgress: function (d) { setJobline('triage', d); },
    onDone: function (r) {
      showJobline('triageJob', false);
      renderTriage(r || {});
      toast('Tri', num((r.safe || []).length) + ' fichier(s) jetable(s) et ' +
        num((r.caution || []).length) + ' à vérifier.', 'ok');
    },
    onFail: function (res) {
      showJobline('triageJob', false);
      handleFail(res, null, 'Tri');
      setEmpty('triageTable', res.reason || res.error || 'Examen impossible.');
    }
  });
});

$('btnTriageApply').addEventListener('click', function () {
  var sel = picked('triageTable', TRIAGE);
  if (!sel.length) { toast('Tri', 'Cochez au moins un fichier.', 'warn'); return; }
  destructive({
    action: 'triage_apply',
    body: { files: sel.map(function (f) { return { path: f.path, reason: f.reason || 'Mise de côté depuis l\'interface' }; }) },
    title: 'Mettre de côté ' + num(sel.length) + ' fichier(s)',
    lead: 'Les fichiers sont DÉPLACÉS dans le sas de sécurité, jamais supprimés. Vous pouvez les restaurer à tout moment.',
    label: 'Mise de côté',
    reversible: true,
    unit: 'fichier',
    execLabel: 'Mettre de côté',
    onDone: function (data) {
      var r = (data.result || data) || {};
      toast('Mise de côté', num(r.staged_count || 0) + ' fichier(s) déplacé(s) dans le sas.', 'ok');
      $('btnTriage').click();
      loadStagingList();
      loadStatus(true);
    }
  });
});

/* ── Sas : liste, restauration, purge (destructive) ─────────────────────── */
function loadStagingList() {
  call('staging_list', {}).then(function (res) {
    if (!res.ok) {
      handleFail(res, null, 'Sas');
      setEmpty('stagingTable', res.reason || res.error || 'Sas illisible.');
      return;
    }
    var items = (res.data && (res.data.items || res.data)) || [];
    if (!items.length) { setEmpty('stagingTable', 'Le sas est vide : rien n\'est en attente.'); return; }
    $('stagingTable').innerHTML = scroller(items.map(function (it) {
      return '<div class="trow">' +
        '<span class="chip" data-tone="info">' + esc(shortDate(it.date)) + '</span>' +
        '<span class="tmain"><b>' + esc(base(it.original_path)) + '</b>' +
        '<small title="' + esc(it.original_path || '') + '">' + esc(it.reason || it.original_path || '') + '</small></span>' +
        '<span class="tact"><button class="btn btn-ghost btn-sm" type="button" data-srestore="' + esc(it.id) + '">Restaurer</button></span>' +
        '</div>';
    }).join(''));
  });
}

$('btnStagingList').addEventListener('click', loadStagingList);

$('stagingTable').addEventListener('click', function (e) {
  var b = e.target.closest('[data-srestore]');
  if (!b) return;
  call('staging_restore', { id: b.dataset.srestore }).then(function (res) {
    if (!res.ok) { handleFail(res, null, 'Restauration'); return; }
    toast('Sas', 'Fichier remis à son emplacement d\'origine.', 'ok');
    loadStagingList();
    loadStatus(true);
  });
});

$('btnStagingPurge').addEventListener('click', function () {
  var days = Math.max(1, parseInt($('purgeDays').value, 10) || 30);
  destructive({
    action: 'staging_purge',
    body: { older_than_days: days },
    title: 'Purger le sas de sécurité',
    lead: 'Les éléments mis de côté depuis plus de ' + num(days) + ' jours seront supprimés DÉFINITIVEMENT du disque.',
    label: 'Purge du sas',
    reversible: false,
    unit: 'fichier',
    execLabel: 'Supprimer définitivement',
    onDone: function () { loadStagingList(); loadStatus(true); }
  });
});

/* ── Réorganisation : plan puis application (réversible) ────────────────── */
var ORG_MODES = { category: 'Par catégorie', application: 'Par application', importance: 'Par importance' };
var ORG_PLAN = [];
var orgMode = segGroup('orgmode', function () {
  ORG_PLAN = [];
  $('btnOrgApply').disabled = true;
});

$('btnOrgPlan').addEventListener('click', function () {
  var path = $('orgPath').value.trim() || $('orgPath').placeholder;
  call('organize_plan', { path: path, mode: orgMode.v }).then(function (res) {
    if (!res.ok) { handleFail(res, 'orgResult', 'Réorganisation'); $('btnOrgApply').disabled = true; return; }
    var d = res.data || {};
    ORG_PLAN = d.plan || [];
    $('btnOrgApply').disabled = (ORG_PLAN.length === 0);
    if (!ORG_PLAN.length) {
      showResult('orgResult', 'ok', 'Rien à réorganiser',
        '<p>Aucun fichier de ce dossier ne gagnerait à être déplacé selon ce critère.</p>');
      return;
    }
    var groups = {};
    ORG_PLAN.forEach(function (p) { groups[p.label] = (groups[p.label] || 0) + 1; });
    var keys = Object.keys(groups).sort(function (a, b) { return groups[b] - groups[a]; });
    showResult('orgResult', 'info', num(ORG_PLAN.length) + ' fichier(s) à ranger',
      kv([['Dossier', pathCell('mono', d.path || path)],
          ['Critère', esc(ORG_MODES[orgMode.v] || orgMode.v)],
          ['Dossiers créés', num(keys.length)]]) +
      subTable('Répartition proposée', keys.slice(0, 20).map(function (k) {
        return '<div class="trow"><span class="tmain"><b>' + esc(k) + '</b></span>' +
          '<span class="tsize">' + num(groups[k]) + ' fich.</span></div>';
      })) +
      '<p class="p-note">Rien n\'a été déplacé. Utilisez « Appliquer » pour valider le plan.</p>');
    toast('Réorganisation', num(ORG_PLAN.length) + ' déplacement(s) proposé(s).', 'ok');
  });
});

$('btnOrgApply').addEventListener('click', function () {
  if (!ORG_PLAN.length) { toast('Réorganisation', 'Prévisualisez d\'abord un plan.', 'warn'); return; }
  destructive({
    action: 'organize_apply',
    body: { plan: ORG_PLAN },
    title: 'Appliquer la réorganisation',
    lead: 'Chaque déplacement est journalisé : la session entière pourra être annulée d\'un clic.',
    label: 'Réorganisation',
    reversible: true,
    unit: 'fichier',
    execLabel: 'Appliquer le plan',
    resultId: 'orgResult',
    onDone: function (data) {
      var r = (data.result || data) || {};
      showResult('orgResult', 'ok', 'Réorganisation appliquée',
        kv([['Fichiers déplacés', num(r.moved != null ? r.moved : ORG_PLAN.length)],
            ['Erreurs', num((r.errors || []).length)],
            ['Session', '<span class="mono">' + esc(String(r.session_id || '—').slice(0, 8)) + '</span>']]) +
        '<p>Utilisez « Annuler une réorganisation » pour revenir en arrière.</p>');
      ORG_PLAN = [];
      $('btnOrgApply').disabled = true;
      loadSessions();
    }
  });
});

/* ── Déplacer un dossier (destructif) ───────────────────────────────────── */
$('btnMove').addEventListener('click', function () {
  var src = $('movSrc').value.trim(), dst = $('movDst').value.trim();
  if (!src || !dst) { toast('Déplacement', 'Indiquez le dossier source et la destination.', 'warn'); return; }
  destructive({
    action: 'organize_move_folder',
    body: { source: src, target: dst },
    title: 'Déplacer un dossier entier',
    lead: 'Le dossier source sera déplacé À L\'INTÉRIEUR du dossier de destination, avec tout son contenu.',
    label: 'Déplacement',
    reversible: false,
    unit: 'dossier',
    countLabel: 'Dossier déplacé',
    planTitle: 'Déplacement prévu',
    execLabel: 'Déplacer',
    revNote: 'Le dossier change d\'emplacement. Aucune donnée n\'est supprimée, mais l\'opération n\'est pas annulable depuis l\'interface.',
    resultId: 'movResult',
    onDone: function (data) {
      var r = (data.result || data) || {};
      showResult('movResult', r.status === 'erreur' ? 'danger' : 'ok',
        r.status === 'erreur' ? 'Déplacement refusé' : 'Dossier déplacé',
        kv([['Source', '<span class="mono">' + esc(src) + '</span>'],
            ['Destination', '<span class="mono">' + esc(r.destination || dst) + '</span>']]) +
        (r.message ? '<p>' + esc(r.message) + '</p>' : ''));
    }
  });
});

/* ── Fichiers peu utilisés (réversible) ─────────────────────────────────── */
$('btnLeastUsed').addEventListener('click', function () {
  var path = $('luPath').value.trim() || $('luPath').placeholder;
  var days = Math.max(1, parseInt($('luDays').value, 10) || 180);
  destructive({
    action: 'organize_least_used',
    body: { path: path, days: days },
    title: 'Ranger les fichiers peu utilisés',
    lead: 'Les fichiers non utilisés depuis plus de ' + num(days) + ' jours seront regroupés dans un sous-dossier dédié, sans quitter ce dossier.',
    label: 'Fichiers peu utilisés',
    reversible: true,
    unit: 'fichier',
    execLabel: 'Ranger ces fichiers',
    resultId: 'luResult',
    onDone: function (data) {
      var r = (data.result || data) || {};
      showResult('luResult', 'ok', 'Rangement effectué',
        kv([['Fichiers rangés', num(r.moved != null ? r.moved : (r.count || 0))],
            ['Destination', '<span class="mono">' + esc(r.destination || r.folder || '—') + '</span>']]) +
        '<p>Annulable depuis « Annuler une réorganisation ».</p>');
      loadSessions();
    }
  });
});

/* ── Sessions de réorganisation et annulation ───────────────────────────── */
function loadSessions() {
  call('organize_sessions', {}).then(function (res) {
    if (!res.ok) {
      handleFail(res, null, 'Sessions');
      setEmpty('sessionsTable', res.reason || res.error || 'Journal illisible.');
      return;
    }
    var list = (res.data && (res.data.sessions || res.data.items)) || [];
    if (!list.length) { setEmpty('sessionsTable', 'Aucune réorganisation enregistrée.'); return; }
    $('sessionsTable').innerHTML = scroller(list.map(function (s) {
      var done = s.count - s.undone;
      var all = s.undone >= s.count;
      return '<div class="trow">' +
        '<span class="chip" data-tone="' + (all ? 'ok' : 'brand') + '">' + (all ? 'annulée' : num(done) + ' actifs') + '</span>' +
        '<span class="tmain"><b>' + esc(shortDate(s.date)) + '</b>' +
        '<small>' + num(s.count) + ' déplacement(s) · session ' + esc(String(s.session_id).slice(0, 8)) + '</small></span>' +
        '<span class="tact"><button class="btn btn-ghost btn-sm" type="button" data-undo="' + esc(s.session_id) + '"' +
          (all ? ' disabled' : '') + '>Annuler</button></span></div>';
    }).join(''));
  });
}

$('btnSessions').addEventListener('click', loadSessions);

$('sessionsTable').addEventListener('click', function (e) {
  var b = e.target.closest('[data-undo]');
  if (!b) return;
  b.disabled = true;
  call('organize_undo', { session_id: b.dataset.undo }).then(function (res) {
    if (!res.ok) { b.disabled = false; handleFail(res, null, 'Annulation'); return; }
    var r = res.data || {};
    toast('Annulation', num(r.restored || 0) + ' fichier(s) remis à leur place d\'origine.', 'ok');
    loadSessions();
  });
});

/* ══════════════════════════════════════════════════════════════════════════
   SYSTÈME — démarrage, planification, mode gardien
   ══════════════════════════════════════════════════════════════════════════ */

/* ── Programmes au démarrage ────────────────────────────────────────────── */
function loadStartup() {
  call('startup_list', {}).then(function (res) {
    if (!res.ok) {
      handleFail(res, null, 'Démarrage');
      setEmpty('startupTable', res.reason || res.error || 'Liste indisponible.');
      return;
    }
    var d = res.data || {};
    var reg = d.registry || [], fold = d.startup_folder || [];
    if (!reg.length && !fold.length) { setEmpty('startupTable', 'Aucun programme lancé au démarrage.'); return; }
    var rows = reg.map(function (it) {
      return '<div class="trow">' +
        '<span class="chip" data-tone="' + (it.recommended_disable ? 'warn' : 'info') + '">' + esc(it.hive) + '</span>' +
        '<span class="tmain"><b>' + esc(it.name) + '</b>' +
        '<small title="' + esc(it.command || '') + '">' + esc(it.command || '') + '</small></span>' +
        '<span class="tact">' +
          '<button class="btn btn-ghost btn-sm" type="button" data-sturestore="' + esc(it.hive) + '|' + esc(it.name) + '">Restaurer</button>' +
          '<button class="btn btn-danger btn-sm" type="button" data-stdisable="' + esc(it.hive) + '|' + esc(it.key_path || '') + '|' + esc(it.name) + '">Désactiver</button>' +
        '</span></div>';
    }).concat(fold.map(function (it) {
      return '<div class="trow"><span class="chip" data-tone="info">dossier</span>' +
        '<span class="tmain"><b>' + esc(it.name) + '</b>' +
        '<small title="' + esc(it.path || '') + '">' + esc(it.path || '') + '</small></span>' +
        '<span class="tsize">raccourci</span></div>';
    }));
    $('startupTable').innerHTML = scroller(rows.join(''));
  });
}

$('btnStartup').addEventListener('click', loadStartup);

$('startupTable').addEventListener('click', function (e) {
  var dis = e.target.closest('[data-stdisable]');
  var rst = e.target.closest('[data-sturestore]');
  if (dis) {
    var p = dis.dataset.stdisable.split('|');
    destructive({
      action: 'startup_disable',
      body: { hive: p[0], key_path: p[1], name: p[2] },
      title: 'Désactiver « ' + p[2] + ' » au démarrage',
      lead: 'L\'entrée est DÉPLACÉE vers une clé de sauvegarde : le programme ne se lancera plus au démarrage, et reste restaurable.',
      label: 'Démarrage',
      reversible: true,
      unit: 'entrée',
      countLabel: 'Entrée visée',
      planTitle: 'Entrée de registre',
      execLabel: 'Désactiver',
      onDone: function () { loadStartup(); }
    });
  }
  if (rst) {
    var q = rst.dataset.sturestore.split('|');
    call('startup_restore', { hive: q[0], name: q[1] }).then(function (res) {
      if (!res.ok) { handleFail(res, null, 'Démarrage'); return; }
      toast('Démarrage', 'Entrée restaurée : « ' + q[1] + ' ».', 'ok');
      loadStartup();
    });
  }
});

/* ── Tâches planifiées ──────────────────────────────────────────────────── */
function schedResult(id, res, okTitle, label) {
  if (!res.ok) { handleFail(res, id, label); return; }
  var d = res.data || {};
  var bad = d.status === 'erreur';
  showResult(id, bad ? 'danger' : 'ok', bad ? 'Planification refusée' : okTitle,
    '<p>' + esc(d.message || 'Opération effectuée.') + '</p>');
  toast(label, d.message || okTitle, bad ? 'danger' : 'ok');
}

$('btnSchAdd').addEventListener('click', function () {
  call('schedule_cleanup', { day: $('schDay').value, time: $('schTime').value })
    .then(function (res) { schedResult('schResult', res, 'Nettoyage hebdomadaire planifié', 'Planification'); });
});

$('btnSchDel').addEventListener('click', function () {
  call('schedule_remove', {})
    .then(function (res) { schedResult('schResult', res, 'Tâche supprimée', 'Planification'); });
});

$('btnGsAdd').addEventListener('click', function () {
  call('guardian_schedule', { time: $('gsTime').value })
    .then(function (res) { schedResult('gsResult', res, 'Gardien quotidien activé', 'Gardien planifié'); });
});

$('btnGsDel').addEventListener('click', function () {
  call('guardian_unschedule', {})
    .then(function (res) { schedResult('gsResult', res, 'Gardien quotidien désactivé', 'Gardien planifié'); });
});

/* ── Mode gardien : passe complète (destructive → job) ──────────────────── */
function guardianReport(r) {
  var scans = r.scans || [], reorgs = r.reorganizations || [];
  var temp = r.temp_cleanup || {};
  var staged = r.staged_for_deletion || {};
  var stagedCount = (staged.folders || []).reduce(function (s, f) { return s + (Number(f.staged) || 0); }, 0);
  var threats = scans.reduce(function (s, f) { return s + (Number(f.threats_quarantined) || 0); }, 0);
  var files = scans.reduce(function (s, f) { return s + (Number(f.files_scanned) || 0); }, 0);
  showResult('gdResult', threats ? 'danger' : 'ok',
    threats ? num(threats) + ' menace(s) isolée(s) durant la passe' : 'Passe complète terminée',
    kv([['Espace libéré', mb(temp.total_freed_mb)],
        ['Fichiers analysés', num(files)],
        ['Menaces isolées', num(threats)],
        ['Mis de côté', num(stagedCount)],
        ['Dossiers traités', num((r.folders || []).length)]]) +
    subTable('Dossiers analysés', scans.map(function (s) {
      return '<div class="trow"><span class="chip" data-tone="' + (s.threats_quarantined ? 'danger' : 'ok') + '">' +
        num(s.threats_quarantined || 0) + ' menace(s)</span>' +
        '<span class="tmain"><b>' + esc(base(s.folder)) + '</b><small title="' + esc(s.folder) + '">' + esc(s.folder) + '</small></span>' +
        '<span class="tsize">' + num(s.files_scanned || 0) + ' fich.</span></div>';
    })) +
    subTable('Rangements', reorgs.map(function (o) {
      return '<div class="trow"><span class="chip" data-tone="' + (o.preview_only ? 'warn' : 'ok') + '">' +
        (o.preview_only ? 'proposé' : 'appliqué') + '</span>' +
        '<span class="tmain"><b>' + esc(base(o.folder)) + '</b><small>' +
        esc(o.preview_only ? num(o.candidates || 0) + ' candidat(s)' : num(o.moved || 0) + ' déplacement(s)') + '</small></span></div>';
    })));
}

$('btnGdRun').addEventListener('click', function () {
  var folders = splitPaths($('gdFolders').value || $('gdFolders').placeholder);
  destructive({
    action: 'guardian_run',
    body: { folders: folders },
    title: 'Lancer une passe complète du Mode gardien',
    lead: 'Nettoyage, mise de côté, analyse antivirus puis rangement, sur les dossiers indiqués. Aucune suppression définitive : tout passe par la quarantaine ou le sas.',
    label: 'Mode gardien',
    reversible: true,
    unit: 'dossier',
    countLabel: 'Dossiers traités',
    planTitle: 'Dossiers de la passe',
    execLabel: 'Lancer la passe',
    resultId: 'gdResult',
    onDone: function (data) {
      var r = (data.result || data) || {};
      if (!r.job_id) { guardianReport(r); loadStatus(true); return; }
      showJobline('gdJob', true);
      setJobline('gd', { progress: 0, current: 'Passe complète en cours…' });
      trackJob('guardian_run', r.job_id, {
        onProgress: function (d) { setJobline('gd', d); },
        onDone: function (rep) {
          showJobline('gdJob', false);
          guardianReport(rep || {});
          toast('Mode gardien', 'Passe complète terminée.', 'ok');
          loadGuardianPending();
          loadStatus(true);
        },
        onFail: function (res) {
          showJobline('gdJob', false);
          handleFail(res, 'gdResult', 'Mode gardien');
        }
      });
    }
  });
});

function loadGuardianPending() {
  call('guardian_pending', {}).then(function (res) {
    if (!res.ok) {
      handleFail(res, null, 'File d\'attente');
      setEmpty('gdTable', res.reason || res.error || 'File illisible.');
      $('gdConfirmRow').hidden = true;
      return;
    }
    var items = (res.data && (res.data.items || res.data)) || [];
    if (!items.length) {
      setEmpty('gdTable', 'Aucun élément en attente de suppression définitive.');
      $('gdConfirmRow').hidden = true;
      return;
    }
    $('gdTable').innerHTML = scroller(items.map(function (it) {
      return '<div class="trow">' +
        '<span class="chip" data-tone="warn">' + esc(shortDate(it.date)) + '</span>' +
        '<span class="tmain"><b>' + esc(base(it.original_path)) + '</b>' +
        '<small title="' + esc(it.original_path || '') + '">' + esc(it.reason || it.original_path || '') + '</small></span>' +
        '</div>';
    }).join(''));
    $('gdConfirmRow').hidden = false;
    toast('Mode gardien', num(items.length) + ' élément(s) attendent votre décision.', 'warn');
  });
}

$('btnGdPending').addEventListener('click', loadGuardianPending);

$('btnGdConfirm').addEventListener('click', function () {
  var days = Math.max(1, parseInt($('gdDays').value, 10) || 7);
  destructive({
    action: 'guardian_confirm',
    body: { older_than_days: days },
    title: 'Suppression définitive des éléments en attente',
    lead: 'Seuls les éléments mis de côté depuis plus de ' + num(days) + ' jours sont concernés. Cette suppression est irréversible.',
    label: 'Suppression définitive',
    reversible: false,
    unit: 'élément',
    execLabel: 'Supprimer définitivement',
    resultId: 'gdResult',
    onDone: function (data) {
      var r = (data.result || data) || {};
      showResult('gdResult', 'ok', 'File d\'attente vidée',
        kv([['Éléments supprimés', num(r.purged || 0)], ['Seuil', num(days) + ' jours']]));
      loadGuardianPending();
      loadStatus(true);
    }
  });
});

/* ══════════════════════════════════════════════════════════════════════════
   SÉCURITÉ AVANCÉE (V2) — mode incident, caméra, connexions, intrusion

   Cinq modules livrés, une seule règle de présentation : ces modules rendent
   souvent un résultat PARTIEL (droits insuffisants, Windows uniquement). Ce
   qui n'a pas pu être interrogé est donc toujours affiché — jamais masqué.
   Un rapport incomplet présenté comme complet est pire qu'un rapport absent.
   ══════════════════════════════════════════════════════════════════════════ */

var SOURCE_LABELS = {
  sessions: 'Sessions ouvertes', logiciels: 'Accès à distance',
  connexions: 'Connexions entrantes', journal: 'Journal de sécurité',
  comptes: 'Comptes locaux', webcam: 'Caméra', microphone: 'Microphone',
  quarantaine: 'Quarantaine', sas: 'Sas de tri', rangement: 'Rangement',
  demarrage: 'Démarrage Windows', journal_v2: 'Journal V2', filtre: 'Filtre'
};

function srcLabel(k) { return SOURCE_LABELS[k] || String(k); }

/* Rend la carte des sources d'un rapport. `sources` = { nom: "ok" | motif }. */
function sourcesBlock(sources, titre) {
  var keys = Object.keys(sources || {});
  if (!keys.length) return '';
  var manquantes = keys.filter(function (k) { return String(sources[k]) !== 'ok'; });
  var rows = keys.map(function (k) {
    var v = String(sources[k] == null ? '' : sources[k]);
    var vivante = (v === 'ok');
    return '<div class="src' + (vivante ? '' : ' is-off') + '">' +
      icon(vivante ? 'check' : 'alert') +
      '<span class="src-n">' + esc(srcLabel(k)) + '</span>' +
      '<span class="src-v">' + esc(vivante ? 'a répondu' : v) + '</span></div>';
  }).join('');
  return '<div class="srcs"><p class="srcs-h' + (manquantes.length ? ' is-partial' : '') + '">' +
    esc(titre || 'Sources interrogées') + ' — ' +
    (manquantes.length
      ? num(manquantes.length) + ' sur ' + num(keys.length) + ' n\'ont pas pu répondre : ce rapport est PARTIEL'
      : 'toutes ont répondu') +
    '</p>' + rows + '</div>';
}

/* Variante pour l'historique, dont les sources en panne arrivent en liste. */
function problemsBlock(problemes) {
  problemes = problemes || [];
  if (!problemes.length) return '';
  var rows = problemes.map(function (p) {
    return '<div class="src is-off">' + icon('alert') +
      '<span class="src-n">' + esc(p.libelle || srcLabel(p.source)) + '</span>' +
      '<span class="src-v">' + esc(p.message || 'source indisponible') + '</span></div>';
  }).join('');
  return '<div class="srcs"><p class="srcs-h is-partial">Sources en défaut — ' +
    num(problemes.length) + ' mécanisme(s) n\'ont pas pu être lus : cette vue est PARTIELLE</p>' +
    rows + '</div>';
}

/* Enveloppe rendue par un module V2 à l'intérieur d'une exécution destructive :
   { dry_run:false, action, result: { ok, data } }. */
function v2Result(d) {
  var env = (d && d.result) || {};
  return { ok: env.ok !== false, data: env.data || {}, error: env.error || env.reason || '' };
}


/* ══ MODE INCIDENT ═══════════════════════════════════════════════════════
   Le bouton d'urgence. Il est en barre du haut, visible depuis n'importe
   quel panneau, et ne déclenche jamais rien au clic : il ouvre la modale de
   validation, qui affiche la séquence exacte avant la moindre coupure.
   ════════════════════════════════════════════════════════════════════════ */
var INCIDENT = null;

function paintIncident(d) {
  d = d || {};
  /* Fusion et non remplacement : `status` ne transporte qu'un sous-ensemble
     de l'état (pas la commande de retrait manuel, par exemple). Sans cette
     fusion, le rafraîchissement automatique effaçait des informations que
     seul `incident_state` fournit. */
  INCIDENT = Object.assign({}, INCIDENT || {}, d);
  d = INCIDENT;
  var actif = !!d.actif, corrompu = !!d.corrompu;
  var message = d.message || (actif ? 'Mode incident actif.' : 'Mode incident inactif.');

  var btn = $('btnIncident');
  if (btn) btn.dataset.actif = actif ? 'true' : 'false';

  var box = $('incState');
  if (box) {
    box.dataset.actif = actif ? 'true' : (corrompu ? 'warn' : 'false');
    $('incStateVal').textContent = actif
      ? 'ACTIF' + (d.depuis ? ' depuis le ' + shortDate(d.depuis) : '')
      : (corrompu ? 'État illisible' : 'Inactif');
    $('incStateMsg').textContent = message;
  }

  var banner = $('incidentBanner');
  if (!banner) return;
  if (actif || corrompu) {
    banner.hidden = false;
    $('incidentBannerTitle').textContent = actif
      ? 'Mode Incident ACTIF — le réseau de cette machine est coupé'
      : 'Mode Incident : fichier d\'état illisible';
    $('incidentBannerMsg').textContent = message +
      (d.retrait_manuel ? ' Retrait manuel de la règle : ' + d.retrait_manuel : '');
  } else {
    banner.hidden = true;
  }
}

/* Lu au démarrage de l'interface : un mode incident hérité d'une session
   précédente doit être annoncé tout de suite, avec sa sortie à portée de clic. */
function loadIncidentState(annoncer) {
  return call('incident_state', {}).then(function (res) {
    if (!res.ok) {
      handleFail(res, annoncer ? null : 'incResult', 'Mode Incident');
      return null;
    }
    var d = res.data || {};
    paintIncident(d);
    if (annoncer && (d.actif || d.corrompu)) {
      toast(d.actif ? 'Mode Incident actif' : 'Mode Incident — état illisible',
        d.message || '', 'danger');
      showResult('incResult', 'danger',
        d.actif ? 'Mode Incident hérité d\'une session précédente' : 'Fichier d\'état illisible',
        '<p>' + esc(d.message || '') + '</p>' +
        kv([['Depuis', esc(d.depuis ? shortDate(d.depuis) : 'date inconnue')],
            ['Réseau', d.reseau_coupe ? 'coupé' : 'intact'],
            ['Processus gelés', num(d.nb_geles || 0)],
            ['Étape atteinte', esc(d.etape_atteinte || '—')]]) +
        '<p>Le bouton <b>Rétablir</b> retire la règle de pare-feu et relance les processus gelés. ' +
        'Si l\'application ne parvient pas à le faire, la règle se retire aussi à la main : ' +
        '<span class="mono">' + esc(d.retrait_manuel || '') + '</span></p>');
    }
    return d;
  });
}

function renderIncidentPlan(d) {
  var reseau = d.reseau || {}, gel = d.gel || {},
      sauvegarde = d.sauvegarde || {}, rapport = d.rapport || {};
  var etapes = [
    ['1 — Couper le réseau', reseau.disponible, reseau.action, 'règle « ' + (reseau.regle || '') + ' » ; la carte réseau n\'est pas désactivée'],
    ['2 — Geler les processus', gel.disponible, gel.action, (d.processus || []).length + ' processus candidat(s), liste noire stricte des processus critiques'],
    ['3 — Sauvegarde', sauvegarde.disponible, sauvegarde.action, (sauvegarde.dossiers || []).length + ' dossier(s) personnel(s)'],
    ['4 — Rapport', true, rapport.action, 'écrit dans ' + (rapport.dossier || '—')]
  ];
  var lignes = etapes.map(function (e) {
    var dispo = e[1] !== false;
    return '<div class="trow is-multi is-desc' + (dispo ? '' : ' is-warm') + '">' +
      '<span class="chip" data-tone="' + (dispo ? 'ok' : 'warn') + '">' +
      esc(dispo ? 'exécutable' : 'indisponible ici') + '</span>' +
      '<span class="tmain"><b>' + esc(e[0]) + '</b><small>' + esc(e[2] || '') + '</small></span>' +
      '<span class="treason">' + esc(e[3] || '') + '</span></div>';
  }).join('');

  var procs = (d.processus || []).map(function (p) {
    return '<div class="trow is-multi"><span class="chip" data-tone="warn">PID ' + esc(p.pid) + '</span>' +
      '<span class="tmain"><b>' + esc(p.nom || '?') + '</b><small>' + esc(p.chemin || '') + '</small></span>' +
      '<span class="treason">' + esc(p.raison || 'processus candidat au gel') + '</span></div>';
  });

  var avert = (d.avertissements || []).map(function (a) {
    return '<li>' + esc(a) + '</li>';
  }).join('');

  showResult('incResult', d.deja_actif ? 'warn' : 'info', 'Plan du Mode Incident — rien n\'a été touché',
    kv([['Plateforme', esc(d.plateforme || '—')],
        ['Droits', d.administrateur ? 'administrateur' : 'standard'],
        ['Déjà actif', d.deja_actif ? 'oui' : 'non'],
        ['Fichier d\'état', '<span class="mono">' + esc(d.etat_persistant || '—') + '</span>']]) +
    '<div class="tbl">' + lignes + '</div>' +
    (procs.length ? subTable('Processus qui seraient gelés', procs)
                  : '<p class="p-note">Aucun processus candidat au gel : les autres étapes s\'exécuteraient quand même.</p>') +
    (avert ? '<div class="warnbox"><p class="warnbox-h">Avertissements du module</p><ul>' + avert + '</ul></div>' : ''));
}

function renderIncidentDone(data) {
  var env = v2Result(data);
  var r = env.data || {};
  var etapes = r.etapes || {};
  var ordre = r.ordre || ['reseau', 'processus', 'sauvegarde', 'rapport'];
  var NOMS = { reseau: 'Réseau', processus: 'Gel des processus', sauvegarde: 'Sauvegarde VSS', rapport: 'Rapport' };

  var lignes = ordre.map(function (k) {
    var e = etapes[k] || {};
    var bon = e.ok !== false;
    var tone = bon ? 'ok' : (e.unavailable ? 'warn' : 'danger');
    var txt = e.message || e.reason || e.error || (bon ? 'étape exécutée' : 'étape en échec');
    return '<div class="trow is-multi is-desc' + (bon ? '' : ' is-warm') + '">' +
      '<span class="chip" data-tone="' + tone + '">' + esc(bon ? 'fait' : (e.unavailable ? 'indisponible' : 'échec')) + '</span>' +
      '<span class="tmain"><b>' + esc(NOMS[k] || k) + '</b></span>' +
      '<span class="treason">' + esc(txt) + '</span></div>';
  }).join('');

  var conseils = (r.conseils || []).map(function (c) { return '<li>' + esc(c) + '</li>'; }).join('');

  showResult('incResult', r.degrade ? 'warn' : 'danger',
    'Mode Incident activé — ' + num(r.nb_geles || 0) + ' processus gelé(s)',
    kv([['Horodatage', esc(r.horodatage || '—')],
        ['Durée', esc((r.duree_s != null ? r.duree_s : '—') + ' s')],
        ['Étapes en échec', esc((r.etapes_en_echec || []).join(', ') || 'aucune')],
        ['Fichier d\'état', '<span class="mono">' + esc(r.etat_persistant || '—') + '</span>']]) +
    '<div class="tbl">' + lignes + '</div>' +
    (conseils ? '<div class="warnbox"><p class="warnbox-h">À faire maintenant</p><ul>' + conseils + '</ul></div>' : ''));
  toast('Mode Incident', 'Séquence exécutée. Utilisez « Rétablir » pour tout remettre en place.', 'danger');
}

function triggerIncident() {
  return destructive({
    action: 'incident_activate',
    body: {},
    title: 'Déclencher le Mode Incident',
    lead: 'Voici la séquence exacte, dans l\'ordre où elle s\'exécutera. Rien n\'a encore été touché. ' +
          'La coupure réseau est immédiate, et la sortie du mode tient en un clic.',
    label: 'Mode Incident',
    reversible: true,
    unit: 'étape',
    countLabel: 'Étapes de la séquence',
    planTitle: 'Processus candidats au gel',
    execLabel: 'Déclencher maintenant',
    revNote: 'Réversible : « Rétablir » retire la règle de pare-feu et relance les processus gelés — ' +
             'ils sont suspendus, jamais arrêtés. Mais la coupure du réseau est immédiate : travail en ' +
             'ligne non enregistré perdu, appels coupés, téléchargements interrompus.',
    resultId: 'incResult',
    onDone: function (d) {
      renderIncidentDone(d);
      loadIncidentState(false);
      loadStatus(true);
    }
  });
}

function restoreIncident(btn) {
  if (btn) { btn.disabled = true; }
  return call('incident_restore', {}).then(function (res) {
    if (btn) { btn.disabled = false; }
    if (!res.ok) {
      /* Rétablissement PARTIEL : le module conserve l'état plutôt que de
         prétendre que tout est revenu. On affiche ce qui reste en place. */
      var d = res.data || {};
      showResult('incResult', 'danger', 'Rétablissement partiel',
        '<p>' + esc(d.message || res.error || '') + '</p>' +
        kv([['Processus encore gelés', num((d.restants || []).length)],
            ['Retrait manuel', '<span class="mono">' + esc((INCIDENT && INCIDENT.retrait_manuel) || '') + '</span>']]));
      toast('Mode Incident', d.message || res.error || 'Rétablissement incomplet.', 'danger');
      loadIncidentState(false);
      return;
    }
    var d2 = res.data || {};
    showResult('incResult', 'ok', 'Mode Incident levé',
      '<p>' + esc(d2.message || 'Réseau rétabli, processus relancés.') + '</p>' +
      kv([['Processus relancés', num((d2.relances || []).length)],
          ['Réseau', 'rétabli']]));
    toast('Mode Incident', d2.message || 'Tout est remis en place.', 'ok');
    loadIncidentState(false);
    loadStatus(true);
  });
}

$('btnIncident').addEventListener('click', function () { triggerIncident(); });
$('btnIncidentGo').addEventListener('click', function () { triggerIncident(); });
$('btnIncidentRestore').addEventListener('click', function () { restoreIncident(this); });
$('btnIncidentRestoreTop').addEventListener('click', function () { restoreIncident(this); });

$('btnIncidentPlan').addEventListener('click', function () {
  call('incident_plan', {}).then(function (res) {
    if (!res.ok) { handleFail(res, 'incResult', 'Mode Incident'); return; }
    renderIncidentPlan(res.data || {});
  });
});


/* ══ CAMÉRA ET MICROPHONE ════════════════════════════════════════════════ */
function setCamLamp(on) {
  var l = $('camLamp');
  if (l) l.dataset.on = on ? 'true' : 'false';
}

function camRow(a) {
  var alerte = a.en_cours && !a.autorisee;
  var tone = alerte ? 'danger' : (a.en_cours ? 'warn' : 'info');
  var etat = a.en_cours ? 'EN COURS' : 'terminé';
  var quand = a.debut ? shortDate(a.debut) : 'date inconnue';
  return '<div class="trow' + (alerte ? ' is-hot' : (a.en_cours ? ' is-warm' : '')) + '">' +
    '<span class="chip" data-tone="' + tone + '">' + esc(etat) + '</span>' +
    '<span class="tmain"><b>' + esc(a.application || '?') + '</b><small title="' +
      esc((a.appareil_lisible || a.appareil || '') + ' · ' + quand) + '">' +
      esc((a.appareil_lisible || a.appareil || '') + ' · ' + quand) + '</small></span>' +
    '<span class="tact">' + (a.autorisee
      ? '<span class="chip" data-tone="ok">autorisée</span>'
      : '<button class="btn btn-ghost btn-sm" data-camallow="' + esc(a.application || '') + '">Autoriser</button>') +
    '</span></div>';
}

function renderCamera(d, titre) {
  var acces = d.acces || [];
  var alertes = d.alertes || acces.filter(function (a) { return a.en_cours && !a.autorisee; });

  /* Un accès non autorisé en cours : le signal le plus fort de l'interface. */
  var alarm = $('camAlarm');
  if (alertes.length) {
    alarm.hidden = false;
    alarm.innerHTML = '<div class="alarm-h">' + icon('alert') +
      '<span>' + num(alertes.length) + ' accès NON AUTORISÉ en cours</span></div>' +
      '<div class="alarm-l">' + alertes.map(function (a) {
        return '<div class="alarm-i"><b>' + esc(a.application || '?') + '</b>' +
          '<small>' + esc((a.appareil_lisible || a.appareil || '') +
            ' · depuis ' + (a.debut ? shortDate(a.debut) : 'date inconnue')) + '</small>' +
          '<span class="tact"><button class="btn btn-ghost btn-sm" data-camallow="' +
            esc(a.application || '') + '">Déclarer légitime</button></span></div>';
      }).join('') + '</div>' +
      '<p class="alarm-n">Une application tient la caméra ou le micro <b>en ce moment</b> sans avoir été ' +
      'déclarée. La cause la plus fréquente reste une visioconférence laissée ouverte : dans ce cas, ' +
      'déclare-la légitime. Sinon, ferme-la et lance une analyse.</p>';
  } else {
    alarm.hidden = true;
    alarm.innerHTML = '';
  }

  var t = $('camTable');
  t.innerHTML = acces.length
    ? scroller(acces.map(camRow).join(''))
    : '<p class="empty">Aucun accès enregistré sur la période. Sous Windows, cette liste vient du registre ' +
      'de confidentialité (ConsentStore) — la même source que l\'indicateur de la barre des tâches.</p>';

  var autorisees = d.autorisees || [];
  $('camAllowed').innerHTML = autorisees.length
    ? autorisees.map(function (a) {
        return '<div class="trow"><span class="chip" data-tone="ok">autorisée</span>' +
          '<span class="tmain"><b>' + esc(a) + '</b></span>' +
          '<span class="tact"><button class="btn btn-ghost btn-sm" data-camrevoke="' + esc(a) + '">Retirer</button></span></div>';
      }).join('')
    : '<p class="empty">Aucune application déclarée légitime.</p>';

  var manquantes = Object.keys(d.sources || {}).filter(function (k) {
    return String(d.sources[k]) !== 'ok';
  }).length;
  showResult('camResult', alertes.length ? 'danger' : (manquantes ? 'warn' : 'ok'),
    titre || 'Relevé caméra et microphone',
    kv([['Accès listés', num(acces.length)],
        ['En cours', num((d.en_cours || acces.filter(function (a) { return a.en_cours; })).length)],
        ['Non autorisés', num(alertes.length)],
        ['Applications déclarées', num(autorisees.length)]]) +
    sourcesBlock(d.sources, 'Appareils interrogés') +
    (d.rappel ? '<p class="p-note">' + esc(d.rappel) + '</p>' : ''));
}

function loadCamera(silent) {
  return call('camera_state', {}).then(function (res) {
    if (!res.ok) { handleFail(res, 'camResult', 'Caméra'); return; }
    var d = res.data || {};
    setCamLamp(!!d.surveillance_active);
    renderCamera(d, 'État actuel de la caméra et du microphone');
    if (!silent && (d.alertes || []).length) {
      toast('Caméra', num(d.alertes.length) + ' accès non autorisé en cours.', 'danger');
    }
  });
}

$('btnCamState').addEventListener('click', function () { loadCamera(false); });

$('btnCamRecent').addEventListener('click', function () {
  var heures = Number($('camHours').value) || 24;
  call('camera_recent', { heures: heures }).then(function (res) {
    if (!res.ok) { handleFail(res, 'camResult', 'Caméra'); return; }
    var d = res.data || {};
    renderCamera(d, 'Utilisations des ' + num(d.periode_heures || heures) + ' dernières heures');
  });
});

function camAllow(app) {
  if (!app) { toast('Caméra', 'Indiquez le nom de l\'application.', 'warn'); return; }
  call('camera_allow', { app: app }).then(function (res) {
    if (!res.ok) { handleFail(res, 'camResult', 'Caméra'); return; }
    toast('Caméra', '« ' + app + ' » est déclarée légitime : elle ne déclenchera plus d\'alerte.', 'ok');
    $('camApp').value = '';
    loadCamera(true);
  });
}

$('btnCamAllow').addEventListener('click', function () { camAllow($('camApp').value.trim()); });

$('pCamera').addEventListener('click', function (e) {
  var a = e.target.closest('[data-camallow]');
  var r = e.target.closest('[data-camrevoke]');
  if (a) camAllow(a.dataset.camallow);
  if (r) {
    call('camera_revoke', { app: r.dataset.camrevoke }).then(function (res) {
      if (!res.ok) { handleFail(res, 'camResult', 'Caméra'); return; }
      toast('Caméra', 'Autorisation retirée pour « ' + r.dataset.camrevoke + ' ».', 'warn');
      loadCamera(true);
    });
  }
});

$('btnCamWatch').addEventListener('click', function () {
  call('camera_watch_start', {}).then(function (res) {
    if (!res.ok) { handleFail(res, 'camResult', 'Caméra'); return; }
    var d = res.data || {};
    setCamLamp(true);
    showResult('camResult', 'ok', d.deja_active ? 'Surveillance déjà active' : 'Surveillance démarrée',
      '<p>Chaque nouvelle activation par une application non déclarée produit une notification. ' +
      'Une même session d\'utilisation n\'alerte qu\'une fois : sans cette mémoire, une visioconférence ' +
      'd\'une heure produirait une alerte toutes les cinq secondes.</p>' +
      kv([['Intervalle', esc((d.intervalle != null ? d.intervalle : 5) + ' s')]]));
    toast('Caméra', 'Surveillance continue démarrée.', 'ok');
    loadStatus(true);
  });
});

$('btnCamWatchStop').addEventListener('click', function () {
  call('camera_watch_stop', {}).then(function (res) {
    if (!res.ok) { handleFail(res, 'camResult', 'Caméra'); return; }
    setCamLamp(false);
    showResult('camResult', 'warn', 'Surveillance arrêtée',
      '<p>Les activations de la caméra et du microphone ne sont plus signalées.</p>');
    toast('Caméra', 'Surveillance arrêtée.', 'warn');
    loadStatus(true);
  });
});


/* ══ CONNEXIONS SORTANTES ════════════════════════════════════════════════ */
var NIVEAUX = {
  a_examiner: { tone: 'danger', txt: 'à examiner', cls: ' is-hot' },
  suspect:    { tone: 'warn',   txt: 'suspect',    cls: ' is-warm' },
  normal:     { tone: 'info',   txt: 'normal',     cls: '' }
};

var netView = segGroup('netview');

function netRow(c) {
  var n = NIVEAUX[c.niveau] || NIVEAUX.normal;
  var dest = (c.adresse_distante || '') + (c.port_distant != null ? ':' + c.port_distant : '');
  var raisons = (c.raisons || []).join(' · ');
  return '<div class="trow is-multi' + n.cls + '">' +
    '<span class="chip" data-tone="' + n.tone + '">' + esc(n.txt) + '</span>' +
    '<span class="tmain"><b>' + esc(c.processus || '?') + '</b><small title="' +
      esc(c.chemin || '') + '">' + esc(c.chemin || '') + '</small></span>' +
    '<span class="tsize">' + esc(dest) + '</span>' +
    '<span class="treason">' + (raisons ? esc(raisons) : '<em>aucun signal — ' +
      esc(c.nom_de_domaine || 'pas de nom de domaine connu') + '</em>') +
    ' <em>· score ' + esc(c.score != null ? c.score : 0) + '</em></span>' +
    '</div>';
}

function appRow(a) {
  var n = NIVEAUX[a.niveau] || NIVEAUX.normal;
  var raisons = (a.raisons || []).join(' · ');
  return '<div class="trow is-multi' + n.cls + '">' +
    '<span class="chip" data-tone="' + n.tone + '">' + esc(n.txt) + '</span>' +
    '<span class="tmain"><b>' + esc(a.processus || '?') + '</b><small title="' +
      esc(a.chemin || '') + '">' + esc(a.chemin || '') + '</small></span>' +
    '<span class="tsize">' + num(a.connexions || 0) + ' conn.</span>' +
    '<span class="treason">' + (raisons ? esc(raisons) : '<em>aucun signal</em>') +
    ' <em>· ' + esc((a.destinations || []).slice(0, 4).join(', ')) +
    ((a.destinations || []).length > 4 ? ' …' : '') + '</em></span>' +
    '</div>';
}

$('btnNet').addEventListener('click', function () {
  var parApp = netView.v === 'applications';
  call(parApp ? 'network_apps' : 'network_connections', {}).then(function (res) {
    if (!res.ok) {
      handleFail(res, 'netResult', 'Connexions');
      $('netTable').innerHTML = '<p class="empty">' + esc(res.reason || res.error || '') + '</p>';
      return;
    }
    var d = res.data || {};
    var liste = parApp ? (d.applications || []) : (d.connexions || []);
    $('netTable').innerHTML = liste.length
      ? scroller(liste.map(parApp ? appRow : netRow).join(''))
      : '<p class="empty">Aucune connexion établie au moment du relevé.</p>';

    var chauds = liste.filter(function (x) { return (x.niveau || '') === 'a_examiner'; }).length;
    var tiedes = liste.filter(function (x) { return (x.niveau || '') === 'suspect'; }).length;
    showResult('netResult', chauds ? 'danger' : (tiedes ? 'warn' : 'ok'),
      chauds ? num(chauds) + ' connexion(s) à examiner'
             : (tiedes ? num(tiedes) + ' connexion(s) suspecte(s)' : 'Aucun signal anormal'),
      kv([[parApp ? 'Applications' : 'Connexions', num(d.total || liste.length)],
          ['À examiner', num(chauds)],
          ['Suspectes', num(tiedes)]]) +
      '<p class="p-note">Aucun de ces signaux n\'est une preuve pris isolément : c\'est leur accumulation ' +
      'sur une même connexion qui mérite un regard. Le blocage relève du pare-feu applicatif, pas de ce relevé.</p>');
    logLine('Relevé des connexions : ' + num(liste.length) + ' entrée(s).', chauds ? 'danger' : 'ok');
  });
});


/* ══ QUI ACCÈDE À CET ORDINATEUR ═════════════════════════════════════════ */
var CONSTAT_NIVEAUX = {
  important:   { tone: 'danger', txt: 'important',  cls: ' is-hot' },
  a_verifier:  { tone: 'warn',   txt: 'à vérifier', cls: ' is-warm' },
  information: { tone: 'info',   txt: 'information', cls: '' }
};

function renderIntrusion(d) {
  var constats = d.constats || [];
  $('intruTable').innerHTML = constats.length
    ? scroller(constats.map(function (c) {
        var n = CONSTAT_NIVEAUX[c.niveau] || CONSTAT_NIVEAUX.information;
        return '<div class="trow is-multi is-desc' + n.cls + '">' +
          '<span class="chip" data-tone="' + n.tone + '">' + esc(n.txt) + '</span>' +
          '<span class="tmain"><b>' + esc(c.titre || '') + '</b><small>' + esc(c.categorie || '') + '</small></span>' +
          '<span class="treason">' + esc(c.detail || '') + '</span></div>';
      }).join(''))
    : '<p class="empty">Aucun constat sur les sources qui ont répondu. Ce n\'est pas une garantie : ' +
      'lisez ci-dessous quelles sources n\'ont pas pu être interrogées.</p>';

  var manquantes = Object.keys(d.sources || {}).filter(function (k) {
    return String(d.sources[k]) !== 'ok';
  }).length;
  showResult('intruResult',
    d.importants ? 'danger' : (d.a_verifier || manquantes ? 'warn' : 'ok'),
    d.importants ? num(d.importants) + ' constat(s) important(s)'
                 : (d.a_verifier ? num(d.a_verifier) + ' constat(s) à vérifier' : 'Aucun constat marquant'),
    kv([['Importants', num(d.importants || 0)],
        ['À vérifier', num(d.a_verifier || 0)],
        ['Total', num(constats.length)]]) +
    sourcesBlock(d.sources, 'Sources du rapport') +
    (d.avertissement ? '<p class="p-note">' + esc(d.avertissement) + '</p>' : ''));
}

$('btnIntru').addEventListener('click', function () {
  var jours = Number($('intruDays').value) || 7;
  call('intrusion_report', { jours: jours }).then(function (res) {
    if (!res.ok) { handleFail(res, 'intruResult', 'Accès à la machine'); return; }
    renderIntrusion(res.data || {});
    logLine('Rapport d\'accès établi sur ' + num(jours) + ' jour(s).', 'ok');
  });
});

/* ── Audit d'accès aux fichiers : la seule action destructive des modules V2 */
$('btnAudit').addEventListener('click', function () {
  var folders = splitPaths($('auditFolders').value || $('auditFolders').placeholder);
  if (!folders.length) { toast('Audit', 'Indiquez au moins un dossier.', 'warn'); return; }
  destructive({
    action: 'intrusion_audit_enable',
    body: { folders: folders },
    title: 'Activer l\'audit d\'accès aux fichiers',
    lead: 'La stratégie d\'audit du système et les règles d\'audit des dossiers listés seront modifiées. ' +
          'Rien n\'a encore été touché.',
    label: 'Audit des accès',
    reversible: true,
    unit: 'dossier',
    countLabel: 'Dossiers tracés',
    planTitle: 'Dossiers concernés',
    execLabel: 'Activer l\'audit',
    revNote: 'Réversible : la stratégie d\'audit et les règles posées se retirent par les mêmes outils ' +
             '(auditpol, propriétés de sécurité du dossier). N\'enregistre RIEN du passé — seuls les accès ' +
             'postérieurs à l\'activation seront tracés.',
    resultId: 'auditResult',
    onDone: function (d) {
      var env = v2Result(d);
      var r = env.data || {};
      var traces = r.dossiers_traces || [];
      var echecs = r.echecs || [];
      showResult('auditResult', env.ok && traces.length ? 'ok' : 'warn',
        env.ok && traces.length ? 'Audit activé sur ' + num(traces.length) + ' dossier(s)' : 'Audit non appliqué',
        kv([['Dossiers tracés', num(traces.length)], ['Échecs', num(echecs.length)]]) +
        (traces.length ? subTable('Tracés', traces.map(function (f) {
          return '<div class="trow"><span class="chip" data-tone="ok">tracé</span>' +
            '<span class="tmain"><b>' + esc(f) + '</b></span></div>';
        })) : '') +
        (echecs.length ? subTable('Refusés', echecs.map(function (f) {
          return '<div class="trow is-warm"><span class="chip" data-tone="warn">refusé</span>' +
            '<span class="tmain"><b>' + esc(f) + '</b><small>droits administrateur requis</small></span></div>';
        })) : '') +
        '<p class="p-note">' + esc(r.rappel || env.error ||
          'Seuls les accès à partir de maintenant seront enregistrés.') + '</p>');
    }
  });
});


/* ══ HISTORIQUE UNIFIÉ ═══════════════════════════════════════════════════
   La promesse « tout est réversible » rendue visible : une vue chronologique,
   un bouton « annuler » par entrée, et les mécanismes en panne affichés.
   ════════════════════════════════════════════════════════════════════════ */
var histSrc = segGroup('histsrc', function () { loadHistory(); });

function histRow(e) {
  var quand = e.horodatage ? shortDate(e.horodatage) : 'date inconnue';
  return '<div class="trow is-desc">' +
    '<span class="chip" data-tone="' + (e.annulable ? 'brand' : 'info') + '">' + esc(srcLabel(e.source)) + '</span>' +
    '<span class="tmain"><b>' + esc(e.description || e.type_action || '—') + '</b><small>' +
      esc(e.type_action || '') + '</small></span>' +
    '<span class="tstamp">' + esc(quand) + '</span>' +
    '<span class="tact">' + (e.annulable
      ? '<button class="btn btn-ghost btn-sm" data-undo="' + esc(e.id) + '">Annuler</button>'
      : '<button class="btn btn-ghost btn-sm" disabled title="' +
        esc(e.raison_non_annulable || 'action non annulable') + '">Non annulable</button>') +
    '</span></div>';
}

function loadHistory() {
  var filtre = histSrc.v || null;
  return call('history_list', { limite: 100, filtre: filtre }).then(function (res) {
    if (!res.ok) {
      handleFail(res, 'histResult', 'Historique');
      $('histTable').innerHTML = '<p class="empty">' + esc(res.reason || res.error || '') + '</p>';
      return;
    }
    var d = res.data || {};
    var entrees = d.entrees || [];
    $('histTable').innerHTML = entrees.length
      ? scroller(entrees.map(histRow).join(''))
      : '<p class="empty">Aucune entrée pour ce filtre. Rien n\'a encore été mis de côté, isolé ou réorganisé.</p>';

    var problemes = d.problemes || [];
    showResult('histResult', problemes.length ? 'warn' : 'info',
      num(d.total || entrees.length) + ' action(s) dans l\'historique',
      kv([['Affichées', num(d.affichees || entrees.length)],
          ['Annulables', num(d.annulables || 0)],
          ['Total', num(d.total || entrees.length)],
          ['Mécanismes en défaut', num(problemes.length)]]) +
      problemsBlock(problemes));
  });
}

$('btnHist').addEventListener('click', loadHistory);

/* La vue chronologique se remplit d'elle-même à la première visite : un
   historique qui exige un clic sur « Actualiser » pour montrer quoi que ce
   soit ne rend pas la réversibilité visible. Lecture d'index locaux, aucun
   appel système : le coût est négligeable. */
var histCharge = false;

function autoHistorique() {
  if (histCharge) return;
  if ((location.hash || '').replace(/^#\//, '') !== 'historique') return;
  histCharge = true;
  loadHistory();
}

window.addEventListener('hashchange', autoHistorique);
autoHistorique();

$('histTable').addEventListener('click', function (e) {
  var b = e.target.closest('[data-undo]');
  if (!b) return;
  var row = b.closest('.trow');
  var nom = row.querySelector('.tmain b').textContent;
  b.disabled = true;
  call('history_undo', { id: b.dataset.undo }).then(function (res) {
    if (!res.ok) {
      b.disabled = false;
      handleFail(res, 'histResult', 'Annulation');
      return;
    }
    row.classList.add('is-gone');
    toast('Historique', 'Annulé : ' + nom, 'ok');
    setTimeout(function () { loadHistory(); loadStatus(true); }, 520);
  });
});

/* ══════════════════════════════════════════════════════════════════════════
   COUCHE ASSISTANT — mémoire, jugement du risque, veille, initiative

   Une seule règle gouverne cette section, et c'est elle qui en dicte la
   forme : COMPRENDRE et EXÉCUTER sont deux gestes séparés par un clic
   humain. `assistant.comprendre` ne lance rien — elle rend une lecture, sa
   justification, son niveau de risque et sa confiance. L'exécution part
   d'un autre bouton, sur une autre action du pont. Une capacité destructive
   y ajoute encore le cycle déjà en place partout ailleurs dans cette
   interface : dry_run → plan affiché → jeton de confirmation.

   Deuxième règle, conséquence de la première : l'interface ne décide jamais
   à la place du backend. Elle n'additionne pas les scores, ne compare pas
   les seuils, ne suppose pas un paramètre absent. Elle lit `comprise`,
   `exige_confirmation`, `risque` — et elle demande quand il manque quelque
   chose.
   ══════════════════════════════════════════════════════════════════════════ */

/* Miroir d'AFFICHAGE du seuil normatif de `assistant/intent.py`
   (SEUIL_CONFIANCE = 0,55). Il ne sert QU'À dessiner la barre du seuil sur
   la jauge : la décision « comprise ou non » est prise par le backend et
   lue dans le champ `comprise`. L'interface ne refait jamais ce calcul —
   sinon deux vérités cohabiteraient, et un jour elles divergeraient. */
var AS_SEUIL = 0.55;

/* Échelle de risque du contrat : 0 lecture, 1 réversible, 2 destructif,
   3 irréversible. Le libellé vient du pont ; seul le timbre est ici. */
var AS_RISQUE_TONE = ['info', 'brand', 'warn', 'danger'];
var AS_GRAVITE_TONE = { info: 'info', alerte: 'warn', critique: 'danger' };
var AS_URGENCE_MOT = ['confort', 'à faire', 'important', 'urgent'];

/* Libellés des paramètres que les capacités déclarent. Un paramètre inconnu
   garde son nom brut : mieux vaut un intitulé technique qu'un intitulé
   inventé sur un champ que l'utilisateur va remplir. */
var AS_PARAM_LABELS = {
  path: 'Dossier ou fichier', folders: 'Dossiers (séparés par des virgules)',
  files: 'Fichiers (séparés par des virgules)', apps: 'Applications (séparées par des virgules)',
  app: 'Application', id: 'Identifiant de l\'entrée', url: 'Adresse à vérifier',
  mode: 'Mode de rangement (category, application, importance)',
  sort_by: 'Tri (size, date, name)', include_admin: 'Inclure les emplacements administrateur',
  older_than_days: 'Plus ancien que (jours)', days: 'Jours', jours: 'Jours', heures: 'Heures',
  unused_threshold_days: 'Sans usage depuis (jours)', source: 'Source', target: 'Dossier d\'arrivée',
  session_id: 'Identifiant de session', kind: 'Nature', plan: 'Plan', limite: 'Limite',
  hive: 'Ruche du registre', key_path: 'Clé du registre', name: 'Nom', day: 'Jour', time: 'Heure'
};
/* Paramètres qui attendent une liste, et non une chaîne. */
var AS_PARAM_LISTES = { folders: 1, files: 1, apps: 1 };
/* Paramètres booléens : une case, jamais un champ texte à interpréter. */
var AS_PARAM_BOOL = { include_admin: 1 };

/* État local de la section. Rien n'est persisté : la mémoire de l'assistant
   est côté backend, et une page rechargée doit relire, pas se souvenir. */
var AS_LECTURE = null;     /* dernière intention lue, ou null */
var AS_TELE = null;
var AS_NOTIFS = [];
var AS_SUGG = [];
var AS_AUTO = null;

function asRisqueTone(n) {
  var i = Number(n);
  return AS_RISQUE_TONE[i] || (i >= 3 ? 'danger' : 'info');
}

function pourcent(v, dec) {
  var n = Number(v);
  if (!isFinite(n)) return '—';
  return n.toFixed(dec == null ? 0 : dec).replace('.', ',') + ' %';
}

/* Horodatages de la couche assistant : des secondes epoch, jamais une ISO. */
function asQuand(ts) {
  var n = Number(ts);
  if (!isFinite(n) || n <= 0) return '—';
  var d = new Date(n * 1000);
  if (isNaN(d.getTime())) return '—';
  var meme = d.toDateString() === new Date().toDateString();
  return meme ? hhmmss(d).slice(0, 5)
              : d.toLocaleDateString('fr-FR', { day: '2-digit', month: 'short' }) + ' ' + hhmmss(d).slice(0, 5);
}

function asSecondes(s) {
  var n = Number(s) || 0;
  if (n < 90) return num(n) + ' s';
  return num(n / 60) + ' min';
}


/* ══ 1. CONSOLE DE COMMANDE ═══════════════════════════════════════════════
   Le point le plus important de la section. Le bouton « Comprendre »
   n'appelle que `assistant.comprendre`, qui ne touche à rien. Le bouton
   « Exécuter » est un autre bouton, dans une autre marche, et il reste
   verrouillé tant qu'aucune lecture n'a franchi le seuil du backend.
   ════════════════════════════════════════════════════════════════════════ */

/* Remet l'étape 02 à zéro. Appelée dès que la phrase change : une lecture
   qui ne correspond plus à ce qui est écrit dans le champ serait un piège. */
function asOublierLecture(note) {
  AS_LECTURE = null;
  $('asLecture').hidden = true;
  $('asLecture').dataset.etat = 'vide';
  var s2 = $('asStep2');
  s2.dataset.etat = 'vide';
  $('asExecNote').innerHTML = note || 'Aucune intention lue pour l\'instant : l\'exécution reste ' +
    'verrouillée. C\'est ce verrou qui empêche une phrase mal formulée de déclencher une purge.';
  var b = $('btnAsExecuter');
  b.disabled = true;
  b.innerHTML = icon('check') + ' Exécuter cette capacité';
  $('btnAsOublier').disabled = true;
  var p = $('asParams');
  p.hidden = true;
  p.innerHTML = '';
}

/* Peint la jauge de confiance. `null` = la notion n'a pas de sens ici
   (une suggestion du backend n'est pas une lecture de phrase) : le bloc
   disparaît plutôt que d'afficher un score inventé. */
function asPeindreConfiance(confiance, comprise) {
  var bloc = $('asConf');
  if (confiance == null) { bloc.hidden = true; return; }
  bloc.hidden = false;
  var c = Math.max(0, Math.min(1, Number(confiance) || 0));
  bloc.dataset.franchi = comprise ? 'true' : 'false';
  $('asConfVal').textContent = pourcent(c * 100);
  $('asConfFill').style.width = (c * 100).toFixed(1) + '%';
  var seuil = $('asConfSeuil');
  seuil.style.left = (AS_SEUIL * 100).toFixed(1) + '%';
  seuil.querySelector('span').textContent = 'seuil ' + pourcent(AS_SEUIL * 100);
  $('asConfNote').textContent = comprise
    ? 'La certitude dépasse le seuil requis pour proposer une exécution. Le seuil est franchi, ' +
      'pas l\'action : elle attend toujours votre clic.'
    : 'La certitude reste sous le seuil requis pour agir. L\'assistant demande une reformulation ' +
      'au lieu de deviner — c\'est voulu.';
}

/* Affiche la lecture rendue par `assistant.comprendre`, telle quelle. */
function asPeindreLecture(d) {
  var detail = d.detail || null;
  var comprise = !!d.comprise;
  AS_LECTURE = {
    capacite: d.capacite || '', parametres: d.parametres || {},
    detail: detail, origine: d.origine || 'phrase'
  };

  var carte = $('asLecture');
  carte.hidden = false;
  carte.dataset.etat = comprise ? 'comprise' : 'refusee';

  $('asLectKicker').textContent = d.origine === 'suggestion'
    ? 'Suggestion reprise dans la console'
    : (comprise ? 'Intention comprise' : 'Aucune intention retenue');

  var puce = $('asLectRisque');
  if (detail) {
    puce.dataset.tone = asRisqueTone(detail.risque);
    puce.textContent = detail.risque_libelle || ('risque ' + detail.risque);
  } else {
    puce.dataset.tone = 'warn';
    puce.textContent = 'aucune capacité';
  }

  $('asLectTitre').textContent = detail ? detail.titre
    : (comprise ? (d.capacite || '—') : 'Reformulez votre demande');
  $('asLectDesc').textContent = detail ? (detail.description || '')
    : 'L\'assistant préfère demander plutôt que deviner. Rien n\'a été exécuté, rien n\'a été préparé.';

  asPeindreConfiance(d.confiance, comprise);
  $('asLectJust').textContent = d.justification || '—';

  var lignes = [];
  if (detail) {
    lignes.push(['Capacité', '<span class="mono">' + esc(detail.nom) + '</span>']);
    lignes.push(['Catégorie', esc(detail.categorie || '—')]);
    lignes.push(['Action du pont', '<span class="mono">' + esc(detail.action_bridge || '(interne)') + '</span>']);
    var lus = Object.keys(d.parametres || {});
    lignes.push(['Paramètres lus dans la phrase', lus.length
      ? lus.map(function (k) { return esc(k) + ' = ' + esc(d.parametres[k]); }).join('<br>')
      : 'aucun']);
  }
  /* Champ redondant et volontaire, comme dans la réponse du pont : il dit
     dans l'interface elle-même qu'aucune action n'a eu lieu. */
  lignes.push(['Exécution', 'aucune — cette étape ne touche à rien']);
  $('asLectMeta').innerHTML = kv(lignes);

  if (!comprise || !detail) {
    $('asStep2').dataset.etat = 'refusee';
    $('asExecNote').innerHTML = 'Rien à exécuter : la lecture n\'a pas franchi le seuil du backend. ' +
      'Nommez l\'action <b>et</b> son objet — « analyse le dossier Téléchargements » plutôt que ' +
      '« occupe-toi de mes fichiers ».';
    $('btnAsExecuter').disabled = true;
    $('btnAsOublier').disabled = false;
    $('asParams').hidden = true;
    return;
  }

  asPreparerExecution(detail, d.parametres || {});
}

/* Prépare l'étape 02 : annonce ce que le clic engagera, et réclame les
   paramètres que la phrase ne contenait pas. Deviner un chemin, c'est
   nettoyer au mauvais endroit : ils sont demandés, jamais supposés. */
function asPreparerExecution(detail, parametres) {
  var s2 = $('asStep2');
  s2.dataset.etat = 'prete';

  var manquants = (detail.parametres || []).filter(function (nom) {
    return !(nom in (parametres || {}));
  });

  var zone = $('asParams');
  if (manquants.length) {
    zone.hidden = false;
    zone.innerHTML = '<p class="as-params-h">Cette capacité attend des précisions absentes de votre ' +
      'phrase. Elles ne seront pas devinées : un paramètre supposé, c\'est une action au mauvais ' +
      'endroit. Un champ laissé vide n\'est pas envoyé — le backend appliquera son propre défaut ou ' +
      'refusera proprement.</p>' +
      manquants.map(function (nom) {
        var lbl = AS_PARAM_LABELS[nom] || nom;
        if (AS_PARAM_BOOL[nom]) {
          return '<label class="check as-param-check"><input type="checkbox" data-asparam="' + esc(nom) +
            '" data-astype="bool"><span></span>' + esc(lbl) + '</label>';
        }
        return '<label class="as-param"><span>' + esc(lbl) + '</span>' +
          '<input class="inp" type="text" autocomplete="off" data-asparam="' + esc(nom) + '"' +
          (AS_PARAM_LISTES[nom] ? ' data-astype="liste"' : '') + '></label>';
      }).join('');
  } else {
    zone.hidden = true;
    zone.innerHTML = '';
  }

  $('asExecNote').innerHTML = detail.exige_confirmation
    ? '<b>' + esc(detail.titre) + '</b> est une capacité de niveau <b>' + esc(detail.risque_libelle) +
      '</b>. Le bouton n\'exécute rien directement : il demande d\'abord le plan exact au backend ' +
      '(<span class="mono">dry_run</span>), l\'affiche, et n\'exécute qu\'avec le jeton de ' +
      'confirmation et votre accusé de lecture — comme toutes les opérations destructives de cette interface.'
    : '<b>' + esc(detail.titre) + '</b> est une capacité de niveau <b>' + esc(detail.risque_libelle) +
      '</b> : elle n\'écrit rien qui ne soit réversible depuis l\'Historique. Le clic l\'exécute directement.';

  var b = $('btnAsExecuter');
  b.disabled = false;
  b.innerHTML = icon(detail.exige_confirmation ? 'alert' : 'check') + ' ' +
    (detail.exige_confirmation ? 'Voir le plan puis exécuter' : 'Exécuter') +
    ' — ' + esc(detail.titre);
  $('btnAsOublier').disabled = false;
}

/* Relit les champs de l'étape 02. Un champ vide est OMIS : envoyer une
   chaîne vide ferait croire au backend qu'on a répondu. */
function asRassemblerParams() {
  var out = {};
  var lus = (AS_LECTURE && AS_LECTURE.parametres) || {};
  Object.keys(lus).forEach(function (k) { out[k] = lus[k]; });

  $$('[data-asparam]', $('asParams')).forEach(function (el) {
    var nom = el.dataset.asparam;
    if (el.dataset.astype === 'bool') { if (el.checked) out[nom] = true; return; }
    var v = String(el.value || '').trim();
    if (!v) return;
    out[nom] = el.dataset.astype === 'liste' ? splitPaths(v) : v;
  });
  return out;
}

function asComprendre() {
  var phrase = String($('asPhrase').value || '').trim();
  if (!phrase) {
    toast('Console', 'Écrivez d\'abord ce que vous voulez faire.', 'warn');
    $('asPhrase').focus();
    return;
  }
  asOublierLecture();
  $('asCmdResult').hidden = true;
  var b = $('btnAsComprendre');
  b.disabled = true;
  asVisage('active');

  call('assistant.comprendre', { phrase: phrase }).then(function (res) {
    b.disabled = false;
    asVisage('idle');
    if (!res.ok) {
      /* Module de compréhension absent : l'échec est annoncé pour ce qu'il
         est, et l'étape 02 reste verrouillée — jamais une exécution « au
         cas où » sur une lecture qui n'a pas eu lieu. */
      handleFail(res, 'asCmdResult', 'Compréhension');
      asOublierLecture('La compréhension des phrases est indisponible sur ce poste : aucune intention ' +
        'ne peut être lue, donc rien ne peut être exécuté depuis la console. Les suggestions et les ' +
        'panneaux de la section restent utilisables.');
      return;
    }
    var d = res.data || {};
    asPeindreLecture(d);
    logLine('Console : « ' + phrase + '  » → ' +
      (d.comprise ? d.capacite + ' (' + pourcent((d.confiance || 0) * 100) + ')' : 'aucune capacité retenue'),
      d.comprise ? 'info' : 'warn');
  });
}

/* Résumé générique d'un résultat de capacité. Chaque module rend sa propre
   forme ; la console ne prétend pas la connaître, elle en montre les
   grandeurs lisibles et renvoie au panneau spécialisé pour le détail. */
function asResumeGenerique(obj) {
  if (!obj || typeof obj !== 'object') return '';
  var pairs = [];
  Object.keys(obj).forEach(function (k) {
    if (pairs.length >= 8) return;
    var v = obj[k];
    if (v == null || k === 'ok') return;
    if (typeof v === 'boolean') pairs.push([k, v ? 'oui' : 'non']);
    else if (typeof v === 'number') pairs.push([k, num(v)]);
    else if (typeof v === 'string' && v.length <= 90) pairs.push([k, esc(v)]);
    else if (Array.isArray(v)) pairs.push([k, num(v.length) + ' élément(s)']);
  });
  return pairs.length ? kv(pairs) : '';
}

/* Seule voie d'exécution de la section : console et suggestions y passent
   toutes les deux. Le tri se fait sur ce que le PONT déclare
   (`exige_confirmation`), jamais sur une règle recopiée ici. */
function asExecuterCapacite(detail, params, resultId, apres) {
  if (!detail) return;
  asVisage('exec');
  var action = detail.action_bridge || '';
  if (!action) {
    showResult(resultId, 'warn', 'Capacité interne',
      '<p>« ' + esc(detail.titre) + ' » n\'a pas d\'action web : elle n\'est utilisée qu\'à ' +
      'l\'intérieur de l\'assistant et ne peut pas être lancée depuis l\'interface.</p>');
    asVisage('idle');
    return;
  }

  if (detail.exige_confirmation) {
    /* Exactement le mécanisme des autres actions destructives de
       l'interface — il n'est pas réécrit ici, il est appelé. */
    asVisage('idle');
    return destructive({
      action: action,
      body: params,
      title: detail.titre,
      label: detail.titre,
      lead: 'Cette capacité a été reconnue à partir de votre demande, puis retenue par votre clic. ' +
            'Voici ce qu\'elle ferait exactement. Rien n\'a encore été touché.',
      /* L'échelle du contrat, à la lettre : seul le niveau RÉVERSIBLE (1)
         est annulable par l'Historique. DESTRUCTIF (2) perd des données —
         la modale doit rester rouge et le dire, comme elle le fait déjà
         pour le nettoyage complet dans la section Nettoyage. */
      reversible: Number(detail.risque) <= 1,
      execLabel: Number(detail.risque) >= 3 ? 'Exécuter définitivement' : 'Exécuter',
      revNote: Number(detail.risque) >= 3
        ? '<b>Opération irréversible.</b> Ni l\'Historique ni la quarantaine ne pourront la défaire.'
        : (Number(detail.risque) >= 2
            ? '<b>Perte de données possible.</b> Ce que cette opération supprime ne sera pas ' +
              'récupérable depuis l\'Historique.'
            : 'Opération réversible : elle reste annulable depuis la section Historique.'),
      resultId: resultId,
      onDone: function (data) {
        var r = (data && data.result) || {};
        showResult(resultId, 'ok', '« ' + detail.titre + '  » exécutée',
          '<p>Capacité <span class="mono">' + esc(detail.nom) + '</span> exécutée après validation ' +
          'explicite du plan.</p>' + asResumeGenerique(r));
        loadStatus(true);
        if (apres) apres();
      }
    });
  }

  /* Le visage revient au repos : pendant la lecture du plan destructif, c'est
     l'utilisateur qui a la main, pas la machine. Le laisser sur « j'exécute »
     serait un mensonge — rien n'est en train d'être exécuté. */

  /* Lecture (ou réversible que le pont ne fait pas confirmer) : exécution
     directe. `runJob` couvre les deux formes de réponse du contrat — un
     résultat immédiat, ou un `job_id` à sonder. */
  showJobline('asCmdJob', true);
  $('asCmdJobTitle').textContent = detail.titre;
  pathText($('asCmdJobCurrent'), 'démarrage…');
  return runJob(action, params, {
    onProgress: function (d) { setJobline('asCmdJob', d); },
    onDone: function (r) {
      showJobline('asCmdJob', false);
      var res = r || {};
      showResult(resultId, 'ok', '« ' + detail.titre + '  » exécutée',
        '<p>Capacité <span class="mono">' + esc(detail.nom) + '</span> (' +
        esc(detail.risque_libelle) + ') exécutée. Le détail complet s\'affiche dans le panneau ' +
        'spécialisé de la section correspondante.</p>' + asResumeGenerique(res));
      toast(detail.titre, 'Capacité exécutée.', 'ok');
      loadStatus(true);
      asVisage('idle');
      if (apres) apres();
    },
    onFail: function (res) {
      showJobline('asCmdJob', false);
      asVisage('idle');
      handleFail(res, resultId, detail.titre);
    }
  });
}

$('btnAsComprendre').addEventListener('click', asComprendre);

$('asPhrase').addEventListener('keydown', function (e) {
  /* Entrée déclenche la COMPRÉHENSION, jamais l'exécution : la touche la
     plus facile à frapper par erreur ne doit pas pouvoir engager la machine. */
  if (e.key === 'Enter') { e.preventDefault(); asComprendre(); }
});

/* Une lecture ne survit pas à la modification de la phrase qui l'a produite. */
$('asPhrase').addEventListener('input', function () {
  if (AS_LECTURE) asOublierLecture('La phrase a changé depuis la dernière lecture : ' +
    'relancez « Comprendre » avant d\'exécuter quoi que ce soit.');
});

$$('[data-asphrase]').forEach(function (b) {
  b.addEventListener('click', function () {
    $('asPhrase').value = b.dataset.asphrase;
    asOublierLecture();
    $('asPhrase').focus();
  });
});

$('btnAsOublier').addEventListener('click', function () { asOublierLecture(); });

$('btnAsExecuter').addEventListener('click', function () {
  if (!AS_LECTURE || !AS_LECTURE.detail) return;
  asExecuterCapacite(AS_LECTURE.detail, asRassemblerParams(), 'asCmdResult', function () {
    asRafraichirAssistant(true);
  });
});


/* ══ 2. CYCLE DE CONFIRMATION SANS MODALE ════════════════════════════════
   Trois actions de la couche assistant écrivent et passent donc par
   `_guarded()` côté pont : profil, acquittement, mode autonome. Deux
   d'entre elles sont de niveau RÉVERSIBLE, pour lesquelles le pont répond
   lui-même `exige_confirmation: false`. Leur imposer la modale destructive
   apprendrait à cocher « j'ai lu » sans lire — exactement le risque que la
   modale existe pour écarter. Le couple dry_run → confirm_token est
   respecté à la lettre ; c'est le clic qui tient lieu de validation.
   L'activation du mode autonome, elle, garde la modale : son plan énonce la
   limite du mode, et cette limite doit être lue.
   ════════════════════════════════════════════════════════════════════════ */
function cycleGuarde(action, body) {
  var dry = {};
  Object.keys(body || {}).forEach(function (k) { dry[k] = body[k]; });
  dry.dry_run = true;

  return call(action, dry).then(function (res) {
    if (!res.ok) return res;
    var d = res.data || {};
    var token = d.confirm_token;
    if (!token) {
      return { ok: false, unavailable: false,
               error: 'Le backend n\'a pas fourni de jeton de confirmation : opération refusée par sécurité.' };
    }
    var reel = {};
    Object.keys(body || {}).forEach(function (k) { reel[k] = body[k]; });
    reel.dry_run = false;
    reel.confirm_token = token;
    return call(action, reel).then(function (r2) {
      if (r2 && r2.ok && r2.data) r2.data.plan = d.plan || null;
      return r2;
    });
  });
}


/* ══ 3. FIL DE NOTIFICATIONS ═════════════════════════════════════════════ */
var asNotifFiltre = segGroup('asnotif', function () { asChargerNotifs(); });

function asNotifRow(n) {
  var tone = AS_GRAVITE_TONE[n.gravite] || 'info';
  var acq = !!n.acquittee;
  return '<div class="trow is-multi is-desc' + (acq ? ' is-acq' : (tone === 'danger' ? ' is-warm' : '')) + '">' +
    '<span class="chip" data-tone="' + tone + '">' + esc(n.gravite) + '</span>' +
    '<span class="tmain"><b>' + esc(n.titre) + '</b><small>' + esc(n.source || '') + '</small></span>' +
    '<span class="as-when">' + esc(asQuand(n.horodatage)) + '</span>' +
    '<span class="tact">' + (acq
      ? '<span class="chip" data-tone="ok">' + icon('check') + 'acquittée</span>'
      : '<button class="btn btn-ghost btn-sm" type="button" data-asack="' + esc(n.identifiant) + '">Acquitter</button>') +
    '</span>' +
    '<span class="treason">' + esc(n.corps || '') + '</span></div>';
}

function asChargerNotifs() {
  return call('assistant.notifications',
    { non_acquittees_seulement: asNotifFiltre.v === '1' }).then(function (res) {
    if (!res.ok) {
      setEmpty('asNotifTable', 'Fil de notifications indisponible.');
      handleFail(res, 'asNotifResult', 'Notifications');
      return;
    }
    var d = res.data || {};
    AS_NOTIFS = d.notifications || [];
    var puce = $('asNotifCount');
    var nb = Number(d.non_acquittees) || 0;
    puce.dataset.tone = nb ? 'warn' : 'ok';
    puce.textContent = nb ? num(nb) + ' non acquittée(s)' : 'fil à jour';

    if (!AS_NOTIFS.length) {
      setEmpty('asNotifTable', asNotifFiltre.v === '1'
        ? 'Aucune alerte en attente : tout a été acquitté.'
        : 'Aucune notification. La veille n\'a rien eu à signaler.');
      return;
    }
    $('asNotifTable').innerHTML = scroller(AS_NOTIFS.map(asNotifRow).join(''));
  });
}

$('btnAsNotifs').addEventListener('click', function () { asChargerNotifs(); });

$('asNotifTable').addEventListener('click', function (e) {
  var b = e.target.closest ? e.target.closest('[data-asack]') : null;
  if (!b) return;
  var id = b.dataset.asack;
  var ligne = b.closest('.trow');
  var titre = ligne ? ligne.querySelector('.tmain b').textContent : 'alerte';
  b.disabled = true;
  cycleGuarde('assistant.acquitter', { identifiant: id }).then(function (res) {
    if (!res.ok) {
      b.disabled = false;
      handleFail(res, 'asNotifResult', 'Acquittement');
      return;
    }
    var r = (res.data && res.data.result) || {};
    if (r.acquittee === false) {
      b.disabled = false;
      showResult('asNotifResult', 'warn', 'Rien à acquitter',
        '<p>Aucune notification ne porte cet identifiant : le fil a peut-être été rechargé.</p>');
      return;
    }
    toast('Notifications', 'Acquittée : ' + titre, 'ok');
    asChargerNotifs();
  });
});


/* ══ 4. TÉLÉMÉTRIE EN DIRECT ═════════════════════════════════════════════
   `psutil` peut manquer — sur une machine à désinfecter, l'installer est
   parfois impossible. Dans ce cas la couche rend `disponible: false` et des
   échantillons à zéro : afficher « 0 % » serait un mensonge, les jauges
   passent donc à « n. d. » et la raison est écrite en clair.
   ════════════════════════════════════════════════════════════════════════ */
var asMetrique = segGroup('asmetric', function () {
  if (AS_TELE) asPeindreCourbe(AS_TELE.historique || [], asMetrique.v);
});

var AS_METRIQUE_NOM = { cpu: 'Processeur', memoire: 'Mémoire', disque: 'Disque système' };

function asJauge(caseId, valId, valeur, unite, seuils) {
  var g = $(caseId), b = $(valId);
  if (valeur == null || !isFinite(Number(valeur))) {
    g.dataset.tone = '';
    g.classList.add('is-mute');
    b.textContent = 'n. d.';
    return;
  }
  var v = Number(valeur);
  g.classList.remove('is-mute');
  g.dataset.tone = v >= seuils[1] ? 'danger' : (v >= seuils[0] ? 'warn' : '');
  b.textContent = unite === '%' ? pourcent(v) : (num(v) + ' ' + unite);
}

function asJaugesMuettes() {
  ['asGCpu', 'asGMem', 'asGDisk', 'asGTemp'].forEach(function (id) {
    var g = $(id);
    g.dataset.tone = '';
    g.classList.add('is-mute');
    g.querySelector('b').textContent = 'n. d.';
  });
}

/* Courbe sobre : une seule grandeur à la fois, échelle fixe 0–100 %, une
   ligne de repère à 50 %. Dessinée en pixels réels (et non par un viewBox
   étiré) pour que l'épaisseur du trait ne dépende pas de la largeur. */
function asPeindreCourbe(historique, metrique) {
  var el = $('asTeleChart');
  var pts = (historique || []).map(function (e) { return Number(e[metrique]); })
    .filter(function (v) { return isFinite(v); });

  if (pts.length < 2) {
    el.innerHTML = '<p class="empty">Historique en construction : il faut au moins deux relevés. ' +
      'Chaque lecture de cette section en ajoute un.</p>';
    return;
  }

  var W = Math.max(220, el.clientWidth - 24) || 320;
  var H = 96, pad = 3;
  var pas = (W - 26) / (pts.length - 1);
  var y = function (v) { return pad + (1 - Math.max(0, Math.min(100, v)) / 100) * (H - pad * 2); };
  var coords = pts.map(function (v, i) { return (26 + i * pas).toFixed(1) + ',' + y(v).toFixed(1); });
  var aire = 'M26,' + y(pts[0]).toFixed(1) + ' L' + coords.join(' L') +
    ' L' + (26 + (pts.length - 1) * pas).toFixed(1) + ',' + (H - pad).toFixed(1) +
    ' L26,' + (H - pad).toFixed(1) + ' Z';
  var dernier = pts[pts.length - 1];

  var duree = 0;
  if (historique.length > 1) {
    duree = Number(historique[historique.length - 1].horodatage) - Number(historique[0].horodatage);
  }

  el.innerHTML =
    '<svg viewBox="0 0 ' + W + ' ' + H + '" width="' + W + '" height="' + H + '" role="img" ' +
    'aria-label="Historique de ' + esc(AS_METRIQUE_NOM[metrique] || metrique) + '">' +
    '<defs><linearGradient id="asTeleGrad" x1="0" y1="0" x2="0" y2="1">' +
    '<stop offset="0%" stop-color="#ff9a2e" stop-opacity=".34"/>' +
    '<stop offset="100%" stop-color="#ff9a2e" stop-opacity="0"/>' +
    '</linearGradient></defs>' +
    '<line class="ac-grid" x1="26" y1="' + y(100).toFixed(1) + '" x2="' + W + '" y2="' + y(100).toFixed(1) + '"/>' +
    '<line class="ac-grid" x1="26" y1="' + y(50).toFixed(1) + '" x2="' + W + '" y2="' + y(50).toFixed(1) + '"/>' +
    '<line class="ac-grid" x1="26" y1="' + y(0).toFixed(1) + '" x2="' + W + '" y2="' + y(0).toFixed(1) + '"/>' +
    '<text class="ac-lbl" x="0" y="' + (y(100) + 3).toFixed(1) + '">100</text>' +
    '<text class="ac-lbl" x="4" y="' + (y(50) + 3).toFixed(1) + '">50</text>' +
    '<text class="ac-lbl" x="10" y="' + (y(0) + 3).toFixed(1) + '">0</text>' +
    '<path class="ac-area" d="' + aire + '"/>' +
    '<polyline class="ac-line" points="' + coords.join(' ') + '"/>' +
    '<circle class="ac-dot" cx="' + (26 + (pts.length - 1) * pas).toFixed(1) + '" cy="' +
    y(dernier).toFixed(1) + '" r="2.6"/>' +
    '</svg>' +
    '<p class="as-chart-foot"><span>' + esc(AS_METRIQUE_NOM[metrique] || metrique) + ' — ' +
    num(pts.length) + ' relevé(s) sur ' + esc(asSecondes(duree)) + '</span>' +
    '<span>dernier : ' + esc(pourcent(dernier)) + '</span></p>';
}

function asPeindreTele(d) {
  AS_TELE = d;
  var dispo = !!d.disponible;
  var e = d.echantillon || {};
  $('asTeleLamp').dataset.on = dispo ? 'true' : 'false';

  if (!dispo) {
    asJaugesMuettes();
    $('asTeleChart').innerHTML = '<p class="empty">Aucun historique : cette machine ne publie pas sa charge.</p>';
    showResult('asTeleResult', 'warn', 'Télémétrie indisponible',
      '<p>Le module <span class="mono">psutil</span> n\'est pas installé sur ce poste : la charge du ' +
      'processeur, de la mémoire, du disque et la température ne peuvent pas être relevées.</p>' +
      '<p class="p-note">Ce n\'est pas une panne, et rien d\'autre n\'en dépend : l\'analyse des ' +
      'fichiers, la quarantaine et les suggestions qui n\'utilisent pas la charge continuent de ' +
      'fonctionner normalement. Les valeurs sont affichées « n. d. » plutôt qu\'à zéro, pour ne pas ' +
      'faire passer une absence de mesure pour une machine au repos.</p>');
    $('asTeleNote').textContent = 'Jauges en « n. d. » : aucune mesure disponible, aucune valeur inventée.';
    return;
  }

  $('asTeleResult').hidden = true;
  asJauge('asGCpu', 'asTeleCpu', e.cpu, '%', [70, 90]);
  asJauge('asGMem', 'asTeleMem', e.memoire, '%', [80, 92]);
  asJauge('asGDisk', 'asTeleDisk', e.disque, '%', [85, 93]);
  asJauge('asGTemp', 'asTeleTemp', e.temperature, '°C', [75, 88]);

  var moy = d.moyennes || null;
  $('asTeleNote').textContent = moy
    ? 'Moyenne sur ' + asSecondes(d.fenetre) + ' : processeur ' + pourcent(moy.cpu) +
      ', mémoire ' + pourcent(moy.memoire) + ', disque ' + pourcent(moy.disque) +
      (e.temperature == null ? ' — température non publiée par cette machine.' : '.')
    : 'La télémétrie mesure, elle n\'interprète pas : l\'interprétation est le travail des suggestions.';

  asPeindreCourbe(d.historique || [], asMetrique.v);
}

function asChargerTele(silencieux) {
  return call('assistant.telemetrie', { secondes: 300, limite: 120 }).then(function (res) {
    if (!res.ok) {
      $('asTeleLamp').dataset.on = 'false';
      asJaugesMuettes();
      $('asTeleChart').innerHTML = '<p class="empty">Aucun historique disponible.</p>';
      if (!silencieux) handleFail(res, 'asTeleResult', 'Télémétrie');
      else showResult('asTeleResult', 'warn', 'Télémétrie indisponible',
        '<p>' + esc(res.reason || res.error || 'Module de télémétrie indisponible.') + '</p>');
      return;
    }
    asPeindreTele(res.data || {});
  });
}

$('btnAsTele').addEventListener('click', function () { asChargerTele(false); });


/* ══ 5. SUGGESTIONS ══════════════════════════════════════════════════════ */
function asSuggRow(s, i) {
  var d = s.detail || null;
  var u = Math.max(0, Math.min(3, Number(s.urgence) || 0));
  return '<div class="trow is-multi is-desc' + (u >= 3 ? ' is-warm' : '') + '">' +
    '<span class="as-urg" data-u="' + u + '" title="urgence ' + u + ' sur 3 — ' +
    esc(AS_URGENCE_MOT[u]) + '">' + u + '</span>' +
    '<span class="tmain"><b>' + esc(d ? d.titre : s.capacite) + '</b><small>' + esc(s.capacite) + '</small></span>' +
    (d ? '<span class="chip" data-tone="' + asRisqueTone(d.risque) + '">' + esc(d.risque_libelle) + '</span>' : '') +
    '<span class="tact"><button class="btn btn-ghost btn-sm" type="button" data-assugg="' + i + '">' +
    (d ? 'Reprendre' : 'Indisponible') + '</button></span>' +
    '<span class="treason">' + esc(s.motif || '') + '</span></div>';
}

function asChargerSugg() {
  return call('assistant.suggestions', {}).then(function (res) {
    if (!res.ok) {
      setEmpty('asSuggTable', 'Suggestions indisponibles.');
      handleFail(res, 'asSuggResult', 'Suggestions');
      return;
    }
    var d = res.data || {};
    /* Le pont les rend déjà triées ; le tri est refait ici parce que
       l'ordre d'affichage est une promesse de l'interface, pas un effet de
       bord de l'ordre d'arrivée. */
    AS_SUGG = (d.suggestions || []).slice().sort(function (a, b) {
      return (Number(b.urgence) || 0) - (Number(a.urgence) || 0);
    });
    var puce = $('asSuggCount');
    var urgentes = AS_SUGG.filter(function (s) { return Number(s.urgence) >= 2; }).length;
    puce.dataset.tone = urgentes ? 'warn' : (AS_SUGG.length ? 'info' : 'ok');
    puce.textContent = AS_SUGG.length
      ? num(AS_SUGG.length) + ' suggestion(s)' + (urgentes ? ', ' + num(urgentes) + ' importante(s)' : '')
      : 'rien à proposer';

    if (!AS_SUGG.length) {
      setEmpty('asSuggTable', 'Aucune suggestion : rien d\'anormal n\'a été constaté sur cette machine.');
      return;
    }
    $('asSuggTable').innerHTML = scroller(AS_SUGG.map(asSuggRow).join(''));
  });
}

$('btnAsSugg').addEventListener('click', function () { asChargerSugg(); });

/* Reprendre une suggestion, c'est la charger dans la console — pas la
   lancer. Elle emprunte alors exactement le même chemin qu'une phrase
   comprise : étape 02, paramètres réclamés, modale si le pont l'exige. Une
   seule porte de sortie vers l'exécution, donc une seule à surveiller. */
$('asSuggTable').addEventListener('click', function (e) {
  var b = e.target.closest ? e.target.closest('[data-assugg]') : null;
  if (!b) return;
  var s = AS_SUGG[Number(b.dataset.assugg)];
  if (!s || !s.detail) {
    showResult('asSuggResult', 'warn', 'Capacité inconnue du registre',
      '<p>Cette suggestion nomme une capacité que le registre ne décrit pas : elle ne peut pas être ' +
      'exécutée depuis l\'interface.</p>');
    return;
  }
  $('asPhrase').value = '';
  asPeindreLecture({
    phrase: '', capacite: s.capacite, parametres: {}, confiance: null,
    justification: 'Suggestion émise par la veille : ' + (s.motif || '') +
      ' (urgence ' + (Number(s.urgence) || 0) + ' sur 3, ' + AS_URGENCE_MOT[Math.max(0, Math.min(3, Number(s.urgence) || 0))] + ').',
    comprise: true, detail: s.detail, origine: 'suggestion'
  });
  $('pAssistCmd').scrollIntoView({ behavior: REDUCED ? 'auto' : 'smooth', block: 'center' });
  toast('Suggestion reprise', 'Elle attend votre clic dans la console : rien n\'est encore lancé.', 'info');
});


/* ══ 6. MODE AUTONOME ════════════════════════════════════════════════════
   Un interrupteur, son journal, et un arrêt qui n'attend pas. Ce que le
   mode NE FAIT PAS est écrit dans le panneau lui-même (encart jaune), parce
   que « mode autonome » laisse spontanément croire qu'une machine va
   toucher au disque toute seule. Elle ne le fera pas : le pont refuse toute
   capacité au-dessus de LECTURE, et tout le reste devient une suggestion.
   ════════════════════════════════════════════════════════════════════════ */
/* Le mot « action » est remplacé par « lecture » à l'affichage : c'est ce
   que le journal raconte réellement, puisque le mode autonome n'engage
   jamais autre chose. Écrit en fonction et non en table pour ne pas semer
   dans le fichier une paire `action: '…'` qui ressemblerait, aux yeux du
   contrôle statique des tests, à l'appel d'une action du pont. */
function asJournalMot(t) {
  if (t === 'action') return 'lecture';
  if (t === 'demarrage') return 'démarrage';
  if (t === 'arret') return 'arrêt';
  if (t === 'resultat') return 'résultat';
  return String(t || '—');
}

function asJournalTexte(e) {
  var t = e.type;
  var titre = e.titre || e.capacite || '';
  if (t === 'action') {
    return '« ' + titre + ' » engagée en lecture seule — ' + (e.motif || '');
  }
  if (t === 'resultat') {
    return '« ' + titre + ' » terminée : ' + (e.ok === false ? 'échec' : 'succès') +
      (e.duree != null ? ' en ' + asSecondes(e.duree) : '');
  }
  if (t === 'suggestion') {
    return '« ' + titre + ' » proposée, jamais exécutée — ' + (e.raison || e.motif || '');
  }
  return e.motif || e.raison || '—';
}

function asJournalRow(e) {
  var t = String(e.type || '');
  return '<div class="as-jrow" data-t="' + esc(t) + '">' +
    '<time>' + esc(asQuand(e.horodatage)) + '</time>' +
    '<span class="as-jk">' + esc(asJournalMot(t)) + '</span>' +
    '<p>' + esc(asJournalTexte(e)) + '</p></div>';
}

function asPeindreAuto(d) {
  AS_AUTO = d || {};
  d = AS_AUTO;
  var actif = !!d.actif;

  $('asAutoLamp').dataset.on = actif ? 'true' : 'false';
  var box = $('asAutoState');
  /* Jaune quand il tourne, pas rouge : ce n'est pas une urgence. Mais pas
     vert non plus — une machine qui agit seule mérite qu'on le remarque. */
  box.dataset.actif = actif ? 'warn' : 'false';
  $('asAutoStateVal').textContent = actif ? 'ACTIF — lecture seule' : 'Arrêté';

  var journal = d.journal || [];
  var agies = journal.filter(function (e) { return e.type === 'action'; }).length;
  var proposees = journal.filter(function (e) { return e.type === 'suggestion'; }).length;

  $('asAutoStateMsg').textContent = actif
    ? 'Un cycle toutes les ' + asSecondes(d.intervalle) + ', au plus ' + num(d.budget) +
      ' action(s) par cycle, uniquement des capacités de lecture. ' + num(d.cycles) +
      ' cycle(s) exécuté(s) depuis l\'activation.'
    : (d.executeur === false
        ? 'Aucun exécuteur n\'est branché sur cette instance : le mode autonome ne pourrait rien lancer.'
        : 'Aucune surveillance autonome en cours. Les suggestions restent calculées à la demande, ' +
          'et aucune action n\'est engagée sans vous.');

  $('asAutoKv').innerHTML = kv([
    ['Budget par cycle', num(d.budget) + ' action(s)'],
    ['Intervalle', esc(asSecondes(d.intervalle))],
    ['Cycles exécutés', num(d.cycles)],
    ['Actions engagées', num(agies) + ' (lecture seule)'],
    ['Devenues suggestions', num(proposees)]
  ]);

  $('btnAsAutoStart').disabled = actif;
  $('btnAsAutoStop').disabled = !actif;

  if (!journal.length) {
    setEmpty('asAutoJournal', 'Aucun cycle exécuté : le journal est vide.');
    return;
  }
  $('asAutoJournal').innerHTML = scroller(journal.slice().reverse().map(asJournalRow).join(''));
}

function asChargerAuto(silencieux) {
  return call('assistant.autonomie', {}).then(function (res) {
    if (!res.ok) {
      $('asAutoLamp').dataset.on = 'false';
      $('asAutoStateVal').textContent = 'État illisible';
      $('asAutoStateMsg').textContent = res.reason || res.error || 'Mode autonome indisponible.';
      $('asAutoState').dataset.actif = 'warn';
      if (!silencieux) handleFail(res, 'asAutoResult', 'Mode autonome');
      return;
    }
    asPeindreAuto(res.data || {});
  });
}

$('btnAsAuto').addEventListener('click', function () { asChargerAuto(false); });

/* Activation : la modale est GARDÉE ici, parce que son plan contient la
   phrase qui dit la limite du mode (« n'exécutera QUE des capacités de
   lecture »). C'est le seul endroit où le backend l'écrit ; elle doit être
   lue avant, pas découverte après. */
$('btnAsAutoStart').addEventListener('click', function () {
  destructive({
    action: 'assistant.autonomie',
    body: { etat: 'demarrer' },
    title: 'Activer la surveillance autonome',
    label: 'Mode autonome',
    lead: 'ANTI-ZEEVIRIUS va surveiller seul, dans des limites qu\'il ne peut pas franchir. ' +
          'Voici exactement ce que vous autorisez. Rien n\'est encore activé.',
    reversible: true,
    unit: 'réglage',
    countLabel: 'Changement demandé',
    planTitle: 'Ce qui change',
    execLabel: 'Activer la surveillance',
    revNote: 'Réversible à tout instant : « Arrêter immédiatement » coupe la boucle avant l\'action ' +
             'suivante, sans attendre la fin du cycle en cours. Le mode n\'exécute que des capacités ' +
             'de lecture — tout ce qui écrit sur le disque vous est proposé, jamais appliqué.',
    resultId: 'asAutoResult',
    onDone: function (data) {
      var etat = (data && data.result) || {};
      asPeindreAuto(etat);
      /* Le premier cycle part immédiatement côté backend : sans cette
         relecture, le journal resterait vide sous les yeux de l'utilisateur
         jusqu'au rafraîchissement périodique. */
      setTimeout(function () { asChargerAuto(true); }, 1500);
      showResult('asAutoResult', 'ok', 'Surveillance autonome activée',
        '<p>Un cycle toutes les ' + esc(asSecondes(etat.intervalle)) + ', au plus ' +
        num(etat.budget) + ' action(s) de lecture par cycle. Tout ce qui écrit sur le disque ' +
        'apparaîtra dans les suggestions, et n\'en sortira que par votre clic.</p>');
    }
  });
});

/* Arrêt : aucune modale. « Arrêt immédiat » veut dire immédiat — faire lire
   un plan pour couper une surveillance serait un contresens, et le plan
   lui-même n'a rien à montrer. Le cycle du contrat est respecté quand même. */
$('btnAsAutoStop').addEventListener('click', function () {
  var b = this;
  b.disabled = true;
  cycleGuarde('assistant.autonomie', { etat: 'arreter' }).then(function (res) {
    if (!res.ok) {
      b.disabled = false;
      handleFail(res, 'asAutoResult', 'Mode autonome');
      return;
    }
    var etat = (res.data && res.data.result) || {};
    asPeindreAuto(etat);
    showResult('asAutoResult', 'warn', 'Surveillance autonome arrêtée',
      '<p>La boucle est coupée. L\'arrêt a pris effet avant l\'action suivante, sans attendre la fin ' +
      'du cycle en cours. Le journal ci-dessus reste consultable.</p>');
    toast('Mode autonome', 'Surveillance arrêtée.', 'warn');
  });
});


/* ══ 7. CHARGEMENT DE LA SECTION ═════════════════════════════════════════
   Aucun appel tant que la section n'est pas ouverte : la télémétrie
   échantillonne à chaque lecture, et sonder une machine que personne ne
   regarde n'a pas de sens. Le rafraîchissement s'arrête aussi dès que
   l'onglet passe en arrière-plan.
   ════════════════════════════════════════════════════════════════════════ */
var asOuvert = false;
var asTicks = 0;

function asVisible() {
  return (location.hash || '').replace(/^#\//, '') === 'assistant' && !document.hidden;
}

function asRafraichirAssistant(tout) {
  asChargerTele(true);
  if (tout) { asChargerNotifs(); asChargerSugg(); asChargerAuto(true); }
}

function asOuvrirAssistant() {
  if (!asVisible()) return;
  if (!asOuvert) {
    asOuvert = true;
    asOublierLecture();
    asRafraichirAssistant(true);
    return;
  }
  asRafraichirAssistant(false);
}

window.addEventListener('hashchange', asOuvrirAssistant);

/* Télémétrie « en direct » : un relevé toutes les 6 s tant que la section
   est sous les yeux. Le reste (fil d'alertes, suggestions, journal du mode
   autonome) suit trois fois moins vite — ces lectures interrogent le
   système, elles ne doivent pas devenir une charge à elles seules. */
setInterval(function () {
  if (!asVisible() || !asOuvert) return;
  asTicks++;
  asChargerTele(true);
  if (asTicks % 3 === 0) { asChargerNotifs(); asChargerAuto(true); }
  if (asTicks % 6 === 0) asChargerSugg();
}, 6000);


/* ══════════════════════════════════════════════════════════════════════════
   AMORÇAGE
   ══════════════════════════════════════════════════════════════════════════ */
function boot() {
  route();                       /* le panneau demandé par l'URL, sinon le tableau de bord */
  logLine('Interface prête — liaison locale 127.0.0.1.', 'ok');
  if (!TOKEN && !DEMO) {
    toast('Jeton de session absent',
      'La page n\'a pas été servie par l\'outil : les actions seront refusées (403). Ouvrez l\'adresse affichée au démarrage.', 'danger');
  }
  /* Premier appel, avant même l'état global : un mode incident resté actif
     d'une session précédente doit être annoncé tout de suite. Un utilisateur
     avec un réseau coupé sans savoir pourquoi est un échec grave. */
  loadIncidentState(true);

  loadStatus(true).then(function () {
    if (SCENE) scene(SCENE);
  });
  /* La section Assistant ne se charge qu'à l'ouverture — y compris
     lorsque l'URL la désigne d'entrée de jeu. */
  asOuvrirAssistant();
  /* Rafraîchissement discret de l'état, sans toast ni entrée de journal. */
  setInterval(function () { if (!document.hidden) loadStatus(true); }, 45000);
}

/* Scènes de capture (?scene=...) : uniquement de l'affichage, aucune écriture.
   Elles peignent les panneaux avec un jeu de données représentatif, pour
   documenter et vérifier des situations qu'on ne peut pas provoquer sur la
   machine de développement (Windows uniquement, ou infection en cours).
   Aucun appel au backend, aucune donnée persistée. */
function scene(name) {
  if (name === 'scan') {
    setScan('running', { progress: .62, done: 1284, total: 2071,
      current: 'C:\\Users\\Public\\Downloads\\pilote_imprimante_v4.exe', threats: 2 });
  }

  if (name === 'camera') {
    renderCamera({
      acces: [
        { application: 'inconnu32.exe', appareil: 'webcam', appareil_lisible: 'caméra',
          en_cours: true, autorisee: false, debut: '2026-08-20T14:31:12' },
        { application: 'Teams.exe', appareil: 'microphone', appareil_lisible: 'microphone',
          en_cours: true, autorisee: true, debut: '2026-08-20T14:02:40' },
        { application: 'Camera.exe', appareil: 'webcam', appareil_lisible: 'caméra',
          en_cours: false, autorisee: false, debut: '2026-08-19T09:12:03', fin: '2026-08-19T09:12:09' }
      ],
      en_cours: [{}, {}],
      alertes: [{ application: 'inconnu32.exe', appareil: 'webcam', appareil_lisible: 'caméra',
                  en_cours: true, autorisee: false, debut: '2026-08-20T14:31:12' }],
      autorisees: ['Teams.exe'],
      sources: { webcam: 'ok', microphone: 'ok' },
      rappel: 'Sur la plupart des portables, la diode de la caméra est câblée sur l\'alimentation du ' +
              'capteur : si elle s\'allume, la caméra filme, quel que soit ce qu\'affiche un logiciel.'
    }, 'État actuel de la caméra et du microphone');
  }

  if (name === 'intrusion') {
    renderIntrusion({
      importants: 2, a_verifier: 2,
      constats: [
        { categorie: 'session', niveau: 'important', titre: 'Session Bureau à distance ouverte : ZEEV-ADMIN',
          detail: 'Quelqu\'un est connecté à distance sur cette machine EN CE MOMENT. Si ce n\'est pas toi, ' +
                  'c\'est le constat le plus urgent de ce rapport.' },
        { categorie: 'compte', niveau: 'important', titre: 'Compte créé récemment : support_tech',
          detail: 'La création d\'un compte est un moyen classique de garder un accès. Si tu ne l\'as pas créé, c\'est grave.' },
        { categorie: 'logiciel', niveau: 'a_verifier', titre: 'Logiciel d\'accès à distance actif : AnyDesk',
          detail: 'Ce logiciel est légitime, mais il permet à un tiers de prendre la main. L\'as-tu installé ' +
                  'toi-même, et sais-tu pourquoi il tourne ?' },
        { categorie: 'journal', niveau: 'a_verifier', titre: '48 échecs de connexion sur 7 jours',
          detail: 'Un nombre élevé d\'échecs peut trahir des tentatives répétées de deviner un mot de passe.' },
        { categorie: 'compte', niveau: 'information', titre: '3 comptes locaux actifs',
          detail: 'Passe la liste en revue : un compte que tu ne reconnais pas mérite une explication.' }
      ],
      sources: { sessions: 'ok', logiciels: 'ok', connexions: 'ok',
                 journal: 'droits insuffisants — relancer en administrateur',
                 comptes: 'PowerShell indisponible (Windows uniquement)' },
      avertissement: 'Ce rapport ne dit pas QUI, au sens d\'une personne : il donne un compte, une machine, ' +
                     'une adresse. Et il ne dit pas quels documents ont été lus — Windows ne l\'enregistre ' +
                     'pas par défaut.'
    });
  }

  if (name === 'incident-actif') {
    paintIncident({
      actif: true, depuis: '2026-08-20T14:32:07', reseau_coupe: true, nb_geles: 2,
      etape_atteinte: 'termine', regle: 'AZ_INCIDENT',
      retrait_manuel: 'netsh advfirewall firewall delete rule name=AZ_INCIDENT',
      message: 'MODE INCIDENT ACTIF depuis 2026-08-20T14:32:07 — réseau coupé (règle AZ_INCIDENT) ; ' +
               '2 processus gelé(s) : facture.exe, runner.exe. Utilise « Rétablir » pour tout remettre en place.'
    });
  }
}

if (document.readyState === 'loading') {
  document.addEventListener('DOMContentLoaded', boot);
} else {
  boot();
}


/* Fermeture de l'IIFE ouverte en tête de fichier. */
})();
