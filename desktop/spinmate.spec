# PyInstaller spec for the SpinMate desktop app. From the repo root:
#   pyinstaller desktop/spinmate.spec --noconfirm
# Output: dist/SpinMate/ (SpinMate.exe + _internal). One folder, not one file: every run starts the exe
# again with --runner, and a one-file exe would unpack itself each time.
import os

from PyInstaller.utils.hooks import collect_data_files, collect_submodules

ROOT = os.path.abspath(os.path.join(SPECPATH, ".."))
MACROSPIN = ["main", "importdata", "conf", "current", "output", "run", "solver"]  # original code, by module name

a = Analysis(
    [os.path.join(SPECPATH, "spinmate.py")],
    pathex=[ROOT, os.path.join(ROOT, "macrospin")],
    datas=[(os.path.join(ROOT, "web"), "web"),
           (os.path.join(ROOT, "examples"), "examples")]
          + collect_data_files("scienceplots") + collect_data_files("latex2mathml"),
    hiddenimports=MACROSPIN + collect_submodules("app") + collect_submodules("spinmate_mcp") + collect_submodules("mcp") + collect_submodules("uvicorn")
                  + ["scipy.signal", "matplotlib.backends.backend_agg", "multipart"],
    excludes=["tkinter", "PyQt5", "PyQt6", "PySide2", "PySide6", "IPython", "pytest", "psycopg"],
    noarchive=False,
)
pyz = PYZ(a.pure)
exe = EXE(
    pyz, a.scripts, [],
    exclude_binaries=True,
    name="SpinMate",
    icon=os.path.join(SPECPATH, "spinmate.ico"),
    console=True,          # the window shows the address and stops the app when closed
)
coll = COLLECT(exe, a.binaries, a.datas, name="SpinMate")
