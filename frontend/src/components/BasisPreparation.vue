<script setup lang="ts">
import {computed,ref,watch} from 'vue'
import {currentDocuments} from '../caseflowWorkflow'
import {stage4Request,submitGroup} from '../stage4Client'
const props=defineProps<{project:any}>()
const emit=defineEmits<{updated:[project:any],started:[],points:[],evidence:[value:any]}>()
const target=ref(''),selected=ref<string[]>([]),history=ref(false),consent=ref(false),busy=ref(false),error=ref(''),notice=ref(''),bundleId=ref(''),ruleIds=ref<string[]>([]),answers=ref<Record<string,string>>({})
const s=computed(()=>props.project.state),bundles=computed<any[]>(()=>s.value.basis_bundles??[]),bundle=computed(()=>bundles.value.find(b=>b.id===bundleId.value))
const bindingRule=ref(''),bindingEvidence=ref<string[]>([]),bindingNote=ref('')
async function bindEvidence(){busy.value=true;error.value='';try{emit('updated',await stage4Request('/projects/'+props.project.id+'/basis-bundles/'+bundleId.value+'/rule-evidence',{revision:props.project.revision,rule_id:bindingRule.value,evidence_ids:bindingEvidence.value,note:bindingNote.value}));bindingRule.value='';bindingEvidence.value=[];bindingNote.value=''}catch(e){error.value=e instanceof Error?e.message:String(e)}finally{busy.value=false}}
const estimatedSegments=computed(()=>selected.value.reduce((n,id)=>{const d=s.value.documents.find((d:any)=>d.id===id);return n+(d?.pages??[]).reduce((k:number,page:string)=>k+Math.ceil(page.length/400),0)},0))
const bundleState=ref<any>(null)
let detailGeneration=0
watch(()=>[props.project.id,props.project.revision,bundleId.value],async()=>{const token=++detailGeneration;bundleState.value=null;if(!bundleId.value)return;try{const detail=await stage4Request('/projects/'+props.project.id+'/basis-bundles/'+bundleId.value);if(token===detailGeneration)bundleState.value=detail}catch(e){if(token===detailGeneration)error.value=e instanceof Error?e.message:String(e)}},{immediate:true})
const docs=computed(()=>history.value?s.value.documents:currentDocuments(s.value))
watch(()=>props.project.id,()=>{target.value='';selected.value=currentDocuments(s.value).map(d=>d.id);bundleId.value=bundles.value.at(-1)?.id??'';consent.value=false},{immediate:true})
watch(()=>props.project.revision,()=>{selected.value=selected.value.filter(id=>s.value.documents.some((d:any)=>d.id===id));consent.value=false})
watch(()=>bundles.value.map(b=>b.id).join(','),(value,old)=>{const latest=bundles.value.at(-1)?.id;if(!bundleId.value||(latest&&old!==undefined&&!old.split(',').includes(latest)))bundleId.value=latest??''})
watch(bundleId,()=>{ruleIds.value=bundle.value?.requirement_bindings.map((r:any)=>r.id)??[]},{immediate:true})
watch(()=>JSON.stringify([target.value,selected.value,ruleIds.value,bundleId.value]),()=>consent.value=false)
async function run(kind:string){busy.value=true;error.value='';notice.value='';try{
 const data=kind==='prepare_basis'?{revision:props.project.revision,kind,target:target.value,document_ids:selected.value,consent:consent.value}:{revision:props.project.revision,kind,basis_bundle_id:bundleId.value,rule_ids:ruleIds.value,consent:consent.value}
 const result=await submitGroup(props.project.id,data);notice.value=result.recovered?'已找回上次提交，请核对任务中心，本次未另建任务。':'任务组已保存，可在任务中心查看。';emit('started')
 }catch(e){error.value=e instanceof Error?e.message:String(e)}finally{busy.value=false}}
