; Inno Setup script for the Cuelog installer. Built by packaging\build.ps1.
#ifndef AppVersion
  #define AppVersion "0.0.0"
#endif

[Setup]
AppId={{6B0C2F7E-3D2A-4C8E-9A51-4E7C1D0F5A21}
AppName=Cuelog
AppVersion={#AppVersion}
AppVerName=Cuelog {#AppVersion}
AppPublisher=flodlol
AppPublisherURL=https://github.com/flodlol/Cuelog
AppSupportURL=https://github.com/flodlol/Cuelog/issues
DefaultDirName={localappdata}\Programs\Cuelog
DefaultGroupName=Cuelog
DisableProgramGroupPage=yes
DisableDirPage=yes
PrivilegesRequired=lowest
OutputDir=..\dist
OutputBaseFilename=Cuelog-Setup-{#AppVersion}
SetupIconFile=..\cuelog\assets\cuelog.ico
UninstallDisplayIcon={app}\Cuelog.exe
UninstallDisplayName=Cuelog
Compression=lzma2/max
SolidCompression=yes
WizardStyle=modern
ArchitecturesAllowed=x64compatible
ArchitecturesInstallIn64BitMode=x64compatible
MinVersion=10.0
CloseApplications=yes
LicenseFile=..\LICENSE

[Tasks]
Name: "desktopicon"; Description: "Create a &desktop shortcut"; GroupDescription: "Shortcuts:"

[Files]
Source: "..\dist\Cuelog\*"; DestDir: "{app}"; Flags: ignoreversion recursesubdirs createallsubdirs

[InstallDelete]
; old app files from a previous version (your data lives elsewhere and is never touched)
Type: filesandordirs; Name: "{app}\_internal"

[Icons]
Name: "{autoprograms}\Cuelog"; Filename: "{app}\Cuelog.exe"; AppUserModelID: "Cuelog.DesktopScrobbler"
Name: "{autodesktop}\Cuelog"; Filename: "{app}\Cuelog.exe"; Tasks: desktopicon; AppUserModelID: "Cuelog.DesktopScrobbler"

[Run]
Filename: "{app}\Cuelog.exe"; Description: "Start Cuelog"; Flags: nowait postinstall skipifsilent

[Messages]
FinishedLabel=Cuelog is installed. Your listening data stays in AppData\Local\Cuelog, also if you uninstall.
