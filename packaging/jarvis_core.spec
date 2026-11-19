# -*- mode: python ; coding: utf-8 -*-
"""Specification PyInstaller du binaire JARVIS 2.0 (Windows / Linux).

Produit un dossier `dist/JARVIS/` contenant `jarvis.exe` (ou `jarvis`), a livrer avec
`config/`, `web/dist/` et `.env.example` copies a cote par scripts/build_windows.ps1.

Construction :
    pyinstaller packaging/jarvis_core.spec --noconfirm --clean
"""

from pathlib import Path

from PyInstaller.utils.hooks import collect_submodules

ROOT = Path(SPECPATH).resolve().parent  # noqa: F821 - fourni par PyInstaller

hiddenimports = []
for package in ("jarvis_core", "jarvis_shared", "jarvis_desktop"):
    hiddenimports += collect_submodules(package)

# Serveurs et protocoles charges dynamiquement par uvicorn.
hiddenimports += [
    "uvicorn.logging",
    "uvicorn.loops",
    "uvicorn.loops.auto",
    "uvicorn.loops.asyncio",
    "uvicorn.protocols",
    "uvicorn.protocols.http",
    "uvicorn.protocols.http.auto",
    "uvicorn.protocols.http.h11_impl",
    "uvicorn.protocols.websockets",
    "uvicorn.protocols.websockets.auto",
    "uvicorn.protocols.websockets.websockets_impl",
    "uvicorn.lifespan",
    "uvicorn.lifespan.on",
    "uvicorn.lifespan.off",
    "apscheduler.triggers.cron",
    "apscheduler.schedulers.asyncio",
    "aiogram",
    "sqlite3",
    "jarvis_shared.codegen",
]

datas = []
# Icone de l'application si disponible (Windows uniquement).
icon_file = ROOT / "jarvis_icon.ico"
icon = str(icon_file) if icon_file.is_file() else None

a = Analysis(  # noqa: F821
    [str(ROOT / "packaging" / "launcher.py")],
    pathex=[str(ROOT), str(ROOT / "packages" / "shared"), str(ROOT / "packages" / "core"),
            str(ROOT / "packages" / "desktop")],
    binaries=[],
    datas=datas,
    hiddenimports=hiddenimports,
    hookspath=[],
    hooksconfig={},
    runtime_hooks=[],
    excludes=["tkinter", "matplotlib", "PyQt5", "PyQt6", "PySide6", "pytest"],
    noarchive=False,
)

pyz = PYZ(a.pure)  # noqa: F821

exe = EXE(  # noqa: F821
    pyz,
    a.scripts,
    [],
    exclude_binaries=True,
    name="jarvis",
    debug=False,
    bootloader_ignore_signals=False,
    strip=False,
    upx=False,
    console=False,
    disable_windowed_traceback=False,
    icon=icon,
)

coll = COLLECT(  # noqa: F821
    exe,
    a.binaries,
    a.datas,
    strip=False,
    upx=False,
    name="JARVIS",
)
