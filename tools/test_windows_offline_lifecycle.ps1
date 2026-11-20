Set-StrictMode -Version Latest
$ErrorActionPreference = 'Stop'
function Check([bool]$Ok,[string]$Message) { if (-not $Ok) { throw "Windows lifecycle test failed: $Message" } }
function New-TestKit([string]$Path,[string]$Version,[switch]$Active) {
    New-Item -ItemType Directory -Path $Path -Force | Out-Null
    New-Item -ItemType Directory -Path (Join-Path $Path 'images') -Force | Out-Null
    $scripts = @('windows-lifecycle-common.ps1','install.ps1','install.cmd','start.ps1','stop.ps1','backup.ps1','restore.ps1','upgrade.ps1','uninstall.ps1')
    foreach ($name in $scripts) { Copy-Item (Join-Path $root ('deploy/offline/' + $name)) (Join-Path $Path $name) }
    Copy-Item (Join-Path $root 'deploy/offline/compose.yaml') (Join-Path $Path 'compose.yaml')
    Copy-Item (Join-Path $root '.env.example') (Join-Path $Path '.env.example')
    [IO.File]::WriteAllText((Join-Path $Path 'release-version.txt'),($Version + [Environment]::NewLine))
    [IO.File]::WriteAllText((Join-Path $Path 'release-arch.txt'),('amd64' + [Environment]::NewLine))
    [IO.File]::WriteAllText((Join-Path $Path 'images/backend.tar'),'fake-backend')
    [IO.File]::WriteAllText((Join-Path $Path 'images/frontend.tar'),'fake-frontend')
    $manifest = foreach ($file in (Get-ChildItem -LiteralPath $Path -File -Recurse | Sort-Object FullName)) {
        $relative = $file.FullName.Substring($Path.Length + 1).Replace('\','/')
        $hash = (Get-FileHash -LiteralPath $file.FullName -Algorithm SHA256).Hash.ToLowerInvariant()
        "$hash  $relative"
    }
    [IO.File]::WriteAllLines((Join-Path $Path 'CHECKSUMS.sha256'),$manifest,[Text.UTF8Encoding]::new($false))
    if ($Active) {
        $envText = "PORTAL_VERSION=$Version" + [Environment]::NewLine + "PORTAL_CONTOUR=SOURCE" + [Environment]::NewLine
        [IO.File]::WriteAllText((Join-Path $Path '.env'),$envText,[Text.UTF8Encoding]::new($false))
    }
}
function Run-PortalScript([string]$Kit,[string]$ScriptName,[string[]]$ScriptArgs=@()) {
    $scriptPath = Join-Path $Kit $ScriptName
    $arguments = @('-NoProfile','-ExecutionPolicy','Bypass','-File',$scriptPath) + $ScriptArgs
    $previousPreference = $ErrorActionPreference
    try {
        $ErrorActionPreference = 'Continue'
        $output = @(& powershell.exe @arguments 2>&1)
        $code = $LASTEXITCODE
        foreach ($line in $output) { Write-Host $line }
    } finally { $ErrorActionPreference = $previousPreference }
    return [int]$code
}

$root = (Resolve-Path (Join-Path $PSScriptRoot '..')).Path
$tmp = Join-Path ([IO.Path]::GetTempPath()) ('htp-win-lifecycle-' + [guid]::NewGuid().ToString('N'))
$bin = Join-Path $tmp 'bin'
$kit1 = Join-Path $tmp 'kit-1.0.0'
$kit2 = Join-Path $tmp 'kit-1.0.1'
$kit3 = Join-Path $tmp 'kit-1.0.2'
New-Item -ItemType Directory -Path $bin -Force | Out-Null
$oldPath = $env:PATH
$envNames = @('FAKE_DOCKER_LOG','FAKE_DOCKER_STATE','FAKE_DOCKER_DATA_ARCHIVE','FAKE_DOCKER_RESTORED_DATA','FAKE_DOCKER_PAUSE_FAIL','FAKE_DOCKER_PAUSE_PARTIAL','FAKE_DOCKER_UP_FAIL_ONCE','FAKE_DOCKER_OS')
$oldEnv = @{}
foreach ($name in $envNames) { $oldEnv[$name] = [Environment]::GetEnvironmentVariable($name) }

try {
    $fakeDockerSource = @'
using System;
using System.IO;
public class DockerStub {
    static string Env(string name, string fallback = "") {
        string value = Environment.GetEnvironmentVariable(name);
        return value == null ? fallback : value;
    }
    static bool Has(string[] args, string value) {
        foreach (string arg in args) if (String.Equals(arg, value, StringComparison.OrdinalIgnoreCase)) return true;
        return false;
    }
    static string After(string[] args, string value) {
        for (int i = 0; i + 1 < args.Length; i++) if (String.Equals(args[i], value, StringComparison.OrdinalIgnoreCase)) return args[i + 1];
        return "";
    }
    static string State() {
        string path = Env("FAKE_DOCKER_STATE");
        if (String.IsNullOrEmpty(path) || !File.Exists(path)) return "running";
        return File.ReadAllText(path).Trim();
    }
    static void SetState(string value) {
        string path = Env("FAKE_DOCKER_STATE");
        if (!String.IsNullOrEmpty(path)) File.WriteAllText(path, value);
    }
    static string MountSource(string[] args) {
        string mount = After(args, "--mount");
        if (String.IsNullOrEmpty(mount)) return "";
        foreach (string part in mount.Split(',')) if (part.StartsWith("source=", StringComparison.OrdinalIgnoreCase)) return part.Substring(7);
        return "";
    }
    public static int Main(string[] args) {
        string log = Env("FAKE_DOCKER_LOG");
        if (!String.IsNullOrEmpty(log)) File.AppendAllText(log, String.Join(" ", args) + Environment.NewLine);
        if (args.Length == 0) return 0;
        if (args[0] == "info") {
            string format = After(args, "--format");
            if (format.IndexOf("Architecture", StringComparison.OrdinalIgnoreCase) >= 0) Console.WriteLine("x86_64");
            else Console.WriteLine(Env("FAKE_DOCKER_OS", "linux"));
            return 0;
        }
        if (args[0] == "compose") {
            if (Has(args, "version")) { Console.WriteLine("Docker Compose version v2.30.0"); return 0; }
            if (Has(args, "ps")) {
                string status = After(args, "--status"), state = State();
                if (status == "running" && state == "running") { Console.WriteLine("fake-a"); Console.WriteLine("fake-b"); }
                else if (status == "running" && state == "partial") Console.WriteLine("fake-b");
                else if (status == "paused" && state == "paused") { Console.WriteLine("fake-a"); Console.WriteLine("fake-b"); }
                else if (status == "paused" && state == "partial") Console.WriteLine("fake-a");
                else if (String.IsNullOrEmpty(status) && state == "running") { Console.WriteLine("fake-a"); Console.WriteLine("fake-b"); }
                return 0;
            }
            if (Has(args, "pause")) {
                if (Env("FAKE_DOCKER_PAUSE_PARTIAL") == "1") { SetState("partial"); return 0; }
                SetState("paused");
                if (Env("FAKE_DOCKER_PAUSE_FAIL") == "1") return 41;
                return 0;
            }
            if (Has(args, "unpause")) { SetState("running"); return 0; }
            if (Has(args, "stop") || Has(args, "down")) { SetState("stopped"); return 0; }
            if (Has(args, "up")) {
                string failOnce = Env("FAKE_DOCKER_UP_FAIL_ONCE");
                if (!String.IsNullOrEmpty(failOnce) && !File.Exists(failOnce)) { File.WriteAllText(failOnce, "first activation failed"); return 42; }
                if (Env("FAKE_DOCKER_UP_FAIL") == "1") return 43;
                SetState("running"); return 0;
            }
            return 0;
        }
        if (args[0] == "unpause") { SetState("running"); return 0; }
        if (args[0] == "image" && args.Length > 1 && args[1] == "inspect") {
            string format = After(args, "--format");
            if (format.IndexOf("Architecture", StringComparison.OrdinalIgnoreCase) >= 0) Console.WriteLine("amd64");
            else if (format.IndexOf("org.opencontainers.image.version", StringComparison.OrdinalIgnoreCase) >= 0) {
                string image = args[args.Length - 1]; int colon = image.LastIndexOf(':');
                Console.WriteLine(colon >= 0 ? image.Substring(colon + 1) : "1.0.0");
            }
            return 0;
        }
        if (args[0] == "volume" || args[0] == "load") return 0;
        if (args[0] == "run") {
            if (Has(args, "-czf")) {
                string source = MountSource(args), data = Env("FAKE_DOCKER_DATA_ARCHIVE");
                if (String.IsNullOrEmpty(source) || String.IsNullOrEmpty(data) || !File.Exists(data)) return 51;
                Directory.CreateDirectory(source); File.Copy(data, Path.Combine(source, "portal-data.tar.gz"), true); return 0;
            }
            if (Has(args, "-tzf")) { Console.WriteLine("./marker.txt"); return 0; }
            if (Has(args, "-tvzf")) { Console.WriteLine("-rw-r--r-- 1 0 0 24 Jan 01 00:00 marker.txt"); return 0; }
            if (Has(args, "-xzf")) {
                string source = MountSource(args), archive = Path.Combine(source, "portal-data.tar.gz"), restored = Env("FAKE_DOCKER_RESTORED_DATA");
                if (String.IsNullOrEmpty(source) || !File.Exists(archive) || String.IsNullOrEmpty(restored)) return 52;
                File.Copy(archive, restored, true); return 0;
            }
            return 0;
        }
        return 0;
    }
}
'@
    $fakeDocker = Join-Path $bin 'docker.exe'
    Add-Type -TypeDefinition $fakeDockerSource -OutputAssembly $fakeDocker -OutputType ConsoleApplication
    $env:PATH = "$bin;$oldPath"
    $env:FAKE_DOCKER_LOG = Join-Path $tmp 'docker.log'
    $env:FAKE_DOCKER_STATE = Join-Path $tmp 'docker-state.txt'
    $env:FAKE_DOCKER_DATA_ARCHIVE = Join-Path $tmp 'persistent-data.tar.gz'
    $env:FAKE_DOCKER_RESTORED_DATA = Join-Path $tmp 'restored-data.tar.gz'
    $env:FAKE_DOCKER_OS = 'linux'
    [IO.File]::WriteAllText($env:FAKE_DOCKER_LOG,'')
    [IO.File]::WriteAllText($env:FAKE_DOCKER_STATE,'running')
    [IO.File]::WriteAllText($env:FAKE_DOCKER_DATA_ARCHIVE,'persistent-data-marker')

    New-TestKit $kit1 '1.0.0' -Active
    New-TestKit $kit2 '1.0.1'
    New-TestKit $kit3 '1.0.2'

    foreach ($script in (Get-ChildItem -LiteralPath $kit1 -Filter '*.ps1' -File)) {
        $tokens = $null; $errors = $null
        [Management.Automation.Language.Parser]::ParseFile($script.FullName,[ref]$tokens,[ref]$errors) | Out-Null
        Check ($errors.Count -eq 0) "PowerShell parse errors in $($script.Name): $($errors -join '; ')"
    }

    $backupText = [IO.File]::ReadAllText((Join-Path $kit1 'backup.ps1'))
    Check ($backupText.Contains("'pause'") -and $backupText.Contains("'unpause'")) 'Backup does not pause/unpause services'
    Check ($backupText.Contains("'--pull','never'") -and $backupText.Contains("'--network','none'")) 'Backup is not offline-only'
    Check ($backupText.Contains('Backend image architecture mismatch')) 'Backup does not verify local image architecture'
    Check ($backupText.Contains('Set-PortalPrivateDirectoryAcl') -and $backupText.Contains('Set-PortalPrivateFileAcl')) 'Backup does not restrict secret-bearing NTFS paths'
    Check ($backupText.Contains('$pauseAttempted') -and $backupText.Contains("'unpause'," + '$containerId')) 'Backup does not recover partially paused containers'

    $restoreText = [IO.File]::ReadAllText((Join-Path $kit1 'restore.ps1'))
    Check ($restoreText.Contains('$ConfirmRestore') -and $restoreText.Contains('matching kit version')) 'Restore lacks confirmation/version pin'
    Check ($restoreText.Contains('Assert-PortalNoReparsePath') -and $restoreText.Contains('Backup sidecar required')) 'Restore must reject symlinked backup inputs'
    Check ($restoreText.Contains('CHECKSUMS.sha256') -and $restoreText.Contains('portal-data.tar.gz')) 'Restore lacks checksum/data validation'
    Check ($restoreText.Contains('images/backend.tar') -and $restoreText.Contains('images/frontend.tar')) 'Restore does not reload matching offline images'

    $upgradeText = [IO.File]::ReadAllText((Join-Path $kit1 'upgrade.ps1'))
    Check ($upgradeText.Contains('backup.ps1') -and $upgradeText.Contains('restore.ps1') -and $upgradeText.Contains('ROLLBACK_OK')) 'Upgrade lacks backup/rollback workflow'
    Check ($upgradeText.Contains('Downgrade or non-increasing version change is not supported')) 'Upgrade must reject downgrade/non-increasing versions'
    Check ($upgradeText.Contains('$newEnvCreated') -and $upgradeText.Contains('Remove-Item -LiteralPath $newEnvPath')) 'Upgrade rollback must remove inactive new-kit .env'

    $commonPath = Join-Path $kit1 'windows-lifecycle-common.ps1'
    $commonText = [IO.File]::ReadAllText($commonPath)
    Check ($commonText.Contains('CHECKSUMS.sha256') -and $commonText.Contains('ReparsePoint')) 'Kit verification lacks checksum/path safety'
    Check ($commonText.Contains('Assert-PortalNoReparsePath') -and $commonText.Contains('Set-PortalPrivateFileAcl')) 'Shared path and secret ACL helpers are missing'
    . $commonPath

    $activeEnvPath = Join-Path $kit1 '.env'
    $activeEnvOriginal = [IO.File]::ReadAllText($activeEnvPath)
    foreach ($duplicate in @(
        "PORTAL_VERSION=0.9.0",
        "PORTAL_CONTOUR=TARGET"
    )) {
        [IO.File]::WriteAllText($activeEnvPath, $activeEnvOriginal + [Environment]::NewLine + $duplicate + [Environment]::NewLine)
        $duplicateRejected = $false
        try { Read-PortalEnv $activeEnvPath | Out-Null } catch { $duplicateRejected = $true }
        Check ($duplicateRejected) "Shared .env reader accepted duplicate critical setting: $duplicate"
    }
    [IO.File]::WriteAllText($activeEnvPath, $activeEnvOriginal)



    [IO.File]::WriteAllText($activeEnvPath, $activeEnvOriginal.Replace('PORTAL_CONTOUR=SOURCE', 'PORTAL_CONTOUR=source'))
    $invalidContourRejected = $false
    try { Read-PortalEnv $activeEnvPath | Out-Null } catch { $invalidContourRejected = $true }
    Check ($invalidContourRejected) 'Shared .env reader accepted a case-mismatched contour'
    [IO.File]::WriteAllText($activeEnvPath, $activeEnvOriginal)

    $aclDir = Join-Path $tmp 'acl-test'; New-Item -ItemType Directory -Path $aclDir | Out-Null
    Set-PortalPrivateDirectoryAcl $aclDir
    $secretFile = Join-Path $aclDir '.env'
    Write-PortalAtomic $secretFile 'JWT_SECRET=test-secret'
    Check ((Get-Acl -LiteralPath $aclDir).AreAccessRulesProtected) 'Backup directory ACL still inherits broad permissions'
    $fileAcl = Get-Acl -LiteralPath $secretFile
    Check ($fileAcl.AreAccessRulesProtected) 'Secret file ACL still inherits broad permissions'
    $currentIdentity = [Security.Principal.WindowsIdentity]::GetCurrent().Name
    Check (@($fileAcl.Access | Where-Object { $_.IdentityReference.Value -eq $currentIdentity -and $_.FileSystemRights.ToString().Contains('FullControl') }).Count -gt 0) 'Secret file ACL does not grant current user full control'

    $outside = Join-Path $tmp 'outside'; New-Item -ItemType Directory -Path $outside | Out-Null
    $sentinel = Join-Path $outside 'sentinel.txt'; [IO.File]::WriteAllText($sentinel,'outside')
    $junction = Join-Path $tmp 'junction'
    & $env:ComSpec /d /c ('mklink /J "' + $junction + '" "' + $outside + '"') *> $null
    Check (Test-Path -LiteralPath $junction) 'Could not create junction test fixture'
    $junctionRejected = $false
    try { Assert-PortalNoReparsePath (Join-Path $junction 'sentinel.txt') -MustExist | Out-Null } catch { $junctionRejected = $true }
    Check ($junctionRejected) 'Path validation accepted a file reached through a parent junction'

    [IO.File]::WriteAllText($env:FAKE_DOCKER_LOG,'')
    $env:FAKE_DOCKER_PAUSE_FAIL = '1'
    $partialPauseCode = Run-PortalScript $kit1 'backup.ps1' @('-InstallDir',$kit1)
    Check ($partialPauseCode -ne 0) 'Injected partial pause failure unexpectedly succeeded'
    Check ([IO.File]::ReadAllText($env:FAKE_DOCKER_STATE).Trim() -eq 'running') 'Partial pause failure left Portal paused'
    Check ([IO.File]::ReadAllText($env:FAKE_DOCKER_LOG).Contains('unpause fake-a')) 'Partial pause recovery did not unpause the originally running container'
    Remove-Item Env:FAKE_DOCKER_PAUSE_FAIL -ErrorAction SilentlyContinue

    # A successful pause command must still prove that every previously running container is paused.
    [IO.File]::WriteAllText($env:FAKE_DOCKER_LOG,'')
    $env:FAKE_DOCKER_PAUSE_PARTIAL = '1'
    $partialSuccessCode = Run-PortalScript $kit1 'backup.ps1' @('-InstallDir',$kit1)
    Check ($partialSuccessCode -ne 0) 'Incomplete pause reported success and produced a snapshot'
    Check (([IO.File]::ReadAllText($env:FAKE_DOCKER_STATE)).Trim() -eq 'running') 'Incomplete pause recovery left Portal paused'
    Check (([IO.File]::ReadAllText($env:FAKE_DOCKER_LOG)).Contains('unpause fake-a')) 'Incomplete pause recovery did not unpause the partially paused container'
    Remove-Item Env:FAKE_DOCKER_PAUSE_PARTIAL -ErrorAction SilentlyContinue

    [IO.File]::WriteAllText($env:FAKE_DOCKER_LOG,'')
    $env:FAKE_DOCKER_OS = 'windows'
    $wrongModeCode = Run-PortalScript $kit1 'backup.ps1' @('-InstallDir',$kit1)
    Check ($wrongModeCode -ne 0) 'Windows-container Docker mode unexpectedly passed Linux image preflight'
    Check (-not ([IO.File]::ReadAllText($env:FAKE_DOCKER_LOG) -match 'pause|down|up')) 'Wrong Docker mode reached a workload mutation'
    $env:FAKE_DOCKER_OS = 'linux'

    [IO.File]::WriteAllText($env:FAKE_DOCKER_LOG,'')
    $backupCode = Run-PortalScript $kit1 'backup.ps1' @('-InstallDir',$kit1)
    Check ($backupCode -eq 0) 'Backup failed'
    Check ([IO.File]::ReadAllText($env:FAKE_DOCKER_STATE).Trim() -eq 'running') 'Successful backup did not resume Portal'
    $backupDir = Join-Path $kit1 'backups'
    $backupArchive = Get-ChildItem -LiteralPath $backupDir -Filter '*.zip' -File | Sort-Object LastWriteTimeUtc | Select-Object -Last 1
    Check ($null -ne $backupArchive) 'Backup archive was not created'
    Check (Test-Path -LiteralPath ($backupArchive.FullName + '.sha256')) 'Backup checksum sidecar was not created'
    Check ((Get-Acl -LiteralPath $backupDir).AreAccessRulesProtected) 'Backup directory ACL is not protected'
    Check ((Get-Acl -LiteralPath $backupArchive.FullName).AreAccessRulesProtected) 'Backup archive ACL is not protected'
    Check ((Get-Acl -LiteralPath ($backupArchive.FullName + '.sha256')).AreAccessRulesProtected) 'Backup sidecar ACL is not protected'

    $logBeforeRestore = [IO.File]::ReadAllText($env:FAKE_DOCKER_LOG)
    $unconfirmedRestore = Run-PortalScript $kit1 'restore.ps1' @('-InstallDir',$kit1,'-BackupArchive',$backupArchive.FullName)
    Check ($unconfirmedRestore -ne 0) 'Restore without confirmation unexpectedly succeeded'
    Check ([IO.File]::ReadAllText($env:FAKE_DOCKER_LOG) -eq $logBeforeRestore) 'Unconfirmed restore reached Docker'
    $restoreCode = Run-PortalScript $kit1 'restore.ps1' @('-InstallDir',$kit1,'-BackupArchive',$backupArchive.FullName,'-ConfirmRestore')
    Check ($restoreCode -eq 0) 'Confirmed matching-version restore failed'
    Check (Test-Path -LiteralPath $env:FAKE_DOCKER_RESTORED_DATA) 'Restore did not extract persistent data'
    Check ([IO.File]::ReadAllText($env:FAKE_DOCKER_RESTORED_DATA) -eq 'persistent-data-marker') 'Restore data did not match the backup snapshot'

    $oldEnvBeforeUpgrade = [IO.File]::ReadAllText((Join-Path $kit1 '.env'))
    $upgradeCode = Run-PortalScript $kit2 'upgrade.ps1' @('-NewKitDir',$kit2,'-InstallDir',$kit1)
    Check ($upgradeCode -eq 0) 'Healthy upgrade failed'
    $newEnvText = Read-PortalEnv (Join-Path $kit2 '.env')
    Check ((Read-PortalSetting $newEnvText 'PORTAL_VERSION') -eq '1.0.1') 'Successful upgrade did not write the new version .env'
    Check ([IO.File]::ReadAllText((Join-Path $kit1 '.env')) -eq $oldEnvBeforeUpgrade) 'Successful upgrade modified the previous kit .env'

    $env:FAKE_DOCKER_UP_FAIL_ONCE = Join-Path $tmp 'fail-next-compose-up'
    $failedUpgradeCode = Run-PortalScript $kit3 'upgrade.ps1' @('-NewKitDir',$kit3,'-InstallDir',$kit2)
    Check ($failedUpgradeCode -ne 0) 'Injected unhealthy candidate unexpectedly upgraded'
    Check (-not (Test-Path -LiteralPath (Join-Path $kit3 '.env'))) 'Failed upgrade left an inactive new-kit .env behind'
    $rollbackEnvText = Read-PortalEnv (Join-Path $kit2 '.env')
    Check ((Read-PortalSetting $rollbackEnvText 'PORTAL_VERSION') -eq '1.0.1') 'Rollback did not preserve the active old version'
    Check ([IO.File]::ReadAllText($env:FAKE_DOCKER_STATE).Trim() -eq 'running') 'Rollback did not return the previous Portal to running state'
    Check ([IO.File]::ReadAllText($env:FAKE_DOCKER_RESTORED_DATA) -eq 'persistent-data-marker') 'Rollback did not restore persistent data'
    Remove-Item Env:FAKE_DOCKER_UP_FAIL_ONCE -ErrorAction SilentlyContinue

    [IO.File]::WriteAllText($env:FAKE_DOCKER_LOG,'')
    $uninstallCode = Run-PortalScript $kit2 'uninstall.ps1' @('-InstallDir',$kit2)
    Check ($uninstallCode -eq 0) 'Non-destructive uninstall failed'
    Check (Test-Path -LiteralPath (Join-Path $kit2 '.env')) 'Default uninstall removed .env'
    $uninstallLog = [IO.File]::ReadAllText($env:FAKE_DOCKER_LOG)
    Check ($uninstallLog -match 'down --remove-orphans') 'Uninstall did not stop Compose project'
    Check (-not ($uninstallLog -match '--volumes')) 'Default uninstall attempted to delete persistent data'

    [IO.File]::WriteAllText($env:FAKE_DOCKER_LOG,'')
    $unconfirmedPurge = Run-PortalScript $kit2 'uninstall.ps1' @('-InstallDir',$kit2,'-PurgeData')
    Check ($unconfirmedPurge -ne 0) 'Purge without confirmation unexpectedly succeeded'
    Check (-not ([IO.File]::ReadAllText($env:FAKE_DOCKER_LOG) -match 'down')) 'Unconfirmed purge reached Docker mutation'

    [IO.File]::WriteAllText($env:FAKE_DOCKER_LOG,'')
    $purgeCode = Run-PortalScript $kit2 'uninstall.ps1' @('-InstallDir',$kit2,'-PurgeData','-ConfirmPurge','DELETE_PORTAL_DATA')
    Check ($purgeCode -eq 0) 'Confirmed purge failed'
    Check ([IO.File]::ReadAllText($env:FAKE_DOCKER_LOG) -match 'down --remove-orphans --volumes') 'Confirmed purge did not remove volume'

    $startText = [IO.File]::ReadAllText((Join-Path $kit1 'start.ps1'))
    Check ($startText.Contains('--no-build') -and $startText.Contains('--pull') -and $startText.Contains('--wait')) 'Start must use prebuilt images and health readiness'
    $stopText = [IO.File]::ReadAllText((Join-Path $kit1 'stop.ps1'))
    Check ($stopText.Contains("@('stop')")) 'Stop must not remove containers or data'

    Write-Host 'Windows offline lifecycle behavior and security tests passed.'
}
finally {
    if (Test-Path -LiteralPath (Join-Path $tmp 'junction')) { & $env:ComSpec /d /c ('rmdir "' + (Join-Path $tmp 'junction') + '"') *> $null }
    $env:PATH = $oldPath
    foreach ($name in $envNames) {
        if ($null -eq $oldEnv[$name]) { Remove-Item ("Env:" + $name) -ErrorAction SilentlyContinue }
        else { Set-Item ("Env:" + $name) $oldEnv[$name] }
    }
    Remove-Item -LiteralPath $tmp -Recurse -Force -ErrorAction SilentlyContinue
}
exit 0
