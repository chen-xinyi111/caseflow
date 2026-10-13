<script setup lang="ts">
import {ref,onMounted} from 'vue'
import {request} from '../api/client'
import type {components} from '../api/schema'
type Target=components['schemas']['ExternalTarget']
type Snapshot=components['schemas']['ExternalSnapshotSummary']
type Task={id:string,created_at:string,status:string,input:{target:string,question:string},report:{summary:string}|null}
const emit=defineEmits<{navigate:[section:string]}>()
const targets=ref<Target[]>([]),recent=ref<Task[]>([]),snapshots=ref<Record<string,Snapshot|null>>({}),error=ref(''),busy=ref(false)
const statuses=ref<Record<string,components['schemas']['ExternalStatus']>>({})
const when=(v:string)=>new Date(v).toLocaleString('zh-CN',{hour12:false})
async function load(){busy.value=true;error.value='';try{
 targets.value=await request<Target[]>('/external-targets')
 recent.value=(await request<Task[]>('/investigations?view=daily')).slice(0,5)
 await Promise.all(targets.value.map(async t=>{const [saved,state]=await Promise.all([request<Snapshot[]>('/external-targets/'+t.id+'/snapshots'),request<typeof statuses.value[string]>('/external-targets/'+t.id+'/status')]);snapshots.value[t.id]=saved[0]??null;statuses.value[t.id]=state}))
}catch(e){error.value=e instanceof Error?e.message:String(e)}finally{busy.value=false}}
function start(id?:string){if(id)localStorage.setItem('opslens-external-target',id);emit('navigate','investigation')}
function open(t:Task){localStorage.setItem('opslens-investigation-last',t.id);emit('navigate','investigation')}
onMounted(load)
</script>
<template>
<div class="workbench">
 <div v-if="error" role="alert" class="alert">{{error}}</div>
 <article class="panel start-panel"><div><h2>遇到问题，从这里开始</h2><p class="muted">选择项目 → 描述问题 → 查看结论与依据</p></div><button class="primary" @click="start()">开始调查</button></article>
 <div class="heading"><h2>已接入项目</h2><button class="text-button" :disabled="busy" @click="load">刷新</button></div>
 <div class="lab-grid"><article class="panel" v-for="t in targets" :key="t.id"><div class="heading"><h3>{{t.name}}</h3><span class="pill">只读接入</span></div><p class="muted">{{t.compose_project}} · {{t.services.length}} 个登记服务</p><p v-if="busy&&!statuses[t.id]" class="muted">正在读取状态…</p><template v-if="statuses[t.id]"><p v-if="statuses[t.id]?.error" class="inline-error">状态暂不可用</p><p v-else-if="statuses[t.id]?.missing_services.length" class="muted">未发现容器：{{statuses[t.id]?.missing_services.join('、')}}</p><p v-for="s in statuses[t.id]?.services" :key="s.service">{{s.service}} · {{s.state==='running'?'运行中':s.state==='exited'?'已停止':s.state}}{{s.health==='unhealthy'?' · 健康检查异常':''}}</p></template><p v-if="snapshots[t.id]" class="muted">最近资料 {{when(snapshots[t.id]!.captured_at)}}</p><p v-else class="muted">尚未采集资料。</p><button class="text-button" @click="start(t.id)">调查此项目 →</button></article></div>
 <article class="panel"><div class="heading"><h2>最近调查</h2><span class="muted">{{recent.length}} 份</span></div><div class="record-list"><button v-for="t in recent" :key="t.id" @click="open(t)"><strong>{{t.report?.summary??t.input.question}}</strong><span>{{when(t.created_at)}} · {{t.status==='completed'?'报告已完成':t.status==='running'?'调查中':t.status==='queued'?'等待调查':'调查未完成'}}</span></button><p class="muted" v-if="!recent.length">还没有日常调查记录。历史演示和评测可在“维护与记录”中查看。</p></div></article>
</div>
</template>
<style scoped>.workbench{display:grid;gap:20px}.start-panel{display:flex;align-items:center;justify-content:space-between;gap:20px}.start-panel h2{font-size:22px}.record-list{display:grid;gap:10px}.record-list button{display:grid;gap:8px;text-align:left;background:white;border:1px solid #dce5e6;border-radius:8px;padding:14px}.record-list span{font-size:12px;color:#617589}@media(max-width:700px){.start-panel{align-items:flex-start;flex-direction:column}}</style>
