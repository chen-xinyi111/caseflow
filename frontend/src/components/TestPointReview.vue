<script setup lang="ts">
import {computed,ref,watch} from 'vue'
import {stage4Request} from '../stage4Client'
const props=defineProps<{project:any}>()
const emit=defineEmits<{updated:[project:any],evidence:[value:any]}>()
const search=ref(''),status=ref(''),group=ref(''),type=ref(''),module=ref(''),priority=ref(''),attention=ref(false)
const selected=ref<Record<string,any>>({}),manual=ref<Record<string,{fields:string[],note:string}>>({}),reviewNote=ref(''),error=ref(''),notice=ref(''),failures=ref<any[]>([]),busy=ref(false),editor=ref<any>(null),answers=ref<Record<string,string>>({})
const s=computed(()=>props.project.state),rows=computed<any[]>(()=>s.value.test_points??[]),bundles=computed<any[]>(()=>s.value.basis_bundles??[])
const filtered=computed(()=>rows.value.filter(r=>(!search.value||(r.code+' '+r.intent+' '+r.tags.join(' ')).toLowerCase().includes(search.value.toLowerCase()))&&(!status.value||r.status===status.value)&&(!group.value||r.group===group.value)&&(!type.value||r.types.includes(type.value))&&(!module.value||r.module===module.value)&&(!priority.value||r.priority===priority.value)&&(!attention.value||r.needs_attention)))
const editorBundle=computed(()=>bundles.value.find(b=>b.id===editor.value?.basis_bundle_id))
const editorStale=computed(()=>!!editor.value?.id&&rows.value.some(r=>r.id===editor.value.id&&(r.revision!==editor.value.point_revision||r.dependency_fingerprint!==editor.value.dependency_fingerprint)))
const fields=[['intent','测试意图'],['expected_direction','预期方向'],['rule_applicability','规则及公共条件适用性'],['citations','当前引文']] as const
const labels:Record<string,string>={pending:'待评审',approved:'人工通过',rejected:'已驳回',needs_review:'待复审',checked:'模型复核未发现疑点',needs_confirmation:'模型建议人工核对',unavailable:'模型复核未完成'}
watch(()=>props.project.id,()=>{selected.value={};manual.value={};editor.value=null;error.value='';notice.value=''})
function choose(row:any,checked:boolean){if(checked){selected.value[row.id]={id:row.id,point_revision:row.revision,dependency_fingerprint:row.dependency_fingerprint,evidence_ids:row.evidence_ids.slice()};manual.value[row.id]??={fields:[],note:''}}else delete selected.value[row.id]}
function selectEligible(){selected.value={};let skipped=0;for(const r of filtered.value){if(!r.blocking_reasons.length)choose(r,true);else skipped++};notice.value='已选择'+Object.keys(selected.value).length+'项；跳过'+skipped+'项，原因列在对应卡片。'}
async function review(decision:string,only?:any){busy.value=true;error.value='';failures.value=[];try{
 if(only)choose(only,true)
 const chosen=only?[selected.value[only.id]]:Object.values(selected.value)
 if(!chosen.length)throw Error('请选择明确的测试点集合')
 const items=chosen.map(r=>({...r,reviewed_fields:manual.value[r.id]?.fields??[],semantic_resolution:manual.value[r.id]?.note??''}))
 emit('updated',await stage4Request('/projects/'+props.project.id+'/test-point-reviews',{revision:props.project.revision,items,decision,note:reviewNote.value}));selected.value={};notice.value='人工评审已保存。规则草稿未被改为已确认；这不是业务测试执行结果。'
 }catch(e){error.value=e instanceof Error?e.message:String(e);failures.value=(e as any).items??[]}finally{busy.value=false}}
function edit(row?:any){
 const b=bundles.value.at(-1)
 editor.value=row?JSON.parse(JSON.stringify({...row,rule_ids:row.requirement_bindings.map((r:any)=>r.id),point_revision:row.revision,questionText:row.questions.join('\n'),tagText:row.tags.join('、')})):{intent:'',group:'未分组',types:['正常'],module:'未分组',priority:'P2',tags:[],rule_ids:[],basis_item_ids:[],evidence_ids:[],questions:[],basis_bundle_id:b?.id??'',questionText:'',tagText:''}
 error.value=''
}
function selectBundle(){editor.value.rule_ids=[];editor.value.basis_item_ids=[];editor.value.evidence_ids=[]}
function selectRequired(){const e=editor.value,b=editorBundle.value;if(!b)return;e.evidence_ids=Array.from(new Set([...e.evidence_ids,...e.rule_ids.flatMap((id:string)=>b.rule_evidence[id]??[]),...e.basis_item_ids.flatMap((id:string)=>b.items.find((i:any)=>i.id===id)?.evidence_ids??[])]))}
async function save(){busy.value=true;error.value='';try{
 const e=editor.value,values=Object.fromEntries(['intent','group','types','module','priority','rule_ids','basis_item_ids','evidence_ids','basis_bundle_id'].map(k=>[k,e[k]]))
 Object.assign(values,{revision:props.project.revision,tags:e.tagText.split(/[、,]/).map((t:string)=>t.trim()).filter(Boolean),questions:e.questionText.split('\n').filter((t:string)=>t.trim())})
 if(e.id)Object.assign(values,{point_revision:e.point_revision,dependency_fingerprint:e.dependency_fingerprint})
 emit('updated',await stage4Request('/projects/'+props.project.id+'/test-points'+(e.id?'/'+e.id:''),values));editor.value=null;notice.value='测试点已保存，人工批准状态需重新核对。'
 }catch(e){error.value=e instanceof Error?e.message:String(e)}finally{busy.value=false}}
