Set-StrictMode -Version Latest
$ErrorActionPreference='Stop'
function Check([bool]$Ok,[string]$Message){if(-not$Ok){throw "Windows lifecycle test failed: $Message"}}
$root=(Resolve-Path (Join-Path $PSScriptRoot '..')).Path
$tmp=Join-Path ([IO.Path]::GetTempPath()) ('htp-win-lifecycle-'+[guid]::NewGuid().ToString('N'))
$kit=Join-Path $tmp 'kit';$bin=Join-Path $tmp 'bin';New-Item -ItemType Directory -Path $kit,$bin|Out-Null
$oldPath=$env:PATH;$oldLog=$env:HTP_DOCKER_LOG
try{
 $scripts=@('windows-lifecycle-common.ps1','install.ps1','install.cmd','start.ps1','stop.ps1','backup.ps1','restore.ps1','upgrade.ps1','uninstall.ps1')
 foreach($f in $scripts){Copy-Item (Join-Path $root ('deploy/offline/'+$f)) (Join-Path $kit $f)}
 Copy-Item (Join-Path $root 'deploy/offline/compose.yaml') (Join-Path $kit 'compose.yaml')
 Copy-Item (Join-Path $root '.env.example') (Join-Path $kit '.env.example')
 New-Item -ItemType Directory -Path (Join-Path $kit 'images') -Force | Out-Null
 [IO.File]::WriteAllText((Join-Path $kit 'release-version.txt'),('1.0.0'+[Environment]::NewLine))
 [IO.File]::WriteAllText((Join-Path $kit 'release-arch.txt'),('amd64'+[Environment]::NewLine))
 [IO.File]::WriteAllText((Join-Path $kit 'images/backend.tar'),'fake-backend')
 [IO.File]::WriteAllText((Join-Path $kit 'images/frontend.tar'),'fake-frontend')
 $manifest=foreach($file in (Get-ChildItem -LiteralPath $kit -File -Recurse | Sort-Object FullName)){
  $relative=$file.FullName.Substring($kit.Length+1).Replace('\','/')
  $hash=(Get-FileHash -LiteralPath $file.FullName -Algorithm SHA256).Hash.ToLowerInvariant()
  "$hash  $relative"
 }
 [IO.File]::WriteAllLines((Join-Path $kit 'CHECKSUMS.sha256'),$manifest,[Text.UTF8Encoding]::new($false))
 [IO.File]::WriteAllText((Join-Path $kit '.env'),("PORTAL_VERSION=1.0.0"+[Environment]::NewLine+"PORTAL_CONTOUR=SOURCE"+[Environment]::NewLine))
 $docker='@echo off'+[Environment]::NewLine+'if /I "%~1"=="info" if "%~3"=="{{.OSType}}" echo linux'+[Environment]::NewLine+'if /I "%~1"=="info" if "%~3"=="{{.Architecture}}" echo x86_64'+[Environment]::NewLine+'echo %*>>"%HTP_DOCKER_LOG%"'+[Environment]::NewLine+'exit /b 0'+[Environment]::NewLine
 [IO.File]::WriteAllText((Join-Path $bin 'docker.cmd'),$docker,[Text.Encoding]::ASCII)
 $env:PATH="$bin;$oldPath";$env:HTP_DOCKER_LOG=Join-Path $tmp 'docker.log';[IO.File]::WriteAllText($env:HTP_DOCKER_LOG,'')
 foreach($script in ($scripts | Where-Object { $_ -like '*.ps1' })){
  $tokens=$null;$errors=$null
  [Management.Automation.Language.Parser]::ParseFile((Join-Path $kit $script),[ref]$tokens,[ref]$errors)|Out-Null
  Check ($errors.Count -eq 0) "PowerShell parse errors in $script : $($errors -join '; ')"
 }
 $uninstall=Join-Path $kit 'uninstall.ps1'
 & powershell.exe -NoProfile -ExecutionPolicy Bypass -File $uninstall -InstallDir $kit
 Check ($LASTEXITCODE -eq 0) 'Non-destructive uninstall failed'
 $log=[IO.File]::ReadAllText($env:HTP_DOCKER_LOG)
 Check ($log -match 'down --remove-orphans') 'Uninstall did not stop Compose project'
 Check (-not($log -match '--volumes')) 'Default uninstall attempted to delete persistent data'
 [IO.File]::WriteAllText($env:HTP_DOCKER_LOG,'')
 & powershell.exe -NoProfile -ExecutionPolicy Bypass -File $uninstall -InstallDir $kit -PurgeData
 Check ($LASTEXITCODE -ne 0) 'Purge without confirmation unexpectedly succeeded'
 Check (-not([IO.File]::ReadAllText($env:HTP_DOCKER_LOG)-match 'down')) 'Unconfirmed purge reached Docker mutation'
 [IO.File]::WriteAllText($env:HTP_DOCKER_LOG,'')
 & powershell.exe -NoProfile -ExecutionPolicy Bypass -File $uninstall -InstallDir $kit -PurgeData -ConfirmPurge DELETE_PORTAL_DATA
 Check ($LASTEXITCODE -eq 0) 'Confirmed purge failed'
 Check ([IO.File]::ReadAllText($env:HTP_DOCKER_LOG)-match 'down --remove-orphans --volumes') 'Confirmed purge did not remove volume'
 $backup=[IO.File]::ReadAllText((Join-Path $kit 'backup.ps1'))
 Check ($backup.Contains("'pause'") -and $backup.Contains("'unpause'")) 'Backup does not pause/unpause services'
 Check ($backup.Contains("'--pull','never'") -and $backup.Contains("'--network','none'")) 'Backup is not offline-only'
 Check ($backup.Contains('Backend image architecture mismatch')) 'Backup does not verify local image architecture'
 $restore=[IO.File]::ReadAllText((Join-Path $kit 'restore.ps1'))
 Check ($restore.Contains('$ConfirmRestore') -and $restore.Contains('matching kit version')) 'Restore lacks confirmation/version pin'
 Check ($restore.Contains('Backup archive must not be a reparse point') -and $restore.Contains('Backup sidecar must not be a reparse point')) 'Restore must reject symlinked backup inputs'
 Check ($restore.Contains('CHECKSUMS.sha256') -and $restore.Contains('portal-data.tar.gz')) 'Restore lacks checksum/data validation'
 Check ($restore.Contains("images/backend.tar") -and $restore.Contains("images/frontend.tar")) 'Restore does not reload matching offline images'
 $upgrade=[IO.File]::ReadAllText((Join-Path $kit 'upgrade.ps1'))
 Check ($upgrade.Contains('backup.ps1') -and $upgrade.Contains('restore.ps1') -and $upgrade.Contains('ROLLBACK_OK')) 'Upgrade lacks backup/rollback workflow'
 Check ($upgrade.Contains('Downgrade or non-increasing version change is not supported')) 'Upgrade must reject downgrade/non-increasing versions'
 $common=[IO.File]::ReadAllText((Join-Path $kit 'windows-lifecycle-common.ps1'))
 Check ($common.Contains('CHECKSUMS.sha256') -and $common.Contains('ReparsePoint')) 'Kit verification lacks checksum/path safety'
 $start=[IO.File]::ReadAllText((Join-Path $kit 'start.ps1'))
 Check ($start.Contains('--no-build') -and $start.Contains('--pull') -and $start.Contains('--wait')) 'Start must use prebuilt images and health readiness'
 $stop=[IO.File]::ReadAllText((Join-Path $kit 'stop.ps1'))
 Check ($stop.Contains("@('stop')")) 'Stop must not remove containers or data'
 Write-Host 'Windows offline lifecycle tests passed.'
}finally{
 $env:PATH=$oldPath
 if($null -eq $oldLog){Remove-Item Env:HTP_DOCKER_LOG -ErrorAction SilentlyContinue}else{$env:HTP_DOCKER_LOG=$oldLog}
 Remove-Item -LiteralPath $tmp -Recurse -Force -ErrorAction SilentlyContinue
}
exit 0
