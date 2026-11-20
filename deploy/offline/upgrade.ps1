[CmdletBinding()]
param([Parameter(Mandatory=$true)][string]$NewKitDir,[string]$InstallDir=$PSScriptRoot)
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
$oldWasRunning = $false
$activationStarted = $false
$oldCompose = $null
$newEnvPath = $null
$newEnvCreated = $false
try {
    $oldDir = [IO.Path]::GetFullPath($InstallDir)
    $newDir = [IO.Path]::GetFullPath($NewKitDir)
    Assert-Portal ($oldDir -ne $newDir) 'New kit must be a separate directory'

    $oldEnvPath = Join-Path $oldDir '.env'
    $oldEnv = Read-PortalEnv $oldEnvPath
    $oldVersion = Read-PortalSetting $oldEnv 'PORTAL_VERSION'
    $oldKit = Assert-PortalKit $oldDir
    $newKit = Assert-PortalKit $newDir
    Assert-PortalDocker $newKit.Architecture

    Assert-Portal ($oldKit.Version -eq $oldVersion) 'Current kit/.env version mismatch'
    Assert-Portal ($oldVersion -ne $newKit.Version) 'Already on target version'
    Assert-Portal ($oldVersion -match '^\d+\.\d+\.\d+$' -and $newKit.Version -match '^\d+\.\d+\.\d+$') 'Automatic upgrade requires stable numeric versions (major.minor.patch)'
    $oldParsed = $null
    $newParsed = $null
    Assert-Portal ([version]::TryParse($oldVersion,[ref]$oldParsed) -and [version]::TryParse($newKit.Version,[ref]$newParsed)) 'Invalid stable version'
    Assert-Portal ($newParsed -gt $oldParsed) 'Downgrade or non-increasing version change is not supported'
    Assert-Portal ($oldKit.Architecture -eq $newKit.Architecture) 'Cross-architecture upgrade unsupported'
    $newEnvPath = Join-Path $newDir '.env'
    Assert-Portal (-not (Test-Path -LiteralPath $newEnvPath)) 'New kit already contains .env'
    Assert-Portal ((Read-PortalSetting $oldEnv 'PORTAL_CONTOUR') -in @('SOURCE','TARGET')) 'Invalid contour'

    $timeout = 180
    if ($env:PORTAL_INSTALL_TIMEOUT_SECONDS) {
        Assert-Portal ([int]::TryParse($env:PORTAL_INSTALL_TIMEOUT_SECONDS,[ref]$timeout) -and $timeout -gt 0) 'Invalid timeout'
    }

    $oldCompose = Get-PortalComposeArgs $oldDir $oldEnvPath
    $running = Invoke-PortalDocker ($oldCompose + @('ps','--status','running','-q')) -Capture
    $oldWasRunning = (($running -join '').Trim().Length -gt 0)

    # Keep a running old workload quiesced from the snapshot until the new
    # version is healthy, or the old matching-version restore has completed.
    $backupOutput = & powershell.exe -NoProfile -ExecutionPolicy Bypass -File (Join-Path $oldDir 'backup.ps1') -InstallDir $oldDir -KeepPaused
    Assert-Portal ($LASTEXITCODE -eq 0) 'Pre-upgrade backup failed'
    $backup = [string]($backupOutput | Where-Object { $_ -is [string] -and $_.Trim() -match '\.zip$' } | Select-Object -Last 1)
    Assert-Portal ($backup -and (Test-Path -LiteralPath $backup -PathType Leaf)) 'Pre-upgrade backup missing'
    if ($oldWasRunning) {
        $paused = Invoke-PortalDocker ($oldCompose + @('ps','--status','paused','-q')) -Capture
        Assert-Portal (($paused -join '').Trim().Length -gt 0) 'Pre-upgrade backup did not leave the old workload paused'
    }

    Invoke-PortalDocker @('load','-i',(Join-Path $newDir 'images/backend.tar')) | Out-Null
    Invoke-PortalDocker @('load','-i',(Join-Path $newDir 'images/frontend.tar')) | Out-Null
    foreach ($image in @("harbor-transfer-portal-backend:$($newKit.Version)","harbor-transfer-portal-frontend:$($newKit.Version)")) {
        Invoke-PortalDocker @('image','inspect',$image) | Out-Null
        $imageArch = (Invoke-PortalDocker @('image','inspect','--format','{{.Architecture}}',$image) -Capture | Select-Object -First 1).Trim()
        if ($imageArch -in @('x86_64','x64')) { $imageArch = 'amd64' }
        if ($imageArch -eq 'aarch64') { $imageArch = 'arm64' }
        Assert-Portal ($imageArch -eq $newKit.Architecture) "Image architecture mismatch: $image"
    }

    $newEnvCreated = $true
    Write-PortalAtomic $newEnvPath (Set-PortalEnvVersion $oldEnv $newKit.Version)
    try {
        $activationStarted = $true
        Invoke-PortalDocker ((Get-PortalComposeArgs $newDir (Join-Path $newDir '.env')) + @('up','-d','--no-build','--pull','never','--wait','--wait-timeout',[string]$timeout)) | Out-Null
        $oldWasRunning = $false
        Write-Host "UPGRADE_OK: $oldVersion -> $($newKit.Version)"
        Write-Host "Backup: $backup"
        Write-Host "Previous kit retained: $oldDir"
    } catch {
        [Console]::Error.WriteLine("Upgrade failed; restoring $oldVersion")
        & powershell.exe -NoProfile -ExecutionPolicy Bypass -File (Join-Path $oldDir 'restore.ps1') -BackupArchive $backup -ConfirmRestore -InstallDir $oldDir
        if ($LASTEXITCODE -ne 0) {
            if ($newEnvCreated -and (Test-Path -LiteralPath $newEnvPath)) {
                try { Remove-Item -LiteralPath $newEnvPath -Force; $newEnvCreated = $false }
                catch { [Console]::Error.WriteLine('CRITICAL: failed to remove inactive new-kit .env after rollback failure') }
            }
            [Console]::Error.WriteLine("ROLLBACK_FAILED; preserve both kits and $backup")
            exit 3
        }
        if ($newEnvCreated -and (Test-Path -LiteralPath $newEnvPath)) {
            try { Remove-Item -LiteralPath $newEnvPath -Force; $newEnvCreated = $false }
            catch {
                [Console]::Error.WriteLine('CRITICAL: old Portal was restored but inactive new-kit .env could not be removed')
                exit 3
            }
        }
        $oldWasRunning = $false
        [Console]::Error.WriteLine("ROLLBACK_OK from $backup")
        exit 2
    }
} catch {
    [Console]::Error.WriteLine($_.Exception.Message)
    if ($newEnvCreated -and $newEnvPath -and (Test-Path -LiteralPath $newEnvPath)) {
        try { Remove-Item -LiteralPath $newEnvPath -Force; $newEnvCreated = $false }
        catch { [Console]::Error.WriteLine('CRITICAL: failed to remove inactive new-kit .env after upgrade failure'); exit 3 }
    }
    if ($oldWasRunning -and -not $activationStarted -and $oldCompose) {
        try {
            $pausedNow = Invoke-PortalDocker ($oldCompose + @('ps','--status','paused','-q')) -Capture
            if (($pausedNow -join '').Trim().Length -gt 0) {
                Invoke-PortalDocker ($oldCompose + @('unpause')) | Out-Null
            }
        } catch {
            [Console]::Error.WriteLine('CRITICAL: unable to resume the previous Portal after pre-activation upgrade failure')
            exit 3
        }
    }
    exit 1
}
 })
