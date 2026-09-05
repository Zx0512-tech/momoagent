param(
    [switch]$NoBrowser
)

$ErrorActionPreference = 'Stop'
$root = $PSScriptRoot
if ([string]::IsNullOrWhiteSpace($root)) {
    $root = Split-Path -Parent $MyInvocation.MyCommand.Path
}
$backend = Join-Path $root 'momo_agent\backend'
$venvPython = Join-Path $root '.venv\Scripts\python.exe'
$runtime = Join-Path $root 'bridge_models\stbridge_opensees\openseespy'
$jointOperationInput = Join-Path $root 'output\operation_staged_inputs\operation_3600s_dt1_10mps_precombined\operation_wind_traffic_3600s.csv'
$jointOperationGenerator = Join-Path $root 'tools\scripts\prepare_joint_operation_load.py'

if (-not (Test-Path $venvPython)) {
    throw 'The virtual environment is missing. Run .\install.ps1 first.'
}
if (-not (Test-Path (Join-Path $root 'platform-ui\dist\index.html'))) {
    throw 'The frontend production build is missing.'
}
if (-not (Test-Path $runtime)) {
    throw 'The bundled USER300 OpenSeesPy runtime is missing.'
}
if (-not (Test-Path $jointOperationInput)) {
    & $venvPython $jointOperationGenerator
    if ($LASTEXITCODE -ne 0) {
        throw 'Failed to generate the required STbridge wind+traffic joint runtime input.'
    }
}

# 仅加载经过审核的平台运行配置；未知 MOMO_* 键必须显式告警，不能静默丢弃。
$envFile = Join-Path $root '.env'
$allowedNames = @(
    'MOMO_PLATFORM_MODE',
    'MOMO_LLM_BASE_URL',
    'MOMO_LLM_MODEL',
    'MOMO_LLM_API_KEY',
    'MOMO_LLM_TIMEOUT_S',
    'MOMO_LLM_COMPRESSION_MODEL',
    'MOMO_LLM_HARNESS_THINKING',
    'MOMO_LLM_HARNESS_MAX_TOKENS',
    'MOMO_AGENT_RUNTIME',
    'MOMO_AGENT_PERSISTENT_LOOP',
    'MOMO_AGENT_CONTEXT_WINDOW_TOKENS',
    'MOMO_ANSYS_MAX_CONCURRENT',
    'MOMO_ANSYS_USER_ELEMENT_PATH'
)
if (Test-Path $envFile) {
    foreach ($line in Get-Content -LiteralPath $envFile -Encoding UTF8) {
        $trimmed = $line.Trim()
        if (-not $trimmed -or $trimmed.StartsWith('#') -or -not $trimmed.Contains('=')) {
            continue
        }
        $name, $value = $trimmed.Split('=', 2)
        $name = $name.Trim()
        if ($allowedNames -contains $name) {
            [Environment]::SetEnvironmentVariable($name, $value.Trim(), 'Process')
        }
        elseif ($name.StartsWith('MOMO_')) {
            Write-Warning "Ignored unsupported .env key: $name"
        }
    }
}

if (-not [string]::IsNullOrWhiteSpace($env:MOMO_ANSYS_USER_ELEMENT_PATH)) {
    $configuredUserElementPath = $env:MOMO_ANSYS_USER_ELEMENT_PATH
    if (-not [IO.Path]::IsPathRooted($configuredUserElementPath)) {
        $configuredUserElementPath = Join-Path $root $configuredUserElementPath
    }
    $userElementPath = [IO.Path]::GetFullPath($configuredUserElementPath)
    $userElementLibrary = Join-Path $userElementPath 'UserElemLib.dll'
    if (-not (Test-Path -LiteralPath $userElementLibrary -PathType Leaf)) {
        throw "ANSYS USER300 runtime is missing: $userElementLibrary"
    }
    $env:MOMO_ANSYS_USER_ELEMENT_PATH = $userElementPath
    $env:ANS_USER_PATH = $userElementPath
    $env:ANS_USER_PATH_242 = $userElementPath
}

# start.ps1 是生产 UI 入口；未显式配置时必须进入 LIVE，不能静默降级到 MOCK。
if ([string]::IsNullOrWhiteSpace($env:MOMO_PLATFORM_MODE)) {
    $env:MOMO_PLATFORM_MODE = 'LIVE'
}
if ($env:MOMO_PLATFORM_MODE -notin @('LIVE', 'MOCK')) {
    throw "Unsupported MOMO_PLATFORM_MODE: $env:MOMO_PLATFORM_MODE"
}

$pythonPaths = @($root, $backend)
if ($env:PYTHONPATH) {
    $pythonPaths += $env:PYTHONPATH
}
$env:PYTHONPATH = $pythonPaths -join [IO.Path]::PathSeparator
$env:OPENSEESPY_USER300_RUNTIME = $runtime
$env:MOMO_REQUIRE_PLATFORM_UI = '1'
$env:MOMO_REQUIRE_ANSYS = '0'

if (-not $NoBrowser) {
    Start-Process powershell -WindowStyle Hidden -ArgumentList @(
        '-NoProfile',
        '-Command',
        "for (`$i=0; `$i -lt 60; `$i++) { try { `$r=Invoke-RestMethod 'http://127.0.0.1:8000/api/v1/readiness' -TimeoutSec 2; if (`$r.status -eq 'READY') { Start-Process 'http://127.0.0.1:8000'; exit 0 } } catch {}; Start-Sleep -Seconds 1 }"
    ) | Out-Null
}

Write-Host '[MOMO] Service URL: http://127.0.0.1:8000' -ForegroundColor Green
Write-Host '[MOMO] Press Ctrl+C to stop. The first real optimization may take 60-90 minutes.' -ForegroundColor Yellow
Push-Location $backend
try {
    & $venvPython -m uvicorn app.main:app --host 127.0.0.1 --port 8000
}
finally {
    Pop-Location
}
