<script setup lang="ts">
import {computed,ref} from 'vue'
const props=defineProps<{pending:any[],requirements:any[],selections:Record<string,string[]>,busy:boolean,sourceLabel?:(citation:any)=>string}>()
defineEmits<{select:[id:string,target:string],decide:[change:any,action:string],source:[citation:any]}>()
const selected=ref('')
const current=computed(()=>props.pending.find(c=>c.id===selected.value)??props.pending[0])
const target=computed(()=>props.requirements.find(r=>r.id===props.selections[current.value?.id]?.[0]))
const label:Record<string,string>={unchanged:'内容一致，核对资料来源',added:'新规则',possible_change:'需要核对的变化',possibly_removed:'新资料中未找到的规则'}
</script>
<template>
<section class="card" id="workflow-changes">
 <div class="heading"><h2>核对规则变化</h2><small>{{pending.length}} 项待处理</small></div>
 <p v-if="!pending.length" class="empty">规则变化已处理。继续确认修改后的规则，再维护受影响的检查项。</p>
 <template v-if="current">
  <p class="muted">一次处理一项。先看新内容，再选择对应的原规则；不要仅凭候选关联直接确认。</p>
  <label>当前查看<select :value="current.id" @change="selected=($event.target as HTMLSelectElement).value"><option v-for="(c,i) in pending" :key="c.id" :value="c.id">{{i+1}} / {{pending.length}} · {{c.proposed?.text??'可能移除的规则'}}</option></select></label>
  <div class="change-new"><small>{{label[current.kind]??'规则变化'}}</small><h3>{{current.proposed?.text??'新资料中未找到对应内容，请核对原文后决定是否移除。'}}</h3><button v-for="(q,i) in current.proposed?.citations??[]" :key="String(i)" class="text-button" @click="$emit('source',q)">查看新资料 · {{sourceLabel?.(q)??'原文位置'}}</button></div>
  <label>这项变化对应哪条原规则？<select :value="selections[current.id]?.[0]??''" @change="$emit('select',current.id,($event.target as HTMLSelectElement).value)"><option value="">请核对后选择</option><option v-for="id in current.old_ids" :key="id" :value="id">{{requirements.find(r=>r.id===id)?.code}} · {{requirements.find(r=>r.id===id)?.text}}</option></select></label>
  <div class="change-old" v-if="target"><small>修改前 · {{target.code}}</small><p>{{target.text}}</p></div>
  <div class="actions"><button v-if="current.proposed" class="primary" :disabled="busy||!target" @click="$emit('decide',current,'replace')">保存这项修改</button><button v-else class="primary" :disabled="busy||!target" @click="$emit('decide',current,'remove')">确认移除这条规则</button><button v-if="current.proposed" class="secondary" :disabled="busy" @click="$emit('decide',current,'add')">作为新规则添加</button><button class="text-button" :disabled="busy" @click="$emit('decide',current,'reject')">本项不更新</button></div>
  <p class="muted">仅改写表述、业务规则没有变化时，可以保留原内容。保存修改后还需核对确认，关联检查项会保留并转为待复审。</p>
 </template>
</section>
</template>
<style scoped>.change-new{margin:18px 0;background:#f0f7f2;border:1px solid #dce9df;padding:18px;border-radius:8px}.change-new h3{font-size:15px;line-height:1.8;font-weight:500;margin:8px 0;overflow-wrap:anywhere}.change-old{padding:16px;background:#f7f8f7;border-radius:8px}.change-old p{font-size:14px;line-height:1.8;margin:6px 0}small{color:#78907e}</style>
