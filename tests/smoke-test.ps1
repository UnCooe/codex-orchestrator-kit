param(
    [Parameter(Mandatory=$false)] [string]$CodexHome = (Join-Path $env:TEMP ('codex-orchestrator-kit-smoke-codex-' + [Guid]::NewGuid().ToString('N'))),
    [Parameter(Mandatory=$false)] [string]$AgentsHome = (Join-Path $env:TEMP ('codex-orchestrator-kit-smoke-agents-' + [Guid]::NewGuid().ToString('N')))
)

Set-StrictMode -Version Latest
$ErrorActionPreference = 'Stop'

$root = Split-Path -Parent $MyInvocation.MyCommand.Path
$root = Split-Path -Parent $root

New-Item -ItemType Directory -Path $CodexHome -Force | Out-Null
New-Item -ItemType Directory -Path $AgentsHome -Force | Out-Null

function Invoke-PythonHook {
    param(
        [Parameter(Mandatory)][string]$Script,
        [Parameter(Mandatory)][string]$InputJson
    )

    if (Get-Command py -ErrorAction SilentlyContinue) {
        return ($InputJson | py -3 $Script)
    }
    if (Get-Command python -ErrorAction SilentlyContinue) {
        return ($InputJson | python $Script)
    }
    if (Get-Command python3 -ErrorAction SilentlyContinue) {
        return ($InputJson | python3 $Script)
    }
    throw 'No python executable found.'
}

function Get-PythonCommand {
    if (Get-Command py -ErrorAction SilentlyContinue) {
        return @('py', '-3')
    }
    if (Get-Command python -ErrorAction SilentlyContinue) {
        return @('python')
    }
    if (Get-Command python3 -ErrorAction SilentlyContinue) {
        return @('python3')
    }
    throw 'No python executable found.'
}

function Assert-JsonField {
    param(
        $JsonText,
        [Parameter(Mandatory)][scriptblock]$Predicate,
        [Parameter(Mandatory)][string]$Message
    )

    if ($null -eq $JsonText -or [string]::IsNullOrWhiteSpace([string]$JsonText)) {
        throw "$Message [hook output was empty]"
    }
    $parsed = $JsonText | ConvertFrom-Json
    if (-not (& $Predicate $parsed)) {
        throw $Message
    }
}

$origHookPath = Join-Path $CodexHome 'hooks.json'
$fixtureBefore = Join-Path $root 'tests\fixtures\hooks.before.json'
Copy-Item -Path $fixtureBefore -Destination $origHookPath -Force
New-Item -ItemType Directory -Path (Join-Path $CodexHome 'agents') -Force | Out-Null
'name = "codebase_research_explorer"' | Set-Content -Path (Join-Path $CodexHome 'agents\codebase-research-explorer.toml') -Encoding utf8
New-Item -ItemType Directory -Path (Join-Path $AgentsHome 'skills\orchestrator') -Force | Out-Null
$initialStaleSkillFile = Join-Path $AgentsHome 'skills\orchestrator\stale-managed-file.txt'
'stale' | Set-Content -Path $initialStaleSkillFile -Encoding utf8

$env:CODEX_HOME = $CodexHome
$env:AGENTS_HOME = $AgentsHome
$fixtureSinceDays = 36500
$managedSkills = @(
    'orchestrator',
    'orchestrator-observability',
    'feature-workflow',
    'review-workflow',
    'merge-workflow',
    'predeploy-workflow',
    'merge-safety'
)
$verifySkillInstall = Join-Path $root 'scripts\verify_skill_install.py'

function Invoke-PythonScript {
    param([Parameter(Mandatory)][string[]]$Arguments)

    $python = Get-PythonCommand
    $pythonArgs = @()
    if ($python.Length -gt 1) {
        $pythonArgs += $python[1..($python.Length - 1)]
    }
    & $python[0] @($pythonArgs + $Arguments)
    if ($LASTEXITCODE -ne 0) {
        throw "Python script failed: $($Arguments -join ' ')"
    }
}

function Assert-ManagedSkillsOnly {
    param([Parameter(Mandatory)][string]$SkillsRoot)

    foreach ($skill in $managedSkills) {
        $skillPath = Join-Path $SkillsRoot "$skill\SKILL.md"
        if (-not (Test-Path $skillPath)) {
            throw "Missing managed skill: $skillPath"
        }
    }
    $actual = @(Get-ChildItem -Path $SkillsRoot -Directory | ForEach-Object { $_.Name } | Sort-Object)
    $expected = @($managedSkills | Sort-Object)
    $diff = Compare-Object -ReferenceObject $expected -DifferenceObject $actual
    if ($diff) {
        throw "Managed skill catalog mismatch: $($actual -join ', ')"
    }
}

function Assert-SkillParity {
    param([Parameter(Mandatory)][string]$TargetRoot)

    Invoke-PythonScript -Arguments @($verifySkillInstall, '--source', (Join-Path $root 'skills'), '--target', $TargetRoot)
}

function Assert-RouterV3HookRuntime {
    param([Parameter(Mandatory)][string]$HooksRoot)

    $actual = @(Get-ChildItem -Path $HooksRoot -File | ForEach-Object { $_.Name } | Sort-Object)
    $expected = @('critical_action_gate.py')
    $diff = Compare-Object -ReferenceObject $expected -DifferenceObject $actual
    if ($diff) {
        throw "Router v3 hook runtime mismatch: $($actual -join ', ')"
    }
}

function Assert-RouterV3HooksJson {
    param([Parameter(Mandatory)][string]$Path)

    $obj = Get-Content -Raw -Encoding UTF8 $Path | ConvertFrom-Json
    $gateEntries = @()
    $commands = @()
    foreach ($event in $obj.hooks.PSObject.Properties) {
        foreach ($entry in @($event.Value)) {
            foreach ($hook in @($entry.hooks)) {
                $command = [string]$hook.command
                $commands += $command
                if ($command -match 'critical_action_gate\.py') {
                    $gateEntries += [pscustomobject]@{ Event = $event.Name; Matcher = [string]$entry.matcher }
                }
            }
        }
    }
    $commandText = $commands -join "`n"
    if ($gateEntries.Count -ne 1 -or $gateEntries[0].Event -ne 'PreToolUse' -or $gateEntries[0].Matcher -ne '^(Bash|exec_command)$') {
        throw "Expected one command PreTool critical gate; found $($gateEntries | ConvertTo-Json -Compress)"
    }
    if ($commandText -match 'pilot_receipt_gate\.py') {
        throw 'Retired pilot gate must not be registered.'
    }
    if (@($gateEntries | Where-Object { $_.Matcher -eq '*' }).Count -gt 0) {
        throw "Managed hooks must not use wildcard matcher; found $($gateEntries | ConvertTo-Json -Compress)"
    }
    if (@($gateEntries | Where-Object { $_.Matcher -eq '^apply_patch$' }).Count -gt 0) {
        throw "Critical gate must not be registered on apply_patch; found $($gateEntries | ConvertTo-Json -Compress)"
    }
    foreach ($legacy in @('orchestrator_user_prompt.py','orchestrator_pre_tool_use.py','orchestrator_post_tool_use.py','orchestrator_stop.py')) {
        if ($commandText -match [regex]::Escape($legacy)) {
            throw "Legacy managed hook still registered: $legacy"
        }
    }
    foreach ($projectHook in @('pre-existing-user-prompt.py','pre-existing-pre-tool.py')) {
        if ($commandText -notmatch [regex]::Escape($projectHook)) {
            throw "Project hook was not preserved: $projectHook"
        }
    }
}

