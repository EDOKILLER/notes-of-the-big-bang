#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""
LE NOTE DEL BIG BANG - MOTORE
=============================
© 2026 Edoardo Battistel

Dalle mappe Planck PR3 della radiazione cosmica di fondo (CMB) allo spettro di potenza
angolare, ai picchi acustici, alle stime della geometria dell'universo e della materia
ordinaria, al suono delle «note» del plasma primordiale.

Scelte principali (vedi progetti/note_big_bang.md):
  - si scaricano le due METÀ della missione (hm1, hm2): il loro spettro INCROCIATO non ha il
    rumore dello strumento; la loro media è la mappa da mostrare, la mezza differenza misura
    il rumore;
  - archivio veloce: Planck Legacy Archive dell'ESA (5 MB/s, ma senza «Range»); se cade a
    metà si riprende da IRSA (lento, ma con «Range»), dal punto raggiunto;
  - maschera comune Planck, ridotta alla risoluzione di lavoro e sfumata ai bordi;
  - correzione per la maschera (f_sky), per il fascio (5′) e per i pixel;
  - i picchi si trovano con una parabola pesata sui dati binnati (errori Monte Carlo);
  - stime semplici: angolo del primo picco, curvatura (regola Ω ≈ (ℓ₁ piatto / ℓ₁)²),
    materia ordinaria ω_b dal rapporto tra 2° e 1° picco (formula di Hu et al. 2001).
