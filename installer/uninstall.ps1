[CmdletBinding()]
param(
    [string]$BundleRoot,
    [ValidateRange(1, 600)]
    [int]$MutexTimeoutSeconds = 60
)

Set-StrictMode -Version 2.0
$ErrorActionPreference = "Stop"
$installScript = Join-Path $PSScriptRoot "install.ps1"
if (-not (Test-Path -LiteralPath $installScript -PathType Leaf)) {
    Write-Error "install.ps1 ausente no bundle W18."
    exit 1
}
if ([string]::IsNullOrEmpty($BundleRoot)) {
    & $installScript -Operation Uninstall -MutexTimeoutSeconds $MutexTimeoutSeconds
}
else {
    & $installScript -Operation Uninstall -BundleRoot $BundleRoot -MutexTimeoutSeconds $MutexTimeoutSeconds
}
exit $LASTEXITCODE
