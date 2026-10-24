# Phone screen (Virtual Display Driver monitor): phonescreen.ps1 status | on <width> <height> | off -> JSON status;
# windows -> JSON list of app windows; move <hwnd> <1 = to phone | 0 = back> -> the list again.
param([string]$action = "status", [long]$w = 0, [int]$h = 0)
Add-Type -Path (Join-Path $PSScriptRoot "DeckDisplay.cs") -ReferencedAssemblies System.Linq, System.Core
[DeckDisplay]::SetProcessDpiAwarenessContext([IntPtr]-4) | Out-Null  # physical pixels, as VNC sees them
if ($action -eq "on") {
  if (-not ([DeckDisplay]::Status() | ConvertFrom-Json).on) {
    $rc = [DeckDisplay]::Set($true); if ($rc -ne 0) { throw "SetDisplayConfig on: $rc" }; Start-Sleep -Milliseconds 500 }
  $rc = [DeckDisplay]::Size($w, $h); if ($rc -ne 0) { throw "ChangeDisplaySettingsEx: $rc" }
  # Phone-like text size: the monitor is about as many pixels wide as the phone, so match a phone's ~2.5x.
  Start-Sleep -Milliseconds 300; $rc = [DeckDisplay]::Scale(250); if ($rc -ne 0) { Write-Warning "scale: $rc" }
} elseif ($action -eq "windows") { [DeckDisplay]::Windows(); exit
} elseif ($action -eq "move") { [DeckDisplay]::Move($w, $h -ne 0); Start-Sleep -Milliseconds 200; [DeckDisplay]::Windows(); exit
} elseif ($action -eq "off") {
  $rc = [DeckDisplay]::Set($false); if ($rc -ne 0) { throw "SetDisplayConfig off: $rc" }
}
Start-Sleep -Milliseconds 300
[DeckDisplay]::Status()
