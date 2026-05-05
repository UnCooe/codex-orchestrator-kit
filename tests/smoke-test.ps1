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

function Read-JsonlLast {
    param([Parameter(Mandatory)][string]$Path)
    return (Get-Content -Path $Path -Tail 1 | ConvertFrom-Json)
}

function Assert-JsonField {
    param(
        [Parameter(Mandatory)]$JsonText,
        [Parameter(Mandatory)][scriptblock]$Predicate,
        [Parameter(Mandatory)][string]$Message
    )

    $parsed = $JsonText | ConvertFrom-Json
    if (-not (& $Predicate $parsed)) {
        throw $Message
    }
}

$origHookPath = Join-Path $CodexHome 'hooks.json'
$fixtureBefore = Join-Path $root 'tests\fixtures\hooks.before.json'
Copy-Item -Path $fixtureBefore -Destination $origHookPath -Force

$env:CODEX_HOME = $CodexHome
$env:AGENTS_HOME = $AgentsHome
& (Join-Path $root 'install.ps1') -CodexHome $CodexHome -AgentsHome $AgentsHome | Out-Null

$installedHookPath = Join-Path $CodexHome 'hooks.json'
$installedText = Get-Content -Raw $installedHookPath
$installed = $installedText | ConvertFrom-Json
$normalized = ($CodexHome -replace '\\', '/').TrimEnd('/')
$resolvedCommand = "$normalized/hooks/orchestrator_user_prompt.py"

