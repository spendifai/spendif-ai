# -*- mode: python ; coding: utf-8 -*-
"""PyInstaller spec for Spendif.ai native desktop app.

Build:
    cd sw_artifacts
    uv run --extra desktop pyinstaller desktop.spec --noconfirm --clean

Produces:
    macOS  → dist/SpendifAi.app
    Windows → dist/SpendifAi/SpendifAi.exe
"""
import sys
from pathlib import Path
from PyInstaller.utils.hooks import collect_all, collect_data_files

# ---------------------------------------------------------------------------
# Streamlit assets (templates, static files, etc.)
# ---------------------------------------------------------------------------
st_datas, st_binaries, st_hiddenimports = collect_all("streamlit")

# Plotly needs its own data files
plotly_datas = collect_data_files("plotly")

# llama-cpp-python: imported lazily inside core.llm_backends, so PyInstaller
# cannot discover it via static analysis. Pull in the python package AND the
# shipped shared libs (libllama, libggml*, libmtmd, …) which ctypes loads at
# runtime from `<llama_cpp>/lib/`. Without these the bundled app crashes on
# first model load with `Shared library with base name 'llama' not found`.
llama_datas, llama_binaries, llama_hiddenimports = collect_all("llama_cpp")

# ---------------------------------------------------------------------------
# Application packages to bundle alongside the frozen launcher
# ---------------------------------------------------------------------------
# nsi/ is deliberately absent. It is the generation input for
# core/static_rules.json, and core/static_rules.json is what runs
# (core/nsi_lookup.py reads it). Listing nsi/ here shipped 16 MB into
# locally built artifacts and nothing into CI ones, because the directory
# is gitignored: same artifact name, different contents per builder.
APP_PACKAGES = [
    "app.py",
    "api",
    "chat_bot",
    "config",
    "core",
    "db",
    "desktop",
    "prompts",
    "reports",
    "services",
    "support",
    "ui",
]

app_datas = []
for pkg in APP_PACKAGES:
    src = Path(pkg)
    if src.is_file():
        app_datas.append((str(src), "."))
    elif src.is_dir():
        app_datas.append((str(src), pkg))

# Splash HTML
app_datas.append(("desktop/splash.html", "desktop"))

# .env.example as fallback
if Path(".env.example").exists():
    app_datas.append((".env.example", "."))

# VERSION file — single source of truth for the app version
_app_version = (
    Path("VERSION").read_text(encoding="utf-8").strip()
    if Path("VERSION").exists()
    else "0.0.0"
)
if Path("VERSION").exists():
    app_datas.append(("VERSION", "."))

# ---------------------------------------------------------------------------
# Hidden imports that PyInstaller cannot auto-detect
# ---------------------------------------------------------------------------
hidden_imports = [
    # App modules
    "api.main",
    "config",
    "core.orchestrator",
    "core.classifier",
    "core.categorizer",
    "core.normalizer",
    "core.sanitizer",
    "core.model_manager",
    "core.llm_backends",
    "core.schemas",
    "core.models",
    "db.models",
    "db.repository",
    "services.import_service",
    "services.settings_service",
    "support.logging",
    # Third-party
    "uvicorn.logging",
    "uvicorn.protocols.http",
    "uvicorn.protocols.http.auto",
    "uvicorn.protocols.websockets",
    "uvicorn.protocols.websockets.auto",
    "uvicorn.lifespan",
    "uvicorn.lifespan.on",
    "pydantic",
    "sqlalchemy.dialects.sqlite",
    "plotly",
    "openpyxl",
    "chardet",
    "yaml",
    "dotenv",
    "webview",
    "llama_cpp",
] + st_hiddenimports + llama_hiddenimports

# ---------------------------------------------------------------------------
# Icon (platform-dependent)
# ---------------------------------------------------------------------------
icon_path = None
if sys.platform == "darwin":
    icns = Path("packaging/macos/spendifai.icns")
    if icns.exists():
        icon_path = str(icns)
elif sys.platform == "win32":
    ico = Path("packaging/windows/spendifai.ico")
    if ico.exists():
        icon_path = str(ico)

# ---------------------------------------------------------------------------
# Analysis
# ---------------------------------------------------------------------------
a = Analysis(
    ["desktop/launcher.py"],
    pathex=["."],
    binaries=st_binaries + llama_binaries,
    datas=app_datas + st_datas + plotly_datas + llama_datas,
    hiddenimports=hidden_imports,
    hookspath=[],
    hooksconfig={},
    runtime_hooks=[],
    excludes=[
        # Exclude heavy optional deps not needed in desktop mode
        "azure",
        "azure.ai",
        "azure.identity",
        "matplotlib",
        "IPython",
        "notebook",
        "tkinter",
    ],
    noarchive=False,
)

# PyInstaller delega lo strip alla toolchain GNU, che e' fatta per ELF: sui
# binari PE di Windows produce file che il caricatore non accetta piu'. La
# documentazione ufficiale lo dichiara "not recommended for Windows", e il
# 2026-09-22 e' costato un pacchetto MSIX che si installava e non partiva:
# python312.dll rovinato, LoadLibrary "Invalid access to memory location",
# nessun log perche' Python non arrivava a girare. Su macOS resta attivo.
STRIP = sys.platform != "win32"

pyz = PYZ(a.pure)

exe = EXE(
    pyz,
    a.scripts,
    [],
    exclude_binaries=True,  # --onedir mode
    name="SpendifAi",
    debug=False,
    bootloader_ignore_signals=False,
    strip=STRIP,
    upx=False,
    # console=True esisteva per non perdere i traceback: senza console, su
    # macOS stdout e stderr finivano in /dev/null e un errore prima della
    # finestra era invisibile. Quella ragione e' venuta meno, perche' il
    # launcher redirige entrambi su file e da oggi ci manda anche l'output del
    # processo Streamlit, che e' dove succede il lavoro vero.
    #
    # Il prezzo di lasciarla accesa lo paga Windows: una finestra di comandi
    # nera resta aperta dietro l'app per tutta la sessione.
    #
    # Resta scoperto un caso solo: un errore del bootloader di PyInstaller,
    # prima che Python parta. Li' non c'e' file di log perche' non c'e' ancora
    # nessuno a scriverlo.
    console=False,
    icon=icon_path,
)

coll = COLLECT(
    exe,
    a.binaries,
    a.datas,
    strip=STRIP,
    upx=False,
    name="SpendifAi",
)

# macOS .app bundle
if sys.platform == "darwin":
    app = BUNDLE(
        coll,
        name="SpendifAi.app",
        icon=icon_path,
        bundle_identifier="ai.spendif.desktop",
        info_plist={
            "CFBundleName": "Spendif.ai",
            "CFBundleDisplayName": "Spendif.ai",
            "CFBundleShortVersionString": _app_version,
            "CFBundleVersion": _app_version,
            "LSMinimumSystemVersion": "12.0",
            "NSHighResolutionCapable": True,
        },
    )
