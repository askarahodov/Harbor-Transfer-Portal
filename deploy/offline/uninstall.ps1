[CmdletBinding()]
param([string]$InstallDir=$PSScriptRoot,[switch]$PurgeData,[string]$ConfirmPurge='')
. (Join-Path $PSScriptRoot 'windows-lifecycle-common.ps1')
try{$kit=Assert-PortalKit $InstallDir;$dir=$kit.Root;$envPath=Join-Path $dir '.env';$envText=Read-PortalEnv $envPath;Assert-Portal ((Read-PortalSetting $envText 'PORTAL_VERSION') -eq $kit.Version) 'Kit/.env version mismatch';Assert-PortalDocker $kit.Architecture;$compose=Get-PortalComposeArgs $dir $envPath
 if($PurgeData){Assert-Portal ($ConfirmPurge-ceq'DELETE_PORTAL_DATA') 'Purge requires -ConfirmPurge DELETE_PORTAL_DATA';Write-Warning 'Persistent Portal data will be permanently deleted';Invoke-PortalDocker ($compose+@('down','--remove-orphans','--volumes'))|Out-Null;Write-Host '.env and kit preserved; volume deleted'}
 else{Invoke-PortalDocker ($compose+@('down','--remove-orphans'))|Out-Null;Write-Host 'Portal stopped; persistent volume and .env preserved'}
}catch{[Console]::Error.WriteLine($_.Exception.Message);exit 1}
