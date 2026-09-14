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
CX, CY = 120.0, 118.0    # centre de la sphère
HALO_CY, HALO_RX, HALO_RY = 122.0, 119.0, 86.0
SPHERE_R = 44.0
ARC_CX, ARC_CY, ARC_RX, ARC_RY = 120.0, 132.0, 94.0, 62.0

# LES AILES, décrites par leur ossature plutôt que par un tracé figé.
# Chaque nœud est (x, y_médiane, demi-épaisseur) : le bord supérieur passe à
# y - m·demi, l'inférieur à y + m·demi, où m est le multiplicateur d'épaisseur
# propre à chaque taille d'icône (voir TUNING). Décrire l'aile ainsi permet de
# l'épaissir pour les petites tailles SANS la déformer : une aile dont on
# remonterait seulement le bord haut cesserait d'être une aile.
#
# Les nœuds intermédiaires sont les points de contrôle des cubiques de
# favicon.svg ; les valeurs proviennent directement de ce fichier, en prenant
# la médiane et le demi-écart de chaque paire de points haut/bas.
WING_SPINE = (
    (5.0,   135.5, 0.0),    # pointe extérieure : épaisseur nulle
    (42.0,  131.0, 11.0),   # contrôle : c'est ce nœud qui donne le galbe
    (76.0,  130.0, 8.0),    # contrôle
    (105.0, 131.0, 2.0),    # raccord à la sphère
)
WING_INNER_X = 135.0        # l'aile traverse le centre jusqu'ici avant de repartir

# LA BANDE incandescente : l'avant du disque passant DEVANT l'horizon.
# Quadratique, incurvée vers le BAS au centre — l'avant d'une ellipse vue de
# trois quarts est son point le plus bas.
BAND = ((48.0, 126.0), (120.0, 140.0), (192.0, 126.0))

# Dégradé des ailes : braise aux pointes, blanc incandescent près de
# l'horizon. Repris tel quel de favicon.svg (#fWing).
WING_STOPS: Sequence[Tuple[float, str, float]] = (
    (0.00, "#b8420c", 0.55),
    (0.16, "#f08324", 0.92),
    (0.38, "#ffd79a", 1.00),
    (0.50, "#fffdf6", 1.00),
    (0.62, "#ffd79a", 1.00),
    (0.84, "#ef7f20", 0.92),
    (1.00, "#a8380a", 0.55),
)

# Arc de lentille : même famille de teintes, mais translucide — c'est une
# image déviée, pas de la matière.
ARC_STOPS: Sequence[Tuple[float, str, float]] = (
    (0.00, "#a8380a", 0.45),
    (0.20, "#f4902c", 0.92),
    (0.50, "#fff6e2", 1.00),
    (0.80, "#f08324", 0.92),
    (1.00, "#a8380a", 0.45),
)

# La bande : le point le plus lumineux de toute l'image.
BAND_STOPS: Sequence[Tuple[float, str, float]] = (
    (0.00, "#ffcf92", 0.75),
    (0.28, "#fffefb", 1.00),
    (0.72, "#fffdf6", 1.00),
    (1.00, "#ffcf92", 0.75),
)

