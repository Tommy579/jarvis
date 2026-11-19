# =======================================================
#   JARVIS 2.0  Construction du binaire Windows (PowerShell)
#   A executer SUR WINDOWS : PyInstaller ne produit pas d'exe depuis Linux.
#   Usage: powershell -ExecutionPolicy Bypass -File scripts\build_windows.ps1
#   Options: -SkipInstall (deps deja installees), -SkipFrontend, -NoZip
# =======================================================

param(
    [switch]$SkipInstall,
    [switch]$SkipFrontend,
    [switch]$NoZip
)

$ErrorActionPreference = "Stop"

$ScriptDir = Split-Path -Parent $MyInvocation.MyCommand.Path
$RootDir = Split-Path -Parent $ScriptDir
Set-Location $RootDir

$DistDir = Join-Path $RootDir "dist\JARVIS"
$ZipPath = Join-Path $RootDir "dist\JARVIS-windows-x64.zip"
$PythonBin = Join-Path $RootDir ".venv\Scripts\python.exe"
if (-not (Test-Path $PythonBin)) {
    # Pas de venv local (CI, ou Python systeme deja provisionne) : on utilise le python du PATH.
    $systemPython = Get-Command python -ErrorAction SilentlyContinue
    if (-not $systemPython) {
        throw "Python introuvable. Lancez d'abord: powershell -ExecutionPolicy Bypass -File install_windows.ps1"
    }
    $PythonBin = $systemPython.Source
    Write-Host "  INFO: pas de .venv, utilisation de $PythonBin" -ForegroundColor Yellow
}

Write-Host "`n=== JARVIS 2.0  build Windows ===" -ForegroundColor Cyan

# 1/5 Environnement
if (-not (Test-Path $PythonBin) -and -not $SkipInstall) {
    throw "Python introuvable. Lancez d'abord: powershell -ExecutionPolicy Bypass -File install_windows.ps1"
}

if (-not $SkipInstall) {
    Write-Host "[1/5] Dependances Python + PyInstaller..." -ForegroundColor Cyan
    & $PythonBin -m pip install --upgrade pip -q
    & $PythonBin -m pip install -e packages\shared -e packages\core -e packages\desktop pyinstaller -q
}
else {
    Write-Host "[1/5] Dependances: ignorees (-SkipInstall)" -ForegroundColor DarkGray
}

# 2/5 Frontend
if (-not $SkipFrontend) {
    Write-Host "[2/5] Compilation du frontend (web/dist)..." -ForegroundColor Cyan
    if (-not (Get-Command npm -ErrorAction SilentlyContinue)) {
        throw "npm est requis. Installez Node.js LTS: winget install OpenJS.NodeJS.LTS"
    }
    Push-Location (Join-Path $RootDir "web")
    try {
        npm ci --no-audit --no-fund
        npm run build
    }
    finally { Pop-Location }
}
else {
    Write-Host "[2/5] Frontend: ignore (-SkipFrontend)" -ForegroundColor DarkGray
}

if (-not (Test-Path (Join-Path $RootDir "web\dist\index.html"))) {
    throw "web\dist\index.html absent : compilez le frontend avant d'empaqueter."
}

# 3/5 Empaquetage PyInstaller
Write-Host "[3/5] Empaquetage PyInstaller..." -ForegroundColor Cyan
& $PythonBin -m PyInstaller packaging\jarvis_core.spec --noconfirm --clean
if ($LASTEXITCODE -ne 0) { throw "PyInstaller a echoue." }

# 4/5 Assemblage du livrable (config, interface, modele .env, icones)
Write-Host "[4/5] Assemblage du dossier JARVIS..." -ForegroundColor Cyan
Copy-Item (Join-Path $RootDir "config") -Destination $DistDir -Recurse -Force
New-Item -ItemType Directory -Path (Join-Path $DistDir "web") -Force | Out-Null
Copy-Item (Join-Path $RootDir "web\dist") -Destination (Join-Path $DistDir "web\dist") -Recurse -Force
Copy-Item (Join-Path $RootDir ".env.example") -Destination (Join-Path $DistDir ".env.example") -Force
foreach ($asset in @("jarvis_icon.ico", "jarvis_icon.png", "README.md")) {
    $path = Join-Path $RootDir $asset
    if (Test-Path $path) { Copy-Item $path -Destination $DistDir -Force }
}

# Lanceur alternatif (console visible, utile pour diagnostiquer)
$launcherCmd = @"
@echo off
cd /d "%~dp0"
jarvis.exe
pause
"@
Set-Content -Path (Join-Path $DistDir "JARVIS-debug.cmd") -Value $launcherCmd -Encoding ASCII

# 5/5 Archive
if (-not $NoZip) {
    Write-Host "[5/5] Archive ZIP..." -ForegroundColor Cyan
    if (Test-Path $ZipPath) { Remove-Item $ZipPath -Force }
    Compress-Archive -Path $DistDir -DestinationPath $ZipPath -CompressionLevel Optimal
    Write-Host "  OK: $ZipPath" -ForegroundColor Green
}
else {
    Write-Host "[5/5] Archive: ignoree (-NoZip)" -ForegroundColor DarkGray
}

Write-Host "`nBuild termine." -ForegroundColor Green
Write-Host "  Executable : $(Join-Path $DistDir 'jarvis.exe')"
Write-Host "  A tester   : double-clic sur JARVIS-debug.cmd pour voir les journaux.`n"
