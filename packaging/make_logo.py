"""
packaging/make_logo.py — fabrique les ressources du logo depuis la photographie.

L'identite visuelle d'ANTI-ZEEVIRIUS est une PHOTOGRAPHIE de trou noir, fournie
par le proprietaire du projet et versionnee dans `docs/identite/`.

Pourquoi une photographie et non un dessin vectoriel
----------------------------------------------------
Cinq versions dessinees a la main ont ete rejetees. La cause n'etait pas le
trace mais le principe : une photographie de trou noir porte du volume, des
filaments et du grain. Un chemin SVG ne sait pas les imiter — il en fait une
illustration, et une illustration de trou noir se lit comme un anneau de
Saturne. La seule facon d'etre fidele a une photographie est de l'utiliser.

Ce que fait ce script
---------------------
1. Il garde la photographie ENTIERE pour l'interface. Un recadrage carre
   coupait le panache gauche et le fond etoile — donc l'essentiel de l'image.
   Seule l'icone Windows est recadree, parce qu'un `.ico` doit etre carre.
2. Il fond les bords vers la transparence. Le fond de la photo est presque noir
   comme l'interface, mais sans ce fondu l'image poserait un carre visible sur
   les cartes en verre depoli du Dashboard.
3. Il ecrit les ressources :
     gui/web/logo-trou-noir.webp   affiche par l'interface (leger)
     gui/web/logo-trou-noir.png    repli pour les contextes sans WebP
     gui/web/favicon.svg           la meme image en data URI (autonome)
     packaging/anti-zeevirius.ico  sept resolutions pour Windows

Pourquoi l'icone Windows est redimensionnee taille par taille
-------------------------------------------------------------
Windows ne reduit pas une grande icone pour l'afficher en petit : il choisit,
dans le `.ico`, l'image dont la taille correspond au contexte (16 px dans la
barre des taches, 32 px sur le Bureau, 256 px pour l'apercu). Une icone qui ne
contiendrait que du 256 px serait reduite par le systeme avec un filtre
mediocre. Sous 32 px, on renforce en plus le contraste : une photographie
reduite a 16 pixels devient une bouillie grise si on ne l'aide pas.

Usage :
    python packaging/make_logo.py
    python packaging/make_logo.py --preview     # + planche de controle

Dependance unique : Pillow.
"""

from __future__ import annotations

import argparse
import base64
import math
import sys
from pathlib import Path
from typing import List, Tuple

from PIL import Image, ImageChops, ImageDraw, ImageEnhance

RACINE = Path(__file__).resolve().parent.parent
SOURCE = RACINE / "docs" / "identite" / "reference-trou-noir.png"
WEB = RACINE / "gui" / "web"
ICO = RACINE / "packaging" / "anti-zeevirius.ico"
PREVIEW = RACINE / "packaging" / "logo-preview.png"

LARGE = 960               # largeur de la ressource web, ratio d'origine garde
SEUIL = 110               # luminance a partir de laquelle un pixel « compte »
MARGE = 1.10              # 10 % d'air autour du sujet mesure
# Fondu alpha : plein jusqu'a PLEIN, eteint a BORD, en fraction de la diagonale.
# Mesuré : au-delà de 0,60 le rectangle de la photographie redevient visible sur
# les cartes en verre dépoli, et une marque qui montre son cadre n'est plus une
# marque, c'est une image collée.
PLEIN, BORD = 0.52, 1.00

TAILLES_ICO: Tuple[int, ...] = (16, 24, 32, 48, 64, 128, 256)
CORNER_R = 0.216          # 52/240, le meme arrondi que le favicon

# Renforts pour les petites tailles : sous 32 px, la photographie perd son
# contraste avant de perdre son detail. C'est le contraste qui porte la
# reconnaissance dans une vignette, pas le detail.
RENFORT = {16: (1.75, 1.30), 24: (1.55, 1.22), 32: (1.35, 1.14),
           48: (1.18, 1.07), 64: (1.10, 1.04)}


def _etendue(img: Image.Image) -> Tuple[int, int, int, int]:
    """Bornes du sujet lumineux. Mesurees, pas devinees."""
    g = img.convert("L")
    W, H = g.size
    px = g.load()
    cols = [max(px[x, y] for y in range(H)) for x in range(W)]
    rows = [max(px[x, y] for x in range(W)) for y in range(H)]
    x0 = next(x for x in range(W) if cols[x] > SEUIL)
    x1 = next(x for x in range(W - 1, -1, -1) if cols[x] > SEUIL)
    y0 = next(y for y in range(H) if rows[y] > SEUIL)
    y1 = next(y for y in range(H - 1, -1, -1) if rows[y] > SEUIL)
    return x0, y0, x1, y1


