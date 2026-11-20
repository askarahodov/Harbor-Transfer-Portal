[CmdletBinding()]
param([string]$InstallDir = $PSScriptRoot, [string]$BackupDir = '', [switch]$KeepPaused)
# Verify the shared executable helper before loading it. This bootstrap deliberately
# uses only built-in PowerShell/.NET APIs and does not dot-source release code.
$bootstrapRoot = [IO.Path]::GetFullPath($PSScriptRoot)
$bootstrapManifest = Join-Path $bootstrapRoot 'CHECKSUMS.sha256'
$bootstrapHelper = Join-Path $bootstrapRoot 'windows-lifecycle-common.ps1'
if (-not (Test-Path -LiteralPath $bootstrapManifest -PathType Leaf)) { throw 'Missing release checksum manifest' }
if (-not (Test-Path -LiteralPath $bootstrapHelper -PathType Leaf)) { throw 'Missing shared lifecycle helper' }
foreach ($bootstrapPath in @($bootstrapManifest, $bootstrapHelper)) {
    $bootstrapItem = Get-Item -LiteralPath $bootstrapPath -Force
    if (($bootstrapItem.Attributes -band [IO.FileAttributes]::ReparsePoint) -ne 0) { throw "Reparse point is not allowed: $bootstrapPath" }
}
$bootstrapLines = @(Get-Content -LiteralPath $bootstrapManifest -Encoding UTF8)
$bootstrapEntries = @($bootstrapLines | Where-Object { $_ -match '  windows-lifecycle-common\.ps1
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
    $BackupDir = Assert-PortalNoReparsePath ([IO.Path]::GetFullPath($BackupDir))
    New-Item -ItemType Directory -Path $BackupDir -Force | Out-Null
    $BackupDir = Assert-PortalNoReparsePath $BackupDir -MustExist
    $backupRoot = [IO.Path]::GetPathRoot($BackupDir)
    Assert-Portal ($BackupDir.TrimEnd('\','/') -ne $backupRoot.TrimEnd('\','/')) 'Backup destination cannot be a filesystem root'
    Assert-Portal ((Get-Item -LiteralPath $BackupDir -Force).PSIsContainer) 'Backup destination must be a directory'
    Assert-Portal (-not $dir.Equals($BackupDir, [StringComparison]::OrdinalIgnoreCase)) 'Backup destination cannot be the installation directory'
    $backupPrefix = $BackupDir.TrimEnd('\','/') + [IO.Path]::DirectorySeparatorChar
    Assert-Portal (-not $dir.StartsWith($backupPrefix, [StringComparison]::OrdinalIgnoreCase)) 'Backup destination cannot contain the installation directory'
    Set-PortalPrivateDirectoryAcl $BackupDir
    foreach ($existingBackup in @(Get-ChildItem -LiteralPath $BackupDir -Filter 'harbor-transfer-portal-backup-*' -File -Force)) {
        Set-PortalPrivateFileAcl $existingBackup.FullName
    }
    $stamp = [DateTime]::UtcNow.ToString('yyyyMMddTHHmmssfffZ')
    $archive = Join-Path $BackupDir "harbor-transfer-portal-backup-v$version-$stamp.zip"
    Assert-Portal (-not (Test-Path -LiteralPath $archive)) 'Backup archive already exists'
    $tmp = Join-Path ([IO.Path]::GetTempPath()) ('htp-backup-' + [guid]::NewGuid().ToString('N'))
    $tmp = Assert-PortalNoReparsePath $tmp
    New-Item -ItemType Directory -Path $tmp | Out-Null
    Set-PortalPrivateDirectoryAcl $tmp
    $compose = Get-PortalComposeArgs $dir $envPath
    $running = Invoke-PortalDocker ($compose + @('ps','--status','running','-q')) -Capture
    $runningIds = @($running | ForEach-Object { ([string]$_).Trim() } | Where-Object { $_ })
    if ($runningIds.Count -gt 0) {
        $pauseAttempted = $true
        Invoke-PortalDocker ($compose + @('pause')) | Out-Null
        $paused = Invoke-PortalDocker ($compose + @('ps','--status','paused','-q')) -Capture
        $pausedIds = @($paused | ForEach-Object { ([string]$_).Trim() } | Where-Object { $_ })
        foreach ($containerId in $runningIds) {
            Assert-Portal ($pausedIds -contains $containerId) "Container $containerId did not enter paused state; refusing an inconsistent snapshot"
        }
    }
    $mount = 'type=bind,source=' + $tmp + ',target=/backup'
    # The restricted NTFS bind mount is accessed by a one-off root helper; the data volume remains read-only.
    Invoke-PortalDocker @('run','--rm','--pull','never','--network','none','--user','0:0','--entrypoint','tar','-v',($volume + ':/app/data:ro'),'--mount',$mount,$image,'-czf','/backup/portal-data.tar.gz','-C','/app/data','.') | Out-Null
    $stagedEnvPath = Join-Path $tmp '.env'
    [IO.File]::Copy($envPath, $stagedEnvPath)
    [IO.File]::SetAttributes($stagedEnvPath, [IO.FileAttributes]::Normal)
    Set-PortalPrivateFileAcl $stagedEnvPath
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
    $archiveFiles = @(
        (Join-Path $tmp '.env'),
        (Join-Path $tmp 'CHECKSUMS.sha256'),
        (Join-Path $tmp 'backup-metadata.txt'),
        (Join-Path $tmp 'portal-data.tar.gz')
    )
    Compress-Archive -LiteralPath $archiveFiles -DestinationPath $archive -CompressionLevel Optimal
    Add-Type -AssemblyName System.IO.Compression
    Add-Type -AssemblyName System.IO.Compression.FileSystem
    $zip = [IO.Compression.ZipFile]::OpenRead($archive)
    try {
        $names = @($zip.Entries | ForEach-Object { $_.FullName })
        $expectedNames = @('.env','CHECKSUMS.sha256','backup-metadata.txt','portal-data.tar.gz')
        Assert-Portal ((($names | Sort-Object) -join '|') -ceq (($expectedNames | Sort-Object) -join '|')) 'Backup archive layout is incomplete or unexpected'
    } finally {
        $zip.Dispose()
    }
    Set-PortalPrivateFileAcl $archive
    $outerHash = (Get-FileHash -LiteralPath $archive -Algorithm SHA256).Hash.ToLowerInvariant()
    [IO.File]::WriteAllText("$archive.sha256","$outerHash  $([IO.Path]::GetFileName($archive))",[Text.UTF8Encoding]::new($false))
    Set-PortalPrivateFileAcl "$archive.sha256"
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
 })
if ($bootstrapEntries.Count -ne 1 -or $bootstrapEntries[0] -cnotmatch '^([0-9a-f]{64})  windows-lifecycle-common\.ps1
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
    $BackupDir = Assert-PortalNoReparsePath ([IO.Path]::GetFullPath($BackupDir))
    New-Item -ItemType Directory -Path $BackupDir -Force | Out-Null
    $BackupDir = Assert-PortalNoReparsePath $BackupDir -MustExist
    $backupRoot = [IO.Path]::GetPathRoot($BackupDir)
    Assert-Portal ($BackupDir.TrimEnd('\','/') -ne $backupRoot.TrimEnd('\','/')) 'Backup destination cannot be a filesystem root'
    Assert-Portal ((Get-Item -LiteralPath $BackupDir -Force).PSIsContainer) 'Backup destination must be a directory'
    Assert-Portal (-not $dir.Equals($BackupDir, [StringComparison]::OrdinalIgnoreCase)) 'Backup destination cannot be the installation directory'
    $backupPrefix = $BackupDir.TrimEnd('\','/') + [IO.Path]::DirectorySeparatorChar
    Assert-Portal (-not $dir.StartsWith($backupPrefix, [StringComparison]::OrdinalIgnoreCase)) 'Backup destination cannot contain the installation directory'
    Set-PortalPrivateDirectoryAcl $BackupDir
    foreach ($existingBackup in @(Get-ChildItem -LiteralPath $BackupDir -Filter 'harbor-transfer-portal-backup-*' -File -Force)) {
        Set-PortalPrivateFileAcl $existingBackup.FullName
    }
    $stamp = [DateTime]::UtcNow.ToString('yyyyMMddTHHmmssfffZ')
    $archive = Join-Path $BackupDir "harbor-transfer-portal-backup-v$version-$stamp.zip"
    Assert-Portal (-not (Test-Path -LiteralPath $archive)) 'Backup archive already exists'
    $tmp = Join-Path ([IO.Path]::GetTempPath()) ('htp-backup-' + [guid]::NewGuid().ToString('N'))
    $tmp = Assert-PortalNoReparsePath $tmp
    New-Item -ItemType Directory -Path $tmp | Out-Null
    Set-PortalPrivateDirectoryAcl $tmp
    $compose = Get-PortalComposeArgs $dir $envPath
    $running = Invoke-PortalDocker ($compose + @('ps','--status','running','-q')) -Capture
    $runningIds = @($running | ForEach-Object { ([string]$_).Trim() } | Where-Object { $_ })
    if ($runningIds.Count -gt 0) {
        $pauseAttempted = $true
        Invoke-PortalDocker ($compose + @('pause')) | Out-Null
        $paused = Invoke-PortalDocker ($compose + @('ps','--status','paused','-q')) -Capture
        $pausedIds = @($paused | ForEach-Object { ([string]$_).Trim() } | Where-Object { $_ })
        foreach ($containerId in $runningIds) {
            Assert-Portal ($pausedIds -contains $containerId) "Container $containerId did not enter paused state; refusing an inconsistent snapshot"
        }
    }
    $mount = 'type=bind,source=' + $tmp + ',target=/backup'
    # The restricted NTFS bind mount is accessed by a one-off root helper; the data volume remains read-only.
    Invoke-PortalDocker @('run','--rm','--pull','never','--network','none','--user','0:0','--entrypoint','tar','-v',($volume + ':/app/data:ro'),'--mount',$mount,$image,'-czf','/backup/portal-data.tar.gz','-C','/app/data','.') | Out-Null
    $stagedEnvPath = Join-Path $tmp '.env'
    [IO.File]::Copy($envPath, $stagedEnvPath)
    [IO.File]::SetAttributes($stagedEnvPath, [IO.FileAttributes]::Normal)
    Set-PortalPrivateFileAcl $stagedEnvPath
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
    $archiveFiles = @(
        (Join-Path $tmp '.env'),
        (Join-Path $tmp 'CHECKSUMS.sha256'),
        (Join-Path $tmp 'backup-metadata.txt'),
        (Join-Path $tmp 'portal-data.tar.gz')
    )
    Compress-Archive -LiteralPath $archiveFiles -DestinationPath $archive -CompressionLevel Optimal
    Add-Type -AssemblyName System.IO.Compression
    Add-Type -AssemblyName System.IO.Compression.FileSystem
    $zip = [IO.Compression.ZipFile]::OpenRead($archive)
    try {
        $names = @($zip.Entries | ForEach-Object { $_.FullName })
        $expectedNames = @('.env','CHECKSUMS.sha256','backup-metadata.txt','portal-data.tar.gz')
        Assert-Portal ((($names | Sort-Object) -join '|') -ceq (($expectedNames | Sort-Object) -join '|')) 'Backup archive layout is incomplete or unexpected'
    } finally {
        $zip.Dispose()
    }
    Set-PortalPrivateFileAcl $archive
    $outerHash = (Get-FileHash -LiteralPath $archive -Algorithm SHA256).Hash.ToLowerInvariant()
    [IO.File]::WriteAllText("$archive.sha256","$outerHash  $([IO.Path]::GetFileName($archive))",[Text.UTF8Encoding]::new($false))
    Set-PortalPrivateFileAcl "$archive.sha256"
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
) {
    throw 'Shared lifecycle helper must have exactly one valid checksum entry'
}
$bootstrapExpected = $Matches[1]
$bootstrapActual = (Get-FileHash -LiteralPath $bootstrapHelper -Algorithm SHA256).Hash
if (-not $bootstrapActual.Equals($bootstrapExpected, [StringComparison]::OrdinalIgnoreCase)) {
    throw 'Shared lifecycle helper checksum mismatch; refusing to execute release code'
}

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
    $BackupDir = Assert-PortalNoReparsePath ([IO.Path]::GetFullPath($BackupDir))
    New-Item -ItemType Directory -Path $BackupDir -Force | Out-Null
    $BackupDir = Assert-PortalNoReparsePath $BackupDir -MustExist
    $backupRoot = [IO.Path]::GetPathRoot($BackupDir)
    Assert-Portal ($BackupDir.TrimEnd('\','/') -ne $backupRoot.TrimEnd('\','/')) 'Backup destination cannot be a filesystem root'
    Assert-Portal ((Get-Item -LiteralPath $BackupDir -Force).PSIsContainer) 'Backup destination must be a directory'
    Assert-Portal (-not $dir.Equals($BackupDir, [StringComparison]::OrdinalIgnoreCase)) 'Backup destination cannot be the installation directory'
    $backupPrefix = $BackupDir.TrimEnd('\','/') + [IO.Path]::DirectorySeparatorChar
    Assert-Portal (-not $dir.StartsWith($backupPrefix, [StringComparison]::OrdinalIgnoreCase)) 'Backup destination cannot contain the installation directory'
    Set-PortalPrivateDirectoryAcl $BackupDir
    foreach ($existingBackup in @(Get-ChildItem -LiteralPath $BackupDir -Filter 'harbor-transfer-portal-backup-*' -File -Force)) {
        Set-PortalPrivateFileAcl $existingBackup.FullName
    }
    $stamp = [DateTime]::UtcNow.ToString('yyyyMMddTHHmmssfffZ')
    $archive = Join-Path $BackupDir "harbor-transfer-portal-backup-v$version-$stamp.zip"
    Assert-Portal (-not (Test-Path -LiteralPath $archive)) 'Backup archive already exists'
    $tmp = Join-Path ([IO.Path]::GetTempPath()) ('htp-backup-' + [guid]::NewGuid().ToString('N'))
    $tmp = Assert-PortalNoReparsePath $tmp
    New-Item -ItemType Directory -Path $tmp | Out-Null
    Set-PortalPrivateDirectoryAcl $tmp
    $compose = Get-PortalComposeArgs $dir $envPath
    $running = Invoke-PortalDocker ($compose + @('ps','--status','running','-q')) -Capture
    $runningIds = @($running | ForEach-Object { ([string]$_).Trim() } | Where-Object { $_ })
    if ($runningIds.Count -gt 0) {
        $pauseAttempted = $true
        Invoke-PortalDocker ($compose + @('pause')) | Out-Null
        $paused = Invoke-PortalDocker ($compose + @('ps','--status','paused','-q')) -Capture
        $pausedIds = @($paused | ForEach-Object { ([string]$_).Trim() } | Where-Object { $_ })
        foreach ($containerId in $runningIds) {
            Assert-Portal ($pausedIds -contains $containerId) "Container $containerId did not enter paused state; refusing an inconsistent snapshot"
        }
    }
    $mount = 'type=bind,source=' + $tmp + ',target=/backup'
    # The restricted NTFS bind mount is accessed by a one-off root helper; the data volume remains read-only.
    Invoke-PortalDocker @('run','--rm','--pull','never','--network','none','--user','0:0','--entrypoint','tar','-v',($volume + ':/app/data:ro'),'--mount',$mount,$image,'-czf','/backup/portal-data.tar.gz','-C','/app/data','.') | Out-Null
    $stagedEnvPath = Join-Path $tmp '.env'
    [IO.File]::Copy($envPath, $stagedEnvPath)
    [IO.File]::SetAttributes($stagedEnvPath, [IO.FileAttributes]::Normal)
    Set-PortalPrivateFileAcl $stagedEnvPath
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
    $archiveFiles = @(
        (Join-Path $tmp '.env'),
        (Join-Path $tmp 'CHECKSUMS.sha256'),
        (Join-Path $tmp 'backup-metadata.txt'),
        (Join-Path $tmp 'portal-data.tar.gz')
    )
    Compress-Archive -LiteralPath $archiveFiles -DestinationPath $archive -CompressionLevel Optimal
    Add-Type -AssemblyName System.IO.Compression
    Add-Type -AssemblyName System.IO.Compression.FileSystem
    $zip = [IO.Compression.ZipFile]::OpenRead($archive)
    try {
        $names = @($zip.Entries | ForEach-Object { $_.FullName })
        $expectedNames = @('.env','CHECKSUMS.sha256','backup-metadata.txt','portal-data.tar.gz')
        Assert-Portal ((($names | Sort-Object) -join '|') -ceq (($expectedNames | Sort-Object) -join '|')) 'Backup archive layout is incomplete or unexpected'
    } finally {
        $zip.Dispose()
    }
    Set-PortalPrivateFileAcl $archive
    $outerHash = (Get-FileHash -LiteralPath $archive -Algorithm SHA256).Hash.ToLowerInvariant()
    [IO.File]::WriteAllText("$archive.sha256","$outerHash  $([IO.Path]::GetFileName($archive))",[Text.UTF8Encoding]::new($false))
    Set-PortalPrivateFileAcl "$archive.sha256"
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
