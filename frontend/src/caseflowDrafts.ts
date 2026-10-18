export type RequirementDraft={text:string,answer:string,baseText:string}
export function mergeRequirementDraft(draft:RequirementDraft|undefined,text:string):RequirementDraft {
  if(!draft)return {text,answer:'',baseText:text}
  if(draft.text===draft.baseText)return {...draft,text,baseText:text}
  return draft
}
export function isStaleDraft(draft:RequirementDraft,text:string):boolean{return draft.baseText!==text}

export function syncDocumentSelection(documents:{id:string,previous_id?:string|null}[],selected:string[],previousIds?:string[]):string[]{
 const ids=new Set(documents.map(d=>d.id)),superseded=new Set(documents.map(d=>d.previous_id).filter(Boolean))
 const current=documents.filter(d=>!superseded.has(d.id))
 if(previousIds===undefined)return current.map(d=>d.id)
 const added=current.filter(d=>!previousIds.includes(d.id))
 if(added.length)return [...new Set([...selected.filter(id=>ids.has(id)&&!superseded.has(id)),...added.map(d=>d.id)])]
 return selected.filter(id=>ids.has(id))
}
