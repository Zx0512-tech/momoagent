param(
    [switch]$SkipFrontendBuild
)

$ErrorActionPreference = 'Stop'
$root = Split-Path -Parent $MyInvocation.MyCommand.Path
$venvPython = Join-Path $root '.venv\Scripts\python.exe'

function Write-Step {
    param([string]$Message)
    Write-Host "[MOMO] $Message" -ForegroundColor Cyan
}

function Assert-ExitCode {
    param([string]$Step)
    if ($LASTEXITCODE -ne 0) {
        throw "$Step failed with exit code $LASTEXITCODE."
    }
}

if (-not (Test-Path $venvPython)) {
    Write-Step 'Creating Python 3.13 virtual environment...'
    if (Get-Command py -ErrorAction SilentlyContinue) {
        & py -3.13 -m venv (Join-Path $root '.venv')
        Assert-ExitCode 'Creating the Python virtual environment'
    }
    elseif (Get-Command python -ErrorAction SilentlyContinue) {
        $version = & python -c "import sys; print(f'{sys.version_info.major}.{sys.version_info.minor}')"
        if ($version -ne '3.13') {
            throw "The bundled USER300 OpenSeesPy runtime requires Python 3.13 x64; found $version."
        }
        & python -m venv (Join-Path $root '.venv')
        Assert-ExitCode 'Creating the Python virtual environment'
    }
    else {
        throw 'Python was not found. Install Python 3.13 x64 first.'
    }
}

Write-Step 'Installing OpenSees package dependencies...'
& $venvPython -m pip install --upgrade pip
Assert-ExitCode 'Upgrading pip'
& $venvPython -m pip install -r (Join-Path $root 'requirements-opensees.txt')
Assert-ExitCode 'Installing Python dependencies'

if (-not $SkipFrontendBuild -and (Get-Command npm.cmd -ErrorAction SilentlyContinue)) {
    Write-Step 'Checking and rebuilding the frontend...'
    Push-Location (Join-Path $root 'platform-ui')
    try {
        & npm.cmd ci
        Assert-ExitCode 'Installing frontend dependencies'
        & npm.cmd run build
        Assert-ExitCode 'Building the frontend'
    }
    finally {
        Pop-Location
    }
}
elseif (-not (Test-Path (Join-Path $root 'platform-ui\dist\index.html'))) {
    throw 'The frontend production build is missing and frontend build was skipped.'
}

Write-Step 'Running submission self-check...'
& $venvPython (Join-Path $root 'verify_submission.py')
Assert-ExitCode 'Running submission self-check'
Write-Host '[MOMO] Installation and self-check completed. Run .\start.ps1 to start the app.' -ForegroundColor Green
