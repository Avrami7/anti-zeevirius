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
  aurora3: 'rgba(238,140,52,',   // rappel ambré, lie l'aurore à la charte
  mwPale:  '212,226,255',        // bleu-blanc des populations stellaires jeunes
  mwWarm:  '255,226,186',        // coeur galactique, plus chaud
  mwDust:  '10,6,14'             // bande d'absorption : de la poussière, pas du vide
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

/* Voie lactée : STATIQUE, donc dessinée UNE SEULE FOIS par redimensionnement
   sur son propre canevas, puis recopiée telle quelle à chaque image. Une
   galaxie ne bouge pas à l'échelle d'une session, et recalculer ses ~1100
   étoiles à 60 images par seconde coûterait plus cher que tout le reste de la
   scène réuni. Demi-résolution : la bande est diffuse, personne ne verra la
   différence, et l'agrandissement lisse le grain plutôt que de le révéler. */
var MW_SCALE = 0.5;
var mwCv = document.createElement('canvas');
var mwCtx = mwCv.getContext('2d');
/* La marque, chargée une seule fois. `drawHole` ne dessine rien tant qu'elle
   n'est pas prête : mieux vaut un halo seul pendant deux images qu'un second
   trou noir qui ne ressemble pas au premier. */
var holeImg = new Image();
holeImg.src = 'logo-trou-noir.webp';

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
  buildMilkyWay();

  // Le trou noir est décentré, et il l'est plus qu'avant. À 0,76 de la largeur
  // il se plaçait DERRIÈRE le bandeau d'état du tableau de bord : les pastilles
  // « Réputation cloud » et consorts s'y délavaient. L'assombrir ne suffisait
  // pas — c'est sa POSITION qui était mauvaise. Un fond illustre, il ne dispute
  // jamais la lisibilité au contenu.
  /* Placement : dans la BANDE OUVERTE, a droite du titre de page — le seul
     endroit du tableau de bord ou aucun panneau ne le recouvre.
     Mesure a l'appui : place a 0,30 de la hauteur, l'anneau tombait derriere le
     panneau hero, dont le `backdrop-filter: blur(18px)` le reduisait a une
     tache sombre. Un fond anime cache derriere une vitre depolie n'est plus un
     fond anime. */
  hole.x = W * 0.80;
  hole.y = H * 0.155;
  hole.r = Math.max(34, Math.min(W, H) * 0.062);   // l'anneau vaut 1,34 r : a 0,085 il mangeait le panneau

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