$defaultDryCodexHome = Join-Path $env:TEMP ('codex-orchestrator-kit-default-dry-codex-' + [Guid]::NewGuid().ToString('N'))
$defaultDryAgentsHome = Join-Path $env:TEMP ('codex-orchestrator-kit-default-dry-agents-' + [Guid]::NewGuid().ToString('N'))
& (Join-Path $root 'install.ps1') -CodexHome $defaultDryCodexHome -AgentsHome $defaultDryAgentsHome | Out-Null
if ((Test-Path $defaultDryCodexHome) -or (Test-Path $defaultDryAgentsHome)) {
    throw 'Default install without -Apply created target directories.'
}

$applyWithoutModesFailed = $false
try {
    & (Join-Path $root 'install.ps1') -CodexHome $defaultDryCodexHome -AgentsHome $defaultDryAgentsHome -Apply | Out-Null
} catch {
    $applyWithoutModesFailed = $true
}
if (-not $applyWithoutModesFailed) {
    throw 'Apply mode without explicit HookMode/SkillsMode should fail.'
}

$applyDryRunFailed = $false
try {
    & (Join-Path $root 'install.ps1') -CodexHome $defaultDryCodexHome -AgentsHome $defaultDryAgentsHome -Apply -DryRun -HookMode auto -SkillsMode auto | Out-Null
} catch {
    $applyDryRunFailed = $_.Exception.Message -like '*Use either -Apply or -DryRun*'
}
if (-not $applyDryRunFailed) {
    throw 'Install should reject using -Apply and -DryRun together.'
}

& (Join-Path $root 'install.ps1') -CodexHome $CodexHome -AgentsHome $AgentsHome -Apply -HookMode auto -SkillsMode auto | Out-Null

$installedHookPath = Join-Path $CodexHome 'hooks.json'
$installedText = Get-Content -Raw $installedHookPath
$installed = $installedText | ConvertFrom-Json
$normalized = ($CodexHome -replace '\\', '/').TrimEnd('/')

if (-not $installed.hooks) {
    throw 'Hook install missing hooks object'
}
if (-not ($installedText -like "*${normalized}*critical_action_gate.py*")) {
    throw "Resolved hook for critical_action_gate.py not found for: $CodexHome"
}
if ($installedText -match 'C:/legacy/orchestrator_user_prompt\.py') {
    throw 'Install merge left legacy orchestrator_user_prompt.py command active.'
}
if ($installedText -match 'legacy.*orchestrator_pre_tool_use\.py') {
    throw 'Install merge left legacy orchestrator_pre_tool_use.py command active.'
}
if ($installedText -notmatch 'pre-existing-user-prompt\.py' -or $installedText -notmatch 'pre-existing-pre-tool\.py') {
    throw 'Install merge did not preserve pre-existing same-event hook entries.'
}
Assert-RouterV3HookRuntime -HooksRoot (Join-Path $CodexHome 'hooks')
Assert-RouterV3HooksJson -Path $installedHookPath
if (Test-Path (Join-Path $CodexHome 'agents\codebase-research-explorer.toml')) {
    throw 'Deprecated codebase-research-explorer agent should be removed during install.'
}
Assert-ManagedSkillsOnly -SkillsRoot (Join-Path $AgentsHome 'skills')
Assert-SkillParity -TargetRoot (Join-Path $AgentsHome 'skills')
if (Test-Path $initialStaleSkillFile) { throw 'Full install did not retire stale managed skill file.' }
if (-not (Get-ChildItem -Path "${initialStaleSkillFile}.bak.*" -ErrorAction SilentlyContinue)) {
    throw 'Full install did not back up stale managed skill file.'
}

$badSkills = Join-Path $env:TEMP ('codex-orchestrator-kit-bad-skills-' + [Guid]::NewGuid().ToString('N'))
Copy-Item -Path (Join-Path $AgentsHome 'skills') -Destination $badSkills -Recurse
New-Item -ItemType Directory -Path (Join-Path $badSkills 'orchestrator\__pycache__') -Force | Out-Null
New-Item -ItemType Directory -Path (Join-Path $badSkills 'orchestrator\.bak.20260722') -Force | Out-Null
'ignored' | Set-Content -Path (Join-Path $badSkills 'orchestrator\__pycache__\ignored.pyc') -Encoding utf8
'ignored' | Set-Content -Path (Join-Path $badSkills 'orchestrator\.bak.20260722\SKILL.md') -Encoding utf8
'ignored' | Set-Content -Path (Join-Path $badSkills 'orchestrator\SKILL.md.bak.20260722') -Encoding utf8
Assert-SkillParity -TargetRoot $badSkills
Add-Content -Path (Join-Path $badSkills 'orchestrator\SKILL.md') -Value '# tampered' -Encoding utf8
$skillParityMismatchFailed = $false
try {
    Assert-SkillParity -TargetRoot $badSkills
} catch {
    $skillParityMismatchFailed = $_.Exception.Message -like '*SHA-256*' -or $_.Exception.Message -like '*verify_skill_install*'
}
if (-not $skillParityMismatchFailed) {
    throw 'verify_skill_install.py should fail on SHA-256 mismatch.'
}

