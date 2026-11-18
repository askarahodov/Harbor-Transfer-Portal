# Native Windows CMD launcher contract test. No real Docker daemon is used.
# Run only on an ephemeral clean checkout; never touch a user's existing .env.
$ErrorActionPreference = 'Stop'
$PSNativeCommandUseErrorActionPreference = $false
$repo = Split-Path -Parent $PSScriptRoot
Set-Location $repo

if (Test-Path '.env') {
    throw 'Windows launcher smoke requires a clean checkout without .env'
}

$mockDir = Join-Path ([System.IO.Path]::GetTempPath()) ("htp-mock-docker-" + [Guid]::NewGuid().ToString("N"))
$null = New-Item -ItemType Directory -Path $mockDir
$originalPath = $env:PATH
$log = Join-Path $mockDir 'docker-calls.txt'
$env:HTP_MOCK_LOG = $log
$env:HTP_MOCK_OS = 'linux'
$env:PATH = "$mockDir;$originalPath"

@'
@echo off
echo revision=%PORTAL_VCS_REF% args=%*>>"%HTP_MOCK_LOG%"
if /I "%~1"=="info" if /I "%~2"=="--format" echo %HTP_MOCK_OS%
exit /b 0
'@ | Set-Content -Encoding ascii -Path (Join-Path $mockDir 'docker.cmd')

function Invoke-Launcher([string]$verb, [int]$expectedExit = 0) {
    Write-Host "SMOKE invoking: run.cmd $verb"
    & cmd.exe /d /c "call run.cmd $verb"
    Write-Host "SMOKE exit code: $LASTEXITCODE"
    if ($LASTEXITCODE -ne $expectedExit) {
        throw "run.cmd $verb returned $LASTEXITCODE, expected $expectedExit"
    }
}

try {
    Invoke-Launcher 'help'
    Invoke-Launcher 'doctor'
    Invoke-Launcher 'up'
    if (!(Test-Path '.env')) {
        if (Test-Path $log) { Get-Content $log | ForEach-Object { Write-Host "MOCK: $_" } }
        throw 'First up did not create .env'
    }
    $envHash = (Get-FileHash '.env' -Algorithm SHA256).Hash
    $calls = Get-Content $log
    if (!($calls -match 'args=compose build')) {
        throw 'Windows build phase never invoked docker compose build'
    }
    if (!($calls -match 'args=compose up -d --no-build --pull never --wait --wait-timeout 180')) {
        throw 'Windows start phase did not wait for ready images'
    }
    $revision = (& git rev-parse --verify HEAD).Trim()
    if (!($calls -match ("revision=" + $revision + " args=compose build"))) {
        throw 'Image build did not receive current PORTAL_VCS_REF'
    }

    $buildCount = @($calls | Where-Object { $_ -match 'args=compose build$' }).Count
    Invoke-Launcher 'start'
    Invoke-Launcher 'status'
    Invoke-Launcher 'stop'
    $after = Get-Content $log
    if (@($after | Where-Object { $_ -match 'args=compose build$' }).Count -ne $buildCount) {
        throw 'Start/status/stop unexpectedly rebuilt images'
    }
    if (!($after -match 'args=compose down$') -or ($after -match 'args=compose down -v')) {
        throw 'Stop must retain the persistent volume'
    }
    if ((Get-FileHash '.env' -Algorithm SHA256).Hash -ne $envHash) {
        throw 'Repeat lifecycle changed existing .env'
    }

    $env:HTP_MOCK_OS = 'windows'
    Invoke-Launcher 'doctor' 2
    Write-Host 'Windows CMD launcher smoke: PASS (mocked Docker, real CMD and Git)'
}
finally {
    $env:PATH = $originalPath
    Remove-Item Env:HTP_MOCK_LOG -ErrorAction SilentlyContinue
    Remove-Item Env:HTP_MOCK_OS -ErrorAction SilentlyContinue
    if (Test-Path '.env') { Remove-Item '.env' -Force }
    Remove-Item $mockDir -Recurse -Force -ErrorAction SilentlyContinue
    # PowerShell GitHub Actions wrapper propagates LASTEXITCODE from the
    # deliberately rejected Windows-containers negative test unless cleared.
    $global:LASTEXITCODE = 0
}
