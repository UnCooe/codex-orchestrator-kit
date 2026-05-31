param(
    [string]$CodexHome = $env:CODEX_HOME,
    [string]$AgentsHome = $env:AGENTS_HOME,
    [ValidateSet('auto', 'hooks_json', 'config_toml')] [string]$HookMode = 'auto',
    [ValidateSet('auto', 'agents', 'codex')] [string]$SkillsMode = 'auto',
    [switch]$Apply,
    [switch]$DryRun
)

Set-StrictMode -Version Latest
$ErrorActionPreference = 'Stop'

$scriptRoot = Split-Path -Parent $MyInvocation.MyCommand.Path
$timestamp = Get-Date -Format 'yyyyMMdd_HHmmss_fff'
$InstallDryRun = -not $Apply.IsPresent
if ($DryRun.IsPresent) {
    if ($Apply.IsPresent) {
        throw 'Use either -Apply or -DryRun, not both.'
    }
    $InstallDryRun = $true
}

function Get-CodexHome {
    param([string]$Value)

    if (-not [string]::IsNullOrWhiteSpace($Value)) {
        return [System.IO.Path]::GetFullPath($Value)
    }

    if ($env:HOME) {
        return Join-Path $env:HOME '.codex'
    }

    if ($env:USERPROFILE) {
        return Join-Path $env:USERPROFILE '.codex'
    }

    throw 'Cannot determine Codex home. Set CODEX_HOME or HOME/USERPROFILE.'
}

function Get-AgentsHome {
    param([string]$Value)

    if (-not [string]::IsNullOrWhiteSpace($Value)) {
        return [System.IO.Path]::GetFullPath($Value)
    }

    if ($env:HOME) {
        return Join-Path $env:HOME '.agents'
    }

    if ($env:USERPROFILE) {
        return Join-Path $env:USERPROFILE '.agents'
    }

    throw 'Cannot determine agents home. Set AGENTS_HOME or HOME/USERPROFILE.'
}

function Resolve-PythonCommand {
    if ($env:CODEX_ORCHESTRATOR_PYTHON) { return $env:CODEX_ORCHESTRATOR_PYTHON }
    if ($env:CODX_ORCHESTRATOR_PYTHON) { return $env:CODX_ORCHESTRATOR_PYTHON }
    if (Get-Command py -ErrorAction SilentlyContinue) { return 'py -3' }
    if (Get-Command python -ErrorAction SilentlyContinue) { return 'python' }
    if (Get-Command python3 -ErrorAction SilentlyContinue) { return 'python3' }
    throw 'No python executable found. Install Python or set CODEX_ORCHESTRATOR_PYTHON.'
}

function ConvertTo-JsonStringContent {
    param([Parameter(Mandatory)][string]$Value)

    $jsonString = ConvertTo-Json -InputObject $Value -Compress
    if ($jsonString.Length -ge 2 -and $jsonString.StartsWith('"') -and $jsonString.EndsWith('"')) {
        return $jsonString.Substring(1, $jsonString.Length - 2)
    }
    return $jsonString
}

function ConvertTo-HookPythonCommand {
    param([Parameter(Mandatory)][string]$Value)

    $trimmed = $Value.Trim()
    if ($trimmed.StartsWith('"') -or $trimmed.StartsWith("'")) {
        return $trimmed
    }

    if ($trimmed -match '^([A-Za-z]:\\.*?\.exe)(\s+.*)?$') {
        $args = if ($matches[2]) { $matches[2] } else { '' }
        return '"' + $matches[1] + '"' + $args
    }

    return $trimmed
}

function Write-Utf8NoBom {
    param(
        [Parameter(Mandatory)][string]$Path,
        [Parameter(Mandatory)][string]$Value
    )

    $encoding = [System.Text.UTF8Encoding]::new($false)
    [System.IO.File]::WriteAllText($Path, $Value, $encoding)
}

function Get-OrchestratorScriptName {
    param([string]$Command)

    if ([string]::IsNullOrWhiteSpace($Command)) { return $null }

    $tokenPattern = '(?:"([^"]+)"|''([^'']+)''|(\S+))'
    foreach ($match in [regex]::Matches($Command, $tokenPattern)) {
        $token = if ($match.Groups[1].Success) { $match.Groups[1].Value }
        elseif ($match.Groups[2].Success) { $match.Groups[2].Value }
        else { $match.Groups[3].Value }
        if (-not $token) { continue }

        if ($token -match '(?i)orchestrator_[^\\/"'']+\.py$') {
            return [System.IO.Path]::GetFileName($token)
        }
    }

    return $null
}

