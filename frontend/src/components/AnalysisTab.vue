<template>
  <div v-loading="loading" class="analysis-tab">
    <div class="toolbar">
      <el-button type="primary" :loading="running" :disabled="status === 'running'" @click="trigger">
        <el-icon class="mr4"><Refresh /></el-icon>{{ status === 'running' ? '分析中…' : '触发分析' }}
      </el-button>
      <el-tag v-if="status" :type="tagType(status)">{{ statusText }}</el-tag>
      <span v-if="status === 'running'" class="hint">正在轮询分析进度，可继续浏览其他标签页</span>
      <span v-if="errorMessage" class="err">{{ errorMessage }}</span>
    </div>

    <el-alert v-for="w in warnings" :key="w" :title="w" type="warning" :closable="false" class="warn" />

    <template v-if="backend || frontend">
      <!-- 接口端点 -->
      <el-card v-if="backend?.endpoints?.length" shadow="never" class="mod-card">
        <template #header>
          <div class="card-header">
            <el-icon color="#409eff"><Link /></el-icon>
            <span class="card-title">接口端点</span>
            <el-tag size="small" type="primary">{{ backend.endpoints.length }}</el-tag>
          </div>
        </template>
        <el-table :data="backend.endpoints" size="small" border>
          <el-table-column prop="method" label="方法" width="90">
            <template #default="{ row }">
              <el-tag size="small" :type="methodTag(row.method)">{{ row.method }}</el-tag>
            </template>
          </el-table-column>
          <el-table-column prop="path" label="路径" />
          <el-table-column prop="controller" label="控制器" width="150" />
          <el-table-column prop="methodName" label="方法名" width="130" />
          <el-table-column label="位置" width="230">
            <template #default="{ row }">{{ row.file }}:{{ row.line }}</template>
          </el-table-column>
        </el-table>
      </el-card>

      <!-- 状态机 -->
      <el-card v-if="machines.length" shadow="never" class="mod-card">
        <template #header>
          <div class="card-header">
            <el-icon color="#67c23a"><Connection /></el-icon>
            <span class="card-title">状态机</span>
            <el-tag size="small" type="success">{{ totalTransitions }} 条转换</el-tag>
          </div>
        </template>
        <template v-for="m in machines" :key="m.id">
          <div class="sm-head">
            <span class="sm-name">{{ m.name }}</span>
            <span class="sm-states">{{ m.states.map((s) => `${s.name}(${s.label})`).join(' → ') }}</span>
          </div>
          <el-table :data="m.transitions" size="small" border>
            <el-table-column prop="from" label="来源态" width="130" />
            <el-table-column label="" width="40">
              <template #default><span class="arrow">→</span></template>
            </el-table-column>
            <el-table-column prop="to" label="目标态" width="130" />
            <el-table-column prop="trigger" label="触发动作" width="110" />
            <el-table-column label="证据" width="110">
              <template #default="{ row }">
                <el-tag :type="row.verified ? 'success' : 'warning'" size="small">{{ row.verified ? 'verified' : '未证实' }}</el-tag>
              </template>
            </el-table-column>
            <el-table-column label="证据位置">
              <template #default="{ row }">{{ (row.evidence || []).join('；') }}</template>
            </el-table-column>
          </el-table>
        </template>
      </el-card>

      <!-- 业务规则 -->
      <el-card v-if="backend?.businessRules?.length" shadow="never" class="mod-card">
        <template #header>
          <div class="card-header">
            <el-icon color="#f56c6c"><Warning /></el-icon>
            <span class="card-title">业务规则</span>
            <el-tag size="small" type="danger">{{ backend.businessRules.length }}</el-tag>
          </div>
        </template>
        <el-table :data="backend.businessRules" size="small" border>
          <el-table-column prop="description" label="规则描述" width="320" />
          <el-table-column prop="guard" label="守卫条件" />
          <el-table-column label="位置" width="220">
            <template #default="{ row }">{{ row.file }}:{{ row.line }}</template>
          </el-table-column>
        </el-table>
      </el-card>

      <!-- 实体 + 枚举 -->
      <el-row :gutter="16">
        <el-col :span="12">
          <el-card v-if="backend?.entities?.length" shadow="never" class="mod-card">
            <template #header>
              <div class="card-header">
                <el-icon color="#e6a23c"><Grid /></el-icon>
                <span class="card-title">实体</span>
                <el-tag size="small" type="warning">{{ backend.entities.length }}</el-tag>
              </div>
            </template>
            <div v-for="e in backend.entities" :key="e.name" class="entity-block">
              <div class="entity-name">{{ e.name }}</div>
              <div class="entity-fields">
                <el-tag v-for="f in e.fields" :key="f.name" size="small" class="tag" type="info">{{ f.name }}</el-tag>
              </div>
              <div v-if="e.fieldConstraints?.length" class="entity-constraints">
                <el-tag v-for="c in e.fieldConstraints" :key="c.field + c.rule" size="small" class="tag">
                  {{ c.field }}: {{ c.rule }}{{ c.value != null ? `=${c.value}` : '' }}
                </el-tag>
              </div>
            </div>
          </el-card>
        </el-col>
        <el-col :span="12">
          <el-card v-if="backend?.enums?.length" shadow="never" class="mod-card">
            <template #header>
              <div class="card-header">
                <el-icon color="#909399"><Collection /></el-icon>
                <span class="card-title">枚举</span>
                <el-tag size="small" type="info">{{ backend.enums.length }}</el-tag>
              </div>
            </template>
            <div v-for="e in backend.enums" :key="e.name" class="entity-block">
              <div class="entity-name">{{ e.name }}</div>
              <div class="entity-fields">
                <el-tag v-for="v in e.values" :key="v.name" size="small" class="tag">{{ v.name }}（{{ v.label }}）</el-tag>
              </div>
            </div>
          </el-card>
        </el-col>
      </el-row>

      <!-- 页面与表单 -->
      <el-card v-if="frontend?.pages?.length" shadow="never" class="mod-card">
        <template #header>
          <div class="card-header">
            <el-icon color="#409eff"><Monitor /></el-icon>
            <span class="card-title">页面与表单</span>
            <el-tag size="small" type="primary">{{ frontend.pages.length }}</el-tag>
          </div>
        </template>
        <el-table :data="frontend.pages" size="small" border>
          <el-table-column prop="name" label="页面" width="150" />
          <el-table-column prop="route" label="路由" width="180" />
          <el-table-column label="表单字段">
            <template #default="{ row }">
              <span v-for="form in row.forms" :key="form">
                <el-tag v-for="f in form.fields" :key="f.prop || f.label" size="small" class="tag">{{ f.label || f.prop }}</el-tag>
              </span>
            </template>
          </el-table-column>
        </el-table>
      </el-card>

      <!-- 选择器 + 文案 -->
      <el-card v-if="frontend?.selectors?.length || frontend?.uiTexts?.length" shadow="never" class="mod-card">
        <template #header>
          <div class="card-header">
            <el-icon color="#909399"><Aim /></el-icon>
            <span class="card-title">元素定位与文案</span>
            <el-tag size="small" type="info">{{ (frontend.selectors?.length || 0) + (frontend.uiTexts?.length || 0) }}</el-tag>
          </div>
        </template>
        <div class="sub-title">选择器（{{ frontend.selectors?.length || 0 }}）</div>
        <el-tag v-for="s in frontend.selectors" :key="s.type + s.value" size="small" class="tag" type="info">{{ s.type }}: {{ s.value }}</el-tag>
        <div class="sub-title mt">UI 文案（{{ frontend.uiTexts?.length || 0 }}）</div>
        <el-tag v-for="t in frontend.uiTexts" :key="t" size="small" class="tag" type="success">{{ t }}</el-tag>
      </el-card>
    </template>

    <el-empty v-if="!backend && !frontend && status === 'success'" description="未识别到任何源码事实" />
  </div>
