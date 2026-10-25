<script setup lang="ts">
import {ref,watch,onUnmounted} from 'vue'
import {stage4Request,recoverGroup,groupPendingKey} from '../stage4Client'
const props=defineProps<{project:any,refreshKey:number}>()
const emit=defineEmits<{updated:[project:any]}>()
const rows=ref<any[]>([]),error=ref(''),busy=ref(false),progress=ref('')
let timer:ReturnType<typeof setTimeout>|undefined,stream:EventSource|undefined,streamId='',generation=0,disposed=false,lastRevision=0
const labels:Record<string,string>={queued:'排队中',running:'处理中',completed:'完成',partial:'部分完成',blocked:'已阻塞',failed:'失败',stale:'依据已变化',pending:'待处理'}
function follow(g:any){
 const id=g?.items.find((i:any)=>i.status==='running')?.task_id??''
 if(id===streamId)return;stream?.close();streamId=id;progress.value=''
 if(!id)return
 stream=new EventSource('/api/v1/caseflow/tasks/'+id+'/events?after='+(localStorage.getItem('cf-cursor:'+id)??'0'))
 stream.onmessage=event=>{if(/^\d+$/.test(event.lastEventId))localStorage.setItem('cf-cursor:'+id,event.lastEventId);try{progress.value=JSON.parse(event.data).payload?.message??''}catch{}}
}
async function load(){
 const token=++generation,pid=props.project.id;if(timer)clearTimeout(timer)
 try{
  const recovered=await recoverGroup(pid),value=await stage4Request('/projects/'+pid+'/task-groups')
  if(disposed||token!==generation||pid!==props.project.id)return
  rows.value=recovered&&!value.some((g:any)=>g.id===recovered.id)?[recovered,...value]:value;error.value=''
  const revision=Math.max(props.project.revision,...rows.value.map(g=>g.expected_project_revision))
  if(revision>lastRevision){lastRevision=revision;const p=await stage4Request('/projects/'+pid);if(token===generation&&pid===props.project.id)emit('updated',p)}
  follow(rows.value.find(g=>['queued','running'].includes(g.status)))
 }catch(e){error.value=e instanceof Error?e.message:String(e)}
 finally{if(!disposed&&token===generation)timer=setTimeout(load,rows.value.some(g=>['queued','running'].includes(g.status))||localStorage.getItem(groupPendingKey(pid))?1500:5000)}
}
async function resume(g:any){busy.value=true;error.value='';try{await stage4Request('/projects/'+props.project.id+'/task-groups/'+g.id+'/resume',{revision:props.project.revision});await load()}catch(e){error.value=e instanceof Error?e.message:String(e)}finally{busy.value=false}}
watch(()=>props.project.id,()=>{lastRevision=props.project.revision;rows.value=[];stream?.close();streamId='';void load()},{immediate:true})
watch(()=>props.refreshKey,()=>void load())
onUnmounted(()=>{disposed=true;generation++;if(timer)clearTimeout(timer);stream?.close()})
</script>
<template>
<section class="card group-tasks" aria-label="任务中心"><div class="heading"><h2>任务中心</h2><button class="text-button" @click="load">刷新任务</button></div>
 <p v-if="error" class="message error" role="alert">{{error}}</p><p v-if="progress" role="status">{{progress}}</p>
 <p v-if="!rows.length" class="muted">准备依据和提取测试点后，可在这里查看每个批次；离开页面不会取消任务。</p>
 <article v-for="g in rows" :key="g.id" class="group-row"><strong>{{({prepare_basis:'准备依据',extract_points:'提取测试点',expand_cases:'展开详细用例'} as Record<string,string>)[g.kind]??g.kind}} · {{labels[g.status]??g.status}}</strong>
  <p>{{g.input.target}} · 完成 {{g.counts.completed}} / {{g.estimated_leaf_tasks}} · 失败 {{g.counts.failed}} · 待处理 {{g.counts.pending}}</p>
  <p v-if="g.error" class="error-text">{{g.error}}</p><p class="muted">{{g.warning}}</p>
  <button v-if="g.can_resume" class="secondary" :disabled="busy" @click="resume(g)">继续已保存任务</button>
  <details><summary>批次和首次失败记录</summary><article v-for="i in g.items" :key="i.key"><p>{{({prepare_segment:'全文分段',prepare_retrieve:'检索及原文回读',prepare_finalize:'保存准备包',extract_points:'提取测试点',expand_points:'展开详细用例'} as Record<string,string>)[i.kind]}} · {{labels[i.status]??i.status}} <span v-if="i.result?.reused">· 复用相同输入的已完成响应</span></p><p v-if="i.error" class="error-text">{{i.error}}</p><small v-for="(a,n) in i.attempts" :key="n">{{a.status}} {{a.error??''}} </small></article></details>
 </article>
</section>
</template>
<style scoped>.group-row{border-top:1px solid #e0e7e2;padding:12px 0}.group-row p{margin:6px 0}.group-tasks small{overflow-wrap:anywhere}</style>
