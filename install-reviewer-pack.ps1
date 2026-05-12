param(
    [Parameter(Mandatory)][string]$PackPath,
    [string]$AgentsHome = $env:AGENTS_HOME,
    [string]$CodexHome = $env:CODEX_HOME,
    [ValidateSet('auto', 'agents', 'codex')] [string]$SkillsMode = 'auto',
    [switch]$DryRun
)

Set-StrictMode -Version Latest
$ErrorActionPreference = 'Stop'

$timestamp = Get-Date -Format 'yyyyMMdd_HHmmss_fff'

function Get-AgentsHome {
    param([string]$Value)

    if (-not [string]::IsNullOrWhiteSpace($Value)) {
        return [System.IO.Path]::GetFullPath($Value)
    }
    if ($env:HOME) { return Join-Path $env:HOME '.agents' }
    if ($env:USERPROFILE) { return Join-Path $env:USERPROFILE '.agents' }
    throw 'Cannot determine agents home. Set AGENTS_HOME or HOME/USERPROFILE.'
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

function Backup-Path {
    param([Parameter(Mandatory)][string]$Path)

    if (-not (Test-Path $Path)) { return }
    $backup = "${Path}.bak.$timestamp"
    Copy-Item -Path $Path -Destination $backup -Recurse -Force
    Write-Host "backed up -> $backup"
}

function Copy-SkillDirectory {
    param(
        [Parameter(Mandatory)][string]$Source,
        [Parameter(Mandatory)][string]$Destination
    )

    $items = Get-ChildItem -Path $Source -Recurse -File | Where-Object {
        $_.FullName -notmatch '[\\/]+__pycache__[\\/]+' -and $_.Extension -ne '.pyc'
    }

    foreach ($item in $items) {
        $relative = $item.FullName.Substring($Source.Length).TrimStart('\', '/')
        $destinationFile = Join-Path $Destination $relative
        $parent = Split-Path $destinationFile -Parent

        if ((-not $DryRun) -and (Test-Path $destinationFile)) {
            Backup-Path -Path $destinationFile
        }

        if ($DryRun) {
            Write-Host "[DRY-RUN] $($item.FullName) -> $destinationFile"
            continue
        }

        New-Item -ItemType Directory -Path $parent -Force | Out-Null
        Copy-Item -Path $item.FullName -Destination $destinationFile -Force
        Write-Host "installed: $destinationFile"
    }
}

$resolvedPackPath = [System.IO.Path]::GetFullPath($PackPath)
$skillsRoot = Join-Path $resolvedPackPath 'skills'
if (-not (Test-Path $skillsRoot)) {
    throw "Reviewer pack must contain a skills directory: $skillsRoot"
}

$CodexHome = Get-CodexHome $CodexHome
$AgentsHome = Get-AgentsHome $AgentsHome
$ResolvedSkillsMode = if ($SkillsMode -ne 'auto') {
    $SkillsMode
} elseif (Test-Path (Join-Path $CodexHome 'skills')) {
    'codex'
} elseif (Test-Path (Join-Path $AgentsHome 'skills')) {
    'agents'
} else {
    'agents'
}
$SkillsRootHome = if ($ResolvedSkillsMode -eq 'codex') { $CodexHome } else { $AgentsHome }
$skillDirs = @(Get-ChildItem -Path $skillsRoot -Directory)
if ($skillDirs.Count -eq 0) {
    throw "Reviewer pack has no skills to install: $skillsRoot"
}

foreach ($skillDir in $skillDirs) {
    $destination = Join-Path (Join-Path $SkillsRootHome 'skills') $skillDir.Name
    Copy-SkillDirectory -Source $skillDir.FullName -Destination $destination
}

Write-Host 'done'
