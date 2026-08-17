"""
packaging/make_icon.py — fabrique l'icône Windows d'ANTI-ZEEVIRIUS.

L'identité du projet est un **trou noir** : un horizon parfaitement opaque,
un disque d'accrétion incandescent vu de profil (donc écrasé en ellipse très
plate, sa moitié avant passant DEVANT l'horizon), et un arc de lentille
gravitationnelle qui passe au-dessus. La référence normative est
`gui/web/favicon.svg` : ce script en reprend les proportions exactes
(viewBox 64 — disque rx=27, horizon r=12, arc ry=17) et sa palette, pour que
l'icône du raccourci et l'onglet de l'interface web soient le même objet.

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

# ── Géométrie, en unités du viewBox 64 de favicon.svg ────────────────────
VB = 64.0
BG = "#08050a"           # fond sombre, presque noir mais pas tout à fait
CORNER_R = 14.0          # rx du <rect> de fond
CX, CY = 32.0, 32.0      # centre du trou noir
DISK_CY = 33.5           # le disque est légèrement sous le centre (vu de face-dessus)
DISK_RX, DISK_RY = 27.0, 5.4
LENS_RX, LENS_RY = 27.0, 17.0
HORIZON_R = 12.0
RIM_R = 12.5
HALO_R = 30.0

# Dégradé du disque d'accrétion, de gauche à droite : braise → orange →
# blanc-chaud au plus vif (le point où la matière file vers nous) → orange →
# braise. Repris tel quel de favicon.svg.
DISK_STOPS: Sequence[Tuple[float, str, float]] = (
    (0.00, "#b8380a", 1.0),
    (0.18, "#f2811b", 1.0),
    (0.50, "#fff6dd", 1.0),
    (0.82, "#f5871c", 1.0),
    (1.00, "#b8380a", 1.0),
)

# Arc de lentille : même famille de teintes, mais translucide — c'est une
# image déviée, pas de la matière.
LENS_STOPS: Sequence[Tuple[float, str, float]] = (
    (0.00, "#b8380a", 0.50),
    (0.50, "#ffe6b4", 0.95),
    (1.00, "#b8380a", 0.50),
)

# Halo radial (rayonnement diffus autour de l'ensemble).
HALO_STOPS: Sequence[Tuple[float, str, float]] = (
    (0.00, "#ffb257", 0.55),
    (0.55, "#ff7a17", 0.18),
    (1.00, "#e04a05", 0.00),
)

# ── Réglages par taille ──────────────────────────────────────────────────
# Les épaisseurs sont exprimées en unités du viewBox 64 : à 16 px, une unité
# ne vaut que 0,25 pixel. Le trait nominal du disque (4.6) donnerait 1,15 px,
# et l'arc (3.4) 0,85 px — sous le pixel, donc gris sale au lieu d'un trait.
# On épaissit donc à mesure qu'on rapetisse, et on supprime les détails qui
# ne peuvent plus exister (liseré de l'horizon, halo trop large).
TUNING = {
    # disk/lens : épaisseur de trait ; disk_ry : demi-hauteur de l'ellipse du
    # disque ; horizon : rayon de l'horizon ; rim : liseré clair ; halo/bloom :
    # rayonnement diffus et lueur. Tout en unités du viewBox 64.
    #
    # Noter la progression de disk_ry : 5.4 (valeur exacte du SVG) au-dessus de
    # 64 px, puis de plus en plus ouvert en descendant. Raison : à 16 px, une
    # ellipse d'à peine 2,7 px de haut traversée par un trait de 1,4 px ne
    # laisse plus AUCUN noir visible au centre — l'horizon disparaît et il ne
    # reste qu'une barre orange. En ouvrant l'ellipse, l'anneau enferme une
    # zone sombre : c'est ce contraste, et non le détail, qui fait reconnaître
    # un trou noir vu de profil dans une vignette de 16 pixels.
    # Pour la même raison l'arc de lentille disparaît à 16 px : 0,8 px de large,
    # il ne produisait qu'un voile gris au-dessus de l'horizon.
    16:  dict(disk=5.6, lens=0.0, disk_ry=9.5, horizon=12.5, rim=False, halo=1.20, bloom=0.0),
    24:  dict(disk=5.6, lens=3.4, disk_ry=7.5, horizon=12.5, rim=False, halo=1.00, bloom=0.8),
    32:  dict(disk=5.4, lens=3.6, disk_ry=6.6, horizon=12.2, rim=False, halo=0.90, bloom=1.4),
    48:  dict(disk=5.0, lens=3.5, disk_ry=6.0, horizon=12.0, rim=True,  halo=0.95, bloom=1.8),
    64:  dict(disk=4.9, lens=3.5, disk_ry=5.7, horizon=12.0, rim=True,  halo=1.00, bloom=2.2),
    128: dict(disk=4.7, lens=3.5, disk_ry=5.5, horizon=12.0, rim=True,  halo=1.00, bloom=2.4),
    256: dict(disk=4.6, lens=3.4, disk_ry=5.4, horizon=12.0, rim=True,  halo=1.00, bloom=2.6),
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


def _radial_halo(px: int, radius_px: float, strength: float) -> Image.Image:
    """Halo radial : disques concentriques du plus large au plus étroit.

    Dessiner 96 anneaux pleins puis réduire en Lanczos coûte moins cher qu'un
    calcul par pixel et donne, après suréchantillonnage, un dégradé continu.
    """
    layer = Image.new("RGBA", (px, px), (0, 0, 0, 0))
    draw = ImageDraw.Draw(layer)
    steps = 96
    cx = CX / VB * px
    cy = CY / VB * px
    for i in range(steps, 0, -1):
        t = i / steps
        r = radius_px * t
        red, green, blue, alpha = _sample(HALO_STOPS, t)
        alpha = round(alpha * strength / 6)  # empilement : chaque anneau ajoute peu
        if alpha <= 0:
            continue
        draw.ellipse((cx - r, cy - r, cx + r, cy + r), fill=(red, green, blue, alpha))
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


def render(size: int) -> Image.Image:
    """Dessine l'icône à `size` pixels, avec les réglages de cette taille."""
    cfg = TUNING.get(size) or TUNING[max(t for t in TUNING if t <= size)]
    px = size * SUPERSAMPLE
    k = px / VB  # facteur unités-viewBox → pixels suréchantillonnés

    disk_w = max(2.0, cfg["disk"] * k)
    lens_w = max(2.0, cfg["lens"] * k) if cfg["lens"] else 0.0
    disk_cy = cfg.get("disk_cy", DISK_CY)
    disk_ry = cfg.get("disk_ry", DISK_RY)

    img = Image.new("RGBA", (px, px), (0, 0, 0, 0))

    # 1. Fond sombre à coins arrondis.
    ImageDraw.Draw(img).rounded_rectangle(
        (0, 0, px - 1, px - 1), radius=CORNER_R * k, fill=_rgb(BG) + (255,)
    )

    # 2. Halo diffus.
    if cfg["halo"] > 0:
        img.alpha_composite(_radial_halo(px, HALO_R * k, cfg["halo"]))

    # Boîtes englobantes. Pillow épaissit un trait VERS L'INTÉRIEUR, alors que
    # SVG le centre sur le tracé : on élargit donc la boîte d'une demi-épaisseur
    # pour retomber sur la même géométrie que favicon.svg.
    def box(rx: float, ry: float, cy: float, w: float):
        return (CX * k - rx * k - w / 2, cy * k - ry * k - w / 2,
                CX * k + rx * k + w / 2, cy * k + ry * k + w / 2)

    lens_box = box(LENS_RX, LENS_RY, disk_cy, lens_w)
    disk_box = box(DISK_RX, disk_ry, disk_cy, disk_w)

    # 3. Lueur : le disque et l'arc, flous, posés SOUS les traits nets. C'est
    #    ce qui donne l'incandescence — un trait net seul paraît dessiné, pas
    #    brûlant. Désactivé à 16 px, où le flou ne ferait que salir.
    if cfg["bloom"] > 0:
        glow = Image.new("RGBA", (px, px), (0, 0, 0, 0))
        m, d = _mask(px)
        if lens_w:
            d.arc(lens_box, 180, 360, fill=200, width=round(lens_w))
        d.ellipse(disk_box, outline=255, width=round(disk_w))
        _paint(glow, m, DISK_STOPS)
        glow = glow.filter(ImageFilter.GaussianBlur(cfg["bloom"] * k))
        glow.putalpha(glow.getchannel("A").point(lambda a: a * 3 // 4))
        img.alpha_composite(glow)

    # 4. Arc de lentille gravitationnelle (moitié supérieure de l'ellipse
    #    haute) : la lumière des étoiles situées DERRIÈRE le trou noir,
    #    ramenée au-dessus de lui par la courbure de l'espace.
    if lens_w:
        m, d = _mask(px)
        d.arc(lens_box, 180, 360, fill=255, width=round(lens_w))
        _paint(img, m, LENS_STOPS)

    # 5. Disque d'accrétion complet (l'ellipse entière : moitié arrière
    #    visible au-dessus de l'horizon, moitié avant qui sera reprise en 7).
    m, d = _mask(px)
    d.ellipse(disk_box, outline=255, width=round(disk_w))
    _paint(img, m, DISK_STOPS)

    # 6. Horizon des événements : noir plein, opaque. Il masque la moitié
    #    arrière du disque et tout ce qui passe derrière lui.
    hr = cfg.get("horizon", HORIZON_R) * k
    ImageDraw.Draw(img).ellipse(
        (CX * k - hr, CY * k - hr, CX * k + hr, CY * k + hr), fill=(0, 0, 0, 255)
    )
    if cfg["rim"]:
        rim_w = max(2.0, 0.7 * k)
        rr = RIM_R * k
        ImageDraw.Draw(img).ellipse(
            (CX * k - rr - rim_w / 2, CY * k - rr - rim_w / 2,
             CX * k + rr + rim_w / 2, CY * k + rr + rim_w / 2),
            outline=(255, 220, 166, 128), width=round(rim_w),
        )

    # 7. Moitié AVANT du disque, redessinée par-dessus l'horizon : c'est elle
    #    qui dit « vu de profil » et empêche de lire l'image comme un anneau.
    m, d = _mask(px)
    d.arc(disk_box, 0, 180, fill=255, width=round(disk_w))
    _paint(img, m, DISK_STOPS)

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
