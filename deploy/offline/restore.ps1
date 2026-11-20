[CmdletBinding()]
param([Parameter(Mandatory=$true)][string]$BackupArchive,[switch]$ConfirmRestore,[string]$InstallDir=$PSScriptRoot)
. (Join-Path $PSScriptRoot 'windows-lifecycle-common.ps1')
$tmp=$null
try{
 Assert-Portal $ConfirmRestore 'Restore is destructive; pass -ConfirmRestore explicitly';$kit=Assert-PortalKit $InstallDir;Assert-PortalDocker $kit.Architecture
 $archive=(Resolve-Path -LiteralPath $BackupArchive).Path;Assert-Portal (Test-Path "$archive.sha256" -PathType Leaf) 'Backup sidecar required'
 $side=(Get-Content "$archive.sha256" -Raw).Trim();Assert-Portal ($side-cmatch '^([0-9a-f]{64})  ([^\r\n]+)$') 'Invalid sidecar';$eh=$Matches[1];$en=$Matches[2]
 Assert-Portal ($en-ceq[IO.Path]::GetFileName($archive)) 'Sidecar filename mismatch';Assert-Portal ((Get-FileHash $archive -Algorithm SHA256).Hash.Equals($eh,[StringComparison]::OrdinalIgnoreCase)) 'Backup archive checksum mismatch'
 Add-Type -AssemblyName System.IO.Compression;Add-Type -AssemblyName System.IO.Compression.FileSystem;$zip=[IO.Compression.ZipFile]::OpenRead($archive)
 try{$names=@($zip.Entries|ForEach-Object{$_.FullName});$allow=@('.env','CHECKSUMS.sha256','backup-metadata.txt','portal-data.tar.gz');Assert-Portal ((($names|Sort-Object)-join'|')-ceq(($allow|Sort-Object)-join'|')) 'Backup layout is not allowlisted';foreach($e in $zip.Entries){Assert-Portal (-not$e.FullName.Contains('/')-and-not$e.FullName.Contains('\')) 'Unsafe archive path'};$tmp=Join-Path ([IO.Path]::GetTempPath()) ('htp-restore-'+[guid]::NewGuid().ToString('N'));[IO.Compression.ZipFile]::ExtractToDirectory($archive,$tmp)}finally{$zip.Dispose()}
 $manifest=Get-Content (Join-Path $tmp 'CHECKSUMS.sha256');Assert-Portal ($manifest.Count-eq 3) 'Expected three backup hashes'
 foreach($line in $manifest){Assert-Portal ($line-cmatch '^([0-9a-f]{64})  (\.env|backup-metadata\.txt|portal-data\.tar\.gz)$') 'Invalid backup manifest entry';$h=$Matches[1];$n=$Matches[2];$a=(Get-FileHash (Join-Path $tmp $n) -Algorithm SHA256).Hash;Assert-Portal ($a.Equals($h,[StringComparison]::OrdinalIgnoreCase)) "Backup hash mismatch: $n"}
 $meta=[IO.File]::ReadAllText((Join-Path $tmp 'backup-metadata.txt'));$v=Read-PortalSetting $meta 'version';$role=Read-PortalSetting $meta 'contour';Assert-Portal ((Read-PortalSetting $meta 'product')-eq'harbor-transfer-portal') 'Backup product mismatch';Assert-Portal ($v-eq$kit.Version) 'Restore requires matching kit version';Assert-Portal ($role-in @('SOURCE','TARGET')) 'Invalid backup contour'
 $backupEnv=Read-PortalEnv (Join-Path $tmp '.env');Assert-Portal ((Read-PortalSetting $backupEnv 'PORTAL_VERSION')-eq$v) 'Backup env version mismatch';Assert-Portal ((Read-PortalSetting $backupEnv 'PORTAL_CONTOUR')-eq$role) 'Backup env contour mismatch'
 $dir=[IO.Path]::GetFullPath($InstallDir);$envPath=Join-Path $dir '.env';if(Test-Path $envPath){$cur=Read-PortalEnv $envPath;Assert-Portal ((Read-PortalSetting $cur 'PORTAL_VERSION')-eq$v) 'Restore refuses version mismatch';Assert-Portal ((Read-PortalSetting $cur 'PORTAL_CONTOUR')-eq$role) 'Restore refuses contour mismatch'}
 foreach($image in @("harbor-transfer-portal-backend:$v","harbor-transfer-portal-frontend:$v")){Invoke-PortalDocker @('image','inspect',$image)|Out-Null;$a=(Invoke-PortalDocker @('image','inspect','--format','{{.Architecture}}',$image) -Capture|Select-Object -First 1).Trim();if($a-in @('x86_64','x64')){$a='amd64'};if($a-eq'aarch64'){$a='arm64'};Assert-Portal ($a-eq$kit.Architecture) "Image architecture mismatch: $image"}
 $mount='type=bind,source='+$tmp+',target=/backup';$listing=Invoke-PortalDocker @('run','--rm','--pull','never','--network','none','--entrypoint','tar','--mount',$mount,"harbor-transfer-portal-backend:$v",'-tzf','/backup/portal-data.tar.gz') -Capture
 foreach($entry in $listing){$m=$entry.Trim();Assert-Portal (-not[IO.Path]::IsPathRooted($m)-and-not($m-match'(^|[\\/])\.\.([\\/]|$)')) "Unsafe data archive path: $m"}
 $volume='harbor-transfer-portal_portal-data';Assert-Portal ((Read-PortalSetting $meta 'volume') -eq $volume) 'Backup volume identity mismatch';$compose=Get-PortalComposeArgs $dir $envPath
 Invoke-PortalDocker ($compose+@('down','--remove-orphans'))|Out-Null;$exists=$true;try{Invoke-PortalDocker @('volume','inspect',$volume)|Out-Null}catch{$exists=$false};if(-not$exists){Invoke-PortalDocker @('volume','create',$volume)|Out-Null}
 Invoke-PortalDocker @('run','--rm','--pull','never','--network','none','-v',($volume+':/app/data'),'--entrypoint','sh',"harbor-transfer-portal-backend:$v",'-c','find /app/data -mindepth 1 -maxdepth 1 -exec rm -rf -- {} +')|Out-Null
 Invoke-PortalDocker @('run','--rm','--pull','never','--network','none','-v',($volume+':/app/data'),'--mount',$mount,'--entrypoint','tar',"harbor-transfer-portal-backend:$v",'-xzf','/backup/portal-data.tar.gz','-C','/app/data')|Out-Null
 Invoke-PortalDocker @('run','--rm','--pull','never','--network','none','-v',($volume+':/app/data'),'--entrypoint','chown',"harbor-transfer-portal-backend:$v",'-R','10001:10001','/app/data')|Out-Null
 Write-PortalAtomic $envPath $backupEnv;$timeout=180;if($env:PORTAL_INSTALL_TIMEOUT_SECONDS){Assert-Portal ([int]::TryParse($env:PORTAL_INSTALL_TIMEOUT_SECONDS,[ref]$timeout)-and$timeout-gt 0) 'Invalid timeout'}
 Invoke-PortalDocker ((Get-PortalComposeArgs $dir $envPath)+@('up','-d','--no-build','--pull','never','--wait','--wait-timeout',[string]$timeout))|Out-Null;Write-Host "Restore completed: $role version $v"
}catch{[Console]::Error.WriteLine($_.Exception.Message);exit 1}
finally{if($tmp -and(Test-Path $tmp)){Remove-Item $tmp -Recurse -Force}}
