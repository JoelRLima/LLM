[CmdletBinding()]
param(
    [ValidateSet('Menu','Install','Update','Open','Status','Rollback','Uninstall')]
    [string]$Operation = 'Menu',
    [string]$Workspace,
    [string]$Profile,
    [int]$WaitForPid = 0
)
$ErrorActionPreference = 'Stop'
$sourceRoot = $PSScriptRoot
$python = Join-Path $sourceRoot '.venv\Scripts\python.exe'
if (-not (Test-Path -LiteralPath $python)) {
    throw 'Python local ausente (.venv). Nenhum download foi iniciado.'
}
function Invoke-WorkbenchOperation([string]$Action) {
    $arguments = @('-I', (Join-Path $sourceRoot 'scripts\manage_workbench.py'), $Action.ToLowerInvariant(), '--source', $sourceRoot)
    if ($Workspace) { $arguments += @('--workspace', $Workspace) }
    if ($Profile) { $arguments += @('--profile', $Profile) }
    if ($WaitForPid -gt 0) { $arguments += @('--wait-pid', [string]$WaitForPid) }
    & $python @arguments
    return
}
if ($Operation -ne 'Menu') {
    Invoke-WorkbenchOperation $Operation
    exit $LASTEXITCODE
}
do {
    Write-Host ''
    Write-Host 'Workbench - Gerenciamento' -ForegroundColor Cyan
    Write-Host '1 Instalar / atualizar    2 Abrir    3 Verificar atualizacoes'
    Write-Host '4 Voltar a versao anterior    5 Desinstalar    0 Sair'
    $choice = Read-Host 'Escolha'
    $action = switch ($choice) { '1' {'Install'} '2' {'Open'} '3' {'Status'} '4' {'Rollback'} '5' {'Uninstall'} }
    if ($action) {
        if ($action -eq 'Uninstall' -and (Read-Host 'Desativar o Workbench preservando dados e versoes? Digite SIM') -ne 'SIM') { continue }
        $raw = Invoke-WorkbenchOperation $action
        try {
            $result = $raw | ConvertFrom-Json
            if ($result.message) { Write-Host $result.message }
            else { $result | Format-List | Out-Host }
        } catch { Write-Host ($raw -join "`n") }
    }
} while ($choice -ne '0')