/* ── La Voie lactée ─────────────────────────────────────────────────────────
   Trois couches, dans l'ordre où l'œil les lit :

   1. la LUEUR diffuse de la bande, en dégradé perpendiculaire à son axe ;
   2. la BANDE DE POUSSIÈRE qui la coupe en deux sur presque toute sa
      longueur — c'est ce détail qui fait reconnaître la Voie lactée plutôt
      qu'un simple nuage lumineux, et c'est celui qu'on oublie ;
   3. les ÉTOILES, réparties en loi normale autour de l'axe : une répartition
      uniforme donnerait un rectangle d'étoiles, pas un disque vu par la
      tranche. La densité décroît donc en s'éloignant de l'axe.

   L'axe est incliné et décalé vers le bas pour passer SOUS la colonne de
   contenu : une bande lumineuse derrière du texte le rend illisible.
*/
function buildMilkyWay() {
  var k = MW_SCALE;
  mwCv.width = Math.max(1, Math.round(W * k));
  mwCv.height = Math.max(1, Math.round(H * k));
  var g = mwCtx;
  g.setTransform(1, 0, 0, 1, 0, 0);
  g.clearRect(0, 0, mwCv.width, mwCv.height);

  var ANGLE = -0.38;                 // ~22 degrés, montant vers la droite
  var cx = W * 0.42 * k, cy = H * 0.70 * k;
  var longueur = Math.hypot(W, H) * 1.25 * k;
  var epaisseur = Math.min(W, H) * 0.30 * k;

  g.save();
  g.translate(cx, cy);
  g.rotate(ANGLE);

  // 1. Lueur : dégradé perpendiculaire, éteint aux deux bords pour qu'aucune
  //    arête n'apparaisse.
  var lueur = g.createLinearGradient(0, -epaisseur, 0, epaisseur);
  lueur.addColorStop(0.00, 'rgba(' + C.mwPale + ',0)');
  lueur.addColorStop(0.30, 'rgba(' + C.mwPale + ',0.030)');
  lueur.addColorStop(0.50, 'rgba(' + C.mwWarm + ',0.055)');
  lueur.addColorStop(0.70, 'rgba(' + C.mwPale + ',0.030)');
  lueur.addColorStop(1.00, 'rgba(' + C.mwPale + ',0)');
  g.fillStyle = lueur;
  g.fillRect(-longueur / 2, -epaisseur, longueur, epaisseur * 2);

  // Renflement central : le bulbe galactique. Sans lui la bande est un ruban
  // d'épaisseur constante, ce qui ne ressemble à aucune galaxie.
  var bulbe = g.createRadialGradient(0, 0, 0, 0, 0, epaisseur * 1.5);
  bulbe.addColorStop(0.00, 'rgba(' + C.mwWarm + ',0.075)');
  bulbe.addColorStop(0.55, 'rgba(' + C.mwWarm + ',0.028)');
  bulbe.addColorStop(1.00, 'rgba(' + C.mwWarm + ',0)');
  g.fillStyle = bulbe;
  g.fillRect(-epaisseur * 1.5, -epaisseur * 1.5, epaisseur * 3, epaisseur * 3);

  // 2. Bande de poussière : elle ABSORBE, donc elle s'écrit en 'source-over'
  //    par-dessus la lueur, et ondule légèrement — une ligne droite ferait
  //    mécanique.
  g.globalCompositeOperation = 'source-over';
  var pas = Math.max(6, longueur / 60);
  var epD = epaisseur * 0.22;
  var dust = g.createLinearGradient(0, -epD * 2, 0, epD * 2);
  dust.addColorStop(0.00, 'rgba(' + C.mwDust + ',0)');
  dust.addColorStop(0.50, 'rgba(' + C.mwDust + ',0.55)');
  dust.addColorStop(1.00, 'rgba(' + C.mwDust + ',0)');
  g.fillStyle = dust;
  g.beginPath();
  g.moveTo(-longueur / 2, 0);
  for (var x = -longueur / 2; x <= longueur / 2 + pas; x += pas) {
    g.lineTo(x, Math.sin(x * 0.004) * epD * 0.7 - epD);
  }
  for (var x2 = longueur / 2 + pas; x2 >= -longueur / 2; x2 -= pas) {
    g.lineTo(x2, Math.sin(x2 * 0.004) * epD * 0.7 + epD);
  }
  g.closePath();
  g.fill();

  // 3. Étoiles. La somme de deux tirages uniformes approche une loi normale
  //    (théorème central limite, ordre 2) : assez pour concentrer la densité
  //    sur l'axe sans le coût d'un vrai tirage gaussien.
  var n = Math.round(Math.min(1100, Math.max(260, W * H / 1700)));
  for (var i = 0; i < n; i++) {
    var u = (Math.random() + Math.random() - 1);
    var px = (Math.random() - 0.5) * longueur;
    var py = u * epaisseur * 0.85;
    // Plus on s'éloigne de l'axe, plus l'étoile est faible : la bande se fond
    // dans le ciel au lieu de s'y découper.
    var att = 1 - Math.min(1, Math.abs(py) / (epaisseur * 0.85));
    var a = (0.10 + Math.random() * 0.42) * (0.25 + att * 0.75);
    // Les étoiles les plus proches du bulbe tirent vers le chaud.
    var chaude = Math.abs(px) < epaisseur * 1.6 && Math.random() < 0.45;
    g.fillStyle = 'rgba(' + (chaude ? C.mwWarm : C.mwPale) + ',' + a.toFixed(3) + ')';
    var r = (0.22 + Math.random() * 0.62) * k;
    g.beginPath();
    g.arc(px, py, r, 0, Math.PI * 2);
    g.fill();
  }
  g.restore();
}