"""
import json
import math
import re
import ssl
import time
import urllib.error
import urllib.parse
import urllib.request
import wave
from datetime import datetime, timezone
from pathlib import Path

import numpy as np

VERSIONE = "1.4"
FIRMA = "© 2026 Edoardo Battistel"
UA = f"NoteBigBang/{VERSIONE} (progetto personale di astronomia; Python urllib)"

IRSA = "https://irsa.ipac.caltech.edu/data/Planck/release_3"
IRSA_CMB = IRSA + "/all-sky-maps/maps/component-maps/cmb"
IRSA_MAPPE = IRSA + "/all-sky-maps/maps"
IRSA_MASCHERE = IRSA + "/ancillary-data/masks"
IRSA_COSMO = IRSA + "/ancillary-data/cosmoparams"
PLA = "https://pla.esac.esa.int/pla/aio/product-action"


def _cartella_astronomia():
    for d in (Path.home() / "Scrivania" / "Astronomia", Path.home() / "Desktop" / "Astronomia"):
        if d.exists():
            return d
    return Path.home() / "Scrivania" / "Astronomia"


CARTELLA_DATI = _cartella_astronomia() / "Planck"
CACHE = CARTELLA_DATI / "cache"


def cartella_risultati_predefinita():
    return _cartella_astronomia() / "Note_big_bang"


# pipeline -> (descrizione, modello del nome dei file con {parte} = hm1 / hm2 / full)
MAPPE = {
    "smica-nosz": ("SMICA senza SZ (consigliata)", "COM_CMB_IQU-smica-nosz_2048_R3.00_{parte}.fits"),
    "smica": ("SMICA (file più grandi)", "COM_CMB_IQU-smica_2048_R3.00_{parte}.fits"),
    "commander": ("Commander", "COM_CMB_IQU-commander_2048_R3.00_{parte}.fits"),
    "nilc": ("NILC", "COM_CMB_IQU-nilc_2048_R3.00_{parte}.fits"),
    "sevem": ("SEVEM", "COM_CMB_IQU-sevem_2048_R3.00_{parte}.fits"),
}
MASCHERA = "COM_Mask_CMB-common-Mask-Int_2048_R3.00.fits"
SPETTRO_TT = "COM_PowerSpect_CMB-TT-full_R3.01.txt"
SPETTRO_BINNATO = "COM_PowerSpect_CMB-TT-binned_R3.01.txt"
SPETTRO_LCDM = "COM_PowerSpect_CMB-base-plikHM-TTTEEE-lowl-lowE-lensing-minimum-theory_R3.01.txt"

# Nside di lavoro -> ℓ massimo
RISOLUZIONI = {512: 1000, 1024: 2000, 2048: 2500}
FWHM_ARCMIN = 5.0                     # fascio delle mappe CMB di Planck PR3
NSIDE_ORIGINE = 2048

# Dipolo del Sole (Planck 2018 I): ampiezza e direzione galattica
DIPOLO_UK = 3362.08
DIPOLO_L, DIPOLO_B = 264.021, 48.253
T0_K = 2.7255

# Parametri ΛCDM di Planck 2018 usati come riferimento nelle stime
OMEGA_B_H2_PLANCK = 0.02237
OMEGA_M_H2_PLANCK = 0.1430
NS_PLANCK = 0.965
H_PLANCK = 0.674

# zone in cui cercare i picchi acustici (ℓ)
ZONE_PICCHI = [(1, 150, 330), (2, 420, 650), (3, 700, 920), (4, 1000, 1250),
               (5, 1300, 1560), (6, 1620, 1860), (7, 1900, 2150)]


class Interrotto(Exception):
    pass


def nulla(*_a, **_k):
    pass


# =============================================================================
# RETE
# =============================================================================
def _contesto():
    return ssl.create_default_context()


def apri(url, intestazioni=None, metodo="GET", tempo=60):
    h = {"User-Agent": UA, "Accept": "*/*"}
    h.update(intestazioni or {})
    req = urllib.request.Request(url, headers=h, method=metodo)
    return urllib.request.urlopen(req, timeout=tempo, context=_contesto())


def url_irsa(nome):
    if nome.startswith("COM_CMB"):
        return f"{IRSA_CMB}/{nome}"
    if nome.startswith("COM_Mask") or "_Mask_" in nome:
        return f"{IRSA_MASCHERE}/{nome}"
    if nome.startswith("COM_PowerSpect"):
        return f"{IRSA_COSMO}/{nome}"
    return f"{IRSA_MAPPE}/{nome}"


def url_pla(nome):
    if nome.startswith("COM_PowerSpect"):
        return f"{PLA}?COSMOLOGY.FILE_ID={urllib.parse.quote(nome)}"
    return f"{PLA}?MAP.MAP_ID={urllib.parse.quote(nome)}"


def dimensione_remota(nome, tempo=30):
    """Dimensione del file su IRSA (HEAD); None se non si sa."""
    for metodo, intest in (("HEAD", None), ("GET", {"Range": "bytes=0-0"})):
        try:
            with apri(url_irsa(nome), intest, metodo=metodo, tempo=tempo) as r:
                cr = r.headers.get("Content-Range") or ""
                m = re.search(r"/(\d+)", cr)
                if m:
                    return int(m.group(1))
                cl = r.headers.get("Content-Length")
                if cl and metodo == "HEAD":
                    return int(cl)
        except Exception:
            continue
    return None


def _inizio_valido(nome, primi):
    if nome.endswith(".fits"):
        return primi.startswith(b"SIMPLE")
    t = primi[:400].decode("utf-8", "replace").lower()
    return "<html" not in t and "<!doctype" not in t


def scarica_file(nome, cartella=None, log=nulla, avanzamento=nulla, ferma=None, max_tentativi=8):
    """Scarica un file Planck una volta sola. Prima dall'ESA (veloce); se si interrompe,
    riprende da IRSA con «Range» dal punto raggiunto. Restituisce il percorso."""
    cartella = Path(cartella or CARTELLA_DATI)
    cartella.mkdir(parents=True, exist_ok=True)
    dest = cartella / nome
    part = cartella / (nome + ".part")
    segno = cartella / (nome + ".ok")
    atteso = None
    if dest.exists() and segno.exists() and segno.read_text().strip() == str(dest.stat().st_size):
        return dest
    if dest.exists():
        atteso = dimensione_remota(nome)
        if atteso is None or dest.stat().st_size == atteso:
            if atteso:
                segno.write_text(str(atteso))
            return dest
        log(f"  {nome}: il file sul disco ha {dest.stat().st_size} byte invece di {atteso}: lo riscarico")
        dest.unlink()
    if atteso is None:
        atteso = dimensione_remota(nome)
    tentativi = 0
    pla_ok = True
    while True:
        if ferma is not None and ferma.is_set():
            raise Interrotto()
        gia = part.stat().st_size if part.exists() else 0
        if atteso and gia == atteso:
            break
        if atteso and gia > atteso:
            part.unlink()
            gia = 0
        if gia == 0 and pla_ok:
            fonte, url, intest, modo = "ESA", url_pla(nome), None, "wb"
        else:
            fonte, url, intest, modo = "IRSA", url_irsa(nome), ({"Range": f"bytes={gia}-"} if gia else None), "ab"
        try:
            t0 = time.time()
            with apri(url, intest, tempo=60) as r:
                if modo == "ab" and gia and r.status != 206:
                    gia, modo = 0, "wb"          # il server ha ignorato «Range»: si ricomincia
                totale = atteso or (int(r.headers.get("Content-Length") or 0) + gia) or None
                primo = True
                fatti = gia
                ultimo = 0.0
                with open(part, modo) as f:
                    while True:
                        if ferma is not None and ferma.is_set():
                            raise Interrotto()
                        pezzo = r.read(1024 * 1024)
                        if not pezzo:
                            break
                        if primo and fatti == 0:
                            primo = False
                            if not _inizio_valido(nome, pezzo):
                                raise ValueError(f"{fonte} non ha mandato il file ma: "
                                                 + re.sub(r"\s+", " ", pezzo[:120].decode("utf-8", "replace")))
                        primo = False
                        f.write(pezzo)
                        fatti += len(pezzo)
                        ora = time.time()
                        if ora - ultimo > 0.5:
                            ultimo = ora
                            v = (fatti - gia) / max(ora - t0, 1e-3) / 1e6
                            avanzamento(fatti, totale or fatti,
                                        f"Scarico {nome}: {fatti / 1e6:.0f}"
                                        + (f" di {totale / 1e6:.0f}" if totale else "") + f" MB a {v:.1f} MB/s ({fonte})")
            fatti = part.stat().st_size
            if atteso is None or fatti == atteso:
                if atteso is None and fatti == 0:
                    raise ValueError("file vuoto")
                break
            raise OSError(f"collegamento chiuso a {fatti / 1e6:.1f} MB di {atteso / 1e6:.1f}")
        except Interrotto:
            raise
        except Exception as e:
            tentativi += 1
            if fonte == "ESA":
                pla_ok = tentativi < 2 and not isinstance(e, ValueError)
                if isinstance(e, ValueError) and part.exists():
                    part.unlink()
            log(f"  {nome}: {fonte} – {type(e).__name__}: {e}"[:300])
            if tentativi >= max_tentativi:
                raise RuntimeError(f"non riesco a scaricare {nome} dopo {tentativi} tentativi") from e
            attesa = min(5 * tentativi, 60)
            log(f"  riprovo tra {attesa} s" + (" da IRSA, dal punto raggiunto" if part.exists() and part.stat().st_size else ""))
            for _ in range(attesa * 2):
                if ferma is not None and ferma.is_set():
                    raise Interrotto()
                time.sleep(0.5)
    with open(part, "rb") as f:
        if not _inizio_valido(nome, f.read(2880)):
            part.unlink()
            raise RuntimeError(f"{nome}: il file scaricato non è valido")
    part.replace(dest)
    segno.write_text(str(dest.stat().st_size))
    log(f"  {nome}: scaricato ({dest.stat().st_size / 1e6:.1f} MB)")
    return dest


# =============================================================================
# SPETTRI UFFICIALI (testo)
# =============================================================================
def leggi_tabella(percorso):
    righe = []
    for r in Path(percorso).read_text(encoding="utf-8", errors="replace").splitlines():
        s = r.strip()
        if not s or s[0] in "#!;%":
            continue
        try:
            righe.append([float(x) for x in re.split(r"[\s,]+", s)])
        except ValueError:
            continue
    if not righe:
        raise ValueError(f"{Path(percorso).name}: nessuna riga di numeri")
    n = min(len(r) for r in righe)
    return np.array([r[:n] for r in righe])


def leggi_spettro_ufficiale(percorso):
    """Spettro TT Planck: colonne ℓ, D_ℓ, −dD, +dD (e BestFit nel binnato). μK²."""
    a = leggi_tabella(percorso)
    out = {"ell": a[:, 0], "D": a[:, 1]}
    if a.shape[1] >= 4:
        out["err"] = 0.5 * (np.abs(a[:, 2]) + np.abs(a[:, 3]))
    elif a.shape[1] >= 3:
        out["err"] = np.abs(a[:, 2])
    return out


def leggi_lcdm(percorso):
    """Modello ΛCDM migliore: colonne L, TT, TE, EE, BB, PP (D_ℓ in μK²)."""
    a = leggi_tabella(percorso)
    return {"ell": a[:, 0], "D": a[:, 1]}


# =============================================================================
# HEALPIX (healpy)
# =============================================================================
def hp_():
    try:
        import healpy as hp
    except ImportError as e:
        raise RuntimeError("manca healpy: lancia il programma con ~/Claude/Astropy/venv_compila/bin/python "
                           "(lì healpy è installato) oppure installalo con pip") from e
    return hp


def _cattivi(m):
    return ~np.isfinite(m) | (m < -1e30)


def leggi_healpix(percorso, campo=0):
    hp = hp_()
    try:
        m = hp.read_map(str(percorso), field=campo, dtype=np.float32, memmap=True)
    except TypeError:
        m = hp.read_map(str(percorso), field=campo, dtype=np.float32)
    return np.array(m, dtype=np.float32)


def mappa_ridotta(percorso, nside, log=nulla):
    """Mappa I in μK (RING) alla risoluzione di lavoro; salvata in cache. Pixel cattivi = UNSEEN."""
    hp = hp_()
    CACHE.mkdir(parents=True, exist_ok=True)
    f = CACHE / f"{Path(percorso).stem}_n{nside}.npy"
    if f.exists():
        return np.load(f)
    log(f"  leggo {Path(percorso).name} e la porto a Nside {nside}…")
    m = leggi_healpix(percorso)
    cattivi = _cattivi(m)
    m = m * np.float32(1e6)                       # K_CMB -> μK
    m[cattivi] = hp.UNSEEN
    if hp.get_nside(m) != nside:
        m = hp.ud_grade(m, nside, pess=False).astype(np.float32)
    m[_cattivi(m)] = hp.UNSEEN
    np.save(f, m)
    return m


def maschera_ridotta(percorso, nside, log=nulla):
    """Frazione di cielo buono in ogni pixel (0–1) alla risoluzione di lavoro; in cache."""
    hp = hp_()
    CACHE.mkdir(parents=True, exist_ok=True)
    f = CACHE / f"{Path(percorso).stem}_n{nside}.npy"
    if f.exists():
        return np.load(f)
    log(f"  leggo la maschera e la porto a Nside {nside}…")
    m = leggi_healpix(percorso)
    m[_cattivi(m)] = 0
    if hp.get_nside(m) != nside:
        m = hp.ud_grade(m, nside, pess=False)
    m = np.clip(np.asarray(m, dtype=np.float32), 0, 1)
    np.save(f, m)
    return m


def finestra_apodizzata(frazione, nside, gradi, log=nulla):
    """Maschera binaria (pixel buoni almeno al 90 %) con bordi sfumati su ~gradi/2."""
    hp = hp_()
    binaria = (frazione > 0.9).astype(np.float64)
    if gradi <= 0:
        return binaria
    CACHE.mkdir(parents=True, exist_ok=True)
    f = CACHE / f"finestra_n{nside}_apo{gradi:.2f}_{int(binaria.sum())}.npy"
    if f.exists():
        return np.load(f)
    log(f"  sfumo i bordi della maschera ({gradi:.1f}°)…")
    lmax = min(3 * nside - 1, 1500)
    s = hp.smoothing(binaria, fwhm=math.radians(gradi), lmax=lmax, iter=1)
    w = np.clip((np.asarray(s) - 0.5) / 0.5, 0, 1) * binaria
    np.save(f, w)
    return w


def finestra_pixel(nside, lmax):
    hp = hp_()
    try:
        try:
            p = hp.pixwin(nside, lmax=lmax)
        except TypeError:
            p = hp.pixwin(nside)
        p = np.asarray(p, dtype=float)[:lmax + 1]
        if p.size == lmax + 1:
            return p
    except Exception:
        pass
    # ripiego: pixel come una gaussiana con FWHM ≈ lato del pixel
    lato = math.sqrt(4 * math.pi / (12 * nside * nside))
    ell = np.arange(lmax + 1)
    return np.exp(-0.5 * ell * (ell + 1) * (lato / 2.355) ** 2)


def funzione_finestra(nside, lmax):
    """Fascio gaussiano 5′ × pixel 2048 × (pixel della risoluzione di lavoro se ridotta)."""
    hp = hp_()
    b = np.asarray(hp.gauss_beam(math.radians(FWHM_ARCMIN / 60.0), lmax=lmax), dtype=float)[:lmax + 1]
    b = b * finestra_pixel(NSIDE_ORIGINE, lmax)
    if nside < NSIDE_ORIGINE:
        b = b * finestra_pixel(nside, lmax)
    return b


def _prepara(m, w):
    """Toglie monopolo e dipolo residui sui pixel buoni e moltiplica per la maschera."""
    hp = hp_()
    x = np.array(m, dtype=np.float64)
    cattivi = _cattivi(x) | (w <= 0)
    x[cattivi] = hp.UNSEEN
    x = np.asarray(hp.remove_dipole(x, bad=hp.UNSEEN), dtype=np.float64)
    x[cattivi] = 0.0
    return x * w


def spettri(m1, m2, w, nside, lmax, log=nulla, ferma=None):
    """C_ℓ corretti (μK²): incrociato hm1×hm2, rumore (mezza differenza), automatico della media."""
    hp = hp_()
    w2 = float(np.mean(w ** 2))
    fsky = float(np.mean(w > 0))
    a, b = _prepara(m1, w), _prepara(m2, w)
    finestra = funzione_finestra(nside, lmax)
    out = {}
    for chiave, testo, x, y in (("incrociato", "lo spettro incrociato delle due metà", a, b),
                                ("rumore", "il rumore (mezza differenza delle due metà)", 0.5 * (a - b), None),
                                ("automatico", "lo spettro della mappa media (con il rumore)", 0.5 * (a + b), None)):
        if ferma is not None and ferma.is_set():
            raise Interrotto()
        log(f"  calcolo {testo} (ℓ fino a {lmax})…")
        t0 = time.time()
        cl = hp.anafast(x, y, lmax=lmax, iter=1) if y is not None else hp.anafast(x, lmax=lmax, iter=1)
        out[chiave] = np.asarray(cl, dtype=float)[:lmax + 1] / w2 / finestra ** 2
        log(f"    fatto in {time.time() - t0:.0f} s")
    out.update({"w2": w2, "fsky": fsky, "finestra": finestra})
    return out


# =============================================================================
# SPETTRO: D_ℓ, BIN, PICCHI, STIME
# =============================================================================
def d_da_c(ell, cl):
    ell = np.asarray(ell, dtype=float)
    return ell * (ell + 1) * np.asarray(cl) / (2 * math.pi)


def bin_spettro(cl_x, cl_n, fsky, larghezza=20, lmin_bin=30, incrociato=True):
    """Bin di D_ℓ: ℓ da 2 a lmin_bin−1 uno per uno, poi bin di «larghezza».
    Errori: varianza cosmica + rumore, ridotti dal cielo usato (f_sky)."""
    lmax = cl_x.size - 1
    bordi = list(range(2, lmin_bin)) + list(range(lmin_bin, lmax + 2, larghezza))
    if bordi[-1] < lmax + 1:
        bordi.append(lmax + 1)
    lb, db, sb, nb = [], [], [], []
    for lo, hi in zip(bordi[:-1], bordi[1:]):
        ell = np.arange(lo, min(hi, lmax + 1))
        if ell.size == 0:
            continue
        c = float(np.mean(cl_x[ell]))
        n = np.clip(cl_n[ell], 0, None) if cl_n is not None else np.zeros(ell.size)
        cpos = max(c, 0.0)
        if incrociato:
            var = (cpos ** 2 + (cpos + 2 * n) ** 2) / ((2 * ell + 1) * fsky)
        else:
            var = 2 * (cpos + n) ** 2 / ((2 * ell + 1) * fsky)
        fattore = ell * (ell + 1) / (2 * math.pi)
        lb.append(float(np.mean(ell)))
        db.append(float(np.mean(fattore * cl_x[ell])))
        sb.append(float(math.sqrt(np.sum((fattore ** 2) * var)) / ell.size))
        nb.append(ell.size)
    return {"ell": np.array(lb), "D": np.array(db), "err": np.array(sb), "n": np.array(nb)}


def _parabola(l, d, s):
    """Vertice di una parabola pesata: (ℓ, D) oppure None se non è un massimo dentro i dati."""
    if l.size < 3:
        return None
    x0 = float(np.mean(l))
    try:
        a, b, c = np.polyfit(l - x0, d, 2, w=1.0 / np.maximum(s, 1e-9))
    except (np.linalg.LinAlgError, ValueError):
        return None
    if not a < 0:
        return None
    xv = -b / (2 * a)
    if not (l.min() - x0 <= xv <= l.max() - x0):
        return None
    return xv + x0, c - b * b / (4 * a)


def trova_picco(ell, D, err, lo, hi, n_mc=300, seme=0):
    """Picco nella zona [lo, hi]: parabola sui punti vicini al massimo; errori Monte Carlo."""
    ell, D = np.asarray(ell, float), np.asarray(D, float)
    senza_errori = err is None
    err = np.ones_like(D) if senza_errori else np.asarray(err, float)
    sel = (ell >= lo) & (ell <= hi) & np.isfinite(D)
    if sel.sum() < 4:
        return None
    l, d, s = ell[sel], D[sel], err[sel]
    if d.size > 15:                     # dati fitti (spettro non binnato): si liscia prima di cercare il massimo
        k = max(3, d.size // 15)
        dl = np.convolve(d, np.ones(k) / k, mode="same")
    else:
        dl = d
    i = int(np.argmax(dl))
    semi = max(45.0, 0.16 * l[i])
    vic = np.abs(l - l[i]) <= semi
    if vic.sum() < 3:
        return None
    v = _parabola(l[vic], d[vic], s[vic])
    if v is None:
        return None
    lv, hv = [], []
    if senza_errori or n_mc <= 0:
        el = eh = 0.0                    # modello teorico: niente errori
    else:
        rng = np.random.default_rng(seme)
        for _ in range(n_mc):
            p = _parabola(l[vic], d[vic] + rng.normal(0, s[vic]), s[vic])
            if p is not None:
                lv.append(p[0])
                hv.append(p[1])
        el = float(np.std(lv)) if len(lv) > 5 else float("nan")
        eh = float(np.std(hv)) if len(hv) > 5 else float("nan")
    return {"ell": float(v[0]), "D": float(v[1]), "e_ell": el, "e_D": eh,
            "campioni_ell": np.array(lv), "campioni_D": np.array(hv)}


def trova_picchi(ell, D, err=None, lmax=None, n_mc=300):
    out = []
    lmax = lmax or float(np.max(ell))
    for n, lo, hi in ZONE_PICCHI:
        if hi > lmax - 20:
            break
        p = trova_picco(ell, D, err, lo, hi, n_mc=n_mc, seme=n)
        if p:
            p["n"] = n
            out.append(p)
    return out


def hu_h2(omega_b, omega_m=OMEGA_M_H2_PLANCK, ns=NS_PLANCK):
    """Rapporto D_ℓ(2° picco)/D_ℓ(1° picco) (Hu, Fukugita, Zaldarriaga & Tegmark 2001, ApJ 549, 669)."""
    esp = 12.0 * omega_m ** 0.52
    return 0.925 * omega_m ** 0.18 * 2.4 ** (ns - 1) / (1 + (omega_b / 0.0164) ** esp) ** 0.2


def omega_b_da_h2(h2, omega_m=OMEGA_M_H2_PLANCK, ns=NS_PLANCK):
    lo, hi = 0.002, 0.10
    if not (hu_h2(hi, omega_m, ns) < h2 < hu_h2(lo, omega_m, ns)):
        return float("nan")
    for _ in range(60):
        mid = 0.5 * (lo + hi)
        if hu_h2(mid, omega_m, ns) > h2:
            lo = mid
        else:
            hi = mid
    return 0.5 * (lo + hi)


def stime(picchi, picchi_lcdm):
    """Angolo del 1° picco, curvatura (regola semplice) e materia ordinaria dal rapporto dei picchi."""
    out = {}
    p1 = next((p for p in picchi if p["n"] == 1), None)
    p2 = next((p for p in picchi if p["n"] == 2), None)
    q1 = next((p for p in picchi_lcdm if p["n"] == 1), None) if picchi_lcdm else None
    if p1:
        out["l1"], out["e_l1"] = p1["ell"], p1["e_ell"]
        out["angolo_gradi"] = 180.0 / p1["ell"]
        out["e_angolo_gradi"] = 180.0 * p1["e_ell"] / p1["ell"] ** 2
        l1_piatto = q1["ell"] if q1 else 220.0
        out["l1_piatto"] = l1_piatto
        out["l1_piatto_da_modello"] = bool(q1)
        om = (l1_piatto / p1["ell"]) ** 2
        out["omega_tot"] = om
        out["e_omega_tot"] = 2 * om * p1["e_ell"] / p1["ell"]
    if p1 and p2:
        h2 = p2["D"] / p1["D"]
        out["h2"] = h2
        n = min(p1["campioni_D"].size, p2["campioni_D"].size)
        if n > 20:
            hs = p2["campioni_D"][:n] / p1["campioni_D"][:n]
            out["e_h2"] = float(np.std(hs))
            wb = np.array([omega_b_da_h2(h) for h in hs])
            wb = wb[np.isfinite(wb)]
        else:
            out["e_h2"] = float("nan")
            wb = np.array([])
        out["omega_b_h2"] = omega_b_da_h2(h2)
        out["e_omega_b_h2"] = float(np.std(wb)) if wb.size > 20 else float("nan")
        out["omega_b"] = out["omega_b_h2"] / H_PLANCK ** 2
    return out


# =============================================================================
# 1.1: VALLI, SORGENTI RESIDUE, SIMULAZIONI
# =============================================================================
def trova_valli(ell, D, err, picchi, n_mc=200):
    """Minimi tra due picchi consecutivi (parabola rivolta in alto sui punti vicini al minimo)."""
    out = []
    for a, b in zip(picchi[:-1], picchi[1:]):
        lo, hi = a["ell"] + 0.15 * (b["ell"] - a["ell"]), b["ell"] - 0.15 * (b["ell"] - a["ell"])
        p = trova_picco(ell, -np.asarray(D, float), err, lo, hi, n_mc=n_mc, seme=100 + a["n"])
        if p:
            p["D"] = -p["D"]
            p["campioni_D"] = -p["campioni_D"]
            p["n"] = a["n"]
            out.append(p)
    return out


def sorgenti_residue(b, ufficiale, lmin=500):
    """Stima della luce residua di sorgenti puntiformi e galassie lontane nella mappa:
    D_dati − D_ufficiale = A·(ℓ/1000)² + c·D_ufficiale (ℓ ≥ lmin). Lo spettro ufficiale di Planck
    è già pulito da queste «foreground», la mappa no. Restituisce A, c, errori e lo spettro corretto."""
    if ufficiale is None or np.nanmax(b["ell"]) < 1500:
        return None          # sotto ℓ ≈ 1500 il termine ∝ ℓ² non si distingue da un fattore di scala
    lu, du = ufficiale["ell"], ufficiale["D"]
    sel = (b["ell"] >= lmin) & (b["ell"] <= lu.max()) & np.isfinite(b["D"]) & (b["err"] > 0)
    if sel.sum() < 8:
        return None
    rif = np.interp(b["ell"][sel], lu, du)
    X = np.vstack([(b["ell"][sel] / 1000.0) ** 2, rif]).T
    w = 1.0 / b["err"][sel]
    y = b["D"][sel] - rif
    coef, *_ = np.linalg.lstsq(X * w[:, None], y * w, rcond=None)
    cov = np.linalg.pinv((X * w[:, None]).T @ (X * w[:, None]))
    resid = (y - X @ coef) * w
    chi2 = float(np.sum(resid ** 2) / max(sel.sum() - 2, 1))
    err = np.sqrt(np.diag(cov) * max(chi2, 1.0))
    A, c = float(coef[0]), float(coef[1])
    corr = dict(b)
    # 1.2: si toglie solo la luce in più (∝ ℓ²). La differenza di scala c non si toglie: applicata a tutti gli ℓ
    # abbassava anche il 1° picco (−2,6 % nella 1.1), dove non era stata misurata.
    corr["D"] = b["D"] - A * (b["ell"] / 1000.0) ** 2
    corr["err"] = b["err"]
    return {"A": A, "e_A": float(err[0]), "c": c, "e_c": float(err[1]), "chi2": chi2, "lmin": lmin, "bin": corr}


def simulazioni(n_sim, D_modello_ell, D_modello, sp, w, nside, lmax, larghezza, log=nulla, ferma=None,
                avanzamento=None, seme=7):
    """Errori «veri»: n_sim cieli simulati dal modello ΛCDM con lo stesso fascio, gli stessi pixel, la stessa
    maschera e lo stesso rumore (stimato dalla mezza differenza), analizzati esattamente come i dati.
    Restituisce la dispersione di posizione e altezza dei picchi, di Ω_tot e di Ω_b h²."""
    hp = hp_()
    lsim = min(3 * nside - 1, lmax + 300)
    ell = np.arange(lsim + 1, dtype=float)
    D = np.interp(ell, D_modello_ell, D_modello, left=0, right=0)
    cl = np.zeros_like(ell)
    cl[2:] = 2 * math.pi * D[2:] / (ell[2:] * (ell[2:] + 1))
    fin = funzione_finestra(nside, lsim)
    cl_segnale = cl * fin ** 2
    n_full = np.zeros_like(ell)
    k = min(sp["rumore"].size, ell.size)
    n_full[:k] = np.clip(sp["rumore"][:k], 0, None) * (sp["finestra"][:k] ** 2 if sp["finestra"].size >= k else 1)
    if k < ell.size:
        n_full[k:] = n_full[k - 1]
    cl_rumore_meta = 2 * n_full                     # ogni metà ha il doppio del rumore della mappa intera
    w2 = float(np.mean(w ** 2))
    fsky = float(np.mean(w > 0))
    finestra = funzione_finestra(nside, lmax)
    picchi_s, valli_s, stime_s = [], [], []
    np.random.seed(seme)
    t0 = time.time()
    for i in range(n_sim):
        if ferma is not None and ferma.is_set():
            raise Interrotto()
        cielo = hp.synfast(cl_segnale, nside, lmax=lsim)
        m1 = cielo + hp.synfast(cl_rumore_meta, nside, lmax=lsim)
        m2 = cielo + hp.synfast(cl_rumore_meta, nside, lmax=lsim)
        a, b_ = _prepara(m1, w), _prepara(m2, w)
        clx = np.asarray(hp.anafast(a, b_, lmax=lmax, iter=1), dtype=float)[:lmax + 1] / w2 / finestra ** 2
        bb = bin_spettro(clx, n_full[:lmax + 1] / np.maximum(finestra ** 2, 1e-12), fsky, larghezza=larghezza)
        pk = trova_picchi(bb["ell"], bb["D"], bb["err"], lmax=lmax, n_mc=0)
        picchi_s.append({p["n"]: (p["ell"], p["D"]) for p in pk})
        valli_s.append({v["n"]: (v["ell"], v["D"]) for v in trova_valli(bb["ell"], bb["D"], bb["err"], pk, n_mc=0)})
        stime_s.append(stime(pk, None))
        if avanzamento:
            dt = (time.time() - t0) / (i + 1)
            avanzamento(i + 1, n_sim, f"Simulazione {i + 1} di {n_sim} (circa {dt * (n_sim - i - 1):.0f} s alla fine)…")
    out = {"n": n_sim, "picchi": {}, "valli": {}}
    for chiave, lista in (("picchi", picchi_s), ("valli", valli_s)):
        numeri_ = sorted({k for d in lista for k in d})
        for nn in numeri_:
            v = np.array([d[nn] for d in lista if nn in d])
            if len(v) >= max(3, n_sim // 2):
                out[chiave][nn] = {"e_ell": float(np.std(v[:, 0], ddof=1)), "e_D": float(np.std(v[:, 1], ddof=1)),
                                   "trovati": int(len(v))}
    for chiave in ("omega_tot", "omega_b_h2", "h2", "angolo_gradi"):
        v = np.array([s_[chiave] for s_ in stime_s if chiave in s_ and np.isfinite(s_[chiave])])
        if v.size >= 3:
            out["e_" + chiave] = float(np.std(v, ddof=1))
    log(f"  {n_sim} simulazioni in {time.time() - t0:.0f} s")
    return out


def applica_errori_simulati(picchi, valli, st, sim):
    """Sostituisce gli errori Monte Carlo (ottimisti) con quelli delle simulazioni, se più grandi."""
    if not sim:
        return
    for lista, chiave in ((picchi, "picchi"), (valli, "valli")):
        for p in lista:
            e = sim[chiave].get(p["n"])
            if e:
                p["e_ell_mc"], p["e_D_mc"] = p["e_ell"], p["e_D"]
                p["e_ell"] = max(p["e_ell"], e["e_ell"])
                p["e_D"] = max(p["e_D"], e["e_D"])
    for k in ("omega_tot", "omega_b_h2", "h2", "angolo_gradi"):
        if "e_" + k in sim and k in st:
            st["e_" + k + "_mc"] = st.get("e_" + k)
            st["e_" + k] = max(st.get("e_" + k) or 0, sim["e_" + k])
    if "e_omega_tot" in st and "e_l1" in st and picchi:
        st["e_l1"] = picchi[0]["e_ell"]


# =============================================================================
# DIPOLO E MAPPA DI MOLLWEIDE
# =============================================================================
def mappa_dipolo(nside):
    """Dipolo del moto del Sole (μK) in coordinate galattiche, RING."""
    hp = hp_()
    l, b = math.radians(DIPOLO_L), math.radians(DIPOLO_B)
    d = np.array([math.cos(b) * math.cos(l), math.cos(b) * math.sin(l), math.sin(b)])
    x, y, z = hp.pix2vec(nside, np.arange(12 * nside * nside))
    return (DIPOLO_UK * (d[0] * x + d[1] * y + d[2] * z)).astype(np.float32)


def griglia_mollweide(larghezza=1200):
    """θ, φ (radianti, convenzione HEALPix) per ogni punto dell'immagine; centro = centro galattico,
    longitudine che cresce verso SINISTRA come nelle carte del cielo."""
    h = larghezza // 2
    x = np.linspace(-2 * math.sqrt(2), 2 * math.sqrt(2), larghezza)
    y = np.linspace(math.sqrt(2), -math.sqrt(2), h)
    X, Y = np.meshgrid(x, y)
    dentro = X ** 2 / 8 + Y ** 2 / 2 <= 1
    t = np.arcsin(np.clip(Y / math.sqrt(2), -1, 1))
    lat = np.arcsin(np.clip((2 * t + np.sin(2 * t)) / math.pi, -1, 1))
    cos_t = np.where(np.abs(np.cos(t)) < 1e-9, 1e-9, np.cos(t))
    lon = math.pi * X / (2 * math.sqrt(2) * cos_t)
    theta = math.pi / 2 - lat
    phi = np.mod(-lon, 2 * math.pi)
    return theta[dentro], phi[dentro], dentro


def immagine_mollweide(mappa, larghezza=1200):
    hp = hp_()
    nside = hp.get_nside(mappa)
    theta, phi, dentro = griglia_mollweide(larghezza)
    img = np.full(dentro.shape, np.nan, dtype=np.float32)
    v = np.asarray(mappa)[hp.ang2pix(nside, theta, phi)].astype(np.float32)
    v[_cattivi(v)] = np.nan
    img[dentro] = v
    return img


def mappa_colori():
    """Scala di colori originale per la CMB: blu freddo – crema – arancio – rosso scuro."""
    from matplotlib.colors import LinearSegmentedColormap
    cm = LinearSegmentedColormap.from_list("cmb_note", [
        (0.00, "#0b1f5c"), (0.18, "#1f5fb4"), (0.35, "#7fb6e6"), (0.50, "#fbf3dc"),
        (0.65, "#f6b25a"), (0.82, "#d9541e"), (1.00, "#6b0f0f")])
    cm.set_bad("#d8d8d8")
    return cm


# =============================================================================
# NOTE MUSICALI E SUONO
# =============================================================================
NOMI_NOTE = ["C", "C#", "D", "D#", "E", "F", "F#", "G", "G#", "A", "A#", "B"]
NOMI_NOTE_IT = ["Do", "Do#", "Re", "Re#", "Mi", "Fa", "Fa#", "Sol", "Sol#", "La", "La#", "Si"]


def nota(freq_hz, italiano=False):
    """Nota più vicina: (nome, scarto in centesimi). Ottave all'inglese (La a 440 Hz = A4)."""
    if not freq_hz or freq_hz <= 0:
        return "?", 0.0
    midi = 69 + 12 * math.log2(freq_hz / 440.0)
    n = int(round(midi))
    cent = 100 * (midi - n)
    nomi = NOMI_NOTE_IT if italiano else NOMI_NOTE
    return f"{nomi[n % 12]}{n // 12 - 1}", cent