if ($bootstrapEntries.Count -ne 1 -or $bootstrapEntries[0] -cnotmatch '^([0-9a-f]{64})  windows-lifecycle-common\.ps1
$oldWasRunning = $false
$activationStarted = $false
$oldCompose = $null
$newEnvPath = $null
$newEnvCreated = $false
try {
    $oldDir = [IO.Path]::GetFullPath($InstallDir)
    $newDir = [IO.Path]::GetFullPath($NewKitDir)
    Assert-Portal ($oldDir -ne $newDir) 'New kit must be a separate directory'

    $oldEnvPath = Join-Path $oldDir '.env'
    $oldEnv = Read-PortalEnv $oldEnvPath
    $oldVersion = Read-PortalSetting $oldEnv 'PORTAL_VERSION'
    $oldKit = Assert-PortalKit $oldDir
    $newKit = Assert-PortalKit $newDir
    Assert-PortalDocker $newKit.Architecture

    Assert-Portal ($oldKit.Version -eq $oldVersion) 'Current kit/.env version mismatch'
    Assert-Portal ($oldVersion -ne $newKit.Version) 'Already on target version'
    Assert-Portal ($oldVersion -match '^\d+\.\d+\.\d+$' -and $newKit.Version -match '^\d+\.\d+\.\d+$') 'Automatic upgrade requires stable numeric versions (major.minor.patch)'
    $oldParsed = $null
    $newParsed = $null
    Assert-Portal ([version]::TryParse($oldVersion,[ref]$oldParsed) -and [version]::TryParse($newKit.Version,[ref]$newParsed)) 'Invalid stable version'
    Assert-Portal ($newParsed -gt $oldParsed) 'Downgrade or non-increasing version change is not supported'
    Assert-Portal ($oldKit.Architecture -eq $newKit.Architecture) 'Cross-architecture upgrade unsupported'
    $newEnvPath = Join-Path $newDir '.env'
    Assert-Portal (-not (Test-Path -LiteralPath $newEnvPath)) 'New kit already contains .env'
    Assert-Portal ((Read-PortalSetting $oldEnv 'PORTAL_CONTOUR') -in @('SOURCE','TARGET')) 'Invalid contour'

    $timeout = 180
    if ($env:PORTAL_INSTALL_TIMEOUT_SECONDS) {
        Assert-Portal ([int]::TryParse($env:PORTAL_INSTALL_TIMEOUT_SECONDS,[ref]$timeout) -and $timeout -gt 0) 'Invalid timeout'
    }

    $oldCompose = Get-PortalComposeArgs $oldDir $oldEnvPath
    $running = Invoke-PortalDocker ($oldCompose + @('ps','--status','running','-q')) -Capture
    $oldWasRunning = (($running -join '').Trim().Length -gt 0)

    # Keep a running old workload quiesced from the snapshot until the new
    # version is healthy, or the old matching-version restore has completed.
    $backupOutput = & powershell.exe -NoProfile -ExecutionPolicy Bypass -File (Join-Path $oldDir 'backup.ps1') -InstallDir $oldDir -KeepPaused
    Assert-Portal ($LASTEXITCODE -eq 0) 'Pre-upgrade backup failed'
    $backup = [string]($backupOutput | Where-Object { $_ -is [string] -and $_.Trim() -match '\.zip$' } | Select-Object -Last 1)
    Assert-Portal ($backup -and (Test-Path -LiteralPath $backup -PathType Leaf)) 'Pre-upgrade backup missing'
    if ($oldWasRunning) {
        $paused = Invoke-PortalDocker ($oldCompose + @('ps','--status','paused','-q')) -Capture
        Assert-Portal (($paused -join '').Trim().Length -gt 0) 'Pre-upgrade backup did not leave the old workload paused'
    }

    Invoke-PortalDocker @('load','-i',(Join-Path $newDir 'images/backend.tar')) | Out-Null
    Invoke-PortalDocker @('load','-i',(Join-Path $newDir 'images/frontend.tar')) | Out-Null
    foreach ($image in @("harbor-transfer-portal-backend:$($newKit.Version)","harbor-transfer-portal-frontend:$($newKit.Version)")) {
        Invoke-PortalDocker @('image','inspect',$image) | Out-Null
        $imageArch = (Invoke-PortalDocker @('image','inspect','--format','{{.Architecture}}',$image) -Capture | Select-Object -First 1).Trim()
        if ($imageArch -in @('x86_64','x64')) { $imageArch = 'amd64' }
        if ($imageArch -eq 'aarch64') { $imageArch = 'arm64' }
        Assert-Portal ($imageArch -eq $newKit.Architecture) "Image architecture mismatch: $image"
    }

    $newEnvCreated = $true
    Write-PortalAtomic $newEnvPath (Set-PortalEnvVersion $oldEnv $newKit.Version)
    try {
        $activationStarted = $true
        Invoke-PortalDocker ((Get-PortalComposeArgs $newDir (Join-Path $newDir '.env')) + @('up','-d','--no-build','--pull','never','--wait','--wait-timeout',[string]$timeout)) | Out-Null
        $oldWasRunning = $false
        Write-Host "UPGRADE_OK: $oldVersion -> $($newKit.Version)"
        Write-Host "Backup: $backup"
        Write-Host "Previous kit retained: $oldDir"
    } catch {
        [Console]::Error.WriteLine("Upgrade failed; restoring $oldVersion")
        & powershell.exe -NoProfile -ExecutionPolicy Bypass -File (Join-Path $oldDir 'restore.ps1') -BackupArchive $backup -ConfirmRestore -InstallDir $oldDir
        if ($LASTEXITCODE -ne 0) {
            if ($newEnvCreated -and (Test-Path -LiteralPath $newEnvPath)) {
                try { Remove-Item -LiteralPath $newEnvPath -Force; $newEnvCreated = $false }
                catch { [Console]::Error.WriteLine('CRITICAL: failed to remove inactive new-kit .env after rollback failure') }
            }
            [Console]::Error.WriteLine("ROLLBACK_FAILED; preserve both kits and $backup")
            exit 3
        }
        if ($newEnvCreated -and (Test-Path -LiteralPath $newEnvPath)) {
            try { Remove-Item -LiteralPath $newEnvPath -Force; $newEnvCreated = $false }
            catch {
                [Console]::Error.WriteLine('CRITICAL: old Portal was restored but inactive new-kit .env could not be removed')
                exit 3
            }
        }
        $oldWasRunning = $false
        [Console]::Error.WriteLine("ROLLBACK_OK from $backup")
        exit 2
    }
} catch {
    [Console]::Error.WriteLine($_.Exception.Message)
    if ($newEnvCreated -and $newEnvPath -and (Test-Path -LiteralPath $newEnvPath)) {
        try { Remove-Item -LiteralPath $newEnvPath -Force; $newEnvCreated = $false }
        catch { [Console]::Error.WriteLine('CRITICAL: failed to remove inactive new-kit .env after upgrade failure'); exit 3 }
    }
    if ($oldWasRunning -and -not $activationStarted -and $oldCompose) {
        try {
            $pausedNow = Invoke-PortalDocker ($oldCompose + @('ps','--status','paused','-q')) -Capture
            if (($pausedNow -join '').Trim().Length -gt 0) {
                Invoke-PortalDocker ($oldCompose + @('unpause')) | Out-Null
            }
        } catch {
            [Console]::Error.WriteLine('CRITICAL: unable to resume the previous Portal after pre-activation upgrade failure')
            exit 3
        }
    }
    exit 1
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
$oldWasRunning = $false
$activationStarted = $false
$oldCompose = $null
$newEnvPath = $null
$newEnvCreated = $false
try {
    $oldDir = [IO.Path]::GetFullPath($InstallDir)
    $newDir = [IO.Path]::GetFullPath($NewKitDir)
    Assert-Portal ($oldDir -ne $newDir) 'New kit must be a separate directory'

    $oldEnvPath = Join-Path $oldDir '.env'
    $oldEnv = Read-PortalEnv $oldEnvPath
    $oldVersion = Read-PortalSetting $oldEnv 'PORTAL_VERSION'
    $oldKit = Assert-PortalKit $oldDir
    $newKit = Assert-PortalKit $newDir
    Assert-PortalDocker $newKit.Architecture

    Assert-Portal ($oldKit.Version -eq $oldVersion) 'Current kit/.env version mismatch'
    Assert-Portal ($oldVersion -ne $newKit.Version) 'Already on target version'
    Assert-Portal ($oldVersion -match '^\d+\.\d+\.\d+$' -and $newKit.Version -match '^\d+\.\d+\.\d+$') 'Automatic upgrade requires stable numeric versions (major.minor.patch)'
    $oldParsed = $null
    $newParsed = $null
    Assert-Portal ([version]::TryParse($oldVersion,[ref]$oldParsed) -and [version]::TryParse($newKit.Version,[ref]$newParsed)) 'Invalid stable version'
    Assert-Portal ($newParsed -gt $oldParsed) 'Downgrade or non-increasing version change is not supported'
    Assert-Portal ($oldKit.Architecture -eq $newKit.Architecture) 'Cross-architecture upgrade unsupported'
    $newEnvPath = Join-Path $newDir '.env'
    Assert-Portal (-not (Test-Path -LiteralPath $newEnvPath)) 'New kit already contains .env'
    Assert-Portal ((Read-PortalSetting $oldEnv 'PORTAL_CONTOUR') -in @('SOURCE','TARGET')) 'Invalid contour'

    $timeout = 180
    if ($env:PORTAL_INSTALL_TIMEOUT_SECONDS) {
        Assert-Portal ([int]::TryParse($env:PORTAL_INSTALL_TIMEOUT_SECONDS,[ref]$timeout) -and $timeout -gt 0) 'Invalid timeout'
    }

    $oldCompose = Get-PortalComposeArgs $oldDir $oldEnvPath
    $running = Invoke-PortalDocker ($oldCompose + @('ps','--status','running','-q')) -Capture
    $oldWasRunning = (($running -join '').Trim().Length -gt 0)

    # Keep a running old workload quiesced from the snapshot until the new
    # version is healthy, or the old matching-version restore has completed.
    $backupOutput = & powershell.exe -NoProfile -ExecutionPolicy Bypass -File (Join-Path $oldDir 'backup.ps1') -InstallDir $oldDir -KeepPaused
    Assert-Portal ($LASTEXITCODE -eq 0) 'Pre-upgrade backup failed'
    $backup = [string]($backupOutput | Where-Object { $_ -is [string] -and $_.Trim() -match '\.zip$' } | Select-Object -Last 1)
    Assert-Portal ($backup -and (Test-Path -LiteralPath $backup -PathType Leaf)) 'Pre-upgrade backup missing'
    if ($oldWasRunning) {
        $paused = Invoke-PortalDocker ($oldCompose + @('ps','--status','paused','-q')) -Capture
        Assert-Portal (($paused -join '').Trim().Length -gt 0) 'Pre-upgrade backup did not leave the old workload paused'
    }

    Invoke-PortalDocker @('load','-i',(Join-Path $newDir 'images/backend.tar')) | Out-Null
    Invoke-PortalDocker @('load','-i',(Join-Path $newDir 'images/frontend.tar')) | Out-Null
    foreach ($image in @("harbor-transfer-portal-backend:$($newKit.Version)","harbor-transfer-portal-frontend:$($newKit.Version)")) {
        Invoke-PortalDocker @('image','inspect',$image) | Out-Null
        $imageArch = (Invoke-PortalDocker @('image','inspect','--format','{{.Architecture}}',$image) -Capture | Select-Object -First 1).Trim()
        if ($imageArch -in @('x86_64','x64')) { $imageArch = 'amd64' }
        if ($imageArch -eq 'aarch64') { $imageArch = 'arm64' }
        Assert-Portal ($imageArch -eq $newKit.Architecture) "Image architecture mismatch: $image"
    }

    $newEnvCreated = $true
    Write-PortalAtomic $newEnvPath (Set-PortalEnvVersion $oldEnv $newKit.Version)
    try {
        $activationStarted = $true
        Invoke-PortalDocker ((Get-PortalComposeArgs $newDir (Join-Path $newDir '.env')) + @('up','-d','--no-build','--pull','never','--wait','--wait-timeout',[string]$timeout)) | Out-Null
        $oldWasRunning = $false
        Write-Host "UPGRADE_OK: $oldVersion -> $($newKit.Version)"
        Write-Host "Backup: $backup"
        Write-Host "Previous kit retained: $oldDir"
    } catch {
        [Console]::Error.WriteLine("Upgrade failed; restoring $oldVersion")
        & powershell.exe -NoProfile -ExecutionPolicy Bypass -File (Join-Path $oldDir 'restore.ps1') -BackupArchive $backup -ConfirmRestore -InstallDir $oldDir
        if ($LASTEXITCODE -ne 0) {
            if ($newEnvCreated -and (Test-Path -LiteralPath $newEnvPath)) {
                try { Remove-Item -LiteralPath $newEnvPath -Force; $newEnvCreated = $false }
                catch { [Console]::Error.WriteLine('CRITICAL: failed to remove inactive new-kit .env after rollback failure') }
            }
            [Console]::Error.WriteLine("ROLLBACK_FAILED; preserve both kits and $backup")
            exit 3
        }
        if ($newEnvCreated -and (Test-Path -LiteralPath $newEnvPath)) {
            try { Remove-Item -LiteralPath $newEnvPath -Force; $newEnvCreated = $false }
            catch {
                [Console]::Error.WriteLine('CRITICAL: old Portal was restored but inactive new-kit .env could not be removed')
                exit 3
            }
        }
        $oldWasRunning = $false
        [Console]::Error.WriteLine("ROLLBACK_OK from $backup")
        exit 2
    }
} catch {
    [Console]::Error.WriteLine($_.Exception.Message)
    if ($newEnvCreated -and $newEnvPath -and (Test-Path -LiteralPath $newEnvPath)) {
        try { Remove-Item -LiteralPath $newEnvPath -Force; $newEnvCreated = $false }
        catch { [Console]::Error.WriteLine('CRITICAL: failed to remove inactive new-kit .env after upgrade failure'); exit 3 }
    }
    if ($oldWasRunning -and -not $activationStarted -and $oldCompose) {
        try {
            $pausedNow = Invoke-PortalDocker ($oldCompose + @('ps','--status','paused','-q')) -Capture
            if (($pausedNow -join '').Trim().Length -gt 0) {
                Invoke-PortalDocker ($oldCompose + @('unpause')) | Out-Null
            }
        } catch {
            [Console]::Error.WriteLine('CRITICAL: unable to resume the previous Portal after pre-activation upgrade failure')
            exit 3
        }
    }
    exit 1
}
