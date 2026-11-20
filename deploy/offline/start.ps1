[CmdletBinding()]
param([string]$InstallDir=$PSScriptRoot)
# Verify the shared executable helper before loading it; do not execute release code first.
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
$bootstrapEntries = @($bootstrapLines | Where-Object { $_.EndsWith('  windows-lifecycle-common.ps1', [StringComparison]::Ordinal) })
if ($bootstrapEntries.Count -ne 1) { throw 'Shared lifecycle helper must have exactly one checksum entry' }
$bootstrapEntry = [string]$bootstrapEntries[0]
$bootstrapExpected = $bootstrapEntry.Substring(0, [Math]::Min(64, $bootstrapEntry.Length))
if ($bootstrapEntry.Length -ne (64 + 2 + 'windows-lifecycle-common.ps1'.Length) -or $bootstrapEntry.Substring(64, 2) -ne '  ' -or $bootstrapExpected -cnotmatch '^[0-9a-f]{64}') {
    throw 'Shared lifecycle helper checksum entry is invalid'
}
$bootstrapActual = (Get-FileHash -LiteralPath $bootstrapHelper -Algorithm SHA256).Hash
if (-not $bootstrapActual.Equals($bootstrapExpected, [StringComparison]::OrdinalIgnoreCase)) {
    throw 'Shared lifecycle helper checksum mismatch; refusing to execute release code'
}
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
