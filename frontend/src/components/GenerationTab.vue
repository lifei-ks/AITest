<template>
  <div>
    <el-empty v-if="!loading && !requirements.length" description="还没有需求版本，先创建一期需求文档">
      <el-button type="primary" @click="$emit('go-requirements')">去创建需求版本</el-button>
    </el-empty>

    <template v-else>
      <div class="toolbar">
        <el-select
          :model-value="requirementId"
          placeholder="选择需求版本"
          style="width: 240px"
          :loading="loading"
          @update:model-value="onSelectVersion"
        >
          <el-option
            v-for="r in requirements"
            :key="r.id"
            :label="`${r.version} · ${r.name}`"
            :value="r.id"
          >
            <span class="opt-ver">{{ r.version }}</span>
            <span class="opt-name">{{ r.name }}</span>
            <el-tag size="small" effect="plain" :type="statusTag(r.status)" class="opt-tag">{{ r.statusText }}</el-tag>
          </el-option>
        </el-select>

        <el-radio-group v-model="mode" size="small" :disabled="running">
          <el-radio-button value="auto">自动（LLM 优先）</el-radio-button>
          <el-radio-button value="llm">仅 LLM</el-radio-button>
          <el-radio-button value="rule">仅规则</el-radio-button>
        </el-radio-group>

        <el-button
          type="primary"
          :loading="running"
          :disabled="running || !requirementId || !pointCount"
          @click="start"
        >
          <el-icon class="mr4"><MagicStick /></el-icon>生成用例
        </el-button>

        <el-button v-if="!pointCount" size="small" @click="$emit('go-requirements')">先解析测试点</el-button>
        <el-tag v-if="running" type="warning">生成中… 已生成 {{ cases.length }} 条</el-tag>
        <el-tag v-else-if="stat" type="success">完成 · {{ stat.mode === 'llm' ? 'LLM' : '规则模式' }}</el-tag>
      </div>

      <el-alert
        v-if="requirementId && !pointCount && !loading"
        :title="`当前版本还没有测试点，请到「需求版本」解析需求文档后再生成`"
        type="warning"
        :closable="false"
        class="mb"
      />

      <el-progress
        v-if="running"
        :percentage="progressPercent"
        :stroke-width="6"
        :show-text="false"
        class="progress"
      />
      <div v-if="running" class="progress-text">{{ progressText }}</div>

      <!-- 统计卡 -->
      <el-row v-if="stat" :gutter="16" class="stats">
        <el-col :xs="8" :sm="4"><el-statistic title="生成" :value="stat.generated" /></el-col>
        <el-col :xs="8" :sm="4"><el-statistic title="保留" :value="stat.kept" /></el-col>
        <el-col :xs="8" :sm="4"><el-statistic title="丢弃" :value="stat.dropped" /></el-col>
        <el-col :xs="24" :sm="12">
          <div class="type-tags">
            <el-tag v-for="(n, t) in stat.byType" :key="t" size="small" :type="typeTag(t)" class="tag">
              {{ typeLabel(t) }}：{{ n }}
            </el-tag>
            <el-tag v-if="stat.testPointCount" size="small" type="info">测试点：{{ stat.testPointCount }}</el-tag>
          </div>
        </el-col>
      </el-row>

      <!-- 丢弃原因 -->
      <el-alert
        v-if="stat && stat.dropReasons && Object.values(stat.dropReasons).some((v) => v)"
        type="info"
        :closable="false"
        class="mb"
        :title="`丢弃原因：无预期 ${stat.dropReasons.no_expected || 0}，低分 ${stat.dropReasons.low_score || 0}，去重 ${stat.dropReasons.duplicate || 0}`"
      />

      <!-- 测试点预览 -->
      <el-collapse v-if="pointCount" class="points">
        <el-collapse-item :title="`本版本测试点（${pointCount}）`" name="points">
          <el-table :data="points" size="small" border max-height="300" v-loading="pointsLoading">
            <el-table-column prop="seq" label="#" width="50" />
            <el-table-column prop="title" label="测试点" min-width="220" show-overflow-tooltip />
            <el-table-column prop="priority" label="优先级" width="80" />
            <el-table-column prop="expected" label="预期" min-width="180" show-overflow-tooltip />
          </el-table>
        </el-collapse-item>
      </el-collapse>

      <!-- 本次生成结果 -->
      <el-table v-if="cases.length" :data="cases" border stripe class="table">
        <el-table-column type="index" label="#" width="50" />
        <el-table-column prop="title" label="标题" min-width="240" show-overflow-tooltip />
        <el-table-column label="类型" width="90">
          <template #default="{ row }">
            <el-tag size="small" :type="typeTag(row.type)">{{ typeLabel(row.type) }}</el-tag>
          </template>
        </el-table-column>
        <el-table-column prop="priority" label="优先级" width="80" />
        <el-table-column prop="qualityScore" label="质量分" width="90" />
        <el-table-column label="预期结果" min-width="200" show-overflow-tooltip>
          <template #default="{ row }">{{ (row.expectedResults || []).join(' / ') }}</template>
        </el-table-column>
      </el-table>

      <el-empty
        v-if="!running && !cases.length && pointCount"
        description="点击「生成用例」：LLM 按测试点生成，失败或缺失时自动用规则补齐"
      />
    </template>
  </div>
</template>

<script setup>
import { computed, onBeforeUnmount, onMounted, ref, watch } from 'vue'
import { ElMessage } from 'element-plus'
import { generateRequirementStream, listRequirements, listTestPoints } from '../api/requirements'

