# Create/protect only this run's new profile-local root. Never alter an existing ACL.
$ErrorActionPreference = 'Stop'
if ($env:GITHUB_ACTIONS -ne 'true' -or $env:RUNNER_ENVIRONMENT -ne 'github-hosted') { throw 'Disposable hosted runner required' }
$work = Join-Path $env:LOCALAPPDATA ("w23-manual-$env:GITHUB_RUN_ID-$env:GITHUB_RUN_ATTEMPT")
if (Test-Path -LiteralPath $work) { throw 'Refusing to reuse or change a preexisting directory' }
$drive = [IO.Path]::GetPathRoot($work).Substring(0,1)
$volume = Get-Volume -DriveLetter $drive
if ($volume.FileSystem -ne 'NTFS' -or $volume.DriveType -ne 'Fixed') { throw 'Local fixed NTFS volume required' }
New-Item -ItemType Directory -Path $work | Out-Null
$identity = [System.Security.Principal.WindowsIdentity]::GetCurrent()
$security = [System.Security.AccessControl.DirectorySecurity]::new()
$security.SetAccessRuleProtection($true, $false)
$security.SetOwner($identity.User)
foreach ($sid in @($identity.User.Value, 'S-1-5-18', 'S-1-5-32-544') | Select-Object -Unique) {
    $principal = [System.Security.Principal.SecurityIdentifier]::new($sid)
    $rule = [System.Security.AccessControl.FileSystemAccessRule]::new($principal, 'FullControl', 'ContainerInherit,ObjectInherit', 'None', 'Allow')
    $security.AddAccessRule($rule)
}
Set-Acl -LiteralPath $work -AclObject $security
if (-not (Get-Acl -LiteralPath $work).AreAccessRulesProtected) { throw 'New private ACL was not protected' }
$marker = "$env:GITHUB_RUN_ID/$env:GITHUB_RUN_ATTEMPT"
[IO.File]::WriteAllText((Join-Path $work '.w23-new-root'), $marker, [Text.UTF8Encoding]::new($false))
$env:W23_DIAG_WORK = $work
"W23_DIAG_WORK=$work" | Out-File -FilePath $env:GITHUB_ENV -Encoding utf8 -Append
python -B -m scripts.w23_diagnostics initialize --work $work --mode $env:W23_DIAG_MODE
if ($LASTEXITCODE -ne 0) { exit $LASTEXITCODE }