</template>

<script setup>
import { computed, onBeforeUnmount, onMounted, ref } from 'vue'
import { ElMessage } from 'element-plus'
import { getAnalysis, triggerAnalysis } from '../api/analysis'

const props = defineProps({ projectId: { type: String, required: true } })
const emit = defineEmits(['analyzed'])

const status = ref(null)
const errorMessage = ref('')
const warnings = ref([])
const backend = ref(null)
const frontend = ref(null)
const machines = ref([])
const loading = ref(false)
const running = ref(false)
let timer = null

const totalTransitions = computed(() => machines.value.reduce((n, m) => n + (m.transitions?.length || 0), 0))
const STATUS_TEXT = { success: '分析完成', failed: '分析失败', running: '分析中', pending: '待分析' }
const statusText = computed(() => STATUS_TEXT[status.value] || '未分析')

async function load(silent = false) {
  if (!silent) loading.value = true
  try {
    const data = await getAnalysis(props.projectId, silent ? { silent: true } : undefined)
    status.value = data.status
    errorMessage.value = data.errorMessage || ''
    warnings.value = data.warnings || []
    backend.value = data.backendResult
    frontend.value = data.frontendResult
    machines.value = data.stateMachines || []
  } finally {
    if (!silent) loading.value = false
  }
}

