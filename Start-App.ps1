# Start-App.ps1 — launched silently by Launch.vbs
# Starts backend + frontend (both hidden), polls until ready, opens browser.

$root    = $PSScriptRoot
$backend = Join-Path $root "backend"
$frontend= Join-Path $root "frontend"

# Refresh PATH from the registry (Machine + User). When Node is installed after
# the current Windows session started, the inherited PATH is stale and 'npm'/'node'
# are not found. Rebuilding it here lets the launcher work without a reboot.
$env:Path = ([Environment]::GetEnvironmentVariable("Path", "Machine"),
             [Environment]::GetEnvironmentVariable("Path", "User")) -join ";"

# Show an error dialog. System.Windows.Forms is loaded lazily (only on failure)
# so the happy path doesn't pay the assembly-load cost on every launch.
function Show-Error($msg) {
    Add-Type -AssemblyName System.Windows.Forms
    [System.Windows.Forms.MessageBox]::Show($msg, "PBI Test Utility", "OK", "Error") | Out-Null
}

function Test-Port($port) {
    # Vite listens on localhost which resolves to IPv6 (::1); uvicorn listens on
    # 127.0.0.1. Try both so readiness checks work regardless of IP family.
    foreach ($addr in @("127.0.0.1", "::1")) {
        try {
            $tcp = New-Object System.Net.Sockets.TcpClient
            if ($tcp.ConnectAsync($addr, $port).Wait(200)) { $tcp.Close(); return $true }
            $tcp.Close()
        } catch { }
    }
    return $false
}

# Resolve an executable: PATH first, then known install locations.
function Resolve-Cmd($name, $fallbacks) {
    $c = Get-Command $name -ErrorAction SilentlyContinue
    if ($c) { return $c.Source }
    foreach ($p in $fallbacks) { if ($p -and (Test-Path $p)) { return $p } }
    return $null
}

# Wait for ALL ports concurrently (not one after another), polling at a fine
# granularity so a server that binds in ~0.3s is detected almost immediately.
function Wait-ForPorts($ports, $timeoutSec = 45) {
    $sw = [System.Diagnostics.Stopwatch]::StartNew()
    $pending = [System.Collections.Generic.List[int]]::new()
    $ports | ForEach-Object { $pending.Add($_) }
    while ($pending.Count -gt 0 -and $sw.Elapsed.TotalSeconds -lt $timeoutSec) {
        for ($i = $pending.Count - 1; $i -ge 0; $i--) {
            if (Test-Port $pending[$i]) { $pending.RemoveAt($i) }
        }
        if ($pending.Count -gt 0) { Start-Sleep -Milliseconds 150 }
    }
    return $pending.Count -eq 0
}

# ── Backend ───────────────────────────────────────────────────────────────
if (-not (Test-Port 8000)) {
    $uvicorn = Join-Path $backend ".venv\Scripts\uvicorn.exe"
    if (-not (Test-Path $uvicorn)) {
        Show-Error "Backend not set up.`nPlease run Setup.bat first."
        exit 1
    }
    Start-Process -FilePath $uvicorn `
        -ArgumentList "main:app", "--host", "127.0.0.1", "--port", "8000" `
        -WorkingDirectory $backend `
        -WindowStyle Hidden
}

# ── Frontend ──────────────────────────────────────────────────────────────
if (-not (Test-Port 5173)) {
    if (-not (Test-Path (Join-Path $frontend "node_modules"))) {
        Show-Error "Frontend not set up.`nPlease run Setup.bat first."
        exit 1
    }
    # Fast path: run Vite directly via node, skipping the npm + cmd.exe wrapper
    # layers (which add ~1s of process startup). Fall back to npm if node/vite
    # can't be located.
    $node   = Resolve-Cmd "node.exe" @("$env:ProgramFiles\nodejs\node.exe",
                                       "${env:ProgramFiles(x86)}\nodejs\node.exe")
    $viteJs = Join-Path $frontend "node_modules\vite\bin\vite.js"
    if ($node -and (Test-Path $viteJs)) {
        Start-Process -FilePath $node -ArgumentList "`"$viteJs`"" `
            -WorkingDirectory $frontend `
            -WindowStyle Hidden
    } else {
        $npm = Resolve-Cmd "npm.cmd" @("$env:ProgramFiles\nodejs\npm.cmd",
                                       "${env:ProgramFiles(x86)}\nodejs\npm.cmd",
                                       "$env:APPDATA\npm\npm.cmd")
        if (-not $npm) {
            Show-Error "Node.js / npm was not found.`nInstall Node.js (https://nodejs.org) and reboot, then try again."
            exit 1
        }
        Start-Process -FilePath "cmd.exe" `
            -ArgumentList "/c", "`"$npm`" run dev" `
            -WorkingDirectory $frontend `
            -WindowStyle Hidden
    }
}

# ── Wait for both ports (concurrently), then open browser ───────────────────
Wait-ForPorts @(8000, 5173) | Out-Null
Start-Process "http://localhost:5173"