function Get-OrchestratorScriptsFromEntry {
    param([object]$Entry)

    $scripts = [System.Collections.Generic.HashSet[string]]::new([StringComparer]::OrdinalIgnoreCase)
    if (-not $Entry.hooks) { return $scripts }

    foreach ($hook in @($Entry.hooks)) {
        $script = Get-OrchestratorScriptName -Command $hook.command
        if ($null -ne $script) {
            $null = $scripts.Add($script)
        }
    }

    return $scripts
}

function Backup-Path {
    param([Parameter(Mandatory)][string]$Path)

    if (-not (Test-Path $Path)) { return }

    $backup = "${Path}.bak.$timestamp"
    Copy-Item -Path $Path -Destination $backup -Recurse -Force
    Write-Host "backed up -> $backup"
}

function Stage-Files {
    param(
        [string]$Source,
        [string]$Destination,
        [switch]$Recurse
    )

    if (-not (Test-Path $Source)) {
        throw "Source not found: $Source"
    }

    if (-not $Recurse) {
        $destDir = Split-Path $Destination -Parent
        if ((-not $InstallDryRun) -and (Test-Path $Destination)) { Backup-Path -Path $Destination }
        if ($InstallDryRun) {
            Write-Host "[DRY-RUN] $Source -> $Destination"
            return
        }

        New-Item -ItemType Directory -Path $destDir -Force | Out-Null
        Copy-Item -Path $Source -Destination $Destination -Force
        Write-Host "installed: $Destination"
        return
    }

    $items = Get-ChildItem -Path $Source -Recurse -File | Where-Object {
        $_.FullName -notmatch '[\\/]+__pycache__[\\/]+' -and $_.Extension -ne '.pyc'
    }
    foreach ($item in $items) {
        $relative = $item.FullName.Substring($Source.Length).TrimStart('\\')
        $destinationFile = Join-Path $Destination $relative
        $parent = Split-Path $destinationFile -Parent
        if ((-not $InstallDryRun) -and (Test-Path $destinationFile)) { Backup-Path -Path $destinationFile }

        if ($InstallDryRun) {
            Write-Host "[DRY-RUN] $($item.FullName) -> $destinationFile"
            continue
        }

        New-Item -ItemType Directory -Path $parent -Force | Out-Null
        Copy-Item -Path $item.FullName -Destination $destinationFile -Force
        Write-Host "installed: $destinationFile"
    }
}

