param(
    [Parameter(Mandatory)][string]$ProjectPath,
    [string]$ProjectId,
    [string[]]$Reviewers = @('subagent-workflow'),
    [switch]$Force
)

Set-StrictMode -Version Latest
$ErrorActionPreference = 'Stop'

$scriptRoot = Split-Path -Parent $MyInvocation.MyCommand.Path
$resolvedProjectPath = [System.IO.Path]::GetFullPath($ProjectPath)
if (-not (Test-Path $resolvedProjectPath)) {
    throw "Project path not found: $resolvedProjectPath"
}

if ([string]::IsNullOrWhiteSpace($ProjectId)) {
    $ProjectId = Split-Path -Leaf $resolvedProjectPath
}

if (-not $Reviewers -or $Reviewers.Count -eq 0) {
    throw 'At least one reviewer must be specified.'
}

$codexDir = Join-Path $resolvedProjectPath '.codex'
$contextPath = Join-Path $codexDir 'project-context.md'
$profilePath = Join-Path $codexDir 'review-profile.toml'

if (-not $Force -and ((Test-Path $contextPath) -or (Test-Path $profilePath))) {
    throw "Project already has .codex review files. Use -Force to overwrite: $codexDir"
}

New-Item -ItemType Directory -Path $codexDir -Force | Out-Null

$contextTemplate = Get-Content -Raw -Encoding UTF8 (Join-Path $scriptRoot 'templates\project-context.md')
Set-Content -Path $contextPath -Value $contextTemplate -Encoding utf8

$reviewerLines = ($Reviewers | ForEach-Object { '  "' + ($_ -replace '"', '\"') + '",' }) -join [Environment]::NewLine
$profileTemplate = Get-Content -Raw -Encoding UTF8 (Join-Path $scriptRoot 'templates\review-profile.toml')
$profileTemplate = [regex]::Replace($profileTemplate, 'project_id\s*=\s*"[^"]*"', 'project_id = "' + ($ProjectId -replace '"', '\"') + '"')
$profileTemplate = [regex]::Replace($profileTemplate, '(?s)enabled_reviewers\s*=\s*\[.*?\]', "enabled_reviewers = [`n$reviewerLines`n]")
Set-Content -Path $profilePath -Value $profileTemplate -Encoding utf8

Write-Host "registered project review profile: $codexDir"