def suono(ell, D, picchi, percorso, durata_coro=7.0, durata_note=8.0, frequenza=44100):
    """WAV: (1) «il coro» – rumore con lo spettro D_ℓ (ℓ diventa una frequenza in Hz: il primo
    picco a ℓ≈220 suona come un La a 220 Hz); (2) «le note» – i picchi uno dopo l'altro, poi insieme."""
    ell = np.asarray(ell, float)
    D = np.clip(np.nan_to_num(np.asarray(D, float)), 0, None)
    rng = np.random.default_rng(42)
    n = int(durata_coro * frequenza)
    f = np.fft.rfftfreq(n, 1.0 / frequenza)
    amp = np.interp(f, ell, np.sqrt(D), left=0.0, right=0.0)
    amp[f < 20] = 0
    spettro = amp * np.exp(2j * math.pi * rng.random(f.size))
    coro = np.fft.irfft(spettro, n)
    coro /= max(np.max(np.abs(coro)), 1e-12)
    rampa = int(0.4 * frequenza)
    inv = np.ones(n)
    inv[:rampa] = np.linspace(0, 1, rampa)
    inv[-rampa:] = np.linspace(1, 0, rampa)
    coro *= inv * 0.8
    # le note: ogni picco 0,9 s, poi l'accordo per il resto
    note = np.zeros(int(durata_note * frequenza))
    if picchi:
        hmax = max(p["D"] for p in picchi)
        t1 = np.arange(int(0.9 * frequenza)) / frequenza
        inv1 = np.minimum(1, t1 / 0.03) * np.exp(-t1 / 0.6)
        pos = 0
        for p in picchi:
            a = math.sqrt(max(p["D"], 0) / hmax)
            tono = a * (np.sin(2 * math.pi * p["ell"] * t1) + 0.25 * np.sin(4 * math.pi * p["ell"] * t1)) * inv1
            fine = min(pos + tono.size, note.size)
            note[pos:fine] += tono[:fine - pos]
            pos += int(0.95 * frequenza)
        resto = note.size - pos
        if resto > frequenza:
            t2 = np.arange(resto) / frequenza
            inv2 = np.minimum(1, t2 / 0.05) * np.exp(-t2 / (resto / frequenza / 2.5))
            acc = sum(math.sqrt(max(p["D"], 0) / hmax) * np.sin(2 * math.pi * p["ell"] * t2) for p in picchi)
            note[pos:] += acc * inv2
        note /= max(np.max(np.abs(note)), 1e-12)
        note *= 0.8
    pausa = np.zeros(int(0.6 * frequenza))
    tutto = np.concatenate([coro, pausa, note])
    dati = (np.clip(tutto, -1, 1) * 32000).astype("<i2")
    with wave.open(str(percorso), "wb") as w:
        w.setnchannels(1)
        w.setsampwidth(2)
        w.setframerate(frequenza)
        w.writeframes(dati.tobytes())
    return {"durata_s": tutto.size / frequenza, "coro_s": durata_coro, "note_s": durata_note}