def _masque_fondu(W: int, H: int, plein: float = PLEIN) -> Image.Image:
    """Alpha plein au centre, eteint au bord, en coordonnees NORMALISEES : le
    fondu suit le rectangle de l'image au lieu d'un cercle, sinon les coins
    d'une image large sont ronges alors que le sujet y est encore visible.

    Puissance 1,5 : un fondu lineaire laisse un cerne visible, l'oeil detecte
    la rupture de derivee."""
    m = Image.new("L", (W, H), 0)
    mp = m.load()
    for y in range(H):
        ny = (y - H / 2) / (H / 2)
        for x in range(W):
            nx = (x - W / 2) / (W / 2)
            # Distance de TCHEBYCHEV, pas euclidienne. Avec la distance
            # euclidienne normalisée sur la diagonale, le fondu n'atteint zéro
            # QU'AUX COINS : au milieu de chaque bord il reste à 41 % d'opacité,
            # et l'œil voit alors le rectangle de la photographie posé sur la
            # carte. Avec max(|nx|,|ny|), l'alpha tombe à zéro sur TOUT le
            # pourtour — il n'y a plus d'arête nulle part.
            d = max(abs(nx), abs(ny))               # 0 au centre, 1 sur tout le bord
            if d <= plein:
                a = 255
            elif d >= BORD:
                a = 0
            else:
                a = int(255 * (1 - (d - plein) / (BORD - plein)) ** 1.8)
            mp[x, y] = a
    return m


def construire_pleine() -> Image.Image:
    """La photographie ENTIERE, a son ratio d'origine, bords fondus.

    Pourquoi entiere : le panache blanc de gauche et le fond etoile font partie
    du sujet. Un recadrage carre les supprime, et le resultat n'est plus « la
    photo » — c'est un gros plan sur la sphere.
    """
    if not SOURCE.is_file():
        raise SystemExit(f"[ERREUR] source absente : {SOURCE}")
    src = Image.open(SOURCE).convert("RGB")
    H = int(round(LARGE * src.height / src.width))
    img = src.resize((LARGE, H), Image.Resampling.LANCZOS).convert("RGBA")
    # Le fondu doit etre FRANC : la photographie est posee sur des cartes en
    # verre depoli, et le moindre liseré residuel se lit comme un cadre.
    img.putalpha(_masque_fondu(LARGE, H))
    return img


def construire_carre(pleine: Image.Image) -> Image.Image:
    """Recadrage carre, POUR L'ICONE SEULEMENT : un `.ico` doit etre carre.

    Le carre est centre sur le sujet mesure, et pris le plus large possible
    pour conserver autant du panache gauche que la contrainte le permet.
    """
    src = Image.open(SOURCE).convert("RGB")
    W, H = src.size
    x0, y0, x1, y1 = _etendue(src)
    cx, cy = (x0 + x1) / 2, (y0 + y1) / 2
    cote = min(W, H)
    gx = max(0, min(int(round(cx - cote / 2)), W - cote))
    gy = max(0, min(int(round(cy - cote / 2)), H - cote))
    carre = src.crop((gx, gy, gx + cote, gy + cote)).resize(
        (640, 640), Image.Resampling.LANCZOS).convert("RGBA")
    # L'icone, elle, garde un fondu tardif : elle est enfermee dans une
    # vignette a coins arrondis qui fournit deja son cadre.
    carre.putalpha(_masque_fondu(640, 640, plein=0.82))
    return carre


def rendre_icone(carre: Image.Image, taille: int) -> Image.Image:
    """Une image du `.ico`, contraste renforce et coins arrondis."""
    im = carre.resize((taille * 4, taille * 4), Image.Resampling.LANCZOS)
    contraste, luminosite = RENFORT.get(taille, (1.0, 1.0))
    if contraste != 1.0:
        rgb = Image.merge("RGB", im.split()[:3])
        rgb = ImageEnhance.Contrast(rgb).enhance(contraste)
        rgb = ImageEnhance.Brightness(rgb).enhance(luminosite)
        im = Image.merge("RGBA", rgb.split() + (im.split()[3],))

    px = taille * 4
    plaque = Image.new("RGBA", (px, px), (0, 0, 0, 0))
    ImageDraw.Draw(plaque).rounded_rectangle(
        (0, 0, px - 1, px - 1), radius=CORNER_R * px, fill=(8, 5, 10, 255))
    plaque.alpha_composite(im)
    coins, cd = Image.new("L", (px, px), 0), None
    cd = ImageDraw.Draw(coins)
    cd.rounded_rectangle((0, 0, px - 1, px - 1), radius=CORNER_R * px, fill=255)
    plaque.putalpha(ImageChops.multiply(plaque.getchannel("A"), coins))
    return plaque.resize((taille, taille), Image.Resampling.LANCZOS)


