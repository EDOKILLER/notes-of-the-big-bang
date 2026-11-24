#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""
LE NOTE DEL BIG BANG 1.4 - i picchi acustici della radiazione cosmica di fondo (Planck)
=====================================================================================
© 2026 Edoardo Battistel

Scarica (una volta sola) le mappe Planck della radiazione cosmica di fondo, calcola lo
spettro di potenza angolare, trova i picchi acustici, stima la geometria dell'universo e
la materia ordinaria, e trasforma lo spettro in suono. Scheda PDF/PNG in inglese.

Uso:   note_big_bang.py                         finestra
       note_big_bang.py --analizza [--mappa smica-nosz|smica|commander|nilc|sevem]
                        [--nside 512|1024|2048] [--bin 20] [--apo 1.0] [--simulazioni 20]
                        [--senza-togliere-sorgenti] [--senza-suono]
       note_big_bang.py --frequenze [--tieni-file-grandi]   il cielo in 9 frequenze (~10,8 GB, poi ridotte)
       note_big_bang.py --scarica [--mappa …]   scarica soltanto i file
       note_big_bang.py --autotest              prove senza rete (apre la finestra per qualche secondo)
       note_big_bang.py --diagnostica           prova degli archivi in rete -> diagnostica.txt
Lo script ha bisogno di healpy: se il Python che lo lancia non ce l'ha, si rilancia da solo con
~/Claude/Astropy/venv_compila/bin/python.
"""

import http.server
import json
import os
import queue
import shutil
import socketserver
import subprocess
import sys
import tempfile
import threading
import time
import traceback
from pathlib import Path

if getattr(sys, "frozen", False):
    QUI = Path(getattr(sys, "_MEIPASS", Path(sys.executable).parent))
else:
    QUI = Path(__file__).resolve().parent
sys.path.insert(0, str(QUI))

VENV = Path.home() / "Claude" / "Astropy" / "venv_compila" / "bin" / "python"


def _rilancia_se_manca_healpy():
    if getattr(sys, "frozen", False) or os.environ.get("NBB_RILANCIATO"):
        return
    try:
        import healpy  # noqa: F401
    except ImportError:
        if VENV.exists() and Path(sys.executable).resolve() != VENV.resolve():
            os.environ["NBB_RILANCIATO"] = "1"
            os.execv(str(VENV), [str(VENV), str(Path(__file__).resolve())] + sys.argv[1:])


import motore_cmb as M  # noqa: E402

TITOLO = f"Le note del Big Bang {M.VERSIONE}"
CLASSE = "NoteBigBang"            # Tk la trasforma in «Notebigbang» (StartupWMClass)
CONFIG = Path.home() / ".note_big_bang.json"
RISOLUZIONI = {"Rapida (Nside 512)": 512, "Normale (Nside 1024)": 1024, "Completa (Nside 2048, lenta)": 2048}
MAPPE = {v[0]: k for k, v in M.MAPPE.items()}
BIN = ["10", "20", "30", "50"]
APO = ["0.5", "1.0", "2.0"]
VISTE = ["Spettro di potenza e picchi", "Mappa del cielo", "Mappa con la maschera", "Mappa con il dipolo del Sole",
         "Solo il dipolo del Sole", "Le note (lo spettro come suono)", "Il cielo in 9 frequenze"]
SIMULAZIONI = {"0 (veloce)": 0, "20 (~6 min)": 20, "50 (~15 min)": 50}
SCALE = [f"{p} %" for p in range(100, 210, 10)]


def leggi_config():
    try:
        return json.loads(CONFIG.read_text(encoding="utf-8"))
    except Exception:
        return {}


def salva_config(d):
    try:
        CONFIG.write_text(json.dumps(d, ensure_ascii=False, indent=1), encoding="utf-8")
    except OSError:
        pass


def ambiente_esterno():
    """Ambiente per i programmi esterni: toglie le librerie di PyInstaller (LD_LIBRARY_PATH)."""
    env = dict(os.environ)
    if "LD_LIBRARY_PATH_ORIG" in env:
        env["LD_LIBRARY_PATH"] = env["LD_LIBRARY_PATH_ORIG"]
    elif getattr(sys, "frozen", False):
        env.pop("LD_LIBRARY_PATH", None)
    return env


def apri_esterno(percorso):
    p = str(percorso)
    if sys.platform.startswith("win"):
        os.startfile(p)  # noqa: S606
    elif sys.platform == "darwin":
        subprocess.Popen(["open", p])
    else:
        subprocess.Popen(["xdg-open", p], env=ambiente_esterno(), stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL)


# =============================================================================
# FINESTRA
# =============================================================================
def crea_app(tk, ttk, tkfont):
    from matplotlib.figure import Figure
    from matplotlib.backends.backend_tkagg import FigureCanvasTkAgg, NavigationToolbar2Tk
    import matplotlib

    class EtichettaACapo(ttk.Label):
        def __init__(self, padre, **kw):
            super().__init__(padre, **kw)
            self.bind("<Configure>", lambda e: self.configure(wraplength=max(50, e.width - 8)))

    class App(tk.Tk):
        def __init__(self):
            super().__init__(className=CLASSE)
            self.cfg = leggi_config()
            self.title(f"{TITOLO} – {M.FIRMA}")
            self.coda = queue.Queue()
            self.ferma = threading.Event()
            self.lavoro = None
            self.ris = None
            self.suonando = None
            self.cartella = Path(self.cfg.get("cartella") or M.cartella_risultati_predefinita())
            self._icona()
            self.stile = ttk.Style(self)
            try:
                self.stile.theme_use("clam")
            except tk.TclError:
                pass
            self.scala = tk.StringVar(value=self.cfg.get("scala", "150 %"))
            self.mappa = tk.StringVar(value=self.cfg.get("mappa", list(MAPPE)[0]))
            if self.mappa.get() not in MAPPE:
                self.mappa.set(list(MAPPE)[0])
            self.risol = tk.StringVar(value=self.cfg.get("risoluzione", list(RISOLUZIONI)[1]))
            if self.risol.get() not in RISOLUZIONI:
                self.risol.set(list(RISOLUZIONI)[1])
            self.bin = tk.StringVar(value=self.cfg.get("bin", "20"))
            self.apo = tk.StringVar(value=self.cfg.get("apo", "1.0"))
            self.con_suono = tk.BooleanVar(value=self.cfg.get("suono", True))
            self.con_rumore = tk.BooleanVar(value=self.cfg.get("rumore", True))
            self.con_sorgenti = tk.BooleanVar(value=self.cfg.get("sorgenti", True))
            self.simul = tk.StringVar(value=self.cfg.get("simulazioni", list(SIMULAZIONI)[1]))
            if self.simul.get() not in SIMULAZIONI:
                self.simul.set(list(SIMULAZIONI)[1])
            self.freq_img = None
            self.vista = tk.StringVar(value=VISTE[0])
            self.stato = tk.StringVar(value="Pronto. Premi «Scarica e calcola»: la prima volta scarica circa 0,6 GB "
                                            "dall'archivio Planck (pochi minuti), poi usa i file sul disco.")
            self._costruisci()
            self.applica_scala()
            self.protocol("WM_DELETE_WINDOW", self.chiudi)
            self.after(120, self._leggi_coda)
            w, h = self.winfo_screenwidth(), self.winfo_screenheight()
            self.geometry(f"{int(w * 0.85)}x{int(h * 0.85)}+{int(w * 0.05)}+{int(h * 0.04)}")
            if self.cfg.get("opzioni_nascoste"):
                self.mostra_nascondi_opzioni(False)
            self.after(400, self._sash_iniziale)

        def _sash_iniziale(self):
            try:
                self.update_idletasks()
                self.pannelli.sashpos(0, int(self.pannelli.winfo_width() * 0.27))
            except tk.TclError:
                pass

        def _icona(self):
            for p in (QUI / "note_big_bang.png", Path(__file__).resolve().parent / "note_big_bang.png"):
                if p.exists():
                    try:
                        self._img_icona = tk.PhotoImage(file=str(p))
                        self.iconphoto(True, self._img_icona)
                    except tk.TclError:
                        pass
                    break

        def _costruisci(self):
            alto = ttk.Frame(self, padding=8)
            alto.pack(fill="x")
            self.alto = alto
            EtichettaACapo(alto, text="La radiazione cosmica di fondo è la luce più antica: partì 380 000 anni dopo il "
                                      "Big Bang. Le sue macchie hanno dimensioni preferite, come le note di uno strumento: "
                                      "il programma le misura sulle mappe del satellite Planck e le fa suonare.").pack(fill="x")
            opz = ttk.LabelFrame(self, text="Opzioni", padding=6)
            opz.pack(fill="x", padx=8)
            self.opz = opz
            # griglia fissa: etichetta, tendina, etichetta, tendina (le tendine si possono restringere)
            # ogni riga ha la sua griglia: le tendine con testi lunghi si allargano, quelle corte hanno larghezza fissa
            righe = [
                (("Mappa:", self.mappa, list(MAPPE), True), ("Risoluzione:", self.risol, list(RISOLUZIONI), True)),
                (("Cieli simulati:", self.simul, list(SIMULAZIONI), True), ("Δℓ:", self.bin, BIN, False),
                 ("Bordo maschera (°):", self.apo, APO, False), ("Testo:", self.scala, SCALE, False)),
            ]
            self.tendine = []
            opz.columnconfigure(0, weight=1)
            for r, voci in enumerate(righe):
                riga = ttk.Frame(opz)
                riga.grid(row=r, column=0, sticky="ew")
                for c, (testo, var, valori, larga) in enumerate(voci):
                    ttk.Label(riga, text=testo).grid(row=0, column=2 * c, sticky="w", padx=(0 if c == 0 else 18, 6), pady=2)
                    t = ttk.Combobox(riga, textvariable=var, values=valori, state="readonly",
                                     width=6 if larga else max(len(v) for v in valori) + 2)
                    t.grid(row=0, column=2 * c + 1, sticky="ew" if larga else "w", pady=2)
                    if larga:
                        riga.columnconfigure(2 * c + 1, weight=1)
                    t.bind("<<ComboboxSelected>>", lambda e: self.applica_scala())
                    self.tendine.append(t)
            spunte = ttk.Frame(opz)
            spunte.grid(row=2, column=0, sticky="w")
            ttk.Checkbutton(spunte, text="Togli la luce residua di sorgenti e galassie lontane",
                            variable=self.con_sorgenti).pack(side="left", pady=2)
            ttk.Checkbutton(spunte, text="Mostra anche lo spettro con il rumore", variable=self.con_rumore,
                            command=self.ridisegna).pack(side="left", padx=(18, 0), pady=2)
            ttk.Checkbutton(spunte, text="Suono delle note", variable=self.con_suono).pack(side="left", padx=(18, 0), pady=2)

            com = ttk.Frame(self, padding=(8, 6, 8, 0))
            com.pack(fill="x")
            self.com = com
            self.b_calcola = ttk.Button(com, text="Scarica e calcola", command=self.avvia)
            self.b_calcola.pack(side="left")
            self.b_ferma = ttk.Button(com, text="Ferma", command=self.interrompi, state="disabled")
            self.b_ferma.pack(side="left", padx=6)
            ttk.Label(com, text="Mostra:").pack(side="left", padx=(16, 4))
            self.c_vista = ttk.Combobox(com, textvariable=self.vista, values=VISTE, state="readonly", width=10)
            self.b_opzioni = ttk.Button(com, text="Nascondi opzioni", command=self.mostra_nascondi_opzioni)
            self.b_opzioni.pack(side="right", padx=(12, 0))
            self.c_vista.pack(side="left", fill="x", expand=True)
            self.c_vista.bind("<<ComboboxSelected>>", lambda e: self.ridisegna())

            # PRIMA la parte bassa fissa (regola del 20/11): così non finisce mai sotto il bordo dello schermo
            basso = ttk.Frame(self, padding=(8, 0, 8, 6))
            basso.pack(side="bottom", fill="x")
            self.barra_av = ttk.Progressbar(basso, mode="determinate")
            self.barra_av.pack(fill="x")
            EtichettaACapo(basso, textvariable=self.stato).pack(fill="x", pady=(2, 4))
            pulsanti = ttk.Frame(basso)
            pulsanti.pack(fill="x")
            self.b_ascolta = ttk.Button(pulsanti, text="Ascolta le note", command=self.ascolta, state="disabled")
            self.b_pdf = ttk.Button(pulsanti, text="Apri la scheda PDF", command=self.apri_pdf, state="disabled")
            self.b_cart = ttk.Button(pulsanti, text="Apri la cartella", command=self.apri_cartella)
            self.b_freq = ttk.Button(pulsanti, text="Mappe in 9 frequenze…", command=self.chiedi_frequenze)
            self.b_cambia = ttk.Button(pulsanti, text="Cambia cartella…", command=self.cambia_cartella)
            self.b_guida = ttk.Button(pulsanti, text="Guida", command=self.guida)
            for k, b in enumerate((self.b_ascolta, self.b_pdf, self.b_cart, self.b_freq, self.b_cambia, self.b_guida)):
                b.grid(row=0, column=k, sticky="ew", padx=(0, 6), pady=2)
                pulsanti.columnconfigure(k, weight=1)
            self.l_cartella = EtichettaACapo(basso, text="", foreground="#555555")
            self.l_cartella.pack(fill="x")

            self.pannelli = ttk.PanedWindow(self, orient="horizontal")
            self.pannelli.pack(fill="both", expand=True, padx=8, pady=6)
            sx = ttk.Frame(self.pannelli)
            dx = ttk.Frame(self.pannelli)
            self.pannelli.add(sx, weight=1)
            self.pannelli.add(dx, weight=3)
            ttk.Label(sx, text="Avanzamento e risultati").pack(anchor="w")
            fr = ttk.Frame(sx)
            fr.pack(fill="both", expand=True)
            self.registro = tk.Text(fr, wrap="word", state="disabled", height=6, width=30)
            sb = ttk.Scrollbar(fr, command=self.registro.yview)
            self.registro.configure(yscrollcommand=sb.set)
            sb.pack(side="right", fill="y")
            self.registro.pack(side="left", fill="both", expand=True)

            self.figura = Figure(figsize=(7, 5), dpi=100)
            self.tela = FigureCanvasTkAgg(self.figura, master=dx)
            self.barra = NavigationToolbar2Tk(self.tela, dx, pack_toolbar=False)
            self.barra.update()
            strumenti = ttk.Frame(dx)
            strumenti.pack(side="bottom", fill="x", pady=(4, 0))
            self.strumenti = strumenti
            self.pulsanti_grafico = {}
            for k, (chiave, testo) in enumerate((("home", "Inizio"), ("back", "Indietro"), ("forward", "Avanti"),
                                                 ("pan", "Sposta"), ("zoom", "Zoom"), ("save", "Salva…"))):
                p = ttk.Button(strumenti, text=testo, command=lambda k=chiave: self._strumento(k))
                p.grid(row=0, column=k, sticky="ew", padx=(0, 4), pady=1)
                strumenti.columnconfigure(k, weight=1, uniform="g")
                self.pulsanti_grafico[chiave] = p
            self.l_strumento = EtichettaACapo(dx, text="")      # compare solo con «Sposta» o «Zoom» attivi
            tw = self.tela.get_tk_widget()
            tw.configure(width=300, height=200)
            tw.pack(fill="both", expand=True)
            self._aggiorna_cartella()

        def _disponi_opzioni(self):
            pass          # 1.2: le opzioni stanno in una griglia fissa

        def _strumento(self, k):
            tb = self.barra
            {"home": tb.home, "back": tb.back, "forward": tb.forward, "pan": tb.pan, "zoom": tb.zoom,
             "save": tb.save_figure}[k]()
            modo = str(getattr(tb.mode, "name", tb.mode) or "").upper()
            for chiave, nome in (("pan", "PAN"), ("zoom", "ZOOM")):
                self.pulsanti_grafico[chiave].configure(style="Attivo.TButton" if modo == nome else "TButton")
            testo = {"PAN": "Sposta attivo: trascina il grafico; ripremi «Sposta» per smettere.",
                     "ZOOM": "Zoom attivo: disegna un riquadro sul grafico; ripremi «Zoom» per "
                             "smettere, «Inizio» per tornare alla vista intera."}.get(modo, "")
            self.l_strumento.configure(text=testo)
            if testo and not self.l_strumento.winfo_manager():
                self.l_strumento.pack(side="bottom", fill="x", after=self.strumenti)
            elif not testo and self.l_strumento.winfo_manager():
                self.l_strumento.pack_forget()

        def mostra_nascondi_opzioni(self, mostra=None):
            visibili = bool(self.opz.winfo_manager())
            mostra = (not visibili) if mostra is None else mostra
            if mostra and not visibili:
                self.opz.pack(fill="x", padx=8, before=self.com)
            elif not mostra and visibili:
                self.opz.pack_forget()
            self.b_opzioni.configure(text="Nascondi opzioni" if mostra else "Mostra opzioni")
            self.cfg["opzioni_nascoste"] = not mostra

        def _aggiorna_cartella(self):
            self.l_cartella.configure(text=f"Risultati in: {self.cartella}   –   {M.FIRMA}")

        # ------------------------------------------------------------ dimensione del testo
        def applica_scala(self):
            try:
                s = int(self.scala.get().split()[0]) / 100.0
            except (ValueError, IndexError):
                s = 1.5
            base = max(8, round(10 * s))
            for nome in ("TkDefaultFont", "TkTextFont", "TkMenuFont", "TkHeadingFont", "TkCaptionFont",
                         "TkSmallCaptionFont", "TkIconFont", "TkTooltipFont", "TkFixedFont"):
                try:
                    tkfont.nametofont(nome).configure(size=base)
                except tk.TclError:
                    pass
            self.registro.configure(font="TkTextFont")
            try:
                riga = int(tkfont.nametofont("TkDefaultFont").metrics("linespace") or round(16 * s))
            except (tk.TclError, TypeError, ValueError):
                riga = round(16 * s)
            p = lambda v: max(1, round(v * s))  # noqa: E731
            self.stile.configure("TButton", padding=(p(8), p(4)))
            self.stile.configure("Attivo.TButton", padding=(p(8), p(4)), background="#f0b050")
            self.stile.configure("TCombobox", padding=p(3), arrowsize=max(12, int(riga * 0.9)))
            self.stile.configure("TCheckbutton", padding=p(2), indicatorsize=max(12, int(riga * 0.8)))
            self.stile.configure("TEntry", padding=p(3))
            for orient in ("Vertical", "Horizontal"):
                self.stile.configure(f"{orient}.TScrollbar", arrowsize=max(14, int(riga * 1.1)), width=max(14, int(riga * 1.1)))
            self.stile.configure("TProgressbar", thickness=max(10, int(riga * 0.8)))
            self.stile.configure("Sash", sashthickness=max(6, int(riga * 0.5)))
            self.option_add("*TCombobox*Listbox.font", "TkTextFont")
            # dal 180 % la frase introduttiva lascia il posto al grafico (resta nella Guida)
            if s >= 1.8 and self.alto.winfo_manager():
                self.alto.pack_forget()
            elif s < 1.8 and not self.alto.winfo_manager():
                self.alto.pack(fill="x", before=self.opz if self.opz.winfo_manager() else self.com)
            matplotlib.rcParams["font.size"] = 10.0 * s
            matplotlib.rcParams["lines.markersize"] = 4.0 * s
            self.after(50, self._disponi_opzioni)
            self.ridisegna()

        # ------------------------------------------------------------ registro e coda
        def scrivi(self, testo):
            self.registro.configure(state="normal")
            self.registro.insert("end", testo + "\n")
            self.registro.see("end")
            self.registro.configure(state="disabled")

        def _leggi_coda(self):
            try:
                while True:
                    tipo, dato = self.coda.get_nowait()
                    if tipo == "log":
                        self.scrivi(dato)
                    elif tipo == "stato":
                        self.stato.set(dato)
                    elif tipo == "avanz":
                        a, b, testo = dato
                        self.barra_av.configure(maximum=max(b, 1), value=min(a, b))
                        if testo:
                            self.stato.set(testo)
                    elif tipo == "risultato":
                        self._nuovo_risultato(dato)
                    elif tipo == "frequenze":
                        self.freq_img = dato
                        self.vista.set(VISTE[6])
                        self.ridisegna()
                    elif tipo == "fine":
                        self._fine(dato)
            except queue.Empty:
                pass
            self.after(120, self._leggi_coda)

        # ------------------------------------------------------------ calcolo
        def _opzioni(self):
            return {"mappa": MAPPE.get(self.mappa.get(), "smica-nosz"), "nside": RISOLUZIONI.get(self.risol.get(), 1024),
                    "bin": int(self.bin.get()), "apo": float(self.apo.get()), "suono": self.con_suono.get(),
                    "cartella": self.cartella, "sorgenti": self.con_sorgenti.get(),
                    "simulazioni": SIMULAZIONI.get(self.simul.get(), 20)}

        def avvia(self):
            if self.lavoro and self.lavoro.is_alive():
                return
            opz = self._opzioni()
            self._salva()
            self.ferma.clear()
            self.b_calcola.configure(state="disabled")
            self.b_ferma.configure(state="normal")
            self.barra_av.configure(value=0)
            self.scrivi(f"\n══════ {self.mappa.get()} – {self.risol.get()} ══════")
            self.lavoro = threading.Thread(target=self._lavora, args=(opz,), daemon=True)
            self.lavoro.start()

        def _lavora(self, opz):
            try:
                ris = M.analizza(opz, log=lambda s: self.coda.put(("log", s)),
                                 avanzamento=lambda a, b, t="": self.coda.put(("avanz", (a, b, t))), ferma=self.ferma)
                self.coda.put(("risultato", ris))
                self.coda.put(("fine", True))
            except M.Interrotto:
                self.coda.put(("log", "Interrotto. I file già scaricati restano sul disco (anche quelli a metà: "
                                      "si riprendono la prossima volta)."))
                self.coda.put(("fine", False))
            except Exception as e:
                self.coda.put(("log", f"ERRORE: {type(e).__name__}: {e}"))
                self.coda.put(("log", "".join(traceback.format_exception(e)[-3:]).rstrip()))
                self.coda.put(("fine", False))

        def _nuovo_risultato(self, ris):
            self.ris = ris
            self.scrivi("— Risultati —")
            for r in M.testo_risultati_italiano(ris):
                self.scrivi("  " + r)
            self.scrivi("— Come nella scheda (inglese) —")
            for r in M.testo_risultati_inglese(ris):
                self.scrivi("  " + r)
            self.b_ascolta.configure(state="normal" if ris.get("wav") else "disabled")
            self.b_pdf.configure(state="normal" if ris.get("pdf") else "disabled")
            self.ridisegna()

        def _fine(self, riuscito):
            self.b_calcola.configure(state="normal")
            self.b_freq.configure(state="normal")
            self.b_ferma.configure(state="disabled")
            if riuscito and self.vista.get() == VISTE[6]:
                self.stato.set(f"Fatto. Scheda delle 9 frequenze in {self.cartella / 'frequenze'}")
            elif riuscito and self.ris:
                self.stato.set(f"Fatto. Scheda, mappa, tabelle e suono in {self.ris['cartella']}")
            elif not riuscito:
                self.stato.set("Calcolo non completato: vedi il riquadro a sinistra.")

        def interrompi(self):
            self.ferma.set()
            self.stato.set("Interruzione richiesta: mi fermo appena possibile…")

        # ------------------------------------------------------------ grafici
        def ridisegna(self):
            self.figura.clear()
            ris = self.ris
            v = self.vista.get()
            if v == VISTE[6]:
                self._disegna_frequenze()
                return
            try:
                self.figura.set_layout_engine("constrained")
            except Exception:
                pass
            if not ris:
                a = self.figura.add_subplot(111)
                a.text(0.5, 0.5, "Qui compariranno la mappa del cielo di Planck,\nlo spettro di potenza con i picchi "
                                 "acustici\ne le note del Big Bang", ha="center", va="center", transform=a.transAxes,
                       color="#555555")
                a.set_axis_off()
                self.tela.draw_idle()
                return
            try:
                ris["mostra_rumore"] = self.con_rumore.get()
                if v == VISTE[0]:
                    g = self.figura.add_gridspec(2, 1, height_ratios=[3, 1])
                    a = self.figura.add_subplot(g[0])
                    r = self.figura.add_subplot(g[1], sharex=a)
                    M.disegna_spettro(a, ris, inglese=False, residui_ax=r)
                    a.set_title("Spettro di potenza della temperatura della CMB")
                elif v == VISTE[5]:
                    a = self.figura.add_subplot(111)
                    M.disegna_note(a, ris, inglese=False)
                else:
                    modo = {VISTE[1]: ("cmb", False), VISTE[2]: ("cmb", True), VISTE[3]: ("cmb_dipolo", False),
                            VISTE[4]: ("dipolo", False)}[v]
                    a = self.figura.add_subplot(111)
                    im = M.disegna_mappa(a, ris, modo[0], con_maschera=modo[1], titoli_inglese=False)
                    self.figura.colorbar(im, ax=a, orientation="horizontal", fraction=0.05, pad=0.04, label="ΔT [μK]")
                    if modo[0] == "cmb_dipolo":
                        a.text(0.5, -0.02, "Il Sole si muove a 370 km/s: davanti la radiazione è un po' più calda, "
                                           "dietro un po' più fredda (effetto Doppler). È 10 volte più forte delle macchie.",
                               ha="center", va="top", transform=a.transAxes, wrap=True, fontsize="small")
            except Exception as e:
                self.scrivi(f"(grafico non disegnato: {e})")
            self.tela.draw_idle()

        # ------------------------------------------------------------ 9 frequenze
        def _carica_frequenze_in_cache(self):
            if self.freq_img is None and M.frequenze_pronte():
                try:
                    mappe = {f[0]: __import__("numpy").load(M.file_cache_frequenza(f[1])) for f in M.FREQUENZE
                             if M.file_cache_frequenza(f[1]).exists()}
                    self.freq_img = M.immagini_frequenze(mappe)
                except Exception as e:
                    self.scrivi(f"(mappe in frequenza non lette: {e})")

        def _disegna_frequenze(self):
            try:
                self.figura.set_layout_engine("none")
            except Exception:
                pass
            self._carica_frequenze_in_cache()
            if not self.freq_img:
                a = self.figura.add_subplot(111)
                a.text(0.5, 0.5, "Le mappe in frequenza non sono ancora scaricate:\npremi «Mappe in 9 frequenze…» qui sotto.",
                       ha="center", va="center", transform=a.transAxes, color="#555555")
                a.set_axis_off()
            else:
                M.disegna_frequenze(self.figura, self.freq_img, inglese=False,
                                    margini={"left": 0.01, "right": 0.99, "top": 0.95, "bottom": 0.02})
            self.tela.draw_idle()

        def chiedi_frequenze(self):
            pronte = M.frequenze_pronte()
            mancano = [f for f in M.FREQUENZE if f[0] not in pronte]
            if not mancano:
                self.vista.set(VISTE[6])
                self.ridisegna()
                self.stato.set("Le 9 mappe in frequenza sono già pronte.")
                return
            gb = sum(f[3] for f in mancano) / 1000
            testo = (f"Mancano {len(mancano)} mappe su 9 ({', '.join(str(f[0]) for f in mancano)} GHz): in tutto circa "
                     f"{gb:.1f} GB dall'archivio dell'ESA (con la rete di casa circa {gb * 1000 / 15 / 60:.0f} minuti).\n\n"
                     "Ogni mappa viene ridotta (Nside 256, circa 3 MB) e poi il file grande viene cancellato, "
                     "così sul disco non resta quasi nulla. Si possono vedere il sincrotrone e la polvere della Via Lattea "
                     "e capire perché la CMB si guarda tra 70 e 217 GHz.\n\nScarico adesso?")
            if not chiedi(self, "Il cielo in 9 frequenze", testo):
                return
            if self.lavoro and self.lavoro.is_alive():
                self.stato.set("Aspetta che finisca il calcolo in corso.")
                return
            self.ferma.clear()
            self.b_calcola.configure(state="disabled")
            self.b_freq.configure(state="disabled")
            self.b_ferma.configure(state="normal")
            self.scrivi("\n══════ Il cielo in 9 frequenze ══════")
            self.lavoro = threading.Thread(target=self._lavora_frequenze, daemon=True)
            self.lavoro.start()

        def _lavora_frequenze(self):
            try:
                mappe = M.scarica_frequenze(log=lambda s: self.coda.put(("log", s)),
                                            avanzamento=lambda a, b, t="": self.coda.put(("avanz", (a, b, t))),
                                            ferma=self.ferma)
                img = M.immagini_frequenze(mappe)
                pdf, png = M.scheda_frequenze(img, self.cartella / "frequenze")
                self.coda.put(("log", f"Scheda delle 9 frequenze: {pdf}"))
                self.coda.put(("frequenze", img))
                self.coda.put(("fine", True))
            except M.Interrotto:
                self.coda.put(("log", "Interrotto: le mappe già ridotte restano in cache."))
                self.coda.put(("fine", False))
            except Exception as e:
                self.coda.put(("log", f"ERRORE: {type(e).__name__}: {e}"))
                self.coda.put(("fine", False))

        # ------------------------------------------------------------ pulsanti
        def ascolta(self):
            if not self.ris or not self.ris.get("wav"):
                return
            if self.suonando and self.suonando.poll() is None:
                self.suonando.terminate()
                self.suonando = None
                self.b_ascolta.configure(text="Ascolta le note")
                return
            for prog in ("paplay", "pw-play", "aplay"):
                if shutil.which(prog):
                    self.suonando = subprocess.Popen([prog, str(self.ris["wav"])], env=ambiente_esterno(),
                                                     stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL)
                    self.b_ascolta.configure(text="Ferma il suono")
                    self.after(500, self._controlla_suono)
                    note = ", ".join(f"{M.nota(p['ell'], True)[0]} ({p['ell']:.0f} Hz)" for p in self.ris.get("picchi", []))
                    self.stato.set("Prima «il coro»: un fruscio con lo stesso spettro della CMB (ℓ diventa una frequenza "
                                   f"in Hz). Poi i picchi uno per uno e insieme: {note}.")
                    return
            apri_esterno(self.ris["wav"])

        def _controlla_suono(self):
            if self.suonando and self.suonando.poll() is None:
                self.after(500, self._controlla_suono)
            else:
                self.b_ascolta.configure(text="Ascolta le note")

        def apri_pdf(self):
            if self.ris and self.ris.get("pdf"):
                apri_esterno(self.ris["pdf"])

        def apri_cartella(self):
            self.cartella.mkdir(parents=True, exist_ok=True)
            apri_esterno(self.ris["cartella"] if self.ris else self.cartella)

        def cambia_cartella(self):
            nuova = None
            if shutil.which("zenity") and not sys.platform.startswith("win"):
                try:
                    r = subprocess.run(["zenity", "--file-selection", "--directory",
                                        "--title=Cartella per i risultati", f"--filename={self.cartella}/"],
                                       capture_output=True, text=True, env=ambiente_esterno(), timeout=600)
                    if r.returncode == 0 and r.stdout.strip():
                        nuova = r.stdout.strip()
                    elif r.returncode == 1:
                        return
                except Exception:
                    nuova = None
            if nuova is None:
                from tkinter import filedialog
                nuova = filedialog.askdirectory(parent=self, initialdir=str(self.cartella), title="Cartella per i risultati")
            if nuova:
                self.cartella = Path(nuova)
                self._aggiorna_cartella()
                self._salva()

        def guida(self):
            testo = None
            for p in (QUI / "LEGGIMI.txt", Path(__file__).resolve().parent / "LEGGIMI.txt"):
                if p.exists():
                    testo = p.read_text(encoding="utf-8")
                    break
            messaggio(self, "Guida", testo or __doc__, larga=True)

        def _salva(self):
            self.cfg.update({"scala": self.scala.get(), "mappa": self.mappa.get(), "risoluzione": self.risol.get(),
                             "bin": self.bin.get(), "apo": self.apo.get(), "suono": self.con_suono.get(),
                             "rumore": self.con_rumore.get(), "cartella": str(self.cartella),
                             "sorgenti": self.con_sorgenti.get(), "simulazioni": self.simul.get()})
            salva_config(self.cfg)

        def chiudi(self):
            if self.lavoro and self.lavoro.is_alive():
                self.ferma.set()
            if self.suonando and self.suonando.poll() is None:
                self.suonando.terminate()
            self._salva()
            self.destroy()

    def messaggio(padre, titolo, testo, larga=False):
        """Finestra di messaggio ridimensionabile con il testo che va a capo."""
        w = tk.Toplevel(padre)
        w.title(titolo)
        w.transient(padre)
        fr = ttk.Frame(w, padding=10)
        fr.pack(fill="both", expand=True)
        t = tk.Text(fr, wrap="word", height=24 if larga else 6, width=92 if larga else 60, font="TkTextFont",
                    relief="flat", background="white" if larga else padre.cget("background"))
        sb = ttk.Scrollbar(fr, command=t.yview)
        t.configure(yscrollcommand=sb.set)
        t.insert("1.0", testo)
        t.configure(state="disabled")
        ttk.Button(fr, text="OK", command=w.destroy).pack(side="bottom", pady=(8, 0))
        if larga:
            sb.pack(side="right", fill="y")
        t.pack(side="left", fill="both", expand=True)
        w.bind("<Escape>", lambda e: w.destroy())
        return w

    def chiedi(padre, titolo, testo):
        """Domanda Sì/No in una finestra ridimensionabile con il testo che va a capo."""
        w = tk.Toplevel(padre)
        w.title(titolo)
        w.transient(padre)
        risposta = {"si": False}
        fr = ttk.Frame(w, padding=10)
        fr.pack(fill="both", expand=True)
        t = tk.Text(fr, wrap="word", height=9, width=70, font="TkTextFont", relief="flat",
                    background=padre.cget("background"))
        t.insert("1.0", testo)
        t.configure(state="disabled")
        bt = ttk.Frame(fr)
        bt.pack(side="bottom", pady=(8, 0))

        def si():
            risposta["si"] = True
            w.destroy()
        ttk.Button(bt, text="Sì, scarica", command=si).pack(side="left", padx=6)
        ttk.Button(bt, text="No", command=w.destroy).pack(side="left", padx=6)
        t.pack(side="top", fill="both", expand=True)
        w.bind("<Escape>", lambda e: w.destroy())
        if getattr(padre, "_autotest", False):
            padre.after(300, si)
        w.wait_window()
        return risposta["si"]

    return App, messaggio


def avvia_finestra():
    import tkinter as tk
    from tkinter import ttk, font as tkfont
    App, _ = crea_app(tk, ttk, tkfont)
    App().mainloop()


# =============================================================================
# RIGA DI COMANDO
# =============================================================================
def leggi_argomenti(a):
    opz = {"mappa": "smica-nosz", "nside": 1024, "bin": 20, "apo": 1.0, "suono": True, "sorgenti": True,
           "simulazioni": 20}
    i = 0
    while i < len(a):
        x = a[i]
        if x in ("--mappa", "--nside", "--bin", "--apo", "--cartella", "--simulazioni") and i + 1 < len(a):
            v = a[i + 1]
            chiave = x[2:]
            opz[chiave] = {"nside": int, "bin": int, "apo": float, "simulazioni": int,
                           "cartella": lambda s: Path(s).expanduser()}.get(chiave, str)(v)
            i += 1
        elif x == "--senza-suono":
            opz["suono"] = False
        elif x == "--senza-togliere-sorgenti":
            opz["sorgenti"] = False
        i += 1
    if opz["mappa"] not in M.MAPPE:
        raise SystemExit(f"Mappa sconosciuta: {opz['mappa']} (scegli tra {', '.join(M.MAPPE)})")
    return opz


def analizza_da_terminale(a):
    opz = leggi_argomenti(a)
    ultimo = [0.0]

    def avanz(x, y, t=""):
        if t and time.time() - ultimo[0] > 2:
            ultimo[0] = time.time()
            print("   …" + t, flush=True)
    try:
        ris = M.analizza(opz, log=lambda s: print(s, flush=True), avanzamento=avanz)
    except Exception as e:
        print(f"ERRORE: {type(e).__name__}: {e}")
        traceback.print_exc()
        return 1
    print("\n— Summary —")
    for r in M.testo_risultati_inglese(ris):
        print("   " + r)
    print(f"\nScheda: {ris['pdf']}")
    return 0


def frequenze_da_terminale(a):
    ultimo = [0.0]

    def avanz(x, y, t=""):
        if t and time.time() - ultimo[0] > 2:
            ultimo[0] = time.time()
            print("   " + t, flush=True)
    mappe = M.scarica_frequenze(log=lambda s: print(s, flush=True), avanzamento=avanz,
                                cancella_grandi="--tieni-file-grandi" not in a)
    cart = M.cartella_risultati_predefinita() / "frequenze"
    pdf, png = M.scheda_frequenze(M.immagini_frequenze(mappe), cart)
    print(f"Scheda delle 9 frequenze: {pdf}")
    return 0


def scarica_da_terminale(a):
    opz = leggi_argomenti(a)
    ultimo = [0.0]

    def avanz(x, y, t=""):
        if t and time.time() - ultimo[0] > 2:
            ultimo[0] = time.time()
            print("   " + t, flush=True)
    M.scarica_tutto(opz["mappa"], log=lambda s: print(s, flush=True), avanzamento=avanz)
    print(f"File in {M.CARTELLA_DATI}")
    return 0


def diagnostica(a=()):
    opz = leggi_argomenti(list(a))
    righe = []

    def scrivi(t):
        print(t, flush=True)
        righe.append(t)

    scrivi(f"{TITOLO} – diagnostica di rete – {time.strftime('%d/%m/%Y %H:%M:%S')}")
    prove = []

    def prova(nome, f):
        t0 = time.time()
        try:
            nota = f()
            prove.append(True)
            scrivi(f"[ok] {nome} ({time.time() - t0:.1f} s) {nota or ''}")
        except Exception as e:
            prove.append(False)
            scrivi(f"[ERRORE] {nome} ({time.time() - t0:.1f} s) {type(e).__name__}: {e}")

    def hp_versione():
        hp = M.hp_()
        return f"healpy {hp.__version__} (Python {sys.version.split()[0]})"
    prova("healpy", hp_versione)
    for nome in M.file_necessari(opz["mappa"]):
        def irsa(nome=nome):
            n = M.dimensione_remota(nome)
            if not n:
                raise RuntimeError("dimensione sconosciuta (file assente?)")
            return f"{n / 1e6:.0f} MB"
        prova(f"IRSA: {nome}", irsa)

        def esa(nome=nome):
            with M.apri(M.url_pla(nome), tempo=60) as r:
                b = r.read(2880)
            if not b.startswith(b"SIMPLE"):
                raise RuntimeError("non è un FITS: " + b[:80].decode("utf-8", "replace"))
            return "FITS ok"
        prova(f"ESA (PLA): {nome}", esa)
    for nome in (M.SPETTRO_BINNATO, M.SPETTRO_LCDM):
        def sp(nome=nome):
            p = M.scarica_file(nome, log=scrivi, max_tentativi=2)
            return f"{p.stat().st_size} byte in {p.parent}"
        prova(f"spettro ufficiale {nome}", sp)
    for ghz, nome, *_ in M.FREQUENZE:
        def fr(nome=nome):
            n = M.dimensione_remota(nome)
            if not n:
                raise RuntimeError("dimensione sconosciuta su IRSA (file assente?)")
            with M.apri(M.url_pla(nome), tempo=60) as r:
                b = r.read(2880)
            if not b.startswith(b"SIMPLE"):
                raise RuntimeError("l'ESA non manda un FITS: " + b[:80].decode("utf-8", "replace"))
            return f"{n / 1e6:.0f} MB, ESA ok"
        prova(f"mappa a {ghz} GHz: {nome}", fr)
    ok = sum(prove)
    scrivi(f"\n{ok}/{len(prove)} prove riuscite." + ("  TUTTO OK" if ok == len(prove) else ""))
    try:
        Path("diagnostica.txt").write_text("\n".join(righe) + "\n", encoding="utf-8")
        print(f"Scritto {Path('diagnostica.txt').resolve()}")
    except OSError:
        pass
    return 0 if ok == len(prove) else 1


# =============================================================================
# AUTOTEST (senza rete)
# =============================================================================
def spettro_finto(lmax):
    """Spettro di prova con picchi acustici simili a quelli veri (D_ℓ in μK²)."""
    import numpy as np
    ell = np.arange(lmax + 1, dtype=float)
    D = (1000 + 4700 * np.exp(-((ell - 220) / 95) ** 2) + 2000 * np.exp(-((ell - 540) / 85) ** 2)
         + 2000 * np.exp(-((ell - 810) / 85) ** 2) + 900 * np.exp(-((ell - 1110) / 90) ** 2)) * np.exp(-(ell / 1500) ** 2)
    D[:2] = 0
    return ell, D


class _Gestore(http.server.BaseHTTPRequestHandler):
    """Server di prova: «esa» ignora Range e chiude a metà; «irsa» accetta Range."""
    radice = None
    taglia = 0

    def log_message(self, *a):
        pass

    def _file(self):
        nome = self.path.split("/")[-1]
        return Path(self.radice) / nome

    def do_HEAD(self):
        f = self._file()
        if not f.exists():
            self.send_error(404)
            return
        self.send_response(200)
        self.send_header("Content-Length", str(f.stat().st_size))
        self.end_headers()

    def do_GET(self):
        f = self._file()
        if not f.exists():
            self.send_error(404)
            return
        dati = f.read_bytes()
        rng = self.headers.get("Range")
        if self.server.tipo == "irsa" and rng:
            import re as _re
            m = _re.match(r"bytes=(\d+)-(\d*)", rng)
            a = int(m.group(1))
            b = int(m.group(2)) if m.group(2) else len(dati) - 1
            self.send_response(206)
            self.send_header("Content-Range", f"bytes {a}-{b}/{len(dati)}")
            self.send_header("Content-Length", str(b - a + 1))
            self.end_headers()
            self.wfile.write(dati[a:b + 1])
            return
        self.send_response(200)
        self.send_header("Content-Length", str(len(dati)))
        self.end_headers()
        if self.server.tipo == "esa" and self.server.taglia:
            self.wfile.write(dati[:self.server.taglia])          # si interrompe a metà
            return
        self.wfile.write(dati)


def _server(tipo, radice, taglia=0):
    class S(socketserver.ThreadingMixIn, http.server.HTTPServer):
        daemon_threads = True
    g = type("G", (_Gestore,), {"radice": str(radice)})
    s = S(("127.0.0.1", 0), g)
    s.tipo, s.taglia = tipo, taglia
    threading.Thread(target=s.serve_forever, daemon=True).start()
    return s


def autotest(con_finestra=True):
    import numpy as np
    esiti = []

    def controlla(nome, cond, nota=""):
        esiti.append(bool(cond))
        print(f"[{'ok' if cond else 'ERRORE'}] {nome} {nota}", flush=True)

    def prova(nome, f):
        try:
            f()
        except Exception as e:
            esiti.append(False)
            print(f"[ERRORE] {nome}: {type(e).__name__}: {e}", flush=True)
            traceback.print_exc()

    print(f"{TITOLO} – autotest – Python {sys.version.split()[0]}", flush=True)
    tmp = Path(tempfile.mkdtemp(prefix="note_big_bang_autotest_"))
    vecchi = (M.CACHE, M.CARTELLA_DATI, M.NSIDE_ORIGINE, M.url_pla, M.url_irsa)
    M.CACHE = tmp / "cache"
    M.CARTELLA_DATI = tmp / "dati"

    def t_formule():
        controlla("note: ℓ = 220 → La (A3)", M.nota(220)[0] == "A3" and M.nota(220, True)[0] == "La3")
        h = M.hu_h2(M.OMEGA_B_H2_PLANCK)
        controlla("formula di Hu: rapporto dei picchi per Planck ≈ 0,46", 0.43 < h < 0.49, f"({h:.3f})")
        controlla("formula di Hu invertita", abs(M.omega_b_da_h2(h) - M.OMEGA_B_H2_PLANCK) < 1e-6)
        ell, D = spettro_finto(1500)
        p = M.trova_picchi(ell, D, None, lmax=1500, n_mc=0)
        v = M.trova_valli(ell, D, None, p, n_mc=0)
        controlla("valli tra i picchi", len(v) >= 2 and 380 < v[0]["ell"] < 440, "(" + ", ".join(f"{q['ell']:.0f}" for q in v) + ")")
        lb = np.arange(40, 2000, 20) + 10.0
        Db = np.interp(lb, ell, D)
        err = np.maximum(Db, 50) * np.sqrt(2 / ((2 * lb + 1) * 20 * 0.77)) + 1
        uff = {"ell": lb, "D": Db}
        finto = {"ell": lb, "D": Db * 1.015 + 40 * (lb / 1000) ** 2 + np.random.default_rng(1).normal(0, err), "err": err,
                 "n": np.full(lb.size, 20)}
        sr = M.sorgenti_residue(finto, uff)
        controlla("sorgenti residue ritrovate (40 μK² a ℓ = 1000, +1,5 %)", sr and abs(sr["A"] - 40) < 4 and abs(sr["c"] - 0.015) < 0.006,
                  f"(A = {sr['A']:.1f}, scala {100 * sr['c']:+.2f} %)" if sr else "")
        controlla("picchi di uno spettro noto", len(p) >= 3 and abs(p[0]["ell"] - 220) < 8 and abs(p[1]["ell"] - 540) < 10,
                  "(" + ", ".join(f"{q['ell']:.0f}" for q in p) + ")")
    prova("formule", t_formule)

    def t_scarica():
        sorgente = tmp / "server"
        sorgente.mkdir()
        nome = "COM_CMB_IQU-prova_2048_R3.00_hm1.fits"
        dati = b"SIMPLE  =                    T" + bytes(np.random.default_rng(1).integers(0, 255, 3_000_000, dtype=np.uint8))
        (sorgente / nome).write_bytes(dati)
        esa = _server("esa", sorgente, taglia=1_200_000)
        irsa = _server("irsa", sorgente)
        M.url_pla = lambda n: f"http://127.0.0.1:{esa.server_address[1]}/{n}"
        M.url_irsa = lambda n: f"http://127.0.0.1:{irsa.server_address[1]}/{n}"
        voci = []
        p = M.scarica_file(nome, tmp / "dati", log=voci.append)
        controlla("scaricamento: ESA si interrompe, IRSA riprende dal punto raggiunto",
                  p.read_bytes() == dati and any("IRSA" in v for v in voci), f"({len(voci)} messaggi)")
        p2 = M.scarica_file(nome, tmp / "dati", log=voci.append)
        controlla("scaricamento: il file già presente non si riscarica", p2 == p)
        esa.shutdown()
        irsa.shutdown()
        M.url_pla, M.url_irsa = vecchi[3], vecchi[4]
    prova("scaricamento", t_scarica)

    risultato = {}

    def t_cielo():
        try:
            hp = M.hp_()
        except RuntimeError as e:
            controlla(f"cielo simulato (healpy assente: {e})", False)
            return
        nside, lmax = 512, 1000
        M.NSIDE_ORIGINE = nside            # i file simulati sono a 512
        ell, D = spettro_finto(3 * nside - 1)
        cl = np.zeros_like(D)
        cl[2:] = 2 * np.pi * D[2:] / (ell[2:] * (ell[2:] + 1))
        rng = np.random.default_rng(3)
        np.random.seed(3)
        cielo = hp.synfast(cl, nside, lmax=3 * nside - 1, fwhm=np.radians(M.FWHM_ARCMIN / 60), pixwin=True)
        d = tmp / "dati"
        d.mkdir(exist_ok=True)
        nomi = M.file_necessari("smica-nosz")
        for nome, sem in ((nomi[0], 11), (nomi[1], 12)):
            mappa = (cielo + np.random.default_rng(sem).normal(0, 25, cielo.size)) * 1e-6     # μK -> K
            hp.write_map(str(d / nome), hp.reorder(mappa.astype(np.float32), r2n=True), nest=True, coord="G",
                         column_names=["I_STOKES"], column_units="K_CMB", dtype=np.float32, overwrite=True)
        th, ph = hp.pix2ang(nside, np.arange(hp.nside2npix(nside)))
        b = 90 - np.degrees(th)
        masc = (np.abs(b) > 15).astype(np.float32)
        for lc, bc in ((30, 40), (200, -50), (300, 30)):                  # tre «sorgenti» mascherate
            v = hp.ang2vec(lc, bc, lonlat=True)
            masc[hp.query_disc(nside, v, np.radians(2))] = 0
        hp.write_map(str(d / nomi[2]), hp.reorder(masc, r2n=True), nest=True, coord="G", column_names=["TMASK"],
                     dtype=np.float32, overwrite=True)
        percorsi = {n: d / n for n in nomi}
        tabella = "\n".join(f"{l:.0f} {x:.4f}" for l, x in zip(ell[2:], D[2:]))
        (d / M.SPETTRO_LCDM).write_text("# L TT\n" + tabella + "\n")
        percorsi[M.SPETTRO_LCDM] = d / M.SPETTRO_LCDM
        t0 = time.time()
        ris = M.analizza({"mappa": "smica-nosz", "nside": nside, "lmax": lmax, "apo": 1.0, "bin": 20, "suono": True,
                          "cartella": tmp / "risultati", "percorsi": percorsi, "simulazioni": 4}, log=lambda s: None)
        dt = time.time() - t0
        vero = M.trova_picchi(ell, D, None, lmax=lmax, n_mc=0)
        p = ris["picchi"]
        controlla("cielo simulato: picchi ritrovati", len(p) >= 3 and all(abs(a["ell"] - b_["ell"]) < 3 * max(a["e_ell"], 2) + 4
                                                                          for a, b_ in zip(p[:3], vero[:3])),
                  "(trovati " + ", ".join(f"{q['ell']:.0f}±{q['e_ell']:.0f}" for q in p) + "; veri "
                  + ", ".join(f"{q['ell']:.0f}" for q in vero) + f"; {dt:.0f} s)")
        bb = ris["bin"]
        sel = (bb["ell"] > 100) & (bb["ell"] < 800)
        rapp = bb["D"][sel] / np.interp(bb["ell"][sel], ell, D)
        controlla("cielo simulato: ampiezza giusta (maschera, fascio, pixel corretti)", abs(np.median(rapp) - 1) < 0.06,
                  f"(rapporto mediano {np.median(rapp):.3f})")
        ba = ris["automatico_bin"]
        alto = (bb["ell"] > 850) & (bb["ell"] < 1000)
        controlla("cielo simulato: lo spettro incrociato non ha il rumore", np.mean(ba["D"][alto]) > 1.15 * np.mean(bb["D"][alto])
                  and abs(np.mean(bb["D"][alto]) / np.mean(np.interp(bb["ell"][alto], ell, D)) - 1) < 0.15,
                  f"(ℓ 850–1000: incrociato {np.mean(bb['D'][alto]):.0f}, con rumore {np.mean(ba['D'][alto]):.0f}, "
                  f"vero {np.mean(np.interp(bb['ell'][alto], ell, D)):.0f})")
        controlla("cielo usato (f_sky)", 0.6 < ris["fsky"] < 0.8, f"({ris['fsky']:.3f})")
        im = ris["immagini"]["dipolo"]
        controlla("dipolo del Sole: ±3,36 mK", abs(np.nanmax(im) - M.DIPOLO_UK) < 60, f"(max {np.nanmax(im):.0f} μK)")
        controlla("mappa di Mollweide", np.isfinite(ris["immagini"]["cmb"]).mean() > 0.7)
        import wave as _w
        with _w.open(str(ris["wav"])) as w:
            durata = w.getnframes() / w.getframerate()
        controlla("suono WAV", 10 < durata < 20, f"({durata:.1f} s)")
        controlla("scheda PDF e PNG", ris["pdf"].stat().st_size > 30000 and ris["png"].stat().st_size > 30000,
                  f"({ris['pdf'].stat().st_size} e {ris['png'].stat().st_size} byte)")
        controlla("tabelle CSV e JSON", all((ris["cartella"] / f).exists() for f in
                                            ("power_spectrum_binned.csv", "power_spectrum_unbinned.csv", "results.json",
                                             "cmb_map.png")))
        controlla("cache delle mappe ridotte", any(M.CACHE.glob("*_n512.npy")))
        sim = ris.get("simulazioni")
        controlla("errori dai cieli simulati", sim and sim["n"] == 4 and 1 in sim["picchi"] and sim["picchi"][1]["e_ell"] > 0
                  and ris["picchi"][0]["e_ell"] >= sim["picchi"][1]["e_ell"] - 1e-9,
                  f"(1° picco ± {sim['picchi'][1]['e_ell']:.1f} dalle simulazioni)" if sim and 1 in sim["picchi"] else "")
        controlla("valli sul cielo simulato", len(ris.get("valli", [])) >= 2,
                  "(" + ", ".join(f"{v['ell']:.0f}" for v in ris.get("valli", [])) + ")")
        # mappe in frequenza: FITS finti a Nside 512 ridotti a 256, poi la scheda delle 9 frequenze
        mappe = {}
        for k, (ghz, nome, unita, *_r) in enumerate(M.FREQUENZE):
            if unita == "MJy/sr":
                m = (1.0 + 50 * np.exp(-(b / 8.0) ** 2)).astype(np.float32)
            else:
                m = ((cielo + 2000 * np.exp(-(b / 5.0) ** 2) * (30.0 / ghz) ** 2) * 1e-6).astype(np.float32)
            hp.write_map(str(d / nome), hp.reorder(m, r2n=True), nest=True, coord="G", column_names=["I_STOKES"],
                         dtype=np.float32, overwrite=True)
            (d / (nome + ".ok")).write_text(str((d / nome).stat().st_size))     # niente rete nell'autotest
        voci = []
        mappe = M.scarica_frequenze(log=voci.append, cartella=d, cancella_grandi=True)
        controlla("mappe in frequenza ridotte a Nside 256 e file grandi cancellati",
                  len(mappe) == 9 and all(hp.get_nside(x) == 256 for x in mappe.values())
                  and not any((d / f[1]).exists() for f in M.FREQUENZE) and len(M.frequenze_pronte()) == 9)
        pdf, png = M.scheda_frequenze(M.immagini_frequenze(mappe), tmp / "risultati" / "frequenze")
        controlla("scheda delle 9 frequenze", png.stat().st_size > 30000, f"({png.stat().st_size} byte)")
        risultato["ris"] = ris
        M.NSIDE_ORIGINE = vecchi[2]
    prova("cielo simulato", t_cielo)

    def t_finestra():
        if not con_finestra:
            return
        if not (os.environ.get("DISPLAY") or os.environ.get("WAYLAND_DISPLAY") or sys.platform.startswith("win")):
            controlla("finestra (nessuno schermo: saltata)", True)
            return
        import tkinter as tk
        from tkinter import ttk, font as tkfont
        global CONFIG
        vecchio = CONFIG
        CONFIG = tmp / "config.json"
        try:
            App, messaggio = crea_app(tk, ttk, tkfont)
            app = App()
            app._autotest = True
            app.update()
            controlla("finestra aperta", app.winfo_exists(), f"(classe {app.winfo_class()})")
            controlla("classe della finestra per l'icona", app.winfo_class() == "Notebigbang", app.winfo_class())
            if risultato.get("ris"):
                app._nuovo_risultato(risultato["ris"])
                app.update()
                for v in VISTE:
                    app.vista.set(v)
                    app.ridisegna()
                    app.update()
            for s in ("100 %", "200 %", "150 %"):
                app.scala.set(s)
                app.applica_scala()
                app.update()
            # 1.2: alla dimensione vera della finestra, al centro di ogni pulsante deve esserci proprio quel pulsante
            larg, alt = app.winfo_screenwidth(), app.winfo_screenheight()
            importanti = {"Scarica e calcola": app.b_calcola, "Ascolta le note": app.b_ascolta, "PDF": app.b_pdf,
                          "Apri la cartella": app.b_cart, "Mappe in 9 frequenze": app.b_freq,
                          "Cambia cartella": app.b_cambia, "Guida": app.b_guida}
            importanti.update({f"grafico: {k}": b for k, b in app.pulsanti_grafico.items()})
            importanti.update({f"tendina {i + 1}": t for i, t in enumerate(app.tendine)})
            for scala in ("150 %", "200 %"):
                app.scala.set(scala)
                app.applica_scala()
                app.geometry(f"{int(larg * 0.85)}x{int(alt * 0.85)}+{int(larg * 0.05)}+{int(alt * 0.04)}")
                for _ in range(5):
                    app.update()
                    time.sleep(0.1)
                coperti = []
                for nome, b in importanti.items():
                    x = b.winfo_rootx() + b.winfo_width() // 2
                    y = b.winfo_rooty() + b.winfo_height() // 2
                    sopra = app.winfo_containing(x, y)
                    if (b.winfo_width() < 20 or b.winfo_height() < 10 or y > alt or x > larg
                            or (sopra is not b and str(sopra).rsplit(".", 1)[0] != str(b))):
                        coperti.append(f"{nome} (al centro: {sopra}, {b.winfo_width()}×{b.winfo_height()} a {x},{y})")
                controlla(f"tutti i pulsanti visibili e non coperti al {scala} ({larg}×{alt})", not coperti,
                          "; ".join(coperti[:4]))
                tagliate = [t.get() for t in app.tendine
                            if tkfont.nametofont("TkTextFont").measure(t.get()) > t.winfo_width() - t.winfo_height() - 8]
                controlla(f"testi delle tendine interi al {scala}", not tagliate, "; ".join(tagliate))
            # 1.3: «Nascondi opzioni» deve dare più altezza al grafico; la riga dei suggerimenti solo con Zoom/Sposta
            alt_prima = app.tela.get_tk_widget().winfo_height()
            app.mostra_nascondi_opzioni(False)
            for _ in range(5):
                app.update()
                time.sleep(0.1)
            alt_dopo = app.tela.get_tk_widget().winfo_height()
            controlla("«Nascondi opzioni» allarga il grafico", alt_dopo > alt_prima + 50 and not app.opz.winfo_manager(),
                      f"(altezza del grafico {alt_prima} → {alt_dopo} pixel)")
            x = app.b_opzioni.winfo_rootx() + app.b_opzioni.winfo_width() // 2
            y = app.b_opzioni.winfo_rooty() + app.b_opzioni.winfo_height() // 2
            controlla("pulsante «Mostra opzioni» visibile", app.winfo_containing(x, y) is app.b_opzioni
                      and app.b_opzioni.cget("text") == "Mostra opzioni")
            app.mostra_nascondi_opzioni(True)
            app.update()
            controlla("opzioni di nuovo visibili", bool(app.opz.winfo_manager()))
            controlla("riga dei suggerimenti nascosta quando è vuota", not app.l_strumento.winfo_manager())
            app._strumento("zoom")
            app.update()
            controlla("riga dei suggerimenti con «Zoom» attivo", bool(app.l_strumento.winfo_manager()))
            app._strumento("zoom")
            app.update()
            app.scala.set("150 %")
            app.applica_scala()
            app.update()
            app.vista.set(VISTE[6])
            app.ridisegna()
            app.update()
            controlla("vista «Il cielo in 9 frequenze» dalla cache", app.freq_img is not None and len(app.figura.axes) == 9,
                      f"({len(app.figura.axes)} mappe)")
            app.chiedi_frequenze()            # tutte già pronte: non scarica nulla
            app.update()
            app.vista.set(VISTE[0])
            app.ridisegna()
            app.update()
            controlla("grafico con i pulsanti italiani al posto della barra", len(app.pulsanti_grafico) == 6
                      and not app.barra.winfo_ismapped() and len(app.figura.axes) >= 1, f"({len(app.figura.axes)} grafici)")
            for k in ("zoom", "zoom", "pan", "pan", "home"):
                app._strumento(k)
                app.update()
            w = messaggio(app, "Prova", "Messaggio di prova con un testo abbastanza lungo da andare a capo. " * 4)
            app.update()
            w.destroy()
            app.after(2500, app.destroy)
            app.mainloop()
            controlla("finestra chiusa senza errori", True)
        finally:
            CONFIG = vecchio
    prova("finestra", t_finestra)

    M.CACHE, M.CARTELLA_DATI, M.NSIDE_ORIGINE, M.url_pla, M.url_irsa = vecchi
    ok = sum(esiti)
    print(f"\n{ok}/{len(esiti)} prove superate." + ("  TUTTO OK" if ok == len(esiti) else "  CI SONO ERRORI"), flush=True)
    shutil.rmtree(tmp, ignore_errors=True)
    return 0 if ok == len(esiti) else 1


def main():
    a = sys.argv[1:]
    if "--versione" in a:
        print(TITOLO)
        return 0
    if "--autotest" in a:
        return autotest(con_finestra="--senza-finestra" not in a)
    if "--diagnostica" in a:
        return diagnostica([x for x in a if x != "--diagnostica"])
    if "--scarica" in a:
        return scarica_da_terminale([x for x in a if x != "--scarica"])
    if "--frequenze" in a:
        return frequenze_da_terminale(a)
    if "--analizza" in a:
        return analizza_da_terminale([x for x in a if x != "--analizza"])
    avvia_finestra()
    return 0


if __name__ == "__main__":
    _rilancia_se_manca_healpy()
    sys.exit(main())
