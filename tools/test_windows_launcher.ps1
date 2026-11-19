# Execute the real run.cmd on a Windows runner without requiring Docker Desktop.
# A tiny local Docker CLI stub emits real CRLF output, including "linux\r\n".
$ErrorActionPreference = "Stop"
$root = (Resolve-Path (Join-Path $PSScriptRoot "..")).Path
$testDir = Join-Path $env:TEMP ("htp-windows-launcher-" + [guid]::NewGuid().ToString("N"))
$oldPath = $env:PATH

New-Item -ItemType Directory -Path $testDir | Out-Null
try {
    Copy-Item (Join-Path $root "run.cmd") (Join-Path $testDir "run.cmd")
    Copy-Item (Join-Path $root "run-online.cmd") (Join-Path $testDir "run-online.cmd")
    Copy-Item (Join-Path $root "compose.online.yaml") (Join-Path $testDir "compose.online.yaml")
    Set-Content -Path (Join-Path $testDir "git.cmd") -Encoding ASCII -Value '@echo off', 'echo 1111111111111111111111111111111111111111'
    Copy-Item (Join-Path $root ".env.example") (Join-Path $testDir ".env.example")

    $source = @'
using System;
public class DockerStub
{
    public static int Main(string[] args)
    {
        if (args.Length == 0) return 90;
        if (args[0] == "info")
        {
            if (args.Length > 1 && args[1] == "--format")
                Console.WriteLine(Environment.GetEnvironmentVariable("HTP_TEST_DOCKER_OS") ?? "linux");
            return 0;
        }
        if (args[0] == "compose" && args.Length > 1)
        {
            // The online launcher selects a compose file using "-f compose.online.yaml".
            int commandIndex = args.Length > 3 && args[1] == "-f" ? 3 : 1;
            switch (args[commandIndex])
            {
                case "version": Console.WriteLine("Docker Compose version v2"); return 0;
                case "config": return 0;
                case "build": return 0;
                case "ps": return 0;
                case "down": return 0;
                case "pull":
                    // Cached SHA-tagged images should not need network on restarts.
                    if (Array.IndexOf(args, "--policy") < 0 || Array.IndexOf(args, "missing") < 0) return 23;
                    if (Environment.GetEnvironmentVariable("HTP_TEST_PULL_FAIL") == "1"
                        && Environment.GetEnvironmentVariable("HTP_TEST_CACHE_WARM") != "1") return 22;
                    return 0;
                case "up": return Environment.GetEnvironmentVariable("HTP_TEST_UP_FAIL") == "1" ? 21 : 0;
                case "port": Console.WriteLine("127.0.0.1:8082"); return 0;
            }
        }
        return 91;
    }
}
'@
    Add-Type -TypeDefinition $source -OutputAssembly (Join-Path $testDir "docker.exe") -OutputType ConsoleApplication
    $env:PATH = "$testDir;$oldPath"

    Push-Location $testDir
    try {
        $env:HTP_TEST_DOCKER_OS = "linux"
        $env:HTP_TEST_UP_FAIL = "0"
        $output = (& cmd.exe /d /c "run.cmd 2>&1" | Out-String)
        if ($LASTEXITCODE -ne 0 -or !$output.Contains("127.0.0.1:8082")) {
            throw "Expected successful Linux-container startup with CRLF OSType; got: $output"
        }

        $env:HTP_TEST_DOCKER_OS = "windows"
        $output = (& cmd.exe /d /c "run.cmd 2>&1" | Out-String)
        if ($LASTEXITCODE -eq 0 -or $output.Contains("127.0.0.1:8082")) {
            throw "Expected Windows-container mode to be rejected; got: $output"
        }

        $env:HTP_TEST_DOCKER_OS = "linux"
        $env:HTP_TEST_UP_FAIL = "1"
        $output = (& cmd.exe /d /c "run.cmd 2>&1" | Out-String)
        if ($LASTEXITCODE -eq 0 -or $output.Contains("127.0.0.1:8082")) {
            throw "Expected unhealthy Docker Compose to fail closed; got: $output"
        }

        Write-Host "Windows run.cmd checks passed: CRLF OSType, wrong mode, failed startup."

        # Real online Windows script must pull matching images, then start without build.
        $env:HTP_TEST_UP_FAIL = "0"
        $env:HTP_TEST_PULL_FAIL = "0"
        $output = (& cmd.exe /d /c "run-online.cmd 2>&1" | Out-String)
        if ($LASTEXITCODE -ne 0 -or !$output.Contains("127.0.0.1:8082")) {
            throw "Expected GHCR pull-only success; got: $output"
        }

        $env:HTP_TEST_PULL_FAIL = "1"
        $env:HTP_TEST_CACHE_WARM = "0"
        $output = (& cmd.exe /d /c "run-online.cmd 2>&1" | Out-String)
        if ($LASTEXITCODE -eq 0 -or $output.Contains("127.0.0.1:8082")) {
            throw "Expected missing images and failed GHCR pull to stop before startup; got: $output"
        }

        # The same simulated registry outage must not block a fully cached restart.
        $env:HTP_TEST_CACHE_WARM = "1"
        $output = (& cmd.exe /d /c "run-online.cmd 2>&1" | Out-String)
        if ($LASTEXITCODE -ne 0 -or !$output.Contains("127.0.0.1:8082")) {
            throw "Expected cached GHCR images to survive a registry outage; got: $output"
        }
        Write-Host "Windows run-online.cmd checks passed: missing pull, cold fail, warm cache restart."
    }
    finally {
        Pop-Location
    }
}
finally {
    $env:PATH = $oldPath
    Remove-Item Env:HTP_TEST_DOCKER_OS -ErrorAction SilentlyContinue
    Remove-Item Env:HTP_TEST_UP_FAIL -ErrorAction SilentlyContinue
    Remove-Item Env:HTP_TEST_PULL_FAIL -ErrorAction SilentlyContinue
    Remove-Item Env:HTP_TEST_CACHE_WARM -ErrorAction SilentlyContinue
    Remove-Item -Recurse -Force $testDir -ErrorAction SilentlyContinue
}

# A deliberately failing Compose probe leaves LASTEXITCODE nonzero in the host shell.
# All assertions passed, so return success explicitly to GitHub Actions.
exit 0
