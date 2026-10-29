# Phone screen (Virtual Display Driver monitor): phonescreen.ps1 status | on <width> <height> | off -> JSON status;
# windows -> JSON list of app windows; move <hwnd> <1 = to phone | 0 = back> -> the list again.
# With the "Deck VDD on/off" tasks (phonescreen-setup.ps1, admin once) the driver itself is enabled only while
# attached: Windows lists its monitor in Settings whenever the driver is enabled, even detached.
param([string]$action = "status", [long]$w = 0, [int]$h = 0)
function DriverOn { (Get-PnpDevice -FriendlyName "Virtual Display Driver" -ErrorAction SilentlyContinue).Status -eq "OK" }
function Toggle([string]$state) {
  if (-not (Get-ScheduledTask -TaskName "Deck VDD $state" -ErrorAction SilentlyContinue)) { return $false }
  Start-ScheduledTask -TaskName "Deck VDD $state"
  for ($i = 0; $i -lt 40 -and (DriverOn) -ne ($state -eq "on"); $i++) { Start-Sleep -Milliseconds 250 }
  return $true
}
# Driver off: nothing to ask DeckDisplay, so the modes come from the driver's settings file.
function OffStatus {
  $modes = ([xml](Get-Content "C:\VirtualDisplayDriver\vdd_settings.xml")).vdd_settings.resolutions.resolution |
    ForEach-Object { "`"$($_.width)x$($_.height)`"" }
  "{`"on`":false,`"ox`":0,`"oy`":0,`"x`":0,`"y`":0,`"w`":0,`"h`":0,`"modes`":[" + ($modes -join ",") + "]}"
}
if ($action -eq "on" -and -not (DriverOn)) {
  if (-not (Toggle "on")) { throw "Virtual Display Driver is disabled (run phonescreen-setup.ps1 as admin once)" }
  Start-Sleep -Milliseconds 1000  # the monitor appears a moment after the device
}
if (-not (DriverOn)) { if ($action -in "status", "off") { OffStatus; exit } }
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
  if (Toggle "off") { Start-Sleep -Milliseconds 300; OffStatus; exit }
}
Start-Sleep -Milliseconds 300
[DeckDisplay]::Status()
