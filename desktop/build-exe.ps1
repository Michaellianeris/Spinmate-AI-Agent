# Builds dist\SpinMate\SpinMate.exe and SpinMate-windows.zip on Windows. From the repo folder:
#   powershell -ExecutionPolicy Bypass -File desktop\build-exe.ps1
$ErrorActionPreference = "Continue"   # native tools write to stderr; failures are checked by exit code
Set-Location (Split-Path $PSScriptRoot -Parent)

function Test-Py312 { & py -3.12 --version *> $null; return $LASTEXITCODE -eq 0 }

# Python 3.12 for this user if missing: the Python install manager first, then winget
if (-not (Test-Py312)) {
    Write-Host "Installing Python 3.12..."
    & py install 3.12
    if (-not (Test-Py312)) {
        winget install -e --id Python.Python.3.12 --scope user --accept-package-agreements --accept-source-agreements
        $env:Path = "$env:LOCALAPPDATA\Programs\Python\Launcher;$env:Path"
    }
    if (-not (Test-Py312)) { throw "Python 3.12 is not available; install it from python.org and run this again" }
}

# separate build environment, so nothing else on the PC changes
if (-not (Test-Path .venv-build)) { py -3.12 -m venv .venv-build }
.\.venv-build\Scripts\python -m pip install --upgrade pip
.\.venv-build\Scripts\pip install -r requirements.txt pyinstaller==6.21.0
if ($LASTEXITCODE -ne 0) { throw "pip install failed" }

.\.venv-build\Scripts\pyinstaller desktop\spinmate.spec --noconfirm
if ($LASTEXITCODE -ne 0) { throw "PyInstaller failed" }

# one real run and a catalog query with the built exe
.\dist\SpinMate\SpinMate.exe --selftest
if ($LASTEXITCODE -ne 0) { throw "selftest failed" }

Compress-Archive -Path dist\SpinMate -DestinationPath SpinMate-windows.zip -Force
Write-Host ""
Write-Host "Done. Start it with: dist\SpinMate\SpinMate.exe"
Write-Host "To give it to someone: SpinMate-windows.zip"
