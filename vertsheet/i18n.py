"""User-interface strings (Italian and English).

``tr(key, **fields)`` returns the string for the current language, formatted
with ``fields``. The language is detected from Windows / the locale and can be
changed at run time with :func:`set_language`.
"""
from __future__ import annotations

import locale
import os
import sys
from typing import Dict

LANGUAGES = {"it": "Italiano", "en": "English"}

IT: Dict[str, str] = {
    # ------------------------------------------------------------ application
    "app.tagline": "Spartiti per pianoforte da leggere in verticale",
    "menu.file": "File",
    "menu.open": "Apri spartito…",
    "menu.recent": "Apri recenti",
    "menu.recent.empty": "(nessun file recente)",
    "menu.recent.clear": "Cancella elenco",
    "menu.export": "Converti e salva…",
    "menu.quit": "Esci",
    "menu.view": "Visualizza",
    "menu.prev": "Pagina precedente",
    "menu.next": "Pagina successiva",
    "menu.zoom_in": "Ingrandisci",
    "menu.zoom_out": "Riduci",
    "menu.fit_page": "Adatta pagina",
    "menu.fit_width": "Adatta larghezza",
    "menu.actual": "Dimensioni reali",
    "menu.refresh": "Aggiorna anteprima",
    "menu.language": "Lingua",
    "menu.help": "Aiuto",
    "menu.howto": "Come si legge uno spartito verticale",
    "menu.formats": "Formati supportati",
    "menu.about": "Informazioni su vertSheet",
    # ------------------------------------------------------------ sidebar
    "sec.score": "1 · Spartito",
    "sec.layout": "2 · Impaginazione",
    "sec.hands": "3 · Mani",
    "sec.titles": "4 · Titoli",
    "sec.export": "5 · Esporta",
    "score.none": "Nessuno spartito aperto",
    "score.browse": "Sfoglia…",
    "score.info": "{measures} battute · {pages} pagine · {pairs} coppie di colonne",
    "score.hands": "Mani: {desc}",
    "score.staff": "Interlinea del rigo: {sp} pt",
    "layout.pairs": "Coppie di colonne per pagina",
    "layout.page": "Formato pagina",
    "layout.landscape": "Orizzontale",
    "layout.divider": "Linee divisorie",
    "divider.after": "Dopo ogni coppia",
    "divider.between": "Solo tra le coppie",
    "divider.none": "Nessuna",
    "layout.symbols": "Simboli",
    "symbols.rot": "Ruotati con il rigo",
    "symbols.upright": "Dritti",
    "layout.numbers": "Numeri di battuta",
    "numbers.all": "Tutti",
    "numbers.system": "Solo a inizio colonna",
    "numbers.none": "Nessuno",
    "layout.spacing": "Spaziatura delle note",
    "layout.staff": "Dimensione del rigo",
    "layout.staff_auto": "Automatica",
    "layout.labels": "Etichette delle mani (M.S. / M.D.)",
    "layout.justify": "Allunga l'ultima colonna fino in fondo",
    "hands.auto": "Automatico (dal file)",
    "hands.split": "Dividi un rigo singolo alla nota",
    "hands.hint": "Le note uguali o più acute vanno alla mano destra (C4 = Do centrale).",
    "titles.title": "Titolo",
    "titles.composer": "Compositore",
    "titles.hint": "Lascia vuoto per usare quelli dello spartito.",
    "export.format": "Formato",
    "format.pdf": "PDF (consigliato)",
    "format.svg": "SVG (un file per pagina)",
    "format.html": "HTML (pagina web)",
    "export.button": "Converti e salva…",
    "export.open": "Apri file",
    "export.folder": "Apri cartella",
    "export.saved": "Salvato: {name}",
    "hand.lh": "M.S.",
    "hand.rh": "M.D.",
    # ------------------------------------------------------------ preview
    "tb.page": "Pagina {i} di {n}",
    "tb.save": "Salva…",
    "tb.zoom": "{pct}%",
    "welcome.title": "Trasforma uno spartito per leggerlo in verticale",
    "welcome.body": "•  Il tempo scorre dall'alto verso il basso\n"
                    "•  Fino a 3 coppie di colonne per pagina, lette da sinistra a destra\n"
                    "•  In ogni coppia: mano sinistra a sinistra, mano destra a destra\n"
                    "•  Una linea divisoria dopo ogni coppia",
    "welcome.open": "Apri spartito…",
    "welcome.formats": "MuseScore (.mscz, .mscx)  ·  MusicXML (.musicxml, .xml, .mxl)",
    "welcome.recent": "Recenti:",
    "preview.busy": "Impaginazione in corso…",
    "preview.error": "Impossibile mostrare l'anteprima",
    # ------------------------------------------------------------ status
    "status.ready": "Pronto. Apri uno spartito per iniziare.",
    "status.loading": "Elaborazione di «{name}»…",
    "status.done": "{name}: {measures} battute su {pages} pagine ({secs} s)",
    "status.error": "Errore: {msg}",
    "status.exporting": "Salvataggio…",
    "status.saved": "Salvati {n} file in {folder}",
    "status.saved1": "Salvato {name}",
    # ------------------------------------------------------------ dialogs
    "dlg.open": "Apri spartito",
    "dlg.export": "Salva lo spartito verticale",
    "ft.scores": "Spartiti",
    "ft.all": "Tutti i file",
    "err.title": "vertSheet – errore",
    "err.notfound": "File non trovato:\n{path}",
    "err.omr": "Questo file è un PDF o un'immagine di uno spartito stampato: per leggerne le note "
               "serve un programma di riconoscimento ottico della musica (OMR).\n\n"
               "Installa Audiveris (gratuito): vertSheet lo userà automaticamente. In alternativa "
               "converti lo spartito in MusicXML con MuseScore, PlayScore o SmartScore e apri il "
               "file .musicxml.",
    "err.omr_link": "Vuoi aprire il sito di Audiveris?",
    "err.musescore": "I file {ext} vanno prima convertiti con MuseScore, che non è installato.\n\n"
                     "Installa MuseScore (gratuito) oppure esporta lo spartito in MusicXML "
                     "(File ▸ Esporta ▸ MusicXML).",
    "err.read": "Impossibile leggere lo spartito:\n{msg}",
    "err.convert": "Conversione non riuscita:\n{msg}",
    "err.generic": "Si è verificato un errore imprevisto:\n{msg}",
    "err.pitch": "Nota non valida: «{text}» (esempi: C4, F#3, Bb2)",
    "err.save": "Impossibile salvare il file:\n{msg}",
    "help.howto.body": "Come si legge\n\n"
                       "1.  Ogni pagina contiene fino a 3 coppie di colonne. Leggi la prima coppia "
                       "(a sinistra) dall'alto verso il basso, poi la seconda, poi la terza, poi "
                       "gira pagina.\n\n"
                       "2.  In ogni coppia la colonna di sinistra è la mano sinistra (M.S.) e quella "
                       "di destra è la mano destra (M.D.). I righi sono ruotati: le note più acute "
                       "stanno più a destra, come sulla tastiera.\n\n"
                       "3.  Le due mani condividono la stessa griglia del tempo: le note alla stessa "
                       "altezza si suonano insieme. Le stanghette attraversano tutta la coppia.\n\n"
                       "4.  Una linea grigia separa una coppia dalla successiva.",
    "help.formats.body": "Formati supportati\n\n"
                         "Letti direttamente:\n"
                         "•  MuseScore 1, 2, 3 e 4 (.mscz, .mscx)\n"
                         "•  MusicXML (.musicxml, .xml, .mxl)\n\n"
                         "Con MuseScore installato:\n"
                         "•  MIDI (.mid, .midi), Guitar Pro, Capella e altri\n\n"
                         "Con Audiveris installato (riconoscimento ottico):\n"
                         "•  PDF e immagini di spartiti stampati (.pdf, .png, .jpg, .tif)\n\n"
                         "Uscita: PDF, SVG (una pagina per file) o HTML.",
    "about.body": "vertSheet {version}\n\n"
                  "Re-impagina gli spartiti per pianoforte in verticale.\n\n"
                  "Nessuna libreria esterna: lettori MusicXML e MuseScore, impaginazione, simboli "
                  "musicali e file PDF sono scritti da zero in Python.\n\n"
                  "Python {py}  ·  Tk {tk}",
    # ------------------------------------------------------------ setup wizard
    "wiz.title": "Installazione di vertSheet",
    "wiz.back": "< Indietro",
    "wiz.next": "Avanti >",
    "wiz.install": "Installa",
    "wiz.finish": "Fine",
    "wiz.cancel": "Annulla",
    "wiz.cancel_confirm": "Vuoi davvero uscire dall'installazione?\nvertSheet non verrà installato.",
    "wiz.language": "Lingua:",
    "wiz.welcome.title": "Benvenuto nell'installazione di vertSheet {version}",
    "wiz.welcome.body": "Questa procedura guidata installerà vertSheet sul computer.\n\n"
                        "vertSheet trasforma gli spartiti per pianoforte (MuseScore, MusicXML) in "
                        "spartiti da leggere in verticale: il tempo scorre dall'alto verso il basso, "
                        "con fino a 3 coppie di colonne mano sinistra / mano destra per pagina.\n\n"
                        "Non servono permessi di amministratore.\n\n"
                        "Fai clic su Avanti per continuare.",
    "wiz.welcome.upgrade": "È già installata la versione {old}: verrà aggiornata alla {new}.",
    "wiz.dir.title": "Cartella di installazione",
    "wiz.dir.sub": "Dove vuoi installare vertSheet?",
    "wiz.dir.body": "vertSheet verrà installato nella cartella seguente.\n"
                    "Per sceglierne un'altra fai clic su Sfoglia.",
    "wiz.dir.browse": "Sfoglia…",
    "wiz.dir.required": "Spazio richiesto: {size}",
    "wiz.dir.free": "Spazio disponibile: {size}",
    "wiz.dir.nonempty": "La cartella «{path}» esiste già e contiene altri file.\n\n"
                        "Installare comunque vertSheet in questa cartella?",
    "wiz.dir.invalid": "Scegli una cartella valida (percorso completo, per esempio C:\\Programmi\\vertSheet).",
    "wiz.dir.nospace": "Non c'è abbastanza spazio libero sul disco selezionato.",
    "wiz.dir.noperm": "Non hai i permessi per scrivere in questa cartella.\n\nScegline un'altra "
                      "(per esempio dentro la tua cartella utente) oppure avvia l'installazione "
                      "come amministratore.",
    "wiz.opts.title": "Opzioni aggiuntive",
    "wiz.opts.sub": "Scegli cosa deve fare l'installazione oltre a copiare i file.",
    "wiz.opts.desktop": "Crea un collegamento sul desktop",
    "wiz.opts.startmenu": "Crea un collegamento nel menu Start",
    "wiz.opts.assoc": "Aggiungi vertSheet ad «Apri con» per i file .mscz, .mscx, .musicxml e .mxl",
    "wiz.opts.assoc_hint": "Le app predefinite non vengono modificate.",
    "wiz.opts.path": "Aggiungi il comando «vertsheet-cli» al PATH (prompt dei comandi)",
    "wiz.ready.title": "Pronto per l'installazione",
    "wiz.ready.sub": "Controlla le impostazioni e fai clic su Installa.",
    "wiz.ready.dir": "Cartella di installazione:",
    "wiz.ready.tasks": "Operazioni:",
    "wiz.ready.copy": "Copia dei file del programma ({size})",
    "wiz.ready.uninstall": "Registrazione in «App installate» per poterlo disinstallare",
    "wiz.progress.title": "Installazione in corso",
    "wiz.progress.sub": "Attendi mentre vertSheet viene installato sul computer.",
    "wiz.step.prepare": "Preparazione…",
    "wiz.step.remove_old": "Rimozione della versione precedente…",
    "wiz.step.extract": "Estrazione: {name}",
    "wiz.step.shortcuts": "Creazione dei collegamenti…",
    "wiz.step.register": "Registrazione nel sistema…",
    "wiz.step.done": "Completato.",
    "wiz.error": "L'installazione non è riuscita:\n\n{msg}",
    "wiz.running": "vertSheet è aperto: chiudilo e riprova.",
    "wiz.done.title": "Installazione completata",
    "wiz.done.body": "vertSheet {version} è stato installato correttamente.\n\n"
                     "Puoi avviarlo dal menu Start, dal collegamento sul desktop oppure facendo "
                     "clic destro su uno spartito ▸ Apri con ▸ vertSheet.\n\n"
                     "Per disinstallarlo: Impostazioni ▸ App ▸ App installate ▸ vertSheet.",
    "wiz.done.launch": "Avvia vertSheet ora",
    "shortcut.desc": "Spartiti per pianoforte da leggere in verticale",
    "assoc.desc": "Spartito (vertSheet)",
    # ------------------------------------------------------------ uninstall
    "uni.title": "Disinstallazione di vertSheet",
    "uni.confirm": "Vuoi rimuovere completamente vertSheet {version} dal computer?\n\n"
                   "Verranno eliminati il programma, i collegamenti e le voci di registro create "
                   "durante l'installazione. Gli spartiti che hai creato non verranno toccati.",
    "uni.done": "vertSheet è stato rimosso dal computer.",
    "uni.notfound": "In questa cartella non è stata trovata un'installazione di vertSheet.",
    "uni.error": "La disinstallazione non è riuscita:\n\n{msg}",
}

