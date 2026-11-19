# =======================================================
#   JARVIS 2.0.0  Automated Windows Installer (PowerShell)
#   Usage: powershell -ExecutionPolicy Bypass -File install_windows.ps1
# =======================================================

param(
    [string]$GeminiApiKey = $env:GEMINI_API_KEY
)

$ErrorActionPreference = "Stop"

$RootDir = Split-Path -Parent $MyInvocation.MyCommand.Path
$VenvDir = Join-Path $RootDir ".venv"
$EnvFile = Join-Path $RootDir ".env"
$EnvExample = Join-Path $RootDir ".env.example"
$Launcher = Join-Path $RootDir "scripts\launch_jarvis_v2.ps1"
$IconPath = Join-Path $RootDir "jarvis_icon.ico"

Write-Host "`n===================================================" -ForegroundColor Cyan
Write-Host "   JARVIS 2.0.0 - AUTOMATED WINDOWS INSTALLER" -ForegroundColor White
Write-Host "===================================================`n" -ForegroundColor Cyan

# 1/4 Configuration (.env)
Write-Host "[1/4] Configuration (.env)..." -ForegroundColor Cyan
if (-not (Test-Path $EnvFile) -and (Test-Path $EnvExample)) {
    Copy-Item $EnvExample $EnvFile
}
if ($GeminiApiKey) {
    if ((Test-Path $EnvFile) -and (Select-String -Path $EnvFile -Pattern "^GEMINI_API_KEY=" -Quiet)) {
        (Get-Content $EnvFile) -replace "^GEMINI_API_KEY=.*", "GEMINI_API_KEY=$GeminiApiKey" | Set-Content $EnvFile -Encoding UTF8
    } else {
        Add-Content -Path $EnvFile -Value "GEMINI_API_KEY=$GeminiApiKey" -Encoding UTF8
    }
    Write-Host "  OK: GEMINI_API_KEY enregistree dans .env" -ForegroundColor Green
} elseif (-not (Test-Path $EnvFile)) {
    New-Item -ItemType File -Path $EnvFile -Force | Out-Null
    Write-Host "  INFO: .env cree (pensez a y ajouter vos cles API)" -ForegroundColor Yellow
} else {
    Write-Host "  OK: .env existant conserve" -ForegroundColor Green
}

# 2/4 Environnement Python
Write-Host "`n[2/4] Environnement Python JARVIS 2.0..." -ForegroundColor Cyan
Set-Location $RootDir
if (Get-Command uv -ErrorAction SilentlyContinue) {
    uv sync
    if ($LASTEXITCODE -ne 0) { uv sync --python 3.12 }
    Write-Host "  OK: Workspace synchronise via uv" -ForegroundColor Green
} else {
    if (-not (Get-Command python -ErrorAction SilentlyContinue)) {
        throw "Python 3.12+ ou uv est requis. Installez Python depuis python.org ou winget install astral-sh.uv."
    }
    if (-not (Test-Path $VenvDir)) {
        python -m venv $VenvDir
    }
    $PipBin = Join-Path $VenvDir "Scripts\pip.exe"
    & $PipBin install --upgrade pip -q
    & $PipBin install -e packages\shared -e packages\core -e packages\desktop -q
    Write-Host "  OK: Packages JARVIS 2.0 installes dans .venv" -ForegroundColor Green
}

# 3/4 Compilation du front-end React
Write-Host "`n[3/4] Compilation de l'interface Web 2.0..." -ForegroundColor Cyan
if (-not (Get-Command npm -ErrorAction SilentlyContinue)) {
    throw "Node.js / npm est requis pour compiler web/. Installez Node.js LTS (winget install OpenJS.NodeJS.LTS)."
}
Push-Location (Join-Path $RootDir "web")
try {
    npm ci --no-audit --no-fund
    npm run build
} finally {
    Pop-Location
}
Write-Host "  OK: Frontend compile dans web\dist" -ForegroundColor Green

# 4/4 Raccourci Bureau Windows
Write-Host "`n[4/4] Creation du raccourci Bureau..." -ForegroundColor Cyan
$DesktopDir = [Environment]::GetFolderPath("Desktop")
$ShortcutPath = Join-Path $DesktopDir "JARVIS.lnk"
$WshShell = New-Object -ComObject WScript.Shell
$Shortcut = $WshShell.CreateShortcut($ShortcutPath)
$Shortcut.TargetPath = "powershell.exe"
$Shortcut.Arguments = "-ExecutionPolicy Bypass -WindowStyle Hidden -File `"$Launcher`""
$Shortcut.WorkingDirectory = $RootDir
if (Test-Path $IconPath) {
    $Shortcut.IconLocation = $IconPath
}
$Shortcut.Description = "Interface web JARVIS 2.0"
$Shortcut.Save()
Write-Host "  OK: Raccourci cree sur le Bureau ($ShortcutPath)" -ForegroundColor Green

Write-Host "`nInstallation JARVIS 2.0.0 terminee !" -ForegroundColor Green
Write-Host "Lancer maintenant : powershell -ExecutionPolicy Bypass -File `"$Launcher`"`n" -ForegroundColor Cyan
