"""
packaging/make_icon.py — fabrique l'icône Windows d'ANTI-ZEEVIRIUS.

L'identité du projet est un **trou noir**, d'après la référence fournie par le
propriétaire du projet : une sphère parfaitement opaque, un disque d'accrétion
vu par la tranche dont l'envergure dépasse largement la sphère et qui s'effile
en pointes ambrées, un arc de lentille gravitationnelle qui ramène l'arrière du
disque au-dessus de la sphère, et surtout une bande incandescente qui traverse
la sphère en son tiers inférieur — l'avant du disque passant DEVANT l'horizon.
C'est cette bande, et l'envergure des ailes, qui font lire un trou noir plutôt
qu'un anneau ; une version sans elles ressemblait à Saturne.

La référence normative est `gui/web/favicon.svg` : ce script en reprend le
viewBox (240), les coordonnées et la palette, pour que l'icône du raccourci, la
fenêtre de l'application et l'onglet du navigateur soient le même objet.

Pourquoi un script plutôt qu'un fichier binaire posé là
-------------------------------------------------------
Un `.ico` est illisible en revue de code et indiffusable en `git diff`. Ici,
le dessin est du texte : on peut le relire, le corriger, le régénérer.

Pourquoi une icône MULTI-RÉSOLUTIONS
------------------------------------
Windows ne redimensionne pas une grande icône pour l'afficher en petit : il
choisit, dans le `.ico`, l'image dont la taille correspond au contexte
d'affichage (16 px dans la barre des tâches et la vue Détails, 32 px sur le
Bureau, 48 px en vue Icônes moyennes, 256 px pour l'aperçu et la fiche de
l'installeur). Une icône qui ne contiendrait que du 256 px serait réduite par
le système avec un filtre médiocre : traits noyés, halo boueux, illisible à
16 px — exactement là où l'utilisateur la voit le plus souvent.

Chaque taille est donc dessinée SÉPARÉMENT, avec ses propres réglages (voir
TUNING) : sous 32 px, un trait à l'échelle exacte du modèle tomberait sous le
pixel et ne donnerait plus qu'un gris sale. On l'épaissit donc relativement,
on ouvre l'ellipse du disque pour qu'il reste du noir visible en son centre,
on retire ce qui ne peut plus exister (le liseré de l'horizon, puis l'arc de
lentille), et on force légèrement le halo pour que l'horizon se détache d'un
fond lui-même sombre. C'est le même objet, redessiné pour rester lisible, pas
la même image rétrécie.

Usage :
    python packaging/make_icon.py                 # écrit anti-zeevirius.ico
    python packaging/make_icon.py --preview       # + planche de contrôle PNG
    python packaging/make_icon.py --bitmap-format bmp   # repli (voir README)

Dépendance unique : Pillow (pip install pillow).
"""

from __future__ import annotations

import argparse
import sys
from pathlib import Path
from typing import List, Sequence, Tuple

from PIL import Image, ImageChops, ImageDraw, ImageFilter

HERE = Path(__file__).resolve().parent
ICO_PATH = HERE / "anti-zeevirius.ico"
PREVIEW_PATH = HERE / "icon-preview.png"

# Tailles exigées par Windows. 24 et 64 servent aux écrans à mise à l'échelle
# 150 %/200 % : sans elles, Windows interpole depuis une autre taille.
ICO_SIZES: Tuple[int, ...] = (16, 24, 32, 48, 64, 128, 256)

# Suréchantillonnage : Pillow ne lisse pas les tracés, on dessine donc en
# grand et on réduit en Lanczos. C'est notre anticrénelage.
SUPERSAMPLE = 8

# ── Géométrie, en unités du viewBox 240 de favicon.svg ───────────────────
VB = 240.0
BG = "#08050a"           # fond sombre, presque noir mais pas tout à fait
CORNER_R = 52.0          # rx du <rect> de fond

