param(
    [ValidateSet('Prepare','Restore')][string]$Action = 'Prepare',
    [ValidateSet('database_stopped','redis_stopped','worker_stopped','wrong_address','transaction_blocked','wrong_version')][string]$Case = 'worker_stopped'
)
. "$PSScriptRoot/Common.ps1"
Import-LocalSettings
$taskCommand = if ($Action -eq 'Restore') { 'restore' } else { 'prepare' }
& $PythonExe (Join-Path $ProjectRoot 'scripts/lab_control.py') $taskCommand --case $Case
Assert-Native 'Isolated fault preparation'
