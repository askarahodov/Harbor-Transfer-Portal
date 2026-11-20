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
 foreach ($image in @("harbor-transfer-portal-backend:$($kit.Version)","harbor-transfer-portal-frontend:$($kit.Version)")) {
  Invoke-PortalDocker @('image','inspect',$image) | Out-Null
  $arch=(Invoke-PortalDocker @('image','inspect','--format','{{.Architecture}}',$image) -Capture | Select-Object -First 1).Trim()
  if ($arch -in @('x86_64','x64')) { $arch='amd64' }
  if ($arch -eq 'aarch64') { $arch='arm64' }
  Assert-Portal ($arch -eq $kit.Architecture) "Image architecture mismatch: $image"
 }
 $compose=Get-PortalComposeArgs $dir $envPath
 Invoke-PortalDocker ($compose+@('config','-q')) | Out-Null
 $timeout=180
 if ($env:PORTAL_INSTALL_TIMEOUT_SECONDS) { Assert-Portal ([int]::TryParse($env:PORTAL_INSTALL_TIMEOUT_SECONDS,[ref]$timeout) -and $timeout -gt 0) 'Invalid install timeout' }
 Invoke-PortalDocker ($compose+@('up','-d','--no-build','--pull','never','--wait','--wait-timeout',[string]$timeout)) | Out-Null
 Write-Host "Harbor Transfer Portal $($kit.Version) is healthy."
} catch { [Console]::Error.WriteLine($_.Exception.Message); exit 1 }
