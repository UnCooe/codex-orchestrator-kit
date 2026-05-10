param(
    [string]$CodexHome = $env:CODEX_HOME,
    [string]$AgentsHome = $env:AGENTS_HOME,
    [switch]$DryRun
)

Set-StrictMode -Version Latest
$ErrorActionPreference = 'Stop'

$scriptRoot = Split-Path -Parent $MyInvocation.MyCommand.Path
$timestamp = Get-Date -Format 'yyyyMMdd_HHmmss_fff'

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
        if ((-not $DryRun) -and (Test-Path $Destination)) { Backup-Path -Path $Destination }
        if ($DryRun) {
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
        if ((-not $DryRun) -and (Test-Path $destinationFile)) { Backup-Path -Path $destinationFile }

        if ($DryRun) {
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

    if ($DryRun) {
        Write-Host '[DRY-RUN] merged hooks.json:'
        Write-Output (ConvertTo-Json $outputObj -Depth 20)
        return
    }

    New-Item -ItemType Directory -Path (Split-Path $OutputPath -Parent) -Force | Out-Null
    $json = ConvertTo-Json $outputObj -Depth 20
    Set-Content -Path $OutputPath -Value $json -Encoding utf8
    Write-Host "installed: $OutputPath"
}

$CodexHome = Get-CodexHome $CodexHome
$AgentsHome = Get-AgentsHome $AgentsHome
$PythonCommand = Resolve-PythonCommand
$HookPythonCommand = ConvertTo-HookPythonCommand $PythonCommand
$CodexHomeNormalized = ($CodexHome -replace '\\', '/')

Write-Host "input: source=$scriptRoot, codex_home=$CodexHome, agents_home=$AgentsHome"

$targets = @(
    @{ Source = Join-Path $scriptRoot 'skills\orchestrator'; Destination = Join-Path $AgentsHome 'skills\orchestrator' },
    @{ Source = Join-Path $scriptRoot 'skills\orchestrator-observability'; Destination = Join-Path $AgentsHome 'skills\orchestrator-observability' },
    @{ Source = Join-Path $scriptRoot 'hooks'; Destination = Join-Path $CodexHome 'hooks' },
    @{ Source = Join-Path $scriptRoot 'agents'; Destination = Join-Path $CodexHome 'agents' }
)

foreach ($target in $targets) {
    Stage-Files -Source $target.Source -Destination $target.Destination -Recurse
}

$hooksTemplate = Get-Content -Path (Join-Path $scriptRoot 'templates\hooks.json.tpl') -Raw
$hooksTemplate = $hooksTemplate.Replace('{{CODEX_HOME}}', (ConvertTo-JsonStringContent $CodexHomeNormalized))
$hooksTemplate = $hooksTemplate.Replace('{{PYTHON_EXE}}', (ConvertTo-JsonStringContent $HookPythonCommand))
$hooksPath = Join-Path $CodexHome 'hooks.json'
if ((-not $DryRun) -and (Test-Path $hooksPath)) {
    Backup-Path -Path $hooksPath
}

Merge-Hooks -TemplateContent $hooksTemplate -ExistingPath $hooksPath -OutputPath $hooksPath

Write-Host 'done'
