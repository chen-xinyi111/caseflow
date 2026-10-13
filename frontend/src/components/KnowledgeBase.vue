<script setup lang="ts">
import {useModal} from "../api/modal"
import { ref,onMounted,onUnmounted } from 'vue'
import {durableRequest,resumePending,eventUrl,saveCursor} from '../api/durable'
import { request } from '../api/client'
import type { components } from '../api/schema'
type Doc=components['schemas']['Document']
type Detail=components['schemas']['DocumentDetail']
type Citation=components['schemas']['Citation']
type Status=components['schemas']['KBStatus']
type Task=components['schemas']['Task']
type Results=components['schemas']['SearchResult']
const docs=ref<Doc[]>([]),status=ref<Status|null>(null)
const query=ref(''),mode=ref<'keyword'|'vector'|'hybrid'|'rerank'>('keyword'),version=ref('v1')
const results=ref<Results|null>(null),task=ref<Task|null>(null)
const busy=ref(false),uploading=ref(false),error=ref(''),progress=ref('')
const file=ref<File|null>(null),title=ref(''),source=ref(''),docVersion=ref('v1'),applies=ref('v1')
const opened=ref<{doc:Doc,pages:string[],page:number,start:number|null,end:number|null}|null>(null)
useModal(opened)
let events:EventSource|null=null
let timer:ReturnType<typeof setTimeout>|undefined
let disposed=false
const labels:Record<string,string>={keyword:'关键词检索',vector:'向量检索',hybrid:'混合检索',rerank:'混合＋重排'}
const date=(value:string)=>new Date(value).toLocaleString('zh-CN',{hour12:false})
const fail=(e:unknown)=>{error.value=e instanceof Error?e.message:String(e)}
const slice=(start:number,end?:number)=>opened.value?Array.from(opened.value.pages[opened.value.page-1]??'').slice(start,end).join(''):''
async function refresh(){
 try{
  const [a,b]=await Promise.all([request<Doc[]>('/kb/documents'),request<Status>('/kb/status')])
  docs.value=a;status.value=b
 }catch(e){fail(e)}
}
function chooseFile(event:Event){
 const target=event.target as HTMLInputElement
 file.value=target.files?.[0]??null
 if(file.value){title.value=file.value.name.replace(/\.(md|txt|pdf)$/i,'');source.value='用户上传/'+file.value.name}
}
async function upload(){
 if(!file.value)return
 uploading.value=true;error.value=''
 try{
  const body=new FormData()
  body.append('file',file.value);body.append('title',title.value);body.append('source',source.value)
  body.append('doc_version',docVersion.value);body.append('applicable_versions',applies.value)
  const response=await fetch('/api/v1/kb/documents',{method:'POST',body})
  const value=await response.json()
  if(!response.ok)throw new Error(typeof value.detail==='string'?value.detail:'文档上传未通过校验')
  progress.value='文档已入库；请更新索引后检索新资料。'
  await refresh()
 }catch(e){fail(e)}finally{uploading.value=false}
}
async function follow(value:Task){
 events?.close();if(timer)clearTimeout(timer)
 task.value=value;busy.value=true
 localStorage.setItem('opslens-kb-last-task',value.id)
 events=new EventSource(eventUrl('/kb/tasks/'+value.id+'/events'))
 events.onmessage=(message)=>{
  saveCursor('/kb/tasks/'+value.id+'/events',message)
  try{
   const event=JSON.parse(message.data) as {kind:string,payload:{message?:string}}
   if(event.payload.message)progress.value=event.payload.message
   if(event.kind==='finished'){events?.close();void poll()}
  }catch(e){fail(e)}
 }
 events.onerror=()=>{progress.value='进度连接正在重连；已保存任务可继续查询'}
 await poll()
}
async function poll(){
 if(!task.value||disposed)return
 try{
  const value=await request<Task>('/kb/tasks/'+task.value.id)
  task.value=value;error.value=value.error??''
  if(['completed','blocked','failed'].includes(value.status)){
   busy.value=false;events?.close();if(timer)clearTimeout(timer)
   if(value.status!=='completed')error.value=value.error??'任务未完成'
   else if(value.kind==='search' && value.result && 'hits' in value.result){results.value=value.result;progress.value='检索完成'}
   else progress.value='索引构建完成'
   await refresh();return
  }
  if(timer)clearTimeout(timer)
  timer=setTimeout(poll,1000)
 }catch(e){fail(e);progress.value='正在重新连接；已保存任务保留';timer=setTimeout(poll,1500)}
}
async function build(kind:'keyword'|'semantic'){
 error.value='';results.value=null;progress.value='索引构建已提交'
 try{await follow(await durableRequest<Task>('/kb/indexes',{mode:kind}))}catch(e){fail(e);busy.value=false}
}
async function search(){
 error.value='';results.value=null;progress.value='检索已提交'
 try{await follow(await durableRequest<Task>('/kb/searches',{query:query.value,mode:mode.value,app_version:version.value}))}catch(e){fail(e);busy.value=false}
}
async function openDoc(doc:Doc){
 try{const value=await request<Detail>('/kb/documents/'+doc.id);opened.value={doc:value,pages:value.pages,page:1,start:null,end:null}}catch(e){fail(e)}
}
async function openCitation(hit:Results['hits'][number]){
 try{const value=await request<Citation>('/kb/citations/'+hit.chunk.id);opened.value={doc:value.document,pages:Array.from({length:value.chunk.page},(_,i)=>i===value.chunk.page-1?value.page_text:''),page:value.chunk.page,start:value.chunk.start_char,end:value.chunk.end_char}}catch(e){fail(e)}
}
async function restoreLast(){
 if(disposed)return
 await refresh()
 try{const resumed=await resumePending<Task>('/kb/indexes')??await resumePending<Task>('/kb/searches');const id=resumed?.id??localStorage.getItem('opslens-kb-last-task');if(id){const value=resumed??await request<Task>('/kb/tasks/'+id);query.value=String(value.input.query??query.value);version.value=String(value.input.app_version??version.value);await follow(value)}}
 catch(e){fail(e);progress.value='正在重新连接；知识库任务保留';timer=setTimeout(restoreLast,1500)}
}
onMounted(restoreLast)
onUnmounted(()=>{disposed=true;events?.close();if(timer)clearTimeout(timer)})
</script>

