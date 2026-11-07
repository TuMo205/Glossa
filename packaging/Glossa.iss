; Inno Setup 6.5+: bộ cài Glossa cho Windows 10/11 64-bit, cài riêng cho người dùng (không cần quyền Admin).
; Biên dịch qua packaging\build.py (bước installer), hoặc:  ISCC /DAppVersion=2.0.0 packaging\Glossa.iss
#define AppName "Glossa"
#define AppExe "Glossa.exe"
#ifndef AppVersion
  #define AppVersion "0.0.0"
#endif

[Setup]
AppId={{58944FD4-2317-4E09-9932-2182F1233552}
AppName={#AppName}
AppVersion={#AppVersion}
AppVerName={#AppName} {#AppVersion}
AppPublisher=Tumo
AppPublisherURL=https://github.com/TuMo205/Glossa
AppSupportURL=https://github.com/TuMo205/Glossa/issues
AppUpdatesURL=https://github.com/TuMo205/Glossa/releases/latest
VersionInfoVersion={#AppVersion}
VersionInfoDescription={#AppName} - dịch màn hình sang tiếng Việt
DefaultDirName={autopf}\Glossa
DefaultGroupName={#AppName}
DisableProgramGroupPage=yes
DisableDirPage=auto
PrivilegesRequired=lowest
ArchitecturesAllowed=x64compatible
ArchitecturesInstallIn64BitMode=x64compatible
MinVersion=10.0.17763
OutputDir=..\dist
OutputBaseFilename=Glossa-Setup-{#AppVersion}
SetupIconFile=..\build\glossa.ico
UninstallDisplayIcon={app}\{#AppExe}
UninstallDisplayName={#AppName}
Compression=lzma2/max
SolidCompression=yes
WizardStyle=modern
AppMutex=Local\Glossa.SingleInstance
CloseApplications=force
RestartApplications=no
ShowLanguageDialog=no
LanguageDetectionMethod=none

[Languages]
#ifexist "Vietnamese.isl"
Name: "vi"; MessagesFile: "compiler:Default.isl,Vietnamese.isl"
#endif
Name: "en"; MessagesFile: "compiler:Default.isl"

[CustomMessages]
GroupOther=Tuỳ chọn khác:
AutoStart=Tự chạy {#AppName} mỗi khi bật máy (như Unikey)
SelfTest=Tự kiểm tra {#AppName}
Guide=Hướng dẫn sử dụng {#AppName}
AskDeleteData=Xoá luôn cài đặt và bộ nhớ bản dịch của {#AppName}?%n%n(Chọn Không nếu sau này bạn còn cài lại.)

[Tasks]
Name: "desktopicon"; Description: "{cm:CreateDesktopIcon}"; GroupDescription: "{cm:AdditionalIcons}"
Name: "autostart"; Description: "{cm:AutoStart}"; GroupDescription: "{cm:GroupOther}"; Flags: unchecked

[InstallDelete]
; dọn file của bản cũ trước khi chép bản mới (tránh lẫn thư viện cũ)
Type: filesandordirs; Name: "{app}\_internal"

[Files]
Source: "..\dist\Glossa\*"; DestDir: "{app}"; Flags: ignoreversion recursesubdirs createallsubdirs

[Icons]
Name: "{autoprograms}\{#AppName}"; Filename: "{app}\{#AppExe}"; Comment: "Dịch mọi chữ trên màn hình sang tiếng Việt"
Name: "{autoprograms}\{#AppName} - công cụ\{cm:SelfTest}"; Filename: "{app}\{#AppExe}"; Parameters: "--selftest"
Name: "{autoprograms}\{#AppName} - công cụ\{cm:Guide}"; Filename: "{sys}\notepad.exe"; Parameters: """{app}\_internal\HUONG_DAN.md"""
Name: "{autoprograms}\{#AppName} - công cụ\{cm:UninstallProgram,{#AppName}}"; Filename: "{uninstallexe}"
Name: "{autodesktop}\{#AppName}"; Filename: "{app}\{#AppExe}"; Tasks: desktopicon

[Registry]
Root: HKCU; Subkey: "Software\Microsoft\Windows\CurrentVersion\Run"; ValueType: string; ValueName: "Glossa"; ValueData: """{app}\{#AppExe}"""; Flags: uninsdeletevalue; Tasks: autostart

[Run]
Filename: "{app}\{#AppExe}"; Description: "{cm:LaunchProgram,{#AppName}}"; Flags: nowait postinstall skipifsilent

[Code]
procedure CurUninstallStepChanged(CurUninstallStep: TUninstallStep);
var
  DataDir: String;
begin
  if CurUninstallStep = usPostUninstall then
  begin
    { app có thể đã tự bật "khởi động cùng Windows" từ bảng điều khiển }
    RegDeleteValue(HKEY_CURRENT_USER, 'Software\Microsoft\Windows\CurrentVersion\Run', 'Glossa');
    DataDir := ExpandConstant('{userappdata}\Glossa');
    if DirExists(DataDir) then
      if SuppressibleMsgBox(CustomMessage('AskDeleteData'), mbConfirmation, MB_YESNO or MB_DEFBUTTON2, IDNO) = IDYES then
        DelTree(DataDir, True, True, True);
  end;
end;