EN: Dict[str, str] = {
    "app.tagline": "Piano sheet music, read vertically",
    "menu.file": "File",
    "menu.open": "Open score…",
    "menu.recent": "Open recent",
    "menu.recent.empty": "(no recent files)",
    "menu.recent.clear": "Clear list",
    "menu.export": "Convert and save…",
    "menu.quit": "Quit",
    "menu.view": "View",
    "menu.prev": "Previous page",
    "menu.next": "Next page",
    "menu.zoom_in": "Zoom in",
    "menu.zoom_out": "Zoom out",
    "menu.fit_page": "Fit page",
    "menu.fit_width": "Fit width",
    "menu.actual": "Actual size",
    "menu.refresh": "Refresh preview",
    "menu.language": "Language",
    "menu.help": "Help",
    "menu.howto": "How to read a vertical score",
    "menu.formats": "Supported formats",
    "menu.about": "About vertSheet",
    "sec.score": "1 · Score",
    "sec.layout": "2 · Layout",
    "sec.hands": "3 · Hands",
    "sec.titles": "4 · Titles",
    "sec.export": "5 · Export",
    "score.none": "No score open",
    "score.browse": "Browse…",
    "score.info": "{measures} measures · {pages} pages · {pairs} column pairs",
    "score.hands": "Hands: {desc}",
    "score.staff": "Staff space: {sp} pt",
    "layout.pairs": "Column pairs per page",
    "layout.page": "Page size",
    "layout.landscape": "Landscape",
    "layout.divider": "Divider lines",
    "divider.after": "After every pair",
    "divider.between": "Only between pairs",
    "divider.none": "None",
    "layout.symbols": "Symbols",
    "symbols.rot": "Rotated with the staff",
    "symbols.upright": "Upright",
    "layout.numbers": "Measure numbers",
    "numbers.all": "All",
    "numbers.system": "Start of each column",
    "numbers.none": "None",
    "layout.spacing": "Note spacing",
    "layout.staff": "Staff size",
    "layout.staff_auto": "Automatic",
    "layout.labels": "Hand labels (L.H. / R.H.)",
    "layout.justify": "Stretch the last column to the page bottom",
    "hands.auto": "Automatic (from the file)",
    "hands.split": "Split a single staff at pitch",
    "hands.hint": "Notes at or above it go to the right hand (C4 = middle C).",
    "titles.title": "Title",
    "titles.composer": "Composer",
    "titles.hint": "Leave empty to use the ones in the score.",
    "export.format": "Format",
    "format.pdf": "PDF (recommended)",
    "format.svg": "SVG (one file per page)",
    "format.html": "HTML (web page)",
    "export.button": "Convert and save…",
    "export.open": "Open file",
    "export.folder": "Show in folder",
    "export.saved": "Saved: {name}",
    "hand.lh": "L.H.",
    "hand.rh": "R.H.",
    "tb.page": "Page {i} of {n}",
    "tb.save": "Save…",
    "tb.zoom": "{pct}%",
    "welcome.title": "Turn sheet music into a vertical score",
    "welcome.body": "•  Time runs from the top of the page to the bottom\n"
                    "•  Up to 3 pairs of columns per page, read from left to right\n"
                    "•  In every pair: left hand on the left, right hand on the right\n"
                    "•  A divider line after every pair",
    "welcome.open": "Open score…",
    "welcome.formats": "MuseScore (.mscz, .mscx)  ·  MusicXML (.musicxml, .xml, .mxl)",
    "welcome.recent": "Recent:",
    "preview.busy": "Laying out the score…",
    "preview.error": "The preview cannot be shown",
    "status.ready": "Ready. Open a score to start.",
    "status.loading": "Processing “{name}”…",
    "status.done": "{name}: {measures} measures on {pages} pages ({secs} s)",
    "status.error": "Error: {msg}",
    "status.exporting": "Saving…",
    "status.saved": "Saved {n} files in {folder}",
    "status.saved1": "Saved {name}",
    "dlg.open": "Open score",
    "dlg.export": "Save the vertical score",
    "ft.scores": "Scores",
    "ft.all": "All files",
    "err.title": "vertSheet – error",
    "err.notfound": "File not found:\n{path}",
    "err.omr": "This file is a PDF or an image of printed sheet music: reading its notes needs an "
               "optical music recognition (OMR) program.\n\n"
               "Install Audiveris (free) and vertSheet will use it automatically. Otherwise "
               "convert the score to MusicXML with MuseScore, PlayScore or SmartScore and open the "
               ".musicxml file.",
    "err.omr_link": "Open the Audiveris website?",
    "err.musescore": "{ext} files must first be converted by MuseScore, which is not installed.\n\n"
                     "Install MuseScore (free) or export the score as MusicXML "
                     "(File ▸ Export ▸ MusicXML).",
    "err.read": "The score cannot be read:\n{msg}",
    "err.convert": "Conversion failed:\n{msg}",
    "err.generic": "An unexpected error occurred:\n{msg}",
    "err.pitch": "Invalid pitch “{text}” (examples: C4, F#3, Bb2)",
    "err.save": "The file cannot be saved:\n{msg}",
    "help.howto.body": "How to read it\n\n"
                       "1.  Every page holds up to 3 pairs of columns. Read the first pair (on the "
                       "left) from top to bottom, then the second, then the third, then turn the "
                       "page.\n\n"
                       "2.  In every pair the left column is the left hand (L.H.) and the right "
                       "column is the right hand (R.H.). The staves are rotated: higher notes are "
                       "further right, as on the keyboard.\n\n"
                       "3.  Both hands share one time grid: notes at the same height are played "
                       "together. Barlines run across the whole pair.\n\n"
                       "4.  A grey line separates one pair from the next.",
    "help.formats.body": "Supported formats\n\n"
                         "Read directly:\n"
                         "•  MuseScore 1, 2, 3 and 4 (.mscz, .mscx)\n"
                         "•  MusicXML (.musicxml, .xml, .mxl)\n\n"
                         "With MuseScore installed:\n"
                         "•  MIDI (.mid, .midi), Guitar Pro, Capella and more\n\n"
                         "With Audiveris installed (optical recognition):\n"
                         "•  PDFs and images of printed music (.pdf, .png, .jpg, .tif)\n\n"
                         "Output: PDF, SVG (one page per file) or HTML.",
    "about.body": "vertSheet {version}\n\n"
                  "Re-engraves piano sheet music to be read vertically.\n\n"
                  "No third-party libraries: the MusicXML and MuseScore readers, the layout "
                  "engine, the music symbols and the PDF writer are all written from scratch in "
                  "Python.\n\n"
                  "Python {py}  ·  Tk {tk}",
    "wiz.title": "vertSheet Setup",
    "wiz.back": "< Back",
    "wiz.next": "Next >",
    "wiz.install": "Install",
    "wiz.finish": "Finish",
    "wiz.cancel": "Cancel",
    "wiz.cancel_confirm": "Do you really want to quit Setup?\nvertSheet will not be installed.",
    "wiz.language": "Language:",
    "wiz.welcome.title": "Welcome to the vertSheet {version} Setup",
    "wiz.welcome.body": "This wizard will install vertSheet on your computer.\n\n"
                        "vertSheet turns piano sheet music (MuseScore, MusicXML) into scores that "
                        "are read vertically: time runs from top to bottom, with up to 3 pairs of "
                        "left-hand / right-hand columns per page.\n\n"
                        "No administrator rights are needed.\n\n"
                        "Click Next to continue.",
    "wiz.welcome.upgrade": "Version {old} is already installed: it will be upgraded to {new}.",
    "wiz.dir.title": "Destination folder",
    "wiz.dir.sub": "Where should vertSheet be installed?",
    "wiz.dir.body": "vertSheet will be installed in the following folder.\n"
                    "To choose a different one, click Browse.",
    "wiz.dir.browse": "Browse…",
    "wiz.dir.required": "Space required: {size}",
    "wiz.dir.free": "Space available: {size}",
    "wiz.dir.nonempty": "The folder “{path}” already exists and contains other files.\n\n"
                        "Install vertSheet in this folder anyway?",
    "wiz.dir.invalid": "Please choose a valid folder (a full path, e.g. C:\\Programs\\vertSheet).",
    "wiz.dir.nospace": "There is not enough free space on the selected drive.",
    "wiz.dir.noperm": "You do not have permission to write to this folder.\n\nChoose another one "
                      "(for example inside your user folder) or run Setup as administrator.",
    "wiz.opts.title": "Additional options",
    "wiz.opts.sub": "Choose what Setup should do besides copying the files.",
    "wiz.opts.desktop": "Create a desktop shortcut",
    "wiz.opts.startmenu": "Create a Start menu shortcut",
    "wiz.opts.assoc": "Add vertSheet to “Open with” for .mscz, .mscx, .musicxml and .mxl files",
    "wiz.opts.assoc_hint": "Your default apps are not changed.",
    "wiz.opts.path": "Add the “vertsheet-cli” command to PATH (command prompt)",
    "wiz.ready.title": "Ready to install",
    "wiz.ready.sub": "Review the settings and click Install.",
    "wiz.ready.dir": "Destination folder:",
    "wiz.ready.tasks": "Tasks:",
    "wiz.ready.copy": "Copy the program files ({size})",
    "wiz.ready.uninstall": "Register in “Installed apps” so it can be uninstalled",
    "wiz.progress.title": "Installing",
    "wiz.progress.sub": "Please wait while vertSheet is installed on your computer.",
    "wiz.step.prepare": "Preparing…",
    "wiz.step.remove_old": "Removing the previous version…",
    "wiz.step.extract": "Extracting: {name}",
    "wiz.step.shortcuts": "Creating shortcuts…",
    "wiz.step.register": "Registering with Windows…",
    "wiz.step.done": "Done.",
    "wiz.error": "Setup failed:\n\n{msg}",
    "wiz.running": "vertSheet is running: close it and try again.",
    "wiz.done.title": "Setup complete",
    "wiz.done.body": "vertSheet {version} has been installed.\n\n"
                     "Start it from the Start menu, from the desktop shortcut, or by right-clicking "
                     "a score ▸ Open with ▸ vertSheet.\n\n"
                     "To uninstall it: Settings ▸ Apps ▸ Installed apps ▸ vertSheet.",
    "wiz.done.launch": "Launch vertSheet now",
    "shortcut.desc": "Piano sheet music, read vertically",
    "assoc.desc": "Score (vertSheet)",
    "uni.title": "Uninstall vertSheet",
    "uni.confirm": "Completely remove vertSheet {version} from this computer?\n\n"
                   "The program, its shortcuts and the registry entries created by Setup will be "
                   "deleted. The scores you created are not touched.",
    "uni.done": "vertSheet has been removed from your computer.",
    "uni.notfound": "No vertSheet installation was found in this folder.",
    "uni.error": "Uninstalling failed:\n\n{msg}",
}

STRINGS = {"it": IT, "en": EN}
_lang = "en"


def detect_language() -> str:
    """'it' when the Windows UI language or the locale is Italian, else 'en'."""
    if sys.platform == "win32":
        try:
            import ctypes
            lang_id = ctypes.windll.kernel32.GetUserDefaultUILanguage()
            return "it" if (lang_id & 0x3FF) == 0x10 else "en"
        except (AttributeError, OSError):
            pass
    for var in ("LC_ALL", "LC_MESSAGES", "LANG"):
        if os.environ.get(var):
            return "it" if os.environ[var].lower().startswith("it") else "en"
    try:
        loc = locale.getlocale()[0] or ""
    except ValueError:
        loc = ""
    return "it" if loc.lower().startswith(("it", "italian")) else "en"


def set_language(lang: str) -> str:
    global _lang
    _lang = lang if lang in STRINGS else "en"
    return _lang


def get_language() -> str:
    return _lang


def tr(key: str, **fields) -> str:
    text = STRINGS[_lang].get(key)
    if text is None:
        text = EN.get(key, key)
    return text.format(**fields) if fields else text


set_language(detect_language())
