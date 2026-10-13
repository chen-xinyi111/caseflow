<script setup lang="ts">
import {useModal} from "./api/modal"
import {durableRequest,resumePending,eventUrl,saveCursor} from './api/durable'
import { ref, watch, onMounted, onUnmounted } from 'vue'
import KnowledgeBase from './components/KnowledgeBase.vue'
import Investigation from './components/Investigation.vue'
import Recovery from './components/Recovery.vue'
import Evaluation from './components/Evaluation.vue'
import GettingStarted from './components/GettingStarted.vue'
import Workbench from './components/Workbench.vue'
import './components/investigation.css'
import './components/knowledge.css'
import { request, sourceName, type LabStatus, type LabJob, type FaultCase, type Evidence, type Collection } from './api/client'
const validSections=['home','guide','overview','jobs','cases','knowledge','investigation','recovery','evaluation','maintenance']
const savedSection=localStorage.getItem('opslens-section')
const section = ref(savedSection&&validSections.includes(savedSection)?(savedSection==='guide'?'home':savedSection):'home'), text = ref('OpsLens AI makes evidence clear')
const pageNames:Record<string,string>={home:'工作台',investigation:'故障调查',knowledge:'知识库',recovery:'恢复处置',maintenance:'维护与记录',guide:'示例记录',overview:'实验环境',jobs:'测试任务',cases:'历史故障案例',evaluation:'对照评测'}
watch(section,value=>localStorage.setItem('opslens-section',value))
const labs = ref<LabStatus[]>([]), cases = ref<FaultCase[]>([])
const job = ref<LabJob | null>(null), selected = ref<FaultCase | null>(null), sample = ref<LabJob | null>(null)
const evidence = ref<Evidence[]>([]), opened = ref<Evidence | null>(null), collection = ref<Collection | null>(null)
const error = ref(''), progress = ref(''), submitting = ref(false), refreshing = ref(false)
useModal(opened)
let events: EventSource | null = null
let timer: ReturnType<typeof setTimeout> | undefined
let disposed = false
const stateName = (s:string) => (({running:'运行中',exited:'已停止',queued:'等待处理',completed:'已完成',partial:'部分采集失败',failed:'采集失败',healthy:'健康',unhealthy:'异常',created:'未启动',restarting:'重启中'} as Record<string,string>)[s] ?? s)
const when = (s:string | null | undefined) => s ? new Date(s).toLocaleString('zh-CN',{hour12:false}) : '尚未采集'
const labName = (s:string) => s==='healthy' ? '正常实验实例' : '故障实验实例'
const showError = (e:unknown) => { error.value = e instanceof Error ? e.message : String(e) }
async function refresh(){
 refreshing.value = true
 try { labs.value = await request<LabStatus[]>('/labs'); cases.value = await request<FaultCase[]>('/fault-cases') }
 catch(e){showError(e)} finally{refreshing.value=false}
}
async function submit(){
 submitting.value=true; error.value=''; job.value=null
 try { job.value=await request<LabJob>('/lab-jobs',{lab:'healthy',text:text.value,request_id:crypto.randomUUID()}); await poll() }
 catch(e){showError(e);submitting.value=false}
}
async function poll(){
 if(!job.value || disposed)return
 try { job.value=await request<LabJob>('/lab-jobs/healthy/'+job.value.id)
  if(job.value.status==='completed'){submitting.value=false;return}
  timer=setTimeout(poll,800)
 }catch(e){showError(e);submitting.value=false}
}
async function choose(item:FaultCase){
 localStorage.setItem('opslens-case',item.id)
 selected.value=item; sample.value=null; evidence.value=[]; opened.value=null; error.value=''; progress.value=''
 try{
  if(item.collection_id)evidence.value=(await request<Collection>('/collections/'+item.collection_id)).evidence ?? []
  if(item.sample_job_id){try{sample.value=await request<LabJob>('/lab-jobs/fault/'+item.sample_job_id)}catch{/* Unavailable historical job is not a current-state assertion. */}}
 }catch(e){showError(e)}
}
async function open(item:Evidence){
 try{opened.value=await request<Evidence>('/evidence/'+item.id)}catch(e){showError(e)}
}
async function collect(){
 events?.close();error.value='';progress.value='采集任务已提交'
 try{
  collection.value=await durableRequest<Collection>('/collections',{lab:'fault'})
  localStorage.setItem('opslens-collection-last',collection.value.id)
  events=new EventSource(eventUrl('/collections/'+collection.value.id+'/events'))
  events.onmessage=async(message)=>{
   if(collection.value)saveCursor('/collections/'+collection.value.id+'/events',message)
   try{
    const event=JSON.parse(message.data) as {kind:string,payload:{message?:string}}
    if(event.payload.message)progress.value=event.payload.message
    if(event.kind==='finished' && collection.value){
     events?.close();collection.value=await request<Collection>('/collections/'+collection.value.id)
     evidence.value=collection.value.evidence ?? [];opened.value=null;await refresh()
    }
   }catch(e){showError(e)}
  }
  events.onerror=()=>{progress.value='进度连接中断，正在重连；已保存证据保留'}
 }catch(e){showError(e)}
}
function workerCase(){const item=cases.value.find(c=>c.id==='worker_stopped');if(item)void choose(item)}
onMounted(async()=>{
 await refresh()
 const selectedCase=cases.value.find(c=>c.id===localStorage.getItem('opslens-case'));if(selectedCase)await choose(selectedCase)
 try{const resumed=await resumePending<Collection>('/collections');const id=resumed?.id??localStorage.getItem('opslens-collection-last');if(id){collection.value=await request<Collection>('/collections/'+id);evidence.value=collection.value.evidence??[];if(['queued','running'].includes(collection.value.status))void pollCollection()}}catch(e){showError(e)}
})
async function pollCollection(){if(!collection.value||disposed)return
 try{collection.value=await request<Collection>('/collections/'+collection.value.id);evidence.value=collection.value.evidence??[];if(['queued','running'].includes(collection.value.status))timer=setTimeout(pollCollection,1000);else progress.value='采集结果已保存'}catch{progress.value='正在重新连接；采集记录保留';timer=setTimeout(pollCollection,1500)}
}
onUnmounted(()=>{disposed=true;events?.close();if(timer)clearTimeout(timer)})
</script>

