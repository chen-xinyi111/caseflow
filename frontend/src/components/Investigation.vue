<script setup lang="ts">
import {useModal} from "../api/modal"
import {ref,computed,onMounted,onUnmounted} from 'vue'
import {durableRequest,resumePending,eventUrl,saveCursor} from '../api/durable'
import {request} from '../api/client'
import ExternalEvidence from './ExternalEvidence.vue'
import type {components} from '../api/schema'
const emit=defineEmits<{navigate:[section:string]}>()
const showHistorical=ref(false)
type Task=components['schemas']['Investigation']
type Ref=components['schemas']['Reference']
const target=ref('fault'),question=ref('这个实例最近的任务为什么一直等待处理？请结合实际运行状态和手册说明原因。'),jobId=ref('')
const mode=ref('verified'),snapshotId=ref<string|null>(null),snapshotTime=ref('')
const collaboration=computed(()=>task.value?.collaboration)
const verification=computed(()=>task.value?.verification)
const verificationStatuses:Record<string,string>={waiting:'等待核验',running:'正在核验',confirmed:'核验通过',needs_information:'需补充信息',not_required:'本次不确认故障原因',failed:'核验未完成',blocked:'额度阻断',interrupted:'核验中断'}
const decisions:Record<string,string>={confirmed:'通过',rejected:'候选原因被否定',uncertain:'发现问题，暂不确认'}
const roles:Record<string,string>={runtime:'运行调查 Agent',knowledge:'知识调查 Agent'}
const taskStatuses:Record<string,string>={pending:'等待依赖',scheduled:'准备开始',running:'正在调查',completed:'已完成',failed:'未完成',skipped:'已跳过'}
const task=ref<Task|null>(null),history=ref<Task[]>([]),busy=ref(false),error=ref(''),progress=ref('')
const dailyHistory=computed(()=>showHistorical.value?history.value:history.value.filter(t=>(t.input.target==='external'||t.input.record_origin==='daily')&&!String(t.metrics.model??'').startsWith('controlled-')))
const model=ref<{available:boolean,reason:string}|null>(null)
const opened=ref<{title:string,text:string}|null>(null)
useModal(opened)
let stream:EventSource|null=null,timer:ReturnType<typeof setTimeout>|undefined,disposed=false
const when=(v:string)=>new Date(v).toLocaleString('zh-CN',{hour12:false})
const statuses:Record<string,string>={queued:'等待调查',running:'正在调查',completed:'报告已完成',failed:'调查失败',blocked:'调查受阻'}
const fail=(e:unknown)=>{error.value=e instanceof Error?e.message:String(e)}
async function refresh(){try{[history.value,model.value]=await Promise.all([request<Task[]>('/investigations'),request<{available:boolean,reason:string}>('/investigations/status')])}catch(e){fail(e)}}
async function poll(){
 if(!task.value||disposed)return
 try{task.value=await request<Task>('/investigations/'+task.value.id);error.value=task.value.error??''
  if(['completed','failed','blocked'].includes(task.value.status)){busy.value=false;stream?.close();if(timer)clearTimeout(timer);if(task.value.error)error.value=task.value.error;progress.value=task.value.status==='completed'?'调查已完成，未执行恢复':statuses[task.value.status]??task.value.status;await refresh();return}
  timer=setTimeout(poll,1000)
 }catch(e){fail(e);progress.value='正在重新连接；任务与保存步骤保留';timer=setTimeout(poll,1500)}
}
async function follow(value:Task){
 stream?.close();if(timer)clearTimeout(timer);task.value=value;error.value=value.error??'';progress.value=statuses[value.status]??value.status;busy.value=['queued','running'].includes(value.status);localStorage.setItem('opslens-investigation-last',value.id)
 if(busy.value){stream=new EventSource(eventUrl('/investigations/'+value.id+'/events'));stream.onmessage=(event)=>{saveCursor('/investigations/'+value.id+'/events',event);try{const item=JSON.parse(event.data);if(item.payload?.message)progress.value=item.payload.message;if(item.kind==='finished'){stream?.close();void poll()}}catch(e){fail(e)}};stream.onerror=()=>{progress.value='进度连接正在重连；调查记录已保存'};await poll()}
}
async function submit(){
 error.value='';progress.value='调查已提交'
 try{busy.value=true;await follow(await durableRequest<Task>('/investigations',{target:target.value,question:question.value,job_id:jobId.value.trim()||null,mode:mode.value,snapshot_id:mode.value==='verified'?snapshotId.value:null}))}catch(e){fail(e);busy.value=false}
}
async function submitExternal(payload:Record<string,unknown>){error.value='';try{busy.value=true;await follow(await durableRequest<Task>('/investigations',payload))}catch(e){fail(e);busy.value=false}}
function recover(){if(!task.value)return;localStorage.setItem('opslens-recovery-investigation',task.value.id);emit('navigate','recovery')}
async function workerDemo(){
 target.value='fault';mode.value='verified';snapshotId.value=null;question.value='这个实例的指定任务为什么一直等待处理？请结合当前运行证据与手册调查。'
 try{const cases=await request<{id:string,sample_job_id:string|null}[]>('/fault-cases');jobId.value=cases.find(c=>c.id==='worker_stopped')?.sample_job_id??''}catch(e){fail(e)}
}
function missingDemo(){mode.value='verified';snapshotId.value=null;target.value='unconnected';jobId.value='';question.value='这个未连接实例的任务一直没有完成。请调查原因，并说明还需要哪些信息。'}
function manualDemo(){mode.value='verified';snapshotId.value=null;target.value='healthy';jobId.value='';question.value='只查手册：文本统计任务的字符数和词数采用什么规则？无需检查当前实例，也无需判断当前故障原因。'}
async function conflictDemo(){
 error.value=''
 try{const example=await request<components['schemas']['ConflictExample']>('/investigations/examples/conflict');if(!example.available)throw new Error(example.reason)
  mode.value='verified';target.value=example.target;jobId.value=example.job_id??'';question.value=example.question??'';snapshotId.value=example.snapshot_id??null;snapshotTime.value=example.collected_at??''
 }catch(e){fail(e)}
}
async function openRef(ref:Ref){
 try{const value=await request<Record<string,unknown>>(ref.url.replace('/api/v1',''));opened.value={title:ref.source,text:ref.kind==='runtime'?String(value.content):String(value.page_text)}}catch(e){fail(e)}
}
const lookup=(id:string)=>task.value?.references.find(r=>r.id===id)
function citation(id:string){const value=lookup(id);if(value)void openRef(value)}
async function restoreLast(){
 if(disposed)return
 await refresh()
 try{const resumed=await resumePending<Task>('/investigations');const id=resumed?.id??localStorage.getItem('opslens-investigation-last');if(id)await follow(resumed??await request<Task>('/investigations/'+id))}
 catch(e){fail(e);progress.value='正在重新连接；原任务保留';timer=setTimeout(restoreLast,1500)}
}
onMounted(restoreLast)
onUnmounted(()=>{disposed=true;stream?.close();if(timer)clearTimeout(timer)})
</script>
<template>
<div class="resume-notice" v-if="task && Number(task.recovery?.recovery_count)>0">已从保存位置继续 · 中断恢复 {{task.recovery?.recovery_count}} 次 · 报告与引用已保留</div>
<div class="investigation">
 <div v-if="error" class="alert" role="alert">{{error}}</div>
 <ExternalEvidence :busy="busy" :available="model?.available" @submit="submitExternal" />
 <details class="panel"><summary>实验环境调查与示例</summary>
  <div class="heading"><h2>提交只读故障调查</h2><span class="pill" :class="model?.available?'good':'amber'">{{model?.available?'调查模型已就绪':'模型额度待核验'}}</span></div>
  <div class="inv-demos"><button class="secondary" :disabled="busy" @click="conflictDemo">准备证据冲突示例</button><button class="secondary" :disabled="busy" @click="workerDemo">准备 worker 停止示例</button><button class="secondary" :disabled="busy" @click="manualDemo">准备仅查手册示例</button><button class="secondary" :disabled="busy" @click="missingDemo">准备信息不足示例</button></div>
  <label for="inv-mode">调查方式</label><select id="inv-mode" v-model="mode" :disabled="busy"><option value="verified">多 Agent 调查与核验</option><option value="multi">多 Agent 协作</option><option value="single">单 Agent 调查</option></select>
  <div class="kb-controls"><div><label for="inv-target">调查实例</label><select id="inv-target" v-model="target" :disabled="busy"><option value="fault">故障实验实例</option><option value="healthy">正常实验实例</option><option value="unconnected">未连接实例（信息不足演示）</option></select></div><div><label for="inv-job">业务任务编号（可选）</label><input id="inv-job" v-model="jobId" :disabled="busy" placeholder="可选，填写需要查询的任务编号"></div></div>
  <label for="inv-question">调查问题</label><textarea id="inv-question" v-model="question" maxlength="1000" rows="3" :disabled="busy"></textarea>
  <p v-if="snapshotId&&mode==='verified'" class="inv-snapshot">已选真实历史快照：{{when(snapshotTime)}}，只代表过去的状态。<button class="text-button" :disabled="busy" @click="snapshotId=null">清除历史参考</button></p>
  <p class="hint">{{mode==='verified'?'先调查，再独立核验原因和证据；有冲突时只读补查，仍无法确定就列出缺少资料。':mode==='multi'?'协调 Agent 按问题分工，独立的运行与知识调查可并行，再合并为带引用的报告。':'单个 Agent 读取运行状态、日志和任务，并检索手册。'}}相关文本会发送给百炼，仅使用已核验免费额度。</p>
  <button class="primary" :disabled="busy||!question.trim()" @click="submit">{{busy?'正在调查…':'开始调查 →'}}</button>
  <p class="muted" role="status">{{progress}}</p><p class="inline-error" v-if="model&&!model.available">{{model.reason}}</p>
 </details>
 <article v-if="task" class="panel inv-report">
  <div class="heading"><h2>调查报告</h2><span class="pill" :class="task.report?.assessment==='supported'?'good':'amber'">{{task.report?(task.report.assessment==='supported'?'已有依据支持':'信息不足'):statuses[task.status]??task.status}}</span></div>
  <p v-if="task.input.target==='external'" class="muted">外部项目 · 仅提供建议 · 结论限定于确认快照采集时刻</p>
  <p v-else class="pill amber">历史／实验记录 · {{String(task.metrics.model??'').startsWith('controlled-')?'示例响应':'来源详情见调查过程'}}</p>
  <details><summary>记录信息</summary><p class="task-id">任务 {{task.id}} · {{when(task.created_at)}} · {{task.input.target}}</p></details>
  <template v-if="task.report"><h3>{{task.report.summary}}</h3><div class="inv-cause" v-if="task.report.root_cause"><strong>有依据的原因</strong><p>{{task.report.root_cause}}</p></div>
   <h3 v-if="task.report.findings?.length">事实与依据</h3><div v-for="(finding,i) in task.report.findings" :key="i" class="inv-finding"><p>{{finding.statement}}</p><div v-for="(ref,j) in finding.references" :key="j"><button class="text-button" @click="citation(ref.id)">{{ref.id}} · {{lookup(ref.id)?.kind==='runtime'?'运行证据':'知识引用'}} · 查看原文 ↗</button><blockquote>{{ref.quote}}</blockquote></div></div>
   <div v-if="task.report.missing_information?.length" class="inv-missing"><h3>还需要补充</h3><ul><li v-for="(item,i) in task.report.missing_information" :key="i">{{item}}</li></ul></div>
   <h3 v-if="task.report.next_steps?.length">下一步建议</h3><ul><li v-for="(item,i) in task.report.next_steps" :key="i">{{item}}</li></ul>
   <button v-if="task.input.target==='fault'&&task.report.assessment==='supported'&&verification?.status==='confirmed'" class="primary" :disabled="busy" @click="recover">审阅恢复方案 →</button>
   <p v-else-if="task.input.target==='external'" class="hint">请根据建议人工处理。如需新证据，重新采集并确认资料后调查。</p>
   <details v-if="task.metrics.model_calls"><summary>调用详情</summary><p class="muted">{{['multi','verified'].includes(String(task.input.mode))?'多 Agent · '+task.metrics.agent_count+' 个参与者':'单 Agent'}} · {{task.metrics.model_calls}} {{String(task.metrics.model).startsWith('controlled-')?'次可控测试响应（无真实模型调用）':'次模型调用'}} · {{task.metrics.tool_calls}} 次只读工具调用<span v-if="task.input.mode==='verified'"> · {{task.metrics.replan_count??0}} 次重新规划</span></p></details>
  </template><p class="muted" v-else>{{["queued","running"].includes(task.status)?"调查步骤与证据正在保存。":"调查已停止；已有核验记录和原文仍可查看，尚未确认原因。"}}</p>
  <details class="investigation-process"><summary>调查与核验过程</summary>
  <section v-if="['multi','verified'].includes(String(task.input.mode))&&collaboration?.tasks" class="inv-collaboration" aria-label="Agent 协作过程">
   <h3>Agent 分工与协作</h3><p><strong>协调 Agent：</strong>{{collaboration.reason}}</p>
   <p class="hint">无前置任务的调查可以并行，最多同时进行两项；综合报告等待调查结束后生成。</p>
   <div class="inv-subtasks"><article v-for="sub in collaboration.tasks" :key="sub.id" class="inv-subtask">
    <div class="heading"><strong>{{roles[sub.role]??sub.role}} · {{sub.id}}</strong><span class="pill" :class="sub.status==='completed'?'good':'amber'">{{taskStatuses[sub.status]??sub.status}}</span></div>
    <p>{{sub.objective}}</p><p class="muted">{{sub.depends_on.length?'前置任务：'+sub.depends_on.join('、'):'独立任务，可并行'}}</p>
    <p v-if="sub.started_at" class="muted">开始 {{when(sub.started_at)}}<br v-if="sub.finished_at"><span v-if="sub.finished_at">结束 {{when(sub.finished_at)}}</span></p>
    <p v-if="sub.output">{{sub.output.summary}}</p><p v-if="sub.error" class="inline-error">{{sub.error}}</p>
   </article></div>
   <p v-if="!collaboration.tasks.length" class="muted">本问题未派发调查子任务。</p>
   <p class="inv-merge"><strong>协调合并：</strong>{{collaboration.merge_status==='completed'?'已完成':collaboration.merge_status==='insufficient'?'保留已有证据，仍有未解决事项':collaboration.merge_status==='running'?'正在合并':collaboration.merge_status==='blocked'?'额度阻断，已有记录保留':collaboration.merge_status==='failed'?'合并未完成，已有记录保留':'等待调查完成'}}</p>
  </section>
  <section v-if="task.input.mode==='verified'&&verification" class="inv-verification" aria-label="原因核验与补查">
   <div class="heading"><h3>原因核验与补查</h3><span class="pill" :class="verification.status==='confirmed'?'good':'amber'">{{verificationStatuses[verification.status]??verification.status}}</span></div>
   <p class="hint">候选原因先核验，未通过就不会作为最终原因。历史资料保留时间，补查只读取当前实例。</p>
   <article v-for="round in verification.rounds" :key="round.number" class="inv-audit-round">
    <strong>核验 Agent · 第 {{round.number}} 次核验：{{round.verdict?decisions[round.verdict.decision]:round.error?'未完成':'正在检查'}}</strong>
    <p class="muted">核验前的候选原因：{{round.candidate.root_cause??'证据不足，未提出原因'}}</p>
    <p v-if="round.verdict">{{round.verdict.reason}}</p><p v-if="round.error" class="inline-error">{{round.error}}</p>
    <div v-for="(issue,i) in round.verdict?.issues??[]" :key="i" class="inv-finding"><p>{{issue.statement}}</p><p v-if="issue.resolution" class="muted">{{issue.resolution}}</p>
     <div v-for="(q,j) in issue.references" :key="j"><button class="text-button" @click="citation(q.id)">{{q.id}} · {{lookup(q.id)?.scope==='historical'?'历史证据':lookup(q.id)?.kind==='runtime'?'运行证据':'知识引用'}} · 查看原文 ↗</button><blockquote>{{q.quote}}</blockquote></div>
    </div>
   </article>
   <div v-for="item in verification.replans" :key="item.number" class="inv-replan"><strong>第 {{item.number}} 次重新规划：{{item.action==='recheck'?'补查':item.action==='clarify'?'请补充信息':'停止'}}</strong><p>{{item.reason}}</p><p class="muted">{{item.status==='completed'?'已完成':item.status==='failed'?'补查未完成':item.status==='interrupted'?'已中断':['skipped','blocked'].includes(String(item.status))?'已停止':'正在准备/补查'}}</p></div>
   <div v-if="verification.conflicts?.length" class="inv-conflicts"><h4>新旧证据检查</h4><div v-for="(issue,i) in verification.conflicts" :key="i"><p>{{issue.resolved?'已核对':'尚未解决'}}：{{issue.statement}}</p><p v-if="issue.resolution" class="muted">{{issue.resolution}}</p><button v-for="(q,j) in issue.references" :key="j" class="text-button" @click="citation(q.id)">{{q.id}} · {{lookup(q.id)?.scope==='historical'?'历史证据':'运行证据'}} · 查看原文 ↗</button></div></div>
   <div v-if="verification.questions?.length"><h4>需要你补充</h4><ul><li v-for="(q,i) in verification.questions" :key="i">{{q}}</li></ul><p class="hint">可在上方问题框补充后重新调查，无需提供密钥或私人资料。</p></div>
   <p v-if="verification.stop_reason" class="muted">{{verification.stop_reason}}</p>
  </section>
  </details>
  <details v-if="task.references.length"><summary>全部来源与采集时间（{{task.references.length}}）</summary><div v-for="ref in task.references" :key="ref.id" class="inv-reference"><button class="text-button" @click="openRef(ref)">{{ref.id}} · {{ref.source}} ↗</button><p class="muted">{{ref.scope==='historical'?'历史快照 · ':''}}{{ref.kind==='runtime'?('采集 '+when(ref.collected_at??task.created_at)):('文档 '+ref.doc_version+' · 索引 '+ref.index_id)}}{{ref.truncated?' · 模型读取了截取片段':''}}</p></div></details>
 </article>
 <article class="panel"><div class="heading"><h2>调查记录</h2><button class="text-button" @click="refresh">刷新列表 ↻</button></div><label class="history-toggle"><input type="checkbox" v-model="showHistorical">包含历史与示例记录</label><div class="inv-history"><button v-for="item in dailyHistory" :key="item.id" :disabled="busy" @click="follow(item)"><strong>{{item.input.question}}</strong><span>{{when(item.created_at)}} · {{statuses[item.status]??item.status}} · {{item.report?.assessment==='insufficient'?'信息不足':item.report?.assessment==='supported'?'已有依据':''}}</span></button></div></article>
 <div v-if="opened" class="modal-backdrop" @click.self="opened=null"><section class="evidence-modal" role="dialog" aria-modal="true" aria-labelledby="inv-original-title"><div class="heading"><div><span class="eyebrow">调查引用原文</span><h2 id="inv-original-title">{{opened.title}}</h2></div><button class="close" aria-label="关闭调查原文" @click="opened=null">×</button></div><pre>{{opened.text}}</pre></section></div>
</div>
</template>
