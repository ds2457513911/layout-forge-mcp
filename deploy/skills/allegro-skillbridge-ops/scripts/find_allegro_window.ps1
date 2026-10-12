# 定位并取证"屏幕上的 Allegro 窗口"（多显示器安全）
# ---------------------------------------------------------------
# 解决三个坑：
#   1) 副屏坐标可能是负数（本例 DISPLAY2 @ (426,-1080)），
#      从 (0,0) 起抓 / 只抓 PrimaryScreen 会完全错过窗口。
#   2) PrintWindow 对 Allegro 的 OpenGL 画布无效（拿到缓存帧/黑块）。
#   3) 窗口 rect 与 VirtualScreen 坐标不同源，必须做原点换算。
#
# 用法：powershell -NoProfile -ExecutionPolicy Bypass -File find_allegro_window.ps1 [-OutDir <dir>]
# 产出：
#   <OutDir>\allegro_window.json   屏幕布局 + 窗口几何 + 可见性（给后续脚本消费）
#   <OutDir>\allegro_shot.png      VirtualScreen 全幅截图（含负坐标区域）
# 更新：2026-09-30

param(
  [string]$OutDir = "C:\Users\ds245\WorkBuddy\Layout\.workbuddy\cache\scratch"
)

$ErrorActionPreference = 'SilentlyContinue'
Add-Type -AssemblyName System.Windows.Forms
Add-Type -AssemblyName System.Drawing

$src = @'
using System;
using System.Runtime.InteropServices;
public class W32 {
  [DllImport("user32.dll")] public static extern bool GetWindowRect(IntPtr h, out RECT r);
  [DllImport("user32.dll")] public static extern bool IsIconic(IntPtr h);
  [DllImport("user32.dll")] public static extern bool IsWindowVisible(IntPtr h);
  [DllImport("user32.dll")] public static extern IntPtr GetForegroundWindow();
  [DllImport("user32.dll")] public static extern bool SetForegroundWindow(IntPtr h);
  [DllImport("user32.dll")] public static extern bool ShowWindow(IntPtr h, int n);
  [DllImport("dwmapi.dll")] public static extern int DwmGetWindowAttribute(IntPtr h, int a, out int v, int s);
  [StructLayout(LayoutKind.Sequential)] public struct RECT { public int L, T, R, B; }
}
'@
Add-Type -TypeDefinition $src

if (-not (Test-Path $OutDir)) { New-Item -ItemType Directory -Path $OutDir -Force | Out-Null }

# ---- 1) 屏幕布局 ----
$screens = @()
foreach ($s in [System.Windows.Forms.Screen]::AllScreens) {
  $screens += [pscustomobject]@{
    device  = $s.DeviceName
    primary = [bool]$s.Primary
    x       = $s.Bounds.X
    y       = $s.Bounds.Y
    w       = $s.Bounds.Width
    h       = $s.Bounds.Height
  }
}
$vs = [System.Windows.Forms.SystemInformation]::VirtualScreen

# ---- 2) 找 Allegro 顶层窗口 ----
$wins = @()
foreach ($p in (Get-Process allegro -ErrorAction SilentlyContinue)) {
  $h = $p.MainWindowHandle
  if ($h -eq 0) { continue }
  $r = New-Object W32+RECT
  [W32]::GetWindowRect($h, [ref]$r) | Out-Null
  $cloaked = -1
  [W32]::DwmGetWindowAttribute($h, 14, [ref]$cloaked, 4) | Out-Null
  $cx = [int](($r.L + $r.R) / 2); $cy = [int](($r.T + $r.B) / 2)
  $onScreen = ""
  foreach ($s in $screens) {
    if ($cx -ge $s.x -and $cx -lt ($s.x + $s.w) -and $cy -ge $s.y -and $cy -lt ($s.y + $s.h)) {
      $onScreen = $s.device
    }
  }
  $wins += [pscustomobject]@{
    pid          = $p.Id
    hwnd         = [int64]$h
    title        = $p.MainWindowTitle
    left         = $r.L
    top          = $r.T
    right        = $r.R
    bottom       = $r.B
    width        = $r.R - $r.L
    height       = $r.B - $r.T
    minimized    = [bool][W32]::IsIconic($h)
    visible      = [bool][W32]::IsWindowVisible($h)
    dwmCloaked   = $cloaked
    isForeground = ([W32]::GetForegroundWindow() -eq $h)
    onScreen     = $onScreen
  }
}

# ---- 3) 全虚拟屏截图（关键：源坐标用 vs.Left/vs.Top，可含负值）----
$shot = Join-Path $OutDir "allegro_shot.png"
try {
  $bmp = New-Object System.Drawing.Bitmap $vs.Width, $vs.Height
  $g = [System.Drawing.Graphics]::FromImage($bmp)
  $g.CopyFromScreen($vs.Left, $vs.Top, 0, 0, $bmp.Size)
  $bmp.Save($shot, [System.Drawing.Imaging.ImageFormat]::Png)
  $g.Dispose(); $bmp.Dispose()
} catch {
  $shot = ""
}

# ---- 4) 输出 JSON（给像素统计/后续脚本消费）----
$json = [pscustomobject]@{
  generatedAt   = (Get-Date).ToString("s")
  virtualScreen = [pscustomobject]@{ x = $vs.X; y = $vs.Y; w = $vs.Width; h = $vs.Height }
  screens       = $screens
  windows       = $wins
  shot          = $shot
}
$jsonPath = Join-Path $OutDir "allegro_window.json"
$json | ConvertTo-Json -Depth 6 | Out-File -FilePath $jsonPath -Encoding utf8
$jsonPath