<template>
<div class="shell">
 <aside><a class="brand" href="/"><span class="mark">O</span><span>OpsLens <b>AI</b></span></a><p class="workspace">本机辅助运维</p>
 <nav aria-label="主导航"><button v-for="(label,key) in {home:'工作台',investigation:'故障调查',knowledge:'知识库',recovery:'恢复处置'}" :key="key" :aria-label="label" :class="{active:section===key}" @click="section=key"><span>{{label}}</span></button></nav>
 <div class="aside-bottom"><button class="maintenance-link" @click="section='maintenance'">维护与记录</button><span>本机访问 · 单人使用</span></div></aside>
 <main><header><span>OpsLens <i>/</i> {{pageNames[section]}}</span><span class="local">● 本机</span></header>
 <div class="content"><div class="page-title"><div><h1>{{pageNames[section]}}</h1><p>{{section==='home'?'查看项目状态，基于证据排查问题。':section==='investigation'?'先预览资料，再开始只读调查。':section==='knowledge'?'查找资料与引用原文。':section==='recovery'?'审阅方案，批准后执行并验证。':'历史记录与维护功能'}}</p></div><button v-if="['overview','jobs','cases'].includes(section)" class="secondary" :disabled="refreshing" @click="refresh">刷新状态</button></div>
 <Workbench v-if="section==='home'" @navigate="section=$event" />
 <article v-if="section==='maintenance'" class="panel"><h2>维护与记录</h2><p class="muted">历史测试与演示完整保留，和日常调查分别查看。</p><div class="maintenance-grid"><button v-for="(label,key) in {guide:'示例记录',evaluation:'对照评测',cases:'历史故障案例',jobs:'测试任务',overview:'实验环境'}" :key="key" class="secondary" @click="section=key">{{label}} →</button></div></article>
 <div v-if="error" class="alert" role="alert">{{error}}</div>
 <template v-if="section==='overview'">
  <div class="summary-grid"><div class="summary"><span>实验实例</span><strong>02 <small>个独立实例</small></strong><p>独立网络 · 数据库 · 队列</p></div><div class="summary"><span>故障场景</span><strong>06 <small>种真实故障</small></strong><p>服务、配置、锁等待与版本</p></div><div class="summary"><span>已保存案例</span><strong>{{String(cases.filter(c=>c.collection_id).length).padStart(2,'0')}} <small>份运行证据</small></strong><p>原始来源与采集时间可追溯</p></div></div>
  <div class="heading"><h2>实验环境</h2><span>实时查询 · {{when(labs[0]?.collected_at)}}</span></div>
  <div class="lab-grid"><article class="panel" v-for="lab in labs" :key="lab.lab"><div class="heading"><h3>{{labName(lab.lab)}}</h3><span class="pill" :class="lab.lab==='healthy'?'good':'amber'">{{lab.lab==='healthy'?'正常业务演示':'隔离故障演示'}}</span></div><p class="muted">{{lab.lab==='healthy'?'用于提交任务和核对正确处理结果。':'故障仅在此实例准备，不影响正常任务。'}}</p><div class="services"><div v-for="service in lab.services" :key="service.service"><span>{{service.service}}</span><span :class="service.state==='running' && service.health!=='unhealthy'?'state-good':'state-bad'">● {{stateName(service.state)}}<small v-if="service.health"> · {{stateName(service.health)}}</small></span></div></div><p class="inline-error" v-if="lab.collection_error">{{lab.collection_error}}</p><button class="text-button" @click="section=lab.lab==='healthy'?'jobs':'cases'">{{lab.lab==='healthy'?'提交正常任务':'查看故障案例'}} →</button></article></div>
  <div class="panel welcome"><span class="step">01</span><div><h3>完成调查与恢复闭环</h3><p>从示例记录页面打开调查、审批与信息不足演示。</p></div><button class="primary" @click="section='guide'">打开示例记录 →</button></div>
 </template>
 <template v-if="section==='jobs'">
  <div class="lab-grid"><article class="panel"><h2>文本统计任务</h2><p class="muted">正常实验实例 · 使用预填文本验证业务结果</p><label for="text">任务文本</label><textarea id="text" v-model="text" maxlength="10000" rows="6"></textarea><div class="input-foot"><span>{{text.length}} / 10000 字符</span><span>按空白分隔统计词数</span></div><div class="example"><strong>预填文本的预期结果</strong><span>字符数 31 · 词数 5</span><p>字符数包括空格；中文连续片段按一个词统计。</p></div><button class="primary" :disabled="submitting || !text.trim()" @click="submit">{{submitting?'正在处理任务…':'提交任务 →'}}</button></article>
  <article class="panel result"><div class="heading"><h2>实际处理结果</h2><span v-if="job" class="pill" :class="job.status==='completed'?'good':'amber'">{{stateName(job.status)}}</span></div><template v-if="job?.result"><div class="result-icon">✓</div><h3>后台任务已完成</h3><div class="result-stats"><div><strong>{{job.result.char_count}}</strong><span>字符数</span></div><div><strong>{{job.result.word_count}}</strong><span>词数</span></div></div><dl><dt>处理版本</dt><dd>{{job.worker_version}}</dd><dt>完成时间</dt><dd>{{when(job.updated_at)}}</dd><dt>任务编号</dt><dd class="mono">{{job.id}}</dd></dl></template><template v-else><div class="result-icon empty">↗</div><h3>{{job?'等待后台处理':'等待提交任务'}}</h3><p class="muted">{{job?'页面会自动查询实际处理状态。':'提交后显示处理状态、实际结果和完成时间。'}}</p><p class="inline-error" v-if="job?.queue_error">{{job.queue_error}}</p></template></article></div>
 </template>
 <template v-if="section==='cases'">
  <div class="case-layout"><div class="case-list"><button v-for="item in cases" :key="item.id" class="case-item" :class="{selected:selected?.id===item.id}" @click="choose(item)"><div><strong>{{item.title}}</strong><span>{{item.collection_id?'已保存真实证据':'尚未准备证据'}}</span></div><span>›</span></button></div>
  <article class="panel case-detail"><template v-if="selected"><div class="heading"><h2>{{selected.title}}</h2><span class="pill amber">独立故障实例</span></div><p class="muted">{{selected.description}}</p><dl class="case-meta"><dt>案例采集时间</dt><dd>{{when(selected.observed_at)}}</dd><dt>当前 worker 状态</dt><dd>{{stateName(labs.find(l=>l.lab==='fault')?.services.find(s=>s.service==='worker')?.state ?? '未知')}}</dd><template v-if="sample"><dt>准备任务当前状态</dt><dd>{{stateName(sample.status)}}</dd></template></dl><div class="heading"><h3>运行证据 <small>{{evidence.length}} 项</small></h3><button class="secondary" :disabled="collection?.status==='queued' || collection?.status==='running'" @click="collect">重新采集当前证据</button></div><p class="muted" v-if="progress" role="status">{{progress}}</p><p class="hint">案例证据记录当时的故障；重新采集展示当前状态，不覆盖历史案例。</p><div class="evidence-list"><button v-for="item in evidence" :key="item.id" @click="open(item)"><div><strong>{{sourceName(item.source)}}</strong><span>{{when(item.collected_at)}}</span></div><span :class="item.success?'evidence-ok':'inline-error'">{{item.success?'打开原文 ↗':'查看采集错误 ↗'}}</span></button></div><p v-if="!evidence.length" class="muted">尚无采集记录，请重新采集或等待案例准备。</p></template><template v-else><div class="result-icon empty">⌕</div><h3>选择一项故障案例</h3><p class="muted">可以先选择左侧“worker 停止”。</p><button class="primary" :disabled="!cases.length" @click="workerCase">打开 worker 停止案例 →</button></template></article></div>
 </template>
 <GettingStarted v-if="section==='guide'" @navigate="section=$event" /><Recovery v-if="section==='recovery'" /><Investigation v-if="section==='investigation'" @navigate="section=$event" /><KnowledgeBase v-if="section==='knowledge'" /><Evaluation v-if="section==='evaluation'" />
 <footer>OpsLens AI <span>基于证据的辅助排障</span></footer>
 </div></main>
 <div class="modal-backdrop" v-if="opened" @click.self="opened=null"><section class="evidence-modal" role="dialog" aria-modal="true" aria-labelledby="evidence-title"><div class="heading"><div><span class="eyebrow">原始运行证据</span><h2 id="evidence-title">{{sourceName(opened.source)}}</h2></div><button class="close" aria-label="关闭证据" @click="opened=null">×</button></div><p class="muted">{{labName(opened.lab)}} · {{when(opened.collected_at)}} · {{opened.success?'采集成功':'采集失败'}}</p><pre>{{opened.content}}</pre><p class="hash">SHA-256 · {{opened.sha256}}</p></section></div>
</div>
</template>
