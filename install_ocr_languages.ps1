# Cai goi nhan dang chu (OCR) cua Windows cho Glossa. Can quyen Administrator.
# Dung: CAI_NGON_NGU_OCR.bat                 (chon tu danh sach)
#       CAI_NGON_NGU_OCR.bat -Langs 4,5      (cai thang tieng Nhat + tieng Han theo so thu tu)
#       ... -Codes ja-JP,ko-KR -Log x.log -NoPause   (Glossa goi tu Bang dieu khien)
param([string]$Langs = "", [string]$Codes = "", [string]$Log = "", [switch]$NoPause)

function Say([string]$msg) {
    Write-Host $msg
    if ($Log) { try { Add-Content -Path $Log -Value $msg -Encoding UTF8 } catch {} }
}

$principal = New-Object Security.Principal.WindowsPrincipal([Security.Principal.WindowsIdentity]::GetCurrent())
if (-not $principal.IsInRole([Security.Principal.WindowsBuiltInRole]::Administrator)) {
    Write-Host "Can quyen Administrator - dang xin quyen (bam Yes)..."
    $argList = "-NoProfile -ExecutionPolicy Bypass -File `"$PSCommandPath`""
    if ($Langs) { $argList += " -Langs `"$Langs`"" }
    if ($Codes) { $argList += " -Codes `"$Codes`"" }
    if ($Log) { $argList += " -Log `"$Log`"" }
    if ($NoPause) { $argList += " -NoPause" }
    Start-Process powershell.exe -Verb RunAs -ArgumentList $argList
    exit
}
if ($Log) { try { Set-Content -Path $Log -Value "" -Encoding UTF8 } catch {} }

$catalog = [ordered]@{
    "1"  = @("en-US", "Tieng Anh")
    "2"  = @("zh-CN", "Tieng Trung (gian the)")
    "3"  = @("zh-TW", "Tieng Trung (phon the)")
    "4"  = @("ja-JP", "Tieng Nhat")
    "5"  = @("ko-KR", "Tieng Han")
    "6"  = @("ru-RU", "Tieng Nga")
    "7"  = @("fr-FR", "Tieng Phap")
    "8"  = @("de-DE", "Tieng Duc")
    "9"  = @("es-ES", "Tieng Tay Ban Nha")
    "10" = @("it-IT", "Tieng Y")
    "11" = @("pt-BR", "Tieng Bo Dao Nha")
    "12" = @("ar-SA", "Tieng A Rap")
    "13" = @("el-GR", "Tieng Hy Lap")
    "14" = @("tr-TR", "Tieng Tho Nhi Ky")
    "15" = @("pl-PL", "Tieng Ba Lan")
    "16" = @("nl-NL", "Tieng Ha Lan")
}

Say "Dang doc danh sach goi OCR cua Windows (mat vai giay)..."
$caps = Get-WindowsCapability -Online | Where-Object { $_.Name -like "Language.OCR*" }

if ($Codes) {
    $todo = $Codes -split "[,; ]+" | Where-Object { $_ }
} else {
    Write-Host ""
    foreach ($k in $catalog.Keys) {
        $code = $catalog[$k][0]
        $c = $caps | Where-Object { $_.Name -like "Language.OCR~~~$code~*" } | Select-Object -First 1
        $state = if ($c) { if ($c.State -eq "Installed") { "DA CAI" } else { "chua cai" } } else { "khong co" }
        "{0,3}. {1,-26} {2,-6}  [{3}]" -f $k, $catalog[$k][1], $code, $state
    }
    Write-Host ""
    if (-not $Langs) {
        $Langs = Read-Host "Nhap so thu tu can cai, cach nhau dau phay (Enter = 1,2,4,5)"
        if (-not $Langs) { $Langs = "1,2,4,5" }
    }
    $todo = @()
    foreach ($k in ($Langs -split "[,; ]+" | Where-Object { $_ })) {
        if ($catalog.Contains($k)) { $todo += $catalog[$k][0] } else { Say "Bo qua: $k" }
    }
}

$failed = 0
foreach ($code in $todo) {
    $c = $caps | Where-Object { $_.Name -like "Language.OCR~~~$code~*" } | Select-Object -First 1
    if (-not $c) { Say "KHONG CO goi OCR cho $code tren ban Windows nay"; $failed++; continue }
    if ($c.State -eq "Installed") { Say "Da co san: $code"; continue }
    Say "Dang cai $code ... (1-3 phut, can Internet)"
    try {
        Add-WindowsCapability -Online -Name $c.Name -ErrorAction Stop | Out-Null
        Say "XONG $code"
    } catch {
        Say "LOI $code : $($_.Exception.Message)"
        $failed++
    }
}
Say "HOAN TAT"
if (-not $NoPause) {
    Write-Host "Trong Glossa: Bang dieu khien -> Ngon ngu... -> Lam moi (hoac mo lai chuong trinh)."
    Read-Host "Nhan Enter de dong"
}
exit $failed
