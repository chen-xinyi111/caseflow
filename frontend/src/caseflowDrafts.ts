export type RequirementDraft={text:string,answer:string,baseText:string}
export function mergeRequirementDraft(draft:RequirementDraft|undefined,text:string):RequirementDraft {
  if(!draft)return {text,answer:'',baseText:text}
  if(draft.text===draft.baseText)return {...draft,text,baseText:text}
  return draft
}
export function isStaleDraft(draft:RequirementDraft,text:string):boolean{return draft.baseText!==text}
