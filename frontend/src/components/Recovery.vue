<script setup lang="ts">
import {ref,onMounted,onUnmounted} from 'vue'
import {durableRequest,resumePending,eventUrl,saveCursor} from '../api/durable'
import {request} from '../api/client'
import type {components} from '../api/schema'
type Plan=components['schemas']['Plan'] & {before:{bindings:Record<string,{state:string}>},approval:{actor:string,approved_at:string}|null,result:{passed:boolean,scope_note?:string,after?:{bindings:Record<string,{state:string}>},probe?:{status:string,worker_version:string|null,result:{char_count:number,word_count:number}|null}}|null}
type Options={available:boolean,reason:string,actions:{id:string,title:string}[],investigations:{id:string,created_at:string,summary:string}[]}
const options=ref<Options|null>(null),history=ref<Plan[]>([]),plan=ref<Plan|null>(null)
const investigation=ref(''),action=ref(''),confirmed=ref(false),busy=ref(false),error=ref(''),progress=ref(''),observation=ref<Record<string,unknown>|null>(null)
let timer:ReturnType<typeof setTimeout>|undefined,stream:EventSource|null=null,disposed=false
const names:Record<string,string>={proposed:'待你批准 · 尚未修改服务',approved:'已批准 · 等待复核',executing:'正在执行前复核与恢复',verifying:'正在验证真实业务',completed:'恢复验证通过',failed:'恢复验证未通过',blocked:'状态变化，已停止',manual:'结果待确认，需要人工处理',superseded:'已有新版本，此方案失效',cancelled:'已取消'}
const when=(s:string)=>new Date(s).toLocaleString('zh-CN',{hour12:false})
const fail=(e:unknown)=>{error.value=e instanceof Error?e.message:String(e)}
const active=(p:Plan)=>['approved','executing','verifying'].includes(p.status)
async function refresh(){try{
 [options.value,history.value]=await Promise.all([request<Options>('/recovery/options'),request<Plan[]>('/recovery/plans')])
 if(!investigation.value && options.value.investigations.length){const desired=localStorage.getItem('opslens-recovery-investigation');investigation.value=options.value.investigations.some(i=>i.id===desired)?desired!:options.value.investigations[0]!.id}
 if(!options.value.actions.some(a=>a.id===action.value))action.value=options.value.actions[0]?.id??''
}catch(e){fail(e)}}
async function poll(){if(!plan.value||disposed)return
 try{plan.value=await request<Plan>('/recovery/plans/'+plan.value.id);error.value=plan.value.error??''
  if(active(plan.value)){timer=setTimeout(poll,900)}else{busy.value=false;stream?.close();progress.value=names[plan.value.status]??plan.value.status;await refresh()}
 }catch(e){fail(e);progress.value='正在重新连接；已保存动作不会重复执行';timer=setTimeout(poll,1500)}}
async function follow(p:Plan){stream?.close();if(timer)clearTimeout(timer);plan.value=p;confirmed.value=false;error.value='';observation.value=null;progress.value=names[p.status]??p.status;busy.value=active(p);localStorage.setItem('opslens-recovery-last',p.id)
 if(busy.value){stream=new EventSource(eventUrl('/recovery/plans/'+p.id+'/events'));stream.onmessage=e=>{saveCursor('/recovery/plans/'+p.id+'/events',e);try{const event=JSON.parse(e.data);progress.value=event.payload.message??progress.value;if(event.kind==='finished')void poll()}catch(e){fail(e)}};stream.onerror=()=>{progress.value='进度正在重连，已保存动作不会重新执行'};void poll()}}
async function propose(){busy.value=true;error.value=''
 try{await follow(await durableRequest<Plan>('/recovery/plans',{investigation_id:investigation.value,action:action.value}));await refresh()}
 catch(e){fail(e)}finally{busy.value=!!plan.value&&active(plan.value)}}