const props = defineProps({
  projectId: { type: String, required: true },
  requirementId: { type: String, default: null },
  // 每次进入标签页时由父组件递增，用于刷新版本/测试点
  refreshKey: { type: Number, default: 0 },
})
const emit = defineEmits(['update:requirementId', 'go-requirements', 'generated'])

const STATUS_TAG = { draft: 'info', analyzing: 'warning', ready: 'success', failed: 'danger' }
const requirements = ref([])
const points = ref([])
const loading = ref(false)
const pointsLoading = ref(false)
const running = ref(false)
const mode = ref('auto')
const cases = ref([])
const stat = ref(null)
const progress = ref({ current: 0, total: 0, message: '' })

let es = null

const pointCount = computed(() => points.value.length)
const progressPercent = computed(() => {
  if (!progress.value.total) return running.value ? 30 : 0
  return Math.min(100, Math.round((progress.value.current / progress.value.total) * 100))
})
const progressText = computed(() => progress.value.message || `已生成 ${cases.value.length} 条用例…`)

function statusTag(s) {
  return STATUS_TAG[s] || 'info'
}

async function loadRequirements() {
  loading.value = true
  try {
    requirements.value = await listRequirements(props.projectId)
    const valid = requirements.value.some((r) => r.id === props.requirementId)
    if (!valid) {
      // 默认选最近一个「已解析」的版本，其次选最新版本
      const ready = [...requirements.value].reverse().find((r) => r.status === 'ready')
      const pick = ready || requirements.value[requirements.value.length - 1]
      if (pick) emit('update:requirementId', pick.id)
    }
  } finally {
    loading.value = false
  }
}

async function loadPoints(id) {
  points.value = []
  if (!id) return
  pointsLoading.value = true
  try {
    points.value = await listTestPoints(id)
  } catch {
    points.value = []
  } finally {
    pointsLoading.value = false
  }
}

function onSelectVersion(id) {
  emit('update:requirementId', id)
}

watch(
  () => props.requirementId,
  (id) => {
    cases.value = []
    stat.value = null
    close()
    loadPoints(id)
  },
  { immediate: true },
)

watch(
  () => props.projectId,
  () => {
    requirements.value = []
    points.value = []
    cases.value = []
    stat.value = null
    close()
    loadRequirements()
  },
)

watch(
  () => props.refreshKey,
  async () => {
    await loadRequirements()
    if (props.requirementId) await loadPoints(props.requirementId)
  },
)

function start() {
  if (running.value) return
  if (!props.requirementId) return ElMessage.warning('请先选择需求版本')
  if (!pointCount.value) return ElMessage.warning('该版本还没有测试点，请先解析需求文档')

  cases.value = []
  stat.value = null
  progress.value = { current: 0, total: pointCount.value, message: '正在生成…' }
  running.value = true

  try {
    es = generateRequirementStream(props.requirementId, mode.value)
  } catch (e) {
    running.value = false
    ElMessage.error(e.message || '无法建立生成连接')
    return
  }

  es.addEventListener('started', () => {})

  es.addEventListener('progress', (e) => {
    try {
      const data = JSON.parse(e.data)
      progress.value = {
        current: data.current || 0,
        total: data.total || pointCount.value,
        message: data.message || '',
      }
    } catch {
      /* 忽略单条进度解析失败 */
    }
  })

  es.addEventListener('case', (e) => {
    try {
      const data = JSON.parse(e.data)
      if (data.testCase) cases.value.push(data.testCase)
    } catch {
      /* 忽略单条用例解析失败 */
    }
  })

  es.addEventListener('complete', (e) => {
    try {
      stat.value = JSON.parse(e.data)
    } catch {
      stat.value = null
    }
    close()
    if (stat.value) {
      ElMessage.success(
        `生成完成：保留 ${stat.value.kept} 条（${stat.value.mode === 'llm' ? 'LLM' : '规则模式'}）`,
      )
      emit('generated', stat.value)
    }
  })

  es.addEventListener('error', (e) => {
    let message = ''
    try {
      message = e.data ? JSON.parse(e.data)?.message || '' : ''
    } catch {
      /* 连接层错误无 data */
    }
    if (message) ElMessage.error(message)
    close()
  })

  es.onerror = () => {
    if (!es) return
    ElMessage.error('生成连接中断，请重试')
    close()
  }
}

function close() {
  running.value = false
  if (es) {
    es.close()
    es = null
  }
}

function typeTag(t) {
  return { positive: 'success', negative: 'danger', boundary: 'warning', state: 'primary' }[t] || 'info'
}
function typeLabel(t) {
  return { positive: '正例', negative: '反例', boundary: '边界', state: '流转' }[t] || t
}

onMounted(loadRequirements)
onBeforeUnmount(close)
</script>

<style scoped>
.toolbar {
  display: flex;
  align-items: center;
  gap: 12px;
  margin-bottom: 12px;
  flex-wrap: wrap;
}
.mr4 {
  margin-right: 4px;
}
.mb {
  margin-bottom: 10px;
}
.progress {
  margin-bottom: 4px;
}
.progress-text {
  color: var(--el-text-color-secondary);
  font-size: 12px;
  margin-bottom: 10px;
}
.stats {
  margin-bottom: 12px;
}
.type-tags {
  display: flex;
  gap: 8px;
  padding-top: 8px;
  flex-wrap: wrap;
}
.tag {
  margin-right: 4px;
}
.points {
  margin-bottom: 12px;
}
.table {
  margin-top: 8px;
}
.opt-ver {
  font-weight: 600;
  margin-right: 6px;
}
.opt-name {
  color: var(--el-text-color-regular);
}
.opt-tag {
  margin-left: 8px;
}
</style>
