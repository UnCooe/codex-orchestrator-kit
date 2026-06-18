param(
    [string]$CodexHome = $env:CODEX_HOME,
    [string]$AgentsHome = $env:AGENTS_HOME,
    [ValidateSet('auto', 'hooks_json', 'config_toml')] [string]$HookMode = 'auto',
    [ValidateSet('auto', 'agents', 'codex')] [string]$SkillsMode = 'auto'
)

Set-StrictMode -Version Latest
$ErrorActionPreference = 'Stop'

function Get-CodexHome {
    param([string]$Value)

    if (-not [string]::IsNullOrWhiteSpace($Value)) {
        return [System.IO.Path]::GetFullPath($Value)
    }
    if ($env:HOME) { return Join-Path $env:HOME '.codex' }
    if ($env:USERPROFILE) { return Join-Path $env:USERPROFILE '.codex' }
    throw 'Cannot determine Codex home. Set CODEX_HOME or HOME/USERPROFILE.'
}

function Get-AgentsHome {
    param([string]$Value)

    if (-not [string]::IsNullOrWhiteSpace($Value)) {
        return [System.IO.Path]::GetFullPath($Value)
    }
    if ($env:HOME) { return Join-Path $env:HOME '.agents' }
    if ($env:USERPROFILE) { return Join-Path $env:USERPROFILE '.agents' }
    throw 'Cannot determine agents home. Set AGENTS_HOME or HOME/USERPROFILE.'
}

function Remove-OrchestratorHooks {
    param([string]$HooksPath)

    if (-not (Test-Path $HooksPath)) { return }

    $obj = Get-Content -Path $HooksPath -Raw | ConvertFrom-Json
    if (-not $obj.hooks) { return }

    $pattern = 'orchestrator_(pre_tool_use|post_tool_use|stop|user_prompt)\.py'
    $updated = @{}

    foreach ($prop in $obj.hooks.PSObject.Properties) {
        $eventName = $prop.Name
        $entries = @($prop.Value)
        $filteredEntries = @()

        foreach ($entry in $entries) {
            $filteredHooks = @($entry.hooks | Where-Object { $_.command -notmatch $pattern })
            if ($filteredHooks.Count -gt 0) {
                $entry.hooks = $filteredHooks
                $filteredEntries += $entry
            }
        }

        if ($filteredEntries.Count -gt 0) {
            $updated[$eventName] = $filteredEntries
        }
    }

    if ($updated.Keys.Count -eq 0) {
        Remove-Item -Path $HooksPath -Force
        return
    }

    $obj.hooks = $updated
    Set-Content -Path $HooksPath -Value (ConvertTo-Json $obj -Depth 20) -Encoding utf8
}

function Remove-ConfigTomlManagedHooks {
    param([string]$ConfigPath)

    if (-not (Test-Path $ConfigPath)) { return }

    $text = Get-Content -Path $ConfigPath -Raw -Encoding utf8
    $beginMarker = '# BEGIN codex-orchestrator-kit managed hooks'
    $endMarker = '# END codex-orchestrator-kit managed hooks'
    $pattern = '(?ms)^[ \t]*' + [regex]::Escape($beginMarker) + "`r?`n.*?^[ \t]*" + [regex]::Escape($endMarker) + "`r?`n?"
    $updated = [regex]::Replace($text, $pattern, '').TrimEnd()
    Set-Content -Path $ConfigPath -Value $updated -Encoding utf8
}

function Backup-Path {
    param([Parameter(Mandatory)][string]$Path, [Parameter(Mandatory)][string]$Timestamp)

    if (-not (Test-Path $Path)) { return }
    $backup = "${Path}.bak.$Timestamp"
    Copy-Item -Path $Path -Destination $backup -Recurse -Force
    Write-Host "backed up -> $backup"
}

$CodexHome = Get-CodexHome $CodexHome
$AgentsHome = Get-AgentsHome $AgentsHome
$timestamp = Get-Date -Format 'yyyyMMdd_HHmmss_fff'
$ResolvedHookMode = if ($HookMode -ne 'auto') {
    $HookMode
} elseif (Test-Path (Join-Path $CodexHome 'hooks.json')) {
    'hooks_json'
} elseif (Test-Path (Join-Path $CodexHome 'config.toml')) {
    'config_toml'
} else {
    'hooks_json'
}
$ResolvedSkillsMode = if ($SkillsMode -ne 'auto') {
    $SkillsMode
} elseif (Test-Path (Join-Path $CodexHome 'skills')) {
    'codex'
} elseif (Test-Path (Join-Path $AgentsHome 'skills')) {
    'agents'
} else {
    'agents'
}
$SkillsHome = if ($ResolvedSkillsMode -eq 'codex') { $CodexHome } else { $AgentsHome }

$removeTargets = @(
    (Join-Path $SkillsHome 'skills\orchestrator')
    (Join-Path $SkillsHome 'skills\orchestrator-observability')
    (Join-Path $SkillsHome 'skills\feature-workflow')
    (Join-Path $SkillsHome 'skills\merge-workflow')
    (Join-Path $SkillsHome 'skills\review-workflow')
    (Join-Path $SkillsHome 'skills\predeploy-workflow')
    (Join-Path $CodexHome 'hooks\orchestrator_pre_tool_use.py')
    (Join-Path $CodexHome 'hooks\orchestrator_post_tool_use.py')
    (Join-Path $CodexHome 'hooks\orchestrator_stop.py')
    (Join-Path $CodexHome 'hooks\orchestrator_user_prompt.py')
    (Join-Path $CodexHome 'hooks\orchestrator_observability.py')
    (Join-Path $CodexHome 'agents\critic.toml')
    (Join-Path $CodexHome 'agents\implementer.toml')
    (Join-Path $CodexHome 'agents\researcher.toml')
    (Join-Path $CodexHome 'agents\reviewer.toml')
    (Join-Path $CodexHome 'agents\test-designer.toml')
    (Join-Path $CodexHome 'agents\requirements-interpreter.toml')
    (Join-Path $CodexHome 'agents\test-writer.toml')
    (Join-Path $CodexHome 'agents\test-runner.toml')
    (Join-Path $CodexHome 'agents\conflict-resolver.toml')
    (Join-Path $CodexHome 'agents\deployer.toml')
    (Join-Path $CodexHome 'agents\codebase-research-explorer.toml')
)

if ($ResolvedHookMode -eq 'hooks_json') {
    $hooksPath = Join-Path $CodexHome 'hooks.json'
    if (Test-Path $hooksPath) {
        Backup-Path -Path $hooksPath -Timestamp $timestamp
        Remove-OrchestratorHooks -HooksPath $hooksPath
    }
} else {
    $configPath = Join-Path $CodexHome 'config.toml'
    if (Test-Path $configPath) {
        Backup-Path -Path $configPath -Timestamp $timestamp
        Remove-ConfigTomlManagedHooks -ConfigPath $configPath
    }
}

foreach ($target in $removeTargets) {
    if (Test-Path $target) {
        Backup-Path -Path $target -Timestamp $timestamp
        if ((Get-Item $target).PSIsContainer) {
            Remove-Item -Path $target -Recurse -Force
        } else {
            Remove-Item -Path $target -Force
        }
        Write-Host "removed: $target"
    }
}

Write-Host 'done'
