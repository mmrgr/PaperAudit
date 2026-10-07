param(
    [ValidateSet('onedir', 'onefile')]
    [string]$Mode = 'onedir',
    [string]$PythonPath = ''
)

$ErrorActionPreference = 'Stop'
$Project = Split-Path -Parent $MyInvocation.MyCommand.Path
$Python = if ($PythonPath) { $PythonPath } elseif ($env:PAPERREVAMPER_PYTHON) { $env:PAPERREVAMPER_PYTHON } elseif ($env:PAPERAUDIT_PYTHON) { $env:PAPERAUDIT_PYTHON } else { (Get-Command python -ErrorAction Stop).Source }
if (-not (Test-Path -LiteralPath $Python)) { throw "Python interpreter not found: $Python" }
$PythonVersion = (& $Python -c "import sys; print(f'{sys.version_info.major}.{sys.version_info.minor}')").Trim()
if ($PythonVersion -notin @('3.11', '3.12', '3.13')) { throw "Unsupported Python $PythonVersion; use CPython 3.11, 3.12, or 3.13" }
$PythonScripts = (& $Python -c "import sysconfig; print(sysconfig.get_path('scripts'))").Trim()
$PyInstaller = Join-Path $PythonScripts 'pyinstaller.exe'
$PyInstaller = [IO.Path]::GetFullPath($PyInstaller)
$BuildRequirements = Join-Path $Project 'requirements-build.txt'
$Dist = Join-Path $Project 'dist'
$Work = Join-Path $Project 'build\pyinstaller'

if (Test-Path $BuildRequirements) {
    & $Python -m pip install -r $BuildRequirements
    if ($LASTEXITCODE -ne 0) { throw "Build dependency installation failed with exit code $LASTEXITCODE" }
} elseif (-not (Test-Path $PyInstaller)) {
    & $Python -m pip install 'pyinstaller==6.22.0'
    if ($LASTEXITCODE -ne 0) { throw "PyInstaller installation failed with exit code $LASTEXITCODE" }
}
$PyInstaller = [IO.Path]::GetFullPath($PyInstaller)
if (-not (Test-Path $PyInstaller)) { throw "PyInstaller executable not found: $PyInstaller" }

# Install runtime dependencies into the exact interpreter selected above;
# otherwise PyInstaller can produce an EXE that builds successfully but fails
# immediately with ``No module named docx``.
if (Test-Path $BuildRequirements) {
    & $Python -m pip install -e "$Project" --no-deps
} else {
    & $Python -m pip install -e "$Project[pdf]"
}
if ($LASTEXITCODE -ne 0) { throw "PaperRevamper dependency installation failed with exit code $LASTEXITCODE" }

Copy-Item (Join-Path $Project 'ui\control-panel.html') (Join-Path $Project 'src\paperrevamper\static\control-panel.html') -Force
Remove-Item (Join-Path $Project 'build\pyinstaller') -Recurse -Force -ErrorAction SilentlyContinue
Remove-Item (Join-Path $Dist 'PaperRevamper') -Recurse -Force -ErrorAction SilentlyContinue

$args = @(
    '--noconfirm', '--clean', '--name', 'PaperRevamper', '--paths', (Join-Path $Project 'src'),
    '--add-data', "$(Join-Path $Project 'checklists');checklists",
    '--add-data', "$(Join-Path $Project 'venues');venues",
    '--add-data', "$(Join-Path $Project 'src\paperrevamper\static');paperrevamper/static",
    '--collect-submodules', 'paperrevamper',
    '--collect-all', 'docx', '--collect-all', 'lxml', '--collect-all', 'yaml', '--collect-all', 'pdfplumber',
    '--distpath', $Dist, '--workpath', $Work, '--specpath', $Work
)
if ($Mode -eq 'onefile') { $args += '--onefile' } else { $args += '--onedir' }
$args += (Join-Path $Project 'src\paperrevamper\desktop.py')

Push-Location $Project
try {
    & $PyInstaller @args
    if ($LASTEXITCODE -ne 0) { throw "PyInstaller failed with exit code $LASTEXITCODE" }
} finally {
    Pop-Location
}

$exe = if ($Mode -eq 'onefile') { Join-Path $Dist 'PaperRevamper.exe' } else { Join-Path $Dist 'PaperRevamper\PaperRevamper.exe' }

# PyInstaller 6.22 can leave base_library.zip in the work directory when
# building an onedir app with the current bundled Python.  The bootloader
# needs this archive beside the collected binaries, otherwise the directory
# build fails before importing the encodings package.
if ($Mode -eq 'onedir') {
    $baseLibrary = Join-Path $Work 'PaperRevamper\base_library.zip'
    $internalDir = Join-Path $Dist 'PaperRevamper\_internal'
    if ((Test-Path $baseLibrary) -and (Test-Path $internalDir)) {
        Copy-Item $baseLibrary (Join-Path $internalDir 'base_library.zip') -Force
    }
}

Write-Host "Built: $exe"