$skillsOnlyCodex = Join-Path $env:TEMP ('codex-orchestrator-kit-skills-only-codex-' + [Guid]::NewGuid().ToString('N'))
$skillsOnlyAgents = Join-Path $env:TEMP ('codex-orchestrator-kit-skills-only-agents-' + [Guid]::NewGuid().ToString('N'))
New-Item -ItemType Directory -Path (Join-Path $skillsOnlyCodex 'agents') -Force | Out-Null
$skillsOnlyHooks = Join-Path $skillsOnlyCodex 'hooks.json'
'{"hooks":{"UserPromptSubmit":[]}}' | Set-Content -Path $skillsOnlyHooks -Encoding utf8
'sentinel = true' | Set-Content -Path (Join-Path $skillsOnlyCodex 'config.toml') -Encoding utf8
'name = "keep_me"' | Set-Content -Path (Join-Path $skillsOnlyCodex 'agents\keep-me.toml') -Encoding utf8
New-Item -ItemType Directory -Path (Join-Path $skillsOnlyCodex 'hooks') -Force | Out-Null
'keep' | Set-Content -Path (Join-Path $skillsOnlyCodex 'hooks\keep-me.py') -Encoding utf8
$skillsOnlyHooksBefore = Get-Content -Raw $skillsOnlyHooks
$skillsOnlyConfigBefore = Get-Content -Raw (Join-Path $skillsOnlyCodex 'config.toml')
& (Join-Path $root 'install.ps1') -CodexHome $skillsOnlyCodex -AgentsHome $skillsOnlyAgents -Apply -SkillsOnly -SkillsMode agents | Out-Null
Assert-ManagedSkillsOnly -SkillsRoot (Join-Path $skillsOnlyAgents 'skills')
Assert-SkillParity -TargetRoot (Join-Path $skillsOnlyAgents 'skills')
if ((Get-Content -Raw $skillsOnlyHooks) -ne $skillsOnlyHooksBefore) {
    throw 'SkillsOnly install mutated hooks.json.'
}
if (-not (Test-Path (Join-Path $skillsOnlyCodex 'agents\keep-me.toml'))) {
    throw 'SkillsOnly install mutated agents directory.'
}
if (-not (Test-Path (Join-Path $skillsOnlyCodex 'hooks\keep-me.py'))) {
    throw 'SkillsOnly install mutated hooks directory.'
}
if ((Get-Content -Raw (Join-Path $skillsOnlyCodex 'config.toml')) -ne $skillsOnlyConfigBefore) {
    throw 'SkillsOnly install mutated config.toml.'
}
& (Join-Path $root 'uninstall.ps1') -CodexHome $skillsOnlyCodex -AgentsHome $skillsOnlyAgents -SkillsOnly -SkillsMode agents | Out-Null
if (Test-Path (Join-Path $skillsOnlyAgents 'skills\orchestrator\SKILL.md')) {
    throw 'SkillsOnly uninstall did not remove installed skills.'
}
if ((Get-Content -Raw $skillsOnlyHooks) -ne $skillsOnlyHooksBefore) {
    throw 'SkillsOnly uninstall mutated hooks.json.'
}
if (-not (Test-Path (Join-Path $skillsOnlyCodex 'agents\keep-me.toml'))) {
    throw 'SkillsOnly uninstall mutated agents directory.'
}
if (-not (Test-Path (Join-Path $skillsOnlyCodex 'hooks\keep-me.py'))) {
    throw 'SkillsOnly uninstall mutated hooks directory.'
}
if ((Get-Content -Raw (Join-Path $skillsOnlyCodex 'config.toml')) -ne $skillsOnlyConfigBefore) {
    throw 'SkillsOnly uninstall mutated config.toml.'
}

$skillsOnlyCodexMode = Join-Path $env:TEMP ('codex-orchestrator-kit-skills-only-codex-mode-' + [Guid]::NewGuid().ToString('N'))
$skillsOnlyCodexModeAgents = Join-Path $env:TEMP ('codex-orchestrator-kit-skills-only-codex-mode-agents-' + [Guid]::NewGuid().ToString('N'))
New-Item -ItemType Directory -Path (Join-Path $skillsOnlyCodexMode 'skills\orchestrator') -Force | Out-Null
New-Item -ItemType Directory -Path (Join-Path $skillsOnlyCodexMode 'agents') -Force | Out-Null
New-Item -ItemType Directory -Path (Join-Path $skillsOnlyCodexMode 'hooks') -Force | Out-Null
$codexModeStale = Join-Path $skillsOnlyCodexMode 'skills\orchestrator\stale-managed-file.txt'
'stale' | Set-Content -Path $codexModeStale -Encoding utf8
'{"hooks":{"UserPromptSubmit":[]}}' | Set-Content -Path (Join-Path $skillsOnlyCodexMode 'hooks.json') -Encoding utf8
'sentinel = true' | Set-Content -Path (Join-Path $skillsOnlyCodexMode 'config.toml') -Encoding utf8
'keep' | Set-Content -Path (Join-Path $skillsOnlyCodexMode 'agents\keep-me.toml') -Encoding utf8
'keep' | Set-Content -Path (Join-Path $skillsOnlyCodexMode 'hooks\keep-me.py') -Encoding utf8
& (Join-Path $root 'install.ps1') -CodexHome $skillsOnlyCodexMode -AgentsHome $skillsOnlyCodexModeAgents -Apply -SkillsOnly -SkillsMode codex | Out-Null
Assert-ManagedSkillsOnly -SkillsRoot (Join-Path $skillsOnlyCodexMode 'skills')
Assert-SkillParity -TargetRoot (Join-Path $skillsOnlyCodexMode 'skills')
if (Test-Path $codexModeStale) { throw 'Codex SkillsOnly install did not retire stale managed skill file.' }
if (-not (Get-ChildItem -Path "${codexModeStale}.bak.*" -ErrorAction SilentlyContinue)) {
    throw 'Codex SkillsOnly install did not back up stale managed skill file.'
}
if (-not (Test-Path (Join-Path $skillsOnlyCodexMode 'agents\keep-me.toml'))) { throw 'Codex SkillsOnly install mutated agents.' }
if (-not (Test-Path (Join-Path $skillsOnlyCodexMode 'hooks\keep-me.py'))) { throw 'Codex SkillsOnly install mutated hooks.' }
if ((Get-Content -Raw (Join-Path $skillsOnlyCodexMode 'config.toml')) -notmatch 'sentinel = true') { throw 'Codex SkillsOnly install mutated config.' }
& (Join-Path $root 'uninstall.ps1') -CodexHome $skillsOnlyCodexMode -AgentsHome $skillsOnlyCodexModeAgents -SkillsOnly -SkillsMode codex | Out-Null
if (Test-Path (Join-Path $skillsOnlyCodexMode 'skills\orchestrator\SKILL.md')) { throw 'Codex SkillsOnly uninstall did not remove skills.' }
if (-not (Test-Path (Join-Path $skillsOnlyCodexMode 'agents\keep-me.toml'))) { throw 'Codex SkillsOnly uninstall mutated agents.' }
if (-not (Test-Path (Join-Path $skillsOnlyCodexMode 'hooks\keep-me.py'))) { throw 'Codex SkillsOnly uninstall mutated hooks.' }

