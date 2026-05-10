param(
    [string]$CodexHome = $env:CODEX_HOME,
    [string]$AgentsHome = $env:AGENTS_HOME
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

$removeTargets = @(
    (Join-Path $AgentsHome 'skills\orchestrator')
    (Join-Path $AgentsHome 'skills\orchestrator-observability')
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
)

$hooksPath = Join-Path $CodexHome 'hooks.json'
if (Test-Path $hooksPath) {
    Backup-Path -Path $hooksPath -Timestamp $timestamp
    Remove-OrchestratorHooks -HooksPath $hooksPath
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
