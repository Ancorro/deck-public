# One-time, as admin: two scheduled tasks that start / stop the TightVNC service (tvnserver), so Deck (no admin) runs
# the VNC server only while a page streams the Screen tab, and sets the service to manual start. S4U: they run hidden,
# without a stored password. Undo: vnc-setup.ps1 -remove (the service goes back to automatic start).
param([switch]$remove, [string]$service = "tvnserver")
foreach ($state in "on", "off") {
  $name = "Deck VNC $state"
  Unregister-ScheduledTask -TaskName $name -Confirm:$false -ErrorAction SilentlyContinue
  if ($remove) { continue }
  $verb = if ($state -eq "on") { "Start" } else { "Stop" }
  $action = New-ScheduledTaskAction -Execute "powershell.exe" -Argument "-NoProfile -NonInteractive -Command `"$verb-Service $service`""
  $who = New-ScheduledTaskPrincipal -UserId "$env:USERDOMAIN\$env:USERNAME" -LogonType S4U -RunLevel Highest
  $opts = New-ScheduledTaskSettingsSet -AllowStartIfOnBatteries -DontStopIfGoingOnBatteries -ExecutionTimeLimit (New-TimeSpan -Minutes 2)
  Register-ScheduledTask -TaskName $name -Action $action -Principal $who -Settings $opts | Out-Null
}
Set-Service $service -StartupType $(if ($remove) { "Automatic" } else { "Manual" })
