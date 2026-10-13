param([ValidateSet('Platform','Fault','All')][string]$Scope='Platform')
. "$PSScriptRoot/Common.ps1"
if (!(Test-Path -LiteralPath $PythonExe) -or !(Test-Path -LiteralPath (Join-Path $ProjectRoot '.env'))) { throw '请先运行Setup.ps1；它不会启动实验服务。' }
Import-LocalSettings
Assert-Toolchain
& $PythonExe (Join-Path $ProjectRoot 'scripts/preflight.py') --scope $Scope
Assert-Native '只读资源预检'