function evidence(row:any,eid:string){const b=bundles.value.find(b=>b.id===row.basis_bundle_id),e=b?.evidence_catalog[eid];if(e)emit('evidence',{document_id:e.document_id,evidence_id:eid})}
async function answer(qid:string){busy.value=true;error.value='';try{emit('updated',await stage4Request('/projects/'+props.project.id+'/clarifications/'+qid,{revision:props.project.revision,answer:answers.value[qid]??''}));answers.value[qid]=''}catch(e){error.value=e instanceof Error?e.message:String(e)}finally{busy.value=false}}
</script>
<template>
<section class="card point-workspace" aria-label="测试点评审"><div class="heading"><h2>测试点评审</h2><button class="secondary" :disabled="!bundles.length||busy" @click="edit()">手工补充测试点</button></div>
 <p>位置校验、模型意见与人工批准分别记录。标准流程中的规则可保持草稿，批准的是具体测试点对当前依据的使用。</p>
 <p v-if="error" class="message error" role="alert">{{error}}</p><p v-if="notice" class="message success" role="status">{{notice}}</p><p v-for="f in failures" :key="f.id" class="error-text">{{rows.find(r=>r.id===f.id)?.code??f.id}}：{{f.reason}}</p>
 <div class="point-filters"><label>搜索<input v-model="search" placeholder="编号、意图或标签"></label><label>评审状态<select v-model="status"><option value="">全部</option><option v-for="v in ['pending','approved','needs_review','rejected']" :key="v" :value="v">{{labels[v]}}</option></select></label><label>场景组<select v-model="group"><option value="">全部</option><option v-for="v in [...new Set(rows.map(r=>r.group))]" :key="v">{{v}}</option></select></label><label>类型<select v-model="type"><option value="">全部</option><option v-for="v in ['正常','异常','边界','权限','状态','界面','接口','性能','安全']" :key="v">{{v}}</option></select></label><label>模块<select v-model="module"><option value="">全部</option><option v-for="v in [...new Set(rows.map(r=>r.module))]" :key="v">{{v}}</option></select></label><label>优先级<select v-model="priority"><option value="">全部</option><option>P1</option><option>P2</option><option>P3</option></select></label></div>
 <label class="check"><input type="checkbox" v-model="attention">仅需关注（问题、待复审或模型意见需核对）</label>
 <div class="actions"><button class="secondary" @click="selectEligible">选择无阻塞项</button><strong>已选{{Object.keys(selected).length}}项</strong></div><label>本批人工评审说明<input v-model="reviewNote" maxlength="2000" placeholder="说明核对了哪些意图和依据"></label>
 <div class="actions"><button class="primary" :disabled="busy||!Object.keys(selected).length||!reviewNote.trim()" @click="review('approved')">通过明确选中的测试点</button><button class="secondary" :disabled="busy||!Object.keys(selected).length||!reviewNote.trim()" @click="review('rejected')">驳回所选</button><button class="secondary" disabled>展开详细用例（暂未开放）</button></div>
 <p v-if="!rows.length" class="empty">先在“资料与知识”准备依据，再提取测试点；也可在已有准备包中人工补充。</p><p v-else-if="!filtered.length" class="empty">没有符合筛选条件的测试点。</p>
 <article v-for="r in filtered" :key="r.id" class="point-card"><div class="heading"><label class="check"><input type="checkbox" :checked="!!selected[r.id]" @change="choose(r,($event.target as HTMLInputElement).checked)" :aria-label="'选择'+r.code"><strong>{{r.code}} · 修订{{r.revision}} · {{r.priority}}</strong></label><span class="badge" :class="r.status">{{labels[r.status]}}</span></div><h3>{{r.intent}}</h3><p>{{r.group}} · {{r.module}} · {{r.types.join(' / ')}} · {{r.tags.join('、')}}</p>
  <p>位置：{{r.location_status==='valid'?'有效':'未校验/缺失'}} · 模型：{{labels[r.semantic_review.status]??r.semantic_review.status}} · 人工：{{labels[r.status]}}</p><p class="muted">{{r.semantic_review.reason}}</p>
  <p>规则：{{r.requirement_bindings.map((b:any)=>(s.requirements.find((q:any)=>q.id===b.id)?.code??b.id)+'@'+b.revision).join('、')}}</p><p>公共条件：{{r.basis_item_ids.map((id:string)=>bundles.find(b=>b.id===r.basis_bundle_id)?.items.find((i:any)=>i.id===id)?.text).join('；')||'未选择'}}</p>
  <p v-for="reason in r.blocking_reasons" :key="reason" class="error-text">{{reason}}</p>
  <div class="actions"><button v-for="eid in r.evidence_ids" :key="eid" class="text-button" @click="evidence(r,eid)">阅读当前证据</button><button class="secondary" @click="edit(r)">编辑/重新绑定</button></div>
  <template v-if="selected[r.id]&&r.semantic_review.status!=='checked'"><p>此点须逐项人工核对，不能用全局勾选绕过未解决问题。</p><label v-for="[value,label] in fields" :key="value" class="check"><input type="checkbox" v-model="manual[r.id]!.fields" :value="value">已核对{{label}}</label><label>此点带依据的人工解决说明<textarea v-model="manual[r.id]!.note" rows="2" maxlength="2000"></textarea></label></template>
  <template v-for="q in s.clarifications.filter((q:any)=>q.status==='open'&&(q.point_ids??[]).includes(r.id))" :key="q.id"><p>{{q.question}}</p><label>这个问题的明确回答<input v-model="answers[q.id]"></label><button class="secondary" :disabled="busy||!answers[q.id]?.trim()" @click="answer(q.id)">保存这项回答</button></template>
 </article>
 <section v-if="editor" class="card point-editor" aria-label="编辑测试点"><h3>{{editor.id?'编辑 '+rows.find(r=>r.id===editor.id)?.code:'人工补充测试点'}}</h3><p v-if="editorStale" class="message error">此点或依据已在服务器变化，本地编辑仍保留。请先核对，再关闭并重新打开。</p>
  <label>准备包<select v-model="editor.basis_bundle_id" @change="selectBundle"><option v-for="b in bundles" :key="b.id" :value="b.id">{{b.target}} · {{b.status}}</option></select></label><label>测试意图<textarea v-model="editor.intent" rows="3" maxlength="1200"></textarea></label>
  <div class="point-filters"><label>场景组<input v-model="editor.group" maxlength="100"></label><label>模块<input v-model="editor.module" maxlength="100"></label><label>优先级<select v-model="editor.priority"><option>P1</option><option>P2</option><option>P3</option></select></label></div>
  <div class="actions"><label v-for="v in ['正常','异常','边界','权限','状态','界面','接口','性能','安全']" :key="v" class="check"><input type="checkbox" v-model="editor.types" :value="v">{{v}}</label></div><label>标签（顿号分隔）<input v-model="editor.tagText" maxlength="500"></label>
  <template v-if="editorBundle"><h4>目标规则</h4><label v-for="b in editorBundle.requirement_bindings" :key="b.id" class="check"><input type="checkbox" v-model="editor.rule_ids" :value="b.id">{{s.requirements.find((r:any)=>r.id===b.id)?.code}} · {{s.requirements.find((r:any)=>r.id===b.id)?.text}}</label><h4>适用公共条件/上下文</h4><label v-for="i in editorBundle.items" :key="i.id" class="check"><input type="checkbox" v-model="editor.basis_item_ids" :value="i.id">{{i.text}} · {{i.applicability}}</label><button class="secondary" @click="selectRequired">加入所选规则与条件的已知证据</button>
   <details><summary>明确选择精确引用位置</summary><label v-for="e in Object.values(editorBundle.evidence_catalog) as any[]" :key="e.id" class="check"><input type="checkbox" v-model="editor.evidence_ids" :value="e.id"><span>{{e.location_label}} · {{e.quote.slice(0,120)}}</span></label></details></template>
  <label>待澄清问题（每行一项）<textarea v-model="editor.questionText" rows="2"></textarea></label><div class="actions"><button class="primary" :disabled="busy||editorStale||!editor.intent.trim()||!editor.basis_bundle_id" @click="save">保存为待评审修订</button><button class="secondary" @click="editor=null">关闭编辑</button></div><p class="muted">保存不会自动批准；移除问题文字不会消除尚未解决的关联问题。</p>
 </section>
 <details v-if="s.point_reviews?.length"><summary>人工评审历史</summary><p v-for="r in [...s.point_reviews].reverse()" :key="r.id">{{rows.find(p=>p.id===r.point_id)?.code}}@{{r.point_revision}} · {{labels[r.decision]}} · {{r.note}} · {{r.semantic_resolution}} · {{r.at}}</p></details>
</section>
</template>
<style scoped>.point-filters{display:grid;grid-template-columns:repeat(auto-fit,minmax(140px,1fr));gap:12px}.point-card{border-top:1px solid #e0e7e2;padding:20px 0;overflow-wrap:anywhere}.point-workspace .check{margin:8px 0}.point-card h3{font-size:16px}.point-editor{margin-top:20px;background:#f8faf8}.point-workspace button{white-space:normal}@media(max-width:500px){.point-filters{grid-template-columns:minmax(0,1fr)}}</style>
