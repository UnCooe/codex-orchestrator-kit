param(
    [string]$CodexHome = $env:CODEX_HOME,
    [string]$AgentsHome = $env:AGENTS_HOME,
    [ValidateSet('auto', 'hooks_json', 'config_toml')] [string]$HookMode = 'auto',
    [ValidateSet('auto', 'agents', 'codex')] [string]$SkillsMode = 'auto',
    [switch]$SkillsOnly
)

Set-StrictMode -Version Latest
$ErrorActionPreference = 'Stop'
$managedHookScripts = [System.Collections.Generic.HashSet[string]]::new([StringComparer]::OrdinalIgnoreCase)
foreach ($scriptName in @(
    'orchestrator_user_prompt.py'
    'orchestrator_pre_tool_use.py'
    'orchestrator_post_tool_use.py'
    'orchestrator_stop.py'
    'orchestrator_observability.py'
    'workflow_profile.py'
    'first_write_gate.py'
    'critical_action_gate.py'
    'pilot_receipt_gate.py'
)) {
    $null = $managedHookScripts.Add($scriptName)
}

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

function Test-ManagedHookCommand {
    param([string]$Command)

    if ([string]::IsNullOrWhiteSpace($Command)) { return $false }
    $tokenPattern = '(?:"([^"]+)"|''([^'']+)''|(\S+))'
    foreach ($match in [regex]::Matches($Command, $tokenPattern)) {
        $token = if ($match.Groups[1].Success) { $match.Groups[1].Value }
        elseif ($match.Groups[2].Success) { $match.Groups[2].Value }
        else { $match.Groups[3].Value }
        $scriptName = ($token -split '[\\/]')[-1]
        if ($managedHookScripts.Contains($scriptName)) { return $true }
    }
    return $false
}

function Test-ReparsePoint {
    param([Parameter(Mandatory)][string]$Path)

    if (-not (Test-Path -LiteralPath $Path)) { return $false }
    $item = Get-Item -LiteralPath $Path -Force
    return (($item.Attributes -band [System.IO.FileAttributes]::ReparsePoint) -ne 0)
}

function Assert-ExactPathNotReparse {
    param([Parameter(Mandatory)][string]$Path)

    if (-not (Test-Path -LiteralPath $Path)) { return }
    if (Test-ReparsePoint -Path $Path) {
        throw "Refusing to operate on reparse/symlink target: $Path"
    }
}

function Assert-NoReparsePoint {
    param([Parameter(Mandatory)][string]$Path)

    if (-not (Test-Path -LiteralPath $Path)) { return }
    Assert-ExactPathNotReparse -Path $Path
    foreach ($item in (Get-ChildItem -LiteralPath $Path -Recurse -Force)) {
        if (($item.Attributes -band [System.IO.FileAttributes]::ReparsePoint) -ne 0) {
            throw "Refusing to operate on reparse/symlink target: $($item.FullName)"
        }
    }
}

