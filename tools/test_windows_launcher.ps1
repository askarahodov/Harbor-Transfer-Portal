# Execute the real run.cmd on a Windows runner without requiring Docker Desktop.
# A tiny local Docker CLI stub emits real CRLF output, including "linux\r\n".
$ErrorActionPreference = "Stop"
$root = (Resolve-Path (Join-Path $PSScriptRoot "..")).Path
$testDir = Join-Path $env:TEMP ("htp-windows-launcher-" + [guid]::NewGuid().ToString("N"))
$oldPath = $env:PATH

New-Item -ItemType Directory -Path $testDir | Out-Null
try {
    Copy-Item (Join-Path $root "run.cmd") (Join-Path $testDir "run.cmd")
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
            switch (args[1])
            {
                case "version": Console.WriteLine("Docker Compose version v2"); return 0;
                case "config": return 0;
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
    }
    finally {
        Pop-Location
    }
}
finally {
    $env:PATH = $oldPath
    Remove-Item Env:HTP_TEST_DOCKER_OS -ErrorAction SilentlyContinue
    Remove-Item Env:HTP_TEST_UP_FAIL -ErrorAction SilentlyContinue
    Remove-Item -Recurse -Force $testDir -ErrorAction SilentlyContinue
}

# A deliberately failing Compose probe leaves LASTEXITCODE nonzero in the host shell.
# All assertions passed, so return success explicitly to GitHub Actions.
exit 0
