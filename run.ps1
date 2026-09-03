<#
.SYNOPSIS
  Start the metro-map designer on Windows.

.DESCRIPTION
  Creates .venv on first run, stops any designer already holding the port, then
  starts a fresh one and opens the browser.

.EXAMPLE
  .\run.ps1
  .\run.ps1 -Port 9000
  .\run.ps1 -Stop            # just shut the running one down
#>
[CmdletBinding()]
param(
    [int]$Port = 8765,
    [string]$Bind = "127.0.0.1",
    [switch]$Stop,           # stop whatever is running and exit
    [switch]$NoBrowser
)

$ErrorActionPreference = "Stop"
Set-Location -Path $PSScriptRoot

function Get-DesignerPids {
    <# Whatever owns the port, plus any stray app.py from an earlier run. #>
    $found = @()
    try {
        $found += (Get-NetTCPConnection -LocalPort $Port -State Listen -ErrorAction Stop).OwningProcess
    } catch {
        # Get-NetTCPConnection is missing on older hosts; fall back to netstat
        $found += (netstat -ano | Select-String ":$Port\s+.*LISTENING" |
                   ForEach-Object { ($_ -split '\s+')[-1] })
    }
    # match our own app.py only — never someone else's project on this machine
    $mine = "metro_map_tool.app"
    $found += Get-CimInstance Win32_Process -Filter "Name='python.exe' OR Name='pythonw.exe'" |
              Where-Object { $_.CommandLine -match $mine -and
                             $_.CommandLine -match [regex]::Escape($PSScriptRoot) } |
              ForEach-Object { $_.ProcessId }
    $found | Where-Object { $_ } | Sort-Object -Unique
}

function Stop-Designer {
    $pids = Get-DesignerPids
    if (-not $pids) { Write-Host "  nothing running on port $Port"; return }
    foreach ($processId in $pids) {
        # ask the server to close itself first, so it can finish a write in flight
        try {
            Invoke-RestMethod -Method Post -TimeoutSec 2 `
                -Uri "http://127.0.0.1:$Port/api/shutdown" | Out-Null
        } catch { }
        Start-Sleep -Milliseconds 400
        if (Get-Process -Id $processId -ErrorAction SilentlyContinue) {
            Write-Host "  killing pid $processId"
            Stop-Process -Id $processId -Force -ErrorAction SilentlyContinue
        } else {
            Write-Host "  stopped pid $processId"
        }
    }
    Start-Sleep -Milliseconds 300
}

# ---------------------------------------------------------------- stop only --
if ($Stop) { Stop-Designer; exit 0 }

# ------------------------------------------------------------------- python --
$py = Join-Path $PSScriptRoot ".venv\Scripts\python.exe"
if (-not (Test-Path $py)) {
    # Windows PowerShell 5.1 has no ?: ternary, so keep this an if/else
    if (Get-Command py -ErrorAction SilentlyContinue)          { $bootstrap = "py" }
    elseif (Get-Command python -ErrorAction SilentlyContinue)  { $bootstrap = "python" }
    else { throw "no Python found on PATH — install Python 3.10+ from python.org" }
    Write-Host "  creating .venv ..."
    & $bootstrap -m venv .venv
    if ($LASTEXITCODE -ne 0) { throw "could not create .venv — is Python 3.10+ installed?" }
    & $py -m pip install --quiet --upgrade pip
    & $py -m pip install --quiet -r requirements.txt
}

# ------------------------------------------------------------------ restart --
Stop-Designer
Write-Host "  starting designer on http://${Bind}:$Port"
$proc = Start-Process -FilePath $py `
    -ArgumentList @("-m", "metro_map_tool.app", "--host", $Bind, "--port", $Port) `
    -WorkingDirectory $PSScriptRoot -PassThru

# wait for it to answer before opening a tab at a dead port
$ready = $false
foreach ($attempt in 1..40) {
    Start-Sleep -Milliseconds 250
    try {
        Invoke-WebRequest -Uri "http://127.0.0.1:$Port/api/maps" -TimeoutSec 2 -UseBasicParsing | Out-Null
        $ready = $true
        break
    } catch { }
}

if (-not $ready) {
    throw "the designer did not come up on port $Port (pid $($proc.Id))"
}

Write-Host "  ready — pid $($proc.Id)"
Write-Host "  stop it with the red Stop button, or:  .\run.ps1 -Stop"
if (-not $NoBrowser) { Start-Process "http://127.0.0.1:$Port/" }
