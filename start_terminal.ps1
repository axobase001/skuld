param(
    [string]$Workspace = (Get-Location).Path,
    [switch]$E2E,
    [switch]$ImportHistory,
    [switch]$VerifyHistory,
    [switch]$SeedWorld,
    [switch]$E2EAccounts,
    [switch]$SetProfileKey,
    [switch]$ListProfiles,
    [string]$ExportPath,
    [string]$Profile = 'default',
    [int]$MaxBatches = 0
)
$ErrorActionPreference = 'Stop'
Set-Location -LiteralPath $PSScriptRoot
if ($Profile -cnotmatch '^[a-z][a-z0-9_-]{0,31}$') {
    throw 'Profile must start with a lowercase letter and contain only lowercase letters, digits, _ or - (max 32).'
}
$accounts = Join-Path $PSScriptRoot '.terminal-accounts'
if ($ListProfiles) {
    'default'
    if (Test-Path -LiteralPath $accounts) {
        Get-ChildItem -LiteralPath $accounts -Directory | Select-Object -ExpandProperty Name
    }
    exit 0
}
if ($Profile -ne 'default' -and $PSBoundParameters.ContainsKey('Workspace')) {
    throw 'Named profiles use their private workspace; -Workspace applies only to default.'
}
if ($Profile -ne 'default' -and ($ImportHistory -or $VerifyHistory -or $SeedWorld -or $E2E)) {
    throw 'History import, digestion and the original E2E currently target only the default profile.'
}
$profileBase = if ($Profile -eq 'default') { $PSScriptRoot } else { Join-Path $accounts $Profile }
$secret = if ($Profile -eq 'default') {
    Join-Path $PSScriptRoot '.skuld-terminal-secrets\deepseek.dpapi'
} else { Join-Path $profileBase 'deepseek.dpapi' }
if ($SetProfileKey) {
    New-Item -ItemType Directory -Path (Split-Path -Parent $secret) -Force | Out-Null
    $newKey = Read-Host "DeepSeek key for local profile $Profile" -AsSecureString
    $newKey | ConvertFrom-SecureString | Set-Content -LiteralPath $secret -Encoding UTF8
    Write-Output "Credential stored for profile $Profile."
    exit 0
}
$inheritedKey = $env:DEEPSEEK_API_KEY
Remove-Item Env:DEEPSEEK_API_KEY -ErrorAction SilentlyContinue
if (Test-Path -LiteralPath $secret) {
    $secure = Get-Content -LiteralPath $secret -Raw | ConvertTo-SecureString
    $ptr = [Runtime.InteropServices.Marshal]::SecureStringToBSTR($secure)
    try {
        $env:DEEPSEEK_API_KEY = [Runtime.InteropServices.Marshal]::PtrToStringBSTR($ptr)
    } finally {
        [Runtime.InteropServices.Marshal]::ZeroFreeBSTR($ptr)
    }
} elseif ($Profile -eq 'default' -and $inheritedKey) {
    $env:DEEPSEEK_API_KEY = $inheritedKey
}
try {
    if ($E2EAccounts) {
        python -m terminal_app.e2e_accounts
    } elseif ($SeedWorld) {
        python -m terminal_app.seed_world_model
    } elseif ($VerifyHistory) {
        python -m terminal_app.verify_claude_history
    } elseif ($ImportHistory) {
        if (-not $ExportPath) { throw '-ImportHistory requires -ExportPath.' }
        python -m terminal_app.import_claude_history --workspace $Workspace --export $ExportPath --max-batches $MaxBatches
    } elseif ($E2E) {
        python -m terminal_app.e2e_v4
    } else {
        if ($Profile -eq 'default') {
            python -m terminal_app.main_v4 --workspace $Workspace --profile default --credential-from-env
        } else {
            python -m terminal_app.main_v4 --profile $Profile --credential-from-env
        }
    }
    exit $LASTEXITCODE
} finally {
    Remove-Item Env:DEEPSEEK_API_KEY -ErrorAction SilentlyContinue
}
