param(
    [switch]$BackendOnly
)

$ErrorActionPreference = "Stop"

$ScriptDir = Split-Path -Parent $MyInvocation.MyCommand.Path
$RootDir = Split-Path -Parent $ScriptDir
Set-Location $RootDir

$EnvFile = Join-Path $RootDir ".env"
$env:PYTHONPATH = "$RootDir\packages\shared;$RootDir\packages\core;$RootDir\packages\desktop;$RootDir"
if (-not $env:JARVIS_WEB_URL) { $env:JARVIS_WEB_URL = "http://localhost:8765" }
if (-not $env:JARVIS_WINDOW_SIZE) { $env:JARVIS_WINDOW_SIZE = "1307,824" }

# --- Jeton d'acces : repris de .env, sinon genere et persiste (meme logique que le .sh) ---
$Token = $env:JARVIS_API_TOKEN
if (-not $Token -and (Test-Path $EnvFile)) {
    $line = Select-String -Path $EnvFile -Pattern '^JARVIS_API_TOKEN=' | Select-Object -First 1
    if ($line) {
        $Token = ($line.Line -replace '^JARVIS_API_TOKEN=', '').Trim().Trim('"').Trim("'")
    }
}
if ($Token -in @('', 'changez_ce_jeton_secret', 'change-me-local-token')) { $Token = $null }
if (-not $Token) {
    $bytes = New-Object byte[] 32
    [System.Security.Cryptography.RandomNumberGenerator]::Create().GetBytes($bytes)
    $Token = [Convert]::ToBase64String($bytes).Replace('+', '').Replace('/', '').Replace('=', '')
    if (-not (Test-Path $EnvFile)) { New-Item -ItemType File -Path $EnvFile -Force | Out-Null }
    if (Select-String -Path $EnvFile -Pattern '^JARVIS_API_TOKEN=' -Quiet) {
        (Get-Content $EnvFile) -replace '^JARVIS_API_TOKEN=.*', "JARVIS_API_TOKEN=$Token" |
            Set-Content $EnvFile -Encoding UTF8
    } else {
        Add-Content -Path $EnvFile -Value "`n# Jeton genere automatiquement par launch_jarvis_v2.ps1`nJARVIS_API_TOKEN=$Token" -Encoding UTF8
    }
    Write-Host "Jeton d'acces genere et enregistre dans .env (JARVIS_API_TOKEN)." -ForegroundColor Yellow
}
$env:JARVIS_API_TOKEN = $Token

$PythonBin = Join-Path $RootDir ".venv\Scripts\python.exe"
if (-not (Test-Path $PythonBin)) {
    throw "Environnement virtuel introuvable ($PythonBin). Lancez d'abord install_windows.ps1."
}

function Test-JarvisHealth {
    try {
        $resp = Invoke-WebRequest -Uri "$($env:JARVIS_WEB_URL)/api/health" -UseBasicParsing -TimeoutSec 2
        return $resp.StatusCode -eq 200
    } catch {
        return $false
    }
}

if (-not (Test-JarvisHealth)) {
    $LogOut = Join-Path $env:TEMP "jarvis-core.log"
    $LogErr = Join-Path $env:TEMP "jarvis-core.err.log"
    Start-Process -FilePath $PythonBin `
        -ArgumentList "-m", "uvicorn", "jarvis_core.api.server:app", "--host", "127.0.0.1", "--port", "8765" `
        -WorkingDirectory $RootDir `
        -WindowStyle Hidden `
        -RedirectStandardOutput $LogOut `
        -RedirectStandardError $LogErr

    for ($i = 0; $i -lt 60; $i++) {
        if (Test-JarvisHealth) { break }
        Start-Sleep -Milliseconds 250
    }
}

if (-not (Test-JarvisHealth)) {
    throw "jarvis-core ne repond pas sur $($env:JARVIS_WEB_URL) (voir $env:TEMP\jarvis-core.err.log)."
}

$AppUrl = "$($env:JARVIS_WEB_URL)/?token=$Token"

if ($BackendOnly) {
    Write-Host "jarvis-core actif sur $($env:JARVIS_WEB_URL)"
    Write-Host "Interface : $AppUrl"
    exit 0
}

$BrowserPaths = @(
    "$env:ProgramFiles\Google\Chrome\Application\chrome.exe",
    "${env:ProgramFiles(x86)}\Google\Chrome\Application\chrome.exe",
    "$env:ProgramFiles\Microsoft\Edge\Application\msedge.exe",
    "${env:ProgramFiles(x86)}\Microsoft\Edge\Application\msedge.exe"
)
$Browser = $BrowserPaths | Where-Object { Test-Path $_ } | Select-Object -First 1

if ($Browser) {
    Start-Process -FilePath $Browser -ArgumentList "--app=$AppUrl", "--window-size=$($env:JARVIS_WINDOW_SIZE)"
} else {
    Start-Process $AppUrl
}
