param([switch]$SelfCheck)
. "$PSScriptRoot/Common.ps1"
Import-LocalSettings
& "$PSScriptRoot/Check.ps1" -Scope Fault
# Pause only the platform; no background generation may claim our controlled tasks.
& "$PSScriptRoot/RestartPlatform.ps1" -StopOnly
try {
    $taskDemoArgs = @((Join-Path $ProjectRoot 'scripts/prepare_demo.py'))
    if ($SelfCheck) { $taskDemoArgs += '--selfcheck' }
    & $PythonExe @taskDemoArgs
    Assert-Native '真实演示准备'
} finally { & "$PSScriptRoot/RestartPlatform.ps1" -SkipBuild }
Write-Output '打开工作台 → 维护与记录 → 示例记录。准备演示只改变故障实例；恢复仍须页面批准。'