async function trigger() {
  running.value = true
  try {
    await triggerAnalysis(props.projectId)
    status.value = 'running'
    poll()
  } finally {
    running.value = false
  }
}

function poll() {
  clearTimer()
  timer = setInterval(async () => {
    try {
      await load(true)
    } catch {
      return // 轮询失败不打断，等下一轮
    }
    if (status.value === 'success' || status.value === 'failed') {
      clearTimer()
      if (status.value === 'success') ElMessage.success('分析完成')
      else ElMessage.error(errorMessage.value || '分析失败')
      emit('analyzed', status.value)
    }
  }, 1500)
}

function clearTimer() {
  if (timer) {
    clearInterval(timer)
    timer = null
  }
}

function tagType(s) {
  return { success: 'success', failed: 'danger', running: 'warning', pending: 'info' }[s] || 'info'
}

function methodTag(m) {
  return { GET: 'success', POST: 'primary', PUT: 'warning', DELETE: 'danger', PATCH: 'info' }[m] || 'info'
}

onMounted(async () => {
  try {
    await load()
  } catch {
    return
  }
  // 打开页面时若后端仍在分析，继续轮询，避免状态永远停在「分析中」
  if (status.value === 'running') poll()
})
onBeforeUnmount(clearTimer)
</script>

<style scoped>
.analysis-tab { max-width: 1400px; }
.toolbar { display: flex; align-items: center; gap: 12px; margin-bottom: 12px; }
.mr4 { margin-right: 4px; }
.err { color: var(--el-color-danger); }
.hint { color: var(--el-text-color-secondary); font-size: 13px; }
.warn { margin-bottom: 8px; }
.mod-card { margin-bottom: 16px; }
.card-header { display: flex; align-items: center; gap: 8px; }
.card-title { font-weight: 600; }
.tag { margin: 2px 4px 2px 0; }
.sm-head { margin-bottom: 6px; }
.sm-name { font-weight: 600; margin-right: 12px; }
.sm-states { color: var(--el-text-color-secondary); font-size: 13px; }
.arrow { color: var(--el-color-primary); font-weight: 700; }
.entity-block { padding: 8px 0; border-bottom: 1px dashed var(--el-border-color-light); }
.entity-block:last-child { border-bottom: none; }
.entity-name { font-weight: 600; margin-bottom: 6px; }
.entity-fields { margin-bottom: 4px; }
.entity-constraints { color: var(--el-text-color-secondary); }
.sub-title { color: var(--el-text-color-secondary); font-size: 13px; margin: 8px 0 4px; }
.mt { margin-top: 12px; }
</style>
