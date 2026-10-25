export type Segment={unit_id:string,start:number,end:number}
export type Unit={id:string,kind:string,page:number,page_number:number|null,text:string,label:string,start:number,end:number}
export const segmentSeparator='\n[…片段分隔…]\n'
const uuid='[0-9a-fA-F]{8}-[0-9a-fA-F]{4}-[0-9a-fA-F]{4}-[0-9a-fA-F]{4}-[0-9a-fA-F]{12}'
export type SourceRoute={projectId:string,documentId?:string,evidenceId?:string,tab?:string}
export function parseSourceRoute(hash:string):SourceRoute|null{
 if(!hash||hash==='#')return null
 const m=hash.match(new RegExp('^#/projects/('+uuid+')/(documents/('+uuid+')|knowledge|points|cases|changes)(?:\\?([^#]*))?$'))
 if(!m)throw new Error('定位链接无效，未切换到其他项目')
 const q=new URLSearchParams(m[4]??'');const evidenceId=q.get('evidence')??undefined
 if(evidenceId&&(!m[3]||!new RegExp('^'+uuid+'$').test(evidenceId)))throw new Error('引用链接无效')
 return {projectId:m[1]!.toLowerCase(),documentId:m[3]?.toLowerCase(),evidenceId:evidenceId?.toLowerCase(),tab:m[3]?'需求':m[2]==='points'?'测试点':m[2]==='cases'?'用例':m[2]==='changes'?'变更':'需求'}
}
export function highlightParts(text:string,ranges:Segment[],unitId:string){
 const chars=Array.from(text),parts:{text:string,marked:boolean}[]=[];let offset=0
 for(const r of ranges.filter(r=>r.unit_id===unitId).sort((a,b)=>a.start-b.start)){
  if(!Number.isInteger(r.start)||!Number.isInteger(r.end)||r.start<offset||r.end<=r.start||r.end>chars.length)throw new Error('引用区间无效')
  if(r.start>offset)parts.push({text:chars.slice(offset,r.start).join(''),marked:false})
  parts.push({text:chars.slice(r.start,r.end).join(''),marked:true});offset=r.end
 }
 if(offset<chars.length)parts.push({text:chars.slice(offset).join(''),marked:false})
 return parts
}
export function segmentQuote(units:Unit[],segments:Segment[]){
 return segments.map(s=>{const u=units.find(u=>u.id===s.unit_id);if(!u)throw new Error('原文段落不存在');return Array.from(u.text).slice(s.start,s.end).join('')}).join(segmentSeparator)
}
