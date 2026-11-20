[CmdletBinding()]
param([string]$InstallDir = $PSScriptRoot, [string]$BackupDir = '')
. (Join-Path $PSScriptRoot 'windows-lifecycle-common.ps1')
$paused = $false
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
    $volume = 'harbor-transfer-portal_portal-data'
    Invoke-PortalDocker @('volume','inspect',$volume) | Out-Null
    if (-not $BackupDir) { $BackupDir = Join-Path $dir 'backups' }
    New-Item -ItemType Directory -Path $BackupDir -Force | Out-Null
    $stamp = [DateTime]::UtcNow.ToString('yyyyMMddTHHmmssZ')
    $archive = Join-Path ([IO.Path]::GetFullPath($BackupDir)) "harbor-transfer-portal-backup-v$version-$stamp.zip"
    Assert-Portal (-not (Test-Path -LiteralPath $archive)) 'Backup archive already exists'
    $tmp = Join-Path ([IO.Path]::GetTempPath()) ('htp-backup-' + [guid]::NewGuid().ToString('N'))
    New-Item -ItemType Directory -Path $tmp | Out-Null
    $compose = Get-PortalComposeArgs $dir $envPath
    $running = Invoke-PortalDocker ($compose + @('ps','--status','running','-q')) -Capture
    if (($running -join '').Trim()) {
        Invoke-PortalDocker ($compose + @('pause')) | Out-Null
        $paused = $true
    }
    $mount = 'type=bind,source=' + $tmp + ',target=/backup'
    Invoke-PortalDocker @('run','--rm','--pull','never','--network','none','--entrypoint','tar','-v',($volume + ':/app/data:ro'),'--mount',$mount,$image,'-czf','/backup/portal-data.tar.gz','-C','/app/data','.') | Out-Null
    Copy-Item -LiteralPath $envPath -Destination (Join-Path $tmp '.env')
    $metadata = @("product=harbor-transfer-portal","version=$version","contour=$contour","created_at_utc=$stamp","volume=$volume") -join [Environment]::NewLine
    [IO.File]::WriteAllText((Join-Path $tmp 'backup-metadata.txt'),$metadata,([Text.UTF8Encoding]::new($false)))
    $hashes = foreach ($name in @('.env','backup-metadata.txt','portal-data.tar.gz')) {
        $hash = (Get-FileHash -LiteralPath (Join-Path $tmp $name) -Algorithm SHA256).Hash.ToLowerInvariant()
        "$hash  $name"
    }
    [IO.File]::WriteAllLines((Join-Path $tmp 'CHECKSUMS.sha256'),$hashes,[Text.UTF8Encoding]::new($false))
    Compress-Archive -Path (Join-Path $tmp '*') -DestinationPath $archive -CompressionLevel Optimal
    $outerHash = (Get-FileHash -LiteralPath $archive -Algorithm SHA256).Hash.ToLowerInvariant()
    [IO.File]::WriteAllText("$archive.sha256","$outerHash  $([IO.Path]::GetFileName($archive))",[Text.UTF8Encoding]::new($false))
    Write-Host "Backup created; it contains secrets and must be protected: $archive"
    Write-Output $archive
} catch {
    [Console]::Error.WriteLine($_.Exception.Message)
    exit 1
} finally {
    if ($paused) {
        try { Invoke-PortalDocker ((Get-PortalComposeArgs $dir $envPath) + @('unpause')) | Out-Null }
        catch { [Console]::Error.WriteLine('CRITICAL: failed to unpause Portal after backup') }
    }
    if ($tmp -and (Test-Path -LiteralPath $tmp)) { Remove-Item -LiteralPath $tmp -Recurse -Force }
}
