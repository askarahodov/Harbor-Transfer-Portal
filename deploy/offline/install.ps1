# Harbor Transfer Portal — Windows offline installer (PowerShell 5.1+).
# Uses only the prebuilt Linux images supplied in this kit.
Set-StrictMode -Version Latest
$ErrorActionPreference = 'Stop'

function Assert-Condition([bool]$Condition, [string]$Message) {
    if (-not $Condition) { throw "Installation failed: $Message" }
}

function Get-Setting([string]$Text, [string]$Name, [string]$Default) {
    $match = [regex]::Match($Text, "(?m)^$([regex]::Escape($Name))=([^\r\n]*)")
    if ($match.Success -and $match.Groups[1].Value.Length -gt 0) {
        return $match.Groups[1].Value.Trim()
    }
    return $Default
}

function Invoke-Docker([string[]]$Arguments) {
    & docker @Arguments
    Assert-Condition ($LASTEXITCODE -eq 0) "Docker command failed: docker $($Arguments -join ' ')"
}

function Normalize-Arch([string]$Arch) {
    switch ($Arch.Trim().ToLowerInvariant()) {
        { $_ -in @('amd64', 'x86_64') } { return 'amd64' }
        { $_ -in @('arm64', 'aarch64') } { return 'arm64' }
        default { throw "Unsupported Docker architecture: $Arch" }
    }
}