# Le symbole de l'interface web n'a pas de cadre : ses ailes peuvent déborder
# du viewBox, et elles le font (pointes à x=5 et x=235 sur 240). Une icône, si :
# elle est enfermée dans une vignette carrée à coins arrondis. Sans retrait,
# les pointes viennent mourir à ras du bord et s'y coupent net — l'aile n'a
# plus de pointe, elle a une tranche. On rétrécit donc la marque autour de son
# centre pour lui rendre de l'air, sans toucher à ses proportions.
MARK_SCALE = 0.88
CX, CY = 120.0, 116.0    # centre de la sphère
HALO_CX, HALO_CY, HALO_RX, HALO_RY = 102.0, 132.0, 88.0, 50.0
SPHERE_R = 38.0
ARC_CX, ARC_CY, ARC_RX, ARC_RY = 120.0, 132.0, 94.0, 62.0

# LE RUBAN DE FACE, décrit par ses deux bords. C'est un TRACÉ CONTINU qui part
# de la pointe gauche, passe DEVANT la sphère et ressort à droite. Les
# coordonnées sont celles de `gui/web/index.html`, à l'unité près.
#
# Pourquoi un tracé continu, et pas deux lobes : dessiner un panache à gauche
# et un filet à droite, chacun à sa hauteur, produit deux rubans qui se
# télescopent au bord de la sphère. C'est la continuité du trajet qui fait lire
# une orbite plutôt qu'un décor posé de part et d'autre.
#
# Pourquoi il est asymétrique : large et blanc à gauche — le côté qui vient
# vers l'observateur, amplifié par le décalage Doppler relativiste — il s'affine
# et vire à l'ambre à droite en remontant. Mesuré sur la référence : masse
# lumineuse gauche/droite = 1,13, colonne la plus brillante à 40 % de la largeur.
# Un ruban symétrique se lit comme un anneau de Saturne, quel que soit son galbe.
RIBBON_TOP = ((8.0, 148.0), (38.0, 142.0), (76.0, 133.0), (120.0, 129.0))
RIBBON_TOP2 = ((120.0, 129.0), (165.0, 125.0), (201.0, 118.0), (232.0, 111.0))
RIBBON_BOT = ((232.0, 111.0), (201.0, 128.0), (163.0, 139.0), (120.0, 144.0))
RIBBON_BOT2 = ((120.0, 144.0), (79.0, 149.0), (38.0, 162.0), (8.0, 148.0))

# L'ARC LENSÉ : la face arrière du disque, relevée au-dessus de la sphère par
# la courbure de l'espace. Il culmine AU-DESSUS DU CENTRE, pas à droite : un arc
# décentré dessine une amande au lieu de refermer l'anneau autour de l'horizon.
ARC_A = ((74.0, 128.0), (78.0, 92.0), (104.0, 64.0), (132.0, 66.0))
ARC_B = ((132.0, 66.0), (164.0, 69.0), (186.0, 88.0), (204.0, 110.0))

# LA BANDE : le segment du ruban qui passe devant l'horizon, rehaussé. Elle
# traverse la sphère à 75 % de sa hauteur — mesure relevée sur la référence.
# Plus bas elle donnerait un demi-disque blanc, plus haut elle couperait la
# sphère en deux.
BAND = ((74.0, 141.0), (120.0, 136.0), (170.0, 129.0))

# Dégradé du disque : le point le plus chaud est à 42 % de la largeur, donc À
# GAUCHE du centre. Repris tel quel de `gui/web/index.html` (#bhDisk).
WING_STOPS: Sequence[Tuple[float, str, float]] = (
    (0.00, "#8a3410", 0.00),
    (0.10, "#e07a22", 0.55),
    (0.26, "#ffc888", 0.92),
    (0.42, "#fffaf0", 1.00),
    (0.55, "#fff0d4", 1.00),
    (0.70, "#f5a44a", 0.90),
    (0.86, "#c9541a", 0.55),
    (1.00, "#6d2606", 0.00),
)

