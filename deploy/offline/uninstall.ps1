[CmdletBinding()]
param([string]$InstallDir=$PSScriptRoot,[switch]$PurgeData,[string]$ConfirmPurge='')
. (Join-Path $PSScriptRoot 'windows-lifecycle-common.ps1')
try{$dir=[IO.Path]::GetFullPath($InstallDir);$envPath=Join-Path $dir '.env';$null=Read-PortalEnv $envPath;Assert-PortalDocker;$compose=Get-PortalComposeArgs $dir $envPath
 if($PurgeData){Assert-Portal ($ConfirmPurge-ceq'DELETE_PORTAL_DATA') 'Purge requires -ConfirmPurge DELETE_PORTAL_DATA';Write-Warning 'Persistent Portal data will be permanently deleted';Invoke-PortalDocker ($compose+@('down','--remove-orphans','--volumes'))|Out-Null;Write-Host '.env and kit preserved; volume deleted'}
 else{Invoke-PortalDocker ($compose+@('down','--remove-orphans'))|Out-Null;Write-Host 'Portal stopped; persistent volume and .env preserved'}
}catch{[Console]::Error.WriteLine($_.Exception.Message);exit 1}
