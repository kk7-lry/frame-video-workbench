param([switch]$NoBrowser, [switch]$Restart)
$ErrorActionPreference = 'Stop'
$appRoot = $PSScriptRoot
$expectedVersion = '0.3.0'
$preferred = @(
    (Join-Path $appRoot '.venv\Scripts\python.exe'),
    (Join-Path $env:USERPROFILE '.agent-reach-venv\Scripts\python.exe'),
    (Join-Path $env:USERPROFILE '.cache\codex-runtimes\codex-primary-runtime\dependencies\python\python.exe')
)
$pythonPath = $preferred | Where-Object { Test-Path -LiteralPath $_ } | Select-Object -First 1
if (-not $pythonPath) {
    $pythonCommand = Get-Command python.exe -ErrorAction SilentlyContinue
    if ($pythonCommand -and $pythonCommand.Source -notlike '*WindowsApps*') { $pythonPath = $pythonCommand.Source }
}
if (-not $pythonPath) { throw 'Python 3.10+ is required. Install Python, then run this launcher again.' }
$selectedPort = $null
$existing = $null
foreach ($candidate in 4175..4190) {
    try {
        $probe = [Net.Sockets.TcpClient]::new()
        try { $probe.Connect('127.0.0.1', $candidate); $occupied = $true } catch { $occupied = $false }
        finally { $probe.Dispose() }
        if (-not $occupied) {
            if (-not $selectedPort) { $selectedPort = $candidate }
            continue
        }
        try {
            $health = Invoke-RestMethod -Uri "http://127.0.0.1:$candidate/api/health" -TimeoutSec 2
            if ($health.app -eq 'frame-workbench' -and $health.workspace -eq $appRoot) {
                $existing = [pscustomobject]@{Port=$candidate; Health=$health}
                break
            }
        } catch {}
    } catch {}
}
$needsRestart = $existing -and ($Restart -or $existing.Health.version -ne $expectedVersion -or $existing.Health.network.state -eq 'access_denied')
if (-not $existing -or $needsRestart) {
    try {
        $preflightRaw = & $pythonPath -B (Join-Path $appRoot 'server.py') --preflight
        $preflightExit = $LASTEXITCODE
        $preflight = ($preflightRaw -join "`n") | ConvertFrom-Json
    } catch { throw 'Candidate startup checks could not complete. Any existing service was left running.' }
    if ($preflightExit -ne 0 -or -not $preflight.ok -or $preflight.version -ne $expectedVersion) {
        throw ('Candidate startup checks failed. Any existing service was left running. ' + $preflight.error)
    }
    if ($existing -and $preflight.network.state -in @('access_denied','unreachable')) {
        throw ('Candidate network check failed (' + $preflight.network.code + '). The existing service was left running.')
    }
}
if ($existing) {
    $selectedPort = $existing.Port
    if ($needsRestart) {
        $body = @{workspace=$appRoot} | ConvertTo-Json -Compress
        try {
            Invoke-RestMethod -Uri "http://127.0.0.1:$selectedPort/api/shutdown" -Method Post -ContentType 'application/json; charset=utf-8' -Body ([Text.Encoding]::UTF8.GetBytes($body)) -Headers @{'X-Frame-Launcher'='restart'} -TimeoutSec 5 | Out-Null
        } catch { throw 'The running service could not stop safely. Finish active tasks or close the old service, then retry.' }
        $stopped = $false
        for($attempt=0; $attempt -lt 40; $attempt++) {
            $probe=[Net.Sockets.TcpClient]::new()
            try { $probe.Connect('127.0.0.1',$selectedPort) } catch { $stopped=$true }
            finally { $probe.Dispose() }
            if($stopped) { break }
            Start-Sleep -Milliseconds 250
        }
        if(-not $stopped) { throw 'The service is still shutting down. Please try again in a moment.' }
    } else {
        if (-not $NoBrowser) { Start-Process "http://127.0.0.1:$selectedPort" }
        Write-Output "Frame is ready: http://127.0.0.1:$selectedPort"
        exit 0
    }
}
if (-not $selectedPort) { throw 'Ports 4175-4190 are occupied.' }
$env:CLIP_PORT = [string]$selectedPort
$dataRoot = Join-Path $appRoot 'data'
New-Item -ItemType Directory -Path $dataRoot -Force | Out-Null
$windowless = Join-Path (Split-Path $pythonPath) 'pythonw.exe'
if (Test-Path -LiteralPath $windowless) { $pythonPath = $windowless }
$argsList = @('-u', ('"' + (Join-Path $appRoot 'server.py') + '"'), '--background')
$serverProcess = Start-Process -FilePath $pythonPath -ArgumentList $argsList -WorkingDirectory $appRoot -WindowStyle Hidden -PassThru
for ($attempt=0; $attempt -lt 30; $attempt++) {
    try {
        $health=Invoke-RestMethod -Uri "http://127.0.0.1:$selectedPort/api/health" -TimeoutSec 2
        if ($health.app -eq 'frame-workbench' -and $health.workspace -eq $appRoot -and $health.version -eq $expectedVersion) {
            if (-not $NoBrowser) { Start-Process "http://127.0.0.1:$selectedPort" }
            Write-Output "Frame is ready: http://127.0.0.1:$selectedPort"
            exit 0
        }
    } catch {}
    Start-Sleep -Milliseconds 300
}
throw ('Server did not start. Check ' + (Join-Path $dataRoot 'server-error.log'))
