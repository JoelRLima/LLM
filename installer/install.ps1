[CmdletBinding()]
param(
    [ValidateSet("Install", "Uninstall")]
    [string]$Operation = "Install",
    [string]$BundleRoot,
    [ValidateRange(1, 600)]
    [int]$MutexTimeoutSeconds = 60
)

Set-StrictMode -Version 2.0
$ErrorActionPreference = "Stop"

if ($PSVersionTable.PSEdition -eq "Desktop") {
    # A PowerShell Core parent can pass an incompatible PSModulePath to the
    # Windows PowerShell 5.1 child used by the installed-product boundary.
    # Keep this process-local and select only the native Windows PowerShell
    # module tree before the first Utility cmdlet is resolved.
    $nativeModuleRoot = [IO.Path]::Combine($PSHOME, "Modules")
    $utilityModuleRoot = [IO.Path]::Combine($nativeModuleRoot, "Microsoft.PowerShell.Utility")
    $utilityModuleManifest = [IO.Path]::Combine($utilityModuleRoot, "Microsoft.PowerShell.Utility.psd1")
    if (-not [IO.Directory]::Exists($nativeModuleRoot) -or
        -not [IO.Directory]::Exists($utilityModuleRoot) -or
        -not [IO.File]::Exists($utilityModuleManifest)) {
        throw "W18 Windows PowerShell native module path unavailable: $nativeModuleRoot"
    }
    $env:PSModulePath = $nativeModuleRoot
    try {
        Import-Module -Name $utilityModuleManifest -Force -ErrorAction Stop
        $expectedUtilityManifest = [IO.Path]::GetFullPath($utilityModuleManifest)
        $nativeUtilityLoaded = $false
        foreach ($module in @(Get-Module -Name Microsoft.PowerShell.Utility)) {
            if ([IO.Path]::GetFullPath([string]$module.Path) -ieq $expectedUtilityManifest) {
                $nativeUtilityLoaded = $true
                break
            }
        }
        if (-not $nativeUtilityLoaded) {
            throw "Microsoft.PowerShell.Utility carregado de uma origem não nativa"
        }
    }
    catch {
        throw "W18 não conseguiu carregar Microsoft.PowerShell.Utility nativo: $($_.Exception.Message)"
    }
}

# W18 v003 is a production boundary, not a development bootstrapper.  Every
# executable used below is either Windows PowerShell/cmd.exe or the CPython
# binary already present in the validated payload.
$script:JournalSchemaVersion = 1
$script:JournalStates = @(
    "PREPARED",
    "STAGED",
    "LAUNCHER_PROMOTED",
    "PATH_MUTATED",
    "FRESH_SHELL_VERIFIED",
    "COMMITTED",
    "ROLLING_BACK",
    "ROLLED_BACK",
    "FAILED_RECOVERY"
)
$script:ScrubbedVariables = @(
    "VIRTUAL_ENV",
    "CONDA_PREFIX",
    "PYTHONPATH",
    "PYTHONHOME",
    "PYTHONUSERBASE",
    "PYTHONDONTWRITEBYTECODE",
    "PIP_CONFIG_FILE",
    "PIP_INDEX_URL",
    "PIP_EXTRA_INDEX_URL",
    "PIP_TRUSTED_HOST",
    "PIP_FIND_LINKS",
    "PIP_NO_INDEX",
    "UV_CONFIG_FILE",
    "UV_PROJECT",
    "UV_PROJECT_ENVIRONMENT",
    "UV_PYTHON",
    "UV_TOOL_DIR",
    "UV_TOOL_BIN_DIR",
    "UV_INDEX_URL",
    "UV_DEFAULT_INDEX",
    "UV_EXTRA_INDEX_URL",
    "UV_INSECURE_HOST",
    "REQUESTS_CA_BUNDLE",
    "CURL_CA_BUNDLE",
    "SSL_CERT_FILE"
)
$script:SourceCheckout = [IO.Path]::GetFullPath((Join-Path $PSScriptRoot ".."))
$script:CurrentJournal = $null
$script:ChildJobTypeAttempted = $false
$script:ChildJobTypeAvailable = $false

try {
    Add-Type -AssemblyName System.IO.Compression.FileSystem
}
catch {
    throw "W18 requer System.IO.Compression.FileSystem para validar o payload offline."
}

function Get-TextSha256 {
    param([byte[]]$Bytes)

    $algorithm = [Security.Cryptography.SHA256]::Create()
    try {
        $digest = $algorithm.ComputeHash($Bytes)
    }
    finally {
        $algorithm.Dispose()
    }
    return (-join ($digest | ForEach-Object { $_.ToString("x2") }))
}

function Get-FileSha256 {
    param([string]$Path)

    if (-not (Test-Path -LiteralPath $Path -PathType Leaf)) {
        throw "Arquivo ausente para hash: $Path"
    }
    $item = Get-Item -LiteralPath $Path -Force
    if (($item.Attributes -band [IO.FileAttributes]::ReparsePoint) -ne 0) {
        throw "Arquivo link-like recusado: $Path"
    }
    return (Get-FileHash -LiteralPath $Path -Algorithm SHA256).Hash.ToLowerInvariant()
}

function Write-AtomicBytes {
    param(
        [string]$Path,
        [byte[]]$Bytes
    )

    $parent = Split-Path -Parent $Path
    if (-not (Test-Path -LiteralPath $parent -PathType Container)) {
        New-Item -ItemType Directory -Path $parent -Force | Out-Null
    }
    $temporary = "$Path.$([Guid]::NewGuid().ToString('N')).tmp"
    $stream = [IO.File]::Open(
        $temporary,
        [IO.FileMode]::CreateNew,
        [IO.FileAccess]::Write,
        [IO.FileShare]::None
    )
    try {
        $stream.Write($Bytes, 0, $Bytes.Length)
        $stream.Flush($true)
    }
    finally {
        $stream.Dispose()
    }
    try {
        if (Test-Path -LiteralPath $Path -PathType Leaf) {
            $backup = "$Path.$([Guid]::NewGuid().ToString('N')).replace.bak"
            [System.IO.File]::Replace($temporary, $Path, $backup, $true)
            if (Test-Path -LiteralPath $backup -PathType Leaf) {
                Remove-Item -LiteralPath $backup -Force -ErrorAction SilentlyContinue
            }
        }
        else {
            [IO.File]::Move($temporary, $Path)
        }
    }
    finally {
        if (Test-Path -LiteralPath $temporary -PathType Leaf) {
            Remove-Item -LiteralPath $temporary -Force -ErrorAction SilentlyContinue
        }
    }
}

function Write-AtomicText {
    param(
        [string]$Path,
        [string]$Text
    )

    $encoding = New-Object System.Text.UTF8Encoding($false)
    Write-AtomicBytes $Path $encoding.GetBytes($Text)
}

function Write-AtomicJson {
    param(
        [string]$Path,
        [object]$Document
    )

    $json = ($Document | ConvertTo-Json -Depth 40) + "`n"
    Write-AtomicText $Path $json
}

function Read-JsonDocument {
    param([string]$Path)

    if (-not (Test-Path -LiteralPath $Path -PathType Leaf)) {
        throw "JSON ausente: $Path"
    }
    try {
        return (Get-Content -LiteralPath $Path -Raw -Encoding UTF8 | ConvertFrom-Json)
    }
    catch {
        throw "JSON inválido: $Path"
    }
}

function Assert-ExactProperties {
    param(
        [object]$Object,
        [string[]]$Expected,
        [string]$Label
    )

    if ($null -eq $Object) {
        throw "$Label ausente"
    }
    $actual = @($Object.PSObject.Properties | ForEach-Object { [string]$_.Name })
    foreach ($name in $actual) {
        if ($Expected -notcontains $name) {
            throw "$Label contém chave não autorizada: $name"
        }
    }
    foreach ($name in $Expected) {
        if ($actual -notcontains $name) {
            throw "$Label não contém chave obrigatória: $name"
        }
    }
}

function Assert-NonEmptyString {
    param(
        [object]$Value,
        [string]$Label
    )

    if (-not ($Value -is [string]) -or [string]::IsNullOrEmpty([string]$Value)) {
        throw "$Label deve ser string não vazia"
    }
    return [string]$Value
}

function Assert-Sha256 {
    param(
        [object]$Value,
        [string]$Label
    )

    $text = Assert-NonEmptyString $Value $Label
    if ($text -cnotmatch "^[0-9a-f]{64}$") {
        throw "$Label não é SHA-256 hexadecimal minúsculo"
    }
    return $text
}

function Assert-CommitOrTree {
    param(
        [object]$Value,
        [string]$Label
    )

    $text = Assert-NonEmptyString $Value $Label
    if ($text -cnotmatch "^[0-9a-f]{40}$") {
        throw "$Label não é um objeto Git válido"
    }
    return $text
}