function Merge-Hooks {
    param(
        [string]$TemplateContent,
        [string]$ExistingPath,
        [string]$OutputPath
    )

    $templateObj = $TemplateContent | ConvertFrom-Json
    $mergedObj = @{}

    if (Test-Path $ExistingPath) {
        try {
            $currentObj = Get-Content -Path $ExistingPath -Raw | ConvertFrom-Json
            if ($currentObj.hooks) {
                foreach ($prop in $currentObj.hooks.PSObject.Properties) {
                    $mergedObj[$prop.Name] = @($prop.Value)
                }
            }
        } catch {
            Write-Warning "Existing hooks.json cannot be parsed. It will be replaced after backup."
        }
    }

    foreach ($eventProp in $templateObj.hooks.PSObject.Properties) {
        $eventName = $eventProp.Name
        $incomingEvents = @($eventProp.Value)

        if (-not $mergedObj.ContainsKey($eventName)) {
            $mergedObj[$eventName] = $incomingEvents
            continue
        }

        $existingEntries = @($mergedObj[$eventName])
        $incomingScripts = [System.Collections.Generic.HashSet[string]]::new([StringComparer]::OrdinalIgnoreCase)
        foreach ($incomingEntry in $incomingEvents) {
            foreach ($script in Get-OrchestratorScriptsFromEntry -Entry $incomingEntry) {
                $null = $incomingScripts.Add($script)
            }
        }

        if ($incomingScripts.Count -gt 0) {
            $dedupedExisting = [System.Collections.Generic.List[object]]::new()
            foreach ($existingEntry in $existingEntries) {
                if (-not $existingEntry.hooks) {
                    $dedupedExisting.Add($existingEntry)
                    continue
                }

                $keptHooks = [System.Collections.Generic.List[object]]::new()
                foreach ($hook in @($existingEntry.hooks)) {
                    $script = Get-OrchestratorScriptName -Command $hook.command
                    if ($null -ne $script -and $incomingScripts.Contains($script)) {
                        continue
                    }
                    $keptHooks.Add($hook)
                }

                if ($keptHooks.Count -eq 0) {
                    continue
                }

                $entryCopy = [ordered]@{}
                foreach ($prop in $existingEntry.PSObject.Properties) {
                    if ($prop.Name -eq 'hooks') {
                        $entryCopy.hooks = @($keptHooks)
                    } else {
                        $entryCopy[$prop.Name] = $prop.Value
                    }
                }
                $dedupedExisting.Add([pscustomobject]$entryCopy)
            }
            $existingEntries = @($dedupedExisting)
        }

        $existingKeys = [System.Collections.Generic.HashSet[string]]::new([StringComparer]::Ordinal)
        foreach ($entry in $existingEntries) {
            $existingKeys.Add((ConvertTo-Json $entry -Depth 20 -Compress)) | Out-Null
        }

        foreach ($entry in $incomingEvents) {
            $sig = ConvertTo-Json $entry -Depth 20 -Compress
            if (-not $existingKeys.Contains($sig)) {
                $existingEntries += $entry
                $existingKeys.Add($sig) | Out-Null
            }
        }

        $mergedObj[$eventName] = $existingEntries
    }

    $orderedEvents = $mergedObj.Keys | Sort-Object
    $outputObj = [ordered]@{ hooks = [ordered]@{} }
    foreach ($key in $orderedEvents) {
        $outputObj.hooks[$key] = $mergedObj[$key]
    }

    if ($InstallDryRun) {
        Write-Host '[DRY-RUN] merged hooks.json:'
        Write-Output (ConvertTo-Json $outputObj -Depth 20)
        return
    }

    New-Item -ItemType Directory -Path (Split-Path $OutputPath -Parent) -Force | Out-Null
    $json = ConvertTo-Json $outputObj -Depth 20
    Write-Utf8NoBom -Path $OutputPath -Value ($json + [Environment]::NewLine)
    Write-Host "installed: $OutputPath"
}

function Resolve-HookMode {
    param(
        [Parameter(Mandatory)][string]$Mode,
        [Parameter(Mandatory)][string]$CodexHomePath
    )

    if ($Mode -ne 'auto') { return $Mode }
    if (Test-Path (Join-Path $CodexHomePath 'hooks.json')) { return 'hooks_json' }
    if (Test-Path (Join-Path $CodexHomePath 'config.toml')) { return 'config_toml' }
    return 'hooks_json'
}

function Resolve-SkillsMode {
    param(
        [Parameter(Mandatory)][string]$Mode,
        [Parameter(Mandatory)][string]$ResolvedHookMode,
        [Parameter(Mandatory)][string]$CodexHomePath,
        [Parameter(Mandatory)][string]$AgentsHomePath
    )

    if ($Mode -ne 'auto') { return $Mode }
    if (Test-Path (Join-Path $CodexHomePath 'skills')) { return 'codex' }
    if ($ResolvedHookMode -eq 'config_toml') { return 'codex' }
    if (Test-Path (Join-Path $AgentsHomePath 'skills')) { return 'agents' }
    return 'agents'
}

