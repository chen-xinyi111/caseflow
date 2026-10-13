<script setup lang="ts">
import {computed,onMounted,ref,watch} from 'vue'
import {request} from '../api/client'
import type {components} from '../api/schema'
type Run=components['schemas']['Run'];type Item=components['schemas']['Item']
const runs=ref<Run[]>([]),report=ref<Run|null>(null),items=ref<Item[]>([]),selected=ref<Item|null>(null)
const caseQuery=ref('')
const error=ref(''),loading=ref(false),kind=ref('investigation'),split=ref('test'),mode=ref(''),state=ref(''),source=ref<Record<string,unknown>|null>(null)
const names:Record<string,string>={keyword:'BM25',vector:'向量',hybrid:'混合',rerank:'混合＋重排',single:'单 Agent',multi:'多 Agent',dev:'开发集',test:'测试集',control:'共享控制',pending:'未运行',running:'处理中',completed:'已完成',failed:'失败',blocked:'受阻',partial:'部分完成'}
const display=(v:string)=>names[v]??v
const pct=(v:unknown)=>typeof v==='number'?(v*100).toFixed(1)+'%':'尚无结果'
const rows=computed(()=>items.value.filter(r=>r.kind===kind.value&&(!caseQuery.value.trim()||r.case_id.toLowerCase().includes(caseQuery.value.trim().toLowerCase()))&&(split.value==='control'?r.input.target==='unconnected':r.split===split.value&&(r.kind!=='investigation'||r.input.target!=='unconnected'))&&(!mode.value||r.mode===mode.value)&&(!state.value||r.status===state.value)))
const summary=computed(()=>report.value?.summary?.filter(r=>r.split===split.value)??[])
const progress=computed(()=>{const r=report.value?.summary??[];return {planned:r.reduce((n:number,x:any)=>n+(x.planned??0),0),completed:r.reduce((n:number,x:any)=>n+(x.completed??0),0),failed:r.reduce((n:number,x:any)=>n+(x.failed??0),0),blocked:r.reduce((n:number,x:any)=>n+(x.blocked??0),0)}})
async function load(id?:string){loading.value=true;error.value=''
 try{runs.value=await request<Run[]>('/evaluations');id=id??localStorage.getItem('opslens-evaluation-run')??runs.value[0]?.id
  if(id){if(report.value?.id!==id){selected.value=null;source.value=null}report.value=await request<Run>('/evaluations/'+id);items.value=await request<Item[]>('/evaluations/'+id+'/items');localStorage.setItem('opslens-evaluation-run',id)
   const previous=localStorage.getItem('opslens-evaluation-item');if(previous&&items.value.some(r=>r.id===previous))await open(previous)}
 }catch(e){error.value=e instanceof Error?e.message:'报告暂不可达，请稍后刷新'}finally{loading.value=false}}
async function open(id:string){if(!report.value)return;source.value=null
 try{selected.value=await request<Item>('/evaluations/'+report.value.id+'/items/'+id);kind.value=selected.value.kind;split.value=selected.value.kind==='investigation'&&selected.value.input.target==='unconnected'?'control':selected.value.split;localStorage.setItem('opslens-evaluation-item',id)}catch(e){error.value=String(e)}}
async function citation(url:string){if(!url.startsWith('/api/v1/evidence/')&&!url.startsWith('/api/v1/kb/citations/')){error.value='引用不属于本机来源';return}
 try{source.value=await request<Record<string,unknown>>(url.slice('/api/v1'.length))}catch(e){error.value=String(e)}}