# L'anneau de photons : lumineux sur TOUT son tour, le plus vif en bas à gauche.
# Sans lui, la sphère n'est qu'un disque découpé dans le fond — c'est le trait
# que les versions précédentes de cette icône omettaient.
RING_STOPS: Sequence[Tuple[float, str, float]] = (
    (0.00, "#ffffff", 1.00),
    (0.32, "#fff8ea", 0.92),
    (0.66, "#ffe9c4", 0.82),
    (1.00, "#ffc98a", 0.62),
)

ARC_STOPS = WING_STOPS

BAND_STOPS: Sequence[Tuple[float, str, float]] = (
    (0.00, "#ffcf92", 0.55),
    (0.26, "#ffffff", 1.00),
    (0.62, "#fffdf6", 1.00),
    (1.00, "#ffbe72", 0.45),
)

HALO_STOPS: Sequence[Tuple[float, str, float]] = (
    (0.00, "#ffbe6a", 0.20),
    (0.34, "#ff8420", 0.12),
    (0.66, "#c2410c", 0.05),
    (1.00, "#7a2408", 0.00),
)

# ── Réglages par taille ──────────────────────────────────────────────────
# Toutes les longueurs sont en unités du viewBox 240 : à 16 px, une unité ne
# vaut que 0,067 pixel. Le trait nominal de la bande (3.4) donnerait 0,23 px —
# soit rien du tout. On épaissit donc à mesure qu'on rapetisse.
#
# `wing` multiplie l'épaisseur des ailes, `sphere` remplace le rayon de
# l'horizon. Les deux grossissent ensemble en descendant : une aile de 0,9 px
# et une sphère de 5,9 px ne survivent pas à une vignette de 16 pixels, il n'en
# resterait qu'une bavure grise. En les gonflant, la marque garde sa
# silhouette — ailes effilées, sphère noire barrée d'un trait blanc — qui est
# ce que l'œil reconnaît, bien avant le détail.
#
# L'arc de lentille disparaît sous 32 px : large de 0,9 px, il ne produisait
# qu'un voile au-dessus de la sphère, sans jamais se lire comme un arc.
TUNING = {
    16:  dict(wing=2.8, band=14.0, glow=24.0, arc=0.0,  sphere=46.0, ring=11.0, halo=1.20, bloom=0.0),
    24:  dict(wing=2.2, band=10.0, glow=18.0, arc=9.0,  sphere=44.0, ring=8.0,  halo=1.05, bloom=3.0),
    32:  dict(wing=1.9, band=8.0,  glow=15.0, arc=7.5,  sphere=42.0, ring=6.4,  halo=0.95, bloom=5.0),
    48:  dict(wing=1.5, band=5.6,  glow=12.0, arc=6.0,  sphere=40.0, ring=4.4,  halo=0.95, bloom=7.0),
    64:  dict(wing=1.3, band=4.4,  glow=10.0, arc=5.4,  sphere=39.0, ring=3.4,  halo=1.00, bloom=8.0),
    128: dict(wing=1.08, band=3.2, glow=8.0,  arc=4.9,  sphere=38.0, ring=2.7,  halo=1.00, bloom=9.0),
    256: dict(wing=1.0, band=2.6,  glow=7.0,  arc=4.6,  sphere=38.0, ring=2.4,  halo=1.00, bloom=9.8),
}


# ── Utilitaires couleur ──────────────────────────────────────────────────
def _rgb(value: str) -> Tuple[int, int, int]:
    value = value.lstrip("#")
    return (int(value[0:2], 16), int(value[2:4], 16), int(value[4:6], 16))


