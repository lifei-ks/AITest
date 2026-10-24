<template>
  <div>
    <el-empty v-if="!loading && !requirements.length" description="还没有需求版本，先创建一期需求文档">
      <el-button type="primary" @click="$emit('go-requirements')">去创建需求版本</el-button>
    </el-empty>

    <template v-else>
      <div class="toolbar-card">
        <div class="toolbar-top">
          <div class="toolbar-scope">
            <div class="field-group">
              <span class="field-label">需求</span>
              <el-select
                :model-value="requirementId"
                class="select-requirement"
                placeholder="选择需求版本"
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
            </div>

            <div class="field-group">
              <span class="field-label">模式</span>
              <el-radio-group v-model="mode" size="small" :disabled="running">
                <el-radio-button value="auto">自动（LLM 优先）</el-radio-button>
                <el-radio-button value="llm">仅 LLM</el-radio-button>
                <el-radio-button value="rule">仅规则</el-radio-button>
              </el-radio-group>
            </div>

            <div class="field-group">
              <span class="field-label">文本 LLM</span>
              <el-select
                v-model="llmProfileId"
                class="select-profile"
                size="small"
                clearable
                placeholder="默认文本 LLM"
                :disabled="running || mode === 'rule'"
              >
                <el-option
                  v-for="p in textProfiles"
                  :key="p.id"
                  :label="`${p.name} · ${p.model}`"
                  :value="p.id"
                />
              </el-select>
            </div>
          </div>

          <div class="toolbar-actions">
            <el-button
              type="primary"
              :loading="running"
              :disabled="running || !requirementId || !pointCount"
              @click="start"
            >
              <el-icon class="mr4"><MagicStick /></el-icon>生成用例
            </el-button>
            <el-button v-if="!pointCount" @click="$emit('go-requirements')">先解析测试点</el-button>
            <el-tag v-if="running" type="warning">生成中… 已展示 {{ cases.length }} 条</el-tag>
            <el-tag v-else-if="stat" :type="stat.fallback ? 'warning' : 'success'">
              完成 · {{ stat.llmCaseCount ?? 0 }} 条 LLM / {{ stat.ruleCaseCount ?? 0 }} 条规则
            </el-tag>
          </div>
        </div>

        <div class="toolbar-versions">
          <div class="field-group">
            <span class="field-label">生成版本</span>
            <el-select
              v-model="selectedGenerationId"
              class="select-generation"
              size="small"
              placeholder="选择生成版本"
              :disabled="running"
              @change="onGenerationChange"
            >
              <el-option :value="LIVE_ID" label="实时用例（未归档）">
                <span class="opt-ver">实时</span>
                <span class="opt-name">实时用例（未归档）</span>
              </el-option>
              <el-option
                v-for="g in generations"
                :key="g.id"
                :label="generationLabel(g)"
                :value="g.id"
              >
                <span class="opt-ver">{{ generationOptionPrefix(g) }}</span>
                <span class="opt-name">{{ generationLabel(g) }}</span>
              </el-option>
            </el-select>
          </div>
          <div class="version-tags">
            <el-tag v-if="selectedGenerationId === LIVE_ID" size="small" type="success" effect="plain">实时</el-tag>
            <el-tag v-else-if="isLegacyGeneration" size="small" type="info" effect="plain">历史归档（只读）</el-tag>
            <el-tag v-else-if="isHistoricalGeneration" size="small" type="info" effect="plain">历史版本（只读）</el-tag>
            <el-tag v-else-if="selectedGeneration" size="small" type="primary" effect="plain">最新版本（只读）</el-tag>
          </div>
        </div>
      </div>

      <el-alert
        v-if="requirementId && !pointCount && !loading"
        :title="`当前版本还没有测试点，请到「需求版本」解析需求文档后再生成`"
        type="warning"
        :closable="false"
        class="mb"
      />

      <el-alert
        v-if="generationNotice"
        :title="generationNotice"
        type="info"
        show-icon
        :closable="false"
        class="mb"
      />

      <el-alert
        v-if="fallbackInfo"
        :title="fallbackInfo.message || 'LLM 不可用，已使用规则生成'"
        :description="fallbackInfo.reason || ''"
        type="warning"
        show-icon
        :closable="false"
        class="mb"
      />

      <el-progress
        v-if="running"
        :percentage="progressPercent"
        :stroke-width="6"
        :show-text="false"
        striped
        striped-flow
        class="progress"
      />
      <div v-if="running" class="progress-text">
        <el-tag v-if="currentMode" size="small" :type="currentMode === 'llm' ? 'primary' : 'warning'" effect="plain">
          {{ currentMode === 'llm' ? 'LLM 生成' : '规则生成' }}
        </el-tag>
        <span>{{ progressText }}</span>
        <span class="muted">已展示 {{ cases.length }} 条</span>
      </div>

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
        :title="`丢弃原因：无预期 ${stat.dropReasons.no_expected || 0}，复合预期 ${stat.dropReasons.compound_expected || 0}，多输入 ${stat.dropReasons.multiple_inputs || 0}，低分 ${stat.dropReasons.low_score || 0}，去重 ${stat.dropReasons.duplicate || 0}`"
      />

      <!-- 测试点预览 -->
      <el-collapse v-if="pointCount" class="points">
        <el-collapse-item :title="`本版本测试点（${pointCount}）`" name="points">
          <el-table :data="points" size="small" border max-height="300" v-loading="pointsLoading">
            <el-table-column prop="seq" label="#" width="50" align="center" />
            <el-table-column prop="title" label="测试点" min-width="220" show-overflow-tooltip />
            <el-table-column prop="priority" label="优先级" width="80" align="center" />
            <el-table-column prop="expected" label="预期" min-width="180" show-overflow-tooltip />
          </el-table>
        </el-collapse-item>
      </el-collapse>

      <!-- 本次生成结果 -->
      <el-table v-if="cases.length" :data="cases" v-loading="generationLoading" border stripe class="table">
        <el-table-column type="index" label="#" width="50" align="center" />
        <el-table-column prop="title" label="标题" :min-width="isNarrow ? 190 : 240" show-overflow-tooltip />
        <el-table-column v-if="!isNarrow" label="类型" width="90" align="center">
          <template #default="{ row }">
            <el-tag size="small" :type="typeTag(row.type)">{{ typeLabel(row.type) }}</el-tag>
          </template>
        </el-table-column>
        <el-table-column prop="priority" label="优先级" width="80" align="center" />
        <el-table-column v-if="!isNarrow" label="来源" width="80" align="center">
          <template #default="{ row }">
            <el-tag size="small" :type="row._draft ? 'warning' : row.source === 'ai' ? 'success' : 'info'" effect="plain">
              {{ row._draft ? 'LLM 候选' : row.source === 'ai' ? 'LLM' : row.source === 'rule' ? '规则' : row.source }}
            </el-tag>
          </template>
        </el-table-column>
        <el-table-column v-if="!isNarrow" prop="qualityScore" label="质量分" width="90" align="center" />
        <el-table-column label="预期结果" :min-width="isNarrow ? 180 : 200" show-overflow-tooltip>
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
import { generateRequirementStream, getGeneration, listGenerations, listRequirements, listTestPoints } from '../api/requirements'
import { listLlmProfiles } from '../api/settings'
import { listTestcases } from '../api/testcases'
import { LIVE_GENERATION_ID as LIVE_ID, generationLabel, generationOptionPrefix } from '../utils/generation'

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
const llmProfiles = ref([])
const llmProfileId = ref(null)
const textProfiles = computed(() => llmProfiles.value.filter((p) => p.kind === 'text' && p.enabled))
const cases = ref([])
const stat = ref(null)
const progress = ref({ current: 0, total: 0, message: '' })
const fallbackInfo = ref(null)
const currentMode = ref('')
const generations = ref([])
const selectedGenerationId = ref('')
const generationNotice = ref('')
const generationLoading = ref(false)
const isNarrow = ref(false)
const narrowMq = window.matchMedia('(max-width: 900px)')
function onNarrowChange(e) {
  isNarrow.value = e.matches
}

