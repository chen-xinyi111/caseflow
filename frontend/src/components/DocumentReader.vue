<script setup lang="ts">
import {computed,nextTick,onMounted,onUnmounted,ref,watch} from 'vue'
import {highlightParts,segmentQuote,type Segment,type Unit} from '../caseflowSources'
const props=defineProps<{projectId:string,documentId:string,evidenceId?:string,legacy?:{page:number,quote?:string}}>()
const emit=defineEmits<{close:[],located:[id:string]}>()
const doc=ref<any>(null),evidence=ref<any>(null),error=ref(''),notice=ref(''),loading=ref(false),unitId=ref(''),candidates=ref<any[]>([])
const selection=ref<Segment|null>(null),pending=ref<Segment[]>([])
const units=computed<Unit[]>(()=>doc.value?.snapshot.units??[])
const activeUnit=computed(()=>units.value.find(u=>u.id===unitId.value)??units.value[0])
const link=computed(()=>evidence.value?new URL(evidence.value.viewer_url,window.location.origin).href:'')
const originalUrl=computed(()=>'/api/v1/caseflow/projects/'+props.projectId+'/documents/'+props.documentId+'/original')
let generation=0,previousOverflow=''
async function api(suffix:string,body?:unknown){
 const response=await fetch('/api/v1/caseflow/projects/'+props.projectId+suffix,body===undefined?{}:{method:'POST',headers:{'Content-Type':'application/json'},body:JSON.stringify(body)})
 const value=await response.json();if(!response.ok)throw Error(typeof value.detail==='string'?value.detail:'定位请求无效');return value
}
async function pin(segments:Segment[],quote:string){
 const current=generation;error.value=''
 try{const value=await api('/evidence',{document_id:props.documentId,snapshot_sha256:doc.value.snapshot_sha256,segments,quote});if(current!==generation)return;evidence.value=value;candidates.value=[];pending.value=[];selection.value=null;unitId.value=value.segments[0]?.unit_id;emit('located',value.id);await focusHit()}
 catch(e){if(current===generation)error.value=e instanceof Error?e.message:String(e)}
}
async function focusHit(){await nextTick();const pane=document.querySelector<HTMLElement>('.document-reader .reader-text'),mark=pane?.querySelector('mark');if(pane&&mark)pane.scrollTop+=mark.getBoundingClientRect().top-pane.getBoundingClientRect().top-24}
async function load(){
 const current=++generation;loading.value=true;doc.value=null;evidence.value=null;error.value='';notice.value='';selection.value=null;pending.value=[];candidates.value=[]
 try{
  const value=await api('/documents/'+props.documentId);if(current!==generation)return;doc.value=value;unitId.value=value.snapshot.units.find((u:Unit)=>u.page===(props.legacy?.page??1))?.id??value.snapshot.units[0]?.id??''
  if(props.evidenceId){const found=await api('/evidence/'+props.evidenceId);if(current!==generation)return;if(found.document_id!==props.documentId||found.snapshot_sha256!==value.snapshot_sha256)throw Error('引用与当前文档版本不一致');evidence.value=found;unitId.value=found.segments[0]?.unit_id;await focusHit()}
  else if(props.legacy?.quote){
   const resolved=await api('/evidence/resolve-legacy',{document_id:props.documentId,page:props.legacy.page,quote:props.legacy.quote});if(current!==generation)return
   if(resolved.location_status==='unique')await pin(resolved.candidates[0].segments,resolved.candidates[0].quote)
   else if(resolved.candidates.length){candidates.value=resolved.candidates;notice.value='原文出现多次，请核对位置后选择；尚未创建精确定位。'}
   else error.value='未找到该连续原文；保留原文可读，不显示有效高亮。'
  }
 }catch(e){if(current===generation)error.value=e instanceof Error?e.message:String(e)}
 finally{if(current===generation)loading.value=false}
}
watch(()=>[props.projectId,props.documentId,props.evidenceId,props.legacy?.page,props.legacy?.quote],load,{immediate:true})
function capture(unit:Unit,event:MouseEvent){
 const selected=window.getSelection(),element=event.currentTarget as HTMLElement
 if(!selected?.rangeCount||selected.isCollapsed)return
 const range=selected.getRangeAt(0)
 if(!element.contains(range.startContainer)||!element.contains(range.endContainer))return
 const prefix=range.cloneRange();prefix.selectNodeContents(element);prefix.setEnd(range.startContainer,range.startOffset)
 const start=Array.from(prefix.toString()).length,end=start+Array.from(range.toString()).length
 if(Array.from(unit.text).slice(start,end).join('')===range.toString())selection.value={unit_id:unit.id,start,end}
}
function addSegment(){
 if(!selection.value)return
 const next=[...pending.value,selection.value].sort((a,b)=>units.value.findIndex(u=>u.id===a.unit_id)-units.value.findIndex(u=>u.id===b.unit_id)||a.start-b.start)
 try{for(const u of units.value)highlightParts(u.text,next,u.id);pending.value=next;selection.value=null;error.value=''}catch(e){error.value=e instanceof Error?e.message:String(e)}
}
async function copy(){try{await navigator.clipboard.writeText(link.value);notice.value='定位链接已复制。'}catch{notice.value='浏览器未允许复制，请从下方链接框复制。'}}
function candidateContext(c:any){const chars=Array.from(doc.value.snapshot.pages[(props.legacy?.page??1)-1] as string);return chars.slice(Math.max(0,c.start-24),c.end+24).join('')}
function key(e:KeyboardEvent){
 if(e.key==='Escape'){e.stopImmediatePropagation();emit('close')}
 if(e.key==='Tab'){
  const nodes=Array.from(document.querySelectorAll<HTMLElement>('.document-reader button:not(:disabled),.document-reader a[href],.document-reader select,.document-reader input'))
  if(e.shiftKey&&document.activeElement===nodes[0]){e.preventDefault();nodes.at(-1)?.focus()}else if(!e.shiftKey&&document.activeElement===nodes.at(-1)){e.preventDefault();nodes[0]?.focus()}
 }
}
onMounted(()=>{previousOverflow=document.body.style.overflow;document.body.style.overflow='hidden';window.addEventListener('keydown',key,true);void nextTick(()=>document.querySelector<HTMLElement>('.document-reader .reader-close')?.focus())})
onUnmounted(()=>{generation++;document.body.style.overflow=previousOverflow;window.removeEventListener('keydown',key,true)})
</script>
<template>
<div class="overlay source-overlay" @click.self="emit('close')"><section class="dialog document-reader" role="dialog" aria-modal="true" :aria-label="doc?.name??'原文阅读与定位'">
 <div class="heading"><h2>{{doc?.name??'原文阅读与定位'}}</h2><button class="close reader-close" @click="emit('close')" aria-label="关闭原文">×</button></div>
 <p v-if="loading" role="status">正在读取已保存的原文版本…</p><p v-if="error" class="message error" role="alert">{{error}}</p><p v-if="notice" class="message" role="status">{{notice}}</p>
 <template v-if="doc">
  <p>版本{{doc.version_number}} · {{doc.format==='pdf'?'PDF真实页码':doc.format==='legacy_text'?'历史文本段，非物理页码':'段落定位，非物理页码'}}<strong v-if="doc.historical"> · 存在更新版本，当前仍为历史原文</strong></p>
  <p class="muted">规范提取文本可能经过脱敏，与原件版式不同。位置校验不代表语义支持或人工审核通过。</p>
  <p v-if="!doc.original_available" class="message">历史文本，原文件未保存。</p>
  <p v-for="w in doc.warnings" :key="w" class="message">{{w}}</p>
  <div class="actions"><a v-if="doc.original_available" :href="originalUrl" target="_blank" rel="noopener">{{doc.format==='pdf'?'打开PDF原件':'下载原件'}}</a><a v-if="doc.source_url" :href="doc.source_url" target="_blank" rel="noopener noreferrer">来源网址</a>
   <label>阅读位置<select v-model="unitId"><option v-for="u in units" :key="u.id" :value="u.id">{{u.label}}</option></select></label><button v-if="evidence" @click="copy">复制定位链接</button></div>
  <input v-if="link" class="source-link" :value="link" readonly aria-label="稳定定位链接" @focus="($event.target as HTMLInputElement).select()">
  <p class="muted">快照 {{doc.snapshot_sha256.slice(0,16)}}… · {{doc.parser_signature}} · 索引 {{doc.index_status}}</p>
  <div v-if="candidates.length" class="reader-candidates"><p>原文重复，选择具体位置：</p><button v-for="(c,i) in candidates" :key="i" @click="pin(c.segments,c.quote)">位置{{i+1}} · {{c.label}} · 字符{{c.start}}—{{c.end}}<small>{{candidateContext(c)}}</small></button></div>
  <div class="reader-columns" :class="{'with-pdf':doc.format==='pdf'&&doc.original_available}">
   <iframe v-if="doc.format==='pdf'&&doc.original_available" :src="originalUrl+'#page='+(activeUnit?.page_number??1)" title="PDF原件同页预览"></iframe>
   <div class="reader-text"><article v-for="u in units.filter(u=>u.id===unitId||evidence?.segments.some((r:Segment)=>r.unit_id===u.id))" :key="u.id" :data-unit="u.id"><h3>{{u.label}}<small v-if="u.kind==='table_paragraph'"> · 表格段落</small></h3>
    <pre @mouseup="capture(u,$event)"><template v-for="(part,i) in highlightParts(u.text,evidence?.segments??[],u.id)" :key="i"><mark v-if="part.marked">{{part.text}}</mark><span v-else>{{part.text}}</span></template></pre>
   </article></div>
  </div>
  <div class="reader-pin"><p>在文本中选择连续文字，添加一个或多个片段后保存精确定位；不会修改规则或用例。</p><button :disabled="!selection||pending.length>=32" @click="addSegment">添加所选片段</button> <button v-if="pending.length" @click="pin(pending,segmentQuote(units,pending))">保存定位（{{pending.length}}个片段）</button> <button v-if="pending.length" @click="pending=[]">清除片段</button><pre v-if="pending.length">{{segmentQuote(units,pending)}}</pre></div>
 </template>
</section></div>
</template>
<style scoped>
.document-reader{width:min(1200px,96vw);max-width:96vw;padding:24px}.reader-columns{display:grid;gap:16px}.reader-columns.with-pdf{grid-template-columns:minmax(0,1fr) minmax(0,1fr)}.reader-columns iframe{width:100%;height:55vh;border:1px solid #ddd}.reader-text{min-width:0;max-height:55vh;overflow:auto}.reader-text pre,.reader-pin pre{white-space:pre-wrap;overflow-wrap:anywhere;word-break:break-word;font:inherit;line-height:1.8}.reader-text mark{background:#fff0a6;color:#241e06}.source-link{width:100%;box-sizing:border-box}.reader-candidates{display:grid;gap:8px}.reader-candidates button{text-align:left;white-space:normal;overflow-wrap:anywhere}.reader-candidates small{display:block}.reader-pin{border-top:1px solid #ddd;margin-top:16px;padding-top:12px}.document-reader .actions{flex-wrap:wrap}.document-reader select{max-width:100%}@media(max-width:650px){.document-reader{padding:14px}.reader-columns.with-pdf{grid-template-columns:minmax(0,1fr)}.reader-columns iframe{height:28vh}.reader-text{max-height:40vh}}
</style>
