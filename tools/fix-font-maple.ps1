# ─────────────────────────────────────────────────────────────────────────────
# fix-font-maple.ps1 —— 修 Windows 侧「找不到以下字体：Maple Mono NF CN」
#
# 病根（2026-10-10 实测坐实）：
#   HKCU\Software\Microsoft\Windows NT\CurrentVersion\Fonts 里，Maple 四条
#   写的是**裸文件名**（MapleMono-NF-CN-Regular.ttf），而 Windows 只认**全路径**
#   —— 同目录能用的 UbuntuMono 写的就是全路径。裸文件名 → GDI/DirectWrite
#   都枚举不到 → Windows Terminal 报「找不到以下字体」。
#   （字体本体没问题：nameID1 就是 "Maple Mono NF CN"，与 WT 的 face 一致）
#
# 用法（Windows 侧，普通用户即可）：
#   powershell -ExecutionPolicy Bypass -File D:\AI\pi-lab\tools\fix-font-maple.ps1
# 验证：
#   powershell -ExecutionPolicy Bypass -File D:\AI\pi-lab\tools\check-font-maple.ps1
# 生效：改完要**重启 Windows Terminal**（它在启动时读字体表；若是登录后才装的，
#       注销/重启一次更稳）
# ─────────────────────────────────────────────────────────────────────────────
$ErrorActionPreference = "SilentlyContinue"
$key = "HKCU:\Software\Microsoft\Windows NT\CurrentVersion\Fonts"
$dir = "$env:LOCALAPPDATA\Microsoft\Windows\Fonts"
$map = [ordered]@{
  "Maple Mono NF CN Regular (TrueType)"    = "MapleMono-NF-CN-Regular.ttf"
  "Maple Mono NF CN Bold (TrueType)"       = "MapleMono-NF-CN-Bold.ttf"
  "Maple Mono NF CN Italic (TrueType)"     = "MapleMono-NF-CN-Italic.ttf"
  "Maple Mono NF CN BoldItalic (TrueType)" = "MapleMono-NF-CN-BoldItalic.ttf"
}
Write-Output "== 1) 把注册表条目改成全路径 =="
foreach ($k in $map.Keys) {
  $full = Join-Path $dir $map[$k]
  if (Test-Path -LiteralPath $full) {
    Set-ItemProperty -Path $key -Name $k -Value $full -Type String
    Write-Output ("  OK  " + $k)
  } else { Write-Output ("  缺文件  " + $full) }
}
Write-Output "== 2) 载入当前会话 + 广播 WM_FONTCHANGE =="
Add-Type -Namespace WP -Name Fonts -MemberDefinition @'
[DllImport("gdi32.dll", CharSet=CharSet.Unicode)] public static extern int AddFontResourceW(string lpFileName);
[DllImport("user32.dll", CharSet=CharSet.Unicode)] public static extern int SendMessageTimeout(IntPtr hWnd, uint Msg, IntPtr wParam, IntPtr lParam, uint fuFlags, uint uTimeout, out IntPtr lpdwResult);
'@
$loaded = 0
foreach ($k in $map.Keys) { $loaded += [WP.Fonts]::AddFontResourceW((Join-Path $dir $map[$k])) }
Write-Output ("  载入字面数: " + $loaded)
$res = [IntPtr]::Zero
[void][WP.Fonts]::SendMessageTimeout([IntPtr]0xffff, 0x001D, [IntPtr]::Zero, [IntPtr]::Zero, 2, 2000, [ref]$res)
Write-Output "  已广播；**重启 Windows Terminal** 即可不再报错"
