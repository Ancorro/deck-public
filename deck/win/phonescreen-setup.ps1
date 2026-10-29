# One-time, as admin: two scheduled tasks that enable / disable the Virtual Display Driver device, so phonescreen.ps1
# (no admin) can turn the driver on only while streaming. Windows lists a monitor for the driver whenever it is enabled,
# even detached. S4U: they run hidden, without a stored password. Undo: phonescreen-setup.ps1 -remove
param([switch]$remove)
foreach ($state in "on", "off") {
  $name = "Deck VDD $state"
  Unregister-ScheduledTask -TaskName $name -Confirm:$false -ErrorAction SilentlyContinue
  if ($remove) { continue }
  $verb = if ($state -eq "on") { "Enable" } else { "Disable" }
  $cmd = "Get-PnpDevice -FriendlyName 'Virtual Display Driver' | $verb-PnpDevice -Confirm:`$false"
  $action = New-ScheduledTaskAction -Execute "powershell.exe" -Argument "-NoProfile -NonInteractive -Command `"$cmd`""
  $who = New-ScheduledTaskPrincipal -UserId "$env:USERDOMAIN\$env:USERNAME" -LogonType S4U -RunLevel Highest
  $opts = New-ScheduledTaskSettingsSet -AllowStartIfOnBatteries -DontStopIfGoingOnBatteries -ExecutionTimeLimit (New-TimeSpan -Minutes 2)
  Register-ScheduledTask -TaskName $name -Action $action -Principal $who -Settings $opts | Out-Null
}
