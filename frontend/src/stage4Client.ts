export async function stage4Request(path:string,body?:unknown):Promise<any>{
 const response=await fetch('/api/v1/caseflow'+path,body===undefined?{}:{method:'POST',headers:{'Content-Type':'application/json'},body:JSON.stringify(body)})
 const data=await response.json()
 if(!response.ok){const detail=data.detail;throw Object.assign(new Error(typeof detail==='string'?detail:detail?.message??'请求未通过校验'),{status:response.status,items:detail?.items??[]})}
 return data
}
export const groupPendingKey=(pid:string)=>'caseflow-group-pending:'+pid
export async function recoverGroup(pid:string){
 const requestId=localStorage.getItem(groupPendingKey(pid));if(!requestId)return null
 const rows=await stage4Request('/projects/'+pid+'/task-groups?request_id='+encodeURIComponent(requestId))
 if(rows.length){localStorage.removeItem(groupPendingKey(pid));return rows[0]}
 return null
}
export async function submitGroup(pid:string,body:Record<string,unknown>){
 if(localStorage.getItem(groupPendingKey(pid))){const found=await recoverGroup(pid);if(found)return {...found,recovered:true};throw Error('上次提交结果尚未找回，请刷新任务中心；不会另建请求重复处理')}
 const request_id=crypto.randomUUID();localStorage.setItem(groupPendingKey(pid),request_id)
 try{const result=await stage4Request('/projects/'+pid+'/task-groups',{...body,request_id});localStorage.removeItem(groupPendingKey(pid));return result}
 catch(error){if([400,403,404,409,422].includes((error as any).status))localStorage.removeItem(groupPendingKey(pid));throw error}
}