$dryCodexHome = Join-Path $env:TEMP ('codex-orchestrator-kit-dry-codex-' + [Guid]::NewGuid().ToString('N'))
$dryAgentsHome = Join-Path $env:TEMP ('codex-orchestrator-kit-dry-agents-' + [Guid]::NewGuid().ToString('N'))
& (Join-Path $root 'install.ps1') -CodexHome $dryCodexHome -AgentsHome $dryAgentsHome -DryRun | Out-Null
if ((Test-Path $dryCodexHome) -or (Test-Path $dryAgentsHome)) {
    throw 'DryRun created target directories.'
}

$oldPythonOverride = $env:CODEX_ORCHESTRATOR_PYTHON
try {
    $env:CODEX_ORCHESTRATOR_PYTHON = 'C:\Program Files\Python\python.exe'
    & (Join-Path $root 'install.ps1') -CodexHome $dryCodexHome -AgentsHome $dryAgentsHome -DryRun | Out-Null
} finally {
    if ($null -eq $oldPythonOverride) {
        Remove-Item Env:CODEX_ORCHESTRATOR_PYTHON -ErrorAction SilentlyContinue
    } else {
        $env:CODEX_ORCHESTRATOR_PYTHON = $oldPythonOverride
    }
}

$overrideCodexHome = Join-Path $env:TEMP ('codex-orchestrator-kit-python-override-codex-' + [Guid]::NewGuid().ToString('N'))
$overrideAgentsHome = Join-Path $env:TEMP ('codex-orchestrator-kit-python-override-agents-' + [Guid]::NewGuid().ToString('N'))
$oldPythonOverride = $env:CODEX_ORCHESTRATOR_PYTHON
try {
    $env:CODEX_ORCHESTRATOR_PYTHON = 'C:\Program Files\Python\python.exe'
    & (Join-Path $root 'install.ps1') -CodexHome $overrideCodexHome -AgentsHome $overrideAgentsHome -Apply -HookMode auto -SkillsMode auto | Out-Null
    $overrideHooks = Get-Content -Raw (Join-Path $overrideCodexHome 'hooks.json') | ConvertFrom-Json
    $overrideCommands = @(
        $overrideHooks.hooks.PreToolUse |
            ForEach-Object { $_.hooks } |
            Where-Object { $_.command -match 'critical_action_gate\.py' } |
            ForEach-Object { $_.command }
    )
    if ($overrideCommands.Count -ne 1) {
        throw "Python override install missing single critical gate: $($overrideCommands -join ', ')"
    }
    $overrideCommand = $overrideCommands[0]
    if (-not $overrideCommand.StartsWith('"C:\Program Files\Python\python.exe" ')) {
        throw "Python override with spaces was not quoted in hook command: $overrideCommand"
    }
} finally {
    if ($null -eq $oldPythonOverride) {
        Remove-Item Env:CODEX_ORCHESTRATOR_PYTHON -ErrorAction SilentlyContinue
    } else {
        $env:CODEX_ORCHESTRATOR_PYTHON = $oldPythonOverride
    }
}

& (Join-Path $root 'install.ps1') -CodexHome $CodexHome -AgentsHome $AgentsHome -Apply -HookMode auto -SkillsMode auto | Out-Null
$installedText = Get-Content -Raw $installedHookPath
if ([regex]::Matches($installedText, 'critical_action_gate\.py').Count -ne 1) {
    throw 'Install is not idempotent: duplicate critical_action_gate.py hook entry found.'
}
if ([regex]::Matches($installedText, 'pilot_receipt_gate\.py').Count -ne 0) {
    throw 'Retired pilot_receipt_gate.py should not be registered.'
}
Assert-RouterV3HooksJson -Path $installedHookPath

$requiredFiles = @(
    (Join-Path $CodexHome 'hooks\critical_action_gate.py'),
    (Join-Path $CodexHome 'agents\critic.toml')
)

foreach ($file in $requiredFiles) {
    if (-not (Test-Path $file)) {
        throw "Missing installed file: $file"
    }
}

$projectSpecificSkillPath = Join-Path $AgentsHome 'skills\project-specific-observability\SKILL.md'
if (Test-Path $projectSpecificSkillPath) {
    throw 'Default install should not install project-specific reviewer packs.'
}

$criticalGate = Join-Path $CodexHome 'hooks\critical_action_gate.py'
$ordinaryGateOutput = Invoke-PythonHook -Script $criticalGate -InputJson '{"tool_name":"Bash","tool_input":{"cmd":"python -V"}}'
if (-not [string]::IsNullOrWhiteSpace(($ordinaryGateOutput -join ''))) {
    throw 'Ordinary Bash fast path should stay silent.'
}

$dangerousGateOutput = Invoke-PythonHook -Script $criticalGate -InputJson '{"tool_name":"Bash","tool_input":{"cmd":"git push origin feature/demo"}}'
Assert-JsonField -JsonText $dangerousGateOutput -Predicate {
    param($value)
    $value.hookSpecificOutput.permissionDecision -eq 'deny' -and
        $value.hookSpecificOutput.permissionDecisionReason -like '*current-turn*'
} -Message 'Unauthorized push candidate was not blocked by critical action gate.'

$hooksJsonBytes = [System.IO.File]::ReadAllBytes((Join-Path $CodexHome 'hooks.json'))
if ($hooksJsonBytes.Length -ge 3 -and $hooksJsonBytes[0] -eq 0xEF -and $hooksJsonBytes[1] -eq 0xBB -and $hooksJsonBytes[2] -eq 0xBF) {
    throw 'Installed hooks.json must be UTF-8 without BOM.'
}

$sessionsRoot = Join-Path $CodexHome 'sessions'
New-Item -ItemType Directory -Path $sessionsRoot -Force | Out-Null