const answer=computed(()=>selected.value?.kind==='investigation'?(selected.value.result as any)?.report:null)
const hits=computed(()=>(selected.value?.result as any)?.hits??[])
watch(kind,value=>{mode.value='';if(selected.value&&selected.value.kind!==value){selected.value=null;source.value=null}})
watch(split,value=>{if(selected.value&&(selected.value.input.target==='unconnected'?'control':selected.value.split)!==value){selected.value=null;source.value=null}});onMounted(()=>load())
</script>
<template>
<section class="evaluation">
 <div class="alert" role="alert" v-if="error">{{error}}</div>
 <div class="heading"><h2>冻结数据，对照真实结果</h2><button class="secondary" :disabled="loading" @click="load(report?.id)">{{loading?'正在读取…':'刷新报告'}}</button></div>
 <p class="muted">完整库为120条检索问题、60条调查案例；本轮正式调查按用户确认固定为24条，开发与测试按来源分组。评测由本地程序执行，页面只读取已保存结果。</p>
 <label>已保存的评测 <select :value="report?.id" @change="load(($event.target as HTMLSelectElement).value)"><option v-for="run in runs" :key="run.id" :value="run.id">{{run.title}} · {{display(run.status)}}</option></select></label>
 <p v-if="!report&&!loading">暂无评测运行。</p>
 <template v-if="report">
  <div class="summary-grid"><div class="summary"><span>计划对照条目</span><strong>{{progress.planned}}</strong><p>480 条检索 ＋ {{report?.manifest.investigation_sample_count??60}} × 2 条调查</p></div><div class="summary"><span>已完成</span><strong>{{progress.completed}}</strong><p>仅表示已有结果；不等于结论通过</p></div><div class="summary"><span>失败／受阻</span><strong>{{progress.failed}} / {{progress.blocked}}</strong><p>保留错误、用量和已有原文</p></div></div>
  <div class="resume-notice">生成模型：{{report.manifest.model}} · 效果结果仅使用真实模型，未运行项目没有模型结果。历史 qwen 开发记录不混入本次对照。</div>
  <div class="panel"><h3>比较范围</h3><p>{{report.manifest.analysis_scope}}</p><p class="inline-error">{{report.manifest.input_split_audit}}</p><p v-for="limit in report.manifest.limits" :key="String(limit)">{{limit}}</p><p>引文存在性与根因规则匹配均可复核；回答是否在语义上由证据充分支持，另保存Codex依据真实故障和原文逐条复核记录；未经复核不声明语义支持。</p></div>
  <div class="eval-filters"><label>集合<select v-model="split"><option value="dev">开发集</option><option value="test">测试集</option><option value="control">共享控制</option></select></label></div>
  <div class="panel eval-table"><h3>各方式结果与分母</h3><table><thead><tr><th>方式</th><th>完成／计划</th><th>失败／受阻</th><th>规则通过／计划</th><th>已完成规则通过率</th><th>MRR@6</th><th>中位耗时</th><th>实际 Token</th></tr></thead><tbody><tr v-for="(s,i) in summary" :key="i"><td>{{display(String(s.mode))}}</td><td>{{s.completed}} / {{s.planned}}</td><td>{{s.failed}} / {{s.blocked}}</td><td>{{s.correct}} / {{s.planned}}</td><td>{{Number(s.completed)>0?pct(s.completed_success_rate):'尚未运行'}}</td><td>{{Number(s.completed)>0&&typeof s.mrr_at_6==='number'?s.mrr_at_6.toFixed(3):'—'}}</td><td>{{typeof s.median_ms==='number'?(s.median_ms/1000).toFixed(2)+' 秒':'—'}}</td><td>{{s.actual_tokens}}<small v-if="Number(s.token_rows)<Number(s.completed)">（仅已知用量）</small></td></tr></tbody></table><p class="muted">正确／计划反映完成覆盖情况，不等于未运行模型的准确率。已完成正确率分母只含已完成条目；失败、受阻另列。</p></div>
  <div class="panel eval-table"><h3>依据真实故障与原文复核</h3><table><thead><tr><th>方式</th><th>已复核</th><th>根因／拒答符合预期</th><th>引用支持声明</th></tr></thead><tbody><tr v-for="(s,i) in summary.filter((r:any)=>r.kind==='investigation')" :key="i"><td>{{display(String(s.mode))}}</td><td>{{s.reviewed}} / {{s.completed}}</td><td>{{s.review_root_correct}} / {{s.reviewed}}</td><td>{{s.quote_supported}} / {{s.quote_reviewed}} · {{pct(s.review_support_rate)}}</td></tr></tbody></table><p>Codex逐条对照真实快照、任务和所列完整原文；不使用被测模型自评，也不等同外部人工专家复核。原关键词规则评分保持，漏掉同义表达的情况另行说明。</p></div>
  <div class="panel"><h3>单／多 Agent 配对</h3><p v-for="(p,i) in (report.paired??[]).filter((x:any)=>x.split===split)" :key="i">完成 {{p.completed_pairs}} / {{p.planned_pairs}} 对；仅单 Agent 通过 {{p.single_only}}，仅多 Agent 通过 {{p.multi_only}}，两者通过 {{p.both_correct}}，两者未通过 {{p.both_incorrect}}。{{p.note}}</p></div>
  <div class="heading"><h2>逐条查看回答与判定</h2><span>{{rows.length}} 条</span></div>
  <label for="eval-case-query">查找案例编号（例如 wrong_version-03）</label><input id="eval-case-query" v-model="caseQuery" placeholder="输入案例编号片段，快速找到记录"><div class="eval-filters"><label>评测类型<select v-model="kind"><option value="investigation">故障调查</option><option value="retrieval">知识检索</option></select></label><label>方式<select v-model="mode"><option value="">全部</option><option v-for="m in kind==='investigation'?['single','multi']:['keyword','vector','hybrid','rerank']" :key="m" :value="m">{{display(m)}}</option></select></label><label>状态<select v-model="state"><option value="">全部</option><option v-for="s in ['completed','failed','blocked','pending','running']" :key="s" :value="s">{{display(s)}}</option></select></label></div>
  <div class="eval-list"><button v-for="row in rows" :key="row.id" :class="{active:selected?.id===row.id}" @click="open(row.id)"><strong>{{row.case_id}} · {{display(row.mode)}}</strong><span>{{display(row.status)}} · {{row.score?.rule_correct===true||row.score?.hit_at_6===1?'规则通过':row.status==='completed'?'规则未通过':'等待结果'}}</span></button></div>
  <article class="panel" v-if="selected"><h3>{{selected.case_id}} · {{display(selected.mode)}} · {{display(selected.status)}}</h3><p><b>输入：</b>{{selected.input.question??selected.input.query}}</p><p v-if="selected.error" class="inline-error">{{selected.error}}</p>
   <template v-if="answer"><p><b>回答：</b>{{answer.summary}}</p><p><b>原因：</b>{{answer.root_cause??'信息不足，未确认原因'}}</p><ul><li v-for="(f,i) in answer.findings" :key="i">{{f.statement}} <template v-for="q in f.references" :key="q.id+q.quote"><button class="text-button" @click="citation(String((selected!.references??[]).find(r=>r.id===q.id)?.url??''))">{{q.id}} · {{q.quote}}</button></template></li></ul><p v-if="answer.missing_information?.length">需要补充：{{answer.missing_information.join('；')}}</p></template>
   <div v-for="hit in hits" :key="hit.chunk.id"><button class="text-button" @click="citation(hit.citation_url)">{{hit.document.title}} · {{hit.document.doc_version}}</button><p>{{hit.chunk.content}}</p></div>
   <p v-if="selected.score"><b>判定：</b>{{selected.score.reason??('目标来源排名：'+(selected.score.expected_rank??'未召回'))}}</p><p v-if="selected.kind==='investigation'&&selected.status==='completed'">引文原文匹配：{{selected.score?.quote_valid}} / {{selected.score?.quote_total}}；语义支持：{{selected.review?.reason?'已保存Codex事实复核（见下方）':'待事实复核'}}。</p>
   <p v-if="selected.review?.reason"><b>原文与真实故障复核：</b>{{selected.review.reason}}（{{selected.review.reviewer}}）</p><details><summary>查看预声明评分依据、版本与原始步骤</summary><h4>评分侧预期（未发送给模型）</h4><pre>{{JSON.stringify(selected.expected,null,2)}}</pre><h4>实际用量</h4><pre>{{JSON.stringify(selected.metrics,null,2)}}</pre><h4>执行步骤</h4><pre>{{JSON.stringify(selected.steps,null,2)}}</pre></details>
  </article>
  <div class="panel" v-if="source"><div class="heading"><h3>引用原文</h3><button class="secondary" @click="source=null">关闭原文</button></div><pre>{{source.content??source.page_text??JSON.stringify(source,null,2)}}</pre></div>
  <div class="panel"><h3>失败分析</h3><p v-if="!Object.keys(report.failure_counts??{}).length">尚无已保存的失败或规则未通过记录。</p><p v-for="(n,reason) in report.failure_counts" :key="reason">{{reason}}：{{n}} 条</p></div>
  <div class="panel"><h3>已有受控处置验证</h3><p>{{report.historical_recovery?.label}}：真实业务验证通过 {{report.historical_recovery?.completed}} / {{report.historical_recovery?.total}}。该数据不计入本次冻结调查对照，评测不会自行批准或执行动作。</p></div>
 </template>
