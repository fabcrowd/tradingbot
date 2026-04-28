#Requires -Version 5.1
<#
.SYNOPSIS
  Copy Cursor assets from a local clone of https://github.com/fabcrowd/skills into this repo's .cursor/

.DESCRIPTION
  Expects the skills repo layout (see that repo's README):
    skills/<skill-id>/SKILL.md
    agents/*.md
    rules/*.mdc
    references/*.md

  Set FABSKILLS_REPO to the root of your clone, or pass -SkillsRepo.

.EXAMPLE
  $env:FABSKILLS_REPO = "C:\Users\you\Desktop\Repos\skills"
  .\scripts\sync_fabcrowd_skills.ps1
#>
param(
    [string] $SkillsRepo = $env:FABSKILLS_REPO
)

$ErrorActionPreference = "Stop"
$botRoot = Resolve-Path (Join-Path $PSScriptRoot "..")

if (-not $SkillsRepo -or -not (Test-Path -LiteralPath $SkillsRepo)) {
    Write-Error "FABSKILLS_REPO is unset or path missing. Clone https://github.com/fabcrowd/skills and set FABSKILLS_REPO to that folder (or pass -SkillsRepo)."
}

function Copy-TreeIfExists {
    param([string] $RelativeName)
    $src = Join-Path $SkillsRepo $RelativeName
    $dst = Join-Path $botRoot ".cursor\$RelativeName"
    if (-not (Test-Path -LiteralPath $src)) { return }
    New-Item -ItemType Directory -Force -Path $dst | Out-Null
    Copy-Item -Path (Join-Path $src "*") -Destination $dst -Recurse -Force
    Write-Host "Synced $RelativeName -> $dst"
}

Copy-TreeIfExists "skills"
Copy-TreeIfExists "agents"
Copy-TreeIfExists "rules"
Copy-TreeIfExists "references"
Write-Host "Done. Open this repo in Cursor to load project skills."