# =============================================================================
# GRAFICI E SCHEDA (in inglese)
# =============================================================================
def _limite_colori(img, perc=99.5):
    v = img[np.isfinite(img)]
    if v.size == 0:
        return 1.0
    return float(np.percentile(np.abs(v), perc)) or 1.0


def disegna_mappa(ax, ris, modo="cmb", con_maschera=False, titoli_inglese=True):
    """modo: cmb | cmb_dipolo | dipolo. Immagini precalcolate in ris['immagini']."""
    img = ris["immagini"][modo].copy()
    if con_maschera and "maschera" in ris["immagini"]:
        img[ris["immagini"]["maschera"] <= 0] = np.nan
    lim = _limite_colori(img) if modo == "cmb" else DIPOLO_UK * 1.02
    im = ax.imshow(img, cmap=mappa_colori(), vmin=-lim, vmax=lim, interpolation="nearest", aspect="equal")
    ax.set_axis_off()
    t = {"cmb": ("CMB temperature (dipole removed)", "Temperatura della CMB (senza dipolo)"),
         "cmb_dipolo": ("CMB + solar dipole (as seen by a moving observer)", "CMB + dipolo del Sole (come la vede chi si muove)"),
         "dipolo": ("Solar dipole only (±3.36 mK)", "Solo il dipolo del Sole (±3,36 mK)")}[modo]
    titolo = t[0] if titoli_inglese else t[1]
    if con_maschera:
        titolo += " – masked" if titoli_inglese else " – con la maschera"
    ax.set_title(titolo)
    return im


