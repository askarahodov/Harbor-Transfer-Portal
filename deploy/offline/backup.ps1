[CmdletBinding()]
param([string]$InstallDir = $PSScriptRoot, [string]$BackupDir = '', [switch]$KeepPaused)
. (Join-Path $PSScriptRoot 'windows-lifecycle-common.ps1')
$pauseAttempted = $false
$runningIds = @()
$backupCompleted = $false
$tmp = $null
try {
    $kit = Assert-PortalKit $InstallDir
    $dir = $kit.Root
    $envPath = Join-Path $dir '.env'
    $envText = Read-PortalEnv $envPath
    $version = Read-PortalSetting $envText 'PORTAL_VERSION'
    $contour = Read-PortalSetting $envText 'PORTAL_CONTOUR'
    Assert-Portal ($version -match '^[A-Za-z0-9._-]+$') 'Invalid PORTAL_VERSION'
    Assert-Portal ($contour -in @('SOURCE','TARGET')) 'Invalid PORTAL_CONTOUR'
    Assert-Portal ((Read-PortalSetting $envText 'PORTAL_VERSION') -eq $kit.Version) 'Kit/.env version mismatch'
    Assert-PortalDocker $kit.Architecture
    $image = "harbor-transfer-portal-backend:$version"
    Invoke-PortalDocker @('image','inspect',$image) | Out-Null
    $imageArch = (Invoke-PortalDocker @('image','inspect','--format','{{.Architecture}}',$image) -Capture | Select-Object -First 1).Trim()
    if ($imageArch -in @('x86_64','x64')) { $imageArch = 'amd64' }
    if ($imageArch -eq 'aarch64') { $imageArch = 'arm64' }
    Assert-Portal ($imageArch -eq $kit.Architecture) 'Backend image architecture mismatch'
    $volume = 'harbor-transfer-portal_portal-data'
    Invoke-PortalDocker @('volume','inspect',$volume) | Out-Null
    if (-not $BackupDir) { $BackupDir = Join-Path $dir 'backups' }
    New-Item -ItemType Directory -Path $BackupDir -Force | Out-Null
    $BackupDir = Assert-PortalNoReparsePath $BackupDir -MustExist
    $backupRoot = [IO.Path]::GetPathRoot($BackupDir)
    Assert-Portal ($BackupDir.TrimEnd('\','/') -ne $backupRoot.TrimEnd('\','/')) 'Backup destination cannot be a filesystem root'
    Assert-Portal ((Get-Item -LiteralPath $BackupDir -Force).PSIsContainer) 'Backup destination must be a directory'
    Set-PortalPrivateDirectoryAcl $BackupDir
    $stamp = [DateTime]::UtcNow.ToString('yyyyMMddTHHmmssZ')
    $archive = Join-Path $BackupDir "harbor-transfer-portal-backup-v$version-$stamp.zip"
    Assert-Portal (-not (Test-Path -LiteralPath $archive)) 'Backup archive already exists'
    $tmp = Join-Path ([IO.Path]::GetTempPath()) ('htp-backup-' + [guid]::NewGuid().ToString('N'))
    New-Item -ItemType Directory -Path $tmp | Out-Null
    Set-PortalPrivateDirectoryAcl $tmp
    $compose = Get-PortalComposeArgs $dir $envPath
    $running = Invoke-PortalDocker ($compose + @('ps','--status','running','-q')) -Capture
    $runningIds = @($running | ForEach-Object { ([string]$_).Trim() } | Where-Object { $_ })
    if ($runningIds.Count -gt 0) {
        $pauseAttempted = $true
        Invoke-PortalDocker ($compose + @('pause')) | Out-Null
    }
    $mount = 'type=bind,source=' + $tmp + ',target=/backup'
    Invoke-PortalDocker @('run','--rm','--pull','never','--network','none','--entrypoint','tar','-v',($volume + ':/app/data:ro'),'--mount',$mount,$image,'-czf','/backup/portal-data.tar.gz','-C','/app/data','.') | Out-Null
    Copy-Item -LiteralPath $envPath -Destination (Join-Path $tmp '.env')
    Set-PortalPrivateFileAcl (Join-Path $tmp '.env')
    $metadata = @("product=harbor-transfer-portal","version=$version","contour=$contour","created_at_utc=$stamp","volume=$volume") -join [Environment]::NewLine
    [IO.File]::WriteAllText((Join-Path $tmp 'backup-metadata.txt'),$metadata,([Text.UTF8Encoding]::new($false)))
    $hashes = foreach ($name in @('.env','backup-metadata.txt','portal-data.tar.gz')) {
        $hash = (Get-FileHash -LiteralPath (Join-Path $tmp $name) -Algorithm SHA256).Hash.ToLowerInvariant()
        "$hash  $name"
    }
    [IO.File]::WriteAllLines((Join-Path $tmp 'CHECKSUMS.sha256'),$hashes,[Text.UTF8Encoding]::new($false))
    foreach ($name in @('backup-metadata.txt','CHECKSUMS.sha256','portal-data.tar.gz')) {
        Set-PortalPrivateFileAcl (Join-Path $tmp $name)
    }
    Compress-Archive -Path (Join-Path $tmp '*') -DestinationPath $archive -CompressionLevel Optimal
    Set-PortalPrivateFileAcl $archive
    $outerHash = (Get-FileHash -LiteralPath $archive -Algorithm SHA256).Hash.ToLowerInvariant()
    [IO.File]::WriteAllText("$archive.sha256","$outerHash  $([IO.Path]::GetFileName($archive))",[Text.UTF8Encoding]::new($false))
    Set-PortalPrivateFileAcl "$archive.sha256"
    Write-Host "Backup created; it contains secrets and must be protected: $archive"
    Write-Host "Backup created; it contains secrets and must be protected: $archive"
    $backupCompleted = $true
    Write-Output $archive
} catch {
    [Console]::Error.WriteLine($_.Exception.Message)
    exit 1
} finally {
    if ($pauseAttempted) {
        if ($KeepPaused -and $backupCompleted) {
            Write-Host 'Portal remains paused for the caller to complete the offline upgrade transaction.'
        } else {
            try {
                $pausedOutput = Invoke-PortalDocker ($compose + @('ps','--status','paused','-q')) -Capture
                $pausedIds = @($pausedOutput | ForEach-Object { ([string]$_).Trim() } | Where-Object { $_ })
                $unpauseFailures = @()
                foreach ($containerId in $pausedIds) {
                    if ($runningIds -contains $containerId) {
                        try { Invoke-PortalDocker @('unpause',$containerId) | Out-Null }
                        catch { $unpauseFailures += $containerId }
                    }
                }
                if ($unpauseFailures.Count -gt 0) { throw "Could not unpause original running containers: $($unpauseFailures -join ', ')" }
            } catch {
                [Console]::Error.WriteLine('CRITICAL: failed to restore the pre-backup running state')
                throw 'Backup failed because Portal could not be safely resumed'
            }
        }
    }
    if ($tmp -and (Test-Path -LiteralPath $tmp)) { Remove-Item -LiteralPath $tmp -Recurse -Force }
}
