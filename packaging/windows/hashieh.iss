; Inno Setup script for the Windows installer.
; Built by CI:  iscc /DAppVersion=0.2.0 /DAppSourceDir=dist\Hashieh /DOutputDir=release packaging\windows\hashieh.iss

#ifndef AppVersion
  #define AppVersion "0.0.0"
#endif
#ifndef AppSourceDir
  #define AppSourceDir "..\..\dist\Hashieh"
#endif
#ifndef OutputDir
  #define OutputDir "..\..\release"
#endif

[Setup]
AppId={{6C1C9A2E-5D7B-4C8F-9E1A-48A1B7D3F0C2}
AppName=Hashieh
AppVersion={#AppVersion}
AppPublisher=HAstaShakiba
AppPublisherURL=https://hastashakiba.github.io/hashieh/
AppSupportURL=https://github.com/HAstaShakiba/hashieh/issues
DefaultDirName={localappdata}\Programs\Hashieh
DefaultGroupName=Hashieh
DisableProgramGroupPage=yes
; Per-user install: no administrator rights needed.
PrivilegesRequired=lowest
OutputDir={#OutputDir}
OutputBaseFilename=Hashieh-{#AppVersion}-windows-x64-setup
SetupIconFile=..\..\data\icons\hashieh.ico
UninstallDisplayIcon={app}\Hashieh.exe
Compression=lzma2/max
SolidCompression=yes
WizardStyle=modern
ArchitecturesAllowed=x64compatible
ArchitecturesInstallIn64BitMode=x64compatible
CloseApplications=yes
LicenseFile=..\..\LICENSE

[Tasks]
Name: "desktopicon"; Description: "{cm:CreateDesktopIcon}"; GroupDescription: "{cm:AdditionalIcons}"; Flags: unchecked

[Files]
Source: "{#AppSourceDir}\*"; DestDir: "{app}"; Flags: recursesubdirs createallsubdirs ignoreversion

[Icons]
Name: "{group}\Hashieh"; Filename: "{app}\Hashieh.exe"
Name: "{autodesktop}\Hashieh"; Filename: "{app}\Hashieh.exe"; Tasks: desktopicon

[Registry]
; Hashieh adds this itself on first start ("Start at login"); remove it on uninstall.
Root: HKCU; Subkey: "Software\Microsoft\Windows\CurrentVersion\Run"; ValueName: "Hashieh"; Flags: uninsdeletevalue dontcreatekey

[Run]
Filename: "{app}\Hashieh.exe"; Description: "Start Hashieh"; Flags: nowait postinstall skipifsilent

[UninstallRun]
Filename: "{app}\hashieh-cli.exe"; Parameters: "--quit"; Flags: runhidden skipifdoesntexist; RunOnceId: "QuitHashieh"