def disegna_spettro(ax, ris, inglese=True, residui_ax=None):
    b = ris["bin"]
    lmax = ris["lmax"]
    if ris.get("lcdm") is not None:
        L = ris["lcdm"]
        sel = L["ell"] <= lmax
        ax.plot(L["ell"][sel], L["D"][sel], color="#b03a2e", lw=1.2, label="Planck best-fit ΛCDM model" if inglese
                else "modello ΛCDM migliore di Planck", zorder=3)
    if ris.get("ufficiale") is not None:
        U = ris["ufficiale"]
        sel = U["ell"] <= lmax
        ax.errorbar(U["ell"][sel], U["D"][sel], yerr=U.get("err", None)[sel] if U.get("err") is not None else None,
                    fmt="s", ms=2.5, color="#7f8c8d", elinewidth=0.6, alpha=0.8,
                    label="official Planck 2018 spectrum (binned)" if inglese else "spettro ufficiale Planck 2018 (binnato)",
                    zorder=2)
    if ris.get("automatico_bin") is not None and ris.get("mostra_rumore", True):
        a = ris["automatico_bin"]
        ax.plot(a["ell"], a["D"], color="#95a5a6", lw=0.8, ls=":", label="this work, without noise removal"
                if inglese else "questo lavoro senza togliere il rumore", zorder=1)
    if ris.get("sorgenti_tolte") and ris.get("bin_grezzo") is not None:
        g = ris["bin_grezzo"]
        ax.plot(g["ell"], g["D"], color="#6c8ebf", lw=0.9, ls="--", alpha=0.8, zorder=3,
                label="this work before removing residual point sources" if inglese
                else "questo lavoro prima di togliere le sorgenti residue")
    etichetta = ("this work: half-mission cross-spectrum" if inglese else "questo lavoro: spettro incrociato delle due metà")
    if ris.get("sorgenti_tolte"):
        etichetta += (", residual sources removed" if inglese else ", sorgenti residue tolte")
    ax.errorbar(b["ell"], b["D"], yerr=b["err"], fmt="o", ms=3, color="#1f4e9c", elinewidth=0.8, label=etichetta,
                zorder=4)
    for p in ris.get("picchi", []):
        ax.axvline(p["ell"], color="#d4a017", lw=0.8, ls="--", zorder=0)
        ax.annotate(f"{p['n']}\nℓ≈{p['ell']:.0f}", (p["ell"], p["D"]), textcoords="offset points", xytext=(0, 8),
                    ha="center", fontsize="small", color="#8a6d00")
    for v in ris.get("valli", []):
        ax.plot([v["ell"]], [v["D"]], marker="v", ms=6, color="#2e8b57", zorder=5, ls="none")
    if ris.get("valli"):
        ax.plot([], [], marker="v", ms=6, color="#2e8b57", ls="none", label="troughs" if inglese else "valli")
    ax.set_xlim(2, lmax)
    top = max(np.nanmax(b["D"][b["ell"] > 50]) if np.any(b["ell"] > 50) else 6000, 1000) * 1.6   # spazio per la legenda
    ax.set_ylim(-300, top)
    ax.set_xlabel("multipole ℓ  (angle ≈ 180°/ℓ)" if inglese else "multipolo ℓ  (angolo ≈ 180°/ℓ)")
    ax.set_ylabel("D_ℓ = ℓ(ℓ+1)C_ℓ/2π  [μK²]" if inglese else "D_ℓ  [μK²]")
    ax.legend(loc="upper right", fontsize="small", ncol=2, framealpha=0.85)
    ax.grid(alpha=0.25)
    if residui_ax is not None and ris.get("lcdm") is not None:
        L = ris["lcdm"]
        mod = np.interp(b["ell"], L["ell"], L["D"])
        ok = mod > 50
        residui_ax.errorbar(b["ell"][ok], (b["D"][ok] - mod[ok]), yerr=b["err"][ok], fmt="o", ms=2, color="#1f4e9c",
                            elinewidth=0.6)
        residui_ax.axhline(0, color="#b03a2e", lw=1)
        alti = ok & (b["ell"] >= 30)
        if np.any(alti):
            r = np.abs(b["D"][alti] - mod[alti]) + b["err"][alti]
            lim = max(float(np.percentile(r, 95)) * 1.3, 50.0)
            residui_ax.set_ylim(-lim, lim)
        residui_ax.set_xlim(2, lmax)
        residui_ax.set_ylabel("data − ΛCDM" if inglese else "dati − ΛCDM")
        residui_ax.set_xlabel("multipole ℓ" if inglese else "multipolo ℓ")
        residui_ax.grid(alpha=0.25)


def disegna_note(ax, ris, inglese=True):
    """Lo spettro come suono: ℓ = frequenza in Hz, con il nome delle note dei picchi."""
    b = ris["bin"]
    ax.fill_between(b["ell"], 0, np.clip(b["D"], 0, None), color="#f0c75e", alpha=0.6, step="mid")
    ax.plot(b["ell"], np.clip(b["D"], 0, None), color="#8a6d00", lw=1)
    for p in ris.get("picchi", []):
        nm, cent = nota(p["ell"], italiano=not inglese)
        ax.annotate(f"{nm}\n{p['ell']:.0f} Hz", (p["ell"], p["D"]), textcoords="offset points", xytext=(0, 6),
                    ha="center", fontsize="small", color="#5b3a00", fontweight="bold")
    ax.set_xlim(20, ris["lmax"])
    ax.set_ylim(0, max(np.nanmax(b["D"]) * 1.3, 1))
    ax.set_xlabel("sound frequency [Hz] = multipole ℓ" if inglese else "frequenza del suono [Hz] = multipolo ℓ")
    ax.set_ylabel("loudness ∝ D_ℓ" if inglese else "intensità ∝ D_ℓ")
    ax.set_title("The notes of the Big Bang" if inglese else "Le note del Big Bang")
    ax.grid(alpha=0.25)


def _f(v, cifre=1):
    if v is None or not np.isfinite(v):
        return "n/a"
    return f"{v:.{cifre}f}"


