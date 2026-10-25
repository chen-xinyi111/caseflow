<script setup lang="ts">
import {ref,watch} from 'vue'
const props=defineProps<{projectId:string,revision:number}>()
const emit=defineEmits<{read:[document:any]}>()
const rows=ref<any[]>([]),error=ref(''),history=ref(false)
let generation=0
const state=(v:string)=>({not_built:'未建立',ready:'就绪',building:'建立中',failed:'失败',stale:'需重建',legacy:'旧索引',missing:'记录缺失',queued:'排队中',running:'处理中',completed:'完成',blocked:'已阻塞',ready_with_warnings:'完成，需核对提示',legacy_text:'保留历史文本'} as Record<string,string>)[v]??v
watch(()=>[props.projectId,props.revision],async()=>{
 const current=++generation;error.value=''
 try{const response=await fetch('/api/v1/caseflow/projects/'+props.projectId+'/documents');const value=await response.json();if(!response.ok)throw Error(value.detail??'无法读取资料');if(current===generation)rows.value=value}
 catch(e){if(current===generation)error.value=e instanceof Error?e.message:String(e)}
},{immediate:true})
</script>
<template>
<section class="card knowledge-library" aria-label="项目知识资料">
 <div class="heading"><h2>资料与知识</h2><label class="check"><input type="checkbox" v-model="history">显示历史版本</label></div>
 <p class="muted">原件、规范提取文本与索引分别保存。阅读和定位不调用模型。</p>
 <p v-if="error" class="message error" role="alert">{{error}}</p>
 <p v-if="!rows.length&&!error" class="empty">添加资料后，可在这里阅读原文、查看版本和精确引用。</p>
 <article class="document" v-for="d in rows.filter(d=>history||!d.historical)" :key="d.id">
  <div><strong>{{d.name}}</strong><p>{{d.role==='requirement'?'需求说明':'补充参考'}} · {{d.format==='legacy_text'?'历史文本':d.format.toUpperCase()}} · 版本{{d.version_number}}<span v-if="d.historical"> · 历史版本</span></p>
   <small>{{d.original_available?'原件已保存':'历史文本，原文件未保存'}} · 解析：{{state(d.parse_status)}} · 索引：{{state(d.index_status)}}</small>
   <p v-if="d.index_attempt_status">最近索引任务：{{state(d.index_attempt_status)}}</p>
   <p v-if="d.index_attempt_error" class="error-text">{{d.index_attempt_error}}</p>
   <p v-for="w in d.warnings" :key="w" class="error-text">{{w}}</p>
   <p v-if="d.index_error" class="error-text">{{d.index_error}}</p>
   <a v-if="d.source_url" :href="d.source_url" target="_blank" rel="noopener noreferrer">来源网址</a>
  </div><button class="secondary" @click="emit('read',d)">阅读与定位</button>
 </article>
</section>
</template>
