; Inno Setup script for the pyfa-mcp installer.
;
;     "C:\Program Files (x86)\Inno Setup 6\ISCC.exe" /DAppVersion=0.1.0 /DPyfaVersion=2.69.0 packaging\pyfa-mcp.iss
;
; after building dist\pyfa-mcp with packaging\pyfa-mcp.spec. Both versions
; come from scripts/track_pyfa.py (version, pyfa) and have no default, so a
; build can never quietly carry a stale one.

#ifndef AppVersion
  #error Pass /DAppVersion=x.y.z -- scripts/track_pyfa.py version
#endif
#ifndef PyfaVersion
  #error Pass /DPyfaVersion=x.y.z -- scripts/track_pyfa.py pyfa
#endif

#define AppName "pyfa-mcp"
#define AppExe "pyfa-mcp.exe"

[Setup]
AppId={{4C972680-9B4A-4B38-941A-23DEAE8517B6}
AppName={#AppName}
AppVersion={#AppVersion}
AppVerName={#AppName} {#AppVersion} (Pyfa {#PyfaVersion})
AppPublisher=Antoine Jacquin-Ravot
AppSupportURL=https://github.com/StormDelay/pyfa-ai-assisted
DefaultDirName={autopf}\{#AppName}
DisableProgramGroupPage=yes
; Per user: no UAC prompt, and --register edits this user's client configs.
PrivilegesRequired=lowest
ArchitecturesAllowed=x64
ArchitecturesInstallIn64BitMode=x64
OutputDir=..\dist
OutputBaseFilename={#AppName}-v{#AppVersion}-pyfa{#PyfaVersion}-setup
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
Name: "devin"; Description: "Devin Desktop"; GroupDescription: "Let these AI apps use pyfa-mcp:"; Check: Found('{%APPDATA}\devin')
Name: "vscode"; Description: "VS Code"; GroupDescription: "Let these AI apps use pyfa-mcp:"; Check: Found('{%APPDATA}\Code\User')
Name: "codex"; Description: "Codex"; GroupDescription: "Let these AI apps use pyfa-mcp:"; Check: Found('{%USERPROFILE}\.codex')

[Files]
Source: "..\dist\pyfa-mcp\*"; DestDir: "{app}"; Flags: ignoreversion recursesubdirs createallsubdirs

[UninstallRun]
; Before the files go: removes our entry wherever it is, writes nothing elsewhere.
Filename: "{app}\{#AppExe}"; Parameters: "--unregister all"; Flags: runhidden waituntilterminated; RunOnceId: "UnregisterClients"

[Code]
function Found(const Path: String): Boolean;
begin
  Result := DirExists(ExpandConstant(Path));
end;

{ The Microsoft Store build keeps its config under a Packages\Claude_<hash> dir.
  Same environment variables and dirs as pyfa_mcp/register.py's table, so the
  checkbox shows exactly when --register can succeed. }
function ClaudeDesktopFound: Boolean;
var
  FindRec: TFindRec;
begin
  Result := DirExists(ExpandConstant('{%APPDATA}\Claude'));
  if not Result and FindFirst(ExpandConstant('{%LOCALAPPDATA}\Packages\Claude_*'), FindRec) then
  begin
    Result := True;
    FindClose(FindRec);
  end;
end;

var
  Failures: String;

{ Runs pyfa-mcp.exe --register for one ticked client, keeping its output:
  a [Run] entry would hide both the failure and the snippet it prints. }
procedure RegisterWith(const Task, Client: String);
var
  Output: String;
  Text: AnsiString;
  ResultCode: Integer;
begin
  if not WizardIsTaskSelected(Task) then
    Exit;
  WizardForm.StatusLabel.Caption := 'Adding pyfa-mcp to ' + Client + '...';
  Output := ExpandConstant('{tmp}\register-' + Client + '.txt');
  if not Exec(ExpandConstant('{cmd}'),
              '/C ""' + ExpandConstant('{app}\{#AppExe}') + '" --register ' + Client +
              ' > "' + Output + '" 2>&1"',
              '', SW_HIDE, ewWaitUntilTerminated, ResultCode) or (ResultCode <> 0) then
  begin
    if not LoadStringFromFile(Output, Text) then
      Text := 'pyfa-mcp.exe could not be started';
    Failures := Failures + #13#10#13#10 + Client + ': ' + String(Text);
  end;
end;

procedure CurStepChanged(CurStep: TSetupStep);
begin
  if CurStep <> ssPostInstall then
    Exit;
  RegisterWith('claude_desktop', 'claude-desktop');
  RegisterWith('claude_code', 'claude-code');
  RegisterWith('cursor', 'cursor');
  RegisterWith('windsurf', 'windsurf');
  RegisterWith('devin', 'devin');
  RegisterWith('vscode', 'vscode');
  RegisterWith('codex', 'codex');
  if Failures <> '' then
  begin
    Log('Registration failures:' + Failures);
    SuppressibleMsgBox('pyfa-mcp is installed, but could not be added to every AI app:' +
      Failures + #13#10#13#10 + 'To add it by hand, run:' + #13#10 + '"' +
      ExpandConstant('{app}\{#AppExe}') + '" --print-config',
      mbError, MB_OK, IDOK);
  end;
end;