$analyzerFixture = Join-Path $root 'tests\fixtures\session-observability.jsonl'
$fixtureSession = Join-Path $sessionsRoot 'session-observability.jsonl'
Copy-Item -Path $analyzerFixture -Destination $fixtureSession -Force
$fixtureTimestamp = [DateTime]::UtcNow.ToString('yyyy-MM-ddTHH:mm:ssZ')
$preToolFixture = @{
    event = 'orchestrator_observability'
    event_name = 'pre_tool_use'
    status = 'blocked'
    created_at = $fixtureTimestamp
    reason = 'broad_scan_without_research_capability'
    session_id = 'smoke-router-v3'
    cwd = 'C:\repo\acme-tooling'
    tool_name = 'shell_command'
    normalized_tool_name = 'shell_command'
    tool_family = 'shell_like'
    input_length = 8
} | ConvertTo-Json -Compress -Depth 10
$structuredStopFixture = @{
    event = 'orchestrator_observability'
    event_name = 'stop'
    status = 'allow'
    created_at = $fixtureTimestamp
    reason = 'fixture structured acceptance'
    session_id = 'smoke-router-v3'
    cwd = 'C:\repo\acme-tooling'
    acceptance_source = 'structured'
    acceptance = @{
        source = 'structured'
        verification_status = 'pass'
        review_status = 'not_required'
        subagent_status = 'closed'
        risk_status = 'low'
        has_work_done_signal = $true
        has_verification_signal = $true
        has_subagent_signal = $false
        footer_field_count = 4
        footer_complete = $true
        footer_missing_fields = @()
    }
} | ConvertTo-Json -Compress -Depth 10
$fixtureNewLine = [Environment]::NewLine
[System.IO.File]::AppendAllText(
    $fixtureSession,
    "$fixtureNewLine$preToolFixture$fixtureNewLine$structuredStopFixture",
    [System.Text.UTF8Encoding]::new($false)
)
$largeCustomOutput = 'y' * 70000
$customToolCall = @{ payload = @{ type = 'custom_tool_call'; name = 'apply_patch' }; timestamp = '2026-05-02T00:00:09Z' } | ConvertTo-Json -Compress -Depth 10
$customToolOutput = @{ payload = @{ type = 'custom_tool_call_output'; output = $largeCustomOutput }; timestamp = '2026-05-02T00:00:10Z' } | ConvertTo-Json -Compress -Depth 10
[System.IO.File]::AppendAllText($fixtureSession, "`n$customToolCall`n$customToolOutput", [System.Text.UTF8Encoding]::new($false))

$nestedSessionDir = Join-Path $sessionsRoot '2026\05\03'
New-Item -ItemType Directory -Path $nestedSessionDir -Force | Out-Null
$nestedSession = Join-Path $nestedSessionDir 'rollout-nested-meta-session.jsonl'
$nestedSessionContent = @(
    (@{ type = 'session_meta'; payload = @{ id = 'nested-meta-session'; cwd = 'E:\repo\meta-project' }; timestamp = '2026-05-02T00:00:11Z' } | ConvertTo-Json -Compress -Depth 10),
    (@{ payload = @{ type = 'message'; role = 'assistant'; content = 'implemented nested metadata change. Done.' }; timestamp = '2026-05-02T00:00:12Z' } | ConvertTo-Json -Compress -Depth 10)
) -join "`n"
[System.IO.File]::WriteAllText($nestedSession, $nestedSessionContent, [System.Text.UTF8Encoding]::new($false))

$contextProject = Join-Path $env:TEMP ('project-context-smoke-' + [Guid]::NewGuid().ToString('N'))
$contextProjectName = Split-Path -Leaf $contextProject
New-Item -ItemType Directory -Path (Join-Path $contextProject '.codex') -Force | Out-Null
@'
项目目标: Smoke validation project
当前阶段: test fixture
核心模块: observability
常用命令: smoke-test
验收标准: reports include redacted context
已知风险: api_key=supersecret
裸 token: sk-1234567890abcdefghijklmnopqrstuvwxyz
'@ | Set-Content -Path (Join-Path $contextProject '.codex\project-context.md') -Encoding utf8
$contextSession = Join-Path $nestedSessionDir 'rollout-project-context-session.jsonl'
$contextSessionContent = @(
    (@{ type = 'session_meta'; payload = @{ id = 'project-context-session'; cwd = $contextProject }; timestamp = '2026-05-02T00:00:13Z' } | ConvertTo-Json -Compress -Depth 10),
    (@{ payload = @{ type = 'message'; role = 'assistant'; content = 'implemented context fixture. Verification: smoke test.' }; timestamp = '2026-05-02T00:00:14Z' } | ConvertTo-Json -Compress -Depth 10)
) -join "`n"
[System.IO.File]::WriteAllText($contextSession, $contextSessionContent, [System.Text.UTF8Encoding]::new($false))

$analyzer = Join-Path $AgentsHome 'skills\orchestrator-observability\scripts\analyze.py'
$triage = Join-Path $AgentsHome 'skills\orchestrator-observability\scripts\triage.py'
$subagentWorkflow = Join-Path $AgentsHome 'skills\orchestrator-observability\scripts\subagent_workflow.py'
$reportId = ('smoke-' + [Guid]::NewGuid().ToString('N'))
$python = Get-PythonCommand
$unitArgs = @()
if ($python.Length -gt 1) {
    $unitArgs += $python[1..($python.Length - 1)]
}
& $python[0] @($unitArgs + (Join-Path $root 'tests\test_session_facts_since_days.py'))
$analyzerOutput = & $python[0] @($python[1..($python.Length - 1)] + $analyzer + @('--codex-home', $CodexHome, '--since-days', $fixtureSinceDays, '--report-id', $reportId))
$analyzerOutputObj = $analyzerOutput | ConvertFrom-Json
if ($analyzerOutputObj.totals.orchestrator_events -lt 3) {
    throw 'Analyzer did not classify fixture orchestrator events.'
}

$jsonReport = Join-Path (Join-Path $CodexHome 'orchestrator-observability\reports') "orchestrator-observability-$reportId.json"
$mdReport = Join-Path (Join-Path $CodexHome 'orchestrator-observability\reports') "orchestrator-observability-$reportId.md"
if (-not (Test-Path $jsonReport)) {
    throw "Missing JSON report: $jsonReport"
}
if (-not (Test-Path $mdReport)) {
    throw "Missing markdown report: $mdReport"
}

