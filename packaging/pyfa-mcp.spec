# PyInstaller spec for pyfa-mcp.exe, the stdio MCP server.
#
#     .venv/Scripts/python -m PyInstaller --noconfirm packaging/pyfa-mcp.spec
#
# From the repo root, with vendor/Pyfa checked out and its eve.db generated
# (scripts/make_evedb.py). Output: dist/pyfa-mcp/, one folder: a one-file
# build would unpack 190 MB on every client start.
import ast
import importlib.util
from pathlib import Path

from PyInstaller.utils.hooks import collect_submodules, copy_metadata

ROOT = Path(SPECPATH).parent
PYFA = ROOT / "vendor" / "Pyfa"
PYFA_PACKAGES = ("eos", "service", "graphs", "gui", "utils")

for needed in (PYFA / "eos" / "__init__.py", PYFA / "eve.db"):
    if not needed.is_file():
        raise SystemExit(f"missing {needed}: run 'git submodule update --init' "
                         "and scripts/make_evedb.py")

# Pyfa travels as source under pyfa/, where eosboot.pyfa_dir() looks when
# frozen: eos imports its effects and migrations by name, which PyInstaller
# cannot follow. The price is that the analysis sees none of Pyfa's imports,
# so they are read from Pyfa's own source below -- which keeps this file in
# step with every Pyfa bump.
datas = [(str(PYFA / package), f"pyfa/{package}") for package in PYFA_PACKAGES]
datas += [(str(PYFA / name), "pyfa") for name in ("config.py", "version.yml", "eve.db")]
datas += [(str(ROOT / "pyfa_mcp" / "unhandled_effects.json"), "pyfa_mcp")]
datas += copy_metadata("pyfa-mcp")  # drift._own_version()


def _pyfa_imports() -> set[str]:
    found = set()
    sources = [PYFA / "config.py",
               *(path for package in PYFA_PACKAGES for path in (PYFA / package).rglob("*.py"))]
    for source in sources:
        for node in ast.walk(ast.parse(source.read_text(encoding="utf-8"))):
            if isinstance(node, ast.Import):
                found |= {alias.name for alias in node.names}
            elif isinstance(node, ast.ImportFrom) and node.level == 0 and node.module:
                # `from xml.etree import ElementTree` imports a submodule.
                found |= {node.module} | {f"{node.module}.{alias.name}" for alias in node.names}
    return found


def _importable(name: str) -> bool:
    if name.split(".")[0] in {*PYFA_PACKAGES, "config", "wx"}:
        return False  # carried as data, or stubbed
    try:
        return importlib.util.find_spec(name) is not None
    except (ImportError, ValueError, AttributeError):
        return False  # `from x import function`, and imports Pyfa guards for other platforms


hiddenimports = sorted(name for name in _pyfa_imports() if _importable(name))
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
