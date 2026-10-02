"""Register pyfa-mcp with MCP clients by editing their config files.

One row per client: where its config lives, the directory that says it is
installed, the key its servers sit under, and whether entries carry
"type": "stdio". Every write keeps everything else in the file (values, line
endings, a symlink), keeps the file as it was before pyfa-mcp first changed it
in <file>.pyfa-mcp.bak, and replaces it atomically. A file this cannot parse,
or could not write back unchanged, is the user's: it is refused, never
rewritten, and the error carries the snippet to add by hand.
"""
from __future__ import annotations

import json
import os
import re
import shutil
import subprocess
import sys
import tomllib
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


def _claude_desktop_dir(appdata: Path, local: Path) -> tuple[Path, Path]:
    """(config dir, install marker): the classic install's, else the Microsoft Store build's.

    Windows redirects a packaged app's %APPDATA% to
    %LOCALAPPDATA%\\Packages\\<family>\\LocalCache\\Roaming; the family name
    ends in a publisher hash, so it is matched by prefix. The package dir is
    the marker: a Store install never started has no redirected dir yet, and
    the installer (pyfa-mcp.iss, ClaudeDesktopFound) checks the same thing.
    """
    classic = appdata / "Claude"
    if classic.is_dir():
        return classic, classic
    for package in sorted(local.glob("Packages/Claude_*")):
        return package / "LocalCache" / "Roaming" / "Claude", package
    return classic, classic