function Assert-BundleRelativePath {
    param(
        [object]$Value,
        [string]$Label
    )

    $text = Assert-NonEmptyString $Value $Label
    if ($text.Contains("`0") -or $text.Contains("\") -or $text.Contains(":")) {
        throw "$Label não é caminho POSIX relativo"
    }
    if ($text.StartsWith("/")) {
        throw "$Label não pode ser absoluto"
    }
    $parts = $text.Split('/')
    foreach ($part in $parts) {
        if ([string]::IsNullOrEmpty($part) -or $part -eq "." -or $part -eq "..") {
            throw "$Label contém componente inseguro"
        }
    }
    return $text
}

function Test-ReparsePoint {
    param([string]$Path)

    if (-not (Test-Path -LiteralPath $Path)) {
        return $false
    }
    $item = Get-Item -LiteralPath $Path -Force
    return (($item.Attributes -band [IO.FileAttributes]::ReparsePoint) -ne 0)
}

function Assert-ContainedPath {
    param(
        [string]$Root,
        [string]$Path,
        [switch]$AllowRoot
    )

    $rootFull = [IO.Path]::GetFullPath($Root).TrimEnd('\') + '\'
    $pathFull = [IO.Path]::GetFullPath($Path)
    if ($AllowRoot -and $pathFull.TrimEnd('\').Equals($rootFull.TrimEnd('\'), [StringComparison]::OrdinalIgnoreCase)) {
        return
    }
    if (-not $pathFull.StartsWith($rootFull, [StringComparison]::OrdinalIgnoreCase)) {
        throw "caminho fora da raiz W18: $Path"
    }
}

function Assert-DirectorySafe {
    param([string]$Path)

    if (Test-Path -LiteralPath $Path) {
        if (-not (Test-Path -LiteralPath $Path -PathType Container)) {
            throw "esperava diretório: $Path"
        }
        if (Test-ReparsePoint $Path) {
            throw "diretório link-like recusado: $Path"
        }
    }
    else {
        New-Item -ItemType Directory -Path $Path -Force | Out-Null
    }
}

function Get-InstallPaths {
    if ([string]::IsNullOrEmpty($env:LOCALAPPDATA)) {
        throw "LOCALAPPDATA não está disponível; instalação por usuário não pode continuar"
    }
    $installRoot = [IO.Path]::GetFullPath((Join-Path $env:LOCALAPPDATA "local-llm-agent\install"))
    return [pscustomobject]@{
        InstallRoot = $installRoot
        VersionsRoot = Join-Path $installRoot "versions"
        BinRoot = Join-Path $installRoot "bin"
        TransactionsRoot = Join-Path $installRoot "transactions"
        ReceiptPath = Join-Path $installRoot "current-install.json"
        JournalPath = Join-Path $installRoot "transactions\current.journal.json"
        StableLauncher = Join-Path $installRoot "bin\llm-agent.cmd"
        OwnedPath = [IO.Path]::GetFullPath((Join-Path $installRoot "bin"))
    }
}

function Get-BundleDirectory {
    param([string]$Requested)

    $candidate = if ([string]::IsNullOrEmpty($Requested)) { $PSScriptRoot } else { $Requested }
    $resolved = [IO.Path]::GetFullPath($candidate)
    if (-not (Test-Path -LiteralPath $resolved -PathType Container)) {
        throw "raiz do bundle não é diretório: $resolved"
    }
    if (Test-ReparsePoint $resolved) {
        throw "raiz do bundle link-like recusada: $resolved"
    }
    return $resolved
}

function Get-BundleFile {
    param(
        [string]$Root,
        [string]$Name
    )

    $path = Join-Path $Root $Name
    Assert-ContainedPath $Root $path
    if (-not (Test-Path -LiteralPath $path -PathType Leaf)) {
        throw "membro do bundle ausente: $Name"
    }
    $item = Get-Item -LiteralPath $path -Force
    if ($item.Name -cne $Name -or (Test-ReparsePoint $path)) {
        throw "membro do bundle não é arquivo regular com nome exato: $Name"
    }
    return $path
}

function Assert-BundleMembers {
    param([string]$Root)

    $expected = @(
        "install.cmd",
        "install.ps1",
        "uninstall.cmd",
        "uninstall.ps1",
        "release-manifest.json",
        "payload-windows-x64.zip",
        "payload-files.json",
        "runtime-windows-py312.lock",
        "bootstrap-pip.lock",
        "local_llm_agent-0.2.0rc1-py3-none-any.whl",
        "NOTICES.md"
    )
    $items = @(Get-ChildItem -LiteralPath $Root -Force)
    foreach ($item in $items) {
        if ($item.PSIsContainer -or (Test-ReparsePoint $item.FullName)) {
            throw "bundle contém diretório ou link não permitido: $($item.Name)"
        }
    }
    $actual = @($items | ForEach-Object { [string]$_.Name })
    if ($actual.Count -ne $expected.Count) {
        throw "allowlist de membros do bundle divergiu"
    }
    foreach ($name in $expected) {
        if ($actual -cnotcontains $name) {
            throw "membro do bundle não autorizado/ausente: $name"
        }
    }
    foreach ($name in $actual) {
        if ($expected -cnotcontains $name) {
            throw "membro extra no bundle: $name"
        }
    }
}

function ConvertTo-CanonicalValue {
    param([object]$Value)

    if ($null -eq $Value) {
        return $null
    }
    if ($Value -is [Collections.IDictionary]) {
        $ordered = [ordered]@{}
        foreach ($key in @($Value.Keys | ForEach-Object { [string]$_ } | Sort-Object)) {
            $ordered[$key] = ConvertTo-CanonicalValue $Value[$key]
        }
        return ,$ordered
    }
    if ($Value -is [PSCustomObject]) {
        $ordered = [ordered]@{}
        foreach ($property in @($Value.PSObject.Properties | Sort-Object Name)) {
            $ordered[[string]$property.Name] = ConvertTo-CanonicalValue $property.Value
        }
        return ,$ordered
    }
    if (($Value -is [Collections.IEnumerable]) -and -not ($Value -is [string])) {
        $array = @()
        foreach ($item in $Value) {
            $array += ,(ConvertTo-CanonicalValue $item)
        }
        return ,$array
    }
    return $Value
}

function ConvertTo-CanonicalJson {
    param([object]$Value)

    $canonical = ConvertTo-CanonicalValue $Value
    return ((ConvertTo-Json -InputObject $canonical -Depth 50 -Compress) + "`n")
}

function Get-CandidateId {
    param([object]$Manifest)

    $projection = [ordered]@{
        application_namespace = [string]$Manifest.product_identity.application_namespace
        application_version = [string]$Manifest.application.version
        application_wheel = [string]$Manifest.application.wheel
        application_wheel_sha256 = [string]$Manifest.application.wheel_sha256
        bootstrap_pip_lock_path = [string]$Manifest.bootstrap_pip_lock.path
        bootstrap_pip_lock_sha256 = [string]$Manifest.bootstrap_pip_lock.sha256
        build_tools = [ordered]@{
            pip = [ordered]@{
                version = [string]$Manifest.build_tools.pip.version
                wheel = [string]$Manifest.build_tools.pip.wheel
                wheel_sha256 = [string]$Manifest.build_tools.pip.wheel_sha256
            }
            uv = [ordered]@{
                asset_url = [string]$Manifest.build_tools.uv.asset_url
                sha256 = [string]$Manifest.build_tools.uv.sha256
                version = [string]$Manifest.build_tools.uv.version
            }
        }
        distribution_name = [string]$Manifest.product_identity.distribution_name
        install_scope = [string]$Manifest.install_scope
        payload_inventory_path = [string]$Manifest.payload.inventory
        payload_inventory_sha256 = [string]$Manifest.payload.inventory_sha256
        payload_path = [string]$Manifest.payload.path
        payload_sha256 = [string]$Manifest.payload.sha256
        platform = [string]$Manifest.platform
        python = $Manifest.python
        runtime_lock_path = [string]$Manifest.runtime_lock.path
        runtime_lock_sha256 = [string]$Manifest.runtime_lock.sha256
        schema_version = [string]$Manifest.schema_version
        source_tree = [string]$Manifest.source.tree
    }
    $bytes = (New-Object System.Text.UTF8Encoding($false)).GetBytes((ConvertTo-CanonicalJson $projection))
    return "w18-$(Get-TextSha256 $bytes)".Substring(0, 36)
}

function Assert-Manifest {
    param([object]$Manifest)

    Assert-ExactProperties $Manifest @(
        "schema_version", "product_identity", "source", "application", "payload",
        "runtime_lock", "bootstrap_pip_lock", "platform", "install_scope",
        "build_tools", "python", "candidate_id"
    ) "manifest"
    if ([string]$Manifest.schema_version -cne "W18-RELEASE-MANIFEST-V3") {
        throw "manifest não é v003; o schema online legado é recusado"
    }

    Assert-ExactProperties $Manifest.product_identity @(
        "display_name", "cli_name", "distribution_name", "application_namespace", "identity_status"
    ) "manifest.product_identity"
    if ([string]$Manifest.product_identity.display_name -cne "LLM Agent" -or
        [string]$Manifest.product_identity.cli_name -cne "llm-agent" -or
        [string]$Manifest.product_identity.distribution_name -cne "local-llm-agent" -or
        [string]$Manifest.product_identity.application_namespace -cne "local-llm-agent" -or
        [string]$Manifest.product_identity.identity_status -cne "provisional") {
        throw "identidade do produto não corresponde à autoridade W18"
    }

    Assert-ExactProperties $Manifest.source @("status", "base_commit", "commit", "tree") "manifest.source"
    if ([string]$Manifest.source.status -notin @("uncommitted_candidate", "committed_release")) {
        throw "status de provenance desconhecido"
    }
    Assert-CommitOrTree $Manifest.source.base_commit "manifest.source.base_commit" | Out-Null
    Assert-CommitOrTree $Manifest.source.tree "manifest.source.tree" | Out-Null
    if ([string]$Manifest.source.tree -ceq [string]$Manifest.source.base_commit) {
        throw "manifest.source.tree não pode ser o commit-base"
    }
    if ([string]$Manifest.source.status -eq "uncommitted_candidate") {
        if ($null -ne $Manifest.source.commit) {
            throw "candidato local deve ter source.commit = null"
        }
    }
    elseif (($Manifest.source.commit -isnot [string]) -or ([string]$Manifest.source.commit -cnotmatch "^[0-9a-f]{40}$")) {
        throw "release comprometido deve ter commit Git real"
    }
    if (($Manifest.source.commit -is [string]) -and ([string]$Manifest.source.commit -ceq [string]$Manifest.source.tree)) {
        throw "manifest.source.commit não pode ser confundido com source.tree"
    }

    Assert-ExactProperties $Manifest.application @("version", "wheel", "wheel_sha256") "manifest.application"
    if ([string]$Manifest.application.version -cne "0.2.0rc1" -or
        [string]$Manifest.application.wheel -cne "local_llm_agent-0.2.0rc1-py3-none-any.whl") {
        throw "identidade da aplicação divergente"
    }
    Assert-Sha256 $Manifest.application.wheel_sha256 "manifest.application.wheel_sha256" | Out-Null

    Assert-ExactProperties $Manifest.payload @("path", "sha256", "inventory", "inventory_sha256") "manifest.payload"
    if ([string](Assert-BundleRelativePath $Manifest.payload.path "manifest.payload.path") -cne "payload-windows-x64.zip" -or
        [string](Assert-BundleRelativePath $Manifest.payload.inventory "manifest.payload.inventory") -cne "payload-files.json") {
        throw "identidade do payload divergente"
    }
    Assert-Sha256 $Manifest.payload.sha256 "manifest.payload.sha256" | Out-Null
    Assert-Sha256 $Manifest.payload.inventory_sha256 "manifest.payload.inventory_sha256" | Out-Null

    foreach ($pair in @(
        @($Manifest.runtime_lock, "runtime_lock", "runtime-windows-py312.lock"),
        @($Manifest.bootstrap_pip_lock, "bootstrap_pip_lock", "bootstrap-pip.lock")
    )) {
        Assert-ExactProperties $pair[0] @("path", "sha256") "manifest.$($pair[1])"
        if ([string](Assert-BundleRelativePath $pair[0].path "manifest.$($pair[1]).path") -cne [string]$pair[2]) {
            throw "caminho de lock divergente"
        }
        Assert-Sha256 $pair[0].sha256 "manifest.$($pair[1]).sha256" | Out-Null
    }
    $logicalPaths = @(
        [string]$Manifest.application.wheel,
        [string]$Manifest.payload.path,
        [string]$Manifest.payload.inventory,
        [string]$Manifest.runtime_lock.path,
        [string]$Manifest.bootstrap_pip_lock.path
    )
    if (($logicalPaths | Sort-Object -Unique).Count -ne $logicalPaths.Count) {
        throw "artefatos lógicos duplicados"
    }
    if ([string]$Manifest.platform -cne "windows-x64" -or [string]$Manifest.install_scope -cne "user") {
        throw "platform/install_scope divergente"
    }

    Assert-ExactProperties $Manifest.build_tools @("uv", "pip") "manifest.build_tools"
    Assert-ExactProperties $Manifest.build_tools.uv @("version", "asset_url", "sha256", "expected_signature") "manifest.build_tools.uv"
    if ([string]$Manifest.build_tools.uv.version -cne "0.12.13" -or
        [string]$Manifest.build_tools.uv.asset_url -cne "https://github.com/astral-sh/uv/releases/download/0.12.13/uv-x86_64-pc-windows-msvc.zip" -or
        [string]$Manifest.build_tools.uv.sha256 -cne "a86c9dc7bad9b03f388583b7187c05fe9951c2e0d392217e8fd43d97787f6ec2") {
        throw "evidência uv de release-build divergente"
    }
    Assert-ExactProperties $Manifest.build_tools.uv.expected_signature @(
        "status", "signer_subject", "signer_thumbprint", "timestamp_subject", "timestamp_thumbprint", "evidence"
    ) "manifest.build_tools.uv.expected_signature"
    if ([string]$Manifest.build_tools.uv.expected_signature.status -cne "Valid" -or
        [string]$Manifest.build_tools.uv.expected_signature.signer_subject -cne 'CN="OpenAI OpCo, LLC", O="OpenAI OpCo, LLC", L=San Francisco, S=California, C=US' -or
        [string]$Manifest.build_tools.uv.expected_signature.signer_thumbprint -cne "DF08A554062ECAD03193D010BCC9170BF5D75AE4" -or
        [string]$Manifest.build_tools.uv.expected_signature.timestamp_subject -cne "CN=Microsoft Public RSA Time Stamping Authority, OU=nShield TSS ESN:A500-05E0-D947, OU=Microsoft America Operations, O=Microsoft Corporation, L=Redmond, S=Washington, C=US" -or
        [string]$Manifest.build_tools.uv.expected_signature.timestamp_thumbprint -cne "FF73F729152A9059805E5E0832449D996EF60411" -or
        [string]$Manifest.build_tools.uv.expected_signature.evidence -cne "Phase 0 local Authenticode observation; not an Astral signer claim") {
        throw "evidência de assinatura uv divergente"
    }
    Assert-ExactProperties $Manifest.build_tools.pip @("version", "wheel", "wheel_sha256") "manifest.build_tools.pip"
    if ([string]$Manifest.build_tools.pip.version -cne "26.2.1" -or
        [string]$Manifest.build_tools.pip.wheel -cne "pip-26.2.1-py3-none-any.whl" -or
        [string]$Manifest.build_tools.pip.wheel_sha256 -cne "71138adf1f4ca900cdb7d289c21b7494329f2332b6d85f0e1c42108c0384ed3e") {
        throw "evidência pip de release-build divergente"
    }

    Assert-ExactProperties $Manifest.python @(
        "implementation", "exact_version", "platform", "build_identity", "build_date",
        "source_artifact_url", "source_artifact_sha256"
    ) "manifest.python"
    if ([string]$Manifest.python.implementation -cne "cpython" -or
        [string]$Manifest.python.exact_version -cne "3.12.14" -or
        [string]$Manifest.python.platform -cne "windows-x86_64" -or
        [string]$Manifest.python.build_identity -cne "cpython-3.12.14-windows-x86_64-none" -or
        [string]$Manifest.python.build_date -cne "20260901" -or
        [string]$Manifest.python.source_artifact_url -cne "https://github.com/astral-sh/python-build-standalone/releases/download/20260901/cpython-3.12.14+20260901-x86_64-pc-windows-msvc-install_only.tar.gz" -or
        [string]$Manifest.python.source_artifact_sha256 -cne "e90c1b6419da3bd812dd73bb3de40287a21abf153438147639ec5e20375ea93f") {
        throw "identidade do CPython embutido divergente"
    }
    Assert-Sha256 $Manifest.python.source_artifact_sha256 "manifest.python.source_artifact_sha256" | Out-Null

    $candidateId = Assert-NonEmptyString $Manifest.candidate_id "manifest.candidate_id"
    if ($candidateId -cnotmatch "^w18-[0-9a-f]{32}$" -or $candidateId -cne (Get-CandidateId $Manifest)) {
        throw "candidate_id não corresponde aos bytes e provenance do artefato"
    }
}

function Assert-PayloadInventory {
    param([object]$Inventory)

    Assert-ExactProperties $Inventory @("schema_version", "files") "payload inventory"
    if ([string]$Inventory.schema_version -cne "W18-PAYLOAD-FILES-V1") {
        throw "schema de payload inventory desconhecido"
    }
    $entries = @($Inventory.files)
    if ($entries.Count -eq 0) {
        throw "payload inventory vazio"
    }
    $seen = @{}
    $previous = $null
    foreach ($entry in $entries) {
        Assert-ExactProperties $entry @("path", "size", "sha256") "payload inventory entry"
        $relative = Assert-BundleRelativePath $entry.path "payload inventory path"
        if ($relative -notmatch "^[^/]+(?:/[^/]+)*$") {
            throw "payload inventory path não é POSIX normalizado: $relative"
        }
        $key = $relative.ToLowerInvariant()
        if ($seen.ContainsKey($key)) {
            throw "colisão de caminho Windows no payload inventory: $relative"
        }
        $seen[$key] = $true
        $size = 0L
        try { $size = [Convert]::ToInt64($entry.size) } catch { throw "tamanho inválido no payload inventory: $relative" }
        if ($size -lt 0) {
            throw "tamanho negativo no payload inventory: $relative"
        }
        Assert-Sha256 $entry.sha256 "payload inventory sha256" | Out-Null
        if ($null -ne $previous -and [StringComparer]::Ordinal.Compare([string]$previous, $relative) -ge 0) {
            throw "payload inventory não está ordenado por caminho POSIX"
        }
        $previous = $relative
    }
    return ,$entries
}

function Get-PayloadArchiveEntries {
    param(
        [string]$ArchivePath,
        [object[]]$InventoryEntries
    )

    $expected = @{}
    foreach ($entry in $InventoryEntries) { $expected[[string]$entry.path] = $entry }
    $actual = @{}
    $archive = [IO.Compression.ZipFile]::OpenRead($ArchivePath)
    try {
        foreach ($entry in @($archive.Entries)) {
            $name = [string]$entry.FullName
            if ($name.EndsWith("/") -or $name.EndsWith("\")) {
                throw "payload archive contém entrada de diretório: $name"
            }
            $relative = Assert-BundleRelativePath $name "payload archive member"
            $key = $relative.ToLowerInvariant()
            if ($actual.ContainsKey($key)) {
                throw "payload archive contém caminho duplicado/case-colliding: $name"
            }
            $actual[$key] = [pscustomobject]@{ Name = $relative; Entry = $entry }
            if (-not $expected.ContainsKey($relative)) {
                throw "payload archive contém membro fora do inventory: $relative"
            }
            if ([int64]$entry.Length -ne [int64]$expected[$relative].size) {
                throw "tamanho do payload archive diverge do inventory: $relative"
            }
        }
        if ($actual.Count -ne $expected.Count) {
            throw "payload archive não representa exatamente o inventory"
        }
        foreach ($relative in $expected.Keys) {
            if (-not $actual.ContainsKey($relative.ToLowerInvariant())) {
                throw "payload archive perdeu membro do inventory: $relative"
            }
        }
        $result = @{}
        foreach ($key in $actual.Keys) { $result[$key] = $actual[$key].Entry }
        return ,$result
    }
    finally {
        $archive.Dispose()
    }
}

function Assert-BundleArtifact {
    param(
        [string]$BundleRoot,
        [string]$RelativePath,
        [string]$ExpectedSha,
        [string]$Label
    )

    $path = Get-BundleFile $BundleRoot $RelativePath
    if ((Get-FileSha256 $path) -cne $ExpectedSha) {
        throw "hash divergente em $Label"
    }
    return $path
}

function Read-ValidatedBundle {
    param([string]$Root)

    Assert-BundleMembers $Root
    $manifestPath = Get-BundleFile $Root "release-manifest.json"
    $manifest = Read-JsonDocument $manifestPath
    Assert-Manifest $manifest
    $inventoryPath = Get-BundleFile $Root "payload-files.json"
    $inventory = Read-JsonDocument $inventoryPath
    $inventoryEntries = Assert-PayloadInventory $inventory
    if ((Get-FileSha256 $inventoryPath) -cne [string]$manifest.payload.inventory_sha256) {
        throw "hash do payload inventory diverge do manifest"
    }
    Assert-BundleArtifact $Root ([string]$manifest.application.wheel) ([string]$manifest.application.wheel_sha256) "application wheel" | Out-Null
    $payloadPath = Assert-BundleArtifact $Root ([string]$manifest.payload.path) ([string]$manifest.payload.sha256) "payload archive"
    Assert-BundleArtifact $Root ([string]$manifest.runtime_lock.path) ([string]$manifest.runtime_lock.sha256) "runtime lock" | Out-Null
    Assert-BundleArtifact $Root ([string]$manifest.bootstrap_pip_lock.path) ([string]$manifest.bootstrap_pip_lock.sha256) "bootstrap lock" | Out-Null
    Get-BundleFile $Root "NOTICES.md" | Out-Null
    $archiveEntries = Get-PayloadArchiveEntries $payloadPath $inventoryEntries
    return [pscustomobject]@{
        Root = $Root
        Manifest = $manifest
        Inventory = $inventory
        InventoryEntries = $inventoryEntries
        PayloadPath = $payloadPath
        ArchiveEntries = $archiveEntries
    }
}

function Copy-StreamToFile {
    param(
        [IO.Stream]$Source,
        [string]$Destination
    )

    $parent = Split-Path -Parent $Destination
    Assert-DirectorySafe $parent
    $target = [IO.File]::Open(
        $Destination,
        [IO.FileMode]::CreateNew,
        [IO.FileAccess]::Write,
        [IO.FileShare]::None
    )
    try {
        $buffer = New-Object byte[] (1024 * 1024)
        while (($read = $Source.Read($buffer, 0, $buffer.Length)) -gt 0) {
            $target.Write($buffer, 0, $read)
        }
        $target.Flush($true)
    }
    finally {
        $target.Dispose()
    }
}

function Extract-Payload {
    param(
        [object]$Bundle,
        [string]$Destination
    )

    Assert-DirectorySafe $Destination
    Assert-ContainedPath (Split-Path -Parent $Destination) $Destination -AllowRoot
    $archive = [IO.Compression.ZipFile]::OpenRead([string]$Bundle.PayloadPath)
    try {
        $entries = @{}
        foreach ($archiveEntry in @($archive.Entries)) {
            $relative = Assert-BundleRelativePath $archiveEntry.FullName "payload archive member"
            $entries[$relative.ToLowerInvariant()] = $archiveEntry
        }
        foreach ($expected in $Bundle.InventoryEntries) {
            $relative = [string]$expected.path
            $key = $relative.ToLowerInvariant()
            if (-not $entries.ContainsKey($key)) {
                throw "payload archive perdeu o arquivo: $relative"
            }
            $target = Join-Path $Destination ([string]$relative -replace '/', '\')
            Assert-ContainedPath $Destination $target
            $parent = Split-Path -Parent $target
            Assert-DirectorySafe $parent
            if (Test-Path -LiteralPath $target) {
                throw "destino de extração já existe: $relative"
            }
            $source = $entries[$key].Open()
            try {
                Copy-StreamToFile $source $target
            }
            finally {
                $source.Dispose()
            }
            if (Test-ReparsePoint $target) {
                throw "arquivo extraído tornou-se link-like: $relative"
            }
        }
    }
    finally {
        $archive.Dispose()
    }
    Verify-PayloadTree $Destination $Bundle.InventoryEntries
}

function Get-RelativePayloadPath {
    param(
        [string]$Root,
        [string]$Path
    )

    Assert-ContainedPath $Root $Path
    $rootFull = [IO.Path]::GetFullPath($Root).TrimEnd('\') + '\'
    $pathFull = [IO.Path]::GetFullPath($Path)
    return $pathFull.Substring($rootFull.Length).Replace('\', '/')
}

function Verify-PayloadTree {
    param(
        [string]$Root,
        [object[]]$InventoryEntries
    )

    $expected = @{}
    foreach ($entry in $InventoryEntries) { $expected[[string]$entry.path.ToLowerInvariant()] = $entry }
    $actual = @{}
    foreach ($item in @(Get-ChildItem -LiteralPath $Root -Recurse -Force)) {
        if ($item.PSIsContainer) {
            if (Test-ReparsePoint $item.FullName) { throw "payload contém diretório link-like: $($item.FullName)" }
            continue
        }
        if (Test-ReparsePoint $item.FullName) { throw "payload contém arquivo link-like: $($item.FullName)" }
        $relative = Get-RelativePayloadPath $Root $item.FullName
        $relativeLower = $relative.ToLowerInvariant()
        if ($relativeLower -in @("payload-files.json", "manifest.json", "acceptance.json")) {
            if ($relative -cnotin @("payload-files.json", "manifest.json", "acceptance.json")) {
                throw "metadata de candidato tem capitalização inesperada: $relative"
            }
            continue
        }
        $key = $relative.ToLowerInvariant()
        if ($actual.ContainsKey($key)) { throw "payload contém colisão de caminho: $relative" }
        $actual[$key] = $relative
        if (-not $expected.ContainsKey($key)) { throw "payload contém arquivo fora do inventory: $relative" }
        $entry = $expected[$key]
        if ($item.Length -ne [int64]$entry.size -or (Get-FileSha256 $item.FullName) -cne [string]$entry.sha256) {
            throw "payload file hash/size diverge: $relative"
        }
    }
    if ($actual.Count -ne $expected.Count) { throw "payload extraído não contém exatamente o inventory" }
    foreach ($entry in $InventoryEntries) {
        $target = Join-Path $Root ([string]$entry.path -replace '/', '\')
        if (-not (Test-Path -LiteralPath $target -PathType Leaf)) {
            throw "payload extraído perdeu arquivo: $($entry.path)"
        }
    }
}

function Assert-PayloadFirewall {
    param([string]$CandidatePath)

    foreach ($item in @(Get-ChildItem -LiteralPath $CandidatePath -Recurse -Force)) {
        if (Test-ReparsePoint $item.FullName) { throw "candidate contém link-like: $($item.FullName)" }
        $relative = Get-RelativePayloadPath $CandidatePath $item.FullName
        $lower = $relative.ToLowerInvariant()
        $parts = $lower.Split('/')
        $canonicalStdlibVenv = $lower -eq "runtime/lib/venv" -or $lower.StartsWith("runtime/lib/venv/")
        if ($parts -contains "pyvenv.cfg") {
            throw "created virtual-environment marker leaked into payload: $relative"
        }
        if ($parts -contains ".venv") {
            throw "unauthorized build virtual environment leaked into payload: $relative"
        }
        if (($parts -contains "venv") -and -not $canonicalStdlibVenv) {
            throw "virtual environment outside canonical stdlib leaked into payload: $relative"
        }
        if (($lower -eq "scripts") -or $lower.StartsWith("scripts/")) {
            throw "unauthorized Scripts layout leaked into payload: $relative"
        }
        if (($lower.StartsWith("runtime/scripts/") -and $lower -ne "runtime/scripts/.empty") -or
            $lower -eq "runtime/bin" -or $lower.StartsWith("runtime/bin/")) {
            throw "virtual-environment runtime layout leaked into payload: $relative"
        }
        if ($lower.StartsWith("bin/") -and $lower -ne "bin/llm-agent.cmd") {
            throw "unauthorized bin member leaked into payload: $relative"
        }
        foreach ($marker in @(".agent-local", ".audit-local", ".git", "task_contract", "task_spec", "transactions", "uv.exe", "pip.exe")) {
            if ($lower.Contains($marker)) { throw "conteúdo de build/autoridade vazou no payload: $relative" }
        }
        if ($lower.Contains("\\lib\\site-packages\\setuptools") -or
            $lower.Contains("/lib/site-packages/setuptools") -or
            $lower.Contains("/lib/site-packages/wheel") -or
            $lower.Contains("/lib/site-packages/pip")) {
            throw "ferramenta de build/runtime proibida vazou no payload: $relative"
        }
    }
    $sitePackages = Join-Path $CandidatePath "runtime\Lib\site-packages"
    if (Test-Path -LiteralPath $sitePackages -PathType Container) {
        foreach ($item in @(Get-ChildItem -LiteralPath $sitePackages -Force)) {
            $lower = $item.Name.ToLowerInvariant()
            if ($lower -eq "pip" -or $lower.StartsWith("pip-") -or $lower.StartsWith("setuptools") -or $lower.StartsWith("wheel")) {
                throw "pacote de build presente no site-packages embutido: $($item.Name)"
            }
        }
    }
    $runtime = Join-Path $CandidatePath "runtime\python.exe"
    $launcher = Join-Path $CandidatePath "bin\llm-agent.cmd"
    $shim = Join-Path $CandidatePath "app\launcher.py"
    $candidateLabel = Split-Path -Leaf $CandidatePath
    foreach ($required in @($runtime, $launcher, $shim)) {
        if (-not (Test-Path -LiteralPath $required -PathType Leaf) -or (Test-ReparsePoint $required)) {
            throw "payload self-contained perdeu membro obrigatório: $required"
        }
    }
}

function Read-RegistryPathSnapshot {
    param(
        [Microsoft.Win32.RegistryKey]$RootKey = [Microsoft.Win32.Registry]::CurrentUser,
        [string]$SubKey = "Environment"
    )

    $key = $RootKey.OpenSubKey($SubKey, $false)
    if ($null -eq $key) {
        return [pscustomobject]@{ key_present = $false; present = $false; value = $null; kind = $null }
    }
    try {
        $value = $key.GetValue("Path", $null, [Microsoft.Win32.RegistryValueOptions]::DoNotExpandEnvironmentNames)
        if ($null -eq $value) {
            return [pscustomobject]@{ key_present = $true; present = $false; value = $null; kind = $null }
        }
        if ($value -isnot [string]) { throw "HKCU Environment\Path não é string" }
        $kind = [string]$key.GetValueKind("Path")
        if ($kind -notin @("String", "ExpandString")) { throw "tipo de registro PATH não suportado: $kind" }
        return [pscustomobject]@{ key_present = $true; present = $true; value = [string]$value; kind = $kind }
    }
    finally {
        $key.Close()
    }
}

function Get-MachinePathValue {
    $snapshot = Read-RegistryPathSnapshot ([Microsoft.Win32.Registry]::LocalMachine) "SYSTEM\CurrentControlSet\Control\Session Manager\Environment"
    if ($snapshot.present) { return [string]$snapshot.value }
    return ""
}

function Set-UserPathValue {
    param(
        [object]$Snapshot,
        [string]$Value
    )

    $base = [Microsoft.Win32.Registry]::CurrentUser
    $key = $null
    try {
        $key = $base.CreateSubKey("Environment")
        if ($null -eq $key) { throw "não foi possível abrir HKCU Environment" }
        $kind = if ($Snapshot.present -and $Snapshot.kind -eq "String") {
            [Microsoft.Win32.RegistryValueKind]::String
        }
        else {
            [Microsoft.Win32.RegistryValueKind]::ExpandString
        }
        $key.SetValue("Path", $Value, $kind)
    }
    finally {
        if ($null -ne $key) { $key.Close() }
    }
}

function Remove-UserPathValue {
    param([switch]$RemoveEmptyEnvironmentKey)

    $key = $null
    $removeKey = $false
    try {
        $key = [Microsoft.Win32.Registry]::CurrentUser.OpenSubKey("Environment", $true)
        if ($null -ne $key) {
            $key.DeleteValue("Path", $false)
            $removeKey = $RemoveEmptyEnvironmentKey -and @($key.GetValueNames()).Count -eq 0
        }
    }
    finally {
        if ($null -ne $key) { $key.Close() }
    }
    if ($removeKey) {
        $check = $null
        try {
            $check = [Microsoft.Win32.Registry]::CurrentUser.OpenSubKey("Environment", $true)
            if ($null -ne $check -and @($check.GetValueNames()).Count -eq 0) {
                $check.Close()
                $check = $null
                [Microsoft.Win32.Registry]::CurrentUser.DeleteSubKey("Environment", $false)
            }
        }
        finally {
            if ($null -ne $check) { $check.Close() }
        }
    }
}

function Restore-UserPathSnapshot {
    param([object]$Snapshot)

    if ($Snapshot.present) {
        Set-UserPathValue $Snapshot ([string]$Snapshot.value)
    }
    else {
        Remove-UserPathValue -RemoveEmptyEnvironmentKey:(-not [bool]$Snapshot.key_present)
    }
}

function Get-ComparableWindowsPath {
    param([string]$Value)

    if ($null -eq $Value) { return "" }
    $text = $Value.Trim()
    if ($text.Length -ge 2 -and $text.StartsWith('"') -and $text.EndsWith('"')) {
        $text = $text.Substring(1, $text.Length - 2).Trim()
    }
    try {
        $text = [Environment]::ExpandEnvironmentVariables($text)
        if ([string]::IsNullOrEmpty($text)) { return "" }
        $text = [IO.Path]::GetFullPath($text)
    }
    catch {
        return $text.ToLowerInvariant()
    }
    if ($text.Length -gt 3) { $text = $text.TrimEnd('\', '/') }
    return $text.ToLowerInvariant()
}

function Test-EquivalentOwnedPath {
    param(
        [string]$Segment,
        [string]$OwnedPath
    )

    $left = Get-ComparableWindowsPath $Segment
    $right = Get-ComparableWindowsPath $OwnedPath
    return (-not [string]::IsNullOrEmpty($left) -and $left -eq $right)
}

function Add-OwnedPathSegment {
    param(
        [object]$Snapshot,
        [string]$OwnedPath
    )

    $raw = if ($Snapshot.present) { [string]$Snapshot.value } else { $null }
    $segments = if ($null -eq $raw) { @() } else { @($raw.Split(';')) }
    $matches = @()
    for ($index = 0; $index -lt $segments.Count; $index++) {
        if (Test-EquivalentOwnedPath $segments[$index] $OwnedPath) { $matches += $index }
    }
    if ($matches.Count -eq 0) {
        $value = if ([string]::IsNullOrEmpty($raw)) { $OwnedPath } else { "$raw;$OwnedPath" }
        return [pscustomobject]@{ changed = $true; value = $value; owned_count = 1 }
    }
    if ($matches.Count -eq 1) {
        return [pscustomobject]@{ changed = $false; value = $raw; owned_count = 1 }
    }
    $kept = New-Object System.Collections.Generic.List[string]
    $retained = $false
    foreach ($segment in $segments) {
        if (Test-EquivalentOwnedPath $segment $OwnedPath) {
            if ($retained) { continue }
            $retained = $true
        }
        [void]$kept.Add($segment)
    }
    return [pscustomobject]@{ changed = $true; value = [string]::Join(';', $kept.ToArray()); owned_count = 1 }
}

function Remove-OwnedPathSegment {
    param(
        [object]$Snapshot,
        [string]$OwnedPath
    )

    if (-not $Snapshot.present) {
        return [pscustomobject]@{ changed = $false; value = $null; owned_count = 0 }
    }
    $segments = @(([string]$Snapshot.value).Split(';'))
    $kept = New-Object System.Collections.Generic.List[string]
    $owned = 0
    foreach ($segment in $segments) {
        if (Test-EquivalentOwnedPath $segment $OwnedPath) { $owned++ } else { [void]$kept.Add($segment) }
    }
    if ($owned -eq 0) {
        return [pscustomobject]@{ changed = $false; value = [string]$Snapshot.value; owned_count = 0 }
    }
    return [pscustomobject]@{ changed = $true; value = [string]::Join(';', $kept.ToArray()); owned_count = $owned }
}

function Get-PersistentPath {
    param([object]$UserSnapshot)

    $machineRaw = Get-MachinePathValue
    $machine = if ($null -ne $machineRaw) { [string]$machineRaw } else { "" }
    $user = if ($UserSnapshot.present) { [string]$UserSnapshot.value } else { "" }
    $machine = [Environment]::ExpandEnvironmentVariables($machine)
    $user = [Environment]::ExpandEnvironmentVariables($user)
    if ($machine -and $user) { return "$machine;$user" }
    return ($machine + $user)
}

function Quote-WindowsArgument {
    param([string]$Argument)

    if ($null -eq $Argument) { return '""' }
    $builder = New-Object System.Text.StringBuilder
    [void]$builder.Append('"')
    $slashes = 0
    foreach ($character in $Argument.ToCharArray()) {
        if ($character -eq '\') {
            $slashes++
            continue
        }
        if ($character -eq '"') {
            for ($index = 0; $index -lt (2 * $slashes + 1); $index++) { [void]$builder.Append('\') }
            [void]$builder.Append('"')
            $slashes = 0
            continue
        }
        for ($index = 0; $index -lt $slashes; $index++) { [void]$builder.Append('\') }
        $slashes = 0
        [void]$builder.Append($character)
    }
    for ($index = 0; $index -lt (2 * $slashes); $index++) { [void]$builder.Append('\') }
    [void]$builder.Append('"')
    return $builder.ToString()
}

function Get-CleanChildEnvironment {
    param(
        [string]$PersistentPath,
        [hashtable]$Overrides = @{}
    )

    $environment = @{}
    $scrubbed = @($script:ScrubbedVariables | ForEach-Object { $_.ToUpperInvariant() })
    foreach ($key in [Environment]::GetEnvironmentVariables("Process").Keys) {
        $name = [string]$key
        if ($scrubbed -contains $name.ToUpperInvariant() -or $name.ToUpperInvariant().StartsWith("W18_")) { continue }
        $value = [string][Environment]::GetEnvironmentVariable($name, "Process")
        if ($value -and ($value.ToLowerInvariant().Contains($script:SourceCheckout.ToLowerInvariant()) -or $value.ToLowerInvariant().Contains("\.venv"))) {
            continue
        }
        $environment[$name] = $value
    }
    $environment["PATH"] = $PersistentPath
    foreach ($key in $Overrides.Keys) { $environment[[string]$key] = [string]$Overrides[$key] }
    # Every installer-owned Python child, including cmd launchers reached from
    # fresh-shell probes, inherits the immutable-candidate bytecode policy.
    $environment["PYTHONDONTWRITEBYTECODE"] = "1"
    return $environment
}

function Ensure-ChildProcessJobType {
    if ($script:ChildJobTypeAttempted) { return }
    $script:ChildJobTypeAttempted = $true
    try {
        if ($null -eq ("W18.ChildProcessJob" -as [type])) {
            Add-Type -TypeDefinition @'
using System;
using System.Runtime.InteropServices;

namespace W18 {
    public static class ChildProcessJob {
        [DllImport("kernel32.dll", SetLastError = true)]
        private static extern IntPtr CreateJobObject(IntPtr attributes, string name);

        [DllImport("kernel32.dll", SetLastError = true)]
        private static extern bool AssignProcessToJobObject(IntPtr job, IntPtr process);

        [DllImport("kernel32.dll", SetLastError = true)]
        private static extern bool TerminateJobObject(IntPtr job, uint exitCode);

        [DllImport("kernel32.dll", SetLastError = true)]
        private static extern bool CloseHandle(IntPtr handle);

        public static IntPtr CreateAndAssign(IntPtr process) {
            IntPtr job = CreateJobObject(IntPtr.Zero, null);
            if (job == IntPtr.Zero) {
                return IntPtr.Zero;
            }
            if (!AssignProcessToJobObject(job, process)) {
                CloseHandle(job);
                return IntPtr.Zero;
            }
            return job;
        }

        public static bool Terminate(IntPtr job, uint exitCode) {
            return job != IntPtr.Zero && TerminateJobObject(job, exitCode);
        }

        public static void Close(IntPtr job) {
            if (job != IntPtr.Zero) {
                CloseHandle(job);
            }
        }
    }
}
'@ -ErrorAction Stop
        }
        $script:ChildJobTypeAvailable = $true
    }
    catch {
        # Some hosts place PowerShell inside a job that disallows nested jobs.
        # The timeout path has a taskkill /T fallback for that documented case.
        $script:ChildJobTypeAvailable = $false
    }
}

function Redact-ChildText {
    param([string]$Text)

    if ($null -eq $Text) { return "" }
    $safe = [string]$Text
    $safe = [regex]::Replace(
        $safe,
        '(?i)((?:--?|)(?:token|password|secret|api[-_]?key|authorization)\s*[=:]?\s+)(?:"[^"]*"|\S+)',
        '$1<redacted>'
    )
    return [regex]::Replace(
        $safe,
        '(?i)((?:token|password|secret|api[-_]?key|authorization)\s*[=:]\s*)(?:"[^"]*"|\S+)',
        '$1<redacted>'
    )
}

function Get-ChildOutputTail {
    param([string]$Text)

    $safe = Redact-ChildText $Text
    $limit = 4000
    if ($safe.Length -gt $limit) { return $safe.Substring($safe.Length - $limit) }
    return $safe
}

function Get-SafeChildArguments {
    param(
        [string[]]$Arguments,
        [string]$RawArguments
    )

    if ($RawArguments) { return (Redact-ChildText $RawArguments) }
    $safe = New-Object System.Collections.Generic.List[string]
    $redactNext = $false
    foreach ($argument in @($Arguments)) {
        $text = [string]$argument
        if ($redactNext) {
            [void]$safe.Add('"<redacted>"')
            $redactNext = $false
            continue
        }
        if ($text -match '^(?i)--?(token|password|secret|api[-_]?key|authorization)$') {
            [void]$safe.Add((Quote-WindowsArgument $text))
            $redactNext = $true
            continue
        }
        if ($text -match '^(?i)(--?(?:token|password|secret|api[-_]?key|authorization))=(.*)$') {
            [void]$safe.Add((Quote-WindowsArgument ($matches[1] + '=<redacted>')))
            continue
        }
        [void]$safe.Add((Quote-WindowsArgument (Redact-ChildText $text)))
    }
    return ($safe.ToArray() -join " ")
}

function Format-ChildDiagnostic {
    param([object]$Diagnostic)

    return @(
        "child step: $($Diagnostic.step)",
        "executable: $($Diagnostic.executable)",
        "arguments: $($Diagnostic.arguments)",
        "cwd: $($Diagnostic.cwd)",
        "timeout_seconds: $($Diagnostic.timeout_seconds)",
        "exit_state: $($Diagnostic.exit_state)",
        "exit_code: $($Diagnostic.exit_code)",
        "process_id: $($Diagnostic.process_id)",
        "termination: $($Diagnostic.termination)",
        "stdout_tail: $($Diagnostic.stdout_tail)",
        "stderr_tail: $($Diagnostic.stderr_tail)"
    ) -join [Environment]::NewLine
}

function Receive-ChildStream {
    param(
        [object]$Task,
        [int]$TimeoutMilliseconds = 5000
    )

    if ($null -eq $Task) {
        return [pscustomobject]@{ complete = $true; text = "" }
    }
    $complete = $Task.Wait($TimeoutMilliseconds)
    if (-not $complete) {
        return [pscustomobject]@{ complete = $false; text = "[stream capture incomplete]" }
    }
    try {
        $text = [string]$Task.GetAwaiter().GetResult()
        return [pscustomobject]@{ complete = $true; text = $text }
    }
    catch {
        return [pscustomobject]@{ complete = $false; text = "[stream capture failed]" }
    }
}

function Stop-ChildProcessTree {
    param(
        [System.Diagnostics.Process]$Process,
        [IntPtr]$JobHandle
    )

    if ($JobHandle -ne [IntPtr]::Zero -and $script:ChildJobTypeAvailable) {
        try {
            if ([W18.ChildProcessJob]::Terminate($JobHandle, 1)) { return "job_object" }
        }
        catch { }
    }

    $taskkill = Join-Path $env:SystemRoot "System32\taskkill.exe"
    if (Test-Path -LiteralPath $taskkill -PathType Leaf) {
        $killPsi = New-Object System.Diagnostics.ProcessStartInfo
        $killPsi.FileName = $taskkill
        $killPsi.Arguments = "/PID $($Process.Id) /T /F"
        $killPsi.WorkingDirectory = [IO.Path]::GetTempPath()
        $killPsi.UseShellExecute = $false
        $killPsi.CreateNoWindow = $true
        $killPsi.RedirectStandardInput = $true
        $killPsi.RedirectStandardOutput = $true
        $killPsi.RedirectStandardError = $true
        $killer = New-Object System.Diagnostics.Process
        $killer.StartInfo = $killPsi
        try {
            $started = $killer.Start()
            if ($started) {
                try { $killer.StandardInput.Close() } catch { }
                $killer.WaitForExit(5000) | Out-Null
            }
            if ($started -and $killer.HasExited -and $killer.ExitCode -eq 0) { return "taskkill_tree" }
        }
        catch { }
        finally {
            $killer.Dispose()
        }
    }
    try { $Process.Kill() } catch { }
    return "direct_process"
}

function Invoke-ExplicitProcess {
    param(
        [string]$FileName,
        [string[]]$Arguments,
        [string]$WorkingDirectory,
        [string]$PersistentPath,
        [hashtable]$EnvironmentOverrides = @{},
        [string]$RawArguments,
        [string]$StepName = "unnamed child",
        [int]$TimeoutMilliseconds = 120000
    )

    $diagnostic = [ordered]@{
        step = $StepName
        executable = [string]$FileName
        arguments = Get-SafeChildArguments $Arguments $RawArguments
        cwd = [string]$WorkingDirectory
        timeout_seconds = [math]::Round($TimeoutMilliseconds / 1000, 3)
        exit_state = "not_started"
        exit_code = $null
        process_id = $null
        termination = $null
        stdout = ""
        stderr = ""
        stdout_tail = ""
        stderr_tail = ""
    }
    $psi = New-Object System.Diagnostics.ProcessStartInfo
    $psi.FileName = $FileName
    $psi.Arguments = if ($RawArguments) { $RawArguments } else { [string]::Join(" ", @($Arguments | ForEach-Object { Quote-WindowsArgument ([string]$_) })) }
    $psi.WorkingDirectory = $WorkingDirectory
    $psi.UseShellExecute = $false
    $psi.CreateNoWindow = $true
    $psi.RedirectStandardInput = $true
    $psi.RedirectStandardOutput = $true
    $psi.RedirectStandardError = $true
    $childEnvironment = Get-CleanChildEnvironment $PersistentPath $EnvironmentOverrides
    $existing = @($psi.EnvironmentVariables.Keys)
    foreach ($key in $existing) { $psi.EnvironmentVariables.Remove($key) }
    foreach ($key in $childEnvironment.Keys) { $psi.EnvironmentVariables[[string]$key] = [string]$childEnvironment[$key] }

    $process = New-Object System.Diagnostics.Process
    $process.StartInfo = $psi
    $jobHandle = [IntPtr]::Zero
    $stdoutTask = $null
    $stderrTask = $null
    try {
        try {
            $started = $process.Start()
            if (-not $started) { throw "process start returned false" }
            $diagnostic.exit_state = "running"
            $diagnostic.process_id = [int]$process.Id
        }
        catch {
            $diagnostic.exit_state = "start_failed"
            $diagnostic.stderr_tail = Get-ChildOutputTail $_.Exception.Message
            throw (Format-ChildDiagnostic ([pscustomobject]$diagnostic))
        }

        Ensure-ChildProcessJobType
        if ($script:ChildJobTypeAvailable) {
            try { $jobHandle = [W18.ChildProcessJob]::CreateAndAssign($process.Handle) } catch { $jobHandle = [IntPtr]::Zero }
        }
        try { $process.StandardInput.Close() } catch { }
        # Start both readers before waiting.  Waiting first can deadlock when a
        # JSON/help probe fills the OS pipe buffer before the child exits.
        $stdoutTask = $process.StandardOutput.ReadToEndAsync()
        $stderrTask = $process.StandardError.ReadToEndAsync()
        $exited = $process.WaitForExit($TimeoutMilliseconds)
        if (-not $exited) {
            $diagnostic.exit_state = "timeout"
            $diagnostic.termination = Stop-ChildProcessTree $process $jobHandle
            try { $process.WaitForExit(5000) | Out-Null } catch { }
            if ($process.HasExited) { $diagnostic.exit_code = $process.ExitCode }
            $stdout = Receive-ChildStream $stdoutTask
            $stderr = Receive-ChildStream $stderrTask
            $diagnostic.stdout = [string]$stdout.text
            $diagnostic.stderr = [string]$stderr.text
            $diagnostic.stdout_tail = Get-ChildOutputTail $stdout.text
            $diagnostic.stderr_tail = Get-ChildOutputTail $stderr.text
            throw (Format-ChildDiagnostic ([pscustomobject]$diagnostic))
        }

        $diagnostic.exit_state = "exited"
        $diagnostic.exit_code = $process.ExitCode
        $stdout = Receive-ChildStream $stdoutTask
        $stderr = Receive-ChildStream $stderrTask
        $diagnostic.stdout = [string]$stdout.text
        $diagnostic.stderr = [string]$stderr.text
        $diagnostic.stdout_tail = Get-ChildOutputTail $stdout.text
        $diagnostic.stderr_tail = Get-ChildOutputTail $stderr.text
        if (-not $stdout.complete -or -not $stderr.complete) {
            $diagnostic.exit_state = "output_capture_timeout"
            $diagnostic.termination = Stop-ChildProcessTree $process $jobHandle
            throw (Format-ChildDiagnostic ([pscustomobject]$diagnostic))
        }
        return [pscustomobject]$diagnostic
    }
    finally {
        if ($jobHandle -ne [IntPtr]::Zero -and $script:ChildJobTypeAvailable) {
            try { [W18.ChildProcessJob]::Close($jobHandle) } catch { }
        }
        $process.Dispose()
    }
}

function Assert-ProcessSuccess {
    param(
        [object]$Result,
        [string]$Label
    )

    if ([int]$Result.exit_code -ne 0) {
        if ($Result.PSObject.Properties.Name -contains "step") {
            throw (Format-ChildDiagnostic $Result)
        }
        $detail = (([string]$Result.stdout) + "`n" + ([string]$Result.stderr)).Trim()
        throw "$Label falhou com código $($Result.exit_code): $detail"
    }
    return [string]$Result.stdout
}

function Invoke-CmdLauncher {
    param(
        [string]$Launcher,
        [string[]]$Arguments,
        [string]$WorkingDirectory,
        [string]$PersistentPath,
        [string]$StepName = "cmd launcher"
    )

    $cmdLine = 'call "' + $Launcher + '"'
    foreach ($argument in $Arguments) { $cmdLine += " " + (Quote-WindowsArgument ([string]$argument)) }
    $comspec = if ($env:ComSpec) { $env:ComSpec } else { Join-Path $env:SystemRoot "System32\cmd.exe" }
    return Invoke-ExplicitProcess -FileName $comspec -Arguments @() -WorkingDirectory $WorkingDirectory -PersistentPath $PersistentPath -RawArguments ("/d /s /c " + $cmdLine) -StepName $StepName
}

function Get-DoctorDocument {
    param([string]$Text)

    try {
        return ($Text | ConvertFrom-Json)
    }
    catch {
        throw "doctor offline não retornou um único documento JSON: $Text"
    }
}

function Assert-PathDoesNotContainCheckout {
    param([object]$Values)

    $checkout = $script:SourceCheckout.ToLowerInvariant()
    foreach ($value in @($Values)) {
        if ($null -ne $value -and ([string]$value).ToLowerInvariant().Contains($checkout)) {
            throw "checkout do agente apareceu em origem/sys.path/processo: $value"
        }
    }
}

function Test-EmbeddedRuntime {
    param(
        [string]$CandidatePath,
        [string]$PersistentPath
    )

    Assert-PayloadFirewall $CandidatePath
    $runtime = Join-Path $CandidatePath "runtime\python.exe"
    $launcher = Join-Path $CandidatePath "bin\llm-agent.cmd"
    $shim = Join-Path $CandidatePath "app\launcher.py"
    $candidateLabel = Split-Path -Leaf $CandidatePath
    $probeRoot = Join-Path ([IO.Path]::GetTempPath()) ("w18-probe-" + [Guid]::NewGuid().ToString("N"))
    $cwd = Join-Path $probeRoot "outside cwd"
    $probeHome = Join-Path $probeRoot "home"
    try {
        Assert-DirectorySafe $cwd
        Assert-DirectorySafe $probeHome
        $version = Invoke-ExplicitProcess $runtime @($shim, "--version") $cwd $PersistentPath -StepName ("$candidateLabel / runtime --version")
        if ((Assert-ProcessSuccess $version "runtime --version").Trim() -cne "llm-agent 0.2.0rc1") {
            throw "payload reportou versão incorreta"
        }
        $help = Invoke-ExplicitProcess $runtime @($shim, "--help") $cwd $PersistentPath -StepName ("$candidateLabel / runtime --help")
        if ((Assert-ProcessSuccess $help "runtime --help").ToLowerInvariant().IndexOf("usage:") -lt 0) {
            throw "payload help não contém usage"
        }
        $init = Invoke-ExplicitProcess $runtime @($shim, "config", "init", "--home", $probeHome) $cwd $PersistentPath -StepName ("$candidateLabel / runtime config init")
        Assert-ProcessSuccess $init "runtime config init" | Out-Null
        $doctorResult = Invoke-ExplicitProcess $runtime @($shim, "doctor", "--json", "--home", $probeHome, "--workspace", $cwd) $cwd $PersistentPath -StepName ("$candidateLabel / runtime doctor offline")
        $doctor = Get-DoctorDocument (Assert-ProcessSuccess $doctorResult "runtime doctor offline")
        if ($doctor.readiness.offline_ready -ne $true) {
            throw "payload doctor não reportou offline_ready=true"
        }

        $originCode = 'import agent,importlib.metadata,json,pathlib,platform,sys; print(json.dumps({"agent":str(pathlib.Path(agent.__file__).resolve()),"exe":str(pathlib.Path(sys.executable).resolve()),"agent_version":agent.__version__,"distribution_version":importlib.metadata.version("local-llm-agent"),"python":platform.python_version(),"path":list(sys.path)}))'
        $originResult = Invoke-ExplicitProcess $runtime @("-c", $originCode) $cwd $PersistentPath -StepName ("$candidateLabel / runtime import-origin")
        $origin = Get-DoctorDocument (Assert-ProcessSuccess $originResult "runtime import-origin")
        if ([string]$origin.agent_version -cne "0.2.0rc1" -or
            [string]$origin.distribution_version -cne "0.2.0rc1" -or
            [string]$origin.python -cne "3.12.14") {
            throw "identidade do runtime embutido divergiu"
        }
        if ([IO.Path]::GetFullPath([string]$origin.exe) -ine [IO.Path]::GetFullPath($runtime)) {
            throw "sys.executable não é o CPython do payload"
        }
        $runtimeRoot = [IO.Path]::GetFullPath((Join-Path $CandidatePath "runtime"))
        if (-not ([IO.Path]::GetFullPath([string]$origin.agent)).StartsWith($runtimeRoot.TrimEnd('\') + '\', [StringComparison]::OrdinalIgnoreCase)) {
            throw "agent importou fora do runtime embutido"
        }
        Assert-PathDoesNotContainCheckout $origin.path
        Assert-PathDoesNotContainCheckout @($origin.agent, $origin.exe)

        $candidateVersion = Invoke-CmdLauncher $launcher @("--version") $cwd $PersistentPath "$candidateLabel / candidate launcher --version"
        if ((Assert-ProcessSuccess $candidateVersion "candidate launcher --version").Trim() -cne "llm-agent 0.2.0rc1") {
            throw "candidate launcher reportou versão incorreta"
        }
        $candidateHelp = Invoke-CmdLauncher $launcher @("--help") $cwd $PersistentPath "$candidateLabel / candidate launcher --help"
        Assert-ProcessSuccess $candidateHelp "candidate launcher --help" | Out-Null
        return [ordered]@{
            version = "llm-agent 0.2.0rc1"
            help = "passed"
            config_init = "passed"
            doctor_offline_ready = $true
            python = "3.12.14"
            sys_executable = [IO.Path]::GetFullPath($runtime)
            import_origin = [IO.Path]::GetFullPath([string]$origin.agent)
            ensurepip = "frozen-stdlib-only; not invoked"
        }
    }
    finally {
        if (Test-Path -LiteralPath $probeRoot) {
            Remove-Item -LiteralPath $probeRoot -Recurse -Force -ErrorAction SilentlyContinue
        }
    }
}

function Test-FreshShell {
    param(
        [object]$Paths,
        [string]$PersistentPath,
        [switch]$ExpectAbsent
    )

    $probeRoot = Join-Path ([IO.Path]::GetTempPath()) ("w18-shell-" + [Guid]::NewGuid().ToString("N"))
    $cwd = Join-Path $probeRoot "arbitrary cwd"
    $probeHome = Join-Path $probeRoot "home"
    try {
        Assert-DirectorySafe $cwd
        Assert-DirectorySafe $probeHome
        $overrides = @{
            W18_DOCTOR_HOME = $probeHome
            W18_DOCTOR_WORKSPACE = $cwd
        }
        $powershell = Join-Path $env:SystemRoot "System32\WindowsPowerShell\v1.0\powershell.exe"
        if (-not (Test-Path -LiteralPath $powershell -PathType Leaf)) { throw "Windows PowerShell 5.1 ausente" }

        $resolutionCode = if ($ExpectAbsent) {
            '& { $c = Get-Command -Name ''llm-agent'' -CommandType Application -ErrorAction SilentlyContinue; if ($null -ne $c) { Write-Output (''COMMAND='' + $c.Source) } else { Write-Output ''COMMAND=ABSENT'' } }'
        }
        else {
            '& { $ErrorActionPreference = ''Stop''; $c = Get-Command -Name ''llm-agent'' -CommandType Application -ErrorAction Stop; Write-Output (''COMMAND='' + $c.Source) }'
        }
        $resolution = Invoke-ExplicitProcess $powershell @(
            "-NoLogo", "-NoProfile", "-NonInteractive", "-ExecutionPolicy", "Bypass", "-Command", $resolutionCode
        ) $cwd $PersistentPath $overrides -StepName "fresh no-profile shell / command resolution"
        $resolutionOutput = Assert-ProcessSuccess $resolution "fresh command resolution"
        if ($ExpectAbsent) {
            foreach ($line in @($resolutionOutput -split "`r?`n" | Where-Object { $_ -like "COMMAND=*" })) {
                $resolvedAbsent = $line.Substring(8).Trim()
                if ($resolvedAbsent -and $resolvedAbsent -ne "ABSENT" -and
                    [IO.Path]::GetFullPath($resolvedAbsent).Equals([IO.Path]::GetFullPath($Paths.StableLauncher), [StringComparison]::OrdinalIgnoreCase)) {
                    throw "fresh shell ainda resolve o launcher W18 após uninstall"
                }
            }
            return $resolutionOutput
        }

        $commandLine = @($resolutionOutput -split "`r?`n" | Where-Object { $_ -like "COMMAND=*" })
        if ($commandLine.Count -ne 1) { throw "fresh shell não reportou exatamente um comando llm-agent" }
        $resolved = $commandLine[0].Substring(8).Trim()
        $resolvedPath = [IO.Path]::GetFullPath($resolved)
        if (-not $resolvedPath.Equals([IO.Path]::GetFullPath($Paths.StableLauncher), [StringComparison]::OrdinalIgnoreCase)) {
            throw "fresh shell resolveu llm-agent fora do launcher estável: $resolvedPath"
        }

        # Pass the path resolved by the command-resolution child to every
        # subsequent fresh shell.  This keeps PATH lookup and command
        # execution as separate, observable substeps without reparsing a
        # path into PowerShell source.
        $overrides["W18_RESOLVED_LAUNCHER"] = $resolvedPath
        $versionCode = '& { $ErrorActionPreference = ''Stop''; & $env:W18_RESOLVED_LAUNCHER --version }'
        $versionResult = Invoke-ExplicitProcess $powershell @(
            "-NoLogo", "-NoProfile", "-NonInteractive", "-ExecutionPolicy", "Bypass", "-Command", $versionCode
        ) $cwd $PersistentPath $overrides -StepName "fresh no-profile shell / --version"
        $versionOutput = Assert-ProcessSuccess $versionResult "fresh --version"
        if ($versionOutput.Trim() -cne "llm-agent 0.2.0rc1") {
            throw "fresh shell não informou a versão exata"
        }

        $helpCode = '& { $ErrorActionPreference = ''Stop''; & $env:W18_RESOLVED_LAUNCHER --help }'
        $helpResult = Invoke-ExplicitProcess $powershell @(
            "-NoLogo", "-NoProfile", "-NonInteractive", "-ExecutionPolicy", "Bypass", "-Command", $helpCode
        ) $cwd $PersistentPath $overrides -StepName "fresh no-profile shell / --help"
        $helpOutput = Assert-ProcessSuccess $helpResult "fresh --help"
        if ($helpOutput.ToLowerInvariant().IndexOf("usage:", [StringComparison]::Ordinal) -lt 0) {
            throw "fresh shell help não contém usage"
        }

        # A newly-created W18_DOCTOR_HOME is intentionally empty.  Initialize
        # only that disposable probe home before running the real doctor gate;
        # this mirrors the embedded-runtime acceptance precondition and does
        # not weaken doctor readiness.
        $configInitCode = '& { $ErrorActionPreference = ''Stop''; & $env:W18_RESOLVED_LAUNCHER config init --home $env:W18_DOCTOR_HOME }'
        $configInitResult = Invoke-ExplicitProcess $powershell @(
            "-NoLogo", "-NoProfile", "-NonInteractive", "-ExecutionPolicy", "Bypass", "-Command", $configInitCode
        ) $cwd $PersistentPath $overrides -StepName "fresh no-profile shell / config init"
        $configInitOutput = Assert-ProcessSuccess $configInitResult "fresh config init"

        $doctorCode = '& { $ErrorActionPreference = ''Stop''; & $env:W18_RESOLVED_LAUNCHER doctor --json --home $env:W18_DOCTOR_HOME --workspace $env:W18_DOCTOR_WORKSPACE }'
        $doctorResult = Invoke-ExplicitProcess $powershell @(
            "-NoLogo", "-NoProfile", "-NonInteractive", "-ExecutionPolicy", "Bypass", "-Command", $doctorCode
        ) $cwd $PersistentPath $overrides -StepName "fresh no-profile shell / doctor --json"
        $doctorOutput = Assert-ProcessSuccess $doctorResult "fresh doctor --json"
        if ($doctorOutput.ToLowerInvariant().IndexOf("offline_ready", [StringComparison]::Ordinal) -lt 0) {
            throw "fresh shell não executou doctor offline"
        }
        return @(
            $resolutionOutput.TrimEnd(),
            $versionOutput.TrimEnd(),
            $helpOutput.TrimEnd(),
            $configInitOutput.TrimEnd(),
            $doctorOutput.TrimEnd()
        ) -join [Environment]::NewLine
    }
    finally {
        if (Test-Path -LiteralPath $probeRoot) {
            Remove-Item -LiteralPath $probeRoot -Recurse -Force -ErrorAction SilentlyContinue
        }
    }
}

function Test-PersistentCollision {
    param(
        [object]$Paths,
        [object]$UserSnapshot
    )

    $withoutProduct = Remove-OwnedPathSegment $UserSnapshot $Paths.OwnedPath
    $persistent = if ($withoutProduct.changed -or $withoutProduct.value -ne $UserSnapshot.value) {
        $machine = [Environment]::ExpandEnvironmentVariables([string](Get-MachinePathValue))
        $user = if ($withoutProduct.value) { [Environment]::ExpandEnvironmentVariables([string]$withoutProduct.value) } else { "" }
        if ($machine -and $user) { "$machine;$user" } else { $machine + $user }
    }
    else {
        Get-PersistentPath $UserSnapshot
    }
    $probeRoot = Join-Path ([IO.Path]::GetTempPath()) ("w18-collision-" + [Guid]::NewGuid().ToString("N"))
    try {
        Assert-DirectorySafe $probeRoot
        $code = '& { $c = Get-Command -Name ''llm-agent'' -CommandType Application -ErrorAction SilentlyContinue; if ($null -ne $c) { Write-Output $c.Source } }'
        $powershell = Join-Path $env:SystemRoot "System32\WindowsPowerShell\v1.0\powershell.exe"
        $result = Invoke-ExplicitProcess $powershell @(
            "-NoLogo", "-NoProfile", "-NonInteractive", "-ExecutionPolicy", "Bypass", "-Command", $code
        ) $probeRoot $persistent -StepName "persistent collision probe"
        $output = Assert-ProcessSuccess $result "persistent collision probe" | Out-String
        foreach ($line in @($output -split "`r?`n" | Where-Object { $_.Trim() })) {
            $resolved = [IO.Path]::GetFullPath($line.Trim())
            if (-not $resolved.Equals([IO.Path]::GetFullPath($Paths.StableLauncher), [StringComparison]::OrdinalIgnoreCase)) {
                throw "colisão persistente de llm-agent detectada em: $resolved"
            }
        }
    }
    finally {
        if (Test-Path -LiteralPath $probeRoot) {
            Remove-Item -LiteralPath $probeRoot -Recurse -Force -ErrorAction SilentlyContinue
        }
    }
}

function New-JournalDocument {
    param(
        [string]$OperationName,
        [object]$Paths,
        [bool]$PriorInstallRootPresent,
        [string]$TransactionId,
        [string]$TransactionRoot,
        [string]$CandidateId,
        [string]$CandidatePath,
        [string]$CandidateStage,
        [string]$CandidateBackup,
        [string]$PayloadSha256,
        [string]$PayloadInventorySha256,
        [object]$PriorPath,
        [bool]$PriorLauncherPresent,
        [string]$PriorLauncherSha256,
        [string]$PriorLauncherBackup,
        [bool]$PriorReceiptPresent,
        [string]$PriorReceiptBackup,
        [string]$PriorCandidateId,
        [string]$PriorPreviousCandidateId
    )

    $now = [DateTime]::UtcNow.ToString("o")
    return [pscustomobject][ordered]@{
        schema_version = $script:JournalSchemaVersion
        operation = $OperationName
        transaction_id = $TransactionId
        state = "PREPARED"
        install_root = [string]$Paths.InstallRoot
        prior_install_root_present = $PriorInstallRootPresent
        stable_launcher = [string]$Paths.StableLauncher
        owned_path = [string]$Paths.OwnedPath
        candidate_id = $CandidateId
        candidate_path = $CandidatePath
        candidate_stage = $CandidateStage
        candidate_backup = $CandidateBackup
        transaction_root = $TransactionRoot
        payload_sha256 = $PayloadSha256
        payload_inventory_sha256 = $PayloadInventorySha256
        prior_path = [pscustomobject][ordered]@{
            key_present = [bool]$PriorPath.key_present
            present = [bool]$PriorPath.present
            value = $PriorPath.value
            kind = $PriorPath.kind
        }
        prior_launcher_present = $PriorLauncherPresent
        prior_launcher_sha256 = $PriorLauncherSha256
        prior_launcher_backup = $PriorLauncherBackup
        launcher_promoted = $false
        path_mutated = $false
        prior_receipt_present = $PriorReceiptPresent
        prior_receipt_backup = $PriorReceiptBackup
        prior_candidate_id = $PriorCandidateId
        prior_previous_candidate_id = $PriorPreviousCandidateId
        created_at = $now
        updated_at = $now
    }
}

function Assert-Journal {
    param(
        [object]$Journal,
        [object]$Paths
    )

    Assert-ExactProperties $Journal @(
        "schema_version", "operation", "transaction_id", "state", "install_root", "prior_install_root_present", "stable_launcher", "owned_path",
        "candidate_id", "candidate_path", "candidate_stage", "candidate_backup", "transaction_root",
        "payload_sha256", "payload_inventory_sha256", "prior_path", "prior_launcher_present", "prior_launcher_sha256",
        "prior_launcher_backup", "launcher_promoted", "path_mutated", "prior_receipt_present", "prior_receipt_backup",
        "prior_candidate_id", "prior_previous_candidate_id", "created_at", "updated_at"
    ) "transaction journal"
    if ([int]$Journal.schema_version -ne $script:JournalSchemaVersion) { throw "schema de journal desconhecido" }
    if ([string]$Journal.operation -notin @("install", "uninstall")) { throw "operação de journal desconhecida" }
    if ([string]$Journal.state -notin $script:JournalStates) { throw "estado de journal desconhecido" }
    foreach ($field in @("prior_install_root_present", "prior_launcher_present", "launcher_promoted", "path_mutated", "prior_receipt_present")) {
        if ($Journal.$field -isnot [bool]) { throw "journal.$field invalido" }
    }
    if ([string]$Journal.install_root -cne [string]$Paths.InstallRoot -or
        [string]$Journal.stable_launcher -cne [string]$Paths.StableLauncher -or
        [string]$Journal.owned_path -cne [string]$Paths.OwnedPath) {
        throw "journal não pertence à instalação W18 atual"
    }
    Assert-ExactProperties $Journal.prior_path @("key_present", "present", "value", "kind") "journal.prior_path"
    foreach ($field in @("key_present", "present")) {
        if ($Journal.prior_path.$field -isnot [bool]) { throw "journal.prior_path.$field invalido" }
    }
    if ($Journal.prior_path.present) {
        if (-not $Journal.prior_path.key_present -or $Journal.prior_path.value -isnot [string] -or
            [string]$Journal.prior_path.kind -notin @("String", "ExpandString")) {
            throw "snapshot de PATH no journal e invalido"
        }
    }
    elseif ($null -ne $Journal.prior_path.value -or $null -ne $Journal.prior_path.kind) {
        throw "snapshot ausente de PATH no journal e inconsistente"
    }
    foreach ($field in @("payload_sha256", "payload_inventory_sha256")) {
        Assert-Sha256 $Journal.$field "journal.$field" | Out-Null
    }

    $operation = [string]$Journal.operation
    $transactionId = [string]$Journal.transaction_id
    if ($transactionId -cnotmatch '^w18-(install|uninstall)-[0-9]{17}-[0-9a-f]{12}$' -or
        -not $transactionId.StartsWith("w18-$operation-", [StringComparison]::Ordinal)) {
        throw "journal.transaction_id invalido para a operacao"
    }
    $trustedTransactionRoot = Join-Path $Paths.TransactionsRoot $transactionId
    if ([string]$Journal.transaction_root -cne $trustedTransactionRoot) { throw "journal.transaction_root nao e canonico" }
    $candidateId = [string]$Journal.candidate_id
    if ($candidateId -cnotmatch '^w18-[0-9a-f]{32}$') { throw "journal.candidate_id invalido" }
    if ([string]$Journal.candidate_path -cne (Join-Path $Paths.VersionsRoot $candidateId)) { throw "journal.candidate_path nao e canonico" }

    if ($operation -eq "install") {
        if ([string]$Journal.candidate_stage -cne (Join-Path $trustedTransactionRoot "candidate-stage")) { throw "journal.candidate_stage nao e canonico" }
        $candidateBackup = [string]$Journal.candidate_backup
        if ($candidateBackup -cne "" -and $candidateBackup -cne (Join-Path $trustedTransactionRoot "candidate-backup")) { throw "journal.candidate_backup nao e canonico" }
    }
    else {
        if ([string]$Journal.candidate_stage -cne $trustedTransactionRoot) { throw "journal uninstall candidate_stage nao e canonico" }
        if ([string]$Journal.candidate_backup -cne (Join-Path $trustedTransactionRoot "uninstall-backup")) { throw "journal uninstall candidate_backup nao e canonico" }
    }

    $expectedLauncherBackup = if ($Journal.prior_launcher_present) { Join-Path $trustedTransactionRoot "prior-launcher.cmd" } else { "" }
    if ([string]$Journal.prior_launcher_backup -cne $expectedLauncherBackup) { throw "journal.prior_launcher_backup e inconsistente" }
    if ($Journal.prior_launcher_present) { Assert-Sha256 $Journal.prior_launcher_sha256 "journal.prior_launcher_sha256" | Out-Null }
    elseif ([string]$Journal.prior_launcher_sha256 -cne "") { throw "journal.prior_launcher_sha256 e inconsistente" }
    $expectedReceiptBackup = if ($Journal.prior_receipt_present) { Join-Path $trustedTransactionRoot "prior-receipt.json" } else { "" }
    if ([string]$Journal.prior_receipt_backup -cne $expectedReceiptBackup) { throw "journal.prior_receipt_backup e inconsistente" }
    foreach ($field in @("prior_candidate_id", "prior_previous_candidate_id")) {
        $value = [string]$Journal.$field
        if ($value -cne "" -and $value -cnotmatch '^w18-[0-9a-f]{32}$') { throw "journal.$field invalido" }
    }
    if (-not $Journal.prior_receipt_present -and ([string]$Journal.prior_candidate_id -cne "" -or [string]$Journal.prior_previous_candidate_id -cne "")) {
        throw "identidade de receipt anterior no journal e inconsistente"
    }
}

function Write-Journal {
    param(
        [object]$Paths,
        [object]$Journal
    )

    $Journal.updated_at = [DateTime]::UtcNow.ToString("o")
    Write-AtomicJson $Paths.JournalPath $Journal
}

function Set-JournalState {
    param(
        [object]$Paths,
        [object]$Journal,
        [string]$State
    )

    if ($script:JournalStates -notcontains $State) { throw "estado de journal não permitido: $State" }
    $Journal.state = $State
    Write-Journal $Paths $Journal
}

function Read-CurrentJournal {
    param([object]$Paths)

    if (-not (Test-Path -LiteralPath $Paths.JournalPath -PathType Leaf)) { return $null }
    if (Test-ReparsePoint $Paths.JournalPath) { throw "journal link-like recusado; recuperação abortada" }
    return (Read-JsonDocument $Paths.JournalPath)
}

function Get-OptionalReceipt {
    param([object]$Paths)

    if (-not (Test-Path -LiteralPath $Paths.ReceiptPath -PathType Leaf)) { return $null }
    if (Test-ReparsePoint $Paths.ReceiptPath) { throw "receipt link-like recusado; operação abortada" }
    $receipt = Read-JsonDocument $Paths.ReceiptPath
    Assert-ExactProperties $receipt @(
        "schema_version", "status", "install_root", "candidate_id", "source", "application", "payload",
        "runtime_lock_sha256", "embedded_python", "stable_launcher", "owned_path", "previous_candidate_id", "installed_at"
    ) "current-install receipt"
    if ([string]$receipt.schema_version -cne "W18-INSTALL-RECEIPT-V3" -or [string]$receipt.status -cne "committed") {
        throw "receipt W18 ausente ou corrompido; nenhuma remoção ampla será autorizada"
    }
    Assert-Sha256 $receipt.runtime_lock_sha256 "receipt.runtime_lock_sha256" | Out-Null
    Assert-Sha256 $receipt.payload.sha256 "receipt.payload.sha256" | Out-Null
    Assert-Sha256 $receipt.payload.inventory_sha256 "receipt.payload.inventory_sha256" | Out-Null
    Assert-Sha256 $receipt.stable_launcher.sha256 "receipt.stable_launcher.sha256" | Out-Null
    if ([string]$receipt.install_root -ine [string]$Paths.InstallRoot -or
        [string]$receipt.stable_launcher.path -ine [string]$Paths.StableLauncher -or
        [string]$receipt.owned_path -ine [string]$Paths.OwnedPath -or
        [string]$receipt.candidate_id -cnotmatch "^w18-[0-9a-f]{32}$") {
        throw "receipt não prova propriedade exclusiva da instalação W18"
    }
    Assert-ExactProperties $receipt.payload @("path", "sha256", "inventory", "inventory_sha256") "receipt.payload"
    Assert-ExactProperties $receipt.stable_launcher @("path", "sha256") "receipt.stable_launcher"
    Assert-ExactProperties $receipt.source @("status", "base_commit", "commit", "tree") "receipt.source"
    if ([string]$receipt.source.status -notin @("uncommitted_candidate", "committed_release")) {
        throw "receipt tem status de provenance inválido"
    }
    Assert-CommitOrTree $receipt.source.base_commit "receipt.source.base_commit" | Out-Null
    Assert-CommitOrTree $receipt.source.tree "receipt.source.tree" | Out-Null
    if ([string]$receipt.source.tree -ceq [string]$receipt.source.base_commit) {
        throw "receipt.source.tree não pode ser o commit-base"
    }
    if ([string]$receipt.source.status -eq "uncommitted_candidate") {
        if ($null -ne $receipt.source.commit) { throw "receipt candidato deve ter source.commit = null" }
    }
    elseif (($receipt.source.commit -isnot [string]) -or ([string]$receipt.source.commit -cnotmatch "^[0-9a-f]{40}$")) {
        throw "receipt release comprometido requer source.commit real"
    }
    if (($receipt.source.commit -is [string]) -and ([string]$receipt.source.commit -ceq [string]$receipt.source.tree)) {
        throw "receipt.source.commit não pode ser confundido com source.tree"
    }
    Assert-ExactProperties $receipt.application @("version", "wheel", "wheel_sha256") "receipt.application"
    if ([string]$receipt.application.version -cne "0.2.0rc1" -or
        [string]$receipt.application.wheel -cne "local_llm_agent-0.2.0rc1-py3-none-any.whl") {
        throw "receipt tem identidade de aplicação inválida"
    }
    Assert-Sha256 $receipt.application.wheel_sha256 "receipt.application.wheel_sha256" | Out-Null
    Assert-ExactProperties $receipt.embedded_python @(
        "implementation", "exact_version", "platform", "build_identity", "build_date"
    ) "receipt.embedded_python"
    if ([string]$receipt.payload.path -cne "payload-windows-x64.zip" -or
        [string]$receipt.payload.inventory -cne "payload-files.json" -or
        [string]$receipt.embedded_python.implementation -cne "cpython" -or
        [string]$receipt.embedded_python.exact_version -cne "3.12.14" -or
        [string]$receipt.embedded_python.platform -cne "windows-x86_64" -or
        [string]$receipt.embedded_python.build_identity -cne "cpython-3.12.14-windows-x86_64-none" -or
        [string]$receipt.embedded_python.build_date -cne "20260901") {
        throw "receipt W18 tem identidade de payload/runtime inválida"
    }
    Assert-NonEmptyString $receipt.installed_at "receipt.installed_at" | Out-Null
    if ($receipt.previous_candidate_id -and [string]$receipt.previous_candidate_id -cnotmatch "^w18-[0-9a-f]{32}$") {
        throw "receipt.previous_candidate_id inválido"
    }
    return $receipt
}

function Write-Receipt {
    param(
        [object]$Paths,
        [object]$Manifest,
        [object]$CandidateAcceptance,
        [string]$StableSha256,
        [string]$PreviousCandidateId
    )

    $receipt = [pscustomobject][ordered]@{
        schema_version = "W18-INSTALL-RECEIPT-V3"
        status = "committed"
        install_root = [string]$Paths.InstallRoot
        candidate_id = [string]$Manifest.candidate_id
        source = $Manifest.source
        application = [pscustomobject][ordered]@{
            version = [string]$Manifest.application.version
            wheel = [string]$Manifest.application.wheel
            wheel_sha256 = [string]$Manifest.application.wheel_sha256
        }
        payload = [pscustomobject][ordered]@{
            path = [string]$Manifest.payload.path
            sha256 = [string]$Manifest.payload.sha256
            inventory = [string]$Manifest.payload.inventory
            inventory_sha256 = [string]$Manifest.payload.inventory_sha256
        }
        runtime_lock_sha256 = [string]$Manifest.runtime_lock.sha256
        embedded_python = [pscustomobject][ordered]@{
            implementation = [string]$Manifest.python.implementation
            exact_version = [string]$Manifest.python.exact_version
            platform = [string]$Manifest.python.platform
            build_identity = [string]$Manifest.python.build_identity
            build_date = [string]$Manifest.python.build_date
        }
        stable_launcher = [pscustomobject][ordered]@{
            path = [string]$Paths.StableLauncher
            sha256 = $StableSha256
        }
        owned_path = [string]$Paths.OwnedPath
        previous_candidate_id = $PreviousCandidateId
        installed_at = [DateTime]::UtcNow.ToString("o")
    }
    Write-AtomicJson $Paths.ReceiptPath $receipt
    return $receipt
}

function Test-CandidateIntegrity {
    param(
        [object]$Paths,
        [object]$Manifest,
        [string]$CandidatePath
    )

    try {
        Assert-ContainedPath $Paths.VersionsRoot $CandidatePath
        if (-not (Test-Path -LiteralPath $CandidatePath -PathType Container) -or (Test-ReparsePoint $CandidatePath)) { return $false }
        $inventoryPath = Join-Path $CandidatePath "payload-files.json"
        $manifestPath = Join-Path $CandidatePath "manifest.json"
        if (-not (Test-Path -LiteralPath $inventoryPath -PathType Leaf) -or
            -not (Test-Path -LiteralPath $manifestPath -PathType Leaf) -or
            (Get-FileSha256 $inventoryPath) -cne [string]$Manifest.payload.inventory_sha256) { return $false }
        $candidateManifest = Read-JsonDocument $manifestPath
        Assert-Manifest $candidateManifest
        if ([string]$candidateManifest.candidate_id -cne [string]$Manifest.candidate_id) { return $false }
        if ((ConvertTo-CanonicalJson $candidateManifest) -cne (ConvertTo-CanonicalJson $Manifest)) { return $false }
        $inventory = Read-JsonDocument $inventoryPath
        $entries = Assert-PayloadInventory $inventory
        $acceptancePath = Join-Path $CandidatePath "acceptance.json"
        if (-not (Test-Path -LiteralPath $acceptancePath -PathType Leaf) -or (Test-ReparsePoint $acceptancePath)) { return $false }
        $acceptance = Read-JsonDocument $acceptancePath
        Assert-ExactProperties $acceptance @("schema_version", "status", "candidate_id", "payload", "probes") "candidate acceptance"
        if ([string]$acceptance.schema_version -cne "W18-CANDIDATE-ACCEPTANCE-V3" -or
            [string]$acceptance.status -cne "passed" -or
            [string]$acceptance.candidate_id -cne [string]$Manifest.candidate_id) { return $false }
        Assert-ExactProperties $acceptance.payload @("sha256", "inventory_sha256") "candidate acceptance.payload"
        if ([string]$acceptance.payload.sha256 -cne [string]$Manifest.payload.sha256 -or
            [string]$acceptance.payload.inventory_sha256 -cne [string]$Manifest.payload.inventory_sha256 -or
            $null -eq $acceptance.probes) { return $false }
        Verify-PayloadTree $CandidatePath $entries
        Assert-PayloadFirewall $CandidatePath
        return $true
    }
    catch {
        return $false
    }
}

function New-StableLauncherText {
    param([string]$CandidateId)

    if ($CandidateId -notmatch "^w18-[0-9a-f]{32}$") { throw "candidate id inválido para launcher estável" }
    # Bind the stable launcher to the exact versioned candidate by layout,
    # rather than embedding a Unicode-sensitive absolute path in a .cmd file.
    # %~dp0 is resolved by cmd.exe and preserves arbitrary user profile paths.
    return "@echo off`r`nsetlocal`r`nset `"PYTHONDONTWRITEBYTECODE=1`"`r`ncall `"%~dp0..\versions\$CandidateId\bin\llm-agent.cmd`" %*`r`nexit /b %ERRORLEVEL%`r`n"
}

function Promote-StableLauncher {
    param(
        [object]$Paths,
        [string]$CandidatePath
    )

    Assert-DirectorySafe $Paths.BinRoot
    $candidateLauncher = Join-Path $CandidatePath "bin\llm-agent.cmd"
    if (-not (Test-Path -LiteralPath $candidateLauncher -PathType Leaf)) { throw "candidate launcher ausente" }
    $candidateId = [IO.Path]::GetFileName([IO.Path]::GetFullPath($CandidatePath))
    $text = New-StableLauncherText $candidateId
    Write-AtomicText $Paths.StableLauncher $text
    return (Get-FileSha256 $Paths.StableLauncher)
}

function Assert-InstallRootLayout {
    param([object]$Paths)

    if (-not (Test-Path -LiteralPath $Paths.InstallRoot -PathType Container)) { return }
    if (Test-ReparsePoint $Paths.InstallRoot) { throw "install root link-like recusado" }
    $allowed = @("versions", "bin", "transactions", "current-install.json")
    foreach ($item in @(Get-ChildItem -LiteralPath $Paths.InstallRoot -Force)) {
        if ($allowed -cnotcontains [string]$item.Name) {
            throw "install root contém item fora da propriedade W18: $($item.Name)"
        }
        if (Test-ReparsePoint $item.FullName) { throw "install root contém item link-like: $($item.Name)" }
    }
}

function Ensure-InstallLayout {
    param([object]$Paths)

    Assert-DirectorySafe $Paths.InstallRoot
    Assert-DirectorySafe $Paths.VersionsRoot
    Assert-DirectorySafe $Paths.BinRoot
    Assert-DirectorySafe $Paths.TransactionsRoot
    Assert-InstallRootLayout $Paths
}

function Remove-OwnedTree {
    param(
        [string]$Root,
        [string]$Path
    )

    if (-not (Test-Path -LiteralPath $Path)) { return }
    Assert-ContainedPath $Root $Path
    if (Test-ReparsePoint $Path) { throw "remoção recusada para caminho link-like: $Path" }
    Remove-Item -LiteralPath $Path -Recurse -Force
}

function Copy-FileBackup {
    param(
        [string]$Source,
        [string]$Destination
    )

    Assert-ContainedPath (Split-Path -Parent $Destination) $Destination
    if (-not (Test-Path -LiteralPath $Source -PathType Leaf)) { throw "backup source ausente: $Source" }
    if (Test-ReparsePoint $Source) { throw "backup source link-like: $Source" }
    $parent = Split-Path -Parent $Destination
    Assert-DirectorySafe $parent
    Copy-Item -LiteralPath $Source -Destination $Destination -Force
    if ((Get-FileSha256 $Destination) -cne (Get-FileSha256 $Source)) { throw "backup não verificado: $Destination" }
}

function Restore-FileBackup {
    param(
        [string]$Backup,
        [string]$Destination
    )

    if (-not (Test-Path -LiteralPath $Backup -PathType Leaf)) { throw "backup necessário ausente: $Backup" }
    if (Test-ReparsePoint $Backup) { throw "backup link-like recusado: $Backup" }
    Write-AtomicBytes $Destination ([IO.File]::ReadAllBytes($Backup))
}

function Test-PathSnapshotEqual {
    param(
        [object]$Actual,
        [object]$Expected
    )

    if ([bool]$Actual.key_present -ne [bool]$Expected.key_present -or
        [bool]$Actual.present -ne [bool]$Expected.present -or
        [string]$Actual.value -cne [string]$Expected.value -or
        [string]$Actual.kind -cne [string]$Expected.kind) {
        return $false
    }
    return $true
}

function Remove-JournalAndTransaction {
    param(
        [object]$Paths,
        [object]$Journal
    )

    if (Test-Path -LiteralPath $Journal.transaction_root) {
        Assert-ContainedPath $Paths.TransactionsRoot ([string]$Journal.transaction_root)
        if (Test-ReparsePoint $Journal.transaction_root) { throw "transaction root link-like" }
        Remove-Item -LiteralPath $Journal.transaction_root -Recurse -Force
    }
    if (Test-Path -LiteralPath $Paths.JournalPath) {
        Assert-ContainedPath $Paths.TransactionsRoot $Paths.JournalPath
        Remove-Item -LiteralPath $Paths.JournalPath -Force
    }
}

function Remove-CreatedTransactionRootIfEmpty {
    param(
        [object]$Paths,
        [string]$TransactionRoot
    )

    if (-not (Test-Path -LiteralPath $TransactionRoot)) { return }
    try {
        Assert-ContainedPath $Paths.TransactionsRoot $TransactionRoot
        if (-not (Test-Path -LiteralPath $TransactionRoot -PathType Container) -or (Test-ReparsePoint $TransactionRoot)) {
            Write-Warning "cleanup do scaffolding W18 preservou transaction root nao seguro: $TransactionRoot"
            return
        }
        if (@(Get-ChildItem -LiteralPath $TransactionRoot -Force).Count -ne 0) {
            Write-Warning "cleanup do scaffolding W18 preservou transaction root nao vazio: $TransactionRoot"
            return
        }
        Remove-Item -LiteralPath $TransactionRoot -Force
    }
    catch {
        Write-Warning "cleanup do scaffolding W18 preservou transaction root por falha segura: $($_.Exception.Message)"
    }
}

function Remove-CreatedInstallScaffolding {
    param(
        [object]$Paths,
        [object]$Journal
    )

    if ([string]$Journal.operation -ne "install" -or [bool]$Journal.prior_install_root_present) { return }
    if (-not (Test-Path -LiteralPath $Paths.InstallRoot)) { return }
    if (-not (Test-Path -LiteralPath $Paths.InstallRoot -PathType Container)) {
        Write-Warning "cleanup do scaffolding W18 preservou install root que nao e diretorio: $($Paths.InstallRoot)"
        return
    }

    try {
        if (Test-ReparsePoint $Paths.InstallRoot) {
            Write-Warning "cleanup do scaffolding W18 preservou install root link-like: $($Paths.InstallRoot)"
            return
        }
        $allowed = @("versions", "bin", "transactions")
        $children = @(Get-ChildItem -LiteralPath $Paths.InstallRoot -Force)
        foreach ($item in $children) {
            if (-not $item.PSIsContainer -or $allowed -cnotcontains [string]$item.Name -or (Test-ReparsePoint $item.FullName)) {
                Write-Warning "cleanup do scaffolding W18 preservou conteudo nao reconhecido: $($item.FullName)"
                return
            }
            if (@(Get-ChildItem -LiteralPath $item.FullName -Force).Count -ne 0) {
                Write-Warning "cleanup do scaffolding W18 preservou diretorio nao vazio: $($item.FullName)"
                return
            }
        }
        foreach ($directory in @($Paths.VersionsRoot, $Paths.BinRoot, $Paths.TransactionsRoot)) {
            if (Test-Path -LiteralPath $directory) {
                Assert-ContainedPath $Paths.InstallRoot $directory
                if (-not (Test-Path -LiteralPath $directory -PathType Container) -or (Test-ReparsePoint $directory)) {
                    Write-Warning "cleanup do scaffolding W18 preservou diretorio nao seguro: $directory"
                    return
                }
                if (@(Get-ChildItem -LiteralPath $directory -Force).Count -ne 0) {
                    Write-Warning "cleanup do scaffolding W18 preservou conteudo surgido durante a verificacao: $directory"
                    return
                }
                Remove-Item -LiteralPath $directory -Force
            }
        }
        if (@(Get-ChildItem -LiteralPath $Paths.InstallRoot -Force).Count -ne 0) {
            Write-Warning "cleanup do scaffolding W18 preservou conteudo surgido durante a verificacao: $($Paths.InstallRoot)"
            return
        }
        Remove-Item -LiteralPath $Paths.InstallRoot -Force
    }
    catch {
        Write-Warning "cleanup do scaffolding W18 preservou install root por falha segura: $($_.Exception.Message)"
    }
}

function Invoke-RollbackTransaction {
    param(
        [object]$Paths,
        [object]$Journal
    )

    $script:CurrentJournal = $Journal
    try {
        Set-JournalState $Paths $Journal "ROLLING_BACK"
        if ([bool]$Journal.launcher_promoted) {
            if ([bool]$Journal.prior_launcher_present) {
                Restore-FileBackup ([string]$Journal.prior_launcher_backup) $Paths.StableLauncher
            }
            elseif (Test-Path -LiteralPath $Paths.StableLauncher) {
                Assert-ContainedPath $Paths.BinRoot $Paths.StableLauncher
                Remove-Item -LiteralPath $Paths.StableLauncher -Force
            }
        }
        if ([bool]$Journal.path_mutated) {
            $prior = [pscustomobject]@{
                key_present = [bool]$Journal.prior_path.key_present
                present = [bool]$Journal.prior_path.present
                value = $Journal.prior_path.value
                kind = $Journal.prior_path.kind
            }
            Restore-UserPathSnapshot $prior
            $after = Read-RegistryPathSnapshot
            if (-not (Test-PathSnapshotEqual $after $prior)) { throw "rollback não restaurou o User PATH exato" }
        }
        if ([string]$Journal.operation -eq "install") {
            $candidate = [string]$Journal.candidate_path
            if ($candidate) {
                Assert-ContainedPath $Paths.VersionsRoot $candidate
                if (Test-Path -LiteralPath $candidate) { Remove-OwnedTree $Paths.VersionsRoot $candidate }
            }
            $candidateBackup = [string]$Journal.candidate_backup
            if ($candidateBackup) {
                Assert-ContainedPath $Journal.transaction_root $candidateBackup
                if (Test-Path -LiteralPath $candidateBackup) {
                    Move-Item -LiteralPath $candidateBackup -Destination $candidate
                }
            }
        }
        else {
            $backupRoot = [string]$Journal.candidate_backup
            if ($backupRoot) {
                Assert-ContainedPath $Journal.transaction_root $backupRoot
                $backupVersions = Join-Path $backupRoot "versions"
                $backupBin = Join-Path $backupRoot "bin"
                if (Test-Path -LiteralPath $backupVersions) {
                    if (Test-Path -LiteralPath $Paths.VersionsRoot) { Remove-OwnedTree $Paths.InstallRoot $Paths.VersionsRoot }
                    Move-Item -LiteralPath $backupVersions -Destination $Paths.VersionsRoot
                }
                if (Test-Path -LiteralPath $backupBin) {
                    if (Test-Path -LiteralPath $Paths.BinRoot) { Remove-OwnedTree $Paths.InstallRoot $Paths.BinRoot }
                    Move-Item -LiteralPath $backupBin -Destination $Paths.BinRoot
                }
            }
        }
        if ([bool]$Journal.prior_receipt_present) {
            Restore-FileBackup ([string]$Journal.prior_receipt_backup) $Paths.ReceiptPath
        }
        elseif (Test-Path -LiteralPath $Paths.ReceiptPath) {
            Assert-ContainedPath $Paths.InstallRoot $Paths.ReceiptPath
            Remove-Item -LiteralPath $Paths.ReceiptPath -Force
        }
        Set-JournalState $Paths $Journal "ROLLED_BACK"
        Remove-JournalAndTransaction $Paths $Journal
        Remove-CreatedInstallScaffolding $Paths $Journal
        $script:CurrentJournal = $null
    }
    catch {
        try {
            $Journal.state = "FAILED_RECOVERY"
            Write-Journal $Paths $Journal
        }
        catch { }
        $script:CurrentJournal = $Journal
        throw
    }
}

function Recover-IncompleteJournal {
    param([object]$Paths)

    $journal = Read-CurrentJournal $Paths
    if ($null -eq $journal) { return }
    Assert-Journal $journal $Paths
    if ([string]$journal.state -eq "FAILED_RECOVERY") {
        throw "journal está em FAILED_RECOVERY; arquivos foram preservados para recuperação manual"
    }
    if ([string]$journal.state -in @("COMMITTED", "ROLLED_BACK")) {
        Remove-JournalAndTransaction $Paths $journal
        return
    }
    Invoke-RollbackTransaction $Paths $journal
}

function Test-ReceiptForManifest {
    param(
        [object]$Paths,
        [object]$Manifest,
        [object]$Receipt,
        [object]$UserSnapshot
    )

    if ($null -eq $Receipt) { return $false }
    if ([string]$Receipt.candidate_id -cne [string]$Manifest.candidate_id -or
        [string]$Receipt.source.status -cne [string]$Manifest.source.status -or
        [string]$Receipt.source.base_commit -cne [string]$Manifest.source.base_commit -or
        [string]$Receipt.source.commit -cne [string]$Manifest.source.commit -or
        [string]$Receipt.source.tree -cne [string]$Manifest.source.tree -or
        [string]$Receipt.application.version -cne [string]$Manifest.application.version -or
        [string]$Receipt.application.wheel -cne [string]$Manifest.application.wheel -or
        [string]$Receipt.application.wheel_sha256 -cne [string]$Manifest.application.wheel_sha256 -or
        [string]$Receipt.payload.sha256 -cne [string]$Manifest.payload.sha256 -or
        [string]$Receipt.payload.inventory_sha256 -cne [string]$Manifest.payload.inventory_sha256 -or
        [string]$Receipt.runtime_lock_sha256 -cne [string]$Manifest.runtime_lock.sha256 -or
        [string]$Receipt.embedded_python.implementation -cne [string]$Manifest.python.implementation -or
        [string]$Receipt.embedded_python.exact_version -cne [string]$Manifest.python.exact_version -or
        [string]$Receipt.embedded_python.platform -cne [string]$Manifest.python.platform -or
        [string]$Receipt.embedded_python.build_identity -cne [string]$Manifest.python.build_identity -or
        [string]$Receipt.embedded_python.build_date -cne [string]$Manifest.python.build_date) { return $false }
    $pathMutation = Add-OwnedPathSegment $UserSnapshot $Paths.OwnedPath
    if ($pathMutation.changed -or $pathMutation.owned_count -ne 1) { return $false }
    if (-not (Test-Path -LiteralPath $Paths.StableLauncher -PathType Leaf)) { return $false }
    if ((Get-FileSha256 $Paths.StableLauncher) -cne [string]$Receipt.stable_launcher.sha256) { return $false }
    $candidatePath = Join-Path $Paths.VersionsRoot ([string]$Manifest.candidate_id)
    if (-not (Test-CandidateIntegrity $Paths $Manifest $candidatePath)) { return $false }
    try {
        Test-FreshShell $Paths (Get-PersistentPath $UserSnapshot) | Out-Null
        return $true
    }
    catch {
        return $false
    }
}

function Write-CandidateMetadata {
    param(
        [string]$CandidatePath,
        [object]$Bundle,
        [object]$Acceptance
    )

    Write-AtomicBytes (Join-Path $CandidatePath "payload-files.json") ([IO.File]::ReadAllBytes((Join-Path $Bundle.Root "payload-files.json")))
    Write-AtomicBytes (Join-Path $CandidatePath "manifest.json") ([IO.File]::ReadAllBytes((Join-Path $Bundle.Root "release-manifest.json")))
    Write-AtomicJson (Join-Path $CandidatePath "acceptance.json") $Acceptance
}

function Cleanup-StaleCandidates {
    param(
        [object]$Paths,
        [string]$CurrentCandidateId,
        [string]$PreviousCandidateId
    )

    if (-not (Test-Path -LiteralPath $Paths.VersionsRoot -PathType Container)) { return }
    foreach ($item in @(Get-ChildItem -LiteralPath $Paths.VersionsRoot -Force)) {
        if (-not $item.PSIsContainer) { continue }
        if (Test-ReparsePoint $item.FullName) { continue }
        $name = [string]$item.Name
        if ($name -notmatch "^w18-[0-9a-f]{32}$") { continue }
        $canonical = Join-Path $Paths.VersionsRoot $name
        if ([IO.Path]::GetFullPath($item.FullName) -cne [IO.Path]::GetFullPath($canonical)) {
            Write-Warning "cleanup bounded preservou candidato com diretório não canônico: $name"
            continue
        }
        if ($name -eq $CurrentCandidateId -or $name -eq $PreviousCandidateId) { continue }
        $candidateLease = $null
        try {
            $candidateLease = Enter-W18CandidateLease $name
            if ([string]$candidateLease.status -eq "active") {
                Write-Warning "W18_CLEANUP_ACTIVE_PRESERVED candidate_id=${name} active_object=$($candidateLease.active_name)"
                continue
            }
            if ([string]$candidateLease.status -ne "acquired") {
                Write-Warning "cleanup bounded preservou candidato ${name}: aquisição ambígua/acesso negado"
                continue
            }
            Write-Warning "W18_CLEANUP_INACTIVE_ELIGIBLE candidate_id=${name} active_object=$($candidateLease.active_name)"
            # The gate remains owned for the whole destructive operation.
            Remove-OwnedTree $Paths.VersionsRoot $item.FullName
        }
        catch {
            Write-Warning "cleanup bounded não removeu candidato obsoleto ${name}: $($_.Exception.Message)"
        }
        finally {
            if ($null -ne $candidateLease -and [string]$candidateLease.status -eq "acquired") {
                Exit-W18CandidateLease $candidateLease
            }
        }
    }
}

function Remove-InstallRootAfterUninstall {
    param([object]$Paths)

    foreach ($directory in @($Paths.VersionsRoot, $Paths.BinRoot, $Paths.TransactionsRoot)) {
        if (Test-Path -LiteralPath $directory) {
            Remove-OwnedTree $Paths.InstallRoot $directory
        }
    }
    if (Test-Path -LiteralPath $Paths.InstallRoot -PathType Container) {
        $remaining = @(Get-ChildItem -LiteralPath $Paths.InstallRoot -Force)
        if ($remaining.Count -eq 0) {
            Remove-Item -LiteralPath $Paths.InstallRoot -Force
        }
    }
}

function Assert-BinOwnership {
    param([object]$Paths)

    if (-not (Test-Path -LiteralPath $Paths.BinRoot -PathType Container)) { return }
    foreach ($item in @(Get-ChildItem -LiteralPath $Paths.BinRoot -Force)) {
        if ($item.Name -cne "llm-agent.cmd" -or (Test-ReparsePoint $item.FullName)) {
            throw "bin W18 contém item não pertencente ao receipt: $($item.Name)"
        }
    }
}

function Invoke-Install {
    param([string]$RequestedBundleRoot)

    $bundleRoot = Get-BundleDirectory $RequestedBundleRoot
    $bundle = Read-ValidatedBundle $bundleRoot
    $paths = Get-InstallPaths
    $priorInstallRootPresent = [bool](Test-Path -LiteralPath $paths.InstallRoot)
    $transactionRoot = ""
    $transactionRootOwnedByAttempt = $false
    try {
    Ensure-InstallLayout $paths
    Assert-BinOwnership $paths
    $priorReceipt = Get-OptionalReceipt $paths
    $priorUserPath = Read-RegistryPathSnapshot
    if ($null -eq $priorReceipt -and (Test-Path -LiteralPath $paths.StableLauncher -PathType Leaf)) {
        throw "launcher estável W18 sem receipt; instalação falha fechada para não assumir propriedade"
    }

    if ($null -ne $priorReceipt -and (Test-ReceiptForManifest $paths $bundle.Manifest $priorReceipt $priorUserPath)) {
        # A same-candidate reinstall is also a normal cleanup surface.  This
        # lets an inactive stale candidate be collected after its application
        # lease has been released, without rewriting the committed receipt.
        try { Cleanup-StaleCandidates $paths ([string]$bundle.Manifest.candidate_id) ([string]$priorReceipt.previous_candidate_id) } catch { Write-Warning "cleanup bounded falhou: $($_.Exception.Message)" }
        Write-Output "W18 instalação idempotente: candidato $($bundle.Manifest.candidate_id) já está validado"
        return
    }

    Test-PersistentCollision $paths $priorUserPath
    $persistentBefore = Get-PersistentPath $priorUserPath
    $candidateId = [string]$bundle.Manifest.candidate_id
    $candidatePath = Join-Path $paths.VersionsRoot $candidateId
    Assert-ContainedPath $paths.VersionsRoot $candidatePath
    $transactionId = "w18-install-$([DateTime]::UtcNow.ToString('yyyyMMddHHmmssfff'))-$([Guid]::NewGuid().ToString('N').Substring(0, 12))"
    $transactionRoot = Join-Path $paths.TransactionsRoot $transactionId
    $candidateStage = Join-Path $transactionRoot "candidate-stage"
    $candidateBackup = if (Test-Path -LiteralPath $candidatePath) { Join-Path $transactionRoot "candidate-backup" } else { "" }
    if ((Test-Path -LiteralPath $candidatePath -PathType Leaf) -or (Test-ReparsePoint $candidatePath)) {
        throw "candidate path existente não é uma versão W18 segura"
    }
    $transactionRootOwnedByAttempt = -not [bool](Test-Path -LiteralPath $transactionRoot)
    Assert-DirectorySafe $transactionRoot
    $priorLauncherPresent = Test-Path -LiteralPath $paths.StableLauncher -PathType Leaf
    $priorLauncherSha = if ($priorLauncherPresent) { Get-FileSha256 $paths.StableLauncher } else { "" }
    if ($priorLauncherPresent -and $null -eq $priorReceipt) {
        throw "launcher estável W18 sem receipt; nenhuma substituição será feita"
    }
    if ($priorLauncherPresent -and $null -ne $priorReceipt -and $priorLauncherSha -cne [string]$priorReceipt.stable_launcher.sha256) {
        throw "launcher estável divergiu do receipt; instalação falha fechada"
    }
    $priorReceiptPresent = $null -ne $priorReceipt
    $priorLauncherBackup = if ($priorLauncherPresent) { Join-Path $transactionRoot "prior-launcher.cmd" } else { "" }
    $priorReceiptBackup = if ($priorReceiptPresent) { Join-Path $transactionRoot "prior-receipt.json" } else { "" }
    $priorCandidateId = if ($null -ne $priorReceipt) { [string]$priorReceipt.candidate_id } else { "" }
    $priorPreviousCandidateId = if ($null -ne $priorReceipt) { [string]$priorReceipt.previous_candidate_id } else { "" }
    $journal = New-JournalDocument "install" $paths $priorInstallRootPresent $transactionId $transactionRoot $candidateId $candidatePath $candidateStage $candidateBackup ([string]$bundle.Manifest.payload.sha256) ([string]$bundle.Manifest.payload.inventory_sha256) $priorUserPath $priorLauncherPresent $priorLauncherSha $priorLauncherBackup $priorReceiptPresent $priorReceiptBackup $priorCandidateId $priorPreviousCandidateId
    Assert-Journal $journal $paths
    Write-Journal $paths $journal
    $script:CurrentJournal = $journal
    }
    catch {
        if (-not $priorInstallRootPresent -and $null -eq $script:CurrentJournal) {
            if ($transactionRootOwnedByAttempt -and $transactionRoot) {
                Remove-CreatedTransactionRootIfEmpty $paths $transactionRoot
            }
            Remove-CreatedInstallScaffolding $paths ([pscustomobject]@{ operation = "install"; prior_install_root_present = $false })
        }
        throw
    }
    $committed = $false
    try {
        if ($priorLauncherPresent) { Copy-FileBackup $paths.StableLauncher $priorLauncherBackup }
        if ($priorReceiptPresent) { Copy-FileBackup $paths.ReceiptPath $priorReceiptBackup }
        if ($candidateBackup) {
            Assert-ContainedPath $paths.VersionsRoot $candidatePath
            Assert-ContainedPath $transactionRoot $candidateBackup
            Move-Item -LiteralPath $candidatePath -Destination $candidateBackup
        }
        Extract-Payload $bundle $candidateStage
        Test-EmbeddedRuntime $candidateStage $persistentBefore | Out-Null
        Set-JournalState $paths $journal "STAGED"
        Move-Item -LiteralPath $candidateStage -Destination $candidatePath
        $acceptanceProbe = Test-EmbeddedRuntime $candidatePath $persistentBefore
        $acceptance = [pscustomobject][ordered]@{
            schema_version = "W18-CANDIDATE-ACCEPTANCE-V3"
            status = "passed"
            candidate_id = $candidateId
            payload = [pscustomobject][ordered]@{
                sha256 = [string]$bundle.Manifest.payload.sha256
                inventory_sha256 = [string]$bundle.Manifest.payload.inventory_sha256
            }
            probes = $acceptanceProbe
        }
        Write-CandidateMetadata $candidatePath $bundle $acceptance
        Set-JournalState $paths $journal "STAGED"

        # Persist the rollback intent before the first byte of stable-launcher
        # replacement.  If the process is hard-stopped between the replace and
        # the following journal update, recovery still restores the prior file.
        $journal.launcher_promoted = $true
        Write-Journal $paths $journal
        $stableSha = Promote-StableLauncher $paths $candidatePath
        # W18_TEST_SEAM_A47_AFTER_LAUNCHER_PROMOTION
        Set-JournalState $paths $journal "LAUNCHER_PROMOTED"
        $stableProbe = Invoke-CmdLauncher $paths.StableLauncher @("--version") ([IO.Path]::GetTempPath()) $persistentBefore "stable launcher --version"
        if ((Assert-ProcessSuccess $stableProbe "launcher estável --version").Trim() -cne "llm-agent 0.2.0rc1") {
            throw "launcher estável reportou versão incorreta"
        }

        $pathMutation = Add-OwnedPathSegment $priorUserPath $paths.OwnedPath
        if ($pathMutation.changed) {
            # Journal intent before the registry write so a crash cannot leave
            # a User PATH mutation without an exact rollback marker.
            $journal.path_mutated = $true
            Write-Journal $paths $journal
            Set-UserPathValue $priorUserPath ([string]$pathMutation.value)
            # W18_TEST_SEAM_A48_AFTER_PATH_MUTATION
            Write-Journal $paths $journal
            $afterPath = Read-RegistryPathSnapshot
            $expectedKind = if ($priorUserPath.present -and $priorUserPath.kind -eq "String") { "String" } else { "ExpandString" }
            $expectedPath = [pscustomobject]@{ key_present = $true; present = $true; value = [string]$pathMutation.value; kind = $expectedKind }
            if (-not (Test-PathSnapshotEqual $afterPath $expectedPath)) { throw "read-back do User PATH divergiu" }
        }
        Set-JournalState $paths $journal "PATH_MUTATED"
        $afterPersistent = Get-PersistentPath (Read-RegistryPathSnapshot)
        Test-FreshShell $paths $afterPersistent | Out-Null
        Set-JournalState $paths $journal "FRESH_SHELL_VERIFIED"
        $previousCandidateId = if ($priorCandidateId -and $priorCandidateId -cne $candidateId) { $priorCandidateId } else { $priorPreviousCandidateId }
        Write-Receipt $paths $bundle.Manifest $acceptance $stableSha $previousCandidateId | Out-Null
        Set-JournalState $paths $journal "COMMITTED"
        $committed = $true
        try { Remove-JournalAndTransaction $paths $journal } catch { Write-Warning "journal committed será removido na próxima recuperação: $($_.Exception.Message)" }
        $script:CurrentJournal = $null
        try { Cleanup-StaleCandidates $paths $candidateId $previousCandidateId } catch { Write-Warning "cleanup bounded falhou: $($_.Exception.Message)" }
        Write-Output "W18 instalação concluída offline: $candidateId"
    }
    catch {
        if (-not $committed) {
            try { Invoke-RollbackTransaction $paths $journal }
            catch { throw "falha W18 e rollback incompleto: $($_.Exception.Message)" }
        }
        throw
    }
}

function Invoke-Uninstall {
    $paths = Get-InstallPaths
    $priorInstallRootPresent = [bool](Test-Path -LiteralPath $paths.InstallRoot)
    if (-not (Test-Path -LiteralPath $paths.InstallRoot -PathType Container)) {
        throw "instalação W18 não encontrada; nenhum dado foi removido"
    }
    Ensure-InstallLayout $paths
    Assert-BinOwnership $paths
    $receipt = Get-OptionalReceipt $paths
    if ($null -eq $receipt) { throw "receipt W18 ausente; nenhuma remoção ampla será autorizada" }
    # Acquire every candidate lease before changing PATH, the launcher, the
    # receipt, or the versions tree.  The handles are held until the complete
    # uninstall mutation has succeeded or rolled back.
    $candidateLeases = Enter-W18UninstallCandidateLeases $paths
    try {
    $userPath = Read-RegistryPathSnapshot
    $priorLauncherPresent = Test-Path -LiteralPath $paths.StableLauncher -PathType Leaf
    $priorLauncherSha = if ($priorLauncherPresent) { Get-FileSha256 $paths.StableLauncher } else { "" }
    if ($priorLauncherPresent -and $priorLauncherSha -cne [string]$receipt.stable_launcher.sha256) {
        throw "launcher estável divergiu do receipt; uninstall fail-closed"
    }
    $transactionId = "w18-uninstall-$([DateTime]::UtcNow.ToString('yyyyMMddHHmmssfff'))-$([Guid]::NewGuid().ToString('N').Substring(0, 12))"
    $transactionRoot = Join-Path $paths.TransactionsRoot $transactionId
    $backupRoot = Join-Path $transactionRoot "uninstall-backup"
    Assert-DirectorySafe $transactionRoot
    $priorLauncherBackup = if ($priorLauncherPresent) { Join-Path $transactionRoot "prior-launcher.cmd" } else { "" }
    $journal = New-JournalDocument "uninstall" $paths $priorInstallRootPresent $transactionId $transactionRoot ([string]$receipt.candidate_id) (Join-Path $paths.VersionsRoot ([string]$receipt.candidate_id)) $transactionRoot $backupRoot ([string]$receipt.payload.sha256) ([string]$receipt.payload.inventory_sha256) $userPath $priorLauncherPresent $priorLauncherSha $priorLauncherBackup $true (Join-Path $transactionRoot "prior-receipt.json") ([string]$receipt.candidate_id) ([string]$receipt.previous_candidate_id)
    Assert-Journal $journal $paths
    Write-Journal $paths $journal
    $script:CurrentJournal = $journal
    $committed = $false
    try {
        Assert-DirectorySafe $backupRoot
        if ($priorLauncherPresent) { Copy-FileBackup $paths.StableLauncher $journal.prior_launcher_backup }
        Copy-FileBackup $paths.ReceiptPath $journal.prior_receipt_backup

        $pathMutation = Remove-OwnedPathSegment $userPath $paths.OwnedPath
        if ($pathMutation.changed) {
            # Journal intent before the registry write; recovery restores the
            # exact prior raw value even across a hard stop in Set-UserPathValue.
            $journal.path_mutated = $true
            Write-Journal $paths $journal
            Set-UserPathValue $userPath ([string]$pathMutation.value)
            Write-Journal $paths $journal
            $afterPath = Read-RegistryPathSnapshot
            if ($userPath.present) {
                $expected = [pscustomobject]@{ key_present = $true; present = $true; value = [string]$pathMutation.value; kind = [string]$userPath.kind }
            }
            else {
                $expected = [pscustomobject]@{ key_present = $true; present = $true; value = [string]$pathMutation.value; kind = "ExpandString" }
            }
            if (-not (Test-PathSnapshotEqual $afterPath $expected)) { throw "read-back do User PATH divergiu durante uninstall" }
        }
        Set-JournalState $paths $journal "PATH_MUTATED"
        if ($priorLauncherPresent) {
            # Mark the launcher mutation before deleting the live file.  The
            # backup is already durable, so rollback can restore it on any
            # interruption or access-denied result.
            $journal.launcher_promoted = $true
            Write-Journal $paths $journal
            Remove-Item -LiteralPath $paths.StableLauncher -Force
            Write-Journal $paths $journal
            Set-JournalState $paths $journal "LAUNCHER_PROMOTED"
        }
        if (Test-Path -LiteralPath $paths.VersionsRoot) {
            Assert-ContainedPath $paths.InstallRoot $paths.VersionsRoot
            Move-Item -LiteralPath $paths.VersionsRoot -Destination (Join-Path $backupRoot "versions")
        }
        if (Test-Path -LiteralPath $paths.BinRoot) {
            Assert-ContainedPath $paths.InstallRoot $paths.BinRoot
            Move-Item -LiteralPath $paths.BinRoot -Destination (Join-Path $backupRoot "bin")
        }
        Set-JournalState $paths $journal "STAGED"
        Remove-Item -LiteralPath $paths.ReceiptPath -Force
        $afterPersistent = Get-PersistentPath (Read-RegistryPathSnapshot)
        Test-FreshShell $paths $afterPersistent -ExpectAbsent | Out-Null
        Set-JournalState $paths $journal "FRESH_SHELL_VERIFIED"
        Set-JournalState $paths $journal "COMMITTED"
        $committed = $true
        try { Remove-JournalAndTransaction $paths $journal } catch { Write-Warning "backup de uninstall será removido na próxima recuperação" }
        $script:CurrentJournal = $null
        try { Remove-InstallRootAfterUninstall $paths } catch { Write-Warning "cleanup do install root falhou: $($_.Exception.Message)" }
        Write-Output "W18 uninstall concluído; config/data/state/cache/logs preservados"
    }
    catch {
        if (-not $committed) {
            try { Invoke-RollbackTransaction $paths $journal }
            catch { throw "falha W18 uninstall e rollback incompleto: $($_.Exception.Message)" }
        }
        throw
    }
    }
    finally {
        foreach ($candidateLease in @($candidateLeases)) {
            Exit-W18CandidateLease $candidateLease
        }
    }
}

function Get-W18CandidateDirectories {
    param([object]$Paths)

    if (-not (Test-Path -LiteralPath $Paths.VersionsRoot -PathType Container)) { return @() }
    $result = New-Object System.Collections.Generic.List[string]
    foreach ($item in @(Get-ChildItem -LiteralPath $Paths.VersionsRoot -Force)) {
        if (-not $item.PSIsContainer -or (Test-ReparsePoint $item.FullName)) {
            throw "versions W18 contém item não pertencente a candidato seguro: $($item.Name)"
        }
        $candidateId = [string]$item.Name
        if ($candidateId -notmatch "^w18-[0-9a-f]{32}$") {
            throw "diretório de candidato W18 inválido: $candidateId"
        }
        $canonical = Join-Path $Paths.VersionsRoot $candidateId
        if ([IO.Path]::GetFullPath($item.FullName) -cne [IO.Path]::GetFullPath($canonical)) {
            throw "diretório de candidato W18 não canônico: $candidateId"
        }
        [void]$result.Add($candidateId)
    }
    return @($result.ToArray())
}

function Get-W18CurrentUserSid {
    $sid = [Security.Principal.WindowsIdentity]::GetCurrent().User.Value
    if ([string]::IsNullOrEmpty($sid)) { throw "SID do usuário Windows ausente" }
    return $sid
}

function New-W18CandidateObjectNames {
    param([string]$CandidateId)

    if ([string]::IsNullOrEmpty($CandidateId) -or $CandidateId -cnotmatch "^w18-[0-9a-f]{32}$") {
        throw "candidate_id inválido para mutex W18"
    }
    $suffix = "$(Get-W18CurrentUserSid)-$CandidateId"
    return [pscustomobject]@{
        gate = "Global\W18-candidate-gate-$suffix"
        active = "Global\W18-candidate-active-$suffix"
    }
}

function Enter-W18CandidateLease {
    param([string]$CandidateId)

    $names = New-W18CandidateObjectNames $CandidateId
    $gate = $null
    try {
        $created = $false
        try {
            $gate = [Threading.Mutex]::new($false, $names.gate, [ref]$created)
        }
        catch [UnauthorizedAccessException] {
            return [pscustomobject]@{ status = "ambiguous"; gate_name = $names.gate; active_name = $names.active; gate = $null }
        }
        try {
            $acquired = $gate.WaitOne()
        }
        catch [Threading.AbandonedMutexException] {
            $acquired = $true
        }
        catch [SystemException] {
            $gate.Dispose()
            return [pscustomobject]@{ status = "ambiguous"; gate_name = $names.gate; active_name = $names.active; gate = $null }
        }
        if (-not $acquired) {
            $gate.Dispose()
            return [pscustomobject]@{ status = "ambiguous"; gate_name = $names.gate; active_name = $names.active; gate = $null }
        }
        try {
            $activeProbe = [Threading.Mutex]::OpenExisting([string]$names.active)
            $activeProbe.Dispose()
            $gate.ReleaseMutex()
            $gate.Dispose()
            return [pscustomobject]@{ status = "active"; gate_name = $names.gate; active_name = $names.active; gate = $null }
        }
        catch [Threading.WaitHandleCannotBeOpenedException] {
            return [pscustomobject]@{ status = "acquired"; gate_name = $names.gate; active_name = $names.active; gate = $gate }
        }
        catch {
            try { $gate.ReleaseMutex() } catch { }
            try { $gate.Dispose() } catch { }
            return [pscustomobject]@{ status = "ambiguous"; gate_name = $names.gate; active_name = $names.active; gate = $null }
        }
    }
    catch {
        if ($null -ne $gate) { try { $gate.Dispose() } catch { } }
        return [pscustomobject]@{ status = "ambiguous"; gate_name = $names.gate; active_name = $names.active; gate = $null }
    }
}

function Exit-W18CandidateLease {
    param([object]$Lease)

    if ($null -eq $Lease -or [string]$Lease.status -ne "acquired" -or $null -eq $Lease.gate) { return }
    try { $Lease.gate.ReleaseMutex() } catch { }
    try { $Lease.gate.Dispose() } catch { }
}

function Enter-W18UninstallCandidateLeases {
    param([object]$Paths)

    $leases = New-Object System.Collections.Generic.List[object]
    try {
        foreach ($candidateId in @(Get-W18CandidateDirectories $Paths | Sort-Object)) {
            $lease = Enter-W18CandidateLease $candidateId
            if ([string]$lease.status -eq "active") {
                throw "uninstall bloqueado: candidato ativo $candidateId ($($lease.active_name))"
            }
            if ([string]$lease.status -ne "acquired") {
                throw "uninstall bloqueado: não foi possível provar que o candidato $candidateId está inativo"
            }
            [void]$leases.Add($lease)
        }
        return @($leases.ToArray())
    }
    catch {
        foreach ($lease in @($leases.ToArray())) { Exit-W18CandidateLease $lease }
        throw
    }
}

function Enter-W18Mutex {
    param([int]$TimeoutSeconds)

    $sid = [Security.Principal.WindowsIdentity]::GetCurrent().User.Value
    $name = "Local\W18-local-llm-agent-$sid"
    $created = $false
    $mutex = [Threading.Mutex]::new($false, $name, [ref]$created)
    $abandoned = $false
    try {
        try {
            $acquired = $mutex.WaitOne([TimeSpan]::FromSeconds($TimeoutSeconds))
        }
        catch [Threading.AbandonedMutexException] {
            $acquired = $true
            $abandoned = $true
        }
        if (-not $acquired) {
            $mutex.Dispose()
            throw "timeout aguardando mutex W18; outra instalação pode estar em andamento"
        }
        return [pscustomobject]@{ mutex = $mutex; abandoned = $abandoned }
    }
    catch {
        if ($null -ne $mutex) { try { $mutex.Dispose() } catch { } }
        throw
    }
}

function Exit-W18Mutex {
    param([object]$Lease)

    if ($null -eq $Lease) { return }
    try { $Lease.mutex.ReleaseMutex() } catch { }
    $Lease.mutex.Dispose()
}

$lease = $null
try {
    $pathsForRecovery = Get-InstallPaths
    $lease = Enter-W18Mutex $MutexTimeoutSeconds
    Recover-IncompleteJournal $pathsForRecovery
    if ($Operation -eq "Uninstall") {
        Invoke-Uninstall
    }
    else {
        Invoke-Install $BundleRoot
    }
    exit 0
}
catch {
    Write-Error ("W18 $Operation falhou: " + $_.Exception.Message)
    exit 1
}
finally {
    Exit-W18Mutex $lease
}
