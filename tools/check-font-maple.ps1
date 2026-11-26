$ErrorActionPreference = "SilentlyContinue"
Add-Type -AssemblyName System.Drawing
Write-Output "== A. fresh-process GDI enum (this is a brand-new process) =="
$fams = (New-Object System.Drawing.Text.InstalledFontCollection).Families | Where-Object { $_.Name -match 'Maple' }
if ($fams) { $fams | ForEach-Object { "  FOUND: " + $_.Name } } else { "  NOT FOUND in GDI" }
Write-Output ""
Write-Output "== B. registry (persistent) =="
(Get-ItemProperty "HKCU:\Software\Microsoft\Windows NT\CurrentVersion\Fonts").PSObject.Properties | Where-Object { $_.Name -match 'Maple' } | ForEach-Object { "  " + $_.Name + "  ->  [" + $_.Value + "]" }
Write-Output ""
Write-Output "== C. Windows Terminal running? (needs restart to re-read fonts) =="
$wt = Get-Process WindowsTerminal -ErrorAction SilentlyContinue
if ($wt) { $wt | ForEach-Object { "  PID " + $_.Id + "  started " + $_.StartTime } } else { "  not running" }
Write-Output ""
Write-Output "== D. DirectWrite view (what WT actually uses) =="
$child = "Add-Type -AssemblyName System.Drawing; (New-Object System.Drawing.Text.InstalledFontCollection).Families | Where-Object { `$_.Name -like '*Maple*' } | ForEach-Object { '  DW/GDI: ' + `$_.Name }"
& powershell.exe -NoProfile -Command $child
