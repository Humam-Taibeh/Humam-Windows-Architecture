; ============================================================
;  PULSE — Windows Setup (Inno Setup 6)
; ============================================================
;  Builds PULSE_Setup_v<VERSION>.exe from the onedir PyInstaller output
;  in dist\PULSE\.
;
;  Not invoked directly — tools\build_release.ps1 defines MyAppVersion
;  from the repo's VERSION file and calls iscc. Compiling this by hand
;  works too; it just falls back to reading VERSION itself.
;
;  Build:  iscc installer\pulse.iss
;  Out:    dist\PULSE_Setup_v10.3.0.exe

#ifndef MyAppVersion
  ; Read the same VERSION file the GUI, the engine and the spec read, so a
  ; hand-run compile cannot stamp a different version than the build script
  ; would have. #include is the only way to get a file's contents into a
  ; preprocessor variable, hence the temporary define.
  #define FileHandle FileOpen("..\VERSION")
  #define MyAppVersion Trim(FileRead(FileHandle))
  #expr FileClose(FileHandle)
#endif

#define MyAppName      "PULSE"
#define MyAppPublisher "Humam Taibeh"
#define MyAppURL       "https://github.com/Humam-Taibeh/Humam-Windows-Architecture"
#define MyAppExeName   "PULSE.exe"
#define SourceDir      "..\dist\PULSE"

