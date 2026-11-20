param(
 [Parameter(Mandatory=$true)][ValidateSet('Install','Start','Stop','Backup','Restore','Upgrade','Uninstall')][string]$Action,
 [string]$InstallPath,[string]$BackupPath,[string]$BackupDirectory,[string]$PreviousInstallPath,
 [switch]$ConfirmRestore,[switch]$PurgeData,[switch]$ConfirmPurge
)
Set-StrictMode -Version Latest
$ErrorActionPreference='Stop'
$script:Root=[IO.Path]::GetFullPath((Split-Path -Parent $MyInvocation.MyCommand.Path))
$script:Volume='harbor-transfer-portal_portal-data'

function Assert([bool]$Ok,[string]$Message){if(-not $Ok){throw $Message}}
function Assert-File([string]$Path,[string]$Label){
 Assert (Test-Path -LiteralPath $Path -PathType Leaf) "$Label is missing: $Path"
 Assert (([IO.File]::GetAttributes($Path) -band [IO.FileAttributes]::ReparsePoint) -eq 0) "$Label must not be a symlink: $Path"
}
function Assert-Directory([string]$Path,[string]$Label){
 Assert (Test-Path -LiteralPath $Path -PathType Container) "$Label is missing: $Path"
 Assert (([IO.File]::GetAttributes($Path) -band [IO.FileAttributes]::ReparsePoint) -eq 0) "$Label must not be a symlink: $Path"
}
function Setting([string]$Text,[string]$Name,[string]$Default=''){
 $m=[regex]::Matches($Text,"(?m)^$([regex]::Escape($Name))=([^\r\n]*)$")
 Assert ($m.Count -le 1) "Duplicate $Name setting"
 if($m.Count -eq 1){return $m[0].Groups[1].Value.Trim()}
 return $Default
}
function EnvText([string]$Path){Assert-File $Path '.env';return [IO.File]::ReadAllText($Path)}
function Protect-File([string]$Path){
 $who=[Security.Principal.WindowsIdentity]::GetCurrent().Name
 & icacls.exe $Path '/inheritance:r' '/grant:r' ($who+':F') 'SYSTEM:F' *> $null
 Assert ($LASTEXITCODE -eq 0) "Could not restrict secret file permissions: $Path"
}
function Protect-Directory([string]$Path){
 $who=[Security.Principal.WindowsIdentity]::GetCurrent().Name
 & icacls.exe $Path '/inheritance:r' '/grant:r' ($who+':(OI)(CI)F') 'SYSTEM:(OI)(CI)F' *> $null
 Assert ($LASTEXITCODE -eq 0) "Could not restrict backup directory permissions: $Path"
}
function Write-Private([string]$Path,[string]$Text){
 $tmp=Join-Path (Split-Path -Parent $Path) ('.htp-'+[Guid]::NewGuid().ToString('N')+'.tmp')
 try{
  [IO.File]::WriteAllText($tmp,$Text,([Text.UTF8Encoding]::new($false)))
  if(Test-Path -LiteralPath $Path){Assert-File $Path 'Destination';[IO.File]::Replace($tmp,$Path,$null)}else{[IO.File]::Move($tmp,$Path)}
  Protect-File $Path
 }finally{if(Test-Path -LiteralPath $tmp){Remove-Item -LiteralPath $tmp -Force -ErrorAction SilentlyContinue}}
}
function Kit([string]$Path,[switch]$Windows){
 Assert-Directory $Path 'Release kit'
 foreach($f in @('release-version.txt','release-arch.txt','CHECKSUMS.sha256','compose.yaml','.env.example','images/backend.tar','images/frontend.tar')){Assert-File (Join-Path $Path $f) $f}
 $v=[IO.File]::ReadAllText((Join-Path $Path 'release-version.txt')).Trim()
 $a=[IO.File]::ReadAllText((Join-Path $Path 'release-arch.txt')).Trim()
 Assert ($v -match '^[A-Za-z0-9._-]+$') 'Invalid release version'
 Assert ($a -in @('amd64','arm64')) "Unsupported release architecture: $a"
 $seen=New-Object 'System.Collections.Generic.HashSet[string]' ([StringComparer]::Ordinal)
 foreach($line in [IO.File]::ReadAllLines((Join-Path $Path 'CHECKSUMS.sha256'))){
  if([string]::IsNullOrWhiteSpace($line)){continue}
  Assert ($line -cmatch '^([0-9a-f]{64})  ([A-Za-z0-9._/-]+)$') "Invalid checksum entry: $line"
  $hash=$Matches[1];$rel=$Matches[2]
  Assert (-not $rel.StartsWith('/') -and -not ($rel.Split('/') -contains '..') -and -not ($rel.Split('/') -contains '.')) "Unsafe checksum path: $rel"
  Assert ($seen.Add($rel)) "Duplicate checksum entry: $rel"
  $file=[IO.Path]::GetFullPath((Join-Path $Path ($rel -replace '/', [IO.Path]::DirectorySeparatorChar)))
  $prefix=[IO.Path]::GetFullPath($Path).TrimEnd('\','/')+[IO.Path]::DirectorySeparatorChar
  Assert ($file.StartsWith($prefix,[StringComparison]::OrdinalIgnoreCase)) "Checksum path escapes kit: $rel"
  Assert-File $file $rel
  $actual=(Get-FileHash -LiteralPath $file -Algorithm SHA256).Hash
  Assert ($actual.Equals($hash,[StringComparison]::OrdinalIgnoreCase)) "SHA-256 mismatch: $rel"
 }
 foreach($f in @('release-version.txt','release-arch.txt','compose.yaml','.env.example','images/backend.tar','images/frontend.tar')){Assert ($seen.Contains($f)) "Required file is not checksummed: $f"}
 if($Windows){
  foreach($f in @('windows-lifecycle.ps1','install.cmd','start.cmd','stop.cmd','backup.cmd','restore.cmd','upgrade.cmd','uninstall.cmd')){
   Assert ($seen.Contains($f)) "Windows lifecycle file is not checksummed: $f";Assert-File (Join-Path $Path $f) $f
  }
 }
 return [pscustomobject]@{Path=[IO.Path]::GetFullPath($Path);Version=$v;Arch=$a}
}
function Quote-Arg([string]$s){
 if($s.Length -eq 0){return '""'};if($s -notmatch '[\s"]'){return $s}
 $b=New-Object Text.StringBuilder;[void]$b.Append('"');$slashes=0
 foreach($ch in $s.ToCharArray()){
  if($ch -eq '\'){$slashes++;continue}
  if($ch -eq '"'){if($slashes -gt 0){[void]$b.Append(('\'*(2*$slashes+1)))}else{[void]$b.Append('\')};[void]$b.Append('"');$slashes=0;continue}
  if($slashes -gt 0){[void]$b.Append(('\'*$slashes));$slashes=0};[void]$b.Append($ch)
 }
 if($slashes -gt 0){[void]$b.Append(('\'*(2*$slashes)))};[void]$b.Append('"');return $b.ToString()
}
function Docker-Output([string[]]$DockerArguments,[switch]$AllowFailure){
 $old=$ErrorActionPreference;$ErrorActionPreference='Continue'
 try{$out=@(& docker @DockerArguments 2>&1);$code=$LASTEXITCODE}finally{$ErrorActionPreference=$old}
 $text=($out|ForEach-Object{[string]$_}) -join [Environment]::NewLine
 if($code -ne 0){if($AllowFailure){return $null};throw "Docker failed ($code): docker $($DockerArguments -join ' ') $text"}
 return $text.Trim()
}
function Docker([string[]]$DockerArguments,[switch]$Quiet){$out=Docker-Output $DockerArguments;if(-not $Quiet -and $out){Write-Host $out}}
function Docker-Process([string[]]$DockerArguments,[bool]$InputStream,[bool]$OutputStream){
 $cmd=Get-Command docker -ErrorAction SilentlyContinue;Assert ($null -ne $cmd) 'Docker CLI is not installed'
 $p=New-Object Diagnostics.ProcessStartInfo;$p.FileName=$cmd.Source
 $p.Arguments=(($DockerArguments|ForEach-Object{Quote-Arg ([string]$_)}) -join ' ')
 $p.UseShellExecute=$false;$p.CreateNoWindow=$true;$p.RedirectStandardInput=$InputStream;$p.RedirectStandardOutput=$OutputStream;$p.RedirectStandardError=$true
 return $p
}
function Docker-ToFile([string[]]$DockerArguments,[string]$Path){
 $p=New-Object Diagnostics.Process;$p.StartInfo=Docker-Process $DockerArguments $false $true;Assert $p.Start() 'Could not start Docker snapshot process'
 $err=$p.StandardError.ReadToEndAsync()
 try{$f=[IO.File]::Create($Path);try{$p.StandardOutput.BaseStream.CopyTo($f)}finally{$f.Dispose()};$p.WaitForExit();$e=$err.Result;Assert ($p.ExitCode -eq 0) "Docker snapshot failed ($($p.ExitCode)): $e"}
 catch{if(-not $p.HasExited){$p.Kill();$p.WaitForExit()};if(Test-Path $Path){Remove-Item $Path -Force};throw}
 finally{$p.Dispose()}
}
function Docker-FromFile([string[]]$DockerArguments,[string]$Path){
 $p=New-Object Diagnostics.Process;$p.StartInfo=Docker-Process $DockerArguments $true $false;Assert $p.Start() 'Could not start Docker restore process'
 $err=$p.StandardError.ReadToEndAsync()
 try{$f=[IO.File]::OpenRead($Path);try{$f.CopyTo($p.StandardInput.BaseStream)}finally{$f.Dispose();$p.StandardInput.Close()};$p.WaitForExit();$e=$err.Result;Assert ($p.ExitCode -eq 0) "Docker restore failed ($($p.ExitCode)): $e"}
 catch{if(-not $p.HasExited){$p.Kill();$p.WaitForExit()};throw}
 finally{$p.Dispose()}
}
function Tool-Output([string]$Name,[string[]]$ToolArguments){
 $tool=Get-Command $Name -ErrorAction SilentlyContinue;Assert ($null -ne $tool) "$Name is required"
 $old=$ErrorActionPreference;$ErrorActionPreference='Continue'
 try{$out=@(& $tool.Source @ToolArguments 2>&1);$code=$LASTEXITCODE}finally{$ErrorActionPreference=$old}
 $text=($out|ForEach-Object{[string]$_}) -join [Environment]::NewLine
 Assert ($code -eq 0) "$Name failed ($code): $text";return $text.Trim()
}
function Normalize-Arch([string]$s) {
 switch ($s.Trim().ToLowerInvariant()) {
  { $_ -in @('amd64','x86_64') } { return 'amd64' }
  { $_ -in @('arm64','aarch64') } { return 'arm64' }
  default { throw "Unsupported Docker architecture: $s" }
 }
}
function Docker-Host([string]$Expected=''){
 Assert ($null -ne (Get-Command docker -ErrorAction SilentlyContinue)) 'Docker Desktop/CLI is not installed'
 Assert ((Docker-Output @('info','--format','{{.OSType}}')) -eq 'linux') 'Docker Desktop must use Linux containers mode'
 $arch=Normalize-Arch (Docker-Output @('info','--format','{{.Architecture}}'))
 if($Expected){Assert ($arch -eq $Expected) "Release architecture $Expected does not match Docker Engine $arch"}
 Docker-Output @('compose','version')|Out-Null;return $arch
}
function Compose-Args([string]$Dir){return @('compose','--env-file',(Join-Path $Dir '.env'),'-f',(Join-Path $Dir 'compose.yaml'))}
function Compose-Check([string]$Dir){Docker-Output ((Compose-Args $Dir)+@('config','-q'))|Out-Null}
function Env-Settings([string]$Dir){
 $text=EnvText (Join-Path $Dir '.env');$v=Setting $text 'PORTAL_VERSION';$c=Setting $text 'PORTAL_CONTOUR'
 Assert ($v -match '^[A-Za-z0-9._-]+$') '.env has no valid PORTAL_VERSION';Assert ($c -in @('SOURCE','TARGET')) '.env has no valid PORTAL_CONTOUR'
 return [pscustomobject]@{Text=$text;Version=$v;Contour=$c}
}
function Check-Transport([string]$text){
 $scheme=Setting $text 'PORTAL_BROWSER_SCHEME' 'http';$bind=Setting $text 'PORTAL_HTTP_BIND' '127.0.0.1';$port=Setting $text 'PORTAL_HTTP_PORT' '8080'
 Assert ($scheme -in @('http','https')) 'PORTAL_BROWSER_SCHEME must be http or https'
 Assert (-not ($scheme -eq 'http' -and $bind -ne '127.0.0.1')) 'Non-loopback listener requires PORTAL_BROWSER_SCHEME=https'
 $n=0;Assert ([int]::TryParse($port,[ref]$n) -and $n -ge 1 -and $n -le 65535) 'PORTAL_HTTP_PORT must be between 1 and 65535'
}
function Timeout-Seconds{$n=180;if($env:PORTAL_INSTALL_TIMEOUT_SECONDS){Assert ([int]::TryParse($env:PORTAL_INSTALL_TIMEOUT_SECONDS,[ref]$n) -and $n -gt 0) 'PORTAL_INSTALL_TIMEOUT_SECONDS must be a positive integer'};return $n}
function Check-Images([string]$Version,[string]$Arch){
 foreach($image in @("harbor-transfer-portal-backend:$Version","harbor-transfer-portal-frontend:$Version")){
  Docker-Output @('image','inspect',$image)|Out-Null
  $got=Normalize-Arch (Docker-Output @('image','inspect','--format','{{.Architecture}}',$image))
  Assert ($got -eq $Arch) "Image architecture mismatch for $image: $got"
  $label=Docker-Output @('image','inspect','--format','{{ index .Config.Labels "org.opencontainers.image.version" }}',$image)
  Assert ($label -eq $Version) "Image release label mismatch for $image: $label"
 }
}
function Load-Images([string]$Dir,[string]$Version,[string]$Arch){
 Docker @('load','-i',(Join-Path $Dir 'images/backend.tar'));Docker @('load','-i',(Join-Path $Dir 'images/frontend.tar'));Check-Images $Version $Arch
}
function New-Environment([string]$Dir,[string]$Version){
 $path=Join-Path $Dir '.env'
 if(Test-Path -LiteralPath $path){$text=EnvText $path;Assert ((Setting $text 'PORTAL_VERSION') -ceq $Version) "Existing .env is configured for another version; use upgrade.cmd";Write-Host 'Existing .env preserved.';return $text}
 $contour=if($env:PORTAL_CONTOUR){$env:PORTAL_CONTOUR}else{'SOURCE'};Assert ($contour -cin @('SOURCE','TARGET')) 'PORTAL_CONTOUR must be SOURCE or TARGET'
 $template=[IO.File]::ReadAllText((Join-Path $Dir '.env.example'))
 foreach($pair in @(@('PORTAL_CONTOUR',$contour),@('PORTAL_VERSION',$Version))){
  Assert ([regex]::IsMatch($template,"(?m)^$($pair[0])=")) "Template missing $($pair[0])"
  $template=[regex]::Replace($template,"(?m)^$($pair[0])=[^\r\n]*",($pair[0]+'='+$pair[1]))
 }
 Assert ($template.Contains('# JWT_SECRET=')) 'Template missing JWT_SECRET placeholder'
 $rng=[Security.Cryptography.RandomNumberGenerator]::Create()
 try{$bytes=New-Object byte[] 32;$rng.GetBytes($bytes);$secret=([BitConverter]::ToString($bytes)).Replace('-','').ToLowerInvariant()}finally{$rng.Dispose()}
 $template=$template.Replace('# JWT_SECRET=',"JWT_SECRET=$secret");Write-Private $path $template;return $template
}
function Start-Portal([string]$Dir,[string]$Version){
 $text=EnvText (Join-Path $Dir '.env');Check-Transport $text;Compose-Check $Dir
 Docker ((Compose-Args $Dir)+@('up','-d','--no-build','--pull','never','--wait','--wait-timeout',[string](Timeout-Seconds)))
 Write-Host ("Portal "+$Version+" is healthy.")
}
function Archive-Lines([string]$Path,[switch]$VerboseListing){
 $tarArguments=if($VerboseListing){@('-tvzf',$Path)}else{@('-tzf',$Path)}
 $text=Tool-Output 'tar.exe' $tarArguments;return @($text -split '\r?\n'|Where-Object{-not [string]::IsNullOrWhiteSpace($_)})
}
function Check-DataArchive([string]$Path){
 $members=Archive-Lines $Path;Assert ($members.Count -gt 0) 'Persistent-data archive is empty'
 foreach($m in $members){$s=$m.Replace('\','/');Assert (-not $s.StartsWith('/') -and $s -notmatch '^[A-Za-z]:' -and -not ($s.Split('/') -contains '..')) "Unsafe archive path: $m"}
 foreach($line in (Archive-Lines $Path -VerboseListing)){Assert ($line.Substring(0,1) -in @('-','d')) 'Persistent-data archive contains links or unsupported member types'}
}
function Backup-Metadata([string]$Path){
 $d=@{};foreach($line in [IO.File]::ReadAllLines($Path)){if($line -match '^([a-z_]+)=(.*)$'){Assert (-not $d.ContainsKey($Matches[1])) 'Duplicate backup metadata key';$d[$Matches[1]]=$Matches[2]}};return $d
}
function Verify-BackupChecksums([string]$Dir){
 $expected=@('.env','backup-metadata.txt','portal-data.tar.gz');$lines=@([IO.File]::ReadAllLines((Join-Path $Dir 'CHECKSUMS.sha256'))|Where-Object{-not [string]::IsNullOrWhiteSpace($_)})
 Assert ($lines.Count -eq 3) 'Backup checksum manifest must contain exactly three entries';$seen=New-Object 'System.Collections.Generic.HashSet[string]' ([StringComparer]::Ordinal)
 foreach($line in $lines){
  Assert ($line -cmatch '^([0-9a-f]{64})  ([A-Za-z0-9._/-]+)$') "Invalid backup checksum entry: $line";$hash=$Matches[1];$name=$Matches[2]
  Assert ($expected -ccontains $name -and $seen.Add($name)) "Unexpected or duplicate backup member: $name"
  Assert-File (Join-Path $Dir $name) $name;$actual=(Get-FileHash -LiteralPath (Join-Path $Dir $name) -Algorithm SHA256).Hash
  Assert ($actual.Equals($hash,[StringComparison]::OrdinalIgnoreCase)) "Backup checksum mismatch: $name"
 }
 foreach($name in $expected){Assert ($seen.Contains($name)) "Backup checksum missing: $name"}
}
function New-Backup([string]$Dir,[string]$Destination,[switch]$LeavePaused,[ref]$WasPaused){
 Assert-Directory $Dir 'Installation directory';Assert-File (Join-Path $Dir 'compose.yaml') 'Compose file'
 $settings=Env-Settings $Dir;$compose=Compose-Args $Dir;Compose-Check $Dir
 $backend="harbor-transfer-portal-backend:$($settings.Version)";Docker-Output @('image','inspect',$backend)|Out-Null;Docker-Output @('volume','inspect',$script:Volume)|Out-Null
 if(-not $Destination){$Destination=Join-Path $Dir 'backups'}
 if(-not (Test-Path -LiteralPath $Destination)){New-Item -ItemType Directory -Path $Destination -Force|Out-Null}
 Assert-Directory $Destination 'Backup directory';Protect-Directory $Destination;$Destination=[IO.Path]::GetFullPath($Destination)
 $stamp=[DateTime]::UtcNow.ToString('yyyyMMddTHHmmssZ');$name="harbor-transfer-portal-backup-v$($settings.Version)-$stamp.tar.gz";$archive=Join-Path $Destination $name
 Assert (-not (Test-Path -LiteralPath $archive)) "Backup already exists: $archive"
 $tmp=Join-Path $Destination ('.htp-backup-'+[Guid]::NewGuid().ToString('N'));New-Item -ItemType Directory -Path $tmp|Out-Null;Protect-Directory $tmp
 $paused=$false;$WasPaused.Value=$false
 try{
  $running=Docker-Output ($compose+@('ps','--status','running','-q'))
  if($running){Docker ($compose+@('pause')) -Quiet;$paused=$true}
  $data=Join-Path $tmp 'portal-data.tar.gz'
  Docker-ToFile @('run','--rm','--pull','never','--network','none','-v',"$($script:Volume):/app/data:ro",'--entrypoint','tar',$backend,'-C','/app/data','-czf','-','.') $data
  Copy-Item -LiteralPath (Join-Path $Dir '.env') -Destination (Join-Path $tmp '.env');Protect-File (Join-Path $tmp '.env');Protect-File $data
  $metadata=@('product=harbor-transfer-portal',"version=$($settings.Version)","created_at_utc=$stamp","volume=$script:Volume") -join [Environment]::NewLine
  [IO.File]::WriteAllText((Join-Path $tmp 'backup-metadata.txt'),($metadata+[Environment]::NewLine),([Text.UTF8Encoding]::new($false)))
  $lines=foreach($f in @('.env','backup-metadata.txt','portal-data.tar.gz')){$h=(Get-FileHash -LiteralPath (Join-Path $tmp $f) -Algorithm SHA256).Hash.ToLowerInvariant();"$h  $f"}
  [IO.File]::WriteAllText((Join-Path $tmp 'CHECKSUMS.sha256'),(($lines -join [Environment]::NewLine)+[Environment]::NewLine),([Text.UTF8Encoding]::new($false)))
  Protect-File (Join-Path $tmp 'backup-metadata.txt');Protect-File (Join-Path $tmp 'CHECKSUMS.sha256')
  Tool-Output 'tar.exe' @('-czf',$archive,'-C',$tmp,'.')|Out-Null;Protect-File $archive
  $outer=(Get-FileHash -LiteralPath $archive -Algorithm SHA256).Hash.ToLowerInvariant()
  [IO.File]::WriteAllText(($archive+'.sha256'),($outer+'  '+$name+[Environment]::NewLine),([Text.UTF8Encoding]::new($false)));Protect-File ($archive+'.sha256')
  if($paused -and $LeavePaused){$WasPaused.Value=$true;$paused=$false;Write-Host 'Portal remains paused for transactional upgrade.'}
  elseif($paused){Docker ($compose+@('unpause')) -Quiet;$paused=$false}
  Write-Host 'Backup created; it contains credentials and signing material and must be protected.'
  return $archive
 }finally{
  if($paused){try{Docker ($compose+@('unpause')) -Quiet}catch{Write-Warning 'Failed to unpause Portal after backup; inspect Compose state.'}}
  if(Test-Path $tmp){Remove-Item -LiteralPath $tmp -Recurse -Force -ErrorAction SilentlyContinue}
 }
}
function Restore-Data([string]$Dir,[string]$Archive,[switch]$WindowsKit){
 $kit=Kit $Dir -Windows:$WindowsKit;Assert-File $Archive 'Backup archive';Assert-File ($Archive+'.sha256') 'Backup sidecar'
 $side=@([IO.File]::ReadAllLines($Archive+'.sha256')|Where-Object{-not [string]::IsNullOrWhiteSpace($_)})
 Assert ($side.Count -eq 1 -and $side[0] -cmatch '^([0-9a-fA-F]{64})  (.+)$') 'Backup sidecar must contain one valid checksum entry'
 $hash=$Matches[1];Assert ($Matches[2] -ceq (Split-Path -Leaf $Archive)) 'Backup sidecar names a different archive'
 $actual=(Get-FileHash -LiteralPath $Archive -Algorithm SHA256).Hash;Assert ($actual.Equals($hash,[StringComparison]::OrdinalIgnoreCase)) 'Backup archive SHA-256 mismatch'
 $members=Archive-Lines $Archive;$normalized=foreach($m in $members){$s=$m.Replace('\','/');if($s -in @('.','./')){''}elseif($s.StartsWith('./')){$s.Substring(2)}else{$s}}
 $expected=@('','.env','CHECKSUMS.sha256','backup-metadata.txt','portal-data.tar.gz')
 Assert ($normalized.Count -eq 5) 'Backup archive layout is not the strict allowlist'
 foreach($f in $expected){Assert ($normalized -ccontains $f) "Unexpected or missing archive member: $f"}
 foreach($line in (Archive-Lines $Archive -VerboseListing)){Assert ($line.Substring(0,1) -in @('-','d')) 'Backup archive contains unsupported member type'}
 $tmp=Join-Path ([IO.Path]::GetTempPath()) ('htp-restore-'+[Guid]::NewGuid().ToString('N'));New-Item -ItemType Directory -Path $tmp|Out-Null
 try{
  Tool-Output 'tar.exe' @('-xzf',$Archive,'-C',$tmp)|Out-Null
  foreach($f in @('.env','CHECKSUMS.sha256','backup-metadata.txt','portal-data.tar.gz')){Assert-File (Join-Path $tmp $f) $f}
  Verify-BackupChecksums $tmp;$meta=Backup-Metadata (Join-Path $tmp 'backup-metadata.txt');$saved=EnvText (Join-Path $tmp '.env')
  $v=Setting $saved 'PORTAL_VERSION';$c=Setting $saved 'PORTAL_CONTOUR'
  Assert ($meta['product'] -ceq 'harbor-transfer-portal' -and $meta['version'] -ceq $v) 'Backup product/version metadata mismatch'
  Assert ($v -ceq $kit.Version) "Restore requires matching kit version $($kit.Version); backup is $v"
  Assert ($meta['volume'] -ceq $script:Volume) 'Backup volume identity mismatch';Assert ($c -in @('SOURCE','TARGET')) 'Invalid backup contour'
  $current=Join-Path $Dir '.env'
  if(Test-Path $current){$now=EnvText $current;Assert ((Setting $now 'PORTAL_VERSION') -ceq $v) 'Restore refuses a version change';Assert ((Setting $now 'PORTAL_CONTOUR') -ceq $c) 'Restore refuses a contour change'}
  $data=Join-Path $tmp 'portal-data.tar.gz';Check-DataArchive $data;Docker-Host $kit.Arch|Out-Null;Load-Images $Dir $v $kit.Arch
  Write-Private (Join-Path $Dir '.env') $saved;$compose=Compose-Args $Dir;Docker ($compose+@('down','--remove-orphans'))
  if($null -eq (Docker-Output @('volume','inspect',$script:Volume) -AllowFailure)){Docker-Output @('volume','create',$script:Volume)|Out-Null}
  $image="harbor-transfer-portal-backend:$v"
  Docker @('run','--rm','--pull','never','--network','none','-v',"$($script:Volume):/app/data",'--entrypoint','sh',$image,'-c','find /app/data -mindepth 1 -maxdepth 1 -exec rm -rf -- {} +')
  Docker-FromFile @('run','--rm','-i','--pull','never','--network','none','-v',"$($script:Volume):/app/data",'--entrypoint','tar',$image,'-C','/app/data','-xzf','-') $data
  Docker @('run','--rm','--pull','never','--network','none','-v',"$($script:Volume):/app/data",'--entrypoint','chown',$image,'-R','10001:10001','/app/data')
  Start-Portal $Dir $v;Write-Host "RESTORE_OK: version $v, contour $c."
 }finally{Remove-Item -LiteralPath $tmp -Recurse -Force -ErrorAction SilentlyContinue}
}
function Install-Portal([string]$Dir){
 $kit=Kit $Dir -Windows;Docker-Host $kit.Arch|Out-Null;$text=New-Environment $Dir $kit.Version;Check-Transport $text;Compose-Check $Dir
 Load-Images $Dir $kit.Version $kit.Arch;Start-Portal $Dir $kit.Version
 Write-Host 'INSTALL_OK: local prebuilt images only; no build or pull. Change admin/admin immediately on a new database.'
}
function Start-Action([string]$Dir){
 $kit=Kit $Dir -Windows;Docker-Host $kit.Arch|Out-Null;$settings=Env-Settings $Dir
 Assert ($settings.Version -ceq $kit.Version) 'Installed .env version does not match this release kit'
 Check-Images $settings.Version $kit.Arch;Start-Portal $Dir $settings.Version
}
function Stop-Action([string]$Dir){
 Kit $Dir -Windows|Out-Null;Docker-Host|Out-Null;Env-Settings $Dir|Out-Null;Compose-Check $Dir;Docker ((Compose-Args $Dir)+@('stop'))
 Write-Host 'STOP_OK: containers stopped; .env and persistent volume preserved.'
}
function Backup-Action([string]$Dir,[string]$Destination){
 $kit=Kit $Dir -Windows;Docker-Host $kit.Arch|Out-Null;$settings=Env-Settings $Dir;Assert ($settings.Version -ceq $kit.Version) 'Installed .env version does not match this release kit'
 $paused=$false;$path=New-Backup $Dir $Destination -WasPaused ([ref]$paused);Write-Host "BACKUP_OK: $path"
}
function Restore-Action([string]$Dir,[string]$Archive,[switch]$Confirmed){
 Assert $Confirmed 'Restore is destructive; pass -ConfirmRestore';Assert (-not [string]::IsNullOrWhiteSpace($Archive)) 'Pass -BackupPath'
 Docker-Host|Out-Null;Restore-Data $Dir ([IO.Path]::GetFullPath($Archive)) -WindowsKit
}
function Upgrade-Action([string]$NewDir,[string]$OldDir){
 $new=Kit $NewDir -Windows;Assert (-not [string]::IsNullOrWhiteSpace($OldDir)) 'Upgrade requires -PreviousInstallPath'
 Assert-Directory $OldDir 'Previous installation';$old=Kit $OldDir;$settings=Env-Settings $OldDir
 Assert ($settings.Version -ceq $old.Version) 'Previous kit and .env version mismatch'
 Assert ($old.Arch -ceq $new.Arch) 'Upgrade cannot change architecture';Assert ($old.Version -cne $new.Version) 'Already on this release'
 Assert (-not (Test-Path (Join-Path $NewDir '.env'))) 'New release directory already has .env'
 Docker-Host $new.Arch|Out-Null;Compose-Check $OldDir
 $paused=$false;$backup=$null;$startAttempted=$false
 try{
  $backupRoot=if($env:PORTAL_BACKUP_DIR){$env:PORTAL_BACKUP_DIR}else{Join-Path $OldDir 'backups'}
  $backup=New-Backup $OldDir $backupRoot -LeavePaused -WasPaused ([ref]$paused)
  Assert-File $backup 'Pre-upgrade backup';Assert-File ($backup+'.sha256') 'Pre-upgrade backup sidecar'
  Load-Images $NewDir $new.Version $new.Arch
  $text=EnvText (Join-Path $OldDir '.env')
  $updated=[regex]::Replace($text,'(?m)^PORTAL_VERSION=[^\r\n]*',('PORTAL_VERSION='+$new.Version),1)
  Assert ($updated -cne $text -and (Setting $updated 'PORTAL_VERSION') -ceq $new.Version) 'Could not prepare upgraded .env'
  Write-Private (Join-Path $NewDir '.env') $updated
  $startAttempted=$true;Start-Portal $NewDir $new.Version
  $paused=$false;Write-Host "UPGRADE_OK: $($old.Version) -> $($new.Version)";Write-Host "Pre-upgrade backup: $backup"
 }catch{
  $failure=$_.Exception.Message
  if($startAttempted -and $backup){
   if(Test-Path (Join-Path $NewDir '.env')){Remove-Item -LiteralPath (Join-Path $NewDir '.env') -Force}
   try{Write-Warning 'New release failed; restoring complete previous snapshot.';Restore-Data $OldDir $backup; $paused=$false}
   catch{$paused=$false;throw "ROLLBACK_FAILED: $($_.Exception.Message). Do not start either release; preserve $backup and recover with the matching previous kit."}
   throw "UPGRADE_FAILED / ROLLBACK_OK: $failure"
  }
  throw
 }finally{
  if($paused -and -not $startAttempted){try{Docker ((Compose-Args $OldDir)+@('unpause')) -Quiet}catch{Write-Warning 'Previous Portal remains paused; inspect Docker Compose state.'}}
 }
}
function Uninstall-Action([string]$Dir,[switch]$DeleteData,[switch]$Confirmed){
 $kit=Kit $Dir -Windows;Docker-Host $kit.Arch|Out-Null;$settings=Env-Settings $Dir;Assert ($settings.Version -ceq $kit.Version) 'Installed .env version does not match this release kit';Compose-Check $Dir
 Assert (-not ($Confirmed -and -not $DeleteData)) '-ConfirmPurge requires -PurgeData'
 if($DeleteData){Assert $Confirmed 'Data deletion requires both -PurgeData and -ConfirmPurge';Write-Warning 'Persistent Docker volume will be permanently deleted.';Docker ((Compose-Args $Dir)+@('down','--remove-orphans','--volumes'));Write-Host 'UNINSTALL_OK: volume removed; .env and release files preserved.'}
 else{Docker ((Compose-Args $Dir)+@('down','--remove-orphans'));Write-Host 'UNINSTALL_OK: containers removed; .env, volume and backups preserved.'}
}
try{
 $dir=if($InstallPath){[IO.Path]::GetFullPath($InstallPath)}else{$script:Root}
 switch($Action){
  'Install'{Install-Portal $dir}
  'Start'{Start-Action $dir}
  'Stop'{Stop-Action $dir}
  'Backup'{Backup-Action $dir $BackupDirectory}
  'Restore'{Restore-Action $dir $BackupPath -Confirmed:$ConfirmRestore}
  'Upgrade'{$old=if($PreviousInstallPath){[IO.Path]::GetFullPath($PreviousInstallPath)}else{''};Upgrade-Action $dir $old}
  'Uninstall'{Uninstall-Action $dir -DeleteData:$PurgeData -Confirmed:$ConfirmPurge}
 }
}catch{[Console]::Error.WriteLine("ERROR: $($_.Exception.Message)");exit 1}