def _sample(stops: Sequence[Tuple[float, str, float]], t: float) -> Tuple[int, int, int, int]:
    """Interpole la rampe (position, couleur, opacité) en t ∈ [0, 1]."""
    t = min(1.0, max(0.0, t))
    prev = stops[0]
    for stop in stops:
        if t <= stop[0]:
            p0, c0, a0 = prev
            p1, c1, a1 = stop
            span = (p1 - p0) or 1.0
            f = (t - p0) / span
            r0, g0, b0 = _rgb(c0)
            r1, g1, b1 = _rgb(c1)
            return (
                round(r0 + (r1 - r0) * f),
                round(g0 + (g1 - g0) * f),
                round(b0 + (b1 - b0) * f),
                round(255 * (a0 + (a1 - a0) * f)),
            )
        prev = stop
    r, g, b = _rgb(stops[-1][1])
    return (r, g, b, round(255 * stops[-1][2]))


def _horizontal_gradient(px: int, stops: Sequence[Tuple[float, str, float]]) -> Image.Image:
    """Bande RGBA de `px` de côté, dégradée sur l'axe X (constante en Y)."""
    row = Image.new("RGBA", (px, 1))
    row.putdata([_sample(stops, x / max(1, px - 1)) for x in range(px)])
    return row.resize((px, px), Image.Resampling.NEAREST)


def _radial_halo(px: int, strength: float,
                 box: Tuple[float, float, float, float]) -> Image.Image:
    """Halo elliptique : ellipses concentriques de la plus large à la plus étroite.

    Dessiner 96 anneaux pleins puis réduire en Lanczos coûte moins cher qu'un
    calcul par pixel et donne, après suréchantillonnage, un dégradé continu.
    """
    layer = Image.new("RGBA", (px, px), (0, 0, 0, 0))
    draw = ImageDraw.Draw(layer)
    steps = 96
    x0, y0, x1, y1 = box
    cx, cy = (x0 + x1) / 2, (y0 + y1) / 2
    max_rx, max_ry = (x1 - x0) / 2, (y1 - y0) / 2
    for i in range(steps, 0, -1):
        t = i / steps
        rx, ry = max_rx * t, max_ry * t
        red, green, blue, alpha = _sample(HALO_STOPS, t)
        alpha = round(alpha * strength / 6)  # empilement : chaque anneau ajoute peu
        if alpha <= 0:
            continue
        draw.ellipse((cx - rx, cy - ry, cx + rx, cy + ry), fill=(red, green, blue, alpha))
    return layer


def _mask(px: int) -> Tuple[Image.Image, ImageDraw.ImageDraw]:
    m = Image.new("L", (px, px), 0)
    return m, ImageDraw.Draw(m)


def _paint(base: Image.Image, mask: Image.Image,
           stops: Sequence[Tuple[float, str, float]]) -> None:
    """Peint un dégradé horizontal à travers `mask` sur `base` (RGBA).

    Pillow ne sait pas tracer un trait dégradé : on trace donc la forme en
    blanc dans un masque, et on s'en sert comme pochoir sur la bande dégradée.
    """
    px = base.size[0]
    grad = _horizontal_gradient(px, stops)
    grad.putalpha(ImageChops.multiply(grad.getchannel("A"), mask))
    base.alpha_composite(grad)


# ── Courbes ──────────────────────────────────────────────────────────────
def _bezier(points: Sequence[Tuple[float, float]], steps: int = 64) -> List[Tuple[float, float]]:
    """Échantillonne une courbe de Bézier (quadratique ou cubique) en polyligne.

    Pillow ne connaît que des segments : toute courbe doit être réduite en
    points. 64 pas suffisent — à la résolution suréchantillonnée, l'écart entre
    deux pas reste sous le pixel.
    """
    n = len(points) - 1
    out = []
    for i in range(steps + 1):
        t = i / steps
        # de Casteljau : on interpole récursivement jusqu'au point unique.
        cur = list(points)
        for _ in range(n):
            cur = [(a[0] + (b[0] - a[0]) * t, a[1] + (b[1] - a[1]) * t)
                   for a, b in zip(cur, cur[1:])]
        out.append(cur[0])
    return out


