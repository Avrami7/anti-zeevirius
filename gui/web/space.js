/* ==========================================================================
   ANTI-ZEEVIRIUS — scène spatiale de fond
   Canvas 2D pur. Aucune dépendance, aucune ressource externe, aucun appel
   réseau : l'outil doit rester utilisable sur une machine hors ligne.

   Couches, de l'arrière vers l'avant :
     1. fond spatial + aurore boréale (rubans lents)
     2. champ d'étoiles à parallaxe, avec scintillement
     3. planètes lointaines (dérive très lente, une à anneau)
     4. astéroïdes (rotation propre, déviés par la gravité du trou noir)
     5. étoiles filantes (occasionnelles)
     6. trou noir : disque d'accrétion en rotation + arc de lentille
                    gravitationnelle — la marque du produit, en mouvement.

   Contraintes de sobriété, volontaires :
     - opacité globale basse : le fond ne doit JAMAIS gêner la lecture de
       l'interface posée par-dessus ;
     - animation suspendue dès que l'onglet passe en arrière-plan (inutile
       de consommer du CPU sur un outil de sécurité qui tourne en fond) ;
     - `prefers-reduced-motion` : une image fixe est rendue, puis plus rien.
   ========================================================================== */
(function () {
'use strict';

var canvas = document.getElementById('spaceCanvas');
if (!canvas || !canvas.getContext) return;
var ctx = canvas.getContext('2d');

var REDUCED = window.matchMedia('(prefers-reduced-motion: reduce)').matches;

/* ── Palette (alignée sur celle du logo et de app.css) ──────────────────── */
var C = {
  void0:   '#040306',
  void1:   '#0a0710',
  hotCore: '#fff6dd',
  hot:     '#ffd9a0',
  amber:   '#f5871c',
  ember:   '#b8380a',
  aurora1: 'rgba(64,196,168,',   // vert-turquoise
  aurora2: 'rgba(120,132,236,',  // violet froid
  aurora3: 'rgba(238,140,52,'    // rappel ambré, lie l'aurore à la charte
};

var W = 0, H = 0, DPR = 1;

/* Aurore : rendue sur un canevas hors-écran en quart de résolution, puis
   agrandie. Mesuré : appliquer ctx.filter='blur()' sur la pleine page à
   chaque image coûtait 65 % du budget (13,7 FPS contre 39,2 sans). En quart
   de résolution le flou porte sur 16 fois moins de pixels, et l'agrandissement
   lisse le reste sans rien coûter. */
var AURORA_SCALE = 0.30;
var auroraCv = document.createElement('canvas');
var auroraCtx = auroraCv.getContext('2d');
var stars = [], planets = [], rocks = [], shooters = [], auroras = [];
var hole = { x: 0, y: 0, r: 0 };
var t0 = performance.now();
var raf = null, running = false;

/* ── Utilitaires ────────────────────────────────────────────────────────── */
function rnd(a, b) { return a + Math.random() * (b - a); }
function rndInt(a, b) { return Math.floor(rnd(a, b + 1)); }

/* ── Construction de la scène (redimensionnable) ────────────────────────── */
function build() {
  var rect = canvas.getBoundingClientRect();
  DPR = Math.min(window.devicePixelRatio || 1, 2);   // plafonné : au-delà, coût pur
  W = Math.max(1, Math.floor(rect.width));
  H = Math.max(1, Math.floor(rect.height));
  canvas.width = Math.floor(W * DPR);
  canvas.height = Math.floor(H * DPR);
  ctx.setTransform(DPR, 0, 0, DPR, 0, 0);

  auroraCv.width = Math.max(1, Math.round(W * AURORA_SCALE));
  auroraCv.height = Math.max(1, Math.round(H * AURORA_SCALE));

  // Le trou noir est décentré : il doit rester lisible sans se placer
  // derrière la colonne de contenu principale.
  hole.x = W * 0.76;
  hole.y = H * 0.34;
  hole.r = Math.max(46, Math.min(W, H) * 0.085);

  // Densité proportionnelle à la surface, bornée pour rester peu coûteuse.
  var area = W * H;
  var nStars = Math.round(Math.min(340, Math.max(90, area / 5200)));

  stars = [];
  for (var i = 0; i < nStars; i++) {
    var depth = Math.random();                       // 0 = lointain, 1 = proche
    stars.push({
      x: Math.random() * W,
      y: Math.random() * H,
      r: 0.35 + depth * 1.15,
      depth: depth,
      base: 0.20 + depth * 0.55,
      // Scintillement : phase et vitesse propres, sinon tout clignote ensemble.
      phase: Math.random() * Math.PI * 2,
      speed: rnd(0.4, 1.7),
      warm: Math.random() < 0.22                     // quelques étoiles chaudes
    });
  }

  auroras = [
    { y: H * 0.14, amp: H * 0.055, len: 0.0021, sp: 0.10, th: H * 0.16, col: C.aurora1, a: 0.085 },
    { y: H * 0.24, amp: H * 0.075, len: 0.0015, sp: -0.07, th: H * 0.20, col: C.aurora2, a: 0.070 },
    { y: H * 0.09, amp: H * 0.040, len: 0.0028, sp: 0.14, th: H * 0.11, col: C.aurora3, a: 0.045 }
  ];

  planets = [
    { x: W * 0.13, y: H * 0.72, r: Math.min(W, H) * 0.052, hue: '#3b2f4d', lit: '#8a6fb0',
      ring: true,  ringTilt: -0.42, vx: 0.0035, vy: -0.0012 },
    { x: W * 0.62, y: H * 0.86, r: Math.min(W, H) * 0.030, hue: '#4a2a20', lit: '#c87a45',
      ring: false, ringTilt: 0,    vx: -0.0025, vy: -0.0008 },
    { x: W * 0.34, y: H * 0.12, r: Math.min(W, H) * 0.018, hue: '#243a44', lit: '#5fa5b8',
      ring: false, ringTilt: 0,    vx: 0.0018, vy: 0.0010 }
  ];

  rocks = [];
  for (var k = 0; k < 14; k++) rocks.push(newRock(true));

  shooters = [];
}

/* ── Astéroïdes ─────────────────────────────────────────────────────────── */
function newRock(anywhere) {
  var size = rnd(3, 9);
  var verts = [];
  var n = rndInt(6, 9);
  for (var i = 0; i < n; i++) {
    var a = (i / n) * Math.PI * 2;
    var rr = size * rnd(0.62, 1.0);                  // silhouette irrégulière
    verts.push([Math.cos(a) * rr, Math.sin(a) * rr]);
  }
  return {
    x: anywhere ? Math.random() * W : -20,
    y: anywhere ? Math.random() * H : Math.random() * H,
    vx: rnd(0.10, 0.34),
    vy: rnd(-0.05, 0.05),
    spin: rnd(-0.006, 0.006),
    rot: Math.random() * Math.PI * 2,
    size: size,
    verts: verts,
    a: rnd(0.20, 0.5)
  };
}

/* ── Étoiles filantes ───────────────────────────────────────────────────── */
function newShooter() {
  var fromTop = Math.random() < 0.75;
  var ang = rnd(0.28, 0.52);                         // trajectoire descendante
  return {
    x: fromTop ? rnd(-W * 0.1, W * 0.85) : -40,
    y: fromTop ? rnd(-40, H * 0.35) : rnd(0, H * 0.5),
    vx: Math.cos(ang) * rnd(6.5, 11),
    vy: Math.sin(ang) * rnd(6.5, 11),
    life: 0,
    max: rnd(52, 88),
    len: rnd(80, 190),
    a: rnd(0.55, 0.95)
  };
}

/* ── Rendu : fond et aurore ─────────────────────────────────────────────── */
function drawBackdrop(tt) {
  var g = ctx.createLinearGradient(0, 0, W * 0.35, H);
  g.addColorStop(0, C.void1);
  g.addColorStop(1, C.void0);
  ctx.fillStyle = g;
  ctx.fillRect(0, 0, W, H);

  // Aurore : rubans sinusoïdaux superposés, dégradés verticalement pour
  // s'éteindre par le haut et par le bas (pas de bord net).
  var k = AURORA_SCALE;
  var actx = auroraCtx;
  actx.setTransform(1, 0, 0, 1, 0, 0);
  actx.clearRect(0, 0, auroraCv.width, auroraCv.height);
  actx.save();
  actx.globalCompositeOperation = 'lighter';
  // Flou léger EN COORDONNÉES RÉDUITES : 6 px ici valent ~24 px une fois
  // le canevas agrandi. Une aurore n'a pas de contour net.
  if (actx.filter !== undefined) actx.filter = 'blur(7px)';
  for (var i = 0; i < auroras.length; i++) {
    var A = auroras[i];
    // La plage du dégradé doit couvrir l'épaisseur du ruban ET toute
    // l'amplitude de son ondulation : sinon le ruban est tranché net là
    // où l'onde sort de la plage (bord droit très visible).
    var span = (A.th + A.amp * 1.9) * k;
    var grd = actx.createLinearGradient(0, (A.y * k) - span, 0, (A.y * k) + span);
    grd.addColorStop(0.0, A.col + '0)');
    grd.addColorStop(0.5, A.col + A.a + ')');
    grd.addColorStop(1.0, A.col + '0)');
    actx.fillStyle = grd;
    actx.beginPath();
    actx.moveTo(0, (A.y - A.th) * k);
    var step = Math.max(8, W / 90);
    for (var x = 0; x <= W + step; x += step) {
      var y = A.y
            + Math.sin(x * A.len + tt * A.sp) * A.amp
            + Math.sin(x * A.len * 2.3 + tt * A.sp * 1.7) * A.amp * 0.32;
      actx.lineTo(x * k, y * k);
    }
    for (var x2 = W + step; x2 >= 0; x2 -= step) {
      var y2 = A.y
             + Math.sin(x2 * A.len + tt * A.sp) * A.amp
             + Math.sin(x2 * A.len * 2.3 + tt * A.sp * 1.7) * A.amp * 0.32;
      actx.lineTo(x2 * k, (y2 + A.th) * k);
    }
    actx.closePath();
    actx.fill();
  }
  actx.restore();

  ctx.save();
  ctx.globalCompositeOperation = 'lighter';
  ctx.drawImage(auroraCv, 0, 0, W, H);
  ctx.restore();
}

/* ── Rendu : étoiles ────────────────────────────────────────────────────── */
function drawStars(tt) {
  for (var i = 0; i < stars.length; i++) {
    var s = stars[i];
    var tw = 0.72 + 0.28 * Math.sin(tt * s.speed + s.phase);
    ctx.globalAlpha = s.base * tw;
    ctx.fillStyle = s.warm ? C.hot : '#dfe8ff';
    ctx.beginPath();
    ctx.arc(s.x, s.y, s.r, 0, Math.PI * 2);
    ctx.fill();
  }
  ctx.globalAlpha = 1;
}

/* ── Rendu : planètes ───────────────────────────────────────────────────── */
function drawPlanets() {
  for (var i = 0; i < planets.length; i++) {
    var p = planets[i];

    if (p.ring) {                                     // anneau : moitié arrière
      ctx.save();
      ctx.translate(p.x, p.y);
      ctx.rotate(p.ringTilt);
      ctx.globalAlpha = 0.30;
      ctx.strokeStyle = p.lit;
      ctx.lineWidth = Math.max(1.4, p.r * 0.10);
      ctx.beginPath();
      ctx.ellipse(0, 0, p.r * 1.85, p.r * 0.44, 0, Math.PI, Math.PI * 2);
      ctx.stroke();
      ctx.restore();
    }

    // Sphère : dégradé décalé = éclairage rasant, donne le volume.
    var g = ctx.createRadialGradient(
      p.x - p.r * 0.42, p.y - p.r * 0.42, p.r * 0.08,
      p.x, p.y, p.r
    );
    g.addColorStop(0, p.lit);
    g.addColorStop(0.55, p.hue);
    g.addColorStop(1, '#050409');
    ctx.globalAlpha = 0.62;
    ctx.fillStyle = g;
    ctx.beginPath();
    ctx.arc(p.x, p.y, p.r, 0, Math.PI * 2);
    ctx.fill();

    if (p.ring) {                                     // moitié avant de l'anneau
      ctx.save();
      ctx.translate(p.x, p.y);
      ctx.rotate(p.ringTilt);
      ctx.globalAlpha = 0.42;
      ctx.strokeStyle = p.lit;
      ctx.lineWidth = Math.max(1.4, p.r * 0.10);
      ctx.beginPath();
      ctx.ellipse(0, 0, p.r * 1.85, p.r * 0.44, 0, 0, Math.PI);
      ctx.stroke();
      ctx.restore();
    }
    ctx.globalAlpha = 1;
  }
}

/* ── Rendu : astéroïdes ─────────────────────────────────────────────────── */
function drawRocks() {
  for (var i = 0; i < rocks.length; i++) {
    var r = rocks[i];
    ctx.save();
    ctx.translate(r.x, r.y);
    ctx.rotate(r.rot);
    ctx.globalAlpha = r.a;
    ctx.fillStyle = '#2a2230';
    ctx.strokeStyle = 'rgba(255,214,170,.22)';
    ctx.lineWidth = 1;
    ctx.beginPath();
    ctx.moveTo(r.verts[0][0], r.verts[0][1]);
    for (var v = 1; v < r.verts.length; v++) ctx.lineTo(r.verts[v][0], r.verts[v][1]);
    ctx.closePath();
    ctx.fill();
    ctx.stroke();
    ctx.restore();
  }
  ctx.globalAlpha = 1;
}

/* ── Rendu : étoiles filantes ───────────────────────────────────────────── */
function drawShooters() {
  for (var i = 0; i < shooters.length; i++) {
    var s = shooters[i];
    var k = s.life / s.max;
    var fade = k < 0.18 ? k / 0.18 : (1 - (k - 0.18) / 0.82);   // apparition/extinction
    if (fade <= 0) continue;

    var n = Math.hypot(s.vx, s.vy) || 1;
    var tx = s.x - (s.vx / n) * s.len;
    var ty = s.y - (s.vy / n) * s.len;

    var g = ctx.createLinearGradient(s.x, s.y, tx, ty);
    g.addColorStop(0, 'rgba(255,246,221,' + (s.a * fade) + ')');
    g.addColorStop(0.35, 'rgba(255,190,120,' + (s.a * fade * 0.45) + ')');
    g.addColorStop(1, 'rgba(255,150,60,0)');
    ctx.strokeStyle = g;
    ctx.lineWidth = 1.9;
    ctx.lineCap = 'round';
    ctx.beginPath();
    ctx.moveTo(s.x, s.y);
    ctx.lineTo(tx, ty);
    ctx.stroke();

    ctx.globalAlpha = fade;                            // tête plus vive
    ctx.fillStyle = C.hotCore;
    ctx.beginPath();
    ctx.arc(s.x, s.y, 1.7, 0, Math.PI * 2);
    ctx.fill();
    ctx.globalAlpha = 1;
  }
}

/* ── Rendu : le trou noir ───────────────────────────────────────────────── */
function drawHole(tt) {
  var x = hole.x, y = hole.y, r = hole.r;

  // Halo chaud diffus
  var halo = ctx.createRadialGradient(x, y, r * 0.6, x, y, r * 4.2);
  halo.addColorStop(0, 'rgba(255,150,60,.20)');
  halo.addColorStop(0.45, 'rgba(230,100,25,.075)');
  halo.addColorStop(1, 'rgba(180,60,10,0)');
  ctx.fillStyle = halo;
  ctx.beginPath();
  ctx.arc(x, y, r * 4.2, 0, Math.PI * 2);
  ctx.fill();

  // Arc de lentille gravitationnelle : la face arrière du disque, courbée
  // au-dessus de l'horizon — la signature visuelle d'un trou noir.
  ctx.save();
  ctx.translate(x, y);
  var arc = ctx.createLinearGradient(-r * 2.3, 0, r * 2.3, 0);
  arc.addColorStop(0, 'rgba(184,56,10,.42)');
  arc.addColorStop(0.5, 'rgba(255,230,180,.92)');
  arc.addColorStop(1, 'rgba(184,56,10,.42)');
  ctx.strokeStyle = arc;
  ctx.lineWidth = Math.max(1.8, r * 0.085);   // plus fin que le disque, comme au logo
  ctx.lineCap = 'round';
  ctx.beginPath();
  // Proportions reprises du logo (viewBox 64) : disque rx=27, horizon r=12,
  // arc ry=17. Rapportées à r, cela donne rx=2.25r et ry=1.42r. L'arc doit
  // culminer AU-DESSUS de l'horizon : plus bas, l'ensemble se lit comme une
  // boule dans un cerceau au lieu d'une lentille gravitationnelle.
  ctx.ellipse(0, r * 0.10, r * 2.28, r * 1.42, 0, Math.PI, Math.PI * 2);
  ctx.stroke();
  ctx.restore();

  // Disque d'accrétion : plusieurs ellipses fines, décalées en phase, dont
  // l'opacité pulse — c'est ce qui donne la rotation de la matière.
  ctx.save();
  ctx.translate(x, y + r * 0.10);
  for (var i = 0; i < 7; i++) {
    var ph = tt * 0.55 + i * 0.9;
    var puls = 0.55 + 0.45 * Math.sin(ph);
    var rx = r * (2.30 - i * 0.055);
    var ry = r * (0.46 - i * 0.012);
    var g = ctx.createLinearGradient(-rx, 0, rx, 0);
    g.addColorStop(0, 'rgba(184,56,10,' + (0.30 * puls) + ')');
    g.addColorStop(0.20, 'rgba(242,129,27,' + (0.72 * puls) + ')');
    g.addColorStop(0.50, 'rgba(255,246,221,' + (0.95 * puls) + ')');
    g.addColorStop(0.80, 'rgba(245,135,28,' + (0.72 * puls) + ')');
    g.addColorStop(1, 'rgba(184,56,10,' + (0.30 * puls) + ')');
    ctx.strokeStyle = g;
    ctx.lineWidth = Math.max(1.1, r * 0.055);
    ctx.beginPath();
    ctx.ellipse(0, 0, rx, ry, 0, 0, Math.PI * 2);
    ctx.stroke();
  }
  ctx.restore();

  // Horizon des événements : noir opaque, il masque le disque qui passe derrière.
  ctx.fillStyle = '#000';
  ctx.beginPath();
  ctx.arc(x, y, r, 0, Math.PI * 2);
  ctx.fill();

  // Liseré incandescent au bord de l'horizon
  ctx.strokeStyle = 'rgba(255,220,166,' + (0.34 + 0.14 * Math.sin(tt * 1.1)) + ')';
  ctx.lineWidth = 1.1;
  ctx.beginPath();
  ctx.arc(x, y, r * 1.035, 0, Math.PI * 2);
  ctx.stroke();

  // Moitié avant du disque : redessinée PAR-DESSUS l'horizon, sinon le trou
  // noir paraîtrait posé devant son propre disque.
  ctx.save();
  ctx.translate(x, y + r * 0.10);
  for (var j = 0; j < 5; j++) {
    var ph2 = tt * 0.55 + j * 0.9;
    var puls2 = 0.55 + 0.45 * Math.sin(ph2);
    var rx2 = r * (2.30 - j * 0.055);
    var ry2 = r * (0.46 - j * 0.012);
    var g2 = ctx.createLinearGradient(-rx2, 0, rx2, 0);
    g2.addColorStop(0, 'rgba(184,56,10,' + (0.34 * puls2) + ')');
    g2.addColorStop(0.5, 'rgba(255,246,221,' + (0.95 * puls2) + ')');
    g2.addColorStop(1, 'rgba(184,56,10,' + (0.34 * puls2) + ')');
    ctx.strokeStyle = g2;
    ctx.lineWidth = Math.max(1.1, r * 0.055);
    ctx.beginPath();
    ctx.ellipse(0, 0, rx2, ry2, 0, 0, Math.PI);
    ctx.stroke();
  }
  ctx.restore();
}

/* ── Physique légère ────────────────────────────────────────────────────── */
function step(dt) {
  var i;

  for (i = 0; i < planets.length; i++) {              // dérive quasi imperceptible
    var p = planets[i];
    p.x += p.vx * dt; p.y += p.vy * dt;
    if (p.x < -p.r * 2) p.x = W + p.r * 2;
    if (p.x > W + p.r * 2) p.x = -p.r * 2;
    if (p.y < -p.r * 2) p.y = H + p.r * 2;
    if (p.y > H + p.r * 2) p.y = -p.r * 2;
  }

  for (i = 0; i < rocks.length; i++) {
    var r = rocks[i];
    // Attraction vers le trou noir : la métaphore du produit, dosée pour
    // rester une inflexion de trajectoire, pas une chute spectaculaire.
    var dx = hole.x - r.x, dy = hole.y - r.y;
    var d2 = dx * dx + dy * dy;
    var d = Math.sqrt(d2) || 1;
    if (d < hole.r * 9) {
      var pull = Math.min(0.030, 260 / d2);
      r.vx += (dx / d) * pull * dt;
      r.vy += (dy / d) * pull * dt;
    }
    r.x += r.vx * dt;
    r.y += r.vy * dt;
    r.rot += r.spin * dt;

    // Happé par l'horizon, ou sorti du cadre → réapparaît par la gauche.
    if (d < hole.r * 0.92 || r.x > W + 40 || r.y < -40 || r.y > H + 40) {
      rocks[i] = newRock(false);
    }
  }

  for (i = shooters.length - 1; i >= 0; i--) {
    var s = shooters[i];
    s.x += s.vx * dt; s.y += s.vy * dt; s.life += dt;
    if (s.life > s.max || s.x > W + 260 || s.y > H + 260) shooters.splice(i, 1);
  }
  // Apparition aléatoire, rare : une étoile filante attendue vaut mieux
  // qu'une pluie continue qui banaliserait l'effet.
  if (shooters.length < 2 && Math.random() < 0.0055 * dt) shooters.push(newShooter());
}

/* ── Boucle ─────────────────────────────────────────────────────────────── */
function frame(now) {
  var tt = (now - t0) / 1000;
  var dt = Math.min(3, (now - (frame._last || now)) / 16.667);   // borne les à-coups
  frame._last = now;

  step(dt);
  render(tt);

  if (running) raf = requestAnimationFrame(frame);
}

function render(tt) {
  ctx.clearRect(0, 0, W, H);
  drawBackdrop(tt);
  drawStars(tt);
  drawPlanets();
  drawRocks();
  drawHole(tt);
  drawShooters();
}

function start() {
  if (running || REDUCED) return;
  running = true;
  frame._last = performance.now();
  raf = requestAnimationFrame(frame);
}

function stop() {
  running = false;
  if (raf) cancelAnimationFrame(raf);
  raf = null;
}

/* ── Cycle de vie ───────────────────────────────────────────────────────── */
var resizeTimer = null;
window.addEventListener('resize', function () {
  clearTimeout(resizeTimer);
  resizeTimer = setTimeout(function () {
    build();
    if (REDUCED) render(0);
  }, 160);
});

// Onglet masqué : on suspend. Un fond animé n'a aucune raison de consommer
// du CPU pendant qu'un scan tourne dans une autre fenêtre.
document.addEventListener('visibilitychange', function () {
  if (document.hidden) stop();
  else start();
});

build();
if (REDUCED) render(0);          // image fixe : la scène existe, elle ne bouge pas
else start();

})();
