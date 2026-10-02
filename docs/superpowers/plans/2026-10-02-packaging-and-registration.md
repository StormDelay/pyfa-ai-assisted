# pyfa-mcp Plan 3 — Packaging and multi-client registration

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** A Windows installer for a one-folder `pyfa-mcp.exe` that needs no Python or Pyfa install, and that registers itself with the user's MCP clients (and removes itself on uninstall), table-driven, without ever damaging a client's config.

**Architecture:** `pyfa_mcp/register.py` holds a table of clients: config file, "installed" marker dir, servers key, entry shape. JSON configs are read with `utf-8-sig`, a file that does not parse is refused (never rewritten), every write backs the file up to `<file>.pyfa-mcp.bak` and replaces it atomically, and everything but our `pyfa` entry is kept. Codex's TOML is edited as text (our table cut out, ours appended) and re-parsed with `tomllib` before writing. Claude Code goes through its own `claude mcp add-json/remove --scope user` when `claude.exe` is on PATH, because running Claude Code sessions rewrite `~/.claude.json`. `server.main` gains `--register / --unregister / --print-config`. A PyInstaller spec carries Pyfa's source as data (as dread-sim does) and derives the hidden imports from Pyfa's own source, so Pyfa bumps need no hand-kept list. An Inno Setup script installs per user, with one checkbox per detected client.

**Tech Stack:** Python 3.14, PyInstaller 6.22.3 (dev group), Inno Setup 6 (`C:\Program Files (x86)\Inno Setup 6\ISCC.exe`), stdlib `tomllib`.

**Spec:** `docs/superpowers/specs/2026-10-02-pyfa-mcp-design.md` (section "Packaging and registration"). Plans 1–2 done.

This is plan 3 of 4. Plan 4 (release pipeline) runs this plan's build, smoke and installer checks in GitHub Actions; this plan builds and checks locally.

Verified on 2026-10-02:
- **Spike build.** PyInstaller 6.22.3 resolves for Python 3.14, and a spike of this spec builds `dist/pyfa-mcp` (193 MB, ~3 min). The extended smoke passed against the frozen exe: `evaluate_fit`, `status`, `fit_graph`, `conditions_format`, `list_fits(source="pyfa")` and `evaluate_fit("pyfa:3")` on a copy of a real Pyfa DB.
  - The spike's first two builds failed on `xml.etree`. Stdlib modules used only by the bundled Pyfa aren't traced, and `from xml.etree import ElementTree` names a submodule. Hence the source-derived `hiddenimports`, including `module.name` candidates.
  - The spike bundle had no `pyfa-mcp` dist-info, so `copy_metadata("pyfa-mcp")` is needed for `drift._own_version()`.
- **Client config docs**, checked against each client's own docs:
  - **Claude Code:** `~/.claude.json`, `mcpServers`, stdio entries `{"type":"stdio","command","args","env"}`; also `claude mcp add-json <name> '<json>' --scope user`.
  - **Claude Desktop:** `%APPDATA%\Claude\claude_desktop_config.json`, `mcpServers` `{command,args,env}`.
  - **Cursor:** `~/.cursor/mcp.json`, `mcpServers`, `type: "stdio"`.
  - **VS Code:** user-profile `mcp.json`, top-level `servers`, `type/command/args/env`. The docs give no Windows path; the user-profile folder is `%APPDATA%\Code\User`, which exists on this machine.
  - **Codex:** `~/.codex/config.toml`, `[mcp_servers.<name>]` with `command`, `args`, `env`.
  - **Windsurf:** its docs URL now redirects to "Devin Desktop" (legacy Cascade agent), config `%APPDATA%\devin\mcp_config.json`, `mcpServers` `{command,args,env}`. The classic Windsurf path `~/.codeium/windsurf/mcp_config.json` is kept as `windsurf`; `devin` is added.
- **This machine:**
  - Claude Desktop is the Microsoft Store (MSIX) build. Its config is at `%LOCALAPPDATA%\Packages\Claude_pzs8sxrjxfjjc\LocalCache\Roaming\Claude\claude_desktop_config.json`, holding other servers and app `preferences`, so every other key must survive. dread-sim's `aiaccess.config_in` solved the MSIX lookup.
  - `claude` (native) and VS Code are on PATH.
  - No client has a `pyfa` entry today.
  - The repo has no `LICENSE`; Pyfa's `vendor/Pyfa/LICENSE` is the GPLv3 text.

## Global Constraints

- Everything in Plans 1–2's Global Constraints still holds.
- Packaging (spec): PyInstaller one-folder build `pyfa-mcp/` with `pyfa-mcp.exe` (stdio server), bundled `eve.db` and eos source; Inno Setup installer.
- `pyfa-mcp.exe --register <client>|auto`, `--unregister <client>` (plus `all`), `--print-config [json|toml]`.
- Registration (spec): table-driven (config path, key, format); each write is atomic, keeps other entries, and backs up the file first. `auto` registers every client whose config directory exists; the installer exposes it as a checkbox listing detected clients; `--print-config` covers clients not in the table.
- Server key everywhere: `pyfa`.
- A client config that does not parse (JSONC comments, trailing commas, bad TOML) is never rewritten: refuse, and give the snippet to add by hand.
- Tests never read or write the real user's client configs, and never run the real `claude` CLI.
- Installer: per-user (`PrivilegesRequired=lowest`), x64, version passed in (`/DAppVersion=`), no default.

## Review Focus

