[Setup]
AppName=PREVÉ
AppVersion=1.0
AppPublisher=Yuju Store
DefaultDirName={localappdata}\PREVE
DefaultGroupName=PREVÉ
PrivilegesRequired=lowest
OutputDir=..\salida_instalador
OutputBaseFilename=PREVE_Setup
Compression=lzma2
SolidCompression=yes
ArchitecturesInstallIn64BitMode=x64compatible
UninstallDisplayIcon={app}\PREVE.exe

[Languages]
Name: "es"; MessagesFile: "compiler:Languages\Spanish.isl"

[Tasks]
Name: "desktopicon"; Description: "Crear acceso directo en el escritorio"

[Files]
Source: "..\dist\PREVE\*"; DestDir: "{app}"; Flags: recursesubdirs ignoreversion; Excludes: "preve.db*,preve_config.json"

[Icons]
Name: "{group}\PREVÉ"; Filename: "{app}\PREVE.exe"
Name: "{autodesktop}\PREVÉ"; Filename: "{app}\PREVE.exe"; Tasks: desktopicon

[Run]
Filename: "{app}\PREVE.exe"; Description: "Abrir PREVÉ"; Flags: nowait postinstall skipifsilent