$reportObj = Get-Content -Raw $jsonReport | ConvertFrom-Json
if ($reportObj.orchestrator_event_breakdown.'orchestrator_pre_tool_use'.total -lt 1) {
    throw 'Analyzer breakdown did not count pre_tool_use events.'
}
if ($reportObj.subagents.spawn_agent -lt 1 -or $reportObj.subagents.wait_agent -lt 1 -or $reportObj.subagents.close_agent -lt 1) {
    throw 'Analyzer did not classify subagent spawn/wait/close events.'
}
if ($reportObj.totals.token_count_events -lt 1 -or $reportObj.totals.reasoning_events -lt 1) {
    throw 'Analyzer did not classify token_count and reasoning events.'
}
if ($reportObj.totals.token_count_max -lt 1) {
    throw 'Analyzer did not extract token_count totals from current Codex token_count payload shape.'
}
if ($reportObj.acceptance.work_done_missing_verification -lt 1) {
    throw 'Analyzer did not classify missing verification acceptance signal.'
}
if ($reportObj.totals.invalid_lines -lt 1) {
    throw 'Analyzer did not count invalid JSONL fixture line.'
}
if ($reportObj.tool_calls.apply_patch -lt 1) {
    throw 'Analyzer did not count custom_tool_call as a tool call.'
}
if ($reportObj.noise.large_tool_outputs -lt 1 -or $reportObj.noise.large_custom_tool_outputs -lt 1) {
    throw 'Analyzer did not count large custom_tool_call_output noise.'
}
if ($reportObj.projects.'acme-tooling' -lt 1 -or $reportObj.projects.'acme-web' -lt 1) {
    throw 'Analyzer project aggregation missing expected CWD-derived project names.'
}
if ($reportObj.projects.'meta-project' -lt 1) {
    throw 'Analyzer did not inherit project CWD from nested session_meta.'
}
$contextProjectProp = $reportObj.projects.PSObject.Properties | Where-Object { $_.Name -eq $contextProjectName } | Select-Object -First 1
$contextProjectCount = if ($contextProjectProp) { $contextProjectProp.Value } else { 0 }
if (-not $contextProjectCount -or $contextProjectCount -lt 1) {
    throw 'Analyzer did not aggregate project context fixture project.'
}
if ($reportObj.sessions_seen -lt 3) {
    throw "Analyzer sessions_seen should count real session ids; got $($reportObj.sessions_seen)."
}
if (-not $reportObj.tool_surface.families.shell_like -or $reportObj.tool_surface.families.shell_like -lt 1) {
    throw 'Analyzer report missing tool_surface shell_like family summary.'
}
$contextKey = "project:$contextProjectName"
$contextEntryProp = $reportObj.project_context.PSObject.Properties | Where-Object { $_.Name -eq $contextKey } | Select-Object -First 1
$contextEntry = if ($contextEntryProp) { $contextEntryProp.Value } else { $null }
if (-not $contextEntry -or -not $contextEntry.available) {
    throw 'Analyzer report did not include available project context.'
}
if ($contextEntry.summary -match 'supersecret' -or $contextEntry.summary -match 'sk-1234567890abcdefghijklmnopqrstuvwxyz' -or $contextEntry.summary -notmatch '\[redacted\]') {
    throw 'Analyzer project context summary was not redacted.'
}
if (-not ($reportObj.facts | Where-Object { $_.session_id -eq 'nested-meta-session' -and $_.cwd -match 'meta-project' })) {
    throw 'Analyzer facts did not inherit session id and cwd from nested session_meta.'
}
if (-not $reportObj.suggestions -or $reportObj.suggestions.Count -lt 1) {
    throw 'Analyzer did not include suggestions.'
}
if (-not ($reportObj.suggestions -match 'verification')) {
    throw 'Analyzer suggestions did not include verification guidance.'
}
if ($reportObj.suggestions -match 'no sessions were detected') {
    throw 'Analyzer emitted false session-extraction suggestion while sessions were present.'
}
$rawJsonReportText = Get-Content -Raw $jsonReport
if ($rawJsonReportText -match 'please implement') {
    throw 'Analyzer report leaked assistant message content.'
}
if ($rawJsonReportText -match 'C:/repo' -or $rawJsonReportText -match 'D:/repo' -or $rawJsonReportText -match 'E:\\repo' -or $rawJsonReportText -match [regex]::Escape($CodexHome) -or $rawJsonReportText -match [regex]::Escape($contextProject)) {
    throw 'Analyzer report leaked full local paths while --include-paths was not set.'
}
if ($reportObj.facts.Count -lt 1) {
    throw 'Analyzer report did not include structured facts.'
}
$requiredFactFields = @('timestamp','session_id','cwd','event_type','role','reason','tool_name','normalized_tool_name','tool_family','status','input_length','output_length','token_count','source_file','source_line','acceptance','tool_surface')
foreach ($field in $requiredFactFields) {
    if (-not ($reportObj.facts[0].PSObject.Properties.Name -contains $field)) {
        throw "Missing required fact field: $field"
    }
}

$structuredStopFact = $reportObj.facts | Where-Object {
    $_.event_type -eq 'orchestrator_stop' -and
    $_.acceptance.source -eq 'structured' -and
    $_.acceptance.verification_status -eq 'pass'
} | Select-Object -First 1
if (-not $structuredStopFact) {
    throw 'Analyzer facts did not preserve structured acceptance fields.'
}
if (-not ($reportObj.facts | Where-Object { $_.event_type -eq 'orchestrator_pre_tool_use' -and $_.tool_name -eq 'shell_command' -and $_.normalized_tool_name -eq 'shell_command' })) {
    throw 'Analyzer facts did not preserve orchestrator event tool_name and normalized alias.'
}
$structuredVerifiedProp = $reportObj.structured_acceptance.PSObject.Properties | Where-Object { $_.Name -eq 'structured_acceptance_verified' } | Select-Object -First 1
$structuredVerified = if ($structuredVerifiedProp) { $structuredVerifiedProp.Value } else { 0 }
if (-not $structuredVerified -or $structuredVerified -lt 1) {
    throw 'Analyzer structured acceptance counters did not count verified structured footer.'
}
$footerMissingProp = $reportObj.acceptance.PSObject.Properties | Where-Object { $_.Name -eq 'work_done_missing_acceptance_footer' } | Select-Object -First 1
$footerMissing = if ($footerMissingProp) { $footerMissingProp.Value } else { 0 }
if (-not $footerMissing -or $footerMissing -lt 1) {
    throw 'Analyzer did not count missing acceptance footers on work-done replies.'
}
if (-not $reportObj.trend -or -not $reportObj.trend.overall) {
    throw 'Analyzer did not emit trend summary.'
}

$subagentWorkflowReportId = ('smoke-subagent-' + [Guid]::NewGuid().ToString('N'))
$subagentWorkflowOutput = & $python[0] @($python[1..($python.Length - 1)] + $subagentWorkflow + @('--codex-home', $CodexHome, '--since-days', $fixtureSinceDays, '--report-id', $subagentWorkflowReportId))
$subagentWorkflowOutputObj = $subagentWorkflowOutput | ConvertFrom-Json
if ($subagentWorkflowOutputObj.subagents.spawn -lt 1 -or $subagentWorkflowOutputObj.subagents.close -lt 1) {
    throw 'Subagent workflow report did not count spawn/close activity.'
}
$subagentWorkflowJsonReport = Join-Path (Join-Path $CodexHome 'orchestrator-observability\reports') "subagent-workflow-$subagentWorkflowReportId.json"
$subagentWorkflowMdReport = Join-Path (Join-Path $CodexHome 'orchestrator-observability\reports') "subagent-workflow-$subagentWorkflowReportId.md"
if (-not (Test-Path $subagentWorkflowJsonReport) -or -not (Test-Path $subagentWorkflowMdReport)) {
    throw 'Subagent workflow report files were not written.'
}
$subagentWorkflowObj = Get-Content -Raw -Encoding UTF8 $subagentWorkflowJsonReport | ConvertFrom-Json
if (
    $subagentWorkflowObj.report_type -ne 'subagent_workflow' -or
    (
        $subagentWorkflowObj.workflow_signals.broad_scan_blocks -lt 1 -and
        $subagentWorkflowObj.workflow_signals.broad_scan_bypasses -lt 1
    )
) {
    throw 'Subagent workflow report missing expected workflow signals.'
}
$rawSubagentWorkflowText = Get-Content -Raw -Encoding UTF8 $subagentWorkflowJsonReport
if ($rawSubagentWorkflowText -match [regex]::Escape($contextProject) -or $rawSubagentWorkflowText -match 'supersecret' -or $rawSubagentWorkflowText -match 'sk-1234567890abcdefghijklmnopqrstuvwxyz') {
    throw 'Subagent workflow report leaked project context path or secret.'
}