def clients() -> dict[str, Client]:
    # ponytail: Windows paths only; the installer is Windows-only too.
    home = Path.home()
    appdata = Path(os.environ.get("APPDATA") or home / "AppData" / "Roaming")
    local = Path(os.environ.get("LOCALAPPDATA") or home / "AppData" / "Local")
    desktop, desktop_marker = _claude_desktop_dir(appdata, local)
    windsurf = home / ".codeium" / "windsurf"
    return {
        "claude-desktop": Client("Claude Desktop", desktop / "claude_desktop_config.json",
                                 desktop_marker, "mcpServers", False),
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


def _toml_table(entry: dict) -> str:
    # JSON strings and arrays are valid TOML basic strings and arrays.
    return (f"[mcp_servers.{NAME}]\ncommand = {json.dumps(entry['command'])}\n"
            f"args = {json.dumps(entry['args'])}\n")


def snippet(fmt: str = "json") -> str:
    cmd, args = command()
    if fmt == "toml":
        return _toml_table({"command": cmd, "args": args})
    return json.dumps({"mcpServers": {NAME: {"command": cmd, "args": args}}}, indent=2)


def _claude_cli() -> str | None:
    """Claude Code's own CLI, when it is a real exe (a .cmd shim mangles JSON args)."""
    found = shutil.which("claude")
    return found if found and found.lower().endswith(".exe") else None


def _by_hand(client: Client, why: str) -> RegisterError:
    by_hand = (_toml_table(client.entry()) if client.toml
               else json.dumps({NAME: client.entry()}, indent=2))
    return RegisterError(f"{client.config}: {why}; not touching it. "
                         f"Add this under \"{client.key}\" by hand:\n{by_hand}")


def _backup(path: Path) -> None:
    """The file as it was before pyfa-mcp first changed it: written once, never overwritten."""
    backup = path.with_name(path.name + ".pyfa-mcp.bak")
    if path.exists() and not backup.exists():
        shutil.copy2(path, backup)


def _replace(path: Path, text: str) -> None:
    """Back up, then swap the whole file: a crash mid-write must not cost other servers."""
    target = path.resolve() if path.is_symlink() else path  # write through a dotfiles link
    _backup(path)
    target.parent.mkdir(parents=True, exist_ok=True)  # only ever below an installed client
    if target.exists() and b"\r\n" in target.read_bytes():
        text = text.replace("\r\n", "\n").replace("\n", "\r\n")  # keep the file's endings
    temp = target.with_name(target.name + ".pyfa-mcp.tmp")
    try:
        temp.write_text(text, encoding="utf-8", newline="")
        os.replace(temp, target)
    finally:
        temp.unlink(missing_ok=True)


def _strict_object(pairs: list) -> dict:
    keys = [key for key, _ in pairs]
    if len(keys) != len(set(keys)):
        raise ValueError("a key appears twice, which a rewrite would collapse")
    return dict(pairs)


def _finite(text: str) -> float:
    value = float(text)
    if value in (float("inf"), float("-inf")):
        raise ValueError(f"{text} is out of range and would come back as Infinity")
    return value


def _no_constant(name: str):
    raise ValueError(f"{name} is not JSON")


def _dump(root: dict) -> str:
    return json.dumps(root, indent=2, ensure_ascii=False) + "\n"


def _read_text(client: Client) -> str:
    try:
        return client.config.read_text(encoding="utf-8-sig")  # Notepad writes a BOM
    except FileNotFoundError:
        return ""
    except UnicodeDecodeError as exc:  # UTF-16 from PowerShell 5, or a legacy code page
        raise _by_hand(client, f"not UTF-8 ({exc.reason})") from exc


def _read_json(client: Client) -> tuple[dict, dict]:
    """(whole file, its servers object, attached to it); empty when there is no file yet."""
    text = _read_text(client)
    try:
        root = json.loads(text, object_pairs_hook=_strict_object, parse_float=_finite,
                          parse_constant=_no_constant) if text.strip() else {}
    except json.JSONDecodeError as exc:
        raise _by_hand(client, f"not plain JSON ({exc.msg}, line {exc.lineno})") from exc
    except ValueError as exc:
        raise _by_hand(client, f"cannot be rewritten unchanged ({exc})") from exc
    if not isinstance(root, dict):
        raise _by_hand(client, "not a JSON object")
    servers = root.setdefault(client.key, {})
    if not isinstance(servers, dict):
        raise _by_hand(client, f'"{client.key}" is not an object')
    return root, servers


_HEADER = re.compile(r"^\s*\[")
_OUR_TABLE = re.compile(r'^\s*\[\s*mcp_servers\s*\.\s*"?' + NAME + r'"?\s*(\.[^\]]*)?\]')


def _read_toml(client: Client) -> tuple[str, dict]:
    """(text, its mcp_servers table); ("", {}) when there is no file yet."""
    text = _read_text(client)
    try:
        data = tomllib.loads(text)
    except tomllib.TOMLDecodeError as exc:
        raise _by_hand(client, f"not valid TOML ({exc})") from exc
    servers = data.get("mcp_servers", {})
    if not isinstance(servers, dict):
        raise _by_hand(client, "mcp_servers is not a table")
    return text, servers


def _merged(existing, entry: dict) -> dict:
    """Our entry for this exe, keeping what the user added to an older one (env, args)."""
    if not isinstance(existing, dict):
        return entry
    extra = list(existing.get("args") or [])
    if extra[:2] == ["-m", "pyfa_mcp"]:  # a checkout's entry: those two were ours
        extra = extra[2:]
    return {**existing, **entry, "args": entry["args"] + extra}


_OUR_MAIN_TABLE = re.compile(r'^\s*\[\s*mcp_servers\s*\.\s*"?' + NAME + r'"?\s*\]')
_KEY_LINE = re.compile(r"^(\s*)(command|args)\s*=")


def _retarget(text: str, want: dict) -> str:
    """Rewrite the command and args lines of our table in place, nothing else."""
    out, ours = [], False
    for line in text.splitlines(keepends=True):
        if _HEADER.match(line):
            ours = bool(_OUR_MAIN_TABLE.match(line))
        match = _KEY_LINE.match(line) if ours else None
        if match:
            end = "\n" if line.endswith("\n") else ""
            line = f"{match.group(1)}{match.group(2)} = {json.dumps(want[match.group(2)])}{end}"
        out.append(line)
    return "".join(out)


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
        servers = tomllib.loads(text).get("mcp_servers", {})
        ours = servers.get(NAME) if isinstance(servers, dict) else "not a table"
    except tomllib.TOMLDecodeError:
        ours = "unparseable"
    if ours != want:  # e.g. `pyfa = {...}` inline under [mcp_servers]
        raise _by_hand(client, f"{NAME} is defined in a form this cannot edit")
    _replace(client.config, text)


def _claude(cli: str, *args: str) -> None:
    done = subprocess.run([cli, *args], capture_output=True, text=True,
                          stdin=subprocess.DEVNULL,
                          creationflags=getattr(subprocess, "CREATE_NO_WINDOW", 0))
    if done.returncode != 0:
        raise RegisterError(f"claude {' '.join(args[:2])} failed: "
                            f"{(done.stderr or done.stdout).strip()}")


def _check_installed(client: Client) -> None:
    if not client.marker.is_dir():
        raise RegisterError(f"{client.label} is not installed (no {client.marker})")


def registered(client: Client) -> dict | None:
    try:
        if client.toml:
            found = _read_toml(client)[1].get(NAME)
        else:
            found = _read_json(client)[1].get(NAME)
    except (RegisterError, OSError):
        return None
    return found if isinstance(found, dict) else None


def add(client: Client) -> str:
    _check_installed(client)
    entry = client.entry()
    if client.toml:
        text, servers = _read_toml(client)
        existing = servers.get(NAME)
        if isinstance(existing, dict) and existing.get("command") == entry["command"]:
            return f"already registered in {client.config}"
        if isinstance(existing, dict):
            want = _merged(existing, entry)
            _write_toml(client, _retarget(text, want), want)
        else:
            kept = _without_ours(text).rstrip("\n")
            _write_toml(client, (kept + "\n\n" if kept.strip() else "") + _toml_table(entry), entry)
        return f"registered in {client.config}; restart {client.label} to use it"
    root, servers = _read_json(client)
    existing = servers.get(NAME)
    # The same exe already: leave the entry be, with whatever the user added to it.
    if isinstance(existing, dict) and existing.get("command") == entry["command"]:
        return f"already registered in {client.config}"
    entry = _merged(existing, entry)
    cli = _claude_cli() if client.label == "Claude Code" else None
    if cli:
        # Running Claude Code sessions rewrite ~/.claude.json; let Claude Code
        # make the change itself. Backed up all the same.
        _backup(client.config)
        if NAME in servers:
            _claude(cli, "mcp", "remove", NAME, "--scope", "user")
        try:
            _claude(cli, "mcp", "add-json", NAME, json.dumps(entry), "--scope", "user")
        except RegisterError as exc:
            if NAME not in servers:
                raise
            try:  # the old entry is already removed: do not leave the user with none
                _claude(cli, "mcp", "add-json", NAME, json.dumps(servers[NAME]), "--scope", "user")
            except RegisterError:
                raise RegisterError(f"{exc}; the previous entry could not be put back either "
                                    f"(it is in {client.config}.pyfa-mcp.bak)") from exc
            raise RegisterError(f"{exc}; the previous entry was put back") from exc
        return "registered through the claude CLI; restart Claude Code to use it"
    servers[NAME] = entry
    _replace(client.config, _dump(root))
    return f"registered in {client.config}; restart {client.label} to use it"


def remove(client: Client) -> str:
    if not client.config.exists():
        return "not registered"
    if client.toml:
        text, servers = _read_toml(client)
        if NAME not in servers:
            return "not registered"
        kept = _without_ours(text).rstrip("\n")
        _write_toml(client, kept + "\n" if kept else "", None)
        return f"removed from {client.config}"
    root, servers = _read_json(client)
    if NAME not in servers:
        return "not registered"
    cli = _claude_cli() if client.label == "Claude Code" else None
    if cli:
        _backup(client.config)
        _claude(cli, "mcp", "remove", NAME, "--scope", "user")
        return "removed through the claude CLI"
    del servers[NAME]
    _replace(client.config, _dump(root))
    return f"removed from {client.config}"


def run(register_name: str | None, unregister_name: str | None,
        print_config: str | None) -> int:
    """The --register / --unregister / --print-config command line; returns the exit code."""
    for stream in (sys.stdout, sys.stderr):  # the installer sends these to a file
        stream.reconfigure(errors="replace")
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
        # Only entries that start this pyfa-mcp: uninstalling must not remove a checkout's.
        ours = command()[0]
        names = [n for n, c in table.items() if (registered(c) or {}).get("command") == ours]
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
        except (RegisterError, OSError, UnicodeError) as exc:
            failed = True
            print(f"{table[name].label}: {exc}", file=sys.stderr)
    return 1 if failed else 0