async function approve(){if(!plan.value||!confirmed.value)return;busy.value=true;error.value=''
 try{await follow(await durableRequest<Plan>('/recovery/plans/'+plan.value.id+'/approve',{version:plan.value.version,digest:plan.value.digest,confirm:true}))}
 catch(e){fail(e);busy.value=false}}
async function cancel(){if(!plan.value)return
 try{await follow(await request<Plan>('/recovery/plans/'+plan.value.id+'/cancel',{request_id:crypto.randomUUID(),version:plan.value.version,digest:plan.value.digest,confirm:true}));await refresh()}catch(e){fail(e)}}
async function recheck(){if(!plan.value)return
 try{observation.value=await request<Record<string,unknown>>('/recovery/plans/'+plan.value.id+'/recheck',{});progress.value='当前状态已只读复核，没有重发恢复动作'}catch(e){fail(e)}}
async function restoreLast(){
 if(disposed)return
 await refresh()
 try{
  const desired=localStorage.getItem('opslens-recovery-investigation');if(desired){localStorage.removeItem('opslens-recovery-investigation');const match=history.value.find(p=>p.investigation_id===desired);if(match)await follow(match);return}
  const created=await resumePending<Plan>('/recovery/plans');const id=created?.id??localStorage.getItem('opslens-recovery-last')
  if(id){const approved=await resumePending<Plan>('/recovery/plans/'+id+'/approve');await follow(approved??created??await request<Plan>('/recovery/plans/'+id))}
 }catch(e){fail(e);progress.value='正在重新连接；已保存动作不会重复执行';timer=setTimeout(restoreLast,1500)}
}
onMounted(restoreLast)
onUnmounted(()=>{disposed=true;stream?.close();if(timer)clearTimeout(timer)})
</script>
<template>
<div class="resume-notice" v-if="plan && Number(plan.recovery?.recovery_count)>0">平台中断后已继续验证 · 恢复 {{plan.recovery?.recovery_count}} 次 · 恢复命令没有重新发出</div>
 <p class="error" role="alert" v-if="error">{{error}}</p>
 <section class="card recovery-form">
  <div class="heading"><h2>先看方案，再批准恢复</h2><button class="secondary" :disabled="busy" @click="refresh">刷新可用动作</button></div>
  <p class="muted">恢复仅适用于隔离实验实例。外部项目请依据调查建议人工处理。生成方案只读取状态；批准后才会修改服务。</p>
  <label for="recovery-investigation">已核验通过的调查</label><select id="recovery-investigation" v-model="investigation" :disabled="busy"><option v-for="i in options?.investigations" :key="i.id" :value="i.id">{{when(i.created_at)}} · {{i.summary}}</option></select>
  <label for="recovery-action">当前允许的恢复动作</label><select id="recovery-action" v-model="action" :disabled="busy"><option v-for="a in options?.actions" :key="a.id" :value="a.id">{{a.title}}</option></select>
  <p v-if="options&&!options.available" class="inline-error">{{options.reason}}</p>
  <p v-if="options && !options.actions.length" class="muted">当前没有适用的自动恢复动作；保持现状，不强行执行。</p>
  <button class="primary" :disabled="busy||!investigation||!action" @click="propose">生成恢复方案（不执行）</button>
 </section>
 <section class="card recovery-plan" v-if="plan" aria-label="恢复方案与批准">
  <div class="heading"><h2>{{plan.title}}</h2><span class="pill" :class="plan.status==='completed'?'success':'warning'">{{names[plan.status]??plan.status}}</span></div>
  <p class="muted">方案第 {{plan.version}} 版 · 故障实验实例 · {{when(plan.created_at)}}<br>有效至 {{when(plan.expires_at)}}；过期请重新生成。</p>
  <p>{{plan.explanation}}</p><p class="recovery-risk">{{plan.risk}}</p>
  <p><strong>生成方案时的 worker：</strong>{{plan.before.bindings?.worker?.state==='exited'?'已停止':plan.before.bindings?.worker?.state==='running'?'运行中':plan.before.bindings?.worker?.state}}</p>
  <template v-if="plan.status==='proposed'"><label class="recovery-confirm"><input type="checkbox" v-model="confirmed" :disabled="busy">我批准此版本在故障实验实例执行上述动作，并用固定样例任务验证。</label><div class="recovery-buttons"><button class="primary" :disabled="busy||!confirmed" @click="approve">批准此版本并执行</button><button class="secondary" :disabled="busy" @click="cancel">取消，不执行</button></div></template>
  <details v-if="plan.approval"><summary>批准记录</summary><p class="muted">此版本已批准 · {{plan.approval.actor==='development_selfcheck'?'维护自检批准（非用户批准）':'工作台批准'}} · {{when(plan.approval.approved_at)}}</p></details>
  <p aria-live="polite">{{progress}}</p><p v-if="plan.error" class="error">{{plan.error}}</p>
  <p class="hint">恢复动作记录：{{Number(plan.recovery?.action_count??0)}} 条</p>
  <section v-if="plan.result" class="recovery-result" aria-label="恢复验证结果"><h3>{{plan.result.passed?'新任务已完成，恢复验证通过':'未能确认业务恢复'}}</h3><p v-if="plan.result.after?.bindings.worker">验证时 worker：{{plan.result.after.bindings.worker.state==='running'?'已运行':plan.result.after.bindings.worker.state}}</p><p v-if="plan.result.probe">任务状态：{{plan.result.probe.status==='completed'?'已完成':plan.result.probe.status}} · 实际版本 {{plan.result.probe.worker_version??'尚无'}}<br>字符数 {{plan.result.probe.result?.char_count??'尚无'}} · 词数 {{plan.result.probe.result?.word_count??'尚无'}}</p><p class="muted">{{plan.result.scope_note}}</p></section>
  <button class="secondary" :disabled="busy" @click="recheck">只读复核当前状态（不重发动作）</button>
  <details v-if="observation"><summary>本次只读复核结果</summary><pre>{{JSON.stringify(observation,null,2)}}</pre></details>
  <details><summary>查看执行前检查与动作记录</summary><pre>{{JSON.stringify({approval:plan.approval,execution:plan.execution,result:plan.result},null,2)}}</pre></details>
 </section>
 <details class="card"><summary>历史恢复方案</summary><div class="heading"><h2>已保存的恢复方案</h2><button class="secondary" @click="refresh">刷新列表</button></div><div class="recovery-history"><button v-for="p in history" :key="p.id" class="secondary" @click="follow(p)">{{p.title}} · 第{{p.version}}版<br>{{when(p.created_at)}} · {{names[p.status]??p.status}}</button></div><p class="muted" v-if="!history.length">尚无恢复方案</p></details>
</template>
<style scoped>
.recovery-form,.recovery-plan{margin-bottom:24px}.recovery-form label{display:block;font-weight:600;margin:18px 0 8px}.recovery-form select{width:100%;padding:12px;border:1px solid var(--line);border-radius:8px;margin-bottom:12px;background:white}.recovery-risk{padding:16px;background:#fff7e8;border-left:3px solid #d8a352}.recovery-confirm{display:flex;align-items:flex-start;gap:10px;margin:24px 0;line-height:1.7}.recovery-confirm input{margin-top:6px}.recovery-buttons{display:flex;gap:12px;flex-wrap:wrap}.recovery-result{padding:20px;background:#edf7f2;border-radius:12px;margin:20px 0}.recovery-history{display:grid;gap:10px}.recovery-history button{text-align:left;line-height:1.8}details{margin-top:16px}pre{white-space:pre-wrap;overflow-wrap:anywhere;max-height:450px;overflow:auto;background:#f5f8f7;padding:18px}
</style>
