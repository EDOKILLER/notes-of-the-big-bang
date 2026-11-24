#!/usr/bin/env bash
# =============================================================================
# Le note del Big Bang 1.4 – crea l'eseguibile per Ubuntu, lo prova e lo installa (menu + Scrivania)
# © 2026 Edoardo Battistel
#
# Uso:  cd ~/Claude/Astropy/programma_note_big_bang/versione_1.4
#       bash crea_eseguibile_ubuntu.sh 2>&1 | tee compila.txt
# Usa il venv ~/Claude/Astropy/venv_compila (PyInstaller + healpy), che vede numpy, scipy,
# astropy, matplotlib e Pillow di sistema.
# =============================================================================
set -e
set -o pipefail
cd "$(dirname "$(readlink -f "$0")")"
PY="$HOME/Claude/Astropy/venv_compila/bin/python"

for f in note_big_bang.py motore_cmb.py crea_icona.py LEGGIMI.txt; do
    [ -f "$f" ] || { echo "ERRORE: manca $f in questa cartella ($(pwd))."; exit 1; }
done
[ -x "$PY" ] || { echo "ERRORE: non trovo $PY"; exit 1; }
"$PY" -c "import healpy" 2>/dev/null || { echo "ERRORE: nel venv manca healpy: $PY -m pip install healpy"; exit 1; }
[ -f note_big_bang.png ] || "$PY" crea_icona.py

echo "=== 1/5  Autotest dallo script (cielo simulato + finestra per qualche secondo) ==="
if ! "$PY" note_big_bang.py --autotest; then
    echo "L'autotest dello script è FALLITO: mandami il testo qui sopra."
    exit 1
fi

echo "=== 2/5  Creazione dell'eseguibile (qualche minuto) ==="
"$PY" -m PyInstaller --noconfirm --clean --onefile --windowed \
    --name NoteBigBang \
    --add-data "note_big_bang.png:." \
    --add-data "LEGGIMI.txt:." \
    --hidden-import motore_cmb \
    --hidden-import PIL._tkinter_finder \
    --hidden-import matplotlib.backends.backend_tkagg \
    --hidden-import matplotlib.backends.backend_pdf \
    --collect-all healpy \
    --collect-all astropy --collect-all erfa \
    --collect-data astropy_iers_data \
    --copy-metadata astropy --copy-metadata numpy --copy-metadata pyerfa --copy-metadata healpy \
    --exclude-module sklearn --exclude-module IPython --exclude-module pandas \
    note_big_bang.py

echo "=== 3/5  Verifica dell'eseguibile (autotest) ==="
if ! ./dist/NoteBigBang --autotest > autotest.txt 2>&1; then
    cat autotest.txt
    echo
    echo "L'autotest dell'eseguibile è FALLITO: i dettagli sono in $(pwd)/autotest.txt"
    echo "L'eseguibile non è stato installato. Mandami autotest.txt per la correzione."
    rm -rf build NoteBigBang.spec
    exit 1
fi
cat autotest.txt

echo "=== 4/5  Prova degli archivi in rete (diagnostica.txt) ==="
./dist/NoteBigBang --diagnostica || echo "(qualche archivio non risponde: vedi diagnostica.txt; il programma si installa lo stesso)"

echo "=== 5/5  Installazione nella home e icona nel menu ==="
DEST="$HOME/.local/bin"
ICONE="$HOME/.local/share/icons/hicolor/512x512/apps"
mkdir -p "$DEST" "$HOME/.local/share/applications" "$ICONE"
cp note_big_bang.png "$ICONE/notebigbang.png"
gtk-update-icon-cache -q "$HOME/.local/share/icons/hicolor" 2>/dev/null || true
cp dist/NoteBigBang "$DEST/NoteBigBang"
chmod +x "$DEST/NoteBigBang"

# tk.Tk(className="NoteBigBang") -> classe della finestra «Notebigbang» (solo la prima maiuscola)
SCRIVANIA="$(xdg-user-dir DESKTOP 2>/dev/null || echo "$HOME/Scrivania")"
cat > "$HOME/.local/share/applications/notebigbang.desktop" <<FINE
[Desktop Entry]
Type=Application
Name=Le note del Big Bang
Comment=I picchi acustici della radiazione cosmica di fondo dalle mappe Planck, e il loro suono – © 2026 Edoardo Battistel
Exec=$DEST/NoteBigBang
Icon=$ICONE/notebigbang.png
StartupWMClass=Notebigbang
Terminal=false
Categories=Science;Astronomy;Education;
FINE
chmod +x "$HOME/.local/share/applications/notebigbang.desktop"
update-desktop-database -q "$HOME/.local/share/applications" 2>/dev/null || true
if [ -d "$SCRIVANIA" ]; then
    cp "$HOME/.local/share/applications/notebigbang.desktop" "$SCRIVANIA/"
    chmod +x "$SCRIVANIA/notebigbang.desktop"
    gio set "$SCRIVANIA/notebigbang.desktop" metadata::trusted true 2>/dev/null || true
fi

rm -rf build NoteBigBang.spec
echo
echo "FATTO."
echo "  Eseguibile:  $DEST/NoteBigBang   (copia anche in $(pwd)/dist/)"
echo "  Avvio:       'Le note del Big Bang' nel menu o doppio clic sull'icona sulla Scrivania"
echo "               (la prima volta: tasto destro -> 'Consenti avvio')."
echo "  Prova vera:  $DEST/NoteBigBang --analizza --nside 1024 2>&1 | tee prova_1024.txt"
echo "               (usa i file già scaricati; con 20 cieli simulati per gli errori ci vogliono ~5 minuti)"
echo "  Frequenze:   $DEST/NoteBigBang --frequenze 2>&1 | tee prova_frequenze.txt   (~10,8 GB da scaricare, poi ridotti)"
echo "  Controllo dell'icona nella barra: xwininfo -root -tree | grep -i notebigbang"
