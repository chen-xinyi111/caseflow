<script setup lang="ts">
import {ref,computed,onMounted,watch} from 'vue'
import {request} from '../api/client'
import type {components} from '../api/schema'
type Target=components['schemas']['ExternalTarget']
type Snapshot=components['schemas']['ExternalSnapshot']
const props=defineProps<{busy:boolean,available?:boolean}>()
const emit=defineEmits<{submit:[payload:Record<string,unknown>]}>()
const targets=ref<Target[]>([]),target=ref(localStorage.getItem('opslens-external-target')??''),question=ref(''),snapshot=ref<Snapshot|null>(null)
const selected=ref<string[]>([]),confirm=ref(false),working=ref(false),error=ref(''),mode=ref('verified')
const validSelection=computed(()=>snapshot.value?.items.some(i=>i.success&&selected.value.includes(i.id))??false)
const name=ref(''),project=ref(''),services=ref('agent_service,postgres,redis,kafka'),logs=ref('agent_service')
const docTitle=ref(''),docText=ref(''),notice=ref('')
async function load(){try{targets.value=await request<Target[]>('/external-targets');if(!targets.value.some(t=>t.id===target.value))target.value=targets.value[0]?.id??''}catch(e){fail(e)}}
function fail(e:unknown){error.value=e instanceof Error?e.message:String(e)}
watch(target,()=>{snapshot.value=null;selected.value=[];confirm.value=false;localStorage.setItem('opslens-external-target',target.value);localStorage.removeItem('opslens-external-snapshot')})
watch(selected,()=>{confirm.value=false},{deep:true})
async function capture(){working.value=true;error.value='';confirm.value=false;try{
 snapshot.value=await request<Snapshot>('/external-targets/'+target.value+'/snapshots',{request_id:crypto.randomUUID()})
 selected.value=snapshot.value.items.filter(i=>i.success).concat(snapshot.value.documents).map(i=>i.id)
 localStorage.setItem('opslens-external-snapshot',snapshot.value.id)
}catch(e){fail(e)}finally{working.value=false}}
async function submit(){if(!snapshot.value||!confirm.value)return;working.value=true;error.value='';try{
 const proof=await request<{id:string}>('/external-snapshots/'+snapshot.value.id+'/consent',{digest:snapshot.value.sha256,selected_ids:selected.value,confirm:true})
 emit('submit',{target:'external',question:question.value,mode:mode.value,external_target_id:target.value,external_snapshot_id:snapshot.value.id,external_consent_id:proof.id})
}catch(e){fail(e)}finally{working.value=false}}
async function register(){working.value=true;error.value='';try{const t=await request<Target>('/external-targets',{name:name.value,compose_project:project.value,services:services.value.split(',').map(s=>s.trim()).filter(Boolean),log_services:logs.value.split(',').map(s=>s.trim()).filter(Boolean)});await load();target.value=t.id;notice.value='项目已登记，尚未采集。'}catch(e){fail(e)}finally{working.value=false}}
async function document(){working.value=true;error.value='';try{await request('/external-targets/'+target.value+'/documents',{title:docTitle.value,content:docText.value});snapshot.value=null;confirm.value=false;notice.value='资料已保存，请重新采集以纳入发送预览。';localStorage.removeItem('opslens-external-snapshot')}catch(e){fail(e)}finally{working.value=false}}
onMounted(async()=>{await load();const id=localStorage.getItem('opslens-external-snapshot');if(id)try{const s=await request<Snapshot>('/external-snapshots/'+id);if(s.target_id===target.value){snapshot.value=s;selected.value=s.items.filter(i=>i.success).concat(s.documents).map(i=>i.id)}}catch{/* Stale preview can be safely collected again. */}})
</script>
<template>
<article class="panel external-form">
 <div class="heading"><h2>调查项目问题</h2><span class="pill">只读 · 仅提供建议</span></div><p v-if="available===false" class="inline-error">调查模型暂不可用；仍可采集并查看本机资料。</p>
 <p v-if="error" class="alert" role="alert">{{error}}</p><p v-if="notice" class="muted" role="status">{{notice}}</p>
 <label for="ext-project">项目</label><select id="ext-project" v-model="target" :disabled="busy||working"><option v-for="t in targets" :key="t.id" :value="t.id">{{t.name}} · {{t.compose_project}}</option></select>
 <label for="ext-question">遇到了什么问题？</label><textarea id="ext-question" v-model="question" rows="3" maxlength="1000" :disabled="busy||working" placeholder="例如：服务无法访问，请结合状态与日志帮助排查。"></textarea>
 <div class="heading"><p class="muted">先在本机采集状态和限定日志，不改变项目运行状态。</p><button class="secondary" :disabled="!target||busy||working" @click="capture">{{working?'处理中…':snapshot?'重新采集资料':'采集并预览资料'}}</button></div>
 <section v-if="snapshot" class="preview"><div class="heading"><h3>待发送资料</h3><span class="muted">{{new Date(snapshot.captured_at).toLocaleString('zh-CN',{hour12:false})}}</span></div>
  <p v-for="(e,i) in snapshot.errors" :key="i" class="inline-error">{{e}}</p>
  <p class="muted">可排除日志或资料。请检查脱敏内容是否仍包含不希望发送的信息。</p>
  <div v-for="item in [...snapshot.items,...snapshot.documents]" :key="item.id" class="preview-item"><label><input type="checkbox" v-model="selected" :value="item.id" :disabled="busy||working||item.success===false">{{item.kind==='knowledge'?'项目资料':item.source==='docker.services'?'容器状态':'应用日志'}} · {{item.source}}<span v-if="item.success===false"> · 采集失败，不发送</span></label><details><summary>查看脱敏原文</summary><pre>{{item.content}}</pre></details></div>
  <p v-if="!validSelection" class="inline-error">请至少保留一项成功采集的运行证据。</p><label class="send-confirm"><input type="checkbox" v-model="confirm" :disabled="busy||working||!validSelection">我确认将选中资料发送至阿里云百炼北京模型服务，用于本次调查。</label>
 </section>
 <details><summary>高级选项</summary><label for="ext-mode">调查方式</label><select id="ext-mode" v-model="mode" :disabled="busy||working"><option value="verified">调查与独立核验</option><option value="single">单 Agent 调查</option><option value="multi">多 Agent 协作</option></select><p class="muted">结论限定于确认快照。需要新证据时须重新采集和确认；额度不可用时停止。</p></details>
 <button class="primary" :disabled="busy||working||available!==true||!question.trim()||!snapshot||!confirm||!validSelection" @click="submit">{{busy?'正在调查…':'开始调查'}}</button>
 <details class="target-management"><summary>管理接入项目与资料</summary><label>项目名称<input v-model="name" maxlength="100"></label><label>Compose 项目名<input v-model="project" placeholder="例如 qinghe-after-sales"></label><label>允许读取状态的服务（逗号分隔）<input v-model="services"></label><label>允许读取日志的服务（逗号分隔，可留空）<input v-model="logs"></label><button class="secondary" :disabled="busy||working||!name.trim()||!project.trim()" @click="register">登记本机项目</button><hr><label>当前项目资料标题<input v-model="docTitle" maxlength="200"></label><label>资料正文<textarea v-model="docText" rows="4" maxlength="200000" placeholder="粘贴脱敏后的 Markdown 或文本资料"></textarea></label><button class="secondary" :disabled="busy||working||!target||!docTitle.trim()||!docText.trim()" @click="document">保存项目资料</button></details>
</article>
</template>
<style scoped>.external-form{display:grid;gap:12px}.external-form label{margin:0}.external-form select,.external-form input:not([type=checkbox]){width:100%;padding:10px;border:1px solid #d6dee8;border-radius:7px;background:white}.external-form .primary{justify-self:start}.preview{background:#f4f9f8;border:1px solid #d4e5df;border-radius:9px;padding:18px}.preview-item{margin:12px 0;padding-bottom:12px;border-bottom:1px solid #dce8e3}.preview-item label,.send-confirm{display:flex;align-items:flex-start;gap:8px;font-size:13px}.preview pre{max-height:280px;overflow:auto;white-space:pre-wrap;overflow-wrap:anywhere;padding:12px;background:white}.external-form details>summary{cursor:pointer;color:#496b68;font-size:13px;padding:8px 0}.target-management{display:grid}.external-form details label{display:block;margin:12px 0 6px}@media(max-width:700px){.external-form .heading{align-items:flex-start;flex-direction:column}}</style>