def _ribbon_outline(m: float, pas: int = 96) -> List[Tuple[float, float]]:
    """Contour fermé du ruban de face, épaisseur multipliée par `m`.

    Le multiplicateur ne déforme pas le tracé : on calcule la MÉDIANE entre les
    deux bords, puis on écarte chaque bord de `m` fois sa distance à cette
    médiane. Épaissir en ne déplaçant qu'un seul bord ferait dériver la
    trajectoire du ruban, et un ruban qui ne suit plus son orbite ne se lit plus
    comme une orbite. Nécessaire aux petites tailles : à 16 px, le ruban nominal
    mesure moins d'un pixel de haut et ne donne qu'une bavure grise.
    """
    haut = _bezier(RIBBON_TOP, pas) + _bezier(RIBBON_TOP2, pas)
    # Le bord inférieur est décrit de droite à gauche : on le remet dans le sens
    # du bord supérieur pour pouvoir apparier les points.
    bas = list(reversed(_bezier(RIBBON_BOT, pas) + _bezier(RIBBON_BOT2, pas)))

    n = min(len(haut), len(bas))
    haut, bas = haut[:n], bas[:n]
    h2, b2 = [], []
    for (xh, yh), (xb, yb) in zip(haut, bas):
        mediane = (yh + yb) / 2
        h2.append((xh, mediane + (yh - mediane) * m))
        b2.append((xb, mediane + (yb - mediane) * m))
    return h2 + list(reversed(b2))


def _arc_trace() -> List[Tuple[float, float]]:
    """Polyligne de l'arc lensé, les deux cubiques mises bout à bout."""
    return _bezier(ARC_A) + _bezier(ARC_B)[1:]