async function resolve(qid:string){busy.value=true;error.value='';try{emit('updated',await stage4Request('/projects/'+props.project.id+'/clarifications/'+qid,{revision:props.project.revision,answer:answers.value[qid]??''}));answers.value[qid]=''}catch(e){error.value=e instanceof Error?e.message:String(e)}finally{busy.value=false}}
function source(id:string){const e=bundle.value?.evidence_catalog[id];if(e)emit('evidence',{document_id:e.document_id,evidence_id:id})}
</script>
<template>
<section id="workflow-basis" class="card basis-preparation" aria-label="准备依据"><div class="heading"><h2>准备依据</h2><small>{{s.workflow_mode==='strict'?'严格流程：依赖规则需确认':'标准流程：规则草稿可分析，测试点需人工批准'}}</small></div>
 <p class="muted">全文逐段处理所选版本，再执行检索和原文回读。处理完成仍可能遗漏业务含义，请核对问题和依据。</p>
 <p v-if="error" class="message error" role="alert">{{error}}</p><p v-if="notice" class="message success" role="status">{{notice}}</p>
 <label>测试目标<textarea v-model="target" rows="3" maxlength="1000" placeholder="例如：检查预约时长边界和必要的登录条件"></textarea></label>
 <label class="check"><input type="checkbox" v-model="history">显示历史版本供明确选择</label>
 <label v-for="d in docs" :key="d.id" class="check"><input type="checkbox" v-model="selected" :value="d.id"><span>{{d.name}} · {{d.role==='context'?'公共条件/参考':'需求'}} · {{d.id.slice(0,8)}}</span></label>
 <label class="check consent"><input type="checkbox" v-model="consent">允许将当前目标及所选资料发送给已配置的模型进行本次处理。</label>
 <p class="muted">预计至少{{estimatedSegments}}个全文片段（按400字粗估，段落边界可能增加批次），每段1次设计、失败时最多1次修正；测试点每批最多3条并独立复核。关键词检索无模型请求，语义索引/检索另有请求。实际段数和保存步骤见任务中心；沿用现有预算。</p>
 <button class="primary" :disabled="busy||!target.trim()||!selected.length||!consent" @click="run('prepare_basis')">准备依据</button>
 <template v-if="bundles.length"><label>查看准备结果<select v-model="bundleId"><option v-for="b in bundles" :key="b.id" :value="b.id">{{b.target}} · {{b.status==='ready'?'选定范围处理完成':'部分完成'}} · {{b.created_at}}</option></select></label>
  <template v-if="bundle"><p><strong>{{bundle.target}}</strong> · {{bundle.status==='ready'?'已选范围处理完成':'存在未读或失败段，不可提取测试点'}}</p>
   <p v-for="reason in bundleState?.stale_reasons??[]" :key="reason" class="message error">{{reason}}</p><p class="message">完成表示程序处理范围已结束，不表示所有业务知识已找全。依据变化后需重新准备并绑定。</p>
   <details open><summary>目标规则（保留原确认状态）</summary><label v-for="r in bundle.requirement_bindings" :key="r.id" class="check"><input type="checkbox" v-model="ruleIds" :value="r.id"><span>{{s.requirements.find((x:any)=>x.id===r.id)?.code}} · {{s.requirements.find((x:any)=>x.id===r.id)?.text}} · {{s.requirements.find((x:any)=>x.id===r.id)?.status==='confirmed'?'已确认':'草稿/待核对'}}<button v-for="eid in bundle.rule_evidence[r.id]" :key="eid" class="text-button" @click.prevent="source(eid)">阅读依据</button></span></label><p v-if="!bundle.requirement_bindings.length">没有可用目标规则，请核对资料和待处理变更。</p></details>
   <details open><summary>公共条件与必要上下文</summary><article v-for="i in bundle.items" :key="i.id"><strong>{{i.kind==='public_condition'?'公共条件':'上下文'}}：{{i.text}}</strong><p>适用解释（待人工核对）：{{i.applicability}}</p><button v-for="eid in i.evidence_ids" :key="eid" class="text-button" @click="source(eid)">阅读依据</button></article><p v-if="!bundle.items.length" class="muted">本次未整理出公共条件，不代表资料中一定不存在。</p></details>
   <details open><summary>问题与未决变更 · {{bundle.issues.length}}</summary><article v-for="(i,n) in bundle.issues" :key="n"><p>{{i.text}}</p><template v-if="i.question_id"><p v-if="s.clarifications.find((q:any)=>q.id===i.question_id)?.status==='resolved'">已记录回答：{{s.clarifications.find((q:any)=>q.id===i.question_id)?.answer}}</p><template v-else><label>这个问题的已确定回答<input v-model="answers[i.question_id]" maxlength="3000"></label><button class="secondary" :disabled="busy||!answers[i.question_id]?.trim()" @click="resolve(i.question_id)">保存这项回答</button></template></template></article></details>
   <details v-if="bundle.issues.some((i:any)=>i.kind==='ambiguous_reference')"><summary>人工消除历史引用的定位歧义</summary><p class="muted">先阅读原文，再选择确切片段。保存会新建依据包，历史包保留；定位明确仍不代表业务语义正确。</p><label>待定位规则<select v-model="bindingRule" @change="bindingEvidence=[]"><option value="">请选择</option><option v-for="i in bundle.issues.filter((i:any)=>i.kind==='ambiguous_reference')" :key="i.requirement_ids[0]" :value="i.requirement_ids[0]">{{i.text}}</option></select></label><label class="check" v-for="e in (Object.values(bundle.evidence_catalog) as any[]).filter(e=>e.document_id===s.requirements.find((r:any)=>r.id===bindingRule)?.document_id)" :key="e.id"><input type="checkbox" v-model="bindingEvidence" :value="e.id"><span>{{e.location_label}} · {{e.quote}} <button class="text-button" @click.prevent="source(e.id)">阅读原文</button></span></label><label>选择理由<textarea v-model="bindingNote" rows="2" maxlength="1500"></textarea></label><button class="secondary" :disabled="busy||!bindingRule||!bindingEvidence.length||!bindingNote.trim()" @click="bindEvidence">保存精确定位并新建依据包</button></details>
   <details><summary>全文处理记录与检索回读</summary><article v-for="m in bundle.coverage_manifest" :key="m.document_id"><p>{{s.documents.find((d:any)=>d.id===m.document_id)?.name}} · {{m.completed_segments}} / {{m.total_segments}} 段完成</p><p v-for="w in m.warnings" :key="w" class="error-text">{{w}}</p><p v-for="seg in m.segments" :key="seg.key">{{seg.status}} · 字符{{seg.start}}—{{seg.end}} <span v-if="seg.reused">复用已完成响应</span> {{seg.error??''}}</p></article><p v-for="r in bundle.retrieval_log" :key="r.key">{{r.result?.query}} · {{r.status}} · {{r.result?.sources.length??0}} 个回读片段 <span v-if="r.result?.query_truncated">检索查询取前500字，目标原文完整保留</span> {{r.error??''}}</p></details>
   <div class="actions"><button class="primary" :disabled="busy||bundle.status!=='ready'||!ruleIds.length||!consent" @click="run('extract_points')">提取测试点供评审</button><button class="secondary" @click="emit('points')">打开测试点评审</button></div>
  </template>
 </template>
</section>
</template>
<style scoped>.basis-preparation .check{margin:9px 0}.basis-preparation article{border-top:1px solid #e0e7e2;padding:12px 0}.basis-preparation .text-button{margin-left:12px}.basis-preparation p{overflow-wrap:anywhere}</style>