function Merge-ConfigTomlHooks {
    param(
        [Parameter(Mandatory)][string]$TemplateContent,
        [Parameter(Mandatory)][string]$OutputPath,
        [Parameter(Mandatory)][string]$BeginMarker,
        [Parameter(Mandatory)][string]$EndMarker
    )

    try {
        $current = Get-Content -Path $OutputPath -Raw -Encoding utf8
    } catch {
        $current = ''
    }

    $managedPattern = '(?ms)^[ \t]*' + [regex]::Escape($BeginMarker) + "`r?`n.*?^[ \t]*" + [regex]::Escape($EndMarker) + "`r?`n?"
    $current = [regex]::Replace($current, $managedPattern, '')

    $eventHeaderRegex = '^\[\[hooks\.([A-Za-z0-9_]+)\]\]$'
    $orchestratorScriptRegex = 'orchestrator_[^\\/''"]+\.py'

    function Split-HookRegion {
        param([string]$Text)

        $lines = [regex]::Split($Text, "`r?`n")
        if ($Text -match "`n$") {
            $trailingNewline = $true
        } else {
            $trailingNewline = $false
        }
        $start = -1
        for ($i = 0; $i -lt $lines.Length; $i++) {
            if ($lines[$i].Trim() -match $eventHeaderRegex) {
                $start = $i
                break
            }
        }
        if ($start -lt 0) {
            return @{
                Prefix = $Text.TrimEnd()
                HookRegion = ''
                Suffix = ''
            }
        }
        $end = $lines.Length
        for ($i = $start + 1; $i -lt $lines.Length; $i++) {
            $trimmed = $lines[$i].Trim()
            if (-not $trimmed) { continue }
            if ($trimmed.StartsWith('[') -and -not $trimmed.StartsWith('[[hooks.')) {
                $end = $i
                break
            }
        }
        if ($start -gt 0) {
            $prefix = (($lines[0..($start - 1)] -join [Environment]::NewLine)).TrimEnd()
        } else {
            $prefix = ''
        }
        $hookRegion = ($lines[$start..($end - 1)] -join [Environment]::NewLine)
        if ($trailingNewline -and $hookRegion) {
            $hookRegion += [Environment]::NewLine
        }
        if ($end -lt $lines.Length) {
            $suffix = ($lines[$end..($lines.Length - 1)] -join [Environment]::NewLine).TrimStart("`r", "`n")
        } else {
            $suffix = ''
        }
        return @{
            Prefix = $prefix
            HookRegion = $hookRegion
            Suffix = $suffix
        }
    }

    function Parse-EventBlocks {
        param([string]$Text)

        $lines = [regex]::Split($Text, "`r?`n")
        $blocks = [System.Collections.Generic.List[object]]::new()
        $currentEvent = $null
        $currentLines = [System.Collections.Generic.List[string]]::new()
        foreach ($line in $lines) {
            $trimmed = $line.Trim()
            if ($trimmed -match $eventHeaderRegex) {
                if ($null -ne $currentEvent) {
                    $blocks.Add(@{ Event = $currentEvent; Text = (($currentLines -join [Environment]::NewLine).TrimEnd() + [Environment]::NewLine) })
                }
                $currentEvent = $Matches[1]
                $currentLines = [System.Collections.Generic.List[string]]::new()
                $currentLines.Add($line)
                continue
            }
            if ($null -ne $currentEvent) {
                $currentLines.Add($line)
            }
        }
        if ($null -ne $currentEvent) {
            $blocks.Add(@{ Event = $currentEvent; Text = (($currentLines -join [Environment]::NewLine).TrimEnd() + [Environment]::NewLine) })
        }
        return $blocks
    }

    function Split-EventBlock {
        param(
            [string]$BlockText,
            [string]$Event
        )

        $lines = [regex]::Split($BlockText, "`r?`n")
        $header = [System.Collections.Generic.List[string]]::new()
        $subblocks = [System.Collections.Generic.List[string]]::new()
        $currentSubblock = [System.Collections.Generic.List[string]]::new()
        $insideSubblock = $false
        $expectedHeader = "[[hooks.$Event.hooks]]"
        foreach ($line in $lines) {
            if ($line.Trim() -eq $expectedHeader) {
                if ($insideSubblock -and $currentSubblock.Count -gt 0) {
                    $subblocks.Add(($currentSubblock -join [Environment]::NewLine).TrimEnd() + [Environment]::NewLine)
                }
                $currentSubblock = [System.Collections.Generic.List[string]]::new()
                $currentSubblock.Add($line)
                $insideSubblock = $true
                continue
            }
            if ($insideSubblock) {
                $currentSubblock.Add($line)
            } else {
                $header.Add($line)
            }
        }
        if ($insideSubblock -and $currentSubblock.Count -gt 0) {
            $subblocks.Add(($currentSubblock -join [Environment]::NewLine).TrimEnd() + [Environment]::NewLine)
        }
        return @{
            Header = (($header -join [Environment]::NewLine).TrimEnd() + [Environment]::NewLine)
            Subblocks = $subblocks
        }
    }

    function Normalize-EventBlock {
        param(
            [string]$BlockText,
            [string]$Event
        )

        $parts = Split-EventBlock -BlockText $BlockText -Event $Event
        $kept = [System.Collections.Generic.List[string]]::new()
        foreach ($subblock in $parts.Subblocks) {
            if ($subblock -notmatch $orchestratorScriptRegex) {
                $kept.Add($subblock)
            }
        }
        if ($kept.Count -eq 0) {
            return $null
        }
        $result = $parts.Header.TrimEnd() + [Environment]::NewLine
        foreach ($subblock in $kept) {
            $result += [Environment]::NewLine + $subblock.TrimEnd() + [Environment]::NewLine
        }
        return $result.TrimEnd() + [Environment]::NewLine
    }

    function Append-ManagedHook {
        param(
            [string]$ExistingBlock,
            [string]$ManagedBlock,
            [string]$Event
        )

        $existingParts = Split-EventBlock -BlockText $ExistingBlock -Event $Event
        $managedParts = Split-EventBlock -BlockText $ManagedBlock -Event $Event
        $existingSubblocks = [System.Collections.Generic.List[string]]::new()
        foreach ($subblock in $existingParts.Subblocks) { $existingSubblocks.Add($subblock) }

        if ($existingSubblocks.Count -eq 0) {
            $baseHeader = if ($managedParts.Header.Trim()) { $managedParts.Header } else { $existingParts.Header }
        } else {
            $baseHeader = $existingParts.Header
        }

        $existingJoined = $existingSubblocks -join [Environment]::NewLine
        foreach ($subblock in $managedParts.Subblocks) {
            if (($subblock -match $orchestratorScriptRegex) -and ($existingJoined -match $orchestratorScriptRegex)) {
                continue
            }
            $existingSubblocks.Add($subblock)
            $existingJoined += [Environment]::NewLine + $subblock
        }

        $result = $baseHeader.TrimEnd() + [Environment]::NewLine
        foreach ($subblock in $existingSubblocks) {
            $result += [Environment]::NewLine + $subblock.TrimEnd() + [Environment]::NewLine
        }
        return $result.TrimEnd() + [Environment]::NewLine
    }

    $regions = Split-HookRegion -Text $current
    $existingBlocks = Parse-EventBlocks -Text $regions.HookRegion
    $managedBlocks = Parse-EventBlocks -Text $TemplateContent
    $orderedEvents = [System.Collections.Generic.List[string]]::new()
    $mergedBlocks = @{}

    foreach ($block in $existingBlocks) {
        $normalized = Normalize-EventBlock -BlockText $block.Text -Event $block.Event
        if ($null -eq $normalized) { continue }
        if (-not $orderedEvents.Contains($block.Event)) {
            $orderedEvents.Add($block.Event)
            $mergedBlocks[$block.Event] = $normalized
        } else {
            $mergedBlocks[$block.Event] = Append-ManagedHook -ExistingBlock $mergedBlocks[$block.Event] -ManagedBlock $normalized -Event $block.Event
        }
    }

    foreach ($block in $managedBlocks) {
        if (-not $orderedEvents.Contains($block.Event)) {
            $orderedEvents.Add($block.Event)
            $mergedBlocks[$block.Event] = $block.Text.TrimEnd() + [Environment]::NewLine
        } else {
            $mergedBlocks[$block.Event] = Append-ManagedHook -ExistingBlock $mergedBlocks[$block.Event] -ManagedBlock $block.Text -Event $block.Event
        }
    }

    $sections = [System.Collections.Generic.List[string]]::new()
    if ($regions.Prefix) { $sections.Add($regions.Prefix.TrimEnd()) }
    if ($orderedEvents.Count -gt 0) {
        $hookTexts = foreach ($event in $orderedEvents) { $mergedBlocks[$event].TrimEnd() }
        $sections.Add(($hookTexts -join ([Environment]::NewLine + [Environment]::NewLine)).TrimEnd())
    }
    if ($regions.Suffix) { $sections.Add($regions.Suffix.TrimEnd()) }
    $newText = ($sections -join ([Environment]::NewLine + [Environment]::NewLine)).TrimEnd() + [Environment]::NewLine

    if ($InstallDryRun) {
        Write-Host '[DRY-RUN] merged config.toml hooks block:'
        Write-Output $newText
        return
    }

    New-Item -ItemType Directory -Path (Split-Path $OutputPath -Parent) -Force | Out-Null
    Write-Utf8NoBom -Path $OutputPath -Value $newText
    Write-Host "installed: $OutputPath"
}

