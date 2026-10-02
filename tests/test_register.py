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


def test_a_config_that_is_not_utf8_is_refused_not_a_crash(roots):
    cursor = _install("cursor")
    cursor.config.write_bytes('{"mcpServers": {"é": {}}}'.encode("utf-16"))
    with pytest.raises(register.RegisterError, match="pyfa"):
        register.add(cursor)
    codex = _install("codex")
    register.add(codex)
    assert register.run(None, "all", None) == 0  # the uninstaller still cleans the rest
    assert not register.registered(codex)


@pytest.mark.parametrize("text", ['mcp_servers = "x"\n', '[[mcp_servers]]\nname = "x"\n'])
def test_codex_mcp_servers_that_is_not_a_table_is_refused(roots, text):
    c = _install("codex")
    c.config.write_text(text, encoding="utf-8")
    assert register.registered(c) is None
    with pytest.raises(register.RegisterError):
        register.add(c)
    assert c.config.read_text(encoding="utf-8") == text


def test_store_claude_desktop_never_started_still_registers(roots):
    package = roots.local / "Packages" / "Claude_pzs8sxrjxfjjc"
    package.mkdir(parents=True)  # installed, but its redirected %APPDATA% is not made yet
    c = register.clients()["claude-desktop"]
    assert c.config == package / "LocalCache" / "Roaming" / "Claude" / "claude_desktop_config.json"
    register.add(c)
    assert register.registered(c) == c.entry()


def test_reregister_keeps_what_the_user_added(roots, monkeypatch):
    c = _install("cursor")
    custom = {**c.entry(), "args": ["--pyfa-dir", "E:\pyfa"], "env": {"X": "1"}}
    c.config.write_text(json.dumps({"mcpServers": {"pyfa": custom}}), encoding="utf-8")
    before = c.config.read_bytes()
    assert "already" in register.add(c)  # same exe: leave it alone
    assert c.config.read_bytes() == before
    monkeypatch.setattr(register, "command", lambda: ("D:\new\pyfa-mcp.exe", []))
    register.add(c)
    assert register.registered(c) == {**custom, "command": "D:\new\pyfa-mcp.exe"}


def test_codex_reregister_keeps_what_the_user_added(roots, monkeypatch):
    c = _install("codex")
    cmd, _ = register.command()
    c.config.write_text(f'[mcp_servers.pyfa]\ncommand = {json.dumps(cmd)}\n'
                        'args = ["--pyfa-dir", "E:/pyfa"]\n\n[mcp_servers.pyfa.env]\nX = "1"\n',
                        encoding="utf-8")
    assert "already" in register.add(c)
    monkeypatch.setattr(register, "command", lambda: ("D:\new\pyfa-mcp.exe", []))
    register.add(c)
    assert register.registered(c) == {"command": "D:\new\pyfa-mcp.exe",
                                      "args": ["--pyfa-dir", "E:/pyfa"], "env": {"X": "1"}}


def test_the_backup_is_the_file_from_before_pyfa(roots, monkeypatch):
    c = _install("claude-desktop")
    c.config.write_text('{"mcpServers": {}, "mine": 1}', encoding="utf-8")
    register.add(c)
    monkeypatch.setattr(register, "command", lambda: ("D:\new\pyfa-mcp.exe", []))
    register.add(c)
    register.remove(c)
    backup = c.config.with_name(c.config.name + ".pyfa-mcp.bak")
    assert backup.read_text(encoding="utf-8") == '{"mcpServers": {}, "mine": 1}'


def test_a_failed_write_leaves_no_temp_file(roots, monkeypatch):
    c = _install("cursor")
    c.config.write_text('{"mcpServers": {}}', encoding="utf-8")

    def full_disk(src, dst):
        raise OSError("disk full")

    monkeypatch.setattr(register.os, "replace", full_disk)
    with pytest.raises(OSError):
        register.add(c)
    assert c.config.read_text(encoding="utf-8") == '{"mcpServers": {}}'
    assert not list(c.config.parent.glob("*.pyfa-mcp.tmp"))


def test_line_endings_are_kept(roots):
    codex = _install("codex")
    codex.config.write_bytes(CODEX.encode())  # LF
    register.add(codex)
    assert b"\r" not in codex.config.read_bytes()
    cursor = _install("cursor")
    cursor.config.write_bytes(b'{\r\n  "mcpServers": {}\r\n}\r\n')  # CRLF
    register.add(cursor)
    data = cursor.config.read_bytes()
    assert data.count(b"\r\n") == data.count(b"\n")


def test_codex_blank_lines_do_not_pile_up(roots):
    c = _install("codex")
    c.config.write_text(CODEX, encoding="utf-8")
    for _ in range(3):
        register.add(c)
        assert "\n\n\n" not in c.config.read_text(encoding="utf-8")
        register.remove(c)
    assert c.config.read_text(encoding="utf-8") == CODEX


def test_json_values_survive_the_rewrite(roots):
    c = _install("cursor")
    c.config.write_text('{"mcpServers": {}, "note": "café"}', encoding="utf-8")
    register.add(c)
    assert '"café"' in c.config.read_text(encoding="utf-8")


@pytest.mark.parametrize("text", [
    '{"mcpServers": {}, "a": 1, "a": 2}',      # duplicate keys would collapse
    '{"mcpServers": {}, "big": 1e400}',        # would come back as Infinity
])
def test_json_that_would_not_round_trip_is_left_alone(roots, text):
    c = _install("cursor")
    c.config.write_text(text, encoding="utf-8")
    with pytest.raises(register.RegisterError, match="pyfa"):
        register.add(c)
    assert c.config.read_text(encoding="utf-8") == text


def test_a_symlinked_config_stays_a_symlink(roots, tmp_path):
    c = _install("cursor")
    real = tmp_path / "dotfiles" / "mcp.json"
    real.parent.mkdir()
    real.write_text('{"mcpServers": {}}', encoding="utf-8")
    try:
        c.config.symlink_to(real)
    except OSError:
        pytest.skip("this account cannot create symlinks")
    register.add(c)
    assert c.config.is_symlink()
    assert register.NAME in json.loads(real.read_text(encoding="utf-8"))["mcpServers"]


def test_claude_cli_puts_the_old_entry_back_when_adding_fails(roots, monkeypatch):
    c = _install("claude-code")
    old = {"type": "stdio", "command": "old.exe", "args": []}
    c.config.write_text(json.dumps({"mcpServers": {"pyfa": old}}), encoding="utf-8")
    calls = []

    def fake_run(argv, **kwargs):
        calls.append(argv[1:])
        new_add = argv[2] == "add-json" and json.loads(argv[4]) != old
        return subprocess.CompletedProcess(argv, 1 if new_add else 0, "", "boom")

    monkeypatch.setattr(register, "_claude_cli", lambda: "C:\bin\claude.exe")
    monkeypatch.setattr(register.subprocess, "run", fake_run)
    with pytest.raises(register.RegisterError, match="previous entry was put back"):
        register.add(c)
    assert calls[-1] == ["mcp", "add-json", "pyfa", json.dumps(old), "--scope", "user"]


def test_unregister_all_keeps_a_checkout_s_entry(roots):
    cursor, vscode = _install("cursor"), _install("vscode")
    register.add(cursor)
    checkout = {"type": "stdio", "command": "D:\dev\.venv\Scripts\python.exe",
                "args": ["-m", "pyfa_mcp"]}
    vscode.config.write_text(json.dumps({"servers": {"pyfa": checkout}}), encoding="utf-8")
    assert register.run(None, "all", None) == 0
    assert register.registered(cursor) is None
    assert register.registered(vscode) == checkout
