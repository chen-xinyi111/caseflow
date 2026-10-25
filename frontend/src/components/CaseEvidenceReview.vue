<script setup lang="ts">
defineProps<{code:string}>()
const model=defineModel<{fields:string[],note:string}>({required:true})
const labels=[['preconditions','前置条件'],['data','输入数据'],['steps','操作步骤'],['expected','预期结果']]
</script>
<template>
<section class="case-human-review"><strong>{{code}} · 逐例人工核对</strong><p class="muted">模型复核未完成、有疑点或内容已修改时，逐项核对当前证据；缺失业务规则和明确矛盾仍需先解决。</p><label class="check" v-for="[value,label] in labels" :key="value"><input type="checkbox" v-model="model.fields" :value="value">已核对{{label}}</label><label>此例带依据的人工解决说明<textarea v-model="model.note" rows="2" maxlength="2000"></textarea></label></section>
</template>
<style scoped>.case-human-review{padding:14px;border:1px solid #dfdbc5;border-radius:8px;margin:14px 0}.case-human-review .check{margin:7px 0}</style>
