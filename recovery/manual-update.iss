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
DefaultDirName={code:ExistingInstallDirectory}
DisableDirPage=no
AppendDefaultDirName=no
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

function FromUpdate(): Boolean;
begin
  Result := ExpandConstant('{param:FROMUPDATE|0}') = '1';
end;

function NormalDirectory(Directory: String): String;
begin
  Result := '';
  if Directory <> '' then
    Result := RemoveBackslashUnlessRoot(ExpandFileName(Directory));
end;

function HasApplication(Directory: String): Boolean;
begin
  Result := (Directory <> '') and
    FileExists(AddBackslash(Directory) + 'Транспорт НСК.exe');
end;

function InitializeSetup(): Boolean;
begin
  if FromUpdate() then
  begin
    InstallDirectory := NormalDirectory(ExpandConstant('{param:DIR|}'));
    Result := HasApplication(InstallDirectory);
    if not Result then
      SuppressibleMsgBox('Не найдена папка запущенной копии «Транспорт НСК».', mbError, MB_OK, MB_OK);
    exit;
  end;

  if not RegQueryStringValue(HKCU64, UninstallKey, 'InstallLocation', InstallDirectory) then
    RegQueryStringValue(HKCU32, UninstallKey, 'InstallLocation', InstallDirectory);
  InstallDirectory := NormalDirectory(InstallDirectory);
  if not HasApplication(InstallDirectory) then
    InstallDirectory := ExpandConstant('{src}');
  Result := True;
end;

function ExistingInstallDirectory(Param: String): String;
begin
  Result := InstallDirectory;
end;

function ShouldSkipPage(PageID: Integer): Boolean;
begin
  Result := FromUpdate() and (PageID = wpSelectDir);
end;

function TargetReady(Directory: String): Boolean;
var
  Stream: TFileStream;
begin
  Result := False;
  try
    Stream := TFileStream.Create(AddBackslash(Directory) + 'Транспорт НСК.exe', fmOpenReadWrite or fmShareExclusive);
    Stream.Free;
    Result := True;
  except
    Log('Waiting for write access to the target executable.');
  end;
end;

function PrepareToInstall(var NeedsRestart: Boolean): String;
var
  Target: String;
  Retry: Integer;
begin
  Target := NormalDirectory(ExpandConstant('{app}'));
  if not HasApplication(Target) or (FromUpdate() and (CompareText(Target, InstallDirectory) <> 0)) then
  begin
    Result := 'Обновление можно установить только в папку существующего приложения.';
    exit;
  end;

  if FromUpdate() then
    for Retry := 1 to 80 do
    begin
      if TargetReady(Target) then break;
      Sleep(250);
    end;

  Result := '';
  if not TargetReady(Target) then
    Result := 'Сохраните работу, закройте «Транспорт НСК» в выбранной папке и повторите обновление.';
end;