$root = Split-Path -Parent $MyInvocation.MyCommand.Path
Push-Location -LiteralPath $root
try {
    $required = @(
        'release-version.txt', 'release-arch.txt', 'CHECKSUMS.sha256',
        'compose.yaml', '.env.example', 'images/backend.tar', 'images/frontend.tar'
    )
    foreach ($file in $required) {
        Assert-Condition (Test-Path -LiteralPath $file -PathType Leaf) "Missing release file: $file"
    }

    # Never execute a release payload if a file is modified or omitted from checksums.
    Write-Host 'Verifying offline kit SHA-256 checksums...'
    $verified = [System.Collections.Generic.HashSet[string]]::new([StringComparer]::Ordinal)
    $basePath = [IO.Path]::GetFullPath($root).TrimEnd('\', '/') + [IO.Path]::DirectorySeparatorChar
    $checksums = Get-Content -LiteralPath 'CHECKSUMS.sha256' -Encoding UTF8
    Assert-Condition ($checksums.Count -gt 0) 'Empty checksum manifest'
    foreach ($line in $checksums) {
        Assert-Condition ($line -cmatch '^([0-9a-f]{64})  ([^\r\n]+)$') "Invalid checksum entry: $line"
        $expected = $Matches[1]
        $relative = $Matches[2]
        Assert-Condition (
            $relative -match '^[A-Za-z0-9._/-]+$' -and
            -not $relative.StartsWith('/') -and
            -not ($relative.Split('/') -contains '..') -and
            -not ($relative.Split('/') -contains '.')
        ) "Unsafe checksum path: $relative"
        Assert-Condition ($verified.Add($relative)) "Duplicate checksum path: $relative"
        $path = [IO.Path]::GetFullPath((Join-Path $root $relative))
        Assert-Condition ($path.StartsWith($basePath, [StringComparison]::OrdinalIgnoreCase)) "Path escapes release kit: $relative"
        Assert-Condition (Test-Path -LiteralPath $path -PathType Leaf) "Missing checked file: $relative"
        Assert-Condition (
            -not (([IO.File]::GetAttributes($path) -band [IO.FileAttributes]::ReparsePoint) -ne 0)
        ) "Symbolic links are not allowed in release payload: $relative"
        $actual = (Get-FileHash -LiteralPath $path -Algorithm SHA256).Hash
        Assert-Condition ($actual.Equals($expected, [StringComparison]::OrdinalIgnoreCase)) "SHA-256 mismatch: $relative"
    }
    foreach ($file in $required) {
        if ($file -ne 'CHECKSUMS.sha256') {
            Assert-Condition ($verified.Contains($file)) "Required file missing from checksum manifest: $file"
        }
    }
    # Installer files also have to be checksummed by the release builder.
    foreach ($file in @('install.ps1', 'install.cmd')) {
        Assert-Condition ($verified.Contains($file)) "Installer missing from checksum manifest: $file"
    }

    $version = (Get-Content -LiteralPath 'release-version.txt' -Raw).Trim()
    $releaseArch = (Get-Content -LiteralPath 'release-arch.txt' -Raw).Trim()
    Assert-Condition ($version -cmatch '^[A-Za-z0-9._-]+$') 'Invalid release version'
    Assert-Condition ($releaseArch -in @('amd64', 'arm64')) "Unsupported release architecture: $releaseArch"

    Assert-Condition ([bool](Get-Command docker -ErrorAction SilentlyContinue)) 'Docker Desktop/CLI is not installed or not in PATH'
    $dockerOs = (& docker info --format '{{.OSType}}' 2>$null | Out-String).Trim()
    Assert-Condition ($LASTEXITCODE -eq 0) 'Docker Engine is not running'
    Assert-Condition ($dockerOs -eq 'linux') 'Switch Docker Desktop to Linux containers mode'
    $dockerArch = (& docker info --format '{{.Architecture}}' 2>$null | Out-String).Trim()
    Assert-Condition ($LASTEXITCODE -eq 0) 'Cannot read Docker Engine architecture'
    Assert-Condition ((Normalize-Arch $dockerArch) -eq $releaseArch) "Kit architecture $releaseArch does not match Docker Engine $dockerArch"
    & docker compose version *> $null
    Assert-Condition ($LASTEXITCODE -eq 0) 'Docker Compose v2 is required'

    $requiredBytes = (Get-Item -LiteralPath 'images/backend.tar').Length +
                     (Get-Item -LiteralPath 'images/frontend.tar').Length + 512MB
    $drive = [IO.DriveInfo]::new([IO.Path]::GetPathRoot($root))
    Assert-Condition ($drive.AvailableFreeSpace -ge $requiredBytes) "Not enough free space; require at least $requiredBytes bytes"

    $envPath = Join-Path $root '.env'
    if (Test-Path -LiteralPath $envPath) {
        $envItem = Get-Item -LiteralPath $envPath -Force
        Assert-Condition (-not $envItem.PSIsContainer) 'Existing .env must be a regular file'
        Assert-Condition (
            -not (($envItem.Attributes -band [IO.FileAttributes]::ReparsePoint) -ne 0)
        ) 'Existing .env must not be a symbolic link'
        $envContent = [IO.File]::ReadAllText($envPath)
        $configuredVersion = Get-Setting $envContent 'PORTAL_VERSION' ''
        Assert-Condition ($configuredVersion -ceq $version) "Existing .env version '$configuredVersion' is not '$version'; use the supported upgrade procedure"
        Write-Host 'Existing .env preserved.'
    } else {
        $contour = if ($env:PORTAL_CONTOUR) { $env:PORTAL_CONTOUR } else { 'SOURCE' }
        Assert-Condition ($contour -cin @('SOURCE', 'TARGET')) 'PORTAL_CONTOUR must be SOURCE or TARGET'
        $rng = [Security.Cryptography.RandomNumberGenerator]::Create()
        try {
            $bytes = New-Object byte[] 32
            $rng.GetBytes($bytes)
            $jwtSecret = ([BitConverter]::ToString($bytes)).Replace('-', '').ToLowerInvariant()
        } finally {
            $rng.Dispose()
        }
        $envContent = [IO.File]::ReadAllText((Join-Path $root '.env.example'))
        foreach ($pair in @(
            @('PORTAL_CONTOUR', $contour),
            @('PORTAL_VERSION', $version)
        )) {
            $key = $pair[0]
            $value = $pair[1]
            Assert-Condition ([regex]::IsMatch($envContent, "(?m)^$key=")) "Template missing $key"
            $envContent = [regex]::Replace($envContent, "(?m)^$key=[^\r\n]*", "$key=$value")
        }
        Assert-Condition ($envContent.Contains('# JWT_SECRET=')) 'Template missing JWT secret placeholder'
        $envContent = $envContent.Replace('# JWT_SECRET=', "JWT_SECRET=$jwtSecret")
        $temporaryEnv = Join-Path $root '.env.tmp'
        Assert-Condition (-not (Test-Path -LiteralPath $temporaryEnv)) 'Stale .env.tmp exists; inspect it before retrying'
        [IO.File]::WriteAllText($temporaryEnv, $envContent, ([Text.UTF8Encoding]::new($false)))
        [IO.File]::Move($temporaryEnv, $envPath)
        Write-Host "Created local .env for $contour; existing configuration is never overwritten."
    }

    $scheme = Get-Setting $envContent 'PORTAL_BROWSER_SCHEME' 'http'
    $bind = Get-Setting $envContent 'PORTAL_HTTP_BIND' '127.0.0.1'
    $port = Get-Setting $envContent 'PORTAL_HTTP_PORT' '8080'
    Assert-Condition ($scheme -in @('http', 'https')) 'PORTAL_BROWSER_SCHEME must be http or https'
    Assert-Condition (-not ($scheme -eq 'http' -and $bind -ne '127.0.0.1')) 'Non-loopback listener requires PORTAL_BROWSER_SCHEME=https'
    $numericPort = 0
    Assert-Condition ([int]::TryParse($port, [ref]$numericPort) -and $numericPort -ge 1 -and $numericPort -le 65535) 'PORTAL_HTTP_PORT must be between 1 and 65535'

    $timeout = 180
    if ($env:PORTAL_INSTALL_TIMEOUT_SECONDS) {
        Assert-Condition ([int]::TryParse($env:PORTAL_INSTALL_TIMEOUT_SECONDS, [ref]$timeout) -and $timeout -gt 0) 'PORTAL_INSTALL_TIMEOUT_SECONDS must be a positive integer'
    }

    Write-Host 'Loading prebuilt images without pulling or building...'
    Invoke-Docker @('load', '-i', 'images/backend.tar')
    Invoke-Docker @('load', '-i', 'images/frontend.tar')
    foreach ($image in @("harbor-transfer-portal-backend:$version", "harbor-transfer-portal-frontend:$version")) {
        & docker image inspect $image *> $null
        Assert-Condition ($LASTEXITCODE -eq 0) "Missing release image after docker load: $image"
        $imageArch = (& docker image inspect --format '{{.Architecture}}' $image | Out-String).Trim()
        Assert-Condition ($LASTEXITCODE -eq 0 -and $imageArch -eq $releaseArch) "Image architecture mismatch: $image"
    }

    Write-Host "Starting Harbor Transfer Portal $version; waiting for container health..."
    Invoke-Docker @('compose', '--env-file', '.env', '-f', 'compose.yaml',
                    'up', '-d', '--no-build', '--pull', 'never', '--wait',
                    '--wait-timeout', [string]$timeout)
    Write-Host ''
    Write-Host "Installation completed; internal listener: http://$bind`:$port/"
    if ($scheme -eq 'https') {
        Write-Host 'Remote browser access requires a separately configured site-managed HTTPS terminator.'
    } else {
        Write-Host 'HTTP is for local bootstrap only; configure HTTPS before remote authenticated access.'
    }
    Write-Host 'First login on a new database: admin/admin; change the password immediately.'
    Write-Host 'Next: configure local Harbor profiles and signing/trust keys in Settings.'
} catch {
    [Console]::Error.WriteLine($_.Exception.Message)
    exit 1
} finally {
    Pop-Location
}