# Halo radial (rayonnement diffus autour de l'ensemble).
HALO_STOPS: Sequence[Tuple[float, str, float]] = (
    (0.00, "#ffb257", 0.40),
    (0.30, "#ff7a17", 0.26),
    (0.62, "#c2410c", 0.14),
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
    16:  dict(wing=2.6, band=15.0, glow=26.0, arc=0.0,  sphere=52.0, rim=False, halo=1.20, bloom=0.0),
    24:  dict(wing=2.1, band=11.0, glow=20.0, arc=0.0,  sphere=50.0, rim=False, halo=1.05, bloom=3.0),
    32:  dict(wing=1.8, band=8.5,  glow=17.0, arc=16.0, sphere=48.0, rim=False, halo=0.95, bloom=5.0),
    48:  dict(wing=1.4, band=6.0,  glow=13.0, arc=14.5, sphere=46.0, rim=False, halo=0.95, bloom=7.0),
    64:  dict(wing=1.2, band=4.8,  glow=11.0, arc=13.5, sphere=45.0, rim=True,  halo=1.00, bloom=8.0),
    128: dict(wing=1.05, band=3.8, glow=9.5,  arc=13.0, sphere=44.0, rim=True,  halo=1.00, bloom=9.0),
    256: dict(wing=1.0, band=3.4,  glow=9.0,  arc=13.0, sphere=44.0, rim=True,  halo=1.00, bloom=9.8),
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


def _wing_outline(m: float) -> List[Tuple[float, float]]:
    """Contour fermé des deux ailes, à l'épaisseur multipliée par `m`.

    On construit l'aile gauche depuis son ossature (bord haut puis bord bas),
    puis on la reflète autour de x = 120 pour obtenir la droite. Le miroir
    garantit une marque symétrique : à la main, deux côtés dessinés séparément
    finissent toujours par diverger de quelques unités, ce qui se voit.
    """
    haut = [(x, y - m * d) for x, y, d in WING_SPINE]
    bas = [(x, y + m * d) for x, y, d in WING_SPINE]

    def miroir(pts):
        return [(2 * CX - x, y) for x, y in pts]

    bord_haut_g = _bezier(haut)
    bord_bas_g = _bezier(bas)
    # Sens de parcours : pointe gauche → raccord gauche → raccord droit →
    # pointe droite → retour par le bord inférieur.
    contour = list(bord_haut_g)
    contour.append((WING_INNER_X, haut[-1][1]))
    contour += miroir(list(reversed(bord_haut_g)))
    contour += miroir(bord_bas_g)
    contour.append((2 * CX - WING_INNER_X, bas[-1][1]))
    contour += list(reversed(bord_bas_g))
    return contour


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
            px, cfg["halo"], boite(CX, HALO_CY, HALO_RX, HALO_RY)))

    ailes = ech(_wing_outline(cfg["wing"]))
    arc_w = lg(cfg["arc"])
    arc_box = boite(ARC_CX, ARC_CY, ARC_RX, ARC_RY)

    # 3. Lueur : ailes et arc, flous, posés SOUS les traits nets. C'est ce qui
    #    donne l'incandescence — un tracé net seul paraît dessiné, pas brûlant.
    #    Désactivé à 16 px, où le flou ne ferait que salir.
    if cfg["bloom"] > 0:
        lueur = Image.new("RGBA", (px, px), (0, 0, 0, 0))
        m, d = _mask(px)
        d.polygon(ailes, fill=255)
        if arc_w:
            d.arc(arc_box, 180, 360, fill=200, width=round(arc_w))
        _paint(lueur, m, WING_STOPS)
        lueur = lueur.filter(ImageFilter.GaussianBlur(cfg["bloom"] * k))
        lueur.putalpha(lueur.getchannel("A").point(lambda a: a * 3 // 4))
        img.alpha_composite(lueur)

    # 4. Arc de lentille gravitationnelle : l'ARRIÈRE du disque, ramené
    #    au-dessus de la sphère par la courbure de l'espace.
    if arc_w:
        m, d = _mask(px)
        d.arc(arc_box, 180, 360, fill=255, width=round(arc_w))
        _paint(img, m, ARC_STOPS)

    # 5. Les ailes : le disque vu par la tranche. C'est leur envergure —
    #    beaucoup plus large que la sphère — qui fait lire un trou noir et non
    #    un anneau.
    m, d = _mask(px)
    d.polygon(ailes, fill=255)
    _paint(img, m, WING_STOPS)

    # 6. La sphère : noir pur, opaque. Aucun dégradé, sinon elle se lit comme
    #    une planète éclairée.
    sphere_box = boite(CX, CY, cfg["sphere"], cfg["sphere"])
    ImageDraw.Draw(img).ellipse(sphere_box, fill=(0, 0, 0, 255))

    # 7. La bande incandescente qui traverse la sphère : l'avant du disque
    #    passant DEVANT l'horizon. Découpée au disque de la sphère — elle
    #    déborde volontairement de part et d'autre pour qu'aucune extrémité ne
    #    soit visible à l'intérieur.
    trace = ech(_bezier(BAND))
    bande = Image.new("RGBA", (px, px), (0, 0, 0, 0))

    # Le voile d'abord, et FLOU : une large ligne nette à 40 % d'opacité ne
    # donne pas une incandescence mais une barre grise à arêtes franches —
    # c'est le défaut qui faisait ressembler la bande à un morceau de métal.
    if lg(cfg["glow"]) >= 1:
        voile = Image.new("RGBA", (px, px), (0, 0, 0, 0))
        m, d = _mask(px)
        d.line(trace, fill=115, width=round(lg(cfg["glow"])), joint="curve")
        _paint(voile, m, BAND_STOPS)
        flou = max(1.0, lg(cfg["glow"]) / 2.4)
        bande.alpha_composite(voile.filter(ImageFilter.GaussianBlur(flou)))

    # Puis le cœur, net : c'est lui qu'on doit lire comme un trait.
    if lg(cfg["band"]) >= 1:
        m, d = _mask(px)
        d.line(trace, fill=247, width=round(lg(cfg["band"])), joint="curve")
        _paint(bande, m, BAND_STOPS)
    decoupe, dc = _mask(px)
    dc.ellipse(sphere_box, fill=255)
    bande.putalpha(ImageChops.multiply(bande.getchannel("A"), decoupe))
    img.alpha_composite(bande)

    # 8. Liseré de l'horizon : la dernière orbite stable, là où la lumière
    #    rase la sphère. Trop fin pour exister sous 64 px.
    if cfg["rim"]:
        rim_w = max(2.0, 0.9 * k)
        ImageDraw.Draw(img).ellipse(
            (sphere_box[0] - rim_w / 2, sphere_box[1] - rim_w / 2,
             sphere_box[2] + rim_w / 2, sphere_box[3] + rim_w / 2),
            outline=(255, 207, 146, 77), width=round(rim_w),
        )

    # 9. Coins arrondis : on découpe la couche alpha finale, sinon le halo
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