if (-not $installed.hooks) {
    throw 'Hook install missing hooks object'
}
if (-not ($installedText -like "*${normalized}*orchestrator_user_prompt.py*")) {
    throw "Resolved hook for orchestrator_user_prompt.py not found for: $CodexHome"
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
    & (Join-Path $root 'install.ps1') -CodexHome $overrideCodexHome -AgentsHome $overrideAgentsHome | Out-Null
    $overrideHooks = Get-Content -Raw (Join-Path $overrideCodexHome 'hooks.json') | ConvertFrom-Json
    $overrideCommand = $overrideHooks.hooks.UserPromptSubmit[0].hooks[0].command
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

& (Join-Path $root 'install.ps1') -CodexHome $CodexHome -AgentsHome $AgentsHome | Out-Null
$installedText = Get-Content -Raw $installedHookPath
if ([regex]::Matches($installedText, 'orchestrator_user_prompt\.py').Count -ne 1) {
    throw 'Install is not idempotent: duplicate orchestrator_user_prompt.py hook entry found.'
}

$requiredFiles = @(
    (Join-Path $AgentsHome 'skills\orchestrator\SKILL.md'),
    (Join-Path $AgentsHome 'skills\orchestrator-observability\SKILL.md'),
    (Join-Path $AgentsHome 'skills\turnstate-observability\SKILL.md'),
    (Join-Path $CodexHome 'hooks\orchestrator_pre_tool_use.py'),
    (Join-Path $CodexHome 'hooks\orchestrator_observability.py'),
    (Join-Path $CodexHome 'agents\critic.toml')
)

foreach ($file in $requiredFiles) {
    if (-not (Test-Path $file)) {
        throw "Missing installed file: $file"
    }
}

$userPrompt = Join-Path $CodexHome 'hooks\orchestrator_user_prompt.py'
$preTool = Join-Path $CodexHome 'hooks\orchestrator_pre_tool_use.py'
$postTool = Join-Path $CodexHome 'hooks\orchestrator_post_tool_use.py'
$stopHook = Join-Path $CodexHome 'hooks\orchestrator_stop.py'

$heavyPromptOutput = Invoke-PythonHook -Script $userPrompt -InputJson '{"prompt":"please implement this feature and add tests"}'
Assert-JsonField -JsonText $heavyPromptOutput -Predicate {
    param($value)
    $value.hookSpecificOutput.hookEventName -eq 'UserPromptSubmit' -and
    $value.hookSpecificOutput.additionalContext -like '*ORCHESTRATOR CHECK*'
} -Message 'UserPromptSubmit heavy prompt did not inject orchestrator context.'

$benignPromptOutput = Invoke-PythonHook -Script $userPrompt -InputJson '{"prompt":"what is 2+2?"}'
if (-not [string]::IsNullOrWhiteSpace(($benignPromptOutput -join ''))) {
    throw 'UserPromptSubmit benign prompt should not emit context.'
}

$preBlockOutput = Invoke-PythonHook -Script $preTool -InputJson '{"tool_name":"bash","tool_input":{"command":"rg --files"}}'
Assert-JsonField -JsonText $preBlockOutput -Predicate {
    param($value)
    $value.decision -eq 'block'
} -Message 'PreToolUse broad scan did not block.'

$preShellCommandBlockOutput = Invoke-PythonHook -Script $preTool -InputJson '{"tool_name":"shell_command","tool_input":{"command":"Get-ChildItem -Recurse"}}'
Assert-JsonField -JsonText $preShellCommandBlockOutput -Predicate {
    param($value)
    $value.decision -eq 'block'
} -Message 'PreToolUse shell_command broad scan did not block.'

$prePowerShellBlockOutput = Invoke-PythonHook -Script $preTool -InputJson '{"tool_name":"powershell","tool_input":{"command":"Select-String -Path . -Recurse -Pattern TODO"}}'
Assert-JsonField -JsonText $prePowerShellBlockOutput -Predicate {
    param($value)
    $value.decision -eq 'block'
} -Message 'PreToolUse PowerShell broad scan did not block.'

$largeResponse = 'x' * 60001
$postBlockPayload = @{ tool_name = 'bash'; tool_response = $largeResponse } | ConvertTo-Json -Compress
$postBlockOutput = Invoke-PythonHook -Script $postTool -InputJson $postBlockPayload
Assert-JsonField -JsonText $postBlockOutput -Predicate {
    param($value)
    $value.decision -eq 'block' -and $value.continue -eq $false
} -Message 'PostToolUse large Bash output did not block.'

$postShellCommandBlockPayload = @{ tool_name = 'shell_command'; tool_response = $largeResponse } | ConvertTo-Json -Compress
$postShellCommandBlockOutput = Invoke-PythonHook -Script $postTool -InputJson $postShellCommandBlockPayload
Assert-JsonField -JsonText $postShellCommandBlockOutput -Predicate {
    param($value)
    $value.decision -eq 'block' -and $value.continue -eq $false
} -Message 'PostToolUse large shell_command output did not block.'

$postPassPayload = @{ tool_name = 'python'; tool_response = $largeResponse } | ConvertTo-Json -Compress
$postPassOutput = Invoke-PythonHook -Script $postTool -InputJson $postPassPayload
if (-not [string]::IsNullOrWhiteSpace(($postPassOutput -join ''))) {
    throw 'PostToolUse non-Bash large output should not block.'
}

$stopMissingVerification = Invoke-PythonHook -Script $stopHook -InputJson '{"last_assistant_message":"implemented the requested change in the portable kit, updated multiple files, and changed installer behavior for migration."}'
Assert-JsonField -JsonText $stopMissingVerification -Predicate {
    param($value)
    $value.decision -eq 'block'
} -Message 'Stop hook did not require verification for work-done message.'

$transcript = Join-Path $CodexHome 'transcript-subagent.jsonl'
$transcriptContent = @(
    '{"payload":{"type":"message","role":"user","content":"implement a feature"}}',
    '{"payload":{"name":"spawn_agent"}}'
) -join "`n"
[System.IO.File]::WriteAllText($transcript, $transcriptContent, [System.Text.UTF8Encoding]::new($false))

$stopLifecyclePayload = @{ transcript_path = $transcript; last_assistant_message = 'implemented the requested change in the portable kit and updated installer behavior. Verification: tests run.' } | ConvertTo-Json -Compress
$stopLifecycleOutput = Invoke-PythonHook -Script $stopHook -InputJson $stopLifecyclePayload
Assert-JsonField -JsonText $stopLifecycleOutput -Predicate {
    param($value)
    $value.decision -eq 'block'
} -Message 'Stop hook did not require subagent lifecycle status.'

$stopPassPayload = @{ transcript_path = $transcript; last_assistant_message = 'implemented the requested change in the portable kit and updated installer behavior. Verification: tests run. Subagents: closed.' } | ConvertTo-Json -Compress
$stopPassOutput = Invoke-PythonHook -Script $stopHook -InputJson $stopPassPayload
Assert-JsonField -JsonText $stopPassOutput -Predicate {
    param($value)
    $value.continue -eq $true
} -Message 'Stop hook did not pass with verification and lifecycle status.'

$structuredAcceptanceMessage = @'
implemented the requested change in the portable kit and updated installer behavior.
verification_status=pass
review_status=not_required
subagent_status=closed
risk_status=low
'@
$structuredStopPayload = @{ transcript_path = $transcript; last_assistant_message = $structuredAcceptanceMessage } | ConvertTo-Json -Compress
$structuredStopOutput = Invoke-PythonHook -Script $stopHook -InputJson $structuredStopPayload
Assert-JsonField -JsonText $structuredStopOutput -Predicate {
    param($value)
    $value.continue -eq $true
} -Message 'Stop hook did not pass with structured acceptance footer.'

$sessionsRoot = Join-Path $CodexHome 'sessions'
New-Item -ItemType Directory -Path $sessionsRoot -Force | Out-Null
$transcriptForObservability = Join-Path $sessionsRoot 'smoke-observability.jsonl'
$eventsRoot = Join-Path $CodexHome 'orchestrator-observability\events'

$preNoCapture = Invoke-PythonHook -Script $preTool -InputJson (@{
    tool_name = 'bash'
    tool_input = @{ command = 'echo hi' }
    transcript_path = $transcriptForObservability
} | ConvertTo-Json -Compress -Depth 10)
if (-not [string]::IsNullOrWhiteSpace($preNoCapture)) {
    throw 'PreToolUse non-blocking execution should not emit JSON block payload.'
}

Remove-Item Env:ORCHESTRATOR_OBSERVABILITY_CAPTURE_TEXT -ErrorAction SilentlyContinue
$eventLog = Get-ChildItem -Path $eventsRoot -Filter '*.jsonl' | Sort-Object LastWriteTime -Descending | Select-Object -First 1
if (-not $eventLog) {
    throw 'No observability event log was written.'
}
$preNoCaptureEvent = Read-JsonlLast -Path $eventLog.FullName
if ($preNoCaptureEvent.event -ne 'orchestrator_observability') {
    throw 'Observability event for pre_tool_use was not written.'
}
if ($preNoCaptureEvent.transcript_path -ne $transcriptForObservability) {
    throw 'Observability event did not preserve transcript_path metadata.'
}
if ($preNoCaptureEvent.PSObject.Properties.Name -contains 'captured_text') {
    if ($preNoCaptureEvent.captured_text) {
        throw 'Capture text default should be disabled for privacy.'
    }
}
if ($preNoCaptureEvent.event_name -ne 'pre_tool_use') {
    throw 'Expected pre_tool_use observability event.'
}
if ($preNoCaptureEvent.normalized_tool_name -ne 'bash' -or $preNoCaptureEvent.tool_family -ne 'shell_like' -or $preNoCaptureEvent.input_length -lt 1) {
    throw 'PreToolUse observability event missing normalized tool-surface metadata.'
}
if (Test-Path $transcriptForObservability) {
    throw 'Observability logging should not append to Codex transcript files.'
}
if ((Get-Content -Raw $eventLog.FullName) -match "`r`n`r`n") {
    throw 'Observability event should be compact JSONL (single-line entries).'
}

$postNonShellMetadataPayload = @{
    tool_name = 'python'
    tool_response = $largeResponse
    transcript_path = $transcriptForObservability
} | ConvertTo-Json -Compress
$postNonShellMetadataOutput = Invoke-PythonHook -Script $postTool -InputJson $postNonShellMetadataPayload
if (-not [string]::IsNullOrWhiteSpace(($postNonShellMetadataOutput -join ''))) {
    throw 'PostToolUse non-shell metadata case should not block.'
}
$postNonShellEvent = Read-JsonlLast -Path $eventLog.FullName
if ($postNonShellEvent.event_name -ne 'post_tool_use' -or $postNonShellEvent.normalized_tool_name -ne 'python' -or $postNonShellEvent.tool_family -ne 'generic' -or $postNonShellEvent.output_length -lt 60001) {
    throw 'PostToolUse non-shell event missing normalized tool-surface metadata.'
}

$env:ORCHESTRATOR_OBSERVABILITY_CAPTURE_TEXT = '1'
$fakeSecret = 'sk-' + '1234567890abcdefghijklmnopqrstuvwxyz'
$preCapture = Invoke-PythonHook -Script $preTool -InputJson (@{
    tool_name = 'bash'
    tool_input = @{ command = "echo API_KEY=$fakeSecret" }
    transcript_path = $transcriptForObservability
} | ConvertTo-Json -Compress -Depth 10)
if (-not [string]::IsNullOrWhiteSpace($preCapture)) {
    throw 'Captured pre_tool_use should not emit a block decision.'
}
$preCaptureEvent = Read-JsonlLast -Path $eventLog.FullName
if (-not ($preCaptureEvent.PSObject.Properties.Name -contains 'captured_text') -or -not $preCaptureEvent.captured_text) {
    throw 'Capture text was enabled but no captured text was recorded.'
}
if ($preCaptureEvent.captured_text -match [regex]::Escape($fakeSecret)) {
    throw 'Sensitive key was not redacted from captured text.'
}

$structuredPassword = 'hunter' + '2'
$structuredApiKey = 'abc' + '123'
$structuredSecretJson = '{"password":"' + $structuredPassword + '","api_key":"' + $structuredApiKey + '"}'
$preStructuredCapture = Invoke-PythonHook -Script $preTool -InputJson (@{
    tool_name = 'bash'
    tool_input = @{ command = "echo $structuredSecretJson" }
    transcript_path = $transcriptForObservability
} | ConvertTo-Json -Compress -Depth 10)
if (-not [string]::IsNullOrWhiteSpace($preStructuredCapture)) {
    throw 'Structured captured pre_tool_use should not emit a block decision.'
}
$preStructuredEvent = Read-JsonlLast -Path $eventLog.FullName
if ($preStructuredEvent.captured_text -match [regex]::Escape($structuredPassword) -or $preStructuredEvent.captured_text -match [regex]::Escape($structuredApiKey)) {
    throw 'Structured secret values were not redacted from captured text.'
}

$analyzerFixture = Join-Path $root 'tests\fixtures\session-observability.jsonl'
$fixtureSession = Join-Path $sessionsRoot 'session-observability.jsonl'
Copy-Item -Path $analyzerFixture -Destination $fixtureSession -Force
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
$turnstateReport = Join-Path $AgentsHome 'skills\turnstate-observability\scripts\turnstate_report.py'
$reportId = ('smoke-' + [Guid]::NewGuid().ToString('N'))
$python = Get-PythonCommand
$unitArgs = @()
if ($python.Length -gt 1) {
    $unitArgs += $python[1..($python.Length - 1)]
}
& $python[0] @($unitArgs + (Join-Path $root 'tests\test_session_facts_since_days.py'))
$analyzerOutput = & $python[0] @($python[1..($python.Length - 1)] + $analyzer + @('--codex-home', $CodexHome, '--since-days', 30, '--report-id', $reportId))
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

$structuredStopFact = $reportObj.facts | Where-Object { $_.event_type -eq 'orchestrator_stop' -and $_.acceptance.source -eq 'structured' } | Select-Object -First 1
if (-not $structuredStopFact -or $structuredStopFact.acceptance.verification_status -ne 'pass') {
    throw 'Analyzer facts did not preserve structured acceptance fields.'
}
if (-not ($reportObj.facts | Where-Object { $_.event_type -eq 'orchestrator_pre_tool_use' -and $_.tool_name -eq 'bash' -and $_.normalized_tool_name -eq 'bash' })) {
    throw 'Analyzer facts did not preserve orchestrator event tool_name and normalized alias.'
}
$structuredVerifiedProp = $reportObj.structured_acceptance.PSObject.Properties | Where-Object { $_.Name -eq 'structured_acceptance_verified' } | Select-Object -First 1
$structuredVerified = if ($structuredVerifiedProp) { $structuredVerifiedProp.Value } else { 0 }
if (-not $structuredVerified -or $structuredVerified -lt 1) {
    throw 'Analyzer structured acceptance counters did not count verified structured footer.'
}

$subagentWorkflowReportId = ('smoke-subagent-' + [Guid]::NewGuid().ToString('N'))
$subagentWorkflowOutput = & $python[0] @($python[1..($python.Length - 1)] + $subagentWorkflow + @('--codex-home', $CodexHome, '--since-days', 30, '--report-id', $subagentWorkflowReportId))
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
if ($subagentWorkflowObj.report_type -ne 'subagent_workflow' -or $subagentWorkflowObj.workflow_signals.broad_scan_blocks -lt 1) {
    throw 'Subagent workflow report missing expected workflow signals.'
}
$rawSubagentWorkflowText = Get-Content -Raw -Encoding UTF8 $subagentWorkflowJsonReport
if ($rawSubagentWorkflowText -match [regex]::Escape($contextProject) -or $rawSubagentWorkflowText -match 'supersecret' -or $rawSubagentWorkflowText -match 'sk-1234567890abcdefghijklmnopqrstuvwxyz') {
    throw 'Subagent workflow report leaked project context path or secret.'
}

$turnstateReportId = ('smoke-turnstate-' + [Guid]::NewGuid().ToString('N'))
$turnstateOutput = & $python[0] @($python[1..($python.Length - 1)] + $turnstateReport + @('--codex-home', $CodexHome, '--since-days', 30, '--report-id', $turnstateReportId))
$turnstateOutputObj = $turnstateOutput | ConvertFrom-Json
if ($turnstateOutputObj.sessions_seen -lt 1) {
    throw 'Turnstate report did not see fixture sessions.'
}

$turnstateJsonReport = Join-Path (Join-Path $CodexHome 'turnstate-observability\reports') "turnstate-compliance-$turnstateReportId.json"
$turnstateMdReport = Join-Path (Join-Path $CodexHome 'turnstate-observability\reports') "turnstate-compliance-$turnstateReportId.md"
if (-not (Test-Path $turnstateJsonReport)) {
    throw "Missing Turnstate JSON report: $turnstateJsonReport"
}
if (-not (Test-Path $turnstateMdReport)) {
    throw "Missing Turnstate markdown report: $turnstateMdReport"
}

$turnstateReportObj = Get-Content -Raw -Encoding UTF8 $turnstateJsonReport | ConvertFrom-Json
if ($turnstateReportObj.rule_profile -ne 'turnstate-default-v1') {
    throw 'Turnstate report used an unexpected rule profile.'
}
if (-not ($turnstateReportObj.PSObject.Properties.Name -contains 'score')) {
    throw 'Turnstate report missing score.'
}
if (-not ($turnstateReportObj.rule_failures.PSObject.Properties.Name -contains 'final_acceptance_present')) {
    throw 'Turnstate report did not classify missing final acceptance verification.'
}
if (-not $turnstateReportObj.suggestions -or $turnstateReportObj.suggestions.Count -lt 1) {
    throw 'Turnstate report did not include suggestions.'
}
$turnstateContextEntryProp = $turnstateReportObj.project_context.PSObject.Properties | Where-Object { $_.Name -eq $contextKey } | Select-Object -First 1
$turnstateContextEntry = if ($turnstateContextEntryProp) { $turnstateContextEntryProp.Value } else { $null }
if (-not $turnstateContextEntry -or -not $turnstateContextEntry.available) {
    throw 'Turnstate report did not include available project context.'
}
if ($turnstateContextEntry.summary -match 'supersecret' -or $turnstateContextEntry.summary -match 'sk-1234567890abcdefghijklmnopqrstuvwxyz' -or $turnstateContextEntry.summary -notmatch '\[redacted\]') {
    throw 'Turnstate project context summary was not redacted.'
}
$rawTurnstateReportText = Get-Content -Raw -Encoding UTF8 $turnstateJsonReport
if ($rawTurnstateReportText -match 'please implement') {
    throw 'Turnstate report leaked assistant message content.'
}
if ($rawTurnstateReportText -match 'C:/repo' -or $rawTurnstateReportText -match 'D:/repo' -or $rawTurnstateReportText -match 'E:\\repo' -or $rawTurnstateReportText -match [regex]::Escape($CodexHome) -or $rawTurnstateReportText -match [regex]::Escape($contextProject) -or $rawTurnstateReportText -match 'sk-1234567890abcdefghijklmnopqrstuvwxyz') {
    throw 'Turnstate report leaked full local paths while --include-paths was not set.'
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
    throw 'High-risk triage did not write high-risk-current alert for UserPromptSubmit handoff.'
}

$highReminderOutput = Invoke-PythonHook -Script $userPrompt -InputJson (@{
    prompt = 'hello'
    cwd = 'C:\safe\project'
} | ConvertTo-Json -Compress)
Assert-JsonField -JsonText $highReminderOutput -Predicate {
    param($value)
    $context = $value.hookSpecificOutput.additionalContext
    $context -like '*ORCHESTRATOR HIGH-RISK ALERT*' -and
    $context -like '*high-risk*' -and
    $context -notlike "*$highReportPath*"
} -Message 'UserPromptSubmit did not emit privacy-bounded high-risk reminder.'

$alertAfterReminder = Get-Content -Raw -Encoding UTF8 $currentAlert | ConvertFrom-Json
if ($alertAfterReminder.reminder_count -lt 1 -or -not $alertAfterReminder.last_reminded_at) {
    throw 'UserPromptSubmit did not update high-risk reminder cooldown state.'
}

$suppressedReminderOutput = Invoke-PythonHook -Script $userPrompt -InputJson (@{
    prompt = 'hello again'
    cwd = 'C:\safe\project'
} | ConvertTo-Json -Compress)
if (-not [string]::IsNullOrWhiteSpace(($suppressedReminderOutput -join ''))) {
    throw 'UserPromptSubmit should suppress the same high-risk reminder within 24 hours.'
}

$alertState = Get-Content -Raw -Encoding UTF8 $currentAlert | ConvertFrom-Json
$alertState.last_reminded_at = '2000-01-01T00:00:00Z'
$alertState | ConvertTo-Json -Depth 10 | Set-Content -Path $currentAlert -Encoding utf8
$combinedReminderOutput = Invoke-PythonHook -Script $userPrompt -InputJson '{"prompt":"please implement this feature and add tests"}'
Assert-JsonField -JsonText $combinedReminderOutput -Predicate {
    param($value)
    $context = $value.hookSpecificOutput.additionalContext
    $context -like '*ORCHESTRATOR HIGH-RISK ALERT*' -and
    $context -like '*ORCHESTRATOR CHECK*'
} -Message 'UserPromptSubmit did not combine high-risk reminder with heavy prompt context.'

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
    }
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
    }
    suggestions = @('Automation looks stable; reduce error message verbosity in summaries.')
}
$benignAutomationReport | ConvertTo-Json -Depth 10 | Set-Content -Path $benignAutomationReportPath -Encoding utf8
$benignAutomationTriageOutput = & $python[0] @($python[1..($python.Length - 1)] + $triage + @('--codex-home', $CodexHome, '--since-days', 3, '--report', $benignAutomationReportPath))
$benignAutomationTriage = $benignAutomationTriageOutput | ConvertFrom-Json
if ($benignAutomationTriage.severity -ne 'none') {
    throw "Benign automation wording should not trigger high-risk triage: $benignAutomationTriageOutput"
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
if (Test-Path (Join-Path $AgentsHome 'skills\turnstate-observability\SKILL.md')) {
    throw 'Uninstall did not remove turnstate observability skill.'
}
if (Test-Path (Join-Path $CodexHome 'hooks\orchestrator_user_prompt.py')) {
    throw 'Uninstall did not remove orchestrator hook script.'
}
if (Test-Path (Join-Path $CodexHome 'hooks\orchestrator_observability.py')) {
    throw 'Uninstall did not remove observability helper hook file.'
}
$uninstalledHooks = Get-Content -Raw $installedHookPath
if ($uninstalledHooks -match 'orchestrator_(pre_tool_use|post_tool_use|stop|user_prompt)\.py') {
    throw 'Uninstall did not strip orchestrator hook entries.'
}
if ($uninstalledHooks -notmatch 'pre-existing-user-prompt\.py' -or $uninstalledHooks -notmatch 'pre-existing-pre-tool\.py') {
    throw 'Uninstall removed the pre-existing hook entry.'
}

Write-Host "smoke-test passed"

