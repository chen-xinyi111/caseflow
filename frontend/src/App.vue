<script setup lang="ts">
import {ref,onMounted,watch} from 'vue'
import Diagnostic from './components/Diagnostic.vue'
import DiagnosticSettings from './components/DiagnosticSettings.vue'
import LegacyWorkbench from './LegacyWorkbench.vue'
import {request} from './api/client'
import type {components} from './api/schema'
type Session=components['schemas']['Session']
const pages:Record<string,string>={diagnostic:'排查',records:'记录',settings:'设置'}
const remembered=localStorage.getItem('opslens-main-section')
const section=ref(remembered&&[...Object.keys(pages),'maintenance'].includes(remembered)?remembered:'diagnostic')
const chosen=ref<string|undefined>(undefined),records=ref<Session[]>([]),error=ref('')
async function loadRecords(){try{records.value=await request<Session[]>('/diagnostic/sessions');error.value=''}catch(e){error.value=e instanceof Error?e.message:String(e)}}
function open(s:Session){chosen.value=s.id;localStorage.setItem('opslens-diagnostic-session',s.id);section.value='diagnostic'}
watch(section,value=>{if(value!=='diagnostic')chosen.value=undefined;localStorage.setItem('opslens-main-section',value);if(value==='records')void loadRecords()})
onMounted(()=>{if(section.value==='records')void loadRecords()})
</script>
<template>
<template v-if="section==='maintenance'"><LegacyWorkbench/><button class="return-main" @click="section='diagnostic'">← 返回排查助手</button></template>
<div v-else class="shell diagnostic-shell">
 <aside><a class="brand" href="/" @click.prevent="section='diagnostic'"><span class="mark">O</span><span>OpsLens</span></a><p class="workspace">后端服务与部署排障</p><nav aria-label="主导航"><button v-for="(name,key) in pages" :key="key" :aria-label="name" :class="{active:section===key}" @click="section=key">{{name}}</button></nav><div class="aside-bottom"><button class="maintenance-link" @click="section='maintenance'">旧版与评测</button><span>单人本机 · 只读分析</span></div></aside>
 <main><header><span>OpsLens <i>/</i> {{pages[section]}}</span><span class="local">● 本机</span></header><div class="content"><div class="page-title"><div><h1>{{section==='diagnostic'?'从报错开始，逐步定位问题。':pages[section]}}</h1><p>{{section==='diagnostic'?'粘贴报错或添加日志，先看关键线索，再决定下一步。':section==='records'?'保存证据、检查过程和用户反馈。':'模型连接、请求预算与官方文档范围。'}}</p></div></div>
 <Diagnostic v-if="section==='diagnostic'" :key="chosen??'current'" :initial-id="chosen" @settings="section='settings'"/>
 <DiagnosticSettings v-if="section==='settings'"/>
 <article v-if="section==='records'" class="panel"><div class="heading"><h2>排查记录</h2><button class="text-button" @click="loadRecords">刷新</button></div><p class="alert" role="alert" v-if="error">{{error}}</p><div class="session-list"><button v-for="s in records" :key="s.id" @click="open(s)"><strong>{{s.problem||s.project||'日志排查'}}</strong><span>{{new Date(s.created_at).toLocaleString('zh-CN',{hour12:false})}} · {{s.status==='solved'?'用户反馈已解决':s.status==='paused'?'已暂停':'仍在排查'}}</span></button><p class="muted" v-if="!records.length">还没有排查记录。添加日志后会自动保存。</p></div></article>
 <footer>OpsLens <span>证据与候选原因分开，修复由你决定。</span></footer></div></main>
</div>
</template>
<style scoped>.return-main{position:fixed;right:20px;bottom:20px;z-index:20;padding:12px 16px;border:1px solid #207d70;background:#207d70;color:white;border-radius:8px}.session-list{display:grid;gap:12px}.session-list button{display:grid;gap:10px;text-align:left;padding:16px;background:white;border:1px solid #dbe4e5;border-radius:8px}.session-list span{font-size:12px;color:#678080}@media(max-width:700px){.diagnostic-shell nav{grid-template-columns:repeat(3,minmax(0,1fr))}.return-main{right:12px;bottom:12px}.page-title h1{font-size:25px}}</style>
