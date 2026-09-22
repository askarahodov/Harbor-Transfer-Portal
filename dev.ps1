[CmdletBinding()]
param(
    [Parameter(ValueFromRemainingArguments = $true)]
    [string[]] $CommandArgs
)

$ErrorActionPreference = 'Stop'
$Launcher = Join-Path $PSScriptRoot 'tools\dev.py'

$Py = Get-Command py -ErrorAction SilentlyContinue
if ($null -ne $Py) {
    & $Py.Source -3 $Launcher @CommandArgs
    exit $LASTEXITCODE
}

$Python = Get-Command python -ErrorAction SilentlyContinue
if ($null -ne $Python) {
    & $Python.Source $Launcher @CommandArgs
    exit $LASTEXITCODE
}

Write-Error 'Python 3.12 не найден. Установите Python 3.12 и повторите команду.'
exit 2