$triageReportDir = Join-Path $CodexHome 'orchestrator-observability\reports'

$highReportPath = Join-Path $triageReportDir 'orchestrator-observability-high-smoke.json'
$highReport = @{
    totals = @{
        orchestrator_events = 10
        blocked_orchestrator_events = 4
    }
    subagents = @{
        lifecycle_delta_spawn_minus_close = 0
    }
    acceptance = @{
        work_done_missing_verification = 0
        work_done_missing_subagent_status = 0
        work_done_missing_acceptance_footer = 2
        acceptance_footer_partial = 1
    }
    trend = @{
        overall = 'worsening'
        signals = @(
            @{
                metric = 'work_done_missing_acceptance_footer'
                status = 'worsened'
                current = 2
                previous = 0
                delta = 2
            }
        )
    }
    suggestions = @('High blocked hook ratio needs review.')
}
$highReport | ConvertTo-Json -Depth 10 | Set-Content -Path $highReportPath -Encoding utf8
$highTriageOutput = & $python[0] @($python[1..($python.Length - 1)] + $triage + @('--codex-home', $CodexHome, '--since-days', 3, '--report', $highReportPath))
$highTriage = $highTriageOutput | ConvertFrom-Json
if ($highTriage.severity -ne 'high') {
    throw "High-risk triage did not classify as high: $highTriageOutput"
}
$currentAlert = Join-Path $CodexHome 'orchestrator-observability\triage\alerts\high-risk-current.json'
if (-not (Test-Path $currentAlert)) {
    throw 'High-risk triage did not write high-risk-current alert.'
}

$lowReportPath = Join-Path $triageReportDir 'orchestrator-observability-low-smoke.json'
$lowReport = @{
    totals = @{
        orchestrator_events = 3
        blocked_orchestrator_events = 0
    }
    subagents = @{
        lifecycle_delta_spawn_minus_close = 0
    }
    acceptance = @{
        work_done_missing_verification = 0
        work_done_missing_subagent_status = 0
        work_done_missing_acceptance_footer = 0
        acceptance_footer_partial = 0
    }
    trend = @{
        overall = 'stable'
        signals = @()
    }
    project_context_available = $true
    suggestions = @('Update guidance wording.')
}
$lowReport | ConvertTo-Json -Depth 10 | Set-Content -Path $lowReportPath -Encoding utf8
$lowPatchPath = Join-Path $triageReportDir 'orchestrator-observability-low-smoke.patch.diff'
@'
diff --git a/docs/proposal-guidance.md b/docs/proposal-guidance.md
--- a/docs/proposal-guidance.md
+++ b/docs/proposal-guidance.md
@@ -1 +1,2 @@
 proposal guidance
+short confirmation card guidance
'@ | Set-Content -Path $lowPatchPath -Encoding utf8
$lowTriageOutput = & $python[0] @($python[1..($python.Length - 1)] + $triage + @('--codex-home', $CodexHome, '--since-days', 3, '--report', $lowReportPath))
$lowTriage = $lowTriageOutput | ConvertFrom-Json
if ($lowTriage.severity -ne 'low' -or -not $lowTriage.proposal_path) {
    throw "Low-risk triage did not create a proposal: $lowTriageOutput"
}
$proposalFiles = Get-ChildItem -Path $lowTriage.proposal_path -File | Select-Object -ExpandProperty Name
$expectedProposalFiles = @('manifest.json','patch.diff','proposal.md','rollback.md','validation.md')
foreach ($expected in $expectedProposalFiles) {
    if ($proposalFiles -notcontains $expected) {
        throw "Low-risk proposal missing file: $expected"
    }
}
if (($proposalFiles | Measure-Object).Count -ne $expectedProposalFiles.Count) {
    throw 'Low-risk proposal should contain only the confirmation card, manifest, patch, validation, and rollback files.'
}

$badReportPath = Join-Path $triageReportDir 'orchestrator-observability-badpatch-smoke.json'
$lowReport | ConvertTo-Json -Depth 10 | Set-Content -Path $badReportPath -Encoding utf8
$badPatchPath = Join-Path $triageReportDir 'orchestrator-observability-badpatch-smoke.patch.diff'
@'
diff --git a/hooks/orchestrator_user_prompt.py b/hooks/orchestrator_user_prompt.py
--- a/hooks/orchestrator_user_prompt.py
+++ b/hooks/orchestrator_user_prompt.py
@@ -1 +1,2 @@
 import json
+print("unsafe")
'@ | Set-Content -Path $badPatchPath -Encoding utf8
$badTriageOutput = & $python[0] @($python[1..($python.Length - 1)] + $triage + @('--codex-home', $CodexHome, '--since-days', 3, '--report', $badReportPath))
$badTriage = $badTriageOutput | ConvertFrom-Json
if ($badTriage.severity -ne 'none') {
    throw "Unsafe patch should not be triaged as low-risk: $badTriageOutput"
}

$privacyReportPath = Join-Path $triageReportDir 'orchestrator-observability-privacy-smoke.json'
$privacyReport = @{
    totals = @{
        orchestrator_events = 1
        blocked_orchestrator_events = 0
    }
    subagents = @{
        lifecycle_delta_spawn_minus_close = 0
    }
    acceptance = @{
        work_done_missing_verification = 0
        work_done_missing_subagent_status = 0
    }
    facts = @(@{ cwd = 'C:/repo/private-project' })
    suggestions = @('Update guidance wording.')
}
$privacyReport | ConvertTo-Json -Depth 10 | Set-Content -Path $privacyReportPath -Encoding utf8
$privacyTriageOutput = & $python[0] @($python[1..($python.Length - 1)] + $triage + @('--codex-home', $CodexHome, '--since-days', 3, '--report', $privacyReportPath))
$privacyTriage = $privacyTriageOutput | ConvertFrom-Json
if ($privacyTriage.severity -ne 'high' -or $privacyTriage.summary.flags.privacy_hit_count -lt 1) {
    throw "Privacy path leak should be high risk: $privacyTriageOutput"
}