let es = null

const pointCount = computed(() => points.value.length)
const selectedGeneration = computed(
  () => generations.value.find((g) => g.id === selectedGenerationId.value) || null,
)
const isSnapshotGeneration = computed(
  () => selectedGenerationId.value !== LIVE_ID && !!selectedGeneration.value,
)
const isHistoricalGeneration = computed(
  () => isSnapshotGeneration.value && !selectedGeneration.value.isLatest,
)
const isLegacyGeneration = computed(
  () => selectedGeneration.value?.mode === 'legacy',
)
const progressPercent = computed(() => {
  if (!progress.value.total) return running.value ? 5 : 0
  return Math.max(running.value ? 2 : 0, Math.min(100, Math.round((progress.value.current / progress.value.total) * 100)))
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

async function loadGenerations(rid, preferredId = '') {
  if (!rid) {
    generations.value = []
    selectedGenerationId.value = ''
    generationNotice.value = ''
    return
  }
  try {
    generations.value = await listGenerations(rid)
  } catch {
    generations.value = []
  }
  if (!generations.value.length) {
    selectedGenerationId.value = LIVE_ID
    generationNotice.value = '当前只有实时用例，尚未记录生成版本；下一次生成会创建版本快照'
    await loadLiveCases(rid)
    return
  }
  if (preferredId && generations.value.some((g) => g.id === preferredId)) {
    selectedGenerationId.value = preferredId
  } else if (
    selectedGenerationId.value !== LIVE_ID
    && generations.value.some((g) => g.id === selectedGenerationId.value)
  ) {
    // 保持当前选中的历史版本
  } else if (selectedGenerationId.value === LIVE_ID) {
    generationNotice.value = '当前查看实时用例（未归档）；每次成功生成会自动保存为独立版本'
    await loadLiveCases(rid)
    return
  } else {
    selectedGenerationId.value = generations.value[0].id
  }
  await loadGeneration(selectedGenerationId.value)
}

async function loadGeneration(generationId) {
  const rid = props.requirementId
  if (!rid || !generationId) return
  generationLoading.value = true
  try {
    const data = await getGeneration(rid, generationId)
    cases.value = data.cases || []
    const meta = generations.value.find((g) => g.id === generationId)
    const historical = !!meta && !meta.isLatest
    if (meta?.mode === 'legacy') {
      generationNotice.value = '当前查看改造前实时用例的历史归档（只读）；后续生成会创建新的独立版本'
    } else if (historical) {
      generationNotice.value = '正在查看历史生成版本（只读快照）；重新生成会创建新版本，不会覆盖历史'
    } else {
      generationNotice.value = '当前查看最新生成版本（只读快照）；切换到「实时用例」可查看当前用例'
    }
    currentMode.value = data.mode === 'legacy' ? '' : data.mode || ''
    fallbackInfo.value = data.summary?.fallback
      ? {
          message: historical ? '该历史版本使用了规则兜底' : '本次生成使用了规则兜底',
          reason: data.summary?.fallbackReason || '',
        }
      : null
    if (historical || meta?.mode === 'legacy') stat.value = null
  } catch {
    /* 请求层已提示 */
  } finally {
    generationLoading.value = false
  }
}

async function loadLiveCases(rid) {
  try {
    const data = await listTestcases(props.projectId, {
      page: 1,
      pageSize: 200,
      requirementId: rid,
    })
    if (!running.value) cases.value = data.items || data.testCases || []
  } catch {
    /* 请求层已提示 */
  }
}

function onGenerationChange(generationId) {
  if (running.value) return
  if (generationId === LIVE_ID) {
    stat.value = null
    fallbackInfo.value = null
    currentMode.value = ''
    generationNotice.value = '当前查看实时用例（未归档）；每次成功生成会自动保存为独立版本'
    loadLiveCases(props.requirementId)
    return
  }
  if (!generationId) {
    cases.value = []
    generationNotice.value = ''
    return
  }
  loadGeneration(generationId)
}

watch(
  () => props.requirementId,
  (id) => {
    cases.value = []
    stat.value = null
    fallbackInfo.value = null
    currentMode.value = ''
    generations.value = []
    selectedGenerationId.value = ''
    generationNotice.value = ''
    close()
    loadPoints(id)
    loadGenerations(id)
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
    fallbackInfo.value = null
    currentMode.value = ''
    generations.value = []
    selectedGenerationId.value = ''
    generationNotice.value = ''
    close()
    loadRequirements()
  },
)

watch(
  () => props.refreshKey,
  async () => {
    await loadRequirements()
    if (props.requirementId) {
      await loadPoints(props.requirementId)
      await loadGenerations(props.requirementId)
    }
  },
)

function start() {
  if (running.value) return
  if (!props.requirementId) return ElMessage.warning('请先选择需求版本')
  if (!pointCount.value) return ElMessage.warning('该版本还没有测试点，请先解析需求文档')

  cases.value = []
  stat.value = null
  fallbackInfo.value = null
  currentMode.value = ''
  selectedGenerationId.value = LIVE_ID
  generationNotice.value = '正在生成新的用例版本；完成后会自动切换到新版本'
  progress.value = { current: 0, total: pointCount.value, message: '正在生成…' }
  running.value = true

  try {
    es = generateRequirementStream(
      props.requirementId,
      mode.value,
      mode.value === 'rule' ? null : llmProfileId.value,
    )
  } catch (e) {
    running.value = false
    ElMessage.error(e.message || '无法建立生成连接')
    return
  }

  es.addEventListener('started', () => {})

  es.addEventListener('progress', (e) => {
    try {
      const data = JSON.parse(e.data)
      if (data.mode) currentMode.value = data.mode
      progress.value = {
        current: data.current || 0,
        total: data.total || pointCount.value,
        message: data.message || '',
      }
    } catch {
      /* 忽略单条进度解析失败 */
    }
  })

  es.addEventListener('fallback', (e) => {
    try {
      const data = JSON.parse(e.data)
      fallbackInfo.value = {
        message: data.message || 'LLM 不可用，已使用规则生成',
        reason: data.reason || '',
      }
      currentMode.value = 'rule'
    } catch {
      /* 忽略降级事件解析失败 */
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

  es.addEventListener('case_draft', (e) => {
    try {
      const data = JSON.parse(e.data)
      if (data.testCase) cases.value.push({ ...data.testCase, _draft: true })
    } catch {
      /* 忽略候选解析失败 */
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
      if (stat.value.generationId) {
        loadGenerations(props.requirementId, stat.value.generationId)
      }
      if (stat.value.fallbackReason) {
        fallbackInfo.value = {
          message: '生成完成，但使用了规则兜底',
          reason: stat.value.fallbackReason,
        }
        ElMessage.warning(
          `生成完成：${stat.value.llmCaseCount || 0} 条 LLM，${stat.value.ruleCaseCount || 0} 条规则`,
        )
      } else {
        ElMessage.success(
          `生成完成：保留 ${stat.value.kept} 条（${stat.value.mode === 'llm' ? 'LLM' : '规则模式'}）`,
        )
      }
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
    ElMessage.error(message || '生成连接中断，请重试')
    close()
  })
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

async function loadLlmProfiles() {
  try {
    llmProfiles.value = await listLlmProfiles()
    const defaultProfile = llmProfiles.value.find((p) => p.kind === 'text' && p.isDefault && p.enabled)
    if (!llmProfileId.value && defaultProfile) llmProfileId.value = defaultProfile.id
  } catch {
    llmProfiles.value = []
  }
}

onMounted(() => {
  isNarrow.value = narrowMq.matches
  narrowMq.addEventListener('change', onNarrowChange)
  loadRequirements()
  loadLlmProfiles()
})
onBeforeUnmount(() => {
  narrowMq.removeEventListener('change', onNarrowChange)
  close()
})
</script>

<style scoped>
.toolbar-card {
  display: flex;
  flex-direction: column;
  gap: 10px;
  padding: 12px;
  margin-bottom: 12px;
  border: 1px solid var(--el-border-color-lighter);
  border-radius: 8px;
  background: var(--el-bg-color);
}
.toolbar-top {
  display: flex;
  align-items: flex-start;
  justify-content: space-between;
  gap: 12px;
  flex-wrap: wrap;
}
.toolbar-scope {
  display: flex;
  align-items: center;
  gap: 12px;
  flex: 1 1 auto;
  min-width: 0;
  flex-wrap: wrap;
}
.field-group {
  display: flex;
  align-items: center;
  gap: 6px;
  min-width: 0;
}
.field-label {
  font-size: 12px;
  color: var(--el-text-color-secondary);
  white-space: nowrap;
  line-height: 32px;
}
.select-requirement {
  width: 220px;
}
.select-profile {
  width: 220px;
}
.select-generation {
  width: 330px;
}
.toolbar-actions {
  display: flex;
  align-items: center;
  gap: 8px;
  flex-wrap: wrap;
  margin-left: auto;
}
.toolbar-card .el-button + .el-button {
  margin-left: 0;
}
.toolbar-versions {
  display: flex;
  align-items: center;
  gap: 10px;
  flex-wrap: wrap;
  padding-top: 10px;
  border-top: 1px dashed var(--el-border-color-lighter);
}
.version-tags {
  display: flex;
  align-items: center;
  gap: 6px;
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
@media (max-width: 900px) {
  .toolbar-card {
    padding: 10px;
  }
  .toolbar-top {
    flex-direction: column;
    align-items: stretch;
  }
  .toolbar-scope {
    flex-direction: column;
    align-items: stretch;
    gap: 10px;
  }
  .field-group {
    width: 100%;
  }
  .field-group .el-select {
    flex: 1;
    width: auto;
  }
  .toolbar-scope .el-radio-group {
    display: flex;
    flex-wrap: wrap;
  }
  .toolbar-actions {
    width: 100%;
    margin-left: 0;
  }
  .toolbar-actions .el-button {
    flex: 1 1 auto;
  }
  .toolbar-versions {
    flex-direction: column;
    align-items: stretch;
  }
  .toolbar-versions .field-group {
    width: 100%;
  }
  .version-tags {
    min-height: 24px;
  }
}
</style>
