type Row={id:string,status?:string,document_id?:string,previous_id?:string|null,role?:string,decision?:string,selected_ids?:string[],[key:string]:any}
export type WorkflowState={documents:Row[],requirements:Row[],cases:(Row&{applied_to?:string})[],changes:Row[],clarifications?:Row[],workflow_mode?:string,test_points?:Row[]}
export type WorkflowTask={kind:string,status:string,input?:{document_ids?:string[]}}
export type NextStep={step:number,title:string,description:string,action:string,target:string}

export function currentDocuments(s:WorkflowState):Row[]{
 const superseded=new Set(s.documents.map(d=>d.previous_id).filter(Boolean))
 return s.documents.filter(d=>!superseded.has(d.id))
}
export function updatedDrafts(s:WorkflowState):Row[]{
 const accepted=s.changes.filter(c=>c.status==='accepted')
 const ids=new Set(accepted.filter(c=>c.decision==='replace').flatMap(c=>c.selected_ids??[]))
 return s.requirements.filter(r=>r.status==='draft'&&(ids.has(r.id)||accepted.some(c=>c.decision==='add'&&c.document_id===r.document_id&&c.proposed?.text===r.text)))
}
export function unreadDocuments(s:WorkflowState,tasks:WorkflowTask[]):Row[]{
 const read=new Set([...s.requirements.map(r=>r.document_id),...s.changes.map(c=>c.document_id),...tasks.filter(t=>t.kind==='extract'&&t.status==='completed').flatMap(t=>t.input?.document_ids??[])])
 return currentDocuments(s).filter(d=>d.role==='requirement'&&!read.has(d.id))
}
export function nextStep(s:WorkflowState,tasks:WorkflowTask[]):NextStep{
 const running=tasks.find(t=>['queued','running'].includes(t.status))
 if(running)return {step:['generate','review'].includes(running.kind)?2:0,title:'正在处理资料',description:'任务已保存，完成后会显示结果和下一步。',action:'',target:''}
 if(!s.documents.length)return {step:0,title:'添加你的需求',description:'粘贴需求文字，或导入已有文档。清单将根据这些内容整理。',action:'添加需求',target:'import'}
 if(s.workflow_mode==='standard'){
  if(s.changes.some(c=>c.status==='pending'))return {step:1,title:'核对规则变化',description:'比较新旧原文并明确接受变化，再准备新依据与复审测试点。',action:'查看规则变化',target:'changes'}
  if(unreadDocuments(s,tasks).length)return {step:0,title:'准备所选资料的依据',description:'明确目标和授权版本，准备规则、公共条件与原文位置。',action:'准备依据',target:'scope'}
  const points=s.test_points??[]
  if(points.some(p=>p.status==='needs_review'))return {step:1,title:'重新核对受影响的测试点',description:'先准备并绑定新依据，再人工复审相关测试点；旧用例与执行快照保留。',action:'打开测试点评审',target:'points'}
  if(s.cases.some(c=>!c.applied_to&&c.status==='needs_review'))return {step:2,title:'更新受影响的详细用例',description:'核对新依据，明确绑定新修订或应用有效建议，然后重新审核。',action:'查看待复审用例',target:'review'}
  if(s.cases.some(c=>!c.applied_to&&c.status==='draft'))return {step:2,title:'审核详细用例设计',description:'对照采用的测试点、公共条件与原文，逐例核对前置、数据、步骤和预期。',action:'打开待审核用例',target:'drafts'}
  if(s.cases.some(c=>!c.applied_to&&c.status==='approved'))return {step:3,title:'导出已保存的用例与追溯',description:'设计审核与业务执行分别记录，未实际执行时保留“未执行”。',action:'查看并导出用例',target:'export'}
  if(points.some(p=>p.status==='approved'&&!p.blocking_reasons?.length))return {step:2,title:'由已通过点展开详细用例',description:'明确选择当前测试点及修订，允许后台逐点展开，结果先保存为草稿。',action:'选择测试点并展开',target:'points'}
  if(points.length)return {step:1,title:'人工评审测试点',description:'核对测试意图、规则草稿、公共条件、原文和相关待澄清问题。',action:'打开测试点评审',target:'points'}
  return {step:1,title:'从准备依据提取测试点',description:'核对依据包与未解决问题，再提取独立测试点供人工评审。',action:'检查准备包',target:'scope'}
 }
 const updates=updatedDrafts(s)
 if(updates.length)return {step:1,title:`确认${updates.length}条已修改规则`,description:'修改已保存。核对新规则后，在这里确认，再继续处理其余变化。',action:'确认修改后的规则',target:'updated'}
 const pending=s.changes.filter(c=>c.status==='pending')
 if(pending.length)return {step:1,title:`核对${pending.length}项规则变化`,description:'逐项比较新旧内容，决定更新、添加或保留。原检查清单仍会保留。',action:'查看规则变化',target:'changes'}
 if(unreadDocuments(s,tasks).length)return {step:0,title:'资料已保存，接着读取需求',description:'确认要处理的资料并允许发送给模型，然后点击“读取需求”。',action:'选择资料并读取',target:'scope'}
 if(s.clarifications?.some(c=>c.status==='open'))return {step:1,title:'先确认不清楚的规则',description:'填写已确定的规则；不清楚的部分保留待确认，避免生成确定的结论。',action:'处理待确认问题',target:'clarify'}
 if(s.requirements.some(r=>r.status==='draft'))return {step:1,title:'核对整理出的规则',description:'对照原文确认规则。已确认的规则可以选择生成检查清单。',action:'核对规则',target:'requirements'}
 if(s.cases.some(c=>!c.applied_to&&c.status==='needs_review'))return {step:2,title:'更新受影响的检查项',description:'规则已变化。打开待复审条目，核对输入、步骤与预期，保存新修订。',action:'查看待复审条目',target:'review'}
 if(s.cases.some(c=>!c.applied_to&&c.status==='draft'))return {step:2,title:'审核生成的检查清单',description:'核对每项的规则依据、检查步骤和预期结果，再审核通过。',action:'打开待审核清单',target:'drafts'}
 if(s.cases.some(c=>!c.applied_to&&c.status==='approved'))return {step:3,title:'已有检查项可以交付了',description:'导出清单用于实际检查。执行后再记录结果；审核通过不代表测试通过。',action:'查看并导出清单',target:'export'}
 if(s.requirements.some(r=>r.status==='confirmed'))return {step:2,title:'生成你的检查清单',description:'选择已确认的规则，确认资料范围，然后生成检查步骤和预期。',action:'选择规则并生成',target:'generate'}
 return {step:0,title:'补充可用的需求资料',description:'当前没有可用于生成清单的规则。可以添加资料或核对已排除的内容。',action:'添加需求',target:'import'}
}
