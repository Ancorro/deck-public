# Agent browser on Windows: Google Chrome + Playwright MCP for Claude and Codex (agent-browser.sh's counterpart).
# One Playwright MCP service per host (127.0.0.1:8931, logon task "Agent browser"). Every Claude/Codex session connects
# to it and gets its own isolated, headless browser context, so agents can browse in parallel.
# Logins: sign in once in a real profile (~\.agent-chrome) with Start menu -> Agent Chrome (or `agent-chrome`); when
# that window closes its cookies are copied to ~\.config\agent-browser\state.json, which every new agent context loads.
# Re-runnable, no admin:  powershell -ExecutionPolicy Bypass -File deploy\agents\agent-browser.ps1
$ErrorActionPreference = 'Stop'
function Step($t) { Write-Host "`n== $t" }
function Write-Text($path, $text) { [IO.File]::WriteAllText($path, $text) }  # UTF-8, no BOM (Node's JSON.parse rejects one)

Step 'Google Chrome'
$chromes = "$env:ProgramFiles\Google\Chrome\Application\chrome.exe", "${env:ProgramFiles(x86)}\Google\Chrome\Application\chrome.exe",
  "$env:LOCALAPPDATA\Google\Chrome\Application\chrome.exe"
$chrome = $chromes | Where-Object { Test-Path $_ } | Select-Object -First 1
if (-not $chrome) {
  winget install --id Google.Chrome -e --silent --accept-source-agreements --accept-package-agreements
  $chrome = $chromes | Where-Object { Test-Path $_ } | Select-Object -First 1
  if (-not $chrome) { throw 'Chrome not found after install' }
}
(Get-Item $chrome).VersionInfo.ProductVersion

Step 'Playwright MCP'
if (-not (Get-Command npm -ErrorAction SilentlyContinue)) { throw 'Needs Node.js: winget install OpenJS.NodeJS.LTS' }
npm install -g -s @playwright/mcp
$pkg = Join-Path (npm root -g) '@playwright\mcp'
$node = (Get-Command node).Source
$dir = "$HOME\.config\agent-browser"
$state = "$dir\state.json"
$config = "$HOME\.config\playwright-mcp.json"
$bin = "$HOME\.local\bin"
New-Item -ItemType Directory -Force $dir, "$HOME\.agent-chrome", $bin | Out-Null
# Headless Chrome says "HeadlessChrome" in its user agent, and eBay, Reddit and others then block it: send the
# installed Chrome's normal one (re-run this script after a major Chrome update).
$chromeVer = (Get-ItemProperty 'HKCU:\Software\Google\Chrome\BLBeacon' -ErrorAction SilentlyContinue).version
$major = if ($chromeVer) { $chromeVer.Split('.')[0] } else { '154' }
$ua = "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/$major.0.0.0 Safari/537.36"
Write-Text $config (@{ browser = @{ browserName = 'chromium'; isolated = $true
  launchOptions = @{ channel = 'chrome'; headless = $true }; contextOptions = @{ storageState = $state; userAgent = $ua } } } | ConvertTo-Json -Depth 5)

# Copies the sign-in profile's cookies + local storage into state.json (session cookies included).
Write-Text "$dir\export.js" @"
const fs = require("fs");
const { chromium } = require(require.resolve("playwright-core", { paths: [$(ConvertTo-Json $pkg)] }));
const state = $(ConvertTo-Json $state), profile = $(ConvertTo-Json "$HOME\.agent-chrome");
(async () => {
  const ctx = await chromium.launchPersistentContext(profile, { channel: "chrome", headless: true });
  await ctx.storageState({ path: state + ".tmp" });
  await ctx.close();
  fs.renameSync(state + ".tmp", state);
  console.log("agent browser logins updated: " + JSON.parse(fs.readFileSync(state)).cookies.length + " cookies -> " + state);
})().catch((e) => { console.error("export failed (is Agent Chrome still open?):", e.message); process.exit(1); });
"@
Write-Text "$bin\agent-browser-export.cmd" "@`"$node`" `"$dir\export.js`" %*`r`n"
# Visible Chrome on the sign-in profile. Sign in to the sites agents need, close the window, and the logins are copied
# for agents (new agent sessions pick them up; running ones keep their old copy).
Write-Text "$bin\agent-chrome.cmd" "@start `"`" /wait `"$chrome`" --user-data-dir=`"$HOME\.agent-chrome`" %*`r`n@timeout /t 1 /nobreak >nul`r`n@call `"$bin\agent-browser-export.cmd`"`r`n"
$lnk = (New-Object -ComObject WScript.Shell).CreateShortcut("$env:APPDATA\Microsoft\Windows\Start Menu\Programs\Agent Chrome.lnk")
$lnk.TargetPath = "$bin\agent-chrome.cmd"; $lnk.IconLocation = "$chrome,0"; $lnk.WindowStyle = 7  # minimized console
$lnk.Description = 'Chrome profile used by Claude/Codex browser tools'; $lnk.Save()
if (-not (Test-Path $state)) {
  & "$bin\agent-browser-export.cmd"
  if ($LASTEXITCODE) { Write-Text $state '{"cookies":[],"origins":[]}' }
}

Step 'Shared browser service'
$taskArgs = "--headless `"$node`" `"$pkg\cli.js`" --config `"$config`" --host 127.0.0.1 --port 8931 --allowed-hosts 127.0.0.1:8931"
$task = 'Agent browser'
Stop-ScheduledTask -TaskName $task -ErrorAction SilentlyContinue
Register-ScheduledTask -TaskName $task -Force -Description 'Playwright MCP for Claude/Codex (127.0.0.1:8931, isolated context per session)' `
  -Action (New-ScheduledTaskAction -Execute "$env:WINDIR\System32\conhost.exe" -Argument $taskArgs) `
  -Trigger (New-ScheduledTaskTrigger -AtLogOn -User $env:USERNAME) `
  -Settings (New-ScheduledTaskSettingsSet -ExecutionTimeLimit 0 -RestartCount 99 -RestartInterval (New-TimeSpan -Minutes 1) `
    -AllowStartIfOnBatteries -DontStopIfGoingOnBatteries -MultipleInstances IgnoreNew) | Out-Null
Get-CimInstance Win32_Process -Filter "Name='node.exe'" | Where-Object CommandLine -like '*@playwright*mcp*cli.js*--port 8931*' |
  ForEach-Object { Stop-Process -Id $_.ProcessId -Force }  # a service from an earlier run
Start-ScheduledTask -TaskName $task
Start-Sleep 3
if (-not (Get-NetTCPConnection -LocalPort 8931 -State Listen -ErrorAction SilentlyContinue)) { throw 'service not listening on 8931' }

Step 'Register with Claude and Codex'
$ErrorActionPreference = 'Continue'  # 'remove' errors when there is nothing to remove
$url = 'http://127.0.0.1:8931/mcp'
claude mcp remove playwright --scope user 2>&1 | Out-Null
claude mcp add --transport http --scope user playwright $url | Out-Null
codex mcp remove playwright 2>&1 | Out-Null
codex mcp add playwright --url $url | Out-Null
claude mcp list 2>&1 | Select-String playwright
codex mcp list 2>&1 | Select-String playwright

Step 'Left for you'
'- Start menu -> Agent Chrome: sign in to the sites agents need, then close the window (logins are copied for agents)'
