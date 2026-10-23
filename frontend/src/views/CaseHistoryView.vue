<template>
  <div class="page">
    <el-page-header class="header" @back="goBack">
      <template #content>
        <span class="title">执行历史</span>
        <el-tag size="small" effect="plain" class="case-tag">{{ caseId }}</el-tag>
      </template>
      <template #extra>
        <el-button @click="goBack">返回执行结果</el-button>
      </template>
    </el-page-header>

    <div v-loading="caseLoading" class="case-summary">
      <div class="case-title">{{ testcase?.title || '该用例的历史执行记录' }}</div>
      <div class="case-meta">
        <span>用例 {{ caseId }}</span>
        <span v-if="testcase?.module">模块：{{ testcase.module }}</span>
        <span v-if="testcase?.priority">优先级：{{ testcase.priority }}</span>
        <span v-if="testcase?.type">类型：{{ testcase.type }}</span>
      </div>
    </div>

    <ExecutionsTab :project-id="projectId" :case-id="caseId" history-mode />
  </div>
</template>

<script setup>
import { computed, onMounted, ref, watch } from 'vue'
import { useRoute, useRouter } from 'vue-router'
import ExecutionsTab from '../components/ExecutionsTab.vue'
import { getTestcase } from '../api/testcases'

const route = useRoute()
const router = useRouter()
const projectId = computed(() => String(route.params.id || ''))
const caseId = computed(() => String(route.params.caseId || ''))
const testcase = ref(null)
const caseLoading = ref(false)

function goBack() {
  router.push({
    name: 'ProjectDetail',
    params: { id: projectId.value },
    query: { tab: 'executions' },
  })
}

async function loadCase() {
  if (!projectId.value || !caseId.value) return
  caseLoading.value = true
  try {
    testcase.value = await getTestcase(projectId.value, caseId.value)
  } catch {
    testcase.value = null
  } finally {
    caseLoading.value = false
  }
}

onMounted(loadCase)
watch(() => [projectId.value, caseId.value], loadCase)
</script>

<style scoped>
.page {
  display: flex;
  flex-direction: column;
  gap: 14px;
}
.header {
  padding: 12px 16px;
  background: #fff;
  border-radius: 8px;
}
.title {
  font-weight: 600;
  font-size: 16px;
}
.case-tag {
  margin-left: 8px;
}
.case-summary {
  min-height: 64px;
  padding: 14px 16px;
  background: #fff;
  border: 1px solid var(--el-border-color-light);
  border-radius: 8px;
}
.case-title {
  font-size: 15px;
  font-weight: 600;
}
.case-meta {
  display: flex;
  flex-wrap: wrap;
  gap: 16px;
  margin-top: 6px;
  color: var(--el-text-color-secondary);
  font-size: 13px;
}
</style>
