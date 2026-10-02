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
