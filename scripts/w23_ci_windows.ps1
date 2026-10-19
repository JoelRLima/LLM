param(
    [Parameter(Mandatory=$true)][ValidateSet('pytest','regressions','installed','command')][string]$Gate,
    [string]$Name = '',
    [ValidateRange(1,1800)][int]$CommandTimeoutSeconds = 1800,
    [string[]]$ChildArguments = @()
)
$ErrorActionPreference = 'Stop'
if ($Gate -ne 'command' -and $CommandTimeoutSeconds -ne 1800) { throw 'CommandTimeoutSeconds is only valid with Gate=command' }
if ($env:GITHUB_ACTIONS -ne 'true') { throw 'Lifecycle runner requires a disposable GitHub-hosted runner' }
$repo = $env:GITHUB_WORKSPACE
$root = Join-Path $env:LOCALAPPDATA 'W23_CAND03_S5_TEMP_20261014'
$evidence = Join-Path $env:RUNNER_TEMP 'w23-evidence'
New-Item -ItemType Directory -Force -Path $evidence | Out-Null
$identity = [System.Security.Principal.WindowsIdentity]::GetCurrent()
$diagnostic = @("TokenUser=$($identity.User.Value)", "TokenOwner=$($identity.Owner.Value)")
$current = [System.IO.DirectoryInfo]::new($env:LOCALAPPDATA)
while ($null -ne $current) {
    $acl = Get-Acl -LiteralPath $current.FullName
    $diagnostic += "$($current.FullName): Owner=$($acl.Owner); SDDL=$($acl.Sddl)"
    $current = $current.Parent
}
$diagnostic | Set-Content -LiteralPath (Join-Path $evidence 'windows-namespace.txt') -Encoding utf8
$marker = Join-Path $root '.w23-ci-owned-root'
$expectedMarker = "$env:GITHUB_RUN_ID/$env:GITHUB_RUN_ATTEMPT"
if (Test-Path -LiteralPath $root) {
    if (-not (Test-Path -LiteralPath $marker) -or (Get-Content -LiteralPath $marker -Raw).Trim() -ne $expectedMarker) {
        throw 'refusing a preexisting scratch root not owned by this campaign'
    }
} else {
    New-Item -ItemType Directory -Path $root | Out-Null
    $security = [System.Security.AccessControl.DirectorySecurity]::new()
    $security.SetAccessRuleProtection($true, $false)
    $security.SetOwner($identity.Owner)
    foreach ($sid in @($identity.Owner.Value, 'S-1-5-18', 'S-1-5-32-544') | Select-Object -Unique) {
        $principal = [System.Security.Principal.SecurityIdentifier]::new($sid)
        $rule = [System.Security.AccessControl.FileSystemAccessRule]::new($principal, 'FullControl', 'ContainerInherit,ObjectInherit', 'None', 'Allow')
        $security.AddAccessRule($rule)
    }
    Set-Acl -LiteralPath $root -AclObject $security
    Set-Content -LiteralPath $marker -Value $expectedMarker -Encoding utf8
}
$run = Join-Path $root ([Guid]::NewGuid().ToString('N'))
$python = (Get-Command python).Source
if (-not $Name) { $Name = $Gate }
if ($Name -notmatch '^[a-z0-9_-]+$') { throw 'unsafe evidence name' }
$tail = @()
$pytestFlag = @()
if ($Gate -eq 'pytest' -or $Gate -eq 'regressions') {
    $tail = @('-m','pytest','-v','--tb=short','--durations=30','-o','faulthandler_timeout=120',('--junitxml='+(Join-Path $evidence ($Gate+'.xml'))))
    if ($Gate -eq 'regressions') { $tail += @(Get-Content -LiteralPath (Join-Path $repo 'quality/w23/acceptance_focus.txt') | Where-Object { $_.Trim() }) }
    $pytestFlag = @('--pytest-command')
} elseif ($Gate -eq 'installed') {
    $tail = @('scripts/verify_installed_package.py','--summary-json',(Join-Path $evidence 'installed-wheel.json'))
} else { $tail = $ChildArguments }
if ($tail.Count -eq 0) { throw 'missing child command' }
$arguments = @('--working-directory',$repo,'--evidence-directory',$evidence,'--scratch-root',$root,
    '--scratch-directory',$run,'--pytest-basetemp',(Join-Path $run 'pytest'),
    '--stdout-log',($Name+'.log'),'--result-json',($Name+'.json'),'--timeout-seconds',([string]$CommandTimeoutSeconds),
    '--job-memory-mb','4096','--process-memory-mb','3072','--max-processes','128') + $pytestFlag
& $python -B (Join-Path $repo 'scripts/w23_bounded_windows.py') @arguments -- $python -B @tail
$code = $LASTEXITCODE
$log = Join-Path $evidence ($Name+'.log')
if (Test-Path -LiteralPath $log) { Get-Content -LiteralPath $log -Tail 100 }
exit $code
