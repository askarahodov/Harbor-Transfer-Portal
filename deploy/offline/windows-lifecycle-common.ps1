Set-StrictMode -Version Latest
$ErrorActionPreference = 'Stop'

function Assert-Portal([bool]$Condition, [string]$Message) {
    if (-not $Condition) { throw $Message }
}
function Read-PortalSetting([string]$Text, [string]$Name, [string]$Default = '') {
    $match = [regex]::Match($Text, "(?m)^$([regex]::Escape($Name))=([^\r\n]*)")
    if ($match.Success) { return $match.Groups[1].Value.Trim() }
    return $Default
}
function Read-PortalEnv([string]$Path) {
    Assert-Portal (Test-Path -LiteralPath $Path -PathType Leaf) "Missing .env: $Path"
    $item = Get-Item -LiteralPath $Path -Force
    Assert-Portal (($item.Attributes -band [IO.FileAttributes]::ReparsePoint) -eq 0) '.env must not be a reparse point'
    return [IO.File]::ReadAllText($Path)
}
function Invoke-PortalDocker([string[]]$DockerArgs, [switch]$Capture) {
    if ($Capture) {
        $output = & docker @DockerArgs 2>&1
        $exitCode = $LASTEXITCODE
        if ($exitCode -ne 0) { throw "Docker failed ($exitCode): $($DockerArgs -join ' ')" }
        return ,@($output | ForEach-Object { [string]$_ })
    }
    & docker @DockerArgs
    $exitCode = $LASTEXITCODE
    if ($exitCode -ne 0) { throw "Docker failed ($exitCode): $($DockerArgs -join ' ')" }
}
function Assert-PortalDocker([string]$ExpectedArch = '') {
    Assert-Portal ([bool](Get-Command docker -ErrorAction SilentlyContinue)) 'Docker CLI is not installed'
    $os = (Invoke-PortalDocker @('info','--format','{{.OSType}}') -Capture | Select-Object -First 1).Trim()
    Assert-Portal ($os -eq 'linux') 'Docker Desktop must use Linux containers'
    Invoke-PortalDocker @('compose','version') | Out-Null
    if ($ExpectedArch) {
        $arch = (Invoke-PortalDocker @('info','--format','{{.Architecture}}') -Capture | Select-Object -First 1).Trim().ToLowerInvariant()
        if ($arch -in @('x86_64','x64')) { $arch = 'amd64' }
        if ($arch -eq 'aarch64') { $arch = 'arm64' }
        Assert-Portal ($arch -eq $ExpectedArch) "Docker architecture $arch does not match release $ExpectedArch"
    }
}
function Assert-PortalKit([string]$KitDir) {
    $root = [IO.Path]::GetFullPath($KitDir)
    $required = @('release-version.txt','release-arch.txt','CHECKSUMS.sha256','compose.yaml','.env.example','images/backend.tar','images/frontend.tar','install.ps1','install.cmd','backup.ps1','restore.ps1','upgrade.ps1','uninstall.ps1','windows-lifecycle-common.ps1')
    foreach ($file in $required) {
        Assert-Portal (Test-Path -LiteralPath (Join-Path $root $file) -PathType Leaf) "Missing kit file: $file"
    }
    $lines = Get-Content -LiteralPath (Join-Path $root 'CHECKSUMS.sha256') -Encoding UTF8
    Assert-Portal ($lines.Count -gt 0) 'Empty checksum manifest'
    $seen = [Collections.Generic.HashSet[string]]::new([StringComparer]::Ordinal)
    foreach ($line in $lines) {
        Assert-Portal ($line -cmatch '^([0-9a-f]{64})  ([A-Za-z0-9._/-]+)$') "Invalid checksum entry: $line"
        $expected = $Matches[1]
        $relative = $Matches[2]
        Assert-Portal (-not ($relative.Split('/') -contains '..') -and -not ($relative.Split('/') -contains '.')) "Unsafe kit path: $relative"
        Assert-Portal ($seen.Add($relative)) "Duplicate kit path: $relative"
        $path = [IO.Path]::GetFullPath((Join-Path $root ($relative -replace '/', [IO.Path]::DirectorySeparatorChar)))
        Assert-Portal ($path.StartsWith($root.TrimEnd('\','/') + [IO.Path]::DirectorySeparatorChar, [StringComparison]::OrdinalIgnoreCase)) "Path escapes kit: $relative"
        Assert-Portal (Test-Path -LiteralPath $path -PathType Leaf) "Missing checked file: $relative"
        $item = Get-Item -LiteralPath $path -Force
        Assert-Portal (($item.Attributes -band [IO.FileAttributes]::ReparsePoint) -eq 0) "Reparse point in kit: $relative"
        $actual = (Get-FileHash -LiteralPath $path -Algorithm SHA256).Hash
        Assert-Portal ($actual.Equals($expected, [StringComparison]::OrdinalIgnoreCase)) "Checksum mismatch: $relative"
    }
    foreach ($file in $required) {
        if ($file -ne 'CHECKSUMS.sha256') { Assert-Portal ($seen.Contains($file)) "Required file is not checksummed: $file" }
    }
    $version = (Get-Content -LiteralPath (Join-Path $root 'release-version.txt') -Raw).Trim()
    $arch = (Get-Content -LiteralPath (Join-Path $root 'release-arch.txt') -Raw).Trim()
    Assert-Portal ($version -cmatch '^[A-Za-z0-9._-]+$') 'Invalid release version'
    Assert-Portal ($arch -in @('amd64','arm64')) "Unsupported architecture: $arch"
    return [pscustomobject]@{ Root = $root; Version = $version; Architecture = $arch }
}
function Get-PortalComposeArgs([string]$InstallDir, [string]$EnvPath) {
    return @('compose','--env-file',$EnvPath,'-f',(Join-Path $InstallDir 'compose.yaml'))
}
function Set-PortalEnvVersion([string]$Text, [string]$Version) {
    $matches = [regex]::Matches($Text, '(?m)^PORTAL_VERSION=[^\r\n]*')
    Assert-Portal ($matches.Count -eq 1) 'Expected exactly one PORTAL_VERSION in .env'
    return [regex]::Replace($Text, '(?m)^PORTAL_VERSION=[^\r\n]*', "PORTAL_VERSION=$Version")
}
function Write-PortalAtomic([string]$Path, [string]$Text) {
    $temporary = $Path + '.tmp.' + [guid]::NewGuid().ToString('N')
    try {
        [IO.File]::WriteAllText($temporary, $Text, ([Text.UTF8Encoding]::new($false)))
        if (Test-Path -LiteralPath $Path) { [IO.File]::Replace($temporary, $Path, $null) }
        else { [IO.File]::Move($temporary, $Path) }
    } finally {
        if (Test-Path -LiteralPath $temporary) { Remove-Item -LiteralPath $temporary -Force }
    }
}