1. **A config the user hand-edited:** VS Code `mcp.json` with `//` comments, a Notepad BOM, trailing commas. Expected: comments and commas are refused with the snippet and the file is unchanged; the BOM is just read. (Task 1)
2. **Registering into a config full of other things** (Claude Desktop's other servers and `preferences`). Expected: every other key is byte-for-byte the same JSON value afterwards. (Task 1)
3. **Re-running `--register` after reinstalling elsewhere.** Expected: our entry points at the new exe; no duplicate. (Task 1, Task 2 for Codex)
4. **Uninstall when the user never registered, or removed the entry by hand.** Expected: no error, no write, no backup file. (Task 1, Task 2)
5. **Codex `config.toml` with comments, other servers and later tables.** Expected: every line outside our table survives in order; a `pyfa` defined inline elsewhere is refused, not duplicated. (Task 2)

---

## File Structure

```
pyfa_mcp/register.py          NEW  client table, entry, JSON/TOML/CLI register + unregister, run()
pyfa_mcp/server.py            --register / --unregister / --print-config dispatch
tests/test_register.py        NEW
packaging/pyfa-mcp.spec       NEW  PyInstaller one-folder build
packaging/pyfa-mcp.iss        NEW  Inno Setup per-user installer, per-client tasks
packaging/mcp_smoke.py        also status, fit_graph, conditions_format
tests/test_server.py          smoke runs offline, against an empty Pyfa dir
pyproject.toml                pyinstaller in the dev group
LICENSE                       NEW  GPLv3 text (installer licence page)
```

---

### Task 1: The client table and JSON registration

**Files:**
- Create: `pyfa_mcp/register.py`, `tests/test_register.py`

**Interfaces:**
- Produces:
  - `register.NAME = "pyfa"`, `register.RegisterError(RuntimeError)`
  - `register.command() -> tuple[str, list[str]]`: frozen `(sys.executable, [])`, else `(sys.executable, ["-m", "pyfa_mcp"])`
  - `register.Client` (frozen dataclass: `label: str, config: Path, marker: Path, key: str, typed: bool`; property `toml: bool`; method `entry() -> dict`)
  - `register.clients() -> dict[str, Client]`, computed from `USERPROFILE`/`Path.home()`, `APPDATA`, `LOCALAPPDATA` at call time; names `claude-desktop, claude-code, cursor, windsurf, devin, vscode, codex`
  - `register.registered(client) -> dict | None` (None when unreadable too)
  - `register.add(client) -> str`, `register.remove(client) -> str` (human message; raise `RegisterError`)
  - `register._claude_cli() -> str | None` (tests monkeypatch it; Task 2 uses it)
  - `register.snippet(fmt: str = "json") -> str` (`toml` added in Task 2)

- [ ] **Step 1: Write the failing tests `tests/test_register.py`**

```python
import json
from pathlib import Path
from types import SimpleNamespace

import pytest

from pyfa_mcp import register


@pytest.fixture(autouse=True)
def roots(tmp_path, monkeypatch):
    """A fake user profile: no test may touch the real client configs or CLI."""
    home = tmp_path / "home"
    appdata, local = home / "AppData" / "Roaming", home / "AppData" / "Local"
    appdata.mkdir(parents=True)
    local.mkdir(parents=True)
    monkeypatch.setenv("USERPROFILE", str(home))
    monkeypatch.setenv("HOME", str(home))
    monkeypatch.setenv("APPDATA", str(appdata))
    monkeypatch.setenv("LOCALAPPDATA", str(local))
    monkeypatch.setattr(register, "_claude_cli", lambda: None)
    return SimpleNamespace(home=home, appdata=appdata, local=local)


def _install(client: str) -> register.Client:
    c = register.clients()[client]
    c.marker.mkdir(parents=True, exist_ok=True)
    return c


def test_every_client_lives_under_the_fake_profile(roots):
    for c in register.clients().values():
        assert roots.home in c.config.parents


def test_entry_shapes(roots):
    cmd, args = register.command()
    assert register.clients()["claude-desktop"].entry() == {"command": cmd, "args": args}
    assert register.clients()["vscode"].entry() == {"type": "stdio", "command": cmd, "args": args}
    assert register.clients()["vscode"].key == "servers"


def test_register_keeps_every_other_key(roots):
    c = _install("claude-desktop")
    original = {"mcpServers": {"dreadsim": {"command": "C:\\d\\dreadsim-mcp.exe", "args": []}},
                "preferences": {"coworkPreferredBrowser": "built_in", "nested": [1, {"a": None}]}}
    c.config.write_text(json.dumps(original), encoding="utf-8")
    assert "registered" in register.add(c)
    after = json.loads(c.config.read_text(encoding="utf-8"))
    assert after["preferences"] == original["preferences"]
    assert after["mcpServers"]["dreadsim"] == original["mcpServers"]["dreadsim"]
    assert after["mcpServers"]["pyfa"] == c.entry()
    backup = c.config.with_name(c.config.name + ".pyfa-mcp.bak")
    assert json.loads(backup.read_text(encoding="utf-8")) == original


def test_register_creates_a_missing_file(roots):
    c = _install("cursor")
    register.add(c)
    assert json.loads(c.config.read_text(encoding="utf-8")) == {"mcpServers": {"pyfa": c.entry()}}
    assert not c.config.with_name(c.config.name + ".pyfa-mcp.bak").exists()


def test_register_twice_writes_once(roots):
    c = _install("cursor")
    register.add(c)
    before = c.config.read_bytes()
    assert "already" in register.add(c)
    assert c.config.read_bytes() == before


def test_reregister_after_moving_points_at_the_new_exe(roots, monkeypatch):
    c = _install("cursor")
    register.add(c)
    monkeypatch.setattr(register, "command", lambda: ("D:\\new\\pyfa-mcp.exe", []))
    register.add(c)
    servers = json.loads(c.config.read_text(encoding="utf-8"))["mcpServers"]
    assert list(servers) == ["pyfa"]
    assert servers["pyfa"]["command"] == "D:\\new\\pyfa-mcp.exe"


def test_notepad_bom_is_read(roots):
    c = _install("windsurf")
    c.config.write_bytes(b"\xef\xbb\xbf" + json.dumps({"mcpServers": {}}).encode())
    register.add(c)
    assert register.registered(c) == c.entry()


@pytest.mark.parametrize("text", [
    '{\n  // my servers\n  "servers": {}\n}\n',
    '{"servers": {"x": {"command": "y"},}}',
    '["not", "an", "object"]',
    '{"servers": []}',
])
def test_a_config_that_does_not_parse_is_left_alone(roots, text):
    c = _install("vscode")
    c.config.write_text(text, encoding="utf-8")
    with pytest.raises(register.RegisterError, match='"pyfa"'):  # the snippet to add by hand
        register.add(c)
    assert c.config.read_text(encoding="utf-8") == text
    assert not c.config.with_name(c.config.name + ".pyfa-mcp.bak").exists()


def test_a_client_that_is_not_installed_is_refused(roots):
    c = register.clients()["cursor"]
    with pytest.raises(register.RegisterError, match="not installed"):
        register.add(c)
    assert not c.config.exists()


def test_unregister_removes_only_ours(roots):
    c = _install("claude-desktop")
    c.config.write_text(json.dumps({"mcpServers": {"other": {"command": "x"}}}), encoding="utf-8")
    register.add(c)
    assert "removed" in register.remove(c)
    assert json.loads(c.config.read_text(encoding="utf-8")) == {"mcpServers": {"other": {"command": "x"}}}


def test_unregister_when_absent_writes_nothing(roots):
    c = _install("claude-desktop")
    assert "not registered" in register.remove(c)
    assert not c.config.exists()
    c.config.write_text('{"mcpServers": {}}', encoding="utf-8")
    assert "not registered" in register.remove(c)
    assert c.config.read_text(encoding="utf-8") == '{"mcpServers": {}}'
    assert not c.config.with_name(c.config.name + ".pyfa-mcp.bak").exists()


def test_microsoft_store_claude_desktop(roots):
    redirected = roots.local / "Packages" / "Claude_pzs8sxrjxfjjc" / "LocalCache" / "Roaming" / "Claude"
    redirected.mkdir(parents=True)
    assert register.clients()["claude-desktop"].config == redirected / "claude_desktop_config.json"
    (roots.appdata / "Claude").mkdir()  # a classic install wins
    assert register.clients()["claude-desktop"].config == \
        roots.appdata / "Claude" / "claude_desktop_config.json"


def test_snippet_json(roots):
    cmd, args = register.command()
    assert json.loads(register.snippet("json")) == \
        {"mcpServers": {"pyfa": {"command": cmd, "args": args}}}
```

- [ ] **Step 2: Run to see them fail**

Run: `.venv/Scripts/python -m pytest tests/test_register.py -q`
Expected: collection error, `ImportError: cannot import name 'register'`.

- [ ] **Step 3: Write `pyfa_mcp/register.py`**

```python
"""Register pyfa-mcp with MCP clients by editing their config files.

One row per client: where its config lives, the directory that says it is
installed, the key its servers sit under, and whether entries carry
"type": "stdio". Every write keeps everything else in the file, backs the
file up first (<file>.pyfa-mcp.bak) and replaces it atomically. A file this
cannot parse is the user's: it is refused, never rewritten, and the error
carries the snippet to add by hand.
"""
from __future__ import annotations

import json
import os
import shutil
import sys
from dataclasses import dataclass
from pathlib import Path

NAME = "pyfa"


class RegisterError(RuntimeError):
    """A client config this must not, or cannot, write."""


def command() -> tuple[str, list[str]]:
    """What a client runs: the packaged exe itself, or this Python on the checkout."""
    if getattr(sys, "frozen", False):
        return sys.executable, []
    return sys.executable, ["-m", "pyfa_mcp"]


@dataclass(frozen=True)
class Client:
    label: str
    config: Path
    marker: Path  # exists when the client is installed
    key: str      # where servers sit: mcpServers, servers, or mcp_servers (TOML)
    typed: bool   # entries carry "type": "stdio"

    @property
    def toml(self) -> bool:
        return self.config.suffix == ".toml"

    def entry(self) -> dict:
        cmd, args = command()
        entry = {"command": cmd, "args": args}
        return {"type": "stdio", **entry} if self.typed else entry


def _claude_desktop_dir(appdata: Path, local: Path) -> Path:
    """The classic install's dir, else the Microsoft Store (MSIX) build's.

    Windows redirects a packaged app's %APPDATA% to
    %LOCALAPPDATA%\\Packages\\<family>\\LocalCache\\Roaming; the family name
    ends in a publisher hash, so it is matched by prefix.
    """
    classic = appdata / "Claude"
    if classic.is_dir():
        return classic
    for package in sorted(local.glob("Packages/Claude_*")):
        redirected = package / "LocalCache" / "Roaming" / "Claude"
        if redirected.is_dir():
            return redirected
    return classic


def clients() -> dict[str, Client]:
    # ponytail: Windows paths only; the installer is Windows-only too.
    home = Path.home()
    appdata = Path(os.environ.get("APPDATA") or home / "AppData" / "Roaming")
    local = Path(os.environ.get("LOCALAPPDATA") or home / "AppData" / "Local")
    desktop = _claude_desktop_dir(appdata, local)
    windsurf = home / ".codeium" / "windsurf"
    return {
        "claude-desktop": Client("Claude Desktop", desktop / "claude_desktop_config.json",
                                 desktop, "mcpServers", False),
        "claude-code": Client("Claude Code", home / ".claude.json", home / ".claude",
                              "mcpServers", True),
        "cursor": Client("Cursor", home / ".cursor" / "mcp.json", home / ".cursor",
                         "mcpServers", True),
        "windsurf": Client("Windsurf", windsurf / "mcp_config.json", windsurf,
                           "mcpServers", False),
        "devin": Client("Devin Desktop", appdata / "devin" / "mcp_config.json",
                        appdata / "devin", "mcpServers", False),
        "vscode": Client("VS Code", appdata / "Code" / "User" / "mcp.json",
                         appdata / "Code" / "User", "servers", True),
        "codex": Client("Codex", home / ".codex" / "config.toml", home / ".codex",
                        "mcp_servers", False),
    }


def snippet(fmt: str = "json") -> str:
    cmd, args = command()
    return json.dumps({"mcpServers": {NAME: {"command": cmd, "args": args}}}, indent=2)


def _claude_cli() -> str | None:
    """Claude Code's own CLI, when it is a real exe (a .cmd shim mangles JSON args)."""
    found = shutil.which("claude")
    return found if found and found.lower().endswith(".exe") else None


def _by_hand(client: Client, why: str) -> RegisterError:
    return RegisterError(f"{client.config}: {why}; not touching it. "
                         f"Add this under \"{client.key}\" by hand:\n"
                         + json.dumps({NAME: client.entry()}, indent=2))


def _replace(path: Path, text: str) -> None:
    """Back up, then swap the whole file: a crash mid-write must not cost other servers."""
    if path.exists():
        shutil.copy2(path, path.with_name(path.name + ".pyfa-mcp.bak"))
    temp = path.with_name(path.name + ".pyfa-mcp.tmp")
    temp.write_text(text, encoding="utf-8")
    os.replace(temp, path)


def _read_json(client: Client) -> tuple[dict, dict]:
    """(whole file, its servers object); ({}, {}) when there is no file yet."""
    try:
        text = client.config.read_text(encoding="utf-8-sig")  # Notepad writes a BOM
    except FileNotFoundError:
        return {}, {}
    if not text.strip():
        return {}, {}
    try:
        root = json.loads(text)
    except json.JSONDecodeError as exc:
        raise _by_hand(client, f"not plain JSON ({exc.msg}, line {exc.lineno})") from exc
    if not isinstance(root, dict):
        raise _by_hand(client, "not a JSON object")
    servers = root.setdefault(client.key, {})
    if not isinstance(servers, dict):
        raise _by_hand(client, f'"{client.key}" is not an object')
    return root, servers


def registered(client: Client) -> dict | None:
    try:
        found = _read_json(client)[1].get(NAME)
    except (RegisterError, OSError):
        return None
    return found if isinstance(found, dict) else None


def _check_installed(client: Client) -> None:
    if not client.marker.is_dir():
        raise RegisterError(f"{client.label} is not installed (no {client.marker})")


def add(client: Client) -> str:
    _check_installed(client)
    root, servers = _read_json(client)
    if servers.get(NAME) == client.entry():
        return f"already registered in {client.config}"
    servers[NAME] = client.entry()
    _replace(client.config, json.dumps(root, indent=2) + "\n")
    return f"registered in {client.config}; restart {client.label} to use it"


def remove(client: Client) -> str:
    if not client.config.exists():
        return "not registered"
    root, servers = _read_json(client)
    if NAME not in servers:
        return "not registered"
    del servers[NAME]
    _replace(client.config, json.dumps(root, indent=2) + "\n")
    return f"removed from {client.config}"
```

- [ ] **Step 4: Run the tests**

Run: `.venv/Scripts/python -m pytest tests/test_register.py -q`
Expected: all pass.

- [ ] **Step 5: Commit**

```bash
git add pyfa_mcp/register.py tests/test_register.py
git commit -m "Register pyfa-mcp in JSON client configs, keeping every other key and refusing files that do not parse"
```

---

### Task 2: Codex's TOML, Claude Code's CLI, and the command line

**Files:**
- Modify: `pyfa_mcp/register.py`, `pyfa_mcp/server.py`, `tests/test_register.py`, `tests/test_server.py`

**Interfaces:**
- Consumes: Task 1's `Client`, `clients()`, `add`, `remove`, `registered`, `_replace`, `_by_hand`, `_check_installed`, `_claude_cli`.
- Produces:
  - `add`/`remove`/`registered` handle `client.toml` (Codex) and Claude Code via CLI
  - `register.snippet("toml") -> str`
  - `register.run(register_name: str | None, unregister_name: str | None, print_config: str | None) -> int` (exit code; `auto` / `all`)
  - `server.main(argv)` options `--register CLIENT|auto`, `--unregister CLIENT|all`, `--print-config [json|toml]`; with any of them it exits with `run()`'s code instead of serving

- [ ] **Step 1: Write the failing tests**

Append to `tests/test_register.py`:
```python
import subprocess
import tomllib

CODEX = """# my codex config
model = "o4"

[mcp_servers.other]
command = "other.exe"
args = ["--x"]

[mcp_servers.other.env]
KEY = "v"

[profiles.fast]
model = "o4-mini"
"""


def test_codex_keeps_every_other_line(roots):
    c = _install("codex")
    c.config.write_text(CODEX, encoding="utf-8")
    register.add(c)
    text = c.config.read_text(encoding="utf-8")
    assert text.startswith(CODEX)  # everything before stays, in order
    data = tomllib.loads(text)
    assert data["mcp_servers"]["pyfa"] == c.entry()
    assert data["mcp_servers"]["other"]["env"] == {"KEY": "v"}
    assert data["profiles"]["fast"]["model"] == "o4-mini"


def test_codex_reregister_replaces_and_unregister_restores(roots, monkeypatch):
    c = _install("codex")
    c.config.write_text(CODEX, encoding="utf-8")
    register.add(c)
    monkeypatch.setattr(register, "command", lambda: ("D:\\new\\pyfa-mcp.exe", []))
    register.add(c)
    text = c.config.read_text(encoding="utf-8")
    assert text.count("[mcp_servers.pyfa]") == 1
    assert tomllib.loads(text)["mcp_servers"]["pyfa"]["command"] == "D:\\new\\pyfa-mcp.exe"
    assert "removed" in register.remove(c)
    assert tomllib.loads(c.config.read_text(encoding="utf-8")) == tomllib.loads(CODEX)


def test_codex_inline_pyfa_is_refused(roots):
    c = _install("codex")
    text = '[mcp_servers]\npyfa = { command = "old.exe" }\n'
    c.config.write_text(text, encoding="utf-8")
    with pytest.raises(register.RegisterError):
        register.add(c)
    assert c.config.read_text(encoding="utf-8") == text


def test_codex_bad_toml_is_left_alone(roots):
    c = _install("codex")
    c.config.write_text("model = \n", encoding="utf-8")
    with pytest.raises(register.RegisterError, match="pyfa"):
        register.add(c)
    assert c.config.read_text(encoding="utf-8") == "model = \n"


def test_codex_unregister_when_absent_writes_nothing(roots):
    c = _install("codex")
    c.config.write_text(CODEX, encoding="utf-8")
    assert "not registered" in register.remove(c)
    assert c.config.read_text(encoding="utf-8") == CODEX


def test_snippet_toml(roots):
    cmd, args = register.command()
    assert tomllib.loads(register.snippet("toml")) == \
        {"mcp_servers": {"pyfa": {"command": cmd, "args": args}}}


@pytest.fixture
def claude_cli(monkeypatch):
    calls = []

    def fake_run(argv, **kwargs):
        calls.append(argv[1:])
        return subprocess.CompletedProcess(argv, 0, "", "")

    monkeypatch.setattr(register, "_claude_cli", lambda: "C:\\bin\\claude.exe")
    monkeypatch.setattr(register.subprocess, "run", fake_run)
    return calls


def test_claude_code_goes_through_its_cli(roots, claude_cli):
    c = _install("claude-code")
    c.config.write_text('{"numStartups": 3}', encoding="utf-8")
    register.add(c)
    assert claude_cli == [["mcp", "add-json", "pyfa", json.dumps(c.entry()), "--scope", "user"]]
    assert c.config.read_text(encoding="utf-8") == '{"numStartups": 3}'  # the CLI writes, not us


def test_claude_code_cli_replaces_a_stale_entry(roots, claude_cli):
    c = _install("claude-code")
    c.config.write_text(json.dumps({"mcpServers": {"pyfa": {"command": "old.exe"}}}), encoding="utf-8")
    register.add(c)
    assert claude_cli[0] == ["mcp", "remove", "pyfa", "--scope", "user"]
    assert claude_cli[1][:3] == ["mcp", "add-json", "pyfa"]


def test_claude_code_cli_unregister_only_when_present(roots, claude_cli):
    c = _install("claude-code")
    assert "not registered" in register.remove(c)
    assert claude_cli == []


def test_claude_code_cli_failure_is_reported(roots, monkeypatch):
    monkeypatch.setattr(register, "_claude_cli", lambda: "C:\\bin\\claude.exe")
    monkeypatch.setattr(register.subprocess, "run", lambda argv, **kw:
                        subprocess.CompletedProcess(argv, 1, "", "boom"))
    with pytest.raises(register.RegisterError, match="boom"):
        register.add(_install("claude-code"))


def test_run_auto_registers_detected_clients_only(roots, capsys):
    cursor, vscode = _install("cursor"), _install("vscode")
    assert register.run("auto", None, None) == 0
    assert register.registered(cursor) and register.registered(vscode)
    assert not register.clients()["codex"].config.exists()
    out = capsys.readouterr().out
    assert "Cursor" in out and "VS Code" in out


def test_run_auto_with_nothing_installed(roots, capsys):
    assert register.run("auto", None, None) == 0
    assert "--print-config" in capsys.readouterr().out


def test_run_unregister_all(roots):
    cursor, vscode = _install("cursor"), _install("vscode")
    register.run("auto", None, None)
    assert register.run(None, "all", None) == 0
    assert not register.registered(cursor) and not register.registered(vscode)


def test_run_one_failure_still_does_the_rest(roots, capsys):
    _install("vscode").config.write_text("{ // comment\n}", encoding="utf-8")
    cursor = _install("cursor")
    assert register.run("auto", None, None) == 1
    assert register.registered(cursor)
    assert "VS Code" in capsys.readouterr().err


def test_run_unknown_client(roots, capsys):
    assert register.run("emacs", None, None) == 1
    assert "claude-desktop" in capsys.readouterr().err


def test_run_print_config(roots, capsys):
    assert register.run(None, None, "toml") == 0
    assert "[mcp_servers.pyfa]" in capsys.readouterr().out
```

Append to `tests/test_server.py`:
```python
def test_main_print_config_exits_without_serving(capsys):
    import json
    with pytest.raises(SystemExit) as exit_info:
        server.main(["--print-config"])
    assert exit_info.value.code == 0
    assert "pyfa" in json.loads(capsys.readouterr().out)["mcpServers"]
```

- [ ] **Step 2: Run to see them fail**

Run: `.venv/Scripts/python -m pytest tests/test_register.py tests/test_server.py -q`
Expected: the Codex, CLI, `run` and `main` tests FAIL (for example, `AttributeError: module 'pyfa_mcp.register' has no attribute 'run'`, `json.JSONDecodeError` on TOML). Task 1's tests still pass.

- [ ] **Step 3: Add TOML, the CLI path and `run` to `pyfa_mcp/register.py`**

Add to the imports:
```python
import re
import subprocess
import tomllib
```

Replace `snippet`:
```python
def _toml_table(entry: dict) -> str:
    # JSON strings and arrays are valid TOML basic strings and arrays.
    return (f"[mcp_servers.{NAME}]\ncommand = {json.dumps(entry['command'])}\n"
            f"args = {json.dumps(entry['args'])}\n")


def snippet(fmt: str = "json") -> str:
    cmd, args = command()
    if fmt == "toml":
        return _toml_table({"command": cmd, "args": args})
    return json.dumps({"mcpServers": {NAME: {"command": cmd, "args": args}}}, indent=2)
```

Change `_by_hand` so a TOML client gets the TOML snippet:
```python
def _by_hand(client: Client, why: str) -> RegisterError:
    by_hand = (_toml_table(client.entry()) if client.toml
               else json.dumps({NAME: client.entry()}, indent=2))
    return RegisterError(f"{client.config}: {why}; not touching it. "
                         f"Add this under \"{client.key}\" by hand:\n{by_hand}")
```

Add the TOML helpers after `_read_json`:
```python
_HEADER = re.compile(r"^\s*\[")
_OUR_TABLE = re.compile(r'^\s*\[\s*mcp_servers\s*\.\s*"?' + NAME + r'"?\s*(\.[^\]]*)?\]')


def _read_toml(client: Client) -> tuple[str, dict]:
    try:
        text = client.config.read_text(encoding="utf-8-sig")
    except FileNotFoundError:
        return "", {}
    try:
        return text, tomllib.loads(text)
    except tomllib.TOMLDecodeError as exc:
        raise _by_hand(client, f"not valid TOML ({exc})") from exc


def _without_ours(text: str) -> str:
    """The TOML text minus our [mcp_servers.pyfa] table and its sub-tables."""
    kept, skipping = [], False
    for line in text.splitlines(keepends=True):
        if _HEADER.match(line):
            skipping = bool(_OUR_TABLE.match(line))
        if not skipping:
            kept.append(line)
    return "".join(kept)


def _write_toml(client: Client, text: str, want: dict | None) -> None:
    """Write `text` only if it parses and holds exactly `want` as our entry."""
    try:
        ours = tomllib.loads(text).get("mcp_servers", {}).get(NAME)
    except tomllib.TOMLDecodeError:
        ours = "unparseable"
    if ours != want:  # e.g. `pyfa = {...}` inline under [mcp_servers]
        raise _by_hand(client, f"{NAME} is defined in a form this cannot edit")
    _replace(client.config, text)
```

Add the CLI runner:
```python
def _claude(cli: str, *args: str) -> None:
    done = subprocess.run([cli, *args], capture_output=True, text=True,
                          stdin=subprocess.DEVNULL,
                          creationflags=getattr(subprocess, "CREATE_NO_WINDOW", 0))
    if done.returncode != 0:
        raise RegisterError(f"claude {' '.join(args[:2])} failed: "
                            f"{(done.stderr or done.stdout).strip()}")
```

Replace `registered`, `add` and `remove`:
```python
def registered(client: Client) -> dict | None:
    try:
        if client.toml:
            found = _read_toml(client)[1].get("mcp_servers", {}).get(NAME)
        else:
            found = _read_json(client)[1].get(NAME)
    except (RegisterError, OSError):
        return None
    return found if isinstance(found, dict) else None


def add(client: Client) -> str:
    _check_installed(client)
    entry = client.entry()
    if client.toml:
        text, data = _read_toml(client)
        if data.get("mcp_servers", {}).get(NAME) == entry:
            return f"already registered in {client.config}"
        kept = _without_ours(text)
        if kept and not kept.endswith("\n"):
            kept += "\n"
        _write_toml(client, kept + ("\n" if kept.strip() else "") + _toml_table(entry), entry)
        return f"registered in {client.config}; restart {client.label} to use it"
    root, servers = _read_json(client)
    if servers.get(NAME) == entry:
        return f"already registered in {client.config}"
    cli = _claude_cli() if client.label == "Claude Code" else None
    if cli:
        # Running Claude Code sessions rewrite ~/.claude.json; let Claude Code
        # make the change itself. Backed up all the same.
        if client.config.exists():
            shutil.copy2(client.config, client.config.with_name(client.config.name + ".pyfa-mcp.bak"))
        if NAME in servers:
            _claude(cli, "mcp", "remove", NAME, "--scope", "user")
        _claude(cli, "mcp", "add-json", NAME, json.dumps(entry), "--scope", "user")
        return "registered through the claude CLI; restart Claude Code to use it"
    servers[NAME] = entry
    _replace(client.config, json.dumps(root, indent=2) + "\n")
    return f"registered in {client.config}; restart {client.label} to use it"


def remove(client: Client) -> str:
    if not client.config.exists():
        return "not registered"
    if client.toml:
        text, data = _read_toml(client)
        if NAME not in data.get("mcp_servers", {}):
            return "not registered"
        _write_toml(client, _without_ours(text), None)
        return f"removed from {client.config}"
    root, servers = _read_json(client)
    if NAME not in servers:
        return "not registered"
    cli = _claude_cli() if client.label == "Claude Code" else None
    if cli:
        shutil.copy2(client.config, client.config.with_name(client.config.name + ".pyfa-mcp.bak"))
        _claude(cli, "mcp", "remove", NAME, "--scope", "user")
        return "removed through the claude CLI"
    del servers[NAME]
    _replace(client.config, json.dumps(root, indent=2) + "\n")
    return f"removed from {client.config}"
```

Append `run`:
```python
def run(register_name: str | None, unregister_name: str | None,
        print_config: str | None) -> int:
    """The --register / --unregister / --print-config command line; returns the exit code."""
    if print_config:
        print(snippet(print_config))
        return 0
    table = clients()
    wanted = register_name or unregister_name
    if register_name == "auto":
        names = [n for n, c in table.items() if c.marker.is_dir()]
        if not names:
            print("no MCP client found; paste the output of --print-config into yours")
            return 0
    elif unregister_name == "all":
        names = [n for n, c in table.items() if registered(c)]
    elif wanted in table:
        names = [wanted]
    else:
        print(f"unknown client '{wanted}'; known: {', '.join(table)}, "
              f"{'auto' if register_name else 'all'}", file=sys.stderr)
        return 1
    action = add if register_name else remove
    failed = False
    for name in names:
        try:
            print(f"{table[name].label}: {action(table[name])}")
        except (RegisterError, OSError) as exc:
            failed = True
            print(f"{table[name].label}: {exc}", file=sys.stderr)
    return 1 if failed else 0
```

- [ ] **Step 4: Wire `server.main`**

In `pyfa_mcp/server.py`, change the import line to add `register`:
```python
from pyfa_mcp import (catalog, conditions, drift, eft, eosboot, evaluate, graphs, pyfadata,
                      register, store)
```

In `main`, after the `--pyfa-dir` argument and before `args = parser.parse_args(argv)`, add:
```python
    setup = parser.add_mutually_exclusive_group()
    setup.add_argument("--register", metavar="CLIENT",
                       help="add pyfa-mcp to an MCP client's config: "
                            + ", ".join(register.clients()) + ", or auto (every one installed)")
    setup.add_argument("--unregister", metavar="CLIENT",
                       help="remove pyfa-mcp from a client's config, or all")
    setup.add_argument("--print-config", nargs="?", const="json", choices=("json", "toml"),
                       help="print the config entry for a client not in the list")
```
and directly after `args = parser.parse_args(argv)`, before `_data_dir = ...` and `pyfadata.set_dir(...)` (which would re-point a test session's pyfadata at the real `~/.pyfa`):
```python
    if args.register or args.unregister or args.print_config:
        sys.exit(register.run(args.register, args.unregister, args.print_config))
```

- [ ] **Step 5: Run the tests**

Run: `.venv/Scripts/python -m pytest -q`
Expected: all pass.

- [ ] **Step 6: Commit**

```bash
git add pyfa_mcp/register.py pyfa_mcp/server.py tests/test_register.py tests/test_server.py
git commit -m "Register with Codex's TOML and through Claude Code's CLI, from --register/--unregister/--print-config"
```

---

### Task 3: The PyInstaller build

**Files:**
- Create: `packaging/pyfa-mcp.spec`
- Modify: `packaging/mcp_smoke.py`, `tests/test_server.py`, `pyproject.toml`

**Interfaces:**
- Consumes: `eosboot.pyfa_dir()` (frozen → `sys._MEIPASS/pyfa`), `drift.BASELINE` (beside `drift.py`), `drift._own_version()` (dist metadata).
- Produces: `dist/pyfa-mcp/pyfa-mcp.exe` (+ `_internal/`); `packaging/mcp_smoke.py` checks `evaluate_fit`, `status`, `fit_graph`, `conditions_format`.

- [ ] **Step 1: Make the smoke test cover more, offline**

In `packaging/mcp_smoke.py`, replace the docstring's second paragraph with:
```
initialize, tools/list, then evaluate_fit, status, fit_graph and
conditions_format. Exits non-zero on anything unexpected; a stray print on
the server's stdout fails json.loads. Pass --data-dir / --pyfa-dir through to
keep it off the user's own data.
```
and replace the single `evaluate_fit` call block:
```python
        result = call("tools/call", {"name": "evaluate_fit", "arguments": {"fit": FIT}})
        if result.get("isError"):
            raise SystemExit(f"evaluate_fit: {result['content']}")
```
with:
```python
        for name, arguments in CALLS:
            result = call("tools/call", {"name": name, "arguments": arguments})
            if result.get("isError"):
                raise SystemExit(f"{name}: {result['content']}")
```
and add below `FIT`:
```python
# One call per subsystem a frozen build could miss an import for: eos (evaluate),
# drift (status), graphs, conditions.
CALLS = (
    ("evaluate_fit", {"fit": FIT}),
    ("status", {}),
    ("fit_graph", {"fit": FIT, "graph": "lock_time", "x": "tgtSigRad", "y": "time",
                   "x_range": [10, 1000]}),
    ("conditions_format", {}),
)
```

In `tests/test_server.py`, replace `test_smoke_over_stdio` so the subprocess stays off the user's Pyfa and off the network (status() would otherwise ask GitHub):
```python
def test_smoke_over_stdio(booted, tmp_path):
    import json
    import time
    from pyfa_mcp import drift
    data = tmp_path / "data"
    data.mkdir()
    fresh = {"tag": None, "checked": time.time()}
    (data / "release-check.json").write_text(
        json.dumps({drift.PYFA_REPO: fresh, drift.OWN_REPO: fresh}), encoding="utf-8")
    proc = subprocess.run(
        [sys.executable, str(ROOT / "packaging" / "mcp_smoke.py"),
         sys.executable, "-m", "pyfa_mcp", "--data-dir", str(data),
         "--pyfa-dir", str(tmp_path / "no-pyfa")],
        capture_output=True, text=True, timeout=300, cwd=ROOT)
    assert proc.returncode == 0, proc.stdout + proc.stderr
```

Run: `.venv/Scripts/python -m pytest tests/test_server.py -q`
Expected: all pass (the server already serves these tools; this step widens the check before the frozen build relies on it).

- [ ] **Step 2: Add PyInstaller to the dev group**

In `pyproject.toml`:
```toml
[dependency-groups]
dev = ["pytest>=8", "pyinstaller==6.22.3"]
```
Run: `uv sync` (uv lives at `%APPDATA%\Python\Python311\Scripts\uv.exe` on the author's machine). Then `.venv/Scripts/python -m PyInstaller --version`.
Expected: `6.22.3`.

- [ ] **Step 3: Write `packaging/pyfa-mcp.spec`**

```python
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
```

- [ ] **Step 4: Build**

Run: `.venv/Scripts/python -m PyInstaller --noconfirm --log-level WARN packaging/pyfa-mcp.spec > build.log 2>&1` (from the repo root; ~3 min)
Expected: exit 0; `dist/pyfa-mcp/pyfa-mcp.exe` exists; `build.log` warnings only for optional SQLAlchemy drivers and `tzdata`.

- [ ] **Step 5: Smoke the frozen build, away from the user's data**

Run (Git Bash, from the repo root):
```bash
T=$(mktemp -d)
.venv/Scripts/python packaging/mcp_smoke.py dist/pyfa-mcp/pyfa-mcp.exe --data-dir "$T/data" --pyfa-dir "$T/no-pyfa"
dist/pyfa-mcp/pyfa-mcp.exe --print-config
```
Expected: `ok`; then JSON whose `command` is the absolute path of `dist\pyfa-mcp\pyfa-mcp.exe` and `args` is `[]`.

Then check the bundle carries its own version metadata (smoke's `status` call would still pass without it, reporting `pyfa_mcp_version: null`):
```bash
ls dist/pyfa-mcp/_internal | grep -i "^pyfa_mcp-"
```
Expected: `pyfa_mcp-0.1.0.dist-info`.

- [ ] **Step 6: Run the whole suite and commit**

Run: `.venv/Scripts/python -m pytest -q`
Expected: all pass.

```bash
git add packaging/pyfa-mcp.spec packaging/mcp_smoke.py tests/test_server.py pyproject.toml
git commit -m "Build pyfa-mcp.exe as one folder carrying Pyfa's source, with imports read from Pyfa itself"
```

---

### Task 4: The installer

**Files:**
- Create: `packaging/pyfa-mcp.iss`, `LICENSE`

**Interfaces:**
- Consumes: `dist/pyfa-mcp/` (Task 3), `pyfa-mcp.exe --register <client>` / `--unregister all` (Task 2).
- Produces: `dist/pyfa-mcp-v<version>-setup.exe`.

- [ ] **Step 1: Add the licence**

Run: `cp vendor/Pyfa/LICENSE LICENSE` (GPLv3 text; the project is GPL-3.0-or-later per `pyproject.toml`).

- [ ] **Step 2: Write `packaging/pyfa-mcp.iss`**

```
; Inno Setup script for the pyfa-mcp installer.
;
;     "C:\Program Files (x86)\Inno Setup 6\ISCC.exe" /DAppVersion=0.1.0 packaging\pyfa-mcp.iss
;
; after building dist\pyfa-mcp with packaging\pyfa-mcp.spec. The version has
; no default, so a build can never quietly carry a stale one.

#ifndef AppVersion
  #error Pass /DAppVersion=x.y.z -- the release decides it, not this file
#endif

#define AppName "pyfa-mcp"
#define AppExe "pyfa-mcp.exe"

[Setup]
AppId={{4C972680-9B4A-4B38-941A-23DEAE8517B6}
AppName={#AppName}
AppVersion={#AppVersion}
AppPublisher=Antoine Jacquin-Ravot
AppSupportURL=https://github.com/StormDelay/pyfa-ai-assisted
DefaultDirName={autopf}\{#AppName}
DisableProgramGroupPage=yes
; Per user: no UAC prompt, and --register edits this user's client configs.
PrivilegesRequired=lowest
ArchitecturesAllowed=x64
ArchitecturesInstallIn64BitMode=x64
OutputDir=..\dist
OutputBaseFilename={#AppName}-v{#AppVersion}-setup
Compression=lzma2/max
SolidCompression=yes
WizardStyle=modern
LicenseFile=..\LICENSE
UninstallDisplayIcon={app}\{#AppExe}
; An upgrade installs over the previous version, so client entries stay valid.
DisableDirPage=auto

[Languages]
Name: "english"; MessagesFile: "compiler:Default.isl"

[Tasks]
; One checkbox per MCP client found on this PC; the same marker dirs as
; pyfa_mcp/register.py's table. A client not listed: pyfa-mcp.exe --print-config.
Name: "claude_desktop"; Description: "Claude Desktop"; GroupDescription: "Let these AI apps use pyfa-mcp:"; Check: ClaudeDesktopFound
Name: "claude_code"; Description: "Claude Code"; GroupDescription: "Let these AI apps use pyfa-mcp:"; Check: Found('{%USERPROFILE}\.claude')
Name: "cursor"; Description: "Cursor"; GroupDescription: "Let these AI apps use pyfa-mcp:"; Check: Found('{%USERPROFILE}\.cursor')
Name: "windsurf"; Description: "Windsurf"; GroupDescription: "Let these AI apps use pyfa-mcp:"; Check: Found('{%USERPROFILE}\.codeium\windsurf')
Name: "devin"; Description: "Devin Desktop"; GroupDescription: "Let these AI apps use pyfa-mcp:"; Check: Found('{userappdata}\devin')
Name: "vscode"; Description: "VS Code"; GroupDescription: "Let these AI apps use pyfa-mcp:"; Check: Found('{userappdata}\Code\User')
Name: "codex"; Description: "Codex"; GroupDescription: "Let these AI apps use pyfa-mcp:"; Check: Found('{%USERPROFILE}\.codex')

[Files]
Source: "..\dist\pyfa-mcp\*"; DestDir: "{app}"; Flags: ignoreversion recursesubdirs createallsubdirs

[Run]
Filename: "{app}\{#AppExe}"; Parameters: "--register claude-desktop"; Tasks: claude_desktop; Flags: runhidden waituntilterminated; StatusMsg: "Registering with Claude Desktop..."
Filename: "{app}\{#AppExe}"; Parameters: "--register claude-code"; Tasks: claude_code; Flags: runhidden waituntilterminated; StatusMsg: "Registering with Claude Code..."
Filename: "{app}\{#AppExe}"; Parameters: "--register cursor"; Tasks: cursor; Flags: runhidden waituntilterminated; StatusMsg: "Registering with Cursor..."
Filename: "{app}\{#AppExe}"; Parameters: "--register windsurf"; Tasks: windsurf; Flags: runhidden waituntilterminated; StatusMsg: "Registering with Windsurf..."
Filename: "{app}\{#AppExe}"; Parameters: "--register devin"; Tasks: devin; Flags: runhidden waituntilterminated; StatusMsg: "Registering with Devin Desktop..."
Filename: "{app}\{#AppExe}"; Parameters: "--register vscode"; Tasks: vscode; Flags: runhidden waituntilterminated; StatusMsg: "Registering with VS Code..."
Filename: "{app}\{#AppExe}"; Parameters: "--register codex"; Tasks: codex; Flags: runhidden waituntilterminated; StatusMsg: "Registering with Codex..."

[UninstallRun]
; Before the files go: removes our entry wherever it is, writes nothing elsewhere.
Filename: "{app}\{#AppExe}"; Parameters: "--unregister all"; Flags: runhidden waituntilterminated; RunOnceId: "UnregisterClients"

[Code]
function Found(const Path: String): Boolean;
begin
  Result := DirExists(ExpandConstant(Path));
end;

{ The Microsoft Store build keeps its config under a Packages\Claude_<hash> dir. }
function ClaudeDesktopFound: Boolean;
var
  FindRec: TFindRec;
begin
  Result := DirExists(ExpandConstant('{userappdata}\Claude'));
  if not Result and FindFirst(ExpandConstant('{localappdata}\Packages\Claude_*'), FindRec) then
  begin
    Result := True;
    FindClose(FindRec);
  end;
end;
```

- [ ] **Step 3: Compile**

Run (PowerShell, repo root): `& "${env:ProgramFiles(x86)}\Inno Setup 6\ISCC.exe" /DAppVersion=0.1.0 packaging\pyfa-mcp.iss`
Expected: exit 0, `dist\pyfa-mcp-v0.1.0-setup.exe`.

- [ ] **Step 4: Silent install, smoke, uninstall — with every client task off**

`/MERGETASKS` with every task negated keeps a silent install from registering anything on the developer's machine.

Run (PowerShell, repo root; `$T` a fresh scratch dir):
```powershell
$T = Join-Path $env:TEMP "pyfa-mcp-installer-check"; Remove-Item -Recurse -Force $T -ErrorAction SilentlyContinue; New-Item -ItemType Directory $T | Out-Null
$off = "!claude_desktop,!claude_code,!cursor,!windsurf,!devin,!vscode,!codex"
$p = Start-Process dist\pyfa-mcp-v0.1.0-setup.exe -Wait -PassThru -ArgumentList "/VERYSILENT","/SUPPRESSMSGBOXES","/NORESTART","/DIR=$T\app","/MERGETASKS=$off","/LOG=$T\install.log"
$p.ExitCode
Select-String -Path "$T\install.log" -Pattern "--register" | Measure-Object | Select-Object -ExpandProperty Count
.venv\Scripts\python packaging\mcp_smoke.py "$T\app\pyfa-mcp.exe" --data-dir "$T\data" --pyfa-dir "$T\no-pyfa"
$u = Start-Process "$T\app\unins000.exe" -Wait -PassThru -ArgumentList "/VERYSILENT","/SUPPRESSMSGBOXES","/NORESTART"
Start-Sleep -Seconds 10; Test-Path "$T\app\pyfa-mcp.exe"
```
Expected: `0`; `0` (no registration ran); `ok`; `False` (uninstalled; Inno's uninstaller relaunches from temp, hence the wait).

- [ ] **Step 5: Commit**

```bash
git add packaging/pyfa-mcp.iss LICENSE
git commit -m "Install pyfa-mcp per user, with one checkbox per MCP client found, and unregister on uninstall"
```

- [ ] **Step 6: HUMAN CHECKPOINT — a real install**

Ask the user to run `dist\pyfa-mcp-v0.1.0-setup.exe` by hand:
1. The client page should list exactly the clients on this PC (here: Claude Desktop, Claude Code, VS Code). They tick the ones they want.
2. Then restart those clients, and ask one of them "what's the EHP of <one of my fits> in my Pyfa?".
3. Then check their Claude Desktop `preferences` and other servers survived (`claude_desktop_config.json.pyfa-mcp.bak` is the before-image).
4. Then uninstall from Windows Settings and see the `pyfa` entries gone.

---

## Self-review notes

- **Spec coverage:**
  - Packaging: one-folder PyInstaller build with eve.db and eos source (Task 3), and the Inno installer (Task 4).
  - The `--register <client>|auto`, `--unregister <client>` and `--print-config [json|toml]` flags (Tasks 1–2).
  - Registration that is table-driven, atomic, keeps other entries and backs up first (Task 1).
  - `auto` means "config directory exists", via each client's `marker` (Task 2).
  - The installer checkbox listing detected clients (Task 4).
  - The initial client table, verified, with `devin` added (Task 1).
  - The MCP smoke against the built exe (Task 3 locally; Plan 4 in CI).
- **Deliberate calls:**
  - **Claude Code goes through `claude mcp add-json`** when `claude.exe` exists, because a running session rewrites `~/.claude.json`; otherwise the file is written like the others.
  - **`--unregister all` is an addition** so the uninstaller needs one line.
  - **One installer task per client** rather than a single `auto` task, so the user picks.
  - **The `.bak` holds the state before our last write**, not every write ever.
  - **JSONC (VS Code allows comments) is refused, not parsed**: a comment-preserving JSONC writer is not worth owning; the error gives the snippet.
- **Types:** client names use hyphens everywhere in Python and `--register` args; Inno task names use underscores (Inno forbids hyphens) and map 1:1 in `[Run]`.
