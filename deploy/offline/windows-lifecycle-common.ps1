Set-StrictMode -Version Latest
$ErrorActionPreference = 'Stop'

function Assert-Portal([bool]$Condition, [string]$Message) {
    if (-not $Condition) { throw $Message }
}
function Assert-PortalNoReparsePath([string]$Path, [switch]$MustExist) {
    Assert-Portal (-not [string]::IsNullOrWhiteSpace($Path)) 'Path is required'
    $full = [IO.Path]::GetFullPath($Path)
    $root = [IO.Path]::GetPathRoot($full)
    Assert-Portal (-not [string]::IsNullOrWhiteSpace($root)) "Path has no filesystem root: $Path"
    $cursor = $root
    if (Test-Path -LiteralPath $cursor) {
        $rootItem = Get-Item -LiteralPath $cursor -Force
        Assert-Portal (($rootItem.Attributes -band [IO.FileAttributes]::ReparsePoint) -eq 0) "Reparse-point filesystem root is not allowed: $cursor"
    }
    $relative = $full.Substring($root.Length)
    $separators = [char[]]@([IO.Path]::DirectorySeparatorChar, [IO.Path]::AltDirectorySeparatorChar)
    foreach ($part in $relative.Split($separators, [StringSplitOptions]::RemoveEmptyEntries)) {
        $cursor = Join-Path $cursor $part
        if (Test-Path -LiteralPath $cursor) {
            $item = Get-Item -LiteralPath $cursor -Force
            Assert-Portal (($item.Attributes -band [IO.FileAttributes]::ReparsePoint) -eq 0) "Reparse point in path is not allowed: $cursor"
        } elseif ($MustExist) {
            throw "Required path component is missing: $cursor"
        }
    }
    if ($MustExist) { Assert-Portal (Test-Path -LiteralPath $full) "Required path does not exist: $full" }
    return $full
}
function Set-PortalPrivateFileAcl([string]$Path) {
    $full = Assert-PortalNoReparsePath $Path -MustExist
    $identity = [Security.Principal.WindowsIdentity]::GetCurrent().Name
    $icacls = Get-Command icacls.exe -ErrorAction SilentlyContinue
    Assert-Portal ($null -ne $icacls) 'icacls.exe is required to protect Portal secrets on Windows'
    & $icacls.Source $full '/reset' *> $null
    Assert-Portal ($LASTEXITCODE -eq 0) "Could not reset ACL before protecting secret file: $full"
    & $icacls.Source $full '/inheritance:r' '/grant:r' ($identity + ':F') 'SYSTEM:F' 'BUILTIN\Administrators:F' *> $null
    Assert-Portal ($LASTEXITCODE -eq 0) "Could not restrict secret file permissions: $full"
}
function Set-PortalPrivateDirectoryAcl([string]$Path) {
    $full = Assert-PortalNoReparsePath $Path -MustExist
    $item = Get-Item -LiteralPath $full -Force
    Assert-Portal $item.PSIsContainer "Expected a directory for private ACL: $full"
    $identity = [Security.Principal.WindowsIdentity]::GetCurrent().Name
    $icacls = Get-Command icacls.exe -ErrorAction SilentlyContinue
    Assert-Portal ($null -ne $icacls) 'icacls.exe is required to protect Portal backups on Windows'
    & $icacls.Source $full '/reset' *> $null
    Assert-Portal ($LASTEXITCODE -eq 0) "Could not reset ACL before protecting directory: $full"
    & $icacls.Source $full '/inheritance:r' '/grant:r' ($identity + ':(OI)(CI)F') 'SYSTEM:(OI)(CI)F' 'BUILTIN\Administrators:(OI)(CI)F' *> $null
    Assert-Portal ($LASTEXITCODE -eq 0) "Could not restrict directory permissions: $full"
}
function Read-PortalSetting([string]$Text, [string]$Name, [string]$Default = '') {
    $matches = [regex]::Matches($Text, "(?m)^$([regex]::Escape($Name))=([^\r\n]*)")
    Assert-Portal ($matches.Count -le 1) "Duplicate setting in .env or metadata: $Name"
    if ($matches.Count -eq 1) { return $matches[0].Groups[1].Value.Trim() }
    return $Default
}
function Read-PortalEnv([string]$Path) {
    $full = Assert-PortalNoReparsePath $Path -MustExist
    Assert-Portal (Test-Path -LiteralPath $full -PathType Leaf) "Missing .env: $full"
    Set-PortalPrivateFileAcl $full
    $text = [IO.File]::ReadAllText($full)
    $version = Read-PortalSetting $text 'PORTAL_VERSION'
    $contour = Read-PortalSetting $text 'PORTAL_CONTOUR'
    Assert-Portal (-not [string]::IsNullOrWhiteSpace($version)) 'Missing PORTAL_VERSION in .env'
    Assert-Portal ($contour -cin @('SOURCE','TARGET')) 'PORTAL_CONTOUR must occur exactly once and be SOURCE or TARGET'
    return $text
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
    $root = Assert-PortalNoReparsePath $KitDir -MustExist
    Assert-Portal (Test-Path -LiteralPath $root -PathType Container) "Release kit is not a directory: $root"
    $required = @('release-version.txt','release-arch.txt','CHECKSUMS.sha256','compose.yaml','.env.example','images/backend.tar','images/frontend.tar','install.ps1','install.cmd','start.ps1','stop.ps1','backup.ps1','restore.ps1','upgrade.ps1','uninstall.ps1','windows-lifecycle-common.ps1')
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
        $path = Assert-PortalNoReparsePath $path -MustExist
        Assert-Portal (Test-Path -LiteralPath $path -PathType Leaf) "Missing checked file: $relative"
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
    $full = Assert-PortalNoReparsePath $Path
    $parent = Split-Path -Parent $full
    Assert-PortalNoReparsePath $parent -MustExist | Out-Null
    $temporary = $full + '.tmp.' + [guid]::NewGuid().ToString('N')
    $hadDestination = Test-Path -LiteralPath $full
    $backup = $full + '.bak.' + [guid]::NewGuid().ToString('N')
    $replaced = $false
    $createdDestination = $false
    try {
        [IO.File]::WriteAllText($temporary, '', ([Text.UTF8Encoding]::new($false)))
        Set-PortalPrivateFileAcl $temporary
        [IO.File]::WriteAllText($temporary, $Text, ([Text.UTF8Encoding]::new($false)))
        if ($hadDestination) {
            [IO.File]::Replace($temporary, $full, $backup)
            $replaced = $true
        } else {
            [IO.File]::Move($temporary, $full)
            $createdDestination = $true
        }
        Set-PortalPrivateFileAcl $full
        if ($replaced -and (Test-Path -LiteralPath $backup)) {
            Set-PortalPrivateFileAcl $backup
            Remove-Item -LiteralPath $backup -Force
            $replaced = $false
        }
    } catch {
        if ($replaced -and (Test-Path -LiteralPath $backup)) {
            try {
                if (Test-Path -LiteralPath $full) { Remove-Item -LiteralPath $full -Force }
                [IO.File]::Move($backup, $full)
                Set-PortalPrivateFileAcl $full
                $replaced = $false
            } catch {
                throw "Atomic update failed and the previous file is preserved at '$backup': $($_.Exception.Message)"
            }
        } elseif ($createdDestination -and (Test-Path -LiteralPath $full)) {
            Remove-Item -LiteralPath $full -Force
        }
        throw
    } finally {
        if (Test-Path -LiteralPath $temporary) { Remove-Item -LiteralPath $temporary -Force }
    }
}