function Remove-OrchestratorHooks {
    param([string]$HooksPath)

    if (-not (Test-Path $HooksPath)) { return }

    $obj = Get-Content -Path $HooksPath -Raw | ConvertFrom-Json
    if (-not $obj.hooks) { return }

    $updated = @{}

    foreach ($prop in $obj.hooks.PSObject.Properties) {
        $eventName = $prop.Name
        $entries = @($prop.Value)
        $filteredEntries = @()

        foreach ($entry in $entries) {
            $filteredHooks = @($entry.hooks | Where-Object { -not (Test-ManagedHookCommand -Command $_.command) })
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
    $text = [regex]::Replace($text, $pattern, '')

    $managedScriptPattern = '(?i)(?<![A-Za-z0-9_.-])(?:orchestrator_user_prompt|orchestrator_pre_tool_use|' +
        'orchestrator_post_tool_use|orchestrator_stop|orchestrator_observability|workflow_profile|' +
        'first_write_gate|critical_action_gate|pilot_receipt_gate)\.py(?![A-Za-z0-9_.-])'
    $eventHeaderPattern = '^\[\[hooks\.([A-Za-z0-9_]+)\]\]\s*$'
    $subhookHeaderPattern = '^\[\[hooks\.([A-Za-z0-9_]+)\.hooks\]\]\s*$'

    $lines = [regex]::Split($text, "(`r?`n)")
    $logicalLines = [System.Collections.Generic.List[string]]::new()
    for ($i = 0; $i -lt $lines.Length; $i += 2) {
        $line = $lines[$i]
        $newline = if ($i + 1 -lt $lines.Length) { $lines[$i + 1] } else { '' }
        $logicalLines.Add($line + $newline)
    }

    $output = [System.Collections.Generic.List[string]]::new()
    $index = 0
    while ($index -lt $logicalLines.Count) {
        $line = $logicalLines[$index]
        $trimmed = $line.Trim()
        if ($trimmed -notmatch $eventHeaderPattern) {
            $output.Add($line)
            $index++
            continue
        }

        $eventLines = [System.Collections.Generic.List[string]]::new()
        $eventLines.Add($line)
        $index++
        while ($index -lt $logicalLines.Count) {
            $nextTrimmed = $logicalLines[$index].Trim()
            if (($nextTrimmed -match $subhookHeaderPattern) -or ($nextTrimmed.StartsWith('[') -and -not $nextTrimmed.StartsWith('[[hooks.')) -or ($nextTrimmed -match $eventHeaderPattern)) {
                break
            }
            $eventLines.Add($logicalLines[$index])
            $index++
        }

        $subblocks = [System.Collections.Generic.List[string]]::new()
        while ($index -lt $logicalLines.Count) {
            $nextTrimmed = $logicalLines[$index].Trim()
            if ($nextTrimmed -notmatch $subhookHeaderPattern) { break }

            $subblock = [System.Collections.Generic.List[string]]::new()
            $subblock.Add($logicalLines[$index])
            $index++
            while ($index -lt $logicalLines.Count) {
                $lookahead = $logicalLines[$index].Trim()
                if (($lookahead -match $eventHeaderPattern) -or ($lookahead -match $subhookHeaderPattern) -or ($lookahead.StartsWith('[') -and -not $lookahead.StartsWith('[[hooks.'))) {
                    break
                }
                $subblock.Add($logicalLines[$index])
                $index++
            }

            $subblockText = ($subblock -join '')
            $hasActiveManagedHook = $false
            foreach ($subblockLine in [regex]::Split($subblockText, "`r?`n")) {
                if ($subblockLine.TrimStart().StartsWith('#')) { continue }
                if ($subblockLine -match $managedScriptPattern) {
                    $hasActiveManagedHook = $true
                    break
                }
            }
            if (-not $hasActiveManagedHook) {
                $subblocks.Add($subblockText)
            }
        }

        if ($subblocks.Count -gt 0) {
            foreach ($eventLine in $eventLines) { $output.Add($eventLine) }
            foreach ($subblockText in $subblocks) { $output.Add($subblockText) }
        }
    }

    $updated = (($output -join '').TrimEnd())
    Set-Content -Path $ConfigPath -Value $updated -Encoding utf8
}

function Backup-Path {
    param([Parameter(Mandatory)][string]$Path, [Parameter(Mandatory)][string]$Timestamp)

    if (-not (Test-Path $Path)) { return }
    Assert-NoReparsePoint -Path $Path
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

$skillRemoveTargets = @(
    (Join-Path $SkillsHome 'skills\orchestrator')
    (Join-Path $SkillsHome 'skills\orchestrator-observability')
    (Join-Path $SkillsHome 'skills\feature-workflow')
    (Join-Path $SkillsHome 'skills\review-workflow')
    (Join-Path $SkillsHome 'skills\merge-workflow')
    (Join-Path $SkillsHome 'skills\predeploy-workflow')
    (Join-Path $SkillsHome 'skills\merge-safety')
)
$removeTargets = @($skillRemoveTargets)
if (-not $SkillsOnly.IsPresent) {
    $removeTargets += @(
    (Join-Path $CodexHome 'hooks\orchestrator_pre_tool_use.py')
    (Join-Path $CodexHome 'hooks\orchestrator_post_tool_use.py')
    (Join-Path $CodexHome 'hooks\orchestrator_stop.py')
    (Join-Path $CodexHome 'hooks\orchestrator_user_prompt.py')
    (Join-Path $CodexHome 'hooks\orchestrator_observability.py')
    (Join-Path $CodexHome 'hooks\workflow_profile.py')
    (Join-Path $CodexHome 'hooks\first_write_gate.py')
    (Join-Path $CodexHome 'hooks\critical_action_gate.py')
    (Join-Path $CodexHome 'hooks\pilot_receipt_gate.py')
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
}

if (-not $SkillsOnly.IsPresent) {
    Assert-ExactPathNotReparse -Path (Join-Path $CodexHome 'hooks')
    if ($ResolvedHookMode -eq 'hooks_json') {
        $hooksPath = Join-Path $CodexHome 'hooks.json'
        Assert-NoReparsePoint -Path $hooksPath
        if (Test-Path $hooksPath) {
            Backup-Path -Path $hooksPath -Timestamp $timestamp
            Remove-OrchestratorHooks -HooksPath $hooksPath
        }
    } else {
        $configPath = Join-Path $CodexHome 'config.toml'
        Assert-NoReparsePoint -Path $configPath
        if (Test-Path $configPath) {
            Backup-Path -Path $configPath -Timestamp $timestamp
            Remove-ConfigTomlManagedHooks -ConfigPath $configPath
        }
    }
}

foreach ($target in $removeTargets) {
    if (Test-Path $target) {
        Assert-NoReparsePoint -Path $target
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