</section>
</template>
<style scoped>
.evaluation{display:grid;gap:20px}.evaluation .panel{margin:0}.evaluation select{max-width:100%;padding:9px;border:1px solid #d6dee8;border-radius:8px;background:white}.eval-filters{display:flex;gap:16px;flex-wrap:wrap}.eval-filters label{display:grid;gap:6px}.eval-table{overflow:auto}.eval-table table{width:100%;border-collapse:collapse;font-size:13px;white-space:nowrap}.eval-table td,.eval-table th{text-align:left;padding:12px;border-bottom:1px solid #e2e8f0}.eval-list{max-height:300px;overflow:auto;display:grid;gap:7px}.eval-list button{text-align:left;display:flex;justify-content:space-between;gap:12px;background:white;border:1px solid #dbe4ed;border-radius:9px;padding:12px}.eval-list button.active{border-color:#268579;background:#edf9f6}.evaluation pre{white-space:pre-wrap;overflow-wrap:anywhere;max-height:450px;overflow:auto;background:#f5f8fb;padding:15px;border-radius:8px}.evaluation .text-button{white-space:normal;text-align:left}.evaluation small{display:block;color:#617589}@media(max-width:700px){.eval-list button{display:grid}.summary-grid{grid-template-columns:1fr}}
</style>
