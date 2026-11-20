[CmdletBinding()]
param([Parameter(Mandatory=$true)][string]$NewKitDir,[string]$InstallDir=$PSScriptRoot)
. (Join-Path $PSScriptRoot 'windows-lifecycle-common.ps1')
try{
 $oldDir=[IO.Path]::GetFullPath($InstallDir);$newDir=[IO.Path]::GetFullPath($NewKitDir);Assert-Portal ($oldDir-ne$newDir) 'New kit must be a separate directory'
 $oldEnv=Read-PortalEnv (Join-Path $oldDir '.env');$oldVersion=Read-PortalSetting $oldEnv 'PORTAL_VERSION';$oldKit=Assert-PortalKit $oldDir;$newKit=Assert-PortalKit $newDir;Assert-PortalDocker $newKit.Architecture
 Assert-Portal ($oldKit.Version-eq$oldVersion) 'Current kit/.env version mismatch';Assert-Portal ($oldVersion-ne$newKit.Version) 'Already on target version';Assert-Portal ($oldKit.Architecture-eq$newKit.Architecture) 'Cross-architecture upgrade unsupported';Assert-Portal (-not(Test-Path (Join-Path $newDir '.env'))) 'New kit already contains .env'
 Assert-Portal ((Read-PortalSetting $oldEnv 'PORTAL_CONTOUR')-in @('SOURCE','TARGET')) 'Invalid contour'
 $out=& (Join-Path $oldDir 'backup.ps1') -InstallDir $oldDir;Assert-Portal ($LASTEXITCODE-eq 0) 'Pre-upgrade backup failed';$backup=[string]($out|Where-Object{$_ -is [string] -and $_.Trim()-match'\.zip$'}|Select-Object -Last 1);Assert-Portal ($backup-and(Test-Path $backup)) 'Pre-upgrade backup missing'
 Invoke-PortalDocker @('load','-i',(Join-Path $newDir 'images/backend.tar'))|Out-Null;Invoke-PortalDocker @('load','-i',(Join-Path $newDir 'images/frontend.tar'))|Out-Null
 foreach($image in @("harbor-transfer-portal-backend:$($newKit.Version)","harbor-transfer-portal-frontend:$($newKit.Version)")){Invoke-PortalDocker @('image','inspect',$image)|Out-Null}
 Write-PortalAtomic (Join-Path $newDir '.env') (Set-PortalEnvVersion $oldEnv $newKit.Version);$timeout=180;if($env:PORTAL_INSTALL_TIMEOUT_SECONDS){Assert-Portal ([int]::TryParse($env:PORTAL_INSTALL_TIMEOUT_SECONDS,[ref]$timeout)-and$timeout-gt 0) 'Invalid timeout'}
 try{Invoke-PortalDocker ((Get-PortalComposeArgs $newDir (Join-Path $newDir '.env'))+@('up','-d','--no-build','--pull','never','--wait','--wait-timeout',[string]$timeout))|Out-Null;Write-Host "UPGRADE_OK: $oldVersion -> $($newKit.Version)";Write-Host "Backup: $backup";Write-Host "Previous kit retained: $oldDir"}
 catch{[Console]::Error.WriteLine("Upgrade failed; restoring $oldVersion");& (Join-Path $oldDir 'restore.ps1') -BackupArchive $backup -ConfirmRestore -InstallDir $oldDir;if($LASTEXITCODE-ne 0){[Console]::Error.WriteLine("ROLLBACK_FAILED; preserve both kits and $backup");exit 3};[Console]::Error.WriteLine("ROLLBACK_OK from $backup");exit 2}
}catch{[Console]::Error.WriteLine($_.Exception.Message);exit 1}