<template>
<div class="knowledge">
 <div v-if="error" class="alert" role="alert">{{error}}</div>
 <div class="kb-info"><span class="pill good">本机知识库</span><span>{{status?.documents??0}} 份文档 · {{status?.chunks??0}} 个段落</span><button class="text-button" @click="refresh">刷新资料与模型状态 ↻</button></div>
 <article class="panel kb-search">
  <div class="heading"><h2>查找故障处理资料</h2><span class="pill" :class="status?.models['text-embedding-v4']?.available?'good':'amber'">{{status?.models['text-embedding-v4']?.available?'语义模型已就绪':'关键词模式可用 · 语义额度待核验'}}</span></div>
  <label for="kb-query">检索问题</label><textarea id="kb-query" v-model="query" maxlength="1000" rows="2"></textarea>
  <div class="kb-controls"><div><label for="kb-mode">检索方式</label><select id="kb-mode" v-model="mode"><option value="keyword">关键词检索（本机）</option><option value="vector">向量检索</option><option value="hybrid">混合检索</option><option value="rerank">混合＋重排</option></select></div><div><label for="kb-version">应用版本</label><input id="kb-version" v-model="version" maxlength="100"></div><button class="primary" :disabled="busy||!query.trim()||!version.trim()" @click="search">{{busy?'任务处理中…':'检索资料 →'}}</button></div>
  <p class="hint">检索资料并打开引用原文。</p>
  <p v-if="mode!=='keyword'&&!status?.models['text-embedding-v4']?.available" class="inline-error">{{status?.models['text-embedding-v4']?.reason}}</p>
  <p v-if="mode==='rerank'&&!status?.models['gte-rerank-v2']?.available" class="inline-error">{{status?.models['gte-rerank-v2']?.reason}}</p>
  <p v-if="progress" class="muted" role="status">{{progress}}</p>
  <p v-if="task" class="task-id">任务 {{task.id}} · {{task.status}}</p>
 </article>
 <section v-if="results" class="kb-results"><div class="heading"><h2>检索结果 · {{results.hits.length}} 条</h2><span>{{labels[results.mode]}} · {{results.elapsed_ms.toFixed(0)}} ms</span></div><p class="hint">索引 {{results.index_id}} · {{results.note}}</p><article class="panel kb-hit" v-for="(hit,index) in results.hits" :key="hit.chunk.id"><div class="heading"><h3><span class="rank">{{index+1}}</span>{{hit.document.title}}</h3><button class="secondary" @click="openCitation(hit)">查看引用原文 ↗</button></div><p class="kb-provenance">{{hit.document.source}} · 文档 {{hit.document.doc_version}} · 适用 {{hit.document.applicable_versions.join('、')}} · 第 {{hit.chunk.page}} 页 / {{hit.chunk.line_start}}–{{hit.chunk.line_end}} 行</p><pre class="kb-excerpt">{{hit.chunk.content}}</pre><details><summary>排序详情</summary><div class="kb-scores"><span>召回：{{hit.routes.join(' + ')}}</span><span v-if="hit.bm25_score!=null">BM25 {{hit.bm25_score.toFixed(3)}}</span><span v-if="hit.vector_score!=null">向量 {{hit.vector_score.toFixed(3)}}</span><span v-if="hit.rerank_score!=null">重排 {{hit.rerank_score.toFixed(3)}}</span></div></details></article><article class="panel" v-if="!results.hits.length"><h3>没有找到匹配资料</h3><p class="muted">请补充关键词、核对应用版本，或上传相关手册后更新索引。</p></article></section>
 <article class="panel"><div class="heading"><h2>知识文档</h2><span>{{docs.length}} 份</span></div><div class="kb-doc-list"><button v-for="doc in docs" :key="doc.id" @click="openDoc(doc)"><strong>{{doc.title}}</strong><span>{{doc.doc_version}} · {{doc.format}} · {{doc.chunk_count}} 段 · 打开原文 ↗</span></button></div></article>
 <details class="panel"><summary>导入文档与管理索引</summary><div class="kb-management"><article class="panel"><h2>导入本机文档</h2><p class="muted">支持 .md、.txt、文本型 .pdf，单文件不超过 5 MiB。</p><label for="kb-file">选择文件</label><input id="kb-file" type="file" accept=".md,.txt,.pdf" @change="chooseFile"><label for="kb-title">文档标题</label><input id="kb-title" v-model="title" maxlength="200"><label for="kb-source">来源说明</label><input id="kb-source" v-model="source" maxlength="500"><div class="kb-controls"><div><label for="kb-doc-version">文档版本</label><input id="kb-doc-version" v-model="docVersion" maxlength="100"></div><div><label for="kb-applies">适用应用版本</label><input id="kb-applies" v-model="applies" placeholder="v1 或 *，多个版本用逗号隔开"></div></div><button class="primary" :disabled="uploading||!file||!title.trim()||!source.trim()" @click="upload">{{uploading?'正在入库…':'导入文档'}}</button></article>
 <article class="panel"><div class="heading"><h2>索引与文档版本</h2><span>{{docs.length}} 份</span></div><p class="muted">更新索引收录各来源最新入库版本。旧索引和旧引用继续保留。</p><div class="kb-build"><button class="secondary" :disabled="busy||!docs.length" @click="build('keyword')">更新关键词索引</button><button class="secondary" :disabled="busy||!docs.length" @click="build('semantic')">构建语义索引</button></div><p class="hint">语义索引会向百炼发送文档文本；仅在免费额度与用完即停核验后调用。</p><div class="kb-index-list"><div v-for="item in status?.indexes.slice(0,5)" :key="item.id"><strong>{{item.mode==='keyword'?'关键词索引':'语义索引'}}</strong><span>{{item.status}}{{item.active?' · 当前':''}} · {{date(item.created_at)}}</span><p v-if="item.error" class="inline-error">{{item.error}}</p></div></div></article></div>
 </details>
 <div v-if="opened" class="modal-backdrop" @click.self="opened=null"><section class="evidence-modal" role="dialog" aria-modal="true" aria-labelledby="kb-citation-title"><div class="heading"><div><span class="eyebrow">知识库原文</span><h2 id="kb-citation-title">{{opened.doc.title}}</h2></div><button class="close" aria-label="关闭知识原文" @click="opened=null">×</button></div><p class="kb-provenance">{{opened.doc.source}} · 文档 {{opened.doc.doc_version}} · 第 {{opened.page}} 页</p><select v-if="opened.start===null&&opened.pages.length>1" v-model="opened.page" aria-label="原文页码"><option v-for="(_,i) in opened.pages" :key="i" :value="i+1">第 {{i+1}} 页</option></select><pre v-if="opened.start!==null&&opened.end!==null"><span>{{slice(0,opened.start)}}</span><mark>{{slice(opened.start,opened.end)}}</mark><span>{{slice(opened.end)}}</span></pre><pre v-else>{{opened.pages[opened.page-1]}}</pre><a class="text-button" :href="'/api/v1/kb/documents/'+opened.doc.id+'/original'">下载保存的原文件 ↗</a><p class="hash">文档 SHA-256 · {{opened.doc.sha256}}</p></section></div>
</div>
</template>