def render(size: int) -> Image.Image:
    """Dessine l'icône à `size` pixels, avec les réglages de cette taille."""
    cfg = TUNING.get(size) or TUNING[max(t for t in TUNING if t <= size)]
    px = size * SUPERSAMPLE
    k = px / VB  # facteur unités-viewBox → pixels suréchantillonnés

    centre = px / 2.0

    def pt(x: float, y: float) -> Tuple[float, float]:
        """Coordonnée viewBox → pixel, retrait compris."""
        return (centre + (x - CX) * k * MARK_SCALE,
                centre + (y - CY) * k * MARK_SCALE)

    def lg(v: float) -> float:
        """Longueur viewBox → pixel, retrait compris."""
        return v * k * MARK_SCALE

    def ech(pts):
        return [pt(x, y) for x, y in pts]

    def boite(cx: float, cy: float, rx: float, ry: float):
        x, y = pt(cx, cy)
        return (x - lg(rx), y - lg(ry), x + lg(rx), y + lg(ry))

    img = Image.new("RGBA", (px, px), (0, 0, 0, 0))

    # 1. Fond sombre à coins arrondis.
    ImageDraw.Draw(img).rounded_rectangle(
        (0, 0, px - 1, px - 1), radius=CORNER_R * k, fill=_rgb(BG) + (255,)
    )

    # 2. Halo diffus.
    if cfg["halo"] > 0:
        img.alpha_composite(_radial_halo(
            px, cfg["halo"], boite(HALO_CX, HALO_CY, HALO_RX, HALO_RY)))

    ruban = ech(_ribbon_outline(cfg["wing"]))
    arc = ech(_arc_trace())
    arc_w = lg(cfg["arc"])
    sphere_box = boite(CX, CY, cfg["sphere"], cfg["sphere"])

    # 3. Lueur : ruban et arc, flous, posés SOUS les tracés nets. C'est ce qui
    #    donne l'incandescence — un tracé net seul paraît dessiné, pas brûlant.
    #    Désactivé à 16 px, où le flou ne ferait que salir.
    if cfg["bloom"] > 0:
        lueur = Image.new("RGBA", (px, px), (0, 0, 0, 0))
        m, dr = _mask(px)
        dr.polygon(ruban, fill=255)
        if arc_w:
            dr.line(arc, fill=200, width=round(arc_w), joint="curve")
        _paint(lueur, m, WING_STOPS)
        lueur = lueur.filter(ImageFilter.GaussianBlur(cfg["bloom"] * k))
        lueur.putalpha(lueur.getchannel("A").point(lambda a: a * 3 // 4))
        img.alpha_composite(lueur)

    # 4. L'ARC LENSÉ, tracé AVANT la sphère : c'est la face arrière du disque,
    #    relevée au-dessus de l'horizon par la courbure de l'espace. Elle passe
    #    donc derrière lui, et la sphère doit l'occulter.
    if arc_w >= 1:
        m, dr = _mask(px)
        dr.line(arc, fill=255, width=round(arc_w), joint="curve")
        _paint(img, m, ARC_STOPS)

    # 5. La sphère : presque noire, mais PAS noir pur. La référence montre à
    #    l'intérieur une luminance olive très faible, plus claire vers le
    #    haut-gauche : c'est l'image de l'arrière du disque, ramenée là par la
    #    courbure. Du noir pur donne un trou de découpe, pas un horizon.
    coeur = Image.new("RGBA", (px, px), (0, 0, 0, 0))
    cd = ImageDraw.Draw(coeur)
    x0, y0, x1, y1 = sphere_box
    etapes = 64
    for i in range(etapes, 0, -1):
        t = i / etapes
        # Dégradé décentré vers le haut-gauche, comme sur la référence.
        cxg = x0 + (x1 - x0) * 0.40
        cyg = y0 + (y1 - y0) * 0.36
        r = max(x1 - x0, y1 - y0) / 2 * 0.64 * t
        v = round(35 * (1 - t) ** 1.2)
        cd.ellipse((cxg - r, cyg - r, cxg + r, cyg + r), fill=(v, v + 2, max(0, v - 8), 255))
    decoupe, dd = _mask(px)
    dd.ellipse(sphere_box, fill=255)
    fond = Image.new("RGBA", (px, px), (0, 0, 0, 0))
    ImageDraw.Draw(fond).ellipse(sphere_box, fill=(0, 0, 0, 255))
    img.alpha_composite(fond)
    coeur.putalpha(ImageChops.multiply(coeur.getchannel("A"), decoupe))
    img.alpha_composite(coeur)

    # 6. L'ANNEAU DE PHOTONS, complet. Sur la référence c'est le trait le plus
    #    net de l'image : la dernière orbite où la lumière rase l'horizon.
    anneau_w = max(2.0, lg(cfg.get("ring", 2.4)))
    for largeur, opacite, flou in ((anneau_w * 2.1, 0.34, True), (anneau_w, 1.0, False)):
        m, dr = _mask(px)
        dr.ellipse((sphere_box[0] - largeur / 2, sphere_box[1] - largeur / 2,
                    sphere_box[2] + largeur / 2, sphere_box[3] + largeur / 2),
                   outline=round(255 * opacite), width=round(largeur))
        couche = Image.new("RGBA", (px, px), (0, 0, 0, 0))
        _paint(couche, m, RING_STOPS)
        if flou:
            couche = couche.filter(ImageFilter.GaussianBlur(largeur / 2.2))
        img.alpha_composite(couche)

    # 7. LE RUBAN DE FACE, tracé APRÈS la sphère donc par-dessus : il passe
    #    DEVANT l'horizon. Puis son segment central est rehaussé — c'est là
    #    qu'il est le plus incandescent — et découpé au disque de la sphère.
    m, dr = _mask(px)
    dr.polygon(ruban, fill=255)
    _paint(img, m, WING_STOPS)

    trace = ech(_bezier(BAND))
    bande = Image.new("RGBA", (px, px), (0, 0, 0, 0))
    if lg(cfg["glow"]) >= 1:
        voile = Image.new("RGBA", (px, px), (0, 0, 0, 0))
        m, dr = _mask(px)
        dr.line(trace, fill=95, width=round(lg(cfg["glow"])), joint="curve")
        _paint(voile, m, BAND_STOPS)
        bande.alpha_composite(voile.filter(
            ImageFilter.GaussianBlur(max(1.0, lg(cfg["glow"]) / 2.4))))
    if lg(cfg["band"]) >= 1:
        m, dr = _mask(px)
        dr.line(trace, fill=243, width=round(lg(cfg["band"])), joint="curve")
        _paint(bande, m, BAND_STOPS)
    bande.putalpha(ImageChops.multiply(bande.getchannel("A"), decoupe))
    img.alpha_composite(bande)

    # 8. Coins arrondis : on découpe la couche alpha finale, sinon le halo
    #    déborderait dans les angles transparents.
    corner, cd = _mask(px)
    cd.rounded_rectangle((0, 0, px - 1, px - 1), radius=CORNER_R * k, fill=255)
    img.putalpha(ImageChops.multiply(img.getchannel("A"), corner))

    return img.resize((size, size), Image.Resampling.LANCZOS)


def build_preview(frames: List[Image.Image], path: Path) -> None:
    """Planche de contrôle, en quatre bandes : chaque taille agrandie au plus
    proche voisin (pour juger pixel à pixel) puis à l'échelle 1:1 (ce que
    l'œil aura réellement), sur fond clair ET sur fond sombre — l'Explorateur
    Windows peut être dans l'un ou l'autre thème, et une icône ne doit
    disparaître dans aucun des deux.
    """
    pad, cell = 14, 132
    # Chaque colonne est aussi large que ce qu'elle doit contenir : la case de
    # zoom (132) ou, pour les grandes tailles, l'image 1:1 elle-même (256).
    widths = [max(cell, f.width) for f in frames]
    xs, cursor = [], pad
    for w in widths:
        xs.append(cursor)
        cursor += w + pad
    width = cursor
    band = pad + cell + pad + 256 + pad     # une bande = rangée zoom + rangée 1:1
    sheet = Image.new("RGB", (width, band * 2), (244, 244, 247))
    sheet.paste((16, 16, 20), (0, band, width, band * 2))

    for band_top in (0, band):
        for x, w, frame in zip(xs, widths, frames):
            filt = (Image.Resampling.NEAREST if frame.width <= cell
                    else Image.Resampling.LANCZOS)
            zoom = frame.resize((cell, cell), filt)
            sheet.paste(zoom, (x + (w - cell) // 2, band_top + pad), zoom)
            # Rangée 1:1, calée en haut de la case, centrée horizontalement.
            y = band_top + pad + cell + pad
            sheet.paste(frame, (x + (w - frame.width) // 2, y), frame)
    sheet.save(path)


def main(argv: List[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description="Génère packaging/anti-zeevirius.ico")
    parser.add_argument("--out", type=Path, default=ICO_PATH)
    parser.add_argument("--preview", action="store_true",
                        help="écrit aussi une planche de contrôle PNG")
    parser.add_argument("--bitmap-format", choices=("png", "bmp"), default="png",
                        help="encodage interne des images du .ico ; 'bmp' est le "
                             "repli si un outil ancien refuse les trames PNG")
    args = parser.parse_args(argv)

    frames = [render(s) for s in ICO_SIZES]
    largest = frames[-1]
    args.out.parent.mkdir(parents=True, exist_ok=True)
    largest.save(
        args.out,
        format="ICO",
        sizes=[(s, s) for s in ICO_SIZES],
        append_images=frames[:-1],   # sinon Pillow rétrécirait le 256 pour toutes
        bitmap_format=args.bitmap_format,
    )
    print(f"écrit : {args.out} ({args.out.stat().st_size / 1024:.1f} Kio, "
          f"{len(ICO_SIZES)} résolutions)")

    if args.preview:
        build_preview(frames, PREVIEW_PATH)
        print(f"écrit : {PREVIEW_PATH}")
    return 0


if __name__ == "__main__":
    sys.exit(main())
