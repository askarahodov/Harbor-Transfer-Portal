[CmdletBinding()]
param([Parameter(Mandatory=$true)][string]$NewKitDir,[string]$InstallDir=$PSScriptRoot)
. (Join-Path $PSScriptRoot 'windows-lifecycle-common.ps1')
$oldWasRunning = $false
$activationStarted = $false
$oldCompose = $null
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
    Assert-Portal (-not (Test-Path (Join-Path $newDir '.env'))) 'New kit already contains .env'
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

    Write-PortalAtomic (Join-Path $newDir '.env') (Set-PortalEnvVersion $oldEnv $newKit.Version)
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
            [Console]::Error.WriteLine("ROLLBACK_FAILED; preserve both kits and $backup")
            exit 3
        }
        $oldWasRunning = $false
        [Console]::Error.WriteLine("ROLLBACK_OK from $backup")
        exit 2
    }
} catch {
    [Console]::Error.WriteLine($_.Exception.Message)
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
