param(
    [Parameter(Mandatory=$true)][ValidateSet('pytest','installed','command')][string]$Gate,
    [string]$Name = '',
    [string[]]$ChildArguments = @()
)
$ErrorActionPreference = 'Stop'
if ($env:GITHUB_ACTIONS -ne 'true') { throw 'Lifecycle runner requires a disposable GitHub-hosted runner' }
$repo = $env:GITHUB_WORKSPACE
$root = Join-Path $env:RUNNER_TEMP 'w23-private'
$evidence = Join-Path $env:RUNNER_TEMP 'w23-evidence'
New-Item -ItemType Directory -Force -Path $root,$evidence | Out-Null
$run = Join-Path $root ([Guid]::NewGuid().ToString('N'))
$python = (Get-Command python).Source
if (-not $Name) { $Name = $Gate }
if ($Name -notmatch '^[a-z0-9_-]+$') { throw 'unsafe evidence name' }
$tail = @()
$pytestFlag = @()
if ($Gate -eq 'pytest') {
    $tail = @('-m','pytest','-q','--tb=short',('--junitxml='+(Join-Path $evidence 'pytest.xml')))
    $pytestFlag = @('--pytest-command')
} elseif ($Gate -eq 'installed') {
    $tail = @('scripts/verify_installed_package.py','--summary-json',(Join-Path $evidence 'installed-wheel.json'))
} else { $tail = $ChildArguments }
if ($tail.Count -eq 0) { throw 'missing child command' }
$arguments = @('--working-directory',$repo,'--evidence-directory',$evidence,'--scratch-root',$root,
    '--scratch-directory',$run,'--pytest-basetemp',(Join-Path $run 'pytest'),
    '--stdout-log',($Name+'.log'),'--result-json',($Name+'.json'),'--timeout-seconds','1800',
    '--job-memory-mb','4096','--process-memory-mb','3072','--max-processes','128') + $pytestFlag
& $python -B (Join-Path $repo 'scripts/w23_bounded_windows.py') @arguments -- $python -B @tail
$code = $LASTEXITCODE
$log = Join-Path $evidence ($Name+'.log')
if (Test-Path -LiteralPath $log) { Get-Content -LiteralPath $log -Tail 100 }
exit $code
