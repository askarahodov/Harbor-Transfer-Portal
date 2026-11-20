[CmdletBinding()]
param([string]$InstallDir=$PSScriptRoot,[switch]$PurgeData,[string]$ConfirmPurge='')
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
try{$kit=Assert-PortalKit $InstallDir;$dir=$kit.Root;$envPath=Join-Path $dir '.env';$envText=Read-PortalEnv $envPath;Assert-Portal ((Read-PortalSetting $envText 'PORTAL_VERSION') -eq $kit.Version) 'Kit/.env version mismatch';Assert-PortalDocker $kit.Architecture;$compose=Get-PortalComposeArgs $dir $envPath
 if($PurgeData){Assert-Portal ($ConfirmPurge-ceq'DELETE_PORTAL_DATA') 'Purge requires -ConfirmPurge DELETE_PORTAL_DATA';Write-Warning 'Persistent Portal data will be permanently deleted';Invoke-PortalDocker ($compose+@('down','--remove-orphans','--volumes'))|Out-Null;Write-Host '.env and kit preserved; volume deleted'}
 else{Invoke-PortalDocker ($compose+@('down','--remove-orphans'))|Out-Null;Write-Host 'Portal stopped; persistent volume and .env preserved'}
}catch{[Console]::Error.WriteLine($_.Exception.Message);exit 1}
