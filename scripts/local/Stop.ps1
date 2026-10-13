param([ValidateSet('Platform','Fault','All')][string]$Scope='Platform')
. "$PSScriptRoot/Common.ps1"
Import-LocalSettings
& "$PSScriptRoot/RestartPlatform.ps1" -StopOnly
if ($Scope -in @('Fault','All')) { Invoke-Compose 'fault' @('--profile','fault-tools','down') }
if ($Scope -eq 'All') { Invoke-Compose 'healthy' @('down') }
Invoke-Compose 'platform' @('down')
Write-Output "OpsLens 已停止（$Scope），数据卷保留。"