/* ── Rendu : fond et aurore ─────────────────────────────────────────────── */
function drawBackdrop(tt) {
  var g = ctx.createLinearGradient(0, 0, W * 0.35, H);
  g.addColorStop(0, C.void1);
  g.addColorStop(1, C.void0);
  ctx.fillStyle = g;
  ctx.fillRect(0, 0, W, H);

  // Voie lactée : une seule recopie, la bande est pré-calculée. 'lighter' pour
  // qu'elle s'ajoute au fond sans l'occulter — de la lumière, pas un calque.
  ctx.save();
  ctx.globalCompositeOperation = 'lighter';
  ctx.drawImage(mwCv, 0, 0, W, H);
  ctx.restore();

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

  /* ── L'ANNEAU, vu PAR LA TRANCHE ─────────────────────────────────────────
     Le propriétaire a tranché : la photographie reste la MARQUE — en-tête,
     jauge, icône, favicon — et le fond porte cet anneau animé. Les deux ne
     montrent pas le même angle de vue, et c'est voulu : un anneau FERMÉ exige
     la vue par la tranche, une vue de trois quarts ne peut pas le produire.

     Ce qui ferme le cercle est la lentille gravitationnelle : elle relève le
     disque par-dessus le sommet ET le rabat sous la base, et les deux arcs se
     rejoignent. C'est la signature de ce point de vue, et c'est exactement ce
     que la référence montre.

     Le mouvement demandé est la CIRCULATION de la lumière le long de
     l'anneau : des zones plus vives qui tournent, et non un clignotement
     d'ensemble. Une pulsation uniforme se lit comme une ampoule ; c'est le
     différentiel qui se lit comme de la matière en orbite.

     Ordre de tracé, qui est l'optique du sujet et non une commodité :
       voile → arrière du disque → moitié arrière de l'anneau → SPHÈRE NOIRE
       → moitié avant de l'anneau → avant du disque qui passe devant. */

  var halo = ctx.createRadialGradient(x, y, r * 0.55, x, y, r * 4.6);
  halo.addColorStop(0, 'rgba(255,170,80,.17)');
  halo.addColorStop(0.42, 'rgba(230,110,30,.065)');
  halo.addColorStop(1, 'rgba(180,60,10,0)');
  ctx.fillStyle = halo;
  ctx.beginPath(); ctx.arc(x, y, r * 4.6, 0, Math.PI * 2); ctx.fill();

  var A = 0.40;                    // discrétion : ce fond passe derrière du texte
  var rr = r * 1.34;               // rayon de l'anneau de photons
  var vitesse = tt * 1.45;         // rotation de la matière

  ctx.save();
  ctx.translate(x, y);
  ctx.globalAlpha = A;

  /* La barre du disque, vue par la tranche : elle file très loin de part et
     d'autre et s'éteint en pointe. C'est elle qui donne l'échelle. */
  function barre(largeur, opac) {
    var g = ctx.createLinearGradient(-r * 4.2, 0, r * 4.2, 0);
    g.addColorStop(0.00, 'rgba(210,90,20,0)');
    g.addColorStop(0.18, 'rgba(255,170,80,' + (opac * 0.55).toFixed(3) + ')');
    g.addColorStop(0.42, 'rgba(255,240,205,' + opac.toFixed(3) + ')');
    g.addColorStop(0.58, 'rgba(255,240,205,' + opac.toFixed(3) + ')');
    g.addColorStop(0.82, 'rgba(255,170,80,' + (opac * 0.55).toFixed(3) + ')');
    g.addColorStop(1.00, 'rgba(210,90,20,0)');
    ctx.fillStyle = g;
    ctx.fillRect(-r * 4.2, -largeur / 2, r * 8.4, largeur);
  }
  barre(r * 0.30, 0.30);           // le voile large
  barre(r * 0.085, 0.85);          // le cœur mince

  /* L'anneau, tracé en segments dont la vivacité suit une onde qui tourne.
     60 segments : en dessous, on voit les raccords ; au-dessus, on paie sans
     rien gagner. Deux ondes de vitesses différentes pour que le motif ne se
     répète pas de façon mécanique. */
  function anneau(demi) {           // demi = 'haut' (derrière) ou 'bas' (devant)
    var n = 60, d0 = demi === 'haut' ? Math.PI : 0;
    for (var i = 0; i < n; i++) {
      var a0 = d0 + (i / n) * Math.PI, a1 = d0 + ((i + 1.15) / n) * Math.PI;
      // Amplitude franche : avec une onde molle, l'anneau brille d'un bloc et
      // la circulation ne se voit pas. Mesure a l'appui — l'ecart entre deux
      // instants etait de 1,5 sur 255, soit rien.
      var onde = 0.30
               + 0.62 * Math.sin(a0 * 2 - vitesse)
               + 0.22 * Math.sin(a0 * 5 + vitesse * 2.3);
      // Le bas est plus vif que le haut : l'avant du disque vient vers nous.
      var f = onde * (demi === 'bas' ? 1 : 0.68);
      ctx.strokeStyle = 'rgba(255,' + Math.round(235 - 40 * (1 - f)) + ',' +
                        Math.round(198 - 70 * (1 - f)) + ',' + Math.max(0, f).toFixed(3) + ')';
      ctx.lineWidth = r * (0.055 + 0.085 * f);   // l'epaisseur suit la vivacite
      ctx.lineCap = 'round';
      ctx.beginPath();
      ctx.ellipse(0, 0, rr, rr * 0.99, 0, a0, a1);
      ctx.stroke();
    }
  }

  anneau('haut');                   // l'arrière, relevé par la lentille

  /* La sphère : noir pur, elle occulte tout ce qui passe derrière elle. */
  ctx.globalAlpha = 1;
  ctx.fillStyle = '#000';
  ctx.beginPath(); ctx.arc(0, 0, rr * 0.955, 0, Math.PI * 2); ctx.fill();
  ctx.globalAlpha = A;

  anneau('bas');                    // l'avant, plus vif

  /* L'avant du disque repasse DEVANT la sphère : c'est ce trait qui empêche
     l'ensemble de se lire comme un simple cerceau posé autour d'une bille. */
  var av = ctx.createLinearGradient(-rr, 0, rr, 0);
  av.addColorStop(0.00, 'rgba(255,200,130,.35)');
  av.addColorStop(0.50, 'rgba(255,250,235,.95)');
  av.addColorStop(1.00, 'rgba(255,200,130,.35)');
  ctx.strokeStyle = av;
  ctx.lineWidth = r * 0.075;
  ctx.beginPath();
  ctx.moveTo(-rr * 0.99, r * 0.02);
  ctx.lineTo(rr * 0.99, r * 0.02);
  ctx.stroke();

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
