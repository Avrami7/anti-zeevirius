/* ==========================================================================
   ANTI-ZEEVIRIUS — poste de commandement
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
var VIEWS = ['tableau-de-bord', 'protection', 'nettoyage', 'rangement', 'systeme'];

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
  task_scheduler: 'Planificateur de tâches', guardian: 'Mode gardien'
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
  $('postureTags').innerHTML = tags.map(function (t) {
    return '<span class="chip" data-tone="' + t[1] + '">' + esc(t[0]) + ' · ' + esc(t[2]) + '</span>';
  }).join('');

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
   AMORÇAGE
   ══════════════════════════════════════════════════════════════════════════ */
function boot() {
  route();                       /* le panneau demandé par l'URL, sinon le tableau de bord */
  logLine('Interface prête — liaison locale 127.0.0.1.', 'ok');
  if (!TOKEN && !DEMO) {
    toast('Jeton de session absent',
      'La page n\'a pas été servie par l\'outil : les actions seront refusées (403). Ouvrez l\'adresse affichée au démarrage.', 'danger');
  }
  loadStatus(true).then(function () {
    if (SCENE) scene(SCENE);
  });
  /* Rafraîchissement discret de l'état, sans toast ni entrée de journal. */
  setInterval(function () { if (!document.hidden) loadStatus(true); }, 45000);
}

/* Scènes de capture (?scene=...) : uniquement de l'affichage, aucune écriture. */
function scene(name) {
  if (name === 'scan') {
    setScan('running', { progress: .62, done: 1284, total: 2071,
      current: 'C:\\Users\\Public\\Downloads\\pilote_imprimante_v4.exe', threats: 2 });
  }
}

if (document.readyState === 'loading') {
  document.addEventListener('DOMContentLoaded', boot);
} else {
  boot();
}


/* Fermeture de l'IIFE ouverte en tête de fichier. */
})();