$CodexHome = Get-CodexHome $CodexHome
$AgentsHome = Get-AgentsHome $AgentsHome
if (-not $InstallDryRun) {
    if (-not $PSBoundParameters.ContainsKey('HookMode')) {
        throw 'Apply mode requires an explicit -HookMode value. Use -HookMode hooks_json, -HookMode config_toml, or -HookMode auto.'
    }
    if (-not $PSBoundParameters.ContainsKey('SkillsMode')) {
        throw 'Apply mode requires an explicit -SkillsMode value. Use -SkillsMode agents, -SkillsMode codex, or -SkillsMode auto.'
    }
}
$PythonCommand = Resolve-PythonCommand
$HookPythonCommand = ConvertTo-HookPythonCommand $PythonCommand
$CodexHomeNormalized = ($CodexHome -replace '\\', '/')
$ResolvedHookMode = Resolve-HookMode -Mode $HookMode -CodexHomePath $CodexHome
$ResolvedSkillsMode = Resolve-SkillsMode -Mode $SkillsMode -ResolvedHookMode $ResolvedHookMode -CodexHomePath $CodexHome -AgentsHomePath $AgentsHome
$SkillsRoot = if ($ResolvedSkillsMode -eq 'codex') { $CodexHome } else { $AgentsHome }