def testo_risultati_inglese(ris):
    s = ris.get("stime", {})
    r = [f"Map: Planck PR3 {ris['mappa']} half-missions (hm1 × hm2), working Nside {ris['nside']}, ℓ ≤ {ris['lmax']}",
         f"Mask: Planck common temperature mask, apodised {ris['apo']:.1f}°, sky used f_sky = {ris['fsky']:.3f}"]
    sr = ris.get("sorgenti")
    if sr:
        r.append(f"Residual point sources/CIB in the map (fit to the official foreground-cleaned spectrum, ℓ ≥ {sr['lmin']}): "
                 f"ΔD = {sr['A']:.1f} ± {sr['e_A']:.1f} μK² × (ℓ/1000)²"
                 + (" – removed" if ris.get("sorgenti_tolte") else " – not removed")
                 + f"; overall scale difference {100 * sr['c']:+.1f} ± {100 * sr['e_c']:.1f} % (not removed)")
    elif ris.get("lmax", 0) < 1500:
        r.append("Residual point sources: not estimated (needs ℓ up to at least 1500, i.e. Nside ≥ 1024)")
    sim = ris.get("simulazioni")
    r.append(f"Errors from {sim['n']} simulated skies (same mask, beam and noise)" if sim
             else "Errors from Monte Carlo of the bins only (optimistic: no simulations)")
    for p in ris.get("picchi", []):
        nm, cent = nota(p["ell"])
        r.append(f"Peak {p['n']}: ℓ = {p['ell']:.1f} ± {_f(p['e_ell'])}, D_ℓ = {p['D']:.0f} ± {_f(p['e_D'], 0)} μK²"
                 f"   (note {nm}, {cent:+.0f} cents)")
    if "angolo_gradi" in s:
        r.append(f"Angular size of the first peak: 180°/ℓ₁ = {s['angolo_gradi']:.3f}° ± {s['e_angolo_gradi']:.3f}°")
        r.append(f"Geometry (rule of thumb Ω_tot ≈ (ℓ₁,flat/ℓ₁)², ℓ₁,flat = {s['l1_piatto']:.1f} "
                 + ("from the ΛCDM model): " if s.get("l1_piatto_da_modello") else "standard value): ")
                 + f"Ω_tot ≈ {s['omega_tot']:.3f} ± {s['e_omega_tot']:.3f}  → "
                 + ("consistent with a FLAT universe" if abs(s["omega_tot"] - 1) < max(2 * s["e_omega_tot"], 0.05)
                    else "not exactly flat by this simple rule (see notes)"))
    if "omega_b_h2" in s:
        r.append(f"Second/first peak height ratio H₂ = {s['h2']:.3f} ± {_f(s.get('e_h2'), 3)}  → ordinary matter "
                 f"ω_b = Ω_b h² ≈ {_f(s['omega_b_h2'], 4)} ± {_f(s.get('e_omega_b_h2'), 4)} "
                 f"(Planck 2018: {OMEGA_B_H2_PLANCK}); Ω_b ≈ {_f(100 * s['omega_b'], 1)} % for h = {H_PLANCK}")
    if ris.get("valli"):
        r.append("Troughs: " + ", ".join(f"ℓ = {v['ell']:.0f} ± {_f(v['e_ell'], 0)}" for v in ris["valli"]))
    g = ris.get("stime_grezze")
    if g and "omega_b_h2" in g:
        r.append(f"Without removing the residual sources: ℓ₁ = {g.get('l1', float('nan')):.1f}, Ω_tot ≈ {g['omega_tot']:.3f}, "
                 f"H₂ = {g['h2']:.3f}, Ω_b h² ≈ {g['omega_b_h2']:.4f}")
    if ris.get("picchi_ufficiali"):
        r.append("Same method on the official Planck binned spectrum: "
                 + ", ".join(f"ℓ{p['n']} = {p['ell']:.0f}" for p in ris["picchi_ufficiali"]))
    if ris.get("picchi_lcdm"):
        r.append("Same method on the ΛCDM model: " + ", ".join(f"ℓ{p['n']} = {p['ell']:.0f}" for p in ris["picchi_lcdm"]))
    return r


def testo_risultati_italiano(ris):
    s = ris.get("stime", {})
    r = [f"Mappa {ris['mappa']} (metà missione 1 × 2), Nside {ris['nside']}, ℓ fino a {ris['lmax']}; "
         f"cielo usato {100 * ris['fsky']:.1f} %"]
    sr = ris.get("sorgenti")
    if sr:
        r.append(f"Luce residua di sorgenti e galassie lontane: {sr['A']:.1f} ± {sr['e_A']:.1f} μK² a ℓ = 1000 "
                 f"(cresce come ℓ²)" + (" – tolta" if ris.get("sorgenti_tolte") else " – non tolta")
                 + f"; differenza di scala {100 * sr['c']:+.1f} % (lasciata)")
    elif ris.get("lmax", 0) < 1500:
        r.append("Sorgenti residue non stimate: serve ℓ fino ad almeno 1500 (Nside 1024 o 2048)")
    sim = ris.get("simulazioni")
    r.append(f"Errori da {sim['n']} cieli simulati" if sim else "Errori senza simulazioni (ottimisti)")
    for p in ris.get("picchi", []):
        nm, _ = nota(p["ell"], italiano=True)
        r.append(f"Picco {p['n']}: ℓ = {p['ell']:.0f} ± {_f(p['e_ell'], 0)} (circa {180 / p['ell']:.2f}°), "
                 f"D = {p['D']:.0f} μK², nota {nm} ({p['ell']:.0f} Hz)")
    if "omega_tot" in s:
        r.append(f"Geometria: Ω totale ≈ {s['omega_tot']:.3f} ± {s['e_omega_tot']:.3f} "
                 + ("→ universo piatto" if abs(s["omega_tot"] - 1) < max(2 * s["e_omega_tot"], 0.05) else "→ da controllare"))
    if "omega_b_h2" in s:
        r.append(f"Materia ordinaria dal rapporto dei picchi: Ω_b h² ≈ {_f(s['omega_b_h2'], 4)} ± "
                 f"{_f(s.get('e_omega_b_h2'), 4)} (Planck {OMEGA_B_H2_PLANCK}), cioè circa "
                 f"{_f(100 * s['omega_b'], 1)} % dell'universo")
    if ris.get("valli"):
        r.append("Valli: " + ", ".join(f"ℓ = {v['ell']:.0f}" for v in ris["valli"]))
    return r


STILE_SCHEDA = {"font.size": 10.0, "lines.markersize": 6.0}


def scheda(ris, cartella):
    """Scheda A4 orizzontale in inglese (misure fisse, qualunque sia la dimensione del testo della finestra)."""
    import matplotlib
    with matplotlib.rc_context(STILE_SCHEDA):
        return _scheda(ris, cartella)


def _scheda(ris, cartella):
    import matplotlib
    matplotlib.use("Agg", force=False)
    from matplotlib.figure import Figure
    from matplotlib.backends.backend_agg import FigureCanvasAgg
    from matplotlib.backends.backend_pdf import PdfPages
    fig = Figure(figsize=(16.54, 11.69), dpi=110)
    FigureCanvasAgg(fig)
    g = fig.add_gridspec(3, 3, height_ratios=[1.15, 1.0, 0.75], width_ratios=[1.05, 1, 1], hspace=0.42, wspace=0.28,
                         left=0.05, right=0.98, top=0.92, bottom=0.075)
    fig.suptitle(f"The notes of the Big Bang – acoustic peaks of the CMB from Planck  ({ris['mappa']}, Nside {ris['nside']})",
                 fontsize=15, fontweight="bold")
    a1 = fig.add_subplot(g[0, 0])
    im = disegna_mappa(a1, ris, "cmb", con_maschera=False)
    fig.colorbar(im, ax=a1, orientation="horizontal", fraction=0.05, pad=0.03, label="ΔT [μK]")
    a1b = fig.add_subplot(g[1, 0])
    disegna_mappa(a1b, ris, "cmb", con_maschera=True)
    a2 = fig.add_subplot(g[0:2, 1:])
    a3 = fig.add_subplot(g[2, 1])
    disegna_spettro(a2, ris, residui_ax=a3)
    a2.set_title("Angular power spectrum of the CMB temperature")
    a4 = fig.add_subplot(g[2, 2])
    disegna_note(a4, ris)
    a5 = fig.add_subplot(g[2, 0])
    a5.set_axis_off()
    righe = testo_risultati_inglese(ris)
    a5.text(0.0, 1.0, "\n".join(_spezza(x, 78) for x in righe), va="top", ha="left", fontsize=7.6, family="DejaVu Sans",
            transform=a5.transAxes)
    fig.text(0.98, 0.006, f"Data: Planck PR3 (ESA PLA / NASA IRSA). Generated {datetime.now():%Y-%m-%d %H:%M} with "
             f"Le note del Big Bang {VERSIONE}.  {FIRMA}", ha="right", fontsize=8, color="#555555")
    fig.text(0.5, 0.5, FIRMA, fontsize=60, color="#000000", alpha=0.035, ha="center", va="center", rotation=25)
    cartella = Path(cartella)
    pdf, png = cartella / "note_big_bang_sheet.pdf", cartella / "note_big_bang_sheet.png"
    with PdfPages(pdf) as pp:
        pp.savefig(fig)
    fig.savefig(png, dpi=110)
    # mappa grande a parte
    f2 = Figure(figsize=(12, 6.6), dpi=150)
    FigureCanvasAgg(f2)
    ax = f2.add_axes([0.02, 0.12, 0.96, 0.8])
    im = disegna_mappa(ax, ris, "cmb")
    cax = f2.add_axes([0.3, 0.07, 0.4, 0.03])
    f2.colorbar(im, cax=cax, orientation="horizontal", label="ΔT [μK]")
    f2.text(0.98, 0.01, f"Planck PR3 {ris['mappa']} – {FIRMA}", ha="right", fontsize=8, color="#555555")
    f2.savefig(cartella / "cmb_map.png", dpi=150)
    return pdf, png


def _spezza(testo, larghezza):
    parole, righe, r = testo.split(), [], ""
    for p in parole:
        if len(r) + len(p) + 1 > larghezza and r:
            righe.append(r)
            r = "   " + p
        else:
            r = (r + " " + p) if r else p
    if r:
        righe.append(r)
    return "\n".join(righe)


