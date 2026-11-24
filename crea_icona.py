#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""
Icona di «Le note del Big Bang»: un ovale di cielo (proiezione di Mollweide) a macchie blu e
arancio come la radiazione cosmica di fondo, attraversato da una curva dorata con tre picchi
acustici, e una croma (nota musicale) dorata in alto a destra, su fondo indaco.
© 2026 Edoardo Battistel
"""
import math
import random
from pathlib import Path

import numpy as np
from PIL import Image, ImageDraw, ImageFilter

LATO = 512


def campo_macchie(w, h, seme=3):
    """Campo casuale gaussiano con macchie di più dimensioni (somma di rumori sfocati)."""
    rng = np.random.default_rng(seme)
    tot = np.zeros((h, w))
    for scala, peso in ((40, 1.0), (16, 0.7), (7, 0.45)):
        r = Image.fromarray((rng.normal(0, 1, (h // scala + 2, w // scala + 2)) * 40 + 128).clip(0, 255).astype(np.uint8))
        r = r.resize((w, h), Image.BICUBIC)
        tot += peso * (np.asarray(r, float) - 128)
    tot /= np.std(tot)
    return tot


def colore(v):
    """Scala di colori dell'icona: blu – crema – arancio."""
    punti = [(-2.2, (16, 46, 120)), (-1.0, (52, 120, 200)), (-0.3, (150, 195, 235)), (0.0, (250, 243, 220)),
             (0.4, (246, 178, 90)), (1.1, (217, 84, 30)), (2.2, (120, 20, 20))]
    v = np.clip(v, punti[0][0], punti[-1][0])
    out = np.zeros(v.shape + (3,))
    for (a, ca), (b, cb) in zip(punti[:-1], punti[1:]):
        m = (v >= a) & (v <= b)
        t = ((v - a) / (b - a))[m][:, None]
        out[m] = (1 - t) * np.array(ca) + t * np.array(cb)
    return out.astype(np.uint8)


def crea(cartella):
    S = LATO * 2
    img = Image.new("RGBA", (S, S), (0, 0, 0, 0))
    yy, xx = np.mgrid[0:S, 0:S]
    r = np.hypot(xx - S * 0.5, yy - S * 0.42) / (S * 0.78)
    t = np.clip(r, 0, 1)[..., None]
    fondo = ((1 - t) * np.array([44, 30, 104]) + t * np.array([10, 8, 36])).astype(np.uint8)
    fondo = Image.fromarray(np.dstack([fondo, np.full((S, S), 255, np.uint8)]), "RGBA")
    masc = Image.new("L", (S, S), 0)
    ImageDraw.Draw(masc).rounded_rectangle([0, 0, S - 1, S - 1], radius=int(S * 0.18), fill=255)
    img.paste(fondo, (0, 0), masc)
    d = ImageDraw.Draw(img)
    rnd = random.Random(11)
    for _ in range(70):
        x, y = rnd.uniform(0.05, 0.95) * S, rnd.uniform(0.05, 0.95) * S
        if ((x - S * 0.5) / (S * 0.44)) ** 2 + ((y - S * 0.47) / (S * 0.24)) ** 2 < 1.15:
            continue
        rr = rnd.choice([1, 1, 2, 2, 3])
        d.ellipse([x - rr, y - rr, x + rr, y + rr], fill=(255, 250, 230, rnd.randint(110, 230)))
    # ovale del cielo con le macchie
    cx, cy, ax, ay = S * 0.5, S * 0.47, S * 0.43, S * 0.23
    alone = Image.new("RGBA", (S, S), (0, 0, 0, 0))
    ImageDraw.Draw(alone).ellipse([cx - ax - S * 0.02, cy - ay - S * 0.02, cx + ax + S * 0.02, cy + ay + S * 0.02],
                                  fill=(120, 160, 255, 110))
    img.alpha_composite(alone.filter(ImageFilter.GaussianBlur(S * 0.02)))
    w, h = int(2 * ax), int(2 * ay)
    macchie = Image.fromarray(colore(campo_macchie(w, h)), "RGB").convert("RGBA")
    m_ov = Image.new("L", (w, h), 0)
    ImageDraw.Draw(m_ov).ellipse([0, 0, w - 1, h - 1], fill=255)
    img.paste(macchie, (int(cx - ax), int(cy - ay)), m_ov)
    ImageDraw.Draw(img).ellipse([cx - ax, cy - ay, cx + ax, cy + ay], outline=(255, 230, 160, 200), width=int(S * 0.006))
    # curva dorata dello spettro: tre picchi che calano
    punti = []
    for i in range(0, 501):
        u = i / 500
        x = S * (0.08 + 0.84 * u)
        dval = (0.15 + 0.95 * math.exp(-((u - 0.22) / 0.085) ** 2) + 0.45 * math.exp(-((u - 0.50) / 0.075) ** 2)
                + 0.42 * math.exp(-((u - 0.75) / 0.075) ** 2)) * math.exp(-(u / 1.3) ** 2)
        punti.append((x, S * 0.86 - S * 0.33 * dval))
    ombra = Image.new("RGBA", (S, S), (0, 0, 0, 0))
    ImageDraw.Draw(ombra).line(punti, fill=(255, 205, 80, 210), width=int(S * 0.034), joint="curve")
    img.alpha_composite(ombra.filter(ImageFilter.GaussianBlur(S * 0.012)))
    ImageDraw.Draw(img).line(punti, fill=(255, 228, 130, 255), width=int(S * 0.016), joint="curve")
    # croma dorata in alto a destra
    nx, ny = S * 0.80, S * 0.17
    oro = (255, 214, 90, 255)
    d = ImageDraw.Draw(img)
    d.ellipse([nx - S * 0.055, ny + S * 0.05, nx + S * 0.035, ny + S * 0.11], fill=oro)
    d.line([(nx + S * 0.03, ny + S * 0.08), (nx + S * 0.03, ny - S * 0.10)], fill=oro, width=int(S * 0.016))
    d.polygon([(nx + S * 0.03, ny - S * 0.10), (nx + S * 0.10, ny - S * 0.04), (nx + S * 0.09, ny - S * 0.01),
               (nx + S * 0.03, ny - S * 0.05)], fill=oro)
    out = img.resize((LATO, LATO), Image.LANCZOS)
    cartella = Path(cartella)
    out.save(cartella / "note_big_bang.png")
    out.save(cartella / "note_big_bang.ico", sizes=[(16, 16), (24, 24), (32, 32), (48, 48), (64, 64), (128, 128),
                                                    (256, 256)])
    return cartella / "note_big_bang.png"


if __name__ == "__main__":
    print(crea(Path(__file__).resolve().parent))
