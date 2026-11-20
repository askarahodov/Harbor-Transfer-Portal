# Windows offline lifecycle contract tests. Docker is stubbed; no real daemon is claimed.
Set-StrictMode -Version Latest
$ErrorActionPreference = 'Stop'

function Check([bool]$Condition,[string]$Message){if(-not $Condition){throw "Windows offline lifecycle test failed: $Message"}}
function Run-Portal([string]$Kit,[string]$Action,[string[]]$Extra=@()){
 $script=Join-Path $Kit 'windows-lifecycle.ps1'
 $previous=$ErrorActionPreference;$ErrorActionPreference='Continue'
 try{$output=@(& powershell.exe -NoProfile -ExecutionPolicy Bypass -File $script -Action $Action @Extra 2>&1);$code=$LASTEXITCODE;foreach($line in $output){Write-Host $line}}
 finally{$ErrorActionPreference=$previous}
 return [int]$code
}
function New-TestKit([string]$Root,[string]$Destination,[string]$Version){
 New-Item -ItemType Directory -Path (Join-Path $Destination 'images') -Force|Out-Null
 foreach($name in @('windows-lifecycle.ps1','install.cmd','start.cmd','stop.cmd','backup.cmd','restore.cmd','upgrade.cmd','uninstall.cmd')){
  Copy-Item -LiteralPath (Join-Path $Root ("deploy/offline/"+$name)) -Destination (Join-Path $Destination $name)
 }
 Copy-Item -LiteralPath (Join-Path $Root 'deploy/offline/compose.yaml') -Destination (Join-Path $Destination 'compose.yaml')
 Copy-Item -LiteralPath (Join-Path $Root '.env.example') -Destination (Join-Path $Destination '.env.example')
 [IO.File]::WriteAllText((Join-Path $Destination 'release-version.txt'),($Version+[Environment]::NewLine))
 [IO.File]::WriteAllText((Join-Path $Destination 'release-arch.txt'),("amd64"+[Environment]::NewLine))
 [IO.File]::WriteAllText((Join-Path $Destination 'images/backend.tar'),("mock backend "+$Version))
 [IO.File]::WriteAllText((Join-Path $Destination 'images/frontend.tar'),("mock frontend "+$Version))
 $entries=@(Get-ChildItem -LiteralPath $Destination -File -Recurse|Where-Object{$_.Name -ne 'CHECKSUMS.sha256'}|Sort-Object FullName|ForEach-Object{
  $relative=$_.FullName.Substring($Destination.Length+1).Replace('\','/')
  $hash=(Get-FileHash -LiteralPath $_.FullName -Algorithm SHA256).Hash.ToLowerInvariant()
  "$hash  $relative"
 })
 [IO.File]::WriteAllText((Join-Path $Destination 'CHECKSUMS.sha256'),(($entries -join [Environment]::NewLine)+[Environment]::NewLine),([Text.UTF8Encoding]::new($false)))
}
$temp=Join-Path ([IO.Path]::GetTempPath()) ('htp-win-lifecycle-'+[Guid]::NewGuid().ToString('N'))
$root=(Resolve-Path (Join-Path $PSScriptRoot '..')).Path
$bin=Join-Path $temp 'bin'
$kit1=Join-Path $temp 'kit-1.0.0'
$kit2=Join-Path $temp 'kit-1.0.1'
$kit3=Join-Path $temp 'kit-1.0.2'
$dataDir=Join-Path $temp 'data'
$oldPath=$env:PATH
$oldVars=@{}
foreach($name in @('FAKE_DOCKER_LOG','FAKE_DOCKER_OS','FAKE_DOCKER_UP_FAIL','FAKE_DOCKER_UP_FAIL_ONCE','FAKE_DOCKER_DATA_ARCHIVE','FAKE_DOCKER_RESTORED_DATA','PORTAL_CONTOUR')){
 $oldVars[$name]=[Environment]::GetEnvironmentVariable($name,'Process')
}
try{
 New-Item -ItemType Directory -Path $bin,$dataDir,$kit1,$kit2,$kit3 -Force|Out-Null
 $fakeSource=@'
using System;
using System.IO;
public class DockerStub
{
    static bool Has(string[] args, string value) { return Array.IndexOf(args, value) >= 0; }
    static string Last(string[] args) { return args.Length == 0 ? "" : args[args.Length - 1]; }
    public static int Main(string[] args)
    {
        string log = Environment.GetEnvironmentVariable("FAKE_DOCKER_LOG");
        if (!String.IsNullOrEmpty(log)) File.AppendAllText(log, String.Join(" ", args) + Environment.NewLine);
        if (args.Length == 0) return 90;
        if (args[0] == "info")
        {
            if (Has(args, "--format"))
            {
                string format = Last(args);
                if (format.Contains("OSType")) Console.WriteLine(Environment.GetEnvironmentVariable("FAKE_DOCKER_OS") ?? "linux");
                else if (format.Contains("Architecture")) Console.WriteLine("x86_64");
            }
            return 0;
        }
        if (args[0] == "compose")
        {
            if (Has(args, "version")) { Console.WriteLine("Docker Compose version v2"); return 0; }
            if (Has(args, "ps")) { Console.WriteLine("fake-running-container"); return 0; }
            if (Has(args, "up"))
            {
                string once = Environment.GetEnvironmentVariable("FAKE_DOCKER_UP_FAIL_ONCE");
                if (!String.IsNullOrEmpty(once) && !File.Exists(once)) { File.WriteAllText(once, "failed once"); return 21; }
                if (Environment.GetEnvironmentVariable("FAKE_DOCKER_UP_FAIL") == "1") return 22;
            }
            return 0;
        }
        if (args[0] == "image" && args.Length > 1 && args[1] == "inspect")
        {
            if (Has(args, "--format"))
            {
                string format = args[3];
                if (format.Contains("Architecture")) Console.WriteLine("amd64");
                else if (format.Contains("org.opencontainers.image.version"))
                {
                    string image = Last(args);
                    int colon = image.LastIndexOf(':');
                    Console.WriteLine(colon >= 0 ? image.Substring(colon + 1) : "unknown");
                }
            }
            return 0;
        }
        if (args[0] == "volume") return 0;
        if (args[0] == "load") return 0;
        if (args[0] == "run")
        {
            if (Has(args, "-czf"))
            {
                string source = Environment.GetEnvironmentVariable("FAKE_DOCKER_DATA_ARCHIVE");
                using (FileStream input = File.OpenRead(source)) input.CopyTo(Console.OpenStandardOutput());
                return 0;
            }
            if (Has(args, "-xzf") && Has(args, "--mount"))
            {
                int mountIndex = Array.IndexOf(args, "--mount");
                string mount = args[mountIndex + 1];
                string source = null;
                foreach (string part in mount.Split(',')) if (part.StartsWith("source=")) source = part.Substring("source=".Length);
                if (String.IsNullOrEmpty(source)) return 24;
                string destination = Environment.GetEnvironmentVariable("FAKE_DOCKER_RESTORED_DATA");
                using (FileStream input = File.OpenRead(source))
                using (FileStream output = File.Create(destination)) input.CopyTo(output);
                return 0;
            }
            return 0;
        }
        return 0;
    }
}
'@
 Add-Type -TypeDefinition $fakeSource -OutputAssembly (Join-Path $bin 'docker.exe') -OutputType ConsoleApplication
 $env:PATH="$bin;$oldPath"
 $env:FAKE_DOCKER_LOG=Join-Path $temp 'docker.log'
 $env:FAKE_DOCKER_DATA_ARCHIVE=Join-Path $temp 'data.tar.gz'
 $env:FAKE_DOCKER_RESTORED_DATA=Join-Path $temp 'restored-data.tar.gz'
 $env:PORTAL_CONTOUR='TARGET'
 [IO.File]::WriteAllText((Join-Path $dataDir 'marker.txt'),'persistent state marker')
 & tar.exe -czf $env:FAKE_DOCKER_DATA_ARCHIVE -C $dataDir .
 Check ($LASTEXITCODE -eq 0) 'Could not prepare a test data archive'

 New-TestKit $root $kit1 '1.0.0'
 New-TestKit $root $kit2 '1.0.1'
 New-TestKit $root $kit3 '1.0.2'

 # Install and repeat: the second run must preserve the exact .env and use no build/pull.
 & (Join-Path $kit1 'install.cmd')
 Check ($LASTEXITCODE -eq 0) 'CMD offline install failed'
 $envPath=Join-Path $kit1 '.env'
 Check (Test-Path $envPath -PathType Leaf) '.env was not created'
 $initialEnv=[IO.File]::ReadAllText($envPath)
 Check ($initialEnv.Contains('PORTAL_CONTOUR=TARGET')) 'TARGET bootstrap role was not written'
 Check ($initialEnv -match '(?m)^JWT_SECRET=[a-f0-9]{64}') 'Strong JWT secret was not generated'
 & (Join-Path $kit1 'install.cmd')
 Check ($LASTEXITCODE -eq 0) 'Idempotent installer rerun failed'
 Check ([IO.File]::ReadAllText($envPath) -ceq $initialEnv) 'Installer rerun overwrote .env'

 Check ((Run-Portal $kit1 'Start') -eq 0) 'Start action failed'
 Check ((Run-Portal $kit1 'Stop') -eq 0) 'Stop action failed'
 $log=[IO.File]::ReadAllText($env:FAKE_DOCKER_LOG)
 Check ($log -match 'up -d --no-build --pull never --wait') 'Install/start did not enforce no-build/no-pull and health wait'
 Check ($log -match 'compose .* stop') 'Stop did not use Compose stop'

 # Backup creates a protected archive plus outer SHA-256; restore requires explicit confirmation.
 Check ((Run-Portal $kit1 'Backup') -eq 0) 'Backup action failed'
 $backup=Get-ChildItem -LiteralPath (Join-Path $kit1 'backups') -Filter '*.tar.gz'|Select-Object -First 1
 Check ($null -ne $backup) 'Backup archive was not created'
 Check (Test-Path ($backup.FullName+'.sha256')) 'Backup sidecar was not created'
 [IO.File]::WriteAllText($env:FAKE_DOCKER_LOG,'')
 $missingConfirmation=Run-Portal $kit1 'Restore' @('-BackupPath',$backup.FullName)
 Check ($missingConfirmation -ne 0) 'Restore accepted a missing confirmation switch'
 Check ([IO.File]::ReadAllText($env:FAKE_DOCKER_LOG) -eq '') 'Restore touched Docker before explicit confirmation'
 Check ((Run-Portal $kit1 'Restore' @('-BackupPath',$backup.FullName,'-ConfirmRestore')) -eq 0) 'Confirmed restore failed'
 Check (Test-Path $env:FAKE_DOCKER_RESTORED_DATA) 'Restore did not stream the data archive to Docker stdin'
 $restoredMembers=@(& tar.exe -tzf $env:FAKE_DOCKER_RESTORED_DATA)
 Check ($LASTEXITCODE -eq 0 -and ($restoredMembers -contains './marker.txt')) 'Restore stream is not a valid persistent-data tar archive'
 $restoredMarker=(& tar.exe -xOf $env:FAKE_DOCKER_RESTORED_DATA './marker.txt' | Out-String).Trim()
 Check ($LASTEXITCODE -eq 0 -and $restoredMarker -eq 'persistent state marker') 'Restore did not preserve the persistent-state marker'

 # Successful upgrade keeps the shared persistent volume and changes only PORTAL_VERSION.
 Check ((Run-Portal $kit2 'Upgrade' @('-PreviousInstallPath',$kit1)) -eq 0) 'Successful offline upgrade failed'
 $newEnv=[IO.File]::ReadAllText((Join-Path $kit2 '.env'))
 Check ($newEnv -match '(?m)^PORTAL_VERSION=1\.0\.1') 'Upgrade did not activate the new release version'
 Check ($newEnv -match '(?m)^PORTAL_CONTOUR=TARGET') 'Upgrade changed the bootstrap contour'
 Check ([IO.File]::ReadAllText((Join-Path $kit1 '.env')) -ceq $initialEnv) 'Upgrade modified the previous installation .env'

 # Failed startup must restore the matching previous snapshot and leave the new kit inactive.
 $failOnce=Join-Path $temp 'fail-up-once.marker'
 $env:FAKE_DOCKER_UP_FAIL_ONCE=$failOnce
 $upgradeCode=Run-Portal $kit3 'Upgrade' @('-PreviousInstallPath',$kit2)
 Check ($upgradeCode -ne 0) 'Failed upgrade unexpectedly returned success'
 Check (-not (Test-Path (Join-Path $kit3 '.env'))) 'Failed upgrade left the new .env active'
 $restoredEnv=[IO.File]::ReadAllText((Join-Path $kit2 '.env'))
 Check ($restoredEnv -match '(?m)^PORTAL_VERSION=1\.0\.1') 'Failed upgrade did not restore previous version'
 Check ([IO.File]::ReadAllText($env:FAKE_DOCKER_LOG) -match 'chown -R 10001:10001') 'Rollback did not restore persistent data ownership'
 Remove-Item Env:FAKE_DOCKER_UP_FAIL_ONCE -ErrorAction SilentlyContinue

 # Safe uninstall preserves the volume; purge requires two explicit switches.
 Check ((Run-Portal $kit2 'Uninstall') -eq 0) 'Safe uninstall failed'
 Check (Test-Path (Join-Path $kit2 '.env')) 'Safe uninstall deleted .env'
 $uninstallLog=[IO.File]::ReadAllText($env:FAKE_DOCKER_LOG)
 Check ($uninstallLog -match 'down --remove-orphans') 'Safe uninstall did not remove containers'
 Check (-not ($uninstallLog -match 'down --remove-orphans --volumes')) 'Safe uninstall purged the data volume'
 Check ((Run-Portal $kit2 'Uninstall' @('-PurgeData')) -ne 0) 'Purge without confirmation was accepted'
 Check ((Run-Portal $kit2 'Uninstall' @('-PurgeData','-ConfirmPurge')) -eq 0) 'Confirmed data purge failed'
 Check (Test-Path (Join-Path $kit2 '.env')) 'Purge deleted host-side .env'

 # Fail closed before image loading on wrong Docker mode or tampered kit payload.
 $badKit=Join-Path $temp 'bad-kit';New-Item -ItemType Directory -Path $badKit|Out-Null;New-TestKit $root $badKit '1.0.3'
 [IO.File]::AppendAllText((Join-Path $badKit 'images/backend.tar'),'tamper')
 [IO.File]::WriteAllText($env:FAKE_DOCKER_LOG,'')
 Check ((Run-Portal $badKit 'Install') -ne 0) 'Tampered release payload was accepted'
 Check ([IO.File]::ReadAllText($env:FAKE_DOCKER_LOG) -eq '') 'Tampered kit reached Docker'
 $modeKit=Join-Path $temp 'mode-kit';New-Item -ItemType Directory -Path $modeKit|Out-Null;New-TestKit $root $modeKit '1.0.4'
 $env:FAKE_DOCKER_OS='windows';[IO.File]::WriteAllText($env:FAKE_DOCKER_LOG,'')
 Check ((Run-Portal $modeKit 'Install') -ne 0) 'Windows-container mode was accepted'
 Check (-not ([IO.File]::ReadAllText($env:FAKE_DOCKER_LOG) -match 'load -i')) 'Wrong Docker mode loaded images'
 $env:FAKE_DOCKER_OS='linux';$env:FAKE_DOCKER_UP_FAIL='1'
 Check ((Run-Portal $modeKit 'Install') -ne 0) 'Unhealthy startup was accepted'
 Remove-Item Env:FAKE_DOCKER_UP_FAIL -ErrorAction SilentlyContinue

 Write-Host 'Windows offline lifecycle contracts passed: install/rerun, start/stop, backup/restore, upgrade rollback, safe/purge uninstall, checksum and Docker-mode failures.'
} finally {
 $env:PATH=$oldPath
 foreach($name in $oldVars.Keys){if($null -eq $oldVars[$name]){Remove-Item ("Env:"+$name) -ErrorAction SilentlyContinue}else{[Environment]::SetEnvironmentVariable($name,$oldVars[$name],'Process')}}
 Remove-Item -LiteralPath $temp -Recurse -Force -ErrorAction SilentlyContinue
}
exit 0