def salva_tabelle(ris, cartella):
    cartella = Path(cartella)
    b = ris["bin"]
    with open(cartella / "power_spectrum_binned.csv", "w", encoding="utf-8") as f:
        f.write("# The notes of the Big Bang – binned TT power spectrum from Planck PR3 half-mission cross-spectrum\n")
        f.write(f"# map {ris['mappa']}, Nside {ris['nside']}, fsky {ris['fsky']:.4f}, beam {FWHM_ARCMIN} arcmin – {FIRMA}\n")
        g = ris.get("bin_grezzo") or b
        f.write(f"# residual sources removed: {bool(ris.get('sorgenti_tolte'))}\n")
        f.write("ell_mean,D_ell_uK2,error_uK2,n_multipoles,D_ell_raw_uK2\n")
        for x in zip(b["ell"], b["D"], b["err"], b["n"], g["D"]):
            f.write(f"{x[0]:.1f},{x[1]:.3f},{x[2]:.3f},{int(x[3])},{x[4]:.3f}\n")
    with open(cartella / "power_spectrum_unbinned.csv", "w", encoding="utf-8") as f:
        f.write("ell,Cl_cross_uK2,Cl_noise_uK2,Cl_auto_uK2,D_cross_uK2\n")
        for l in range(2, ris["lmax"] + 1):
            f.write(f"{l},{ris['cl']['incrociato'][l]:.6e},{ris['cl']['rumore'][l]:.6e},{ris['cl']['automatico'][l]:.6e},"
                    f"{d_da_c(l, ris['cl']['incrociato'][l]):.4f}\n")
    pulito = {"program": f"Le note del Big Bang {VERSIONE}", "signature": FIRMA, "date": datetime.now().isoformat(timespec="seconds"),
              "map": ris["mappa"], "nside": ris["nside"], "lmax": ris["lmax"], "fsky": ris["fsky"], "apodisation_deg": ris["apo"],
              "bin_width": ris["larghezza_bin"],
              "peaks": [{k: v for k, v in p.items() if not k.startswith("campioni")} for p in ris.get("picchi", [])],
              "peaks_official": [{k: v for k, v in p.items() if not k.startswith("campioni")} for p in ris.get("picchi_ufficiali", [])],
              "peaks_lcdm": [{k: v for k, v in p.items() if not k.startswith("campioni")} for p in ris.get("picchi_lcdm", [])],
              "troughs": [{k: v for k, v in p.items() if not k.startswith("campioni")} for p in ris.get("valli", [])],
              "residual_sources": ({k: v for k, v in ris["sorgenti"].items() if k != "bin"} if ris.get("sorgenti") else None),
              "residual_sources_removed": bool(ris.get("sorgenti_tolte")),
              "simulations": ris.get("simulazioni"),
              "estimates_without_source_removal": ris.get("stime_grezze"),
              "estimates": ris.get("stime", {}), "files": ris.get("file", {})}
    (cartella / "results.json").write_text(json.dumps(pulito, indent=1, ensure_ascii=False, default=float), encoding="utf-8")


# =============================================================================
# ANALISI COMPLETA
# =============================================================================
def file_necessari(mappa):
    modello = MAPPE[mappa][1]
    return [modello.format(parte="hm1"), modello.format(parte="hm2"), MASCHERA]


def scarica_tutto(mappa, log=nulla, avanzamento=nulla, ferma=None, cartella=None):
    percorsi = {}
    for i, nome in enumerate(file_necessari(mappa)):
        log(f"File {i + 1}/3: {nome}")
        percorsi[nome] = scarica_file(nome, cartella, log=log, avanzamento=avanzamento, ferma=ferma)
    for nome in (SPETTRO_BINNATO, SPETTRO_LCDM, SPETTRO_TT):
        try:
            percorsi[nome] = scarica_file(nome, cartella, log=log, avanzamento=avanzamento, ferma=ferma, max_tentativi=3)
        except Interrotto:
            raise
        except Exception as e:
            log(f"  (spettro ufficiale {nome} non disponibile: {e}; continuo senza)")
    return percorsi


def analizza(opz, log=nulla, avanzamento=nulla, ferma=None):
    """opz: mappa, nside, apo (gradi), bin (larghezza), cartella (risultati), dati (cartella dei file Planck),
    suono (bool), sorgenti (bool: togli le sorgenti residue), simulazioni (numero di cieli simulati per gli
    errori). Restituisce il dizionario dei risultati."""
    hp = hp_()
    mappa = opz.get("mappa", "smica-nosz")
    nside = int(opz.get("nside", 1024))
    lmax = int(opz.get("lmax") or RISOLUZIONI.get(nside, 2 * nside))
    apo = float(opz.get("apo", 1.0))
    larghezza = int(opz.get("bin", 20))
    t_inizio = time.time()
    log(f"Le note del Big Bang {VERSIONE}: mappa {mappa}, Nside {nside}, ℓ ≤ {lmax}")
    avanzamento(0, 10, "Scarico i file Planck (solo la prima volta)…")
    percorsi = opz.get("percorsi") or scarica_tutto(mappa, log, avanzamento, ferma, opz.get("dati"))
    n1, n2, nm = file_necessari(mappa)

    avanzamento(4, 10, "Preparo le mappe…")
    m1 = mappa_ridotta(percorsi[n1], nside, log)
    m2 = mappa_ridotta(percorsi[n2], nside, log)
    frazione = maschera_ridotta(percorsi[nm], nside, log)
    w = finestra_apodizzata(frazione, nside, apo, log)
    w = w * (~_cattivi(m1)) * (~_cattivi(m2))
    if ferma is not None and ferma.is_set():
        raise Interrotto()

    avanzamento(5, 10, "Calcolo lo spettro di potenza (può richiedere qualche minuto)…")
    sp = spettri(m1, m2, w, nside, lmax, log, ferma)
    fsky = sp["fsky"]
    ell = np.arange(lmax + 1)

    avanzamento(8, 10, "Cerco i picchi acustici…")
    b_grezzo = bin_spettro(sp["incrociato"], sp["rumore"], fsky, larghezza=larghezza)
    ba = bin_spettro(sp["automatico"], None, fsky, larghezza=larghezza, incrociato=False)
    ufficiale = lcdm = None
    picchi_uff, picchi_lcdm = [], []
    if SPETTRO_BINNATO in percorsi:
        try:
            ufficiale = leggi_spettro_ufficiale(percorsi[SPETTRO_BINNATO])
            picchi_uff = trova_picchi(ufficiale["ell"], ufficiale["D"], ufficiale.get("err"), lmax=lmax)
        except Exception as e:
            log(f"  (spettro ufficiale non letto: {e})")
    if SPETTRO_LCDM in percorsi:
        try:
            lcdm = leggi_lcdm(percorsi[SPETTRO_LCDM])
            picchi_lcdm = trova_picchi(lcdm["ell"], lcdm["D"], None, lmax=lmax, n_mc=0)
        except Exception as e:
            log(f"  (modello ΛCDM non letto: {e})")
    # luce residua di sorgenti puntiformi e galassie lontane (stimata contro lo spettro ufficiale pulito)
    sr = sorgenti_residue(b_grezzo, ufficiale)
    if sr:
        log(f"  sorgenti residue: D = {sr['A']:.1f} ± {sr['e_A']:.1f} μK² × (ℓ/1000)², scala {100 * sr['c']:+.1f} % (lasciata)")
    usa_corr = bool(opz.get("sorgenti", True) and sr)
    b = sr["bin"] if usa_corr else b_grezzo
    picchi = trova_picchi(b["ell"], b["D"], b["err"], lmax=lmax)
    valli = trova_valli(b["ell"], b["D"], b["err"], picchi)
    picchi_grezzi = trova_picchi(b_grezzo["ell"], b_grezzo["D"], b_grezzo["err"], lmax=lmax) if usa_corr else picchi
    st = stime(picchi, picchi_lcdm)
    st_grezzo = stime(picchi_grezzi, picchi_lcdm) if usa_corr else None
    sim = None
    n_sim = int(opz.get("simulazioni", 0) or 0)
    if n_sim > 0 and lcdm is not None:
        avanzamento(0, n_sim, f"Simulo {n_sim} cieli per gli errori…")
        log(f"  {n_sim} cieli simulati per gli errori (stessa maschera, fascio e rumore)…")
        sim = simulazioni(n_sim, lcdm["ell"], lcdm["D"], sp, w, nside, lmax, larghezza, log, ferma,
                          avanzamento=avanzamento)
        applica_errori_simulati(picchi, valli, st, sim)
    elif n_sim > 0:
        log("  (simulazioni saltate: manca il modello ΛCDM)")

    avanzamento(9, 10, "Disegno la mappa, la scheda e il suono…")
    nside_vista = min(nside, 512)
    media = 0.5 * (m1.astype(np.float64) + m2.astype(np.float64))
    media[_cattivi(m1) | _cattivi(m2)] = hp.UNSEEN
    vista = hp.ud_grade(media, nside_vista, pess=False) if nside_vista != nside else media
    vista = np.asarray(vista, dtype=np.float64)
    cattivi_v = _cattivi(vista)
    vv = vista.copy()
    vv[cattivi_v] = hp.UNSEEN
    vv = np.asarray(hp.remove_dipole(vv, bad=hp.UNSEEN), dtype=np.float64)   # monopolo/dipolo residui
    vv[cattivi_v] = hp.UNSEEN
    dip = mappa_dipolo(nside_vista)
    con_dip = vv.copy()
    con_dip[~cattivi_v] += dip[~cattivi_v]
    w_vista = hp.ud_grade(w, nside_vista) if nside_vista != nside else w
    immagini = {"cmb": immagine_mollweide(vv), "cmb_dipolo": immagine_mollweide(con_dip),
                "dipolo": immagine_mollweide(dip), "maschera": immagine_mollweide(np.asarray(w_vista))}

    cartella = Path(opz.get("cartella") or cartella_risultati_predefinita())
    sotto = cartella / f"{mappa}_n{nside}_{datetime.now():%Y%m%d-%H%M}"
    sotto.mkdir(parents=True, exist_ok=True)
    ris = {"mappa": mappa, "nside": nside, "lmax": lmax, "apo": apo, "larghezza_bin": larghezza, "fsky": fsky,
           "cl": sp, "ell": ell, "bin": b, "bin_grezzo": b_grezzo, "automatico_bin": ba, "picchi": picchi,
           "valli": valli, "picchi_grezzi": picchi_grezzi, "stime_grezze": st_grezzo, "sorgenti": sr,
           "sorgenti_tolte": usa_corr, "simulazioni": sim, "picchi_ufficiali": picchi_uff,
           "picchi_lcdm": picchi_lcdm, "ufficiale": ufficiale, "lcdm": lcdm, "stime": st, "immagini": immagini,
           "cartella": sotto, "file": {k: str(v) for k, v in percorsi.items()}}
    if opz.get("suono", True):
        D = d_da_c(ell, sp["incrociato"])
        D[:2] = 0
        ris["wav"] = sotto / "notes_of_the_big_bang.wav"
        ris["suono"] = suono(ell, D, picchi, ris["wav"])
    ris["pdf"], ris["png"] = scheda(ris, sotto)
    salva_tabelle(ris, sotto)
    avanzamento(10, 10, f"Fatto in {time.time() - t_inizio:.0f} s.")
    log(f"Risultati in {sotto}")
    return ris


