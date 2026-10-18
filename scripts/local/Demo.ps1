param([switch]$SelfCheck)
. "$PSScriptRoot/Common.ps1"
& "$PSScriptRoot/Start.ps1" -Scope Platform
$taskBase='http://127.0.0.1:18180/api/v1/caseflow'
$taskProjects=Invoke-RestMethod "$taskBase/projects"
$taskExamples=@{reservation='会议室预约 · 示例';permission='文件共享 · 示例';registration='活动报名 · 示例'}
foreach($taskKind in $taskExamples.Keys){
    if(!($taskProjects | Where-Object name -eq $taskExamples[$taskKind])){
        $taskExample=Invoke-RestMethod -Method Post "$taskBase/examples/$taskKind"
        if($SelfCheck -and (!$taskExample.state.requirements.Count -or !$taskExample.state.cases.Count)){throw '示例资料不完整'}
    }
}
Write-Output 'CaseFlow 示例已就绪：http://127.0.0.1:18180/'
Write-Output '示例是人工编写的体验资料；准备示例不会调用模型，也不会填写测试通过结果。'
