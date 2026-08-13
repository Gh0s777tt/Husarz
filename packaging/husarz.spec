# -*- mode: python ; coding: utf-8 -*-
# ============================================================================
# PyInstaller spec — jeden plik wykonywalny launchera Husarza (husarz-app).
# Budowa (w środowisku z zależnościami + pyinstaller):
#   pyinstaller packaging/husarz.spec --noconfirm
# Wynik: dist/husarz-app(.exe). Uruchomienie startuje serwer i otwiera konsolę.
#
# Dołączamy: dane pakietu husarz (konsola api/static/*.html) oraz DOMYŚLNE
# config/ i prompts/, by binarka działała out-of-the-box (operator może nadpisać
# --config/--prompts). NIE dołączamy sekretów ani wag (patrz .gitignore/.dockerignore).
# ============================================================================
from pathlib import Path

from PyInstaller.utils.hooks import collect_data_files

# Ścieżka projektu (katalog nadrzędny 'packaging/').
ROOT = Path(SPECPATH).parent

datas = collect_data_files("husarz")  # m.in. api/static/console.html, py.typed
datas += [(str(ROOT / "config"), "config"), (str(ROOT / "prompts"), "prompts")]

# Importy ładowane leniwie w launcherze — jawnie zadeklarowane dla PyInstaller.
hiddenimports = [
    "uvicorn",
    "uvicorn.logging",
    "uvicorn.loops.auto",
    "uvicorn.protocols.http.auto",
    "uvicorn.protocols.websockets.auto",
    "uvicorn.lifespan.on",
    "fastapi",
    "husarz.api",
    "husarz.accounts",
    "husarz.git",
]

a = Analysis(
    [str(ROOT / "packaging" / "husarz_app.py")],
    pathex=[str(ROOT / "src")],
    datas=datas,
    hiddenimports=hiddenimports,
    hookspath=[],
    runtime_hooks=[],
    excludes=[],
)
pyz = PYZ(a.pure)

exe = EXE(
    pyz,
    a.scripts,
    a.binaries,
    a.datas,
    name="husarz-app",
    console=True,      # okno konsoli z adresem/logami; serwer blokuje w foreground
    onefile=True,
    upx=False,
)
