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
    """(whole file, its servers object, attached to it); empty when there is no file yet."""
    try:
        text = client.config.read_text(encoding="utf-8-sig")  # Notepad writes a BOM
    except FileNotFoundError:
        text = ""
    try:
        root = json.loads(text) if text.strip() else {}
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