Write-Host "mode: $(if ($InstallDryRun) { 'dry-run' } else { 'apply' })"
Write-Host "input: source=$scriptRoot, codex_home=$CodexHome, agents_home=$AgentsHome, hook_mode=$ResolvedHookMode, skills_mode=$ResolvedSkillsMode"

$targets = @(
    @{ Source = Join-Path $scriptRoot 'skills\orchestrator'; Destination = Join-Path $SkillsRoot 'skills\orchestrator' },
    @{ Source = Join-Path $scriptRoot 'skills\orchestrator-observability'; Destination = Join-Path $SkillsRoot 'skills\orchestrator-observability' },
    @{ Source = Join-Path $scriptRoot 'hooks'; Destination = Join-Path $CodexHome 'hooks' },
    @{ Source = Join-Path $scriptRoot 'agents'; Destination = Join-Path $CodexHome 'agents' }
)

Write-Host 'install plan:'
foreach ($target in $targets) {
    $existing = if (Test-Path $target.Destination) { 'backup existing files before overwrite' } else { 'create/copy files' }
    Write-Host "  files: $($target.Source) -> $($target.Destination) ($existing)"
}
if ($ResolvedHookMode -eq 'hooks_json') {
    $plannedHookSurface = Join-Path $CodexHome 'hooks.json'
    $plannedHookAction = if (Test-Path $plannedHookSurface) { 'backup then merge' } else { 'create' }
} else {
    $plannedHookSurface = Join-Path $CodexHome 'config.toml'
    $plannedHookAction = if (Test-Path $plannedHookSurface) { 'backup then merge managed block' } else { 'create managed block' }
}
Write-Host "  hooks: $plannedHookSurface ($plannedHookAction)"

foreach ($target in $targets) {
    Stage-Files -Source $target.Source -Destination $target.Destination -Recurse
}

if ($ResolvedHookMode -eq 'hooks_json') {
    $hooksTemplate = Get-Content -Path (Join-Path $scriptRoot 'templates\hooks.json.tpl') -Raw
    $hooksTemplate = $hooksTemplate.Replace('{{CODEX_HOME}}', (ConvertTo-JsonStringContent $CodexHomeNormalized))
    $hooksTemplate = $hooksTemplate.Replace('{{PYTHON_EXE}}', (ConvertTo-JsonStringContent $HookPythonCommand))
    $hooksPath = Join-Path $CodexHome 'hooks.json'
    if ((-not $InstallDryRun) -and (Test-Path $hooksPath)) {
        Backup-Path -Path $hooksPath
    }

    Merge-Hooks -TemplateContent $hooksTemplate -ExistingPath $hooksPath -OutputPath $hooksPath
} else {
    $configTemplate = Get-Content -Path (Join-Path $scriptRoot 'templates\config.toml_hooks.tpl') -Raw
    $configTemplate = $configTemplate.Replace('{{CODEX_HOME}}', (ConvertTo-JsonStringContent $CodexHomeNormalized))
    $configTemplate = $configTemplate.Replace('{{PYTHON_EXE}}', (ConvertTo-JsonStringContent $HookPythonCommand))
    $configPath = Join-Path $CodexHome 'config.toml'
    if ((-not $InstallDryRun) -and (Test-Path $configPath)) {
        Backup-Path -Path $configPath
    }
    Merge-ConfigTomlHooks -TemplateContent $configTemplate -OutputPath $configPath -BeginMarker '# BEGIN codex-orchestrator-kit managed hooks' -EndMarker '# END codex-orchestrator-kit managed hooks'
}

Write-Host 'done'