[Setup]
; ------------------------------------------------------------
;  IDENTITY
; ------------------------------------------------------------
; AppId is a STABLE GUID and must never change. It is the key Windows uses
; to recognise an existing installation: change it and every upgrade
; installs a second copy alongside the first, with two Start Menu entries
; and an uninstaller that only removes half of it.
AppId={{7B2F4C91-3E8A-4D6B-9F1C-2A5E8D04B7C3}
AppName={#MyAppName}
AppVersion={#MyAppVersion}
AppVerName={#MyAppName} {#MyAppVersion}
VersionInfoVersion={#MyAppVersion}
AppPublisher={#MyAppPublisher}
AppPublisherURL={#MyAppURL}
AppSupportURL={#MyAppURL}/issues
AppUpdatesURL={#MyAppURL}/releases

; ------------------------------------------------------------
;  INSTALL LOCATION AND PRIVILEGE
; ------------------------------------------------------------
; ADMIN / PROGRAM FILES BY DEFAULT, and this is a security decision rather
; than a convention. PULSE runs asInvoked and elevates per task (~24 of
; them touch HKLM, services or machine state). If the application lived in
; a user-writable directory, any process running as the user could replace
; PULSE.exe or the PowerShell engine beside it and wait for the next
; elevated task to run it — a straight privilege-escalation path. Program
; Files is not writable without elevation, which closes it.
;
; This is the same boundary src/utils/resources.py defends when it refuses
; to resolve core.ps1 from the executable's own directory, and the reason
; the PyInstaller spec moved off onefile (%TEMP% extraction).
;
; A per-user install remains available for machines where the technician
; has no admin rights — Inno offers the choice when this override is set —
; but it is not the default, and the elevated-task caveat above applies to
; anyone who picks it.
PrivilegesRequired=admin
PrivilegesRequiredOverridesAllowed=dialog
DefaultDirName={autopf}\{#MyAppName}
DefaultGroupName={#MyAppName}
DisableProgramGroupPage=yes

; ------------------------------------------------------------
;  WIZARD — Next / Next / Install
; ------------------------------------------------------------
LicenseFile=..\LICENSE
WizardStyle=modern
SetupIconFile=..\assets\pulse.ico
UninstallDisplayIcon={app}\{#MyAppExeName}
UninstallDisplayName={#MyAppName} {#MyAppVersion}
OutputDir=..\dist
OutputBaseFilename=PULSE_Setup_v{#MyAppVersion}
Compression=lzma2/max
SolidCompression=yes

; 64-bit only: the engine calls into 64-bit WMI/registry views and the
; PySide6 build is amd64. "compatible" keeps this correct on ARM64 too.
ArchitecturesAllowed=x64compatible
ArchitecturesInstallIn64BitMode=x64compatible

; ------------------------------------------------------------
;  IN-PLACE UPGRADE
; ------------------------------------------------------------
; The auto-updater (src/utils/updater.py) downloads this installer and runs
; it while PULSE is on screen. Without these two, Setup would hit a locked
; PULSE.exe and either fail or demand a reboot; with them it closes the
; running copy, replaces it, and starts it again afterwards.
CloseApplications=yes
RestartApplications=yes
CloseApplicationsFilter=*.exe,*.dll,*.ps1

; ------------------------------------------------------------
;  SIGNING
; ------------------------------------------------------------
; Wired but inert until a certificate exists. UNSIGNED BUILDS WILL TRIP
; SMARTSCREEN on every download — that is expected, not a defect. Once a
; cert is available, define the `signtool` command in the IDE/CLI and
; uncomment these; the updater's Authenticode check (see updater.verify)
; starts enforcing a publisher match at the same moment.
; SignTool=signtool
; SignedUninstaller=yes

[Languages]
Name: "english"; MessagesFile: "compiler:Default.isl"

[Tasks]
Name: "desktopicon"; Description: "{cm:CreateDesktopIcon}"; \
    GroupDescription: "{cm:AdditionalIcons}"

[Files]
; The whole onedir output: PULSE.exe, _internal\ (Qt + the Python runtime),
; src\backend\ (the PowerShell engine), playbooks\, assets\ and VERSION.
; recursesubdirs+createallsubdirs keeps the layout byte-for-byte, which
; matters because resources.py resolves everything relative to it.
Source: "{#SourceDir}\*"; DestDir: "{app}"; Flags: ignoreversion recursesubdirs createallsubdirs

[Icons]
Name: "{group}\{#MyAppName}"; Filename: "{app}\{#MyAppExeName}"
Name: "{group}\{cm:UninstallProgram,{#MyAppName}}"; Filename: "{uninstallexe}"
Name: "{autodesktop}\{#MyAppName}"; Filename: "{app}\{#MyAppExeName}"; Tasks: desktopicon

[Run]
; nowait + skipifsilent: a silent upgrade driven by the updater must not
; block waiting for the app it just relaunched.
Filename: "{app}\{#MyAppExeName}"; \
    Description: "{cm:LaunchProgram,{#StringChange(MyAppName, '&', '&&')}}"; \
    Flags: nowait postinstall skipifsilent

[UninstallDelete]
; PyInstaller writes nothing here at runtime, but a user dropping extra
; playbooks beside the app (a supported workflow — see resources.user_roots)
; leaves files Setup never installed and therefore never tracks.
Type: filesandordirs; Name: "{app}\playbooks"
Type: dirifempty; Name: "{app}"

[Code]
{ ------------------------------------------------------------
  Uninstall: offer to remove preferences, never assume.

  prefs.py stores theme, window geometry and per-task run history under
  HKCU\Software\HumamTaibeh\Pulse. Two things must both stay true:

    * An UPGRADE must never touch it. Inno runs the old uninstaller during
      some upgrade paths, and silently wiping a user's history and window
      placement on a version bump would be a bug they could not explain.
      Hence UninstallSilent() — an upgrade-driven uninstall is silent, an
      interactive one is not.

    * A deliberate REMOVAL should be able to leave nothing behind, but that
      is the user's call. Someone uninstalling to reinstall a fixed build
      does not want to lose a year of task history.
  ------------------------------------------------------------ }
procedure CurUninstallStepChanged(CurUninstallStep: TUninstallStep);
var
  Key: String;
begin
  if CurUninstallStep = usPostUninstall then
  begin
    if UninstallSilent() then
      Exit;
    Key := 'Software\HumamTaibeh\Pulse';
    if RegKeyExists(HKEY_CURRENT_USER, Key) then
    begin
      if MsgBox('Also remove PULSE''s saved settings?' + #13#10#13#10 +
                'This deletes your theme, window position and per-task run ' +
                'history. Choose No if you plan to reinstall.',
                mbConfirmation, MB_YESNO or MB_DEFBUTTON2) = IDYES then
        RegDeleteKeyIncludingSubkeys(HKEY_CURRENT_USER, Key);
    end;
  end;
end;
