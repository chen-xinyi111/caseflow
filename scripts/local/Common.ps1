$ErrorActionPreference = 'Stop'
$ProjectRoot = [IO.Path]::GetFullPath((Join-Path $PSScriptRoot '../..'))
$PythonExe = Join-Path $ProjectRoot '.venv/Scripts/python.exe'
$RuntimeRoot = Join-Path $ProjectRoot '.runtime'
function Assert-Native([string]$Label) {
    if ($LASTEXITCODE -ne 0) { throw "$Label failed (exit $LASTEXITCODE)" }
}
function Get-Docker {
    $taskCommand = Get-Command docker -ErrorAction SilentlyContinue
    if ($taskCommand) { return $taskCommand.Source }
    $taskCandidates = @(
        (Join-Path $env:LOCALAPPDATA 'Programs/DockerDesktop/resources/bin/docker.exe'),
        (Join-Path $env:ProgramFiles 'Docker/Docker/resources/bin/docker.exe')
    )
    $taskFound = $taskCandidates | Where-Object { Test-Path -LiteralPath $_ } | Select-Object -First 1
    if (!$taskFound) { throw 'Docker Desktop is required.' }
    return $taskFound
}
function Import-LocalSettings {
    foreach ($taskLine in (Get-Content -LiteralPath (Join-Path $ProjectRoot '.env'))) {
        if ($taskLine -match '^([A-Z][A-Z0-9_]*)=(.*)$') {
            [Environment]::SetEnvironmentVariable($Matches[1], $Matches[2], 'Process')
        }
    }
    if (!$env:DASHSCOPE_APIKEY) {
        $env:DASHSCOPE_APIKEY = [Environment]::GetEnvironmentVariable('DASHSCOPE_APIKEY','User')
    }
    if (!$env:DASHSCOPE_API_BASE) {
        $env:DASHSCOPE_API_BASE = [Environment]::GetEnvironmentVariable('DASHSCOPE_API_BASE','User')
    }
    $env:PYTHONPATH = Join-Path $ProjectRoot 'src'
    $env:PYTHONUTF8 = '1'
    $env:OPSLENS_DOCKER = Get-Docker
}
function Invoke-Compose([string]$Target, [string[]]$Arguments) {
    if ($Target -notin @('platform','healthy','fault')) { throw 'Unknown OpsLens target' }
    if ($Target -eq 'platform') {
        $taskProject = 'caseflow-platform'
        $taskFile = Join-Path $ProjectRoot 'compose/platform.yaml'
        $taskEnv = Join-Path $ProjectRoot '.env'
    } else {
        $taskProject = "opslens-lab-$Target"
        $taskFile = Join-Path $ProjectRoot 'compose/lab.yaml'
        $taskEnv = Join-Path $RuntimeRoot "$Target.env"
    }
    & $env:OPSLENS_DOCKER compose -p $taskProject --env-file $taskEnv -f $taskFile @Arguments
    Assert-Native "Compose $Target"
}
function Get-Pnpm {
    $taskCommand = Get-Command pnpm.cmd -ErrorAction SilentlyContinue
    if ($taskCommand) { return $taskCommand.Source }
    $taskBundled = Join-Path $env:USERPROFILE '.cache/codex-runtimes/codex-primary-runtime/dependencies/bin/fallback/pnpm.cmd'
    if (Test-Path -LiteralPath $taskBundled) { return $taskBundled }
    throw 'Node.js 24 and pnpm 11.19 are required.'
}

function Assert-Toolchain {
    $taskNode = Get-Command node -ErrorAction SilentlyContinue
    if (!$taskNode) {
        $taskNodePath = Join-Path $env:USERPROFILE '.cache/codex-runtimes/codex-primary-runtime/dependencies/node/bin'
        if (Test-Path -LiteralPath (Join-Path $taskNodePath 'node.exe')) { $env:PATH = "$taskNodePath;$env:PATH" }
        $taskNode = Get-Command node -ErrorAction SilentlyContinue
    }
    if (!$taskNode) { throw '需要Node.js 24；安装后重新打开PowerShell。' }
    $taskNodeVersion = & $taskNode.Source --version
    Assert-Native 'Node version'
    if ($taskNodeVersion -notmatch '^v24\.') { throw '需要Node.js 24。' }
    $taskPnpm = Get-Pnpm
    $taskPnpmVersion = & $taskPnpm --version
    Assert-Native 'pnpm version'
    if ($taskPnpmVersion.Trim() -notmatch '^11\.') { throw '需要pnpm 11，使用项目现有frozen-lockfile。' }
}
function Build-Workbench {
    Assert-Toolchain
    & $PythonExe (Join-Path $ProjectRoot 'scripts/export_openapi.py')
    Assert-Native 'OpenAPI export'
    Push-Location (Join-Path $ProjectRoot 'frontend')
    try {
        $taskPnpm = Get-Pnpm
        & $taskPnpm install --frozen-lockfile
        Assert-Native 'Frontend dependencies'
        & $taskPnpm run types
        Assert-Native 'Frontend API types'
        & $taskPnpm run build
        Assert-Native 'Frontend build'
    } finally { Pop-Location }
}
