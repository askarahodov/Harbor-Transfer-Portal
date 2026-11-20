[CmdletBinding()]
param([string]$InstallDir=$PSScriptRoot)
. (Join-Path $PSScriptRoot 'windows-lifecycle-common.ps1')
try {
 $kit=Assert-PortalKit $InstallDir
 $dir=$kit.Root
 $envPath=Join-Path $dir '.env'
 $envText=Read-PortalEnv $envPath
 Assert-Portal ((Read-PortalSetting $envText 'PORTAL_VERSION') -eq $kit.Version) 'Kit/.env version mismatch'
 Assert-PortalDocker $kit.Architecture
 Invoke-PortalDocker ((Get-PortalComposeArgs $dir $envPath)+@('stop')) | Out-Null
 Write-Host 'Portal services stopped. Containers, persistent volume and .env were preserved.'
} catch { [Console]::Error.WriteLine($_.Exception.Message); exit 1 }
