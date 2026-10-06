param(
    [ValidateSet('onedir', 'onefile')]
    [string]$Mode = 'onedir'
)

$ErrorActionPreference = 'Stop'
$Project = Split-Path -Parent $MyInvocation.MyCommand.Path
$Python = 'C:\Users\mmrgr\.workbuddy\binaries\python\versions\3.13.12\python.exe'
$PyInstaller = Join-Path (Split-Path $Python) 'Scripts\pyinstaller.exe'
$PyInstaller = [IO.Path]::GetFullPath($PyInstaller)
$Dist = Join-Path $Project 'dist'
$Work = Join-Path $Project 'build\pyinstaller'

if (-not (Test-Path $PyInstaller)) {
    & $Python -m pip install pyinstaller
    if ($LASTEXITCODE -ne 0) { throw "PyInstaller installation failed with exit code $LASTEXITCODE" }
}

# The bundled WorkBuddy Python is intentionally separate from the interpreter
# used for development.  Install the runtime dependencies into that exact
# interpreter before freezing; otherwise PyInstaller can produce an EXE that
# builds successfully but fails immediately with ``No module named docx``.
& $Python -m pip install -e "$Project[pdf]"
if ($LASTEXITCODE -ne 0) { throw "PaperAudit dependency installation failed with exit code $LASTEXITCODE" }

Copy-Item (Join-Path $Project 'ui\control-panel.html') (Join-Path $Project 'src\paperaudit\static\control-panel.html') -Force
Remove-Item (Join-Path $Project 'build\pyinstaller') -Recurse -Force -ErrorAction SilentlyContinue
Remove-Item (Join-Path $Dist 'PaperAudit') -Recurse -Force -ErrorAction SilentlyContinue

$args = @(
    '--noconfirm', '--clean', '--name', 'PaperAudit', '--paths', (Join-Path $Project 'src'),
    '--add-data', "$(Join-Path $Project 'checklists');checklists",
    '--add-data', "$(Join-Path $Project 'venues');venues",
    '--add-data', "$(Join-Path $Project 'src\paperaudit\static');paperaudit/static",
    '--collect-submodules', 'paperaudit',
    '--collect-all', 'docx', '--collect-all', 'lxml', '--collect-all', 'yaml', '--collect-all', 'pdfplumber',
    '--distpath', $Dist, '--workpath', $Work, '--specpath', $Work
)
if ($Mode -eq 'onefile') { $args += '--onefile' } else { $args += '--onedir' }
$args += (Join-Path $Project 'src\paperaudit\desktop.py')

Push-Location $Project
try {
    & $PyInstaller @args
    if ($LASTEXITCODE -ne 0) { throw "PyInstaller failed with exit code $LASTEXITCODE" }
} finally {
    Pop-Location
}

$exe = if ($Mode -eq 'onefile') { Join-Path $Dist 'PaperAudit.exe' } else { Join-Path $Dist 'PaperAudit\PaperAudit.exe' }

# PyInstaller 6.22 can leave base_library.zip in the work directory when
# building an onedir app with the current bundled Python.  The bootloader
# needs this archive beside the collected binaries, otherwise the directory
# build fails before importing the encodings package.
if ($Mode -eq 'onedir') {
    $baseLibrary = Join-Path $Work 'PaperAudit\base_library.zip'
    $internalDir = Join-Path $Dist 'PaperAudit\_internal'
    if ((Test-Path $baseLibrary) -and (Test-Path $internalDir)) {
        Copy-Item $baseLibrary (Join-Path $internalDir 'base_library.zip') -Force
    }
}

Write-Host "Built: $exe"
