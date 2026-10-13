param([switch]$VerifyRestore,[switch]$LeavePaused)
. "$PSScriptRoot/Common.ps1"
Import-LocalSettings
& $PythonExe -X utf8 (Join-Path $ProjectRoot 'scripts/archive_product.py') --check-active
Assert-Native 'Backup task check'
& "$PSScriptRoot/RestartPlatform.ps1" -StopOnly
try {
    $taskArguments=@('-X','utf8',(Join-Path $ProjectRoot 'scripts/archive_product.py'))
    if ($VerifyRestore) { $taskArguments += '--verify' }
    & $PythonExe @taskArguments
    Assert-Native 'Private backup and restore verification'
} finally {
    if (!$LeavePaused) { & "$PSScriptRoot/RestartPlatform.ps1" -SkipBuild }
}
