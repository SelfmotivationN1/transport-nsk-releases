#ifndef PayloadDir
  #error PayloadDir must point to the verified portable preview files.
#endif
#ifndef UpdateVersion
  #define UpdateVersion "1.2.4"
#endif
#ifndef OutputName
  #define OutputName "TransportNSK-1.2.4-ManualUpdate"
#endif

[Setup]
AppId={{22D7D156-5DCE-4474-8608-413976D66AEC}
AppName=Транспорт НСК
AppVersion={#UpdateVersion}
AppPublisher=НСК / @iSelfmotivation
AppSupportURL=https://github.com/SelfmotivationN1/transport-nsk-releases
AppMutex=TransportNSK.Application.Running
DefaultDirName={code:ExistingInstallDirectory}
DisableDirPage=yes
DisableProgramGroupPage=yes
UsePreviousAppDir=no
PrivilegesRequired=lowest
ArchitecturesAllowed=x64compatible
ArchitecturesInstallIn64BitMode=x64compatible
Uninstallable=no
CloseApplications=no
RestartApplications=no
OutputBaseFilename={#OutputName}
Compression=lzma2/fast
WizardStyle=modern

[Languages]
Name: "russian"; MessagesFile: "compiler:Languages\Russian.isl"

[Files]
Source: "{#PayloadDir}\Транспорт НСК.exe"; DestDir: "{app}"; Flags: ignoreversion

[Run]
Filename: "{app}\Транспорт НСК.exe"; WorkingDir: "{app}"; Check: FromUpdate; Flags: nowait runasoriginaluser

[Code]
const
  UninstallKey = 'Software\Microsoft\Windows\CurrentVersion\Uninstall\{22D7D156-5DCE-4474-8608-413976D66AEC}_is1';

var
  InstallDirectory: String;

function InitializeSetup(): Boolean;
begin
  Result := RegQueryStringValue(HKCU64, UninstallKey, 'InstallLocation', InstallDirectory);
  if not Result then
    Result := RegQueryStringValue(HKCU32, UninstallKey, 'InstallLocation', InstallDirectory);
  if Result and (InstallDirectory <> '') then
  begin
    InstallDirectory := RemoveBackslashUnlessRoot(ExpandFileName(InstallDirectory));
    Result := FileExists(AddBackslash(InstallDirectory) + 'installed.marker') and
      FileExists(AddBackslash(InstallDirectory) + 'Транспорт НСК.exe');
  end
  else
    Result := False;

  if not Result then
    SuppressibleMsgBox('Установка «Транспорт НСК» в текущем профиле Windows не найдена.', mbError, MB_OK, MB_OK);
end;

function ExistingInstallDirectory(Param: String): String;
begin
  Result := InstallDirectory;
end;

function PrepareToInstall(var NeedsRestart: Boolean): String;
begin
  Result := '';
  if CompareText(RemoveBackslashUnlessRoot(ExpandFileName(ExpandConstant('{app}'))), InstallDirectory) <> 0 then
    Result := 'Обновление можно установить только в папку существующего приложения.';
end;

function FromUpdate(): Boolean;
begin
  Result := ExpandConstant('{param:FROMUPDATE|0}') = '1';
end;
