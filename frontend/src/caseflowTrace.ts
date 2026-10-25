const hash=/^[a-f0-9]{64}$/
const uuid=/^[0-9a-f-]{36}$/i
export function freezePointSelection(row:any){
 if(!uuid.test(row.id)||!Number.isInteger(row.revision)||row.revision<1||!hash.test(row.dependency_fingerprint))throw Error('测试点修订或依据尚未加载，请刷新后明确选择')
 return {id:row.id,point_revision:row.revision,dependency_fingerprint:row.dependency_fingerprint,evidence_ids:[...row.evidence_ids]}
}
export function expansionBindings(selection:any[]){
 if(!selection.length||new Set(selection.map(r=>r.id)).size!==selection.length)throw Error('请选择不重复的测试点集合')
 return selection.map(r=>({id:r.id,revision:r.point_revision,fingerprint:r.dependency_fingerprint}))
}
export function freezeCaseSelection(row:any){
 return {id:row.id,case_revision:row.revision,binding_fingerprint:row.binding_fingerprint,evidence_ids:[...(row.evidence_ids??[])]}
}
export function caseReviewValues(row:any,manual:{fields:string[],note:string},legacyAck=false):Record<string,unknown>{
 if(row.trace_mode!=='point')return {acknowledge_grounding:legacyAck}
 const fields=['preconditions','data','steps','expected']
 return {case_revision:row.revision,binding_fingerprint:row.binding_fingerprint,reviewed_fields:[...manual.fields],
  semantic_resolution:manual.note,evidence_ids:[...row.evidence_ids],
  acknowledge_grounding:manual.fields.length===4&&fields.every(f=>manual.fields.includes(f))&&!!manual.note.trim()}
}
