export function contentChanged(saved:any,edited:any,steps:string[]):boolean{
 if(!saved)return true
 return ['title','preconditions','data','expected'].some(k=>saved[k]!==edited[k])||JSON.stringify(saved.steps)!==JSON.stringify(steps)
}
export function needsGroundingAcknowledgement(row:any,changed=false):boolean{
 return !!row?.grounding&&(changed||!row.grounding.current||row.grounding.status!=='checked')
}
export function groundingLabel(row:any):string{
 if(!row?.grounding)return '尚未进行依据复核'
 if(!row.grounding.current)return '内容或规则已变化，需重新检查'
 if(row.grounding.status==='unavailable')return '自动复核未完成，需人工核对'
 return row.grounding.status==='checked'?'自动复核未发现疑点':'有待人工核对的内容'
}