# =============================================================================
# 1.1: IL CIELO IN 9 FREQUENZE (Via Lattea e «foreground»)
# =============================================================================
# (GHz, file, unità, MB, descrizione inglese, descrizione italiana)
FREQUENZE = [
    (30, "LFI_SkyMap_030_1024_R3.00_full.fits", "K_CMB", 503, "synchrotron and free-free from the Milky Way",
     "sincrotrone e gas ionizzato della Via Lattea"),
    (44, "LFI_SkyMap_044_1024_R3.00_full.fits", "K_CMB", 503, "synchrotron fading, CMB appears",
     "il sincrotrone cala, appare la CMB"),
    (70, "LFI_SkyMap_070_1024_R3.00_full.fits", "K_CMB", 503, "near the foreground minimum",
     "vicino al minimo delle «foreground»"),
    (100, "HFI_SkyMap_100_2048_R3.01_full.fits", "K_CMB", 2013, "CMB dominates, CO line in the plane",
     "domina la CMB; riga del CO sul piano"),
    (143, "HFI_SkyMap_143_2048_R3.01_full.fits", "K_CMB", 2013, "the cleanest view of the CMB",
     "la CMB più pulita"),
    (217, "HFI_SkyMap_217_2048_R3.01_full.fits", "K_CMB", 2013, "CMB, dust starts to rise",
     "CMB; la polvere comincia a salire"),
    (353, "HFI_SkyMap_353_2048_R3.01_full.fits", "K_CMB", 2013, "dust takes over",
     "prevale la polvere"),
    (545, "HFI_SkyMap_545_2048_R3.01_full.fits", "MJy/sr", 604, "cold Galactic dust",
     "polvere fredda della Galassia"),
    (857, "HFI_SkyMap_857_2048_R3.01_full.fits", "MJy/sr", 604, "dust and the infrared light of distant galaxies",
     "polvere e luce infrarossa di galassie lontane"),
]
NSIDE_FREQ = 256


def file_cache_frequenza(nome):
    return CACHE / f"{Path(nome).stem}_n{NSIDE_FREQ}.npy"


def frequenze_pronte():
    return [f[0] for f in FREQUENZE if file_cache_frequenza(f[1]).exists()]


def mappa_frequenza(percorso, unita, log=nulla):
    """Mappa I a Nside 256 (μK_CMB fino a 353 GHz, MJy/sr a 545 e 857), salvata in cache."""
    hp = hp_()
    CACHE.mkdir(parents=True, exist_ok=True)
    f = file_cache_frequenza(Path(percorso).name)
    if f.exists():
        return np.load(f)
    log(f"  leggo {Path(percorso).name} e la porto a Nside {NSIDE_FREQ}…")
    m = leggi_healpix(percorso)
    cattivi = _cattivi(m)
    if unita == "K_CMB":
        m = m * np.float32(1e6)
    m[cattivi] = hp.UNSEEN
    m = np.asarray(hp.ud_grade(m, NSIDE_FREQ, pess=False), dtype=np.float32)
    m[_cattivi(m)] = hp.UNSEEN
    np.save(f, m)
    return m


def scarica_frequenze(scelte=None, log=nulla, avanzamento=nulla, ferma=None, cancella_grandi=True, cartella=None):
    """Scarica le mappe in frequenza scelte (GHz), le riduce a Nside 256 e, se richiesto, cancella i file
    grandi (restano le versioni ridotte in cache: ~3 MB l'una)."""
    scelte = set(scelte or [f[0] for f in FREQUENZE])
    mappe = {}
    da_fare = [f for f in FREQUENZE if f[0] in scelte]
    for i, (ghz, nome, unita, mbyte, _e, _i) in enumerate(da_fare):
        if ferma is not None and ferma.is_set():
            raise Interrotto()
        fc = file_cache_frequenza(nome)
        if fc.exists():
            mappe[ghz] = np.load(fc)
            continue
        log(f"Frequenza {ghz} GHz ({i + 1}/{len(da_fare)}): {nome} (~{mbyte} MB)")
        p = scarica_file(nome, cartella, log=log, avanzamento=avanzamento, ferma=ferma)
        mappe[ghz] = mappa_frequenza(p, unita, log)
        if cancella_grandi:
            try:
                p.unlink()
                (p.parent / (p.name + ".ok")).unlink(missing_ok=True)
                log(f"  cancellato il file grande ({mbyte} MB): resta la versione ridotta in cache")
            except OSError as e:
                log(f"  (non riesco a cancellare {p.name}: {e})")
    return mappe


def _scala_asinh(img):
    v = img[np.isfinite(img)]
    if v.size == 0:
        return img
    x = img - np.median(v)
    v = v - np.median(v)
    s = 1.4826 * np.median(np.abs(v)) or 1.0
    lim = np.percentile(np.abs(v), 99.7) or 1.0
    return np.clip(np.arcsinh(x / s) / np.arcsinh(lim / s), -1, 1)


def _scala_log(img):
    v = img[np.isfinite(img)]
    if v.size == 0:
        return img
    base = np.percentile(v, 0.5)
    y = np.log10(np.clip(img - base, 0, None) + 1e-3 * (np.percentile(v, 50) - base + 1e-6))
    lo, hi = np.nanpercentile(y, [1, 99.8])
    return np.clip((y - lo) / max(hi - lo, 1e-6), 0, 1)


def immagini_frequenze(mappe, larghezza=900):
    out = {}
    for ghz, nome, unita, *_ in FREQUENZE:
        if ghz in mappe:
            img = immagine_mollweide(mappe[ghz], larghezza)
            out[ghz] = {"img": _scala_log(img) if unita == "MJy/sr" else _scala_asinh(img), "unita": unita}
    return out


def disegna_frequenze(fig, immagini, inglese=True, margini=None):
    """Griglia 3 × 3: dal sincrotrone (30 GHz) alla polvere (857 GHz)."""
    import matplotlib
    g = fig.add_gridspec(3, 3, hspace=0.25, wspace=0.04, **(margini or {}))
    for k, (ghz, nome, unita, mbyte, d_en, d_it) in enumerate(FREQUENZE):
        ax = fig.add_subplot(g[k // 3, k % 3])
        ax.set_axis_off()
        if ghz in immagini:
            dati = immagini[ghz]
            if unita == "MJy/sr":
                cm = matplotlib.colormaps["inferno"].copy()
                cm.set_bad("white")
                ax.imshow(dati["img"], cmap=cm, vmin=0, vmax=1, interpolation="nearest")
            else:
                cm = mappa_colori()
                cm.set_bad("white")
                ax.imshow(dati["img"], cmap=cm, vmin=-1, vmax=1, interpolation="nearest")
        else:
            ax.text(0.5, 0.5, "not downloaded" if inglese else "non scaricata", ha="center", va="center",
                    transform=ax.transAxes, color="#777777")
        ax.set_title(f"{ghz} GHz – {d_en if inglese else d_it}", fontsize="small")
    return fig


def scheda_frequenze(immagini, cartella):
    import matplotlib
    with matplotlib.rc_context(STILE_SCHEDA):
        return _scheda_frequenze(immagini, cartella)


def _scheda_frequenze(immagini, cartella):
    import matplotlib
    matplotlib.use("Agg", force=False)
    from matplotlib.figure import Figure
    from matplotlib.backends.backend_agg import FigureCanvasAgg
    fig = Figure(figsize=(16.54, 11.69), dpi=110)
    FigureCanvasAgg(fig)
    fig.subplots_adjust(left=0.01, right=0.99, top=0.9, bottom=0.06)
    disegna_frequenze(fig, immagini, inglese=True)
    fig.suptitle("The sky seen by Planck at nine frequencies – from the Milky Way's synchrotron to its dust",
                 fontsize=15, fontweight="bold")
    fig.text(0.5, 0.025, "30–353 GHz: arcsinh stretch of ΔT (K_CMB); 545–857 GHz: logarithmic (MJy/sr). "
             "Galactic coordinates, centre of the Milky Way in the middle. Data: Planck PR3 frequency maps. " + FIRMA,
             ha="center", fontsize=9, color="#555555")
    cartella = Path(cartella)
    cartella.mkdir(parents=True, exist_ok=True)
    png, pdf = cartella / "planck_nine_frequencies.png", cartella / "planck_nine_frequencies.pdf"
    fig.savefig(png, dpi=110)
    fig.savefig(pdf)
    return pdf, png
