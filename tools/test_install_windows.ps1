# Native Windows contract tests for the shipped offline installer, with a fake Docker.
# No Docker Desktop daemon is required or claimed to be qualified by this smoke test.
Set-StrictMode -Version Latest
$ErrorActionPreference = 'Stop'

function Check([bool]$Condition, [string]$Message) {
    if (-not $Condition) { throw "Windows installer test failed: $Message" }
}

$temp = Join-Path ([IO.Path]::GetTempPath()) ("htp-offline-" + [Guid]::NewGuid().ToString('N'))
$kit = Join-Path $temp 'kit'
$fakeBin = Join-Path $temp 'fake-bin'
New-Item -ItemType Directory -Path $kit, $fakeBin, (Join-Path $kit 'images') -Force | Out-Null
$root = (Resolve-Path (Join-Path $PSScriptRoot '..')).Path
$oldPath = $env:PATH
$oldContour = $env:PORTAL_CONTOUR
$oldLog = $env:FAKE_DOCKER_LOG
$oldOs = $env:FAKE_DOCKER_OS

try {
    foreach ($name in @('install.cmd', 'install.ps1', 'compose.yaml', '.env.example')) {
        $source = if ($name -eq '.env.example') { Join-Path $root $name } else { Join-Path $root "deploy/offline/$name" }
        Copy-Item -LiteralPath $source -Destination (Join-Path $kit $name)
    }
    [IO.File]::WriteAllText((Join-Path $kit 'release-version.txt'), ('1.0.0' + [Environment]::NewLine))
    [IO.File]::WriteAllText((Join-Path $kit 'release-arch.txt'), ('amd64' + [Environment]::NewLine))
    [IO.File]::WriteAllText((Join-Path $kit 'images/backend.tar'), 'mock-backend')
    [IO.File]::WriteAllText((Join-Path $kit 'images/frontend.tar'), 'mock-frontend')
    $entries = @(Get-ChildItem -LiteralPath $kit -File -Recurse | Sort-Object FullName | ForEach-Object {
        $relative = $_.FullName.Substring($kit.Length + 1).Replace('\', '/')
        $hash = (Get-FileHash -LiteralPath $_.FullName -Algorithm SHA256).Hash.ToLowerInvariant()
        "$hash  $relative"
    })
    [IO.File]::WriteAllText((Join-Path $kit 'CHECKSUMS.sha256'), (($entries -join [Environment]::NewLine) + [Environment]::NewLine), (New-Object Text.UTF8Encoding($false)))

    $fakeDocker = @'
@echo off
echo %*>>"%FAKE_DOCKER_LOG%"
if /I "%~1"=="info" (
  if "%~3"=="{{.OSType}}" (
    if not "%FAKE_DOCKER_OS%"=="" (echo %FAKE_DOCKER_OS%) else (echo linux)
    exit /b 0
  )
  if "%~3"=="{{.Architecture}}" (
    echo x86_64
    exit /b 0
  )
)
if /I "%~1"=="image" if /I "%~3"=="--format" (
  echo amd64
  exit /b 0
)
exit /b 0
'@
    [IO.File]::WriteAllText((Join-Path $fakeBin 'docker.cmd'), $fakeDocker, [Text.Encoding]::ASCII)
    $env:PATH = "$fakeBin;$oldPath"
    $env:FAKE_DOCKER_LOG = Join-Path $temp 'docker.log'
    $env:PORTAL_CONTOUR = 'TARGET'
    Remove-Item Env:FAKE_DOCKER_OS -ErrorAction SilentlyContinue
    Check ((Get-Command docker).Source -eq (Join-Path $fakeBin 'docker.cmd')) 'Fake Docker is not first in PATH'

    $firstOutput = & (Join-Path $kit 'install.cmd') 2>&1 | Out-String
    Check ($LASTEXITCODE -eq 0) "Initial CMD install failed: $firstOutput"
    $envPath = Join-Path $kit '.env'
    Check (Test-Path -LiteralPath $envPath -PathType Leaf) '.env was not created'
    $initial = [IO.File]::ReadAllText($envPath)
    Check ($initial.Contains('PORTAL_CONTOUR=TARGET')) 'Bootstrap contour not saved'
    Check ($initial.Contains('PORTAL_VERSION=1.0.0')) 'Version not saved'
    Check ($initial -match '(?m)^JWT_SECRET=[a-f0-9]{64}$') 'Strong JWT secret missing'
    $dockerLog = [IO.File]::ReadAllText($env:FAKE_DOCKER_LOG)
    Check ($dockerLog.Contains('load -i images/backend.tar')) 'Backend image not loaded'
    Check ($dockerLog.Contains('load -i images/frontend.tar')) 'Frontend image not loaded'
    Check ($dockerLog -match 'compose .*up -d --no-build --pull never --wait --wait-timeout 180') 'Compose no-build/no-pull/health contract missing'

    $rerunOutput = & powershell.exe -NoProfile -ExecutionPolicy Bypass -File (Join-Path $kit 'install.ps1') 2>&1 | Out-String
    Check ($LASTEXITCODE -eq 0) "Idempotent rerun failed: $rerunOutput"
    Check ([IO.File]::ReadAllText($envPath) -ceq $initial) 'Rerun overwrote .env'

    [IO.File]::WriteAllText($envPath, $initial.Replace('PORTAL_HTTP_BIND=127.0.0.1', 'PORTAL_HTTP_BIND=0.0.0.0'))
    [IO.File]::WriteAllText($env:FAKE_DOCKER_LOG, '')
    & powershell.exe -NoProfile -ExecutionPolicy Bypass -File (Join-Path $kit 'install.ps1') *> $null
    Check ($LASTEXITCODE -ne 0) 'Non-loopback plain HTTP was accepted'
    Check (-not ([IO.File]::ReadAllText($env:FAKE_DOCKER_LOG) -match 'load -i|compose .*up')) 'Unsafe browser transport reached Docker load or start'
    [IO.File]::WriteAllText($envPath, $initial)

    [IO.File]::WriteAllText($envPath, $initial.Replace('PORTAL_VERSION=1.0.0', 'PORTAL_VERSION=0.9.0'))
    [IO.File]::WriteAllText($env:FAKE_DOCKER_LOG, '')
    & powershell.exe -NoProfile -ExecutionPolicy Bypass -File (Join-Path $kit 'install.ps1') *> $null
    Check ($LASTEXITCODE -ne 0) 'Mismatched release version was accepted'
    Check (-not ([IO.File]::ReadAllText($env:FAKE_DOCKER_LOG) -match 'load -i')) 'Mismatched release loaded images'
    [IO.File]::WriteAllText($envPath, $initial)

    $env:FAKE_DOCKER_OS = 'windows'
    [IO.File]::WriteAllText($env:FAKE_DOCKER_LOG, '')
    & powershell.exe -NoProfile -ExecutionPolicy Bypass -File (Join-Path $kit 'install.ps1') *> $null
    Check ($LASTEXITCODE -ne 0) 'Windows containers mode was accepted'
    Check (-not ([IO.File]::ReadAllText($env:FAKE_DOCKER_LOG) -match 'load -i')) 'Windows containers mode loaded images'
    Remove-Item Env:FAKE_DOCKER_OS -ErrorAction SilentlyContinue

    [IO.File]::AppendAllText((Join-Path $kit 'images/backend.tar'), 'tamper')
    [IO.File]::WriteAllText($env:FAKE_DOCKER_LOG, '')
    & powershell.exe -NoProfile -ExecutionPolicy Bypass -File (Join-Path $kit 'install.ps1') *> $null
    Check ($LASTEXITCODE -ne 0) 'Tampered image was accepted'
    Check ([IO.File]::ReadAllText($env:FAKE_DOCKER_LOG) -eq '') 'Tampered image reached Docker'

    Write-Host 'Windows installer contract passed: checksums, initial install, rerun, transport, version, Docker engine mode.'
} finally {
    $env:PATH = $oldPath
    if ($null -eq $oldContour) { Remove-Item Env:PORTAL_CONTOUR -ErrorAction SilentlyContinue } else { $env:PORTAL_CONTOUR = $oldContour }
    if ($null -eq $oldLog) { Remove-Item Env:FAKE_DOCKER_LOG -ErrorAction SilentlyContinue } else { $env:FAKE_DOCKER_LOG = $oldLog }
    if ($null -eq $oldOs) { Remove-Item Env:FAKE_DOCKER_OS -ErrorAction SilentlyContinue } else { $env:FAKE_DOCKER_OS = $oldOs }
    Remove-Item -LiteralPath $temp -Recurse -Force -ErrorAction SilentlyContinue
}
