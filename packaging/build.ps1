# Builds dist\Cuelog (the app folder), dist\Cuelog-<version>-portable.zip and dist\Cuelog-Setup-<version>.exe.
#   powershell -ExecutionPolicy Bypass -File packaging\build.ps1
$ErrorActionPreference = "Stop"
$root = Split-Path $PSScriptRoot -Parent
Set-Location $root
$py = Join-Path $root ".venv\Scripts\python.exe"
$version = & $py -c "import cuelog; print(cuelog.__version__)"
Write-Host "Building Cuelog $version"

& $py -m pip install -q pyinstaller
& $py packaging\make_icon.py
& $py packaging\version_info.py $version
& $py -m PyInstaller packaging\cuelog.spec --noconfirm --clean --distpath dist --workpath build
if ($LASTEXITCODE) { throw "PyInstaller failed" }

$zip = "dist\Cuelog-$version-portable.zip"
if (Test-Path $zip) { Remove-Item $zip }
Compress-Archive -Path dist\Cuelog -DestinationPath $zip

$iscc = @("$env:LOCALAPPDATA\Programs\Inno Setup 6\ISCC.exe", "${env:ProgramFiles(x86)}\Inno Setup 6\ISCC.exe") |
    Where-Object { Test-Path $_ } | Select-Object -First 1
if (-not $iscc) { throw "Inno Setup 6 not found (winget install JRSoftware.InnoSetup)" }
& $iscc /Q "/DAppVersion=$version" packaging\installer.iss
if ($LASTEXITCODE) { throw "Inno Setup failed" }
Get-ChildItem dist -File | Format-Table Name, @{n="MB"; e={[math]::Round($_.Length / 1MB, 1)}}