def ecrire_favicon(carre: Image.Image) -> None:
    """Le favicon embarque l'image en data URI : un SVG qui pointerait vers un
    fichier voisin ne charge pas de maniere fiable quand il sert de favicon."""
    petit = carre.resize((128, 128), Image.Resampling.LANCZOS)
    tampon = WEB / ".favicon-tmp.webp"
    petit.save(tampon, "WEBP", quality=88, method=6)
    b64 = base64.b64encode(tampon.read_bytes()).decode("ascii")
    tampon.unlink()
    (WEB / "favicon.svg").write_text(
        "<!-- Icone d'onglet — GENEREE par packaging/make_logo.py, ne pas\n"
        "     editer a la main. C'est la meme photographie que l'en-tete du\n"
        "     Dashboard, embarquee en data URI pour que le fichier soit\n"
        "     autonome : un SVG qui pointerait vers un fichier voisin ne charge\n"
        "     pas de maniere fiable quand il sert de favicon. -->\n"
        '<svg xmlns="http://www.w3.org/2000/svg" viewBox="0 0 240 240" width="64" height="64">\n'
        '  <clipPath id="c"><rect width="240" height="240" rx="52"/></clipPath>\n'
        '  <g clip-path="url(#c)">\n'
        '    <rect width="240" height="240" fill="#08050a"/>\n'
        f'    <image x="0" y="0" width="240" height="240" href="data:image/webp;base64,{b64}"/>\n'
        "  </g>\n"
        "</svg>\n", encoding="utf-8")


def planche(frames: List[Image.Image], chemin: Path) -> None:
    """Planche de controle : chaque taille agrandie au plus proche voisin puis
    a l'echelle 1:1, sur fond clair ET sur fond sombre — l'Explorateur Windows
    peut etre dans l'un ou l'autre theme, l'icone ne doit disparaitre dans
    aucun des deux."""
    pad, cell = 14, 132
    larg = [max(cell, f.width) for f in frames]
    xs, curseur = [], pad
    for w in larg:
        xs.append(curseur)
        curseur += w + pad
    bande = pad + cell + pad + 256 + pad
    feuille = Image.new("RGB", (curseur, bande * 2), (244, 244, 247))
    feuille.paste((16, 16, 20), (0, bande, curseur, bande * 2))
    for haut in (0, bande):
        for x, w, f in zip(xs, larg, frames):
            filtre = (Image.Resampling.NEAREST if f.width <= cell
                      else Image.Resampling.LANCZOS)
            z = f.resize((cell, cell), filtre)
            feuille.paste(z, (x + (w - cell) // 2, haut + pad), z)
            feuille.paste(f, (x + (w - f.width) // 2, haut + pad + cell + pad), f)
    feuille.save(chemin)


def main(argv: List[str] | None = None) -> int:
    ap = argparse.ArgumentParser(description="Fabrique les ressources du logo")
    ap.add_argument("--preview", action="store_true",
                    help="ecrit aussi une planche de controle PNG")
    args = ap.parse_args(argv)

    pleine = construire_pleine()
    carre = construire_carre(pleine)
    WEB.mkdir(parents=True, exist_ok=True)
    pleine.save(WEB / "logo-trou-noir.webp", "WEBP", quality=90, method=6)
    pleine.save(WEB / "logo-trou-noir.png", "PNG", optimize=True)
    print(f"  ratio conserve : {pleine.width}x{pleine.height}")
    ecrire_favicon(carre)

    frames = [rendre_icone(carre, t) for t in TAILLES_ICO]
    frames[-1].save(ICO, format="ICO", sizes=[(t, t) for t in TAILLES_ICO],
                    append_images=frames[:-1], bitmap_format="png")

    for f in (WEB / "logo-trou-noir.webp", WEB / "logo-trou-noir.png",
              WEB / "favicon.svg", ICO):
        print(f"ecrit : {f.relative_to(RACINE)} "
              f"({f.stat().st_size / 1024:.1f} Kio)")
    if args.preview:
        planche(frames, PREVIEW)
        print(f"ecrit : {PREVIEW.relative_to(RACINE)}")
    return 0


if __name__ == "__main__":
    sys.exit(main())
