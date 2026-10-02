# PyInstaller spec for pyfa-mcp.exe, the stdio MCP server.
#
#     .venv/Scripts/python -m PyInstaller --noconfirm packaging/pyfa-mcp.spec
#
# From the repo root, with vendor/Pyfa checked out and its eve.db generated
# (scripts/make_evedb.py). Output: dist/pyfa-mcp/, one folder: a one-file
# build would unpack 190 MB on every client start.
import sys
import tomllib
from importlib import metadata
from pathlib import Path

from PyInstaller.utils.hooks import collect_submodules, copy_metadata

ROOT = Path(SPECPATH).parent
PYFA = ROOT / "vendor" / "Pyfa"
sys.path.insert(0, str(ROOT))
from scripts import pyfa_imports  # noqa: E402

for needed in (PYFA / "eos" / "__init__.py", PYFA / "eve.db"):
    if not needed.is_file():
        raise SystemExit(f"missing {needed}: run 'git submodule update --init' "
                         "and scripts/make_evedb.py")

# Pyfa travels as source under pyfa/, where eosboot.pyfa_dir() looks when
# frozen: eos imports its effects and migrations by name, which PyInstaller
# cannot follow. The price is that the analysis sees none of Pyfa's imports,
# so they are read from Pyfa's own source (scripts/pyfa_imports.py) -- which
# keeps this file in step with every Pyfa bump.
missing = pyfa_imports.missing()
if missing:
    raise SystemExit(f"Pyfa imports {', '.join(missing)}, which is not installed: add it to "
                     "pyproject.toml, or to KNOWN_ABSENT in scripts/pyfa_imports.py")

# drift._own_version() reads this metadata; a stale egg-info would label the
# build with an old version.
built = metadata.version("pyfa-mcp")
declared = tomllib.loads((ROOT / "pyproject.toml").read_text(encoding="utf-8"))["project"]["version"]
if built != declared:
    raise SystemExit(f"installed pyfa-mcp metadata says {built}, pyproject.toml {declared}: "
                     "run uv sync")

datas = [(str(PYFA / package), f"pyfa/{package}") for package in pyfa_imports.PACKAGES]
datas += [(str(PYFA / name), "pyfa") for name in ("config.py", "version.yml", "eve.db")]
datas += [(str(ROOT / "pyfa_mcp" / "unhandled_effects.json"), "pyfa_mcp")]
datas += copy_metadata("pyfa-mcp")

hiddenimports = pyfa_imports.hidden_imports()
# Loaded by name at runtime: SQLAlchemy dialects, logbook handlers, crypto backends.
for package in ("sqlalchemy", "logbook", "cryptography"):
    hiddenimports += collect_submodules(package)
# mcp.cli is the SDK's developer command and needs the optional typer.
hiddenimports += collect_submodules("mcp", filter=lambda name: not name.startswith("mcp.cli"))

a = Analysis(
    [str(ROOT / "pyfa_mcp" / "__main__.py")],
    pathex=[str(ROOT)],
    datas=datas,
    hiddenimports=hiddenimports,
    # wx is answered by pyfa_mcp.wxstub; a real one must never be pulled in.
    excludes=["wx", "pytest"],
)
exe = EXE(PYZ(a.pure), a.scripts, [], exclude_binaries=True, name="pyfa-mcp",
          console=True)  # stdio is the protocol: a windowed exe has none
coll = COLLECT(exe, a.binaries, a.datas, strip=False, upx=False, name="pyfa-mcp")