$benignAutomationReportPath = Join-Path $triageReportDir 'orchestrator-observability-benign-automation-smoke.json'
$benignAutomationReport = @{
    totals = @{
        orchestrator_events = 1
        blocked_orchestrator_events = 0
    }
    subagents = @{
        lifecycle_delta_spawn_minus_close = 0
    }
    acceptance = @{
        work_done_missing_verification = 0
        work_done_missing_subagent_status = 0
        work_done_missing_acceptance_footer = 0
        acceptance_footer_partial = 0
    }
    trend = @{
        overall = 'stable'
        signals = @()
    }
    suggestions = @('Automation looks stable; reduce error message verbosity in summaries.')
}
$benignAutomationReport | ConvertTo-Json -Depth 10 | Set-Content -Path $benignAutomationReportPath -Encoding utf8
$benignAutomationTriageOutput = & $python[0] @($python[1..($python.Length - 1)] + $triage + @('--codex-home', $CodexHome, '--since-days', 3, '--report', $benignAutomationReportPath))
$benignAutomationTriage = $benignAutomationTriageOutput | ConvertFrom-Json
if ($benignAutomationTriage.severity -ne 'none') {
    throw "Benign automation wording should not trigger high-risk triage: $benignAutomationTriageOutput"
}

$recoveringReportPath = Join-Path $triageReportDir 'orchestrator-observability-recovering-smoke.json'
$recoveringReport = @{
    totals = @{
        orchestrator_events = 8
        blocked_orchestrator_events = 2
    }
    subagents = @{
        lifecycle_delta_spawn_minus_close = 0
    }
    acceptance = @{
        work_done_missing_verification = 0
        work_done_missing_subagent_status = 0
        work_done_missing_acceptance_footer = 1
        acceptance_footer_partial = 0
    }
    trend = @{
        overall = 'improving'
        signals = @(
            @{
                metric = 'work_done_missing_acceptance_footer'
                status = 'improved'
                current = 1
                previous = 3
                delta = -2
            }
        )
    }
    project_context_available = $true
    suggestions = @('Tighten footer reminders.')
}
$recoveringReport | ConvertTo-Json -Depth 10 | Set-Content -Path $recoveringReportPath -Encoding utf8
$recoveringPatchPath = Join-Path $triageReportDir 'orchestrator-observability-recovering-smoke.patch.diff'
@'
diff --git a/docs/proposal-guidance.md b/docs/proposal-guidance.md
--- a/docs/proposal-guidance.md
+++ b/docs/proposal-guidance.md
@@ -1 +1,2 @@
 proposal guidance
+footer reminder follow-up
'@ | Set-Content -Path $recoveringPatchPath -Encoding utf8
$recoveringTriageOutput = & $python[0] @($python[1..($python.Length - 1)] + $triage + @('--codex-home', $CodexHome, '--since-days', 3, '--report', $recoveringReportPath))
$recoveringTriage = $recoveringTriageOutput | ConvertFrom-Json
if ($recoveringTriage.severity -ne 'low' -or $recoveringTriage.summary.classification -ne 'improved') {
    throw "Recovering trend should downgrade to low-risk proposal: $recoveringTriageOutput"
}

$applyPatchReportPath = Join-Path $triageReportDir 'orchestrator-observability-applypatch-smoke.json'
$lowReport | ConvertTo-Json -Depth 10 | Set-Content -Path $applyPatchReportPath -Encoding utf8
$applyPatchPath = Join-Path $triageReportDir 'orchestrator-observability-applypatch-smoke.patch.diff'
@'
*** Begin Patch
*** Update File: docs/proposal-guidance.md
@@
-proposal guidance
+proposal guidance with confirmation card
*** End Patch
'@ | Set-Content -Path $applyPatchPath -Encoding utf8
$applyPatchTriageOutput = & $python[0] @($python[1..($python.Length - 1)] + $triage + @('--codex-home', $CodexHome, '--since-days', 3, '--report', $applyPatchReportPath))
$applyPatchTriage = $applyPatchTriageOutput | ConvertFrom-Json
if ($applyPatchTriage.severity -ne 'low') {
    throw "Safe apply_patch proposal should be accepted as low risk: $applyPatchTriageOutput"
}

$liveConfigReportPath = Join-Path $triageReportDir 'orchestrator-observability-liveconfig-smoke.json'
$lowReport | ConvertTo-Json -Depth 10 | Set-Content -Path $liveConfigReportPath -Encoding utf8
$liveConfigPatchPath = Join-Path $triageReportDir 'orchestrator-observability-liveconfig-smoke.patch.diff'
@'
diff --git a/.codex/prompts/automation.yaml b/.codex/prompts/automation.yaml
--- a/.codex/prompts/automation.yaml
+++ b/.codex/prompts/automation.yaml
@@ -1 +1,2 @@
 prompt: old
+prompt: new
'@ | Set-Content -Path $liveConfigPatchPath -Encoding utf8
$liveConfigTriageOutput = & $python[0] @($python[1..($python.Length - 1)] + $triage + @('--codex-home', $CodexHome, '--since-days', 3, '--report', $liveConfigReportPath))
$liveConfigTriage = $liveConfigTriageOutput | ConvertFrom-Json
if ($liveConfigTriage.severity -ne 'none') {
    throw "Live config patch should not be low-risk: $liveConfigTriageOutput"
}

& (Join-Path $root 'uninstall.ps1') -CodexHome $CodexHome -AgentsHome $AgentsHome | Out-Null
if (Test-Path (Join-Path $AgentsHome 'skills\orchestrator\SKILL.md')) {
    throw 'Uninstall did not remove orchestrator skill.'
}
if (Test-Path (Join-Path $AgentsHome 'skills\orchestrator-observability\SKILL.md')) {
    throw 'Uninstall did not remove orchestrator observability skill.'
}
if (-not (Test-Path (Join-Path $AgentsHome 'skills\demo-observability\SKILL.md'))) {
    throw 'Default uninstall should preserve external reviewer packs.'
}
if (Test-Path (Join-Path $CodexHome 'hooks\critical_action_gate.py')) {
    throw 'Uninstall did not remove critical action gate.'
}
if (Test-Path (Join-Path $CodexHome 'hooks\pilot_receipt_gate.py')) {
    throw 'Uninstall did not remove pilot receipt gate.'
}
$uninstalledHooks = Get-Content -Raw $installedHookPath
if ($uninstalledHooks -match 'critical_action_gate\.py|pilot_receipt_gate\.py') {
    throw 'Uninstall did not strip managed hook entries.'
}
if ($uninstalledHooks -notmatch 'pre-existing-user-prompt\.py' -or $uninstalledHooks -notmatch 'pre-existing-pre-tool\.py') {
    throw 'Uninstall removed a pre-existing project hook entry.'
}

Write-Host "smoke-test passed"

