$ErrorActionPreference = "Stop"

# Crea el entorno virtual si no existe
if (-not (Test-Path ".venv")) {
    Write-Host "Creando entorno virtual..."
    py -m venv .venv
    if (-not $?) { exit 1 }
}

& ".venv\Scripts\python.exe" -m pip install --upgrade pip
if (-not $?) { exit 1 }

& ".venv\Scripts\python.exe" -m pip install pyinstaller
if (-not $?) { exit 1 }

Write-Host "Empaquetando el ejecutable..."
if (Test-Path "dist\InstaladorMods") { Remove-Item "dist\InstaladorMods" -Recurse -Force }
if (Test-Path "build\InstaladorMods") { Remove-Item "build\InstaladorMods" -Recurse -Force }

& ".venv\Scripts\pyinstaller.exe" --onedir --noconsole --clean `
    --name InstaladorMods `
    --icon app.ico `
    --version-file version_info.txt `
    app.py
if (-not $?) { exit 1 }

Write-Host "Listo: dist\InstaladorMods\InstaladorMods.exe"