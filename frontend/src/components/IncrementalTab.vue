<template>
  <div v-loading="loading" class="incremental-page">
    <div class="toolbar-card">
      <div class="toolbar-row">
        <div class="field-group">
          <span class="field-label">基线版本</span>
          <el-select v-model="baselineId" class="select-requirement" placeholder="选择一期基线">
            <el-option
              v-for="r in orderedRequirements"
              :key="r.id"
              :label="`${r.version} · ${r.name}`"
              :value="r.id"
            />
          </el-select>
        </div>
        <div class="field-group">
          <span class="field-label">当前版本</span>
          <el-select v-model="currentId" class="select-requirement" placeholder="选择二期版本">
            <el-option
              v-for="r in orderedRequirements"
              :key="r.id"
              :label="`${r.version} · ${r.name}`"
              :value="r.id"
            />
          </el-select>
        </div>
        <div class="field-group">
          <span class="field-label">模式</span>
          <el-select v-model="mode" style="width: 120px">
            <el-option label="自动" value="auto" />
            <el-option label="仅 LLM" value="llm" />
            <el-option label="仅规则" value="rule" />
          </el-select>
        </div>
        <div class="field-group">
          <span class="field-label">文本 LLM</span>
          <el-select
            v-model="profileId"
            style="width: 210px"
            clearable
            placeholder="默认文本 LLM"
            :disabled="mode === 'rule'"
          >
            <el-option
              v-for="p in textProfiles"
              :key="p.id"
              :label="`${p.name} · ${p.model}`"
              :value="p.id"
            />
          </el-select>
        </div>
        <div class="toolbar-actions">
          <el-button :loading="snapshotLoading" :disabled="!baselineId" @click="createBaseline">
            建立基线快照
          </el-button>
          <el-button type="primary" :loading="analyzing" :disabled="!canAnalyze" @click="analyze">
            分析变更
          </el-button>
          <el-button
            type="success"
            :loading="generating"
            :disabled="!run || run.status === 'generating'"
            @click="generate"
          >
            生成增量用例
          </el-button>
          <el-button :loading="loading" @click="refreshAll">刷新</el-button>
        </div>
      </div>

      <div class="snapshot-row">
        <div class="snapshot-item">
          <span class="muted">基线快照</span>
          <template v-if="baselineSnapshot">
            <el-tag size="small" type="success" effect="plain">
              {{ baselineSnapshot.endpoints?.length || 0 }} 接口
            </el-tag>
            <span class="muted small">{{ formatTime(baselineSnapshot.createdAt) }}</span>
          </template>
          <el-tag v-else size="small" type="warning" effect="plain">未建立</el-tag>
        </div>
        <div class="snapshot-item">
          <span class="muted">当前快照</span>
          <template v-if="currentSnapshot">
            <el-tag size="small" type="success" effect="plain">
              {{ currentSnapshot.endpoints?.length || 0 }} 接口
            </el-tag>
            <span class="muted small">{{ formatTime(currentSnapshot.createdAt) }}</span>
          </template>
          <el-tag v-else size="small" type="info" effect="plain">分析时自动建立</el-tag>
        </div>
        <div v-if="run" class="run-meta">
          <el-select v-model="runId" size="small" style="width: 280px" @change="loadRun">
            <el-option
              v-for="item in runs"
              :key="item.id"
              :label="`${formatTime(item.createdAt)} · ${statusLabel(item.status)}`"
              :value="item.id"
            />
          </el-select>
          <el-tag :type="statusTag(run.status)" size="small">{{ statusLabel(run.status) }}</el-tag>
          <span class="muted small">{{ formatTime(run.createdAt) }}</span>
        </div>
      </div>
    </div>

    <el-alert
      v-if="!baselineSnapshot"
      title="基线版本还没有接口快照。建议在一期代码分析完成后先建立基线，再切换到二期代码分析；系统不会把所有接口直接判定为新增。"
      type="warning"
      show-icon
      :closable="false"
      class="mb"
    />

    <el-empty
      v-if="!loading && !requirements.length"
      description="还没有需求版本，请先创建一期和二期需求"
    />
    <el-empty
      v-else-if="!run"
      description="选择基线和当前版本，点击「分析变更」生成变更清单与回归推荐"
    />

    <template v-else>
      <div class="metric-grid">
        <div class="metric-card">
          <span class="metric-label">新增接口</span>
          <strong class="metric-value success">{{ endpointCounts.added || 0 }}</strong>
        </div>
        <div class="metric-card">
          <span class="metric-label">修改接口</span>
          <strong class="metric-value warning">{{ endpointCounts.modified || 0 }}</strong>
        </div>
        <div class="metric-card">
          <span class="metric-label">删除 / 待确认</span>
          <strong class="metric-value danger">
            {{ (endpointCounts.deleted || 0) + (endpointCounts.uncertain || 0) }}
          </strong>
        </div>
        <div class="metric-card">
          <span class="metric-label">未变化接口</span>
          <strong class="metric-value">{{ endpointCounts.unchanged || 0 }}</strong>
        </div>
        <div class="metric-card">
          <span class="metric-label">新增用例</span>
          <strong class="metric-value">{{ caseSummary.created || 0 }}</strong>
        </div>
        <div class="metric-card">
          <span class="metric-label">更新用例</span>
          <strong class="metric-value">{{ caseSummary.updated || 0 }}</strong>
        </div>
        <div class="metric-card">
          <span class="metric-label">复用用例</span>
          <strong class="metric-value">{{ caseSummary.reused || 0 }}</strong>
        </div>
        <div class="metric-card">
          <span class="metric-label">回归推荐</span>
          <strong class="metric-value warning">{{ caseSummary.regressionRecommended || 0 }}</strong>
        </div>
      </div>

      <div v-if="summary.conflicts || summary.requiresConfirmation" class="pending-row">
        <el-tag v-if="summary.conflicts" type="danger" effect="plain">
          文档/代码冲突 {{ summary.conflicts }}
        </el-tag>
        <el-tag v-if="summary.requiresConfirmation" type="warning" effect="plain">
          待人工确认 {{ summary.requiresConfirmation }}
        </el-tag>
        <el-tag v-if="ruleChangeCount" type="info" effect="plain">规则变更 {{ ruleChangeCount }}</el-tag>
        <el-tag v-if="permissionChangeCount" type="warning" effect="plain">鉴权变更 {{ permissionChangeCount }}</el-tag>
        <el-tag v-if="transitionChangeCount" type="info" effect="plain">状态流转变更 {{ transitionChangeCount }}</el-tag>
        <span class="muted small">确认后再决定是否生成；未确认的删除不会物理删除历史用例。</span>
      </div>

      <el-tabs v-model="detailTab" class="detail-tabs">
        <el-tab-pane label="变更清单" name="changes">
          <div class="table-toolbar">
            <el-select v-model="changeFilter" size="small" style="width: 170px">
              <el-option label="全部变更" value="all" />
              <el-option label="待人工确认" value="pending" />
              <el-option label="仅接口" value="endpoint" />
              <el-option label="仅业务规则" value="rule" />
              <el-option label="仅冲突" value="conflict" />
              <el-option label="仅鉴权/状态" value="misc" />
            </el-select>
            <span class="muted small">共 {{ filteredChanges.length }} 条</span>
          </div>
          <el-table :data="filteredChanges" border stripe size="small" empty-text="暂无变更">
            <el-table-column label="类型" width="90">
              <template #default="{ row }">{{ entityLabel(row.entityType) }}</template>
            </el-table-column>
            <el-table-column label="变更" width="100">
              <template #default="{ row }">
                <el-tag size="small" :type="changeTypeTag(row.changeType)">
                  {{ changeTypeLabel(row.changeType) }}
                </el-tag>
              </template>
            </el-table-column>
            <el-table-column label="接口 / 规则" min-width="230" show-overflow-tooltip>
              <template #default="{ row }">{{ changeTarget(row) }}</template>
            </el-table-column>
            <el-table-column prop="reason" label="原因" min-width="240" show-overflow-tooltip />
            <el-table-column label="置信度" width="85" align="center">
              <template #default="{ row }">{{ percent(row.confidence) }}</template>
            </el-table-column>
            <el-table-column label="状态" width="105" align="center">
              <template #default="{ row }">
                <el-tag size="small" :type="reviewTag(row.reviewStatus)" effect="plain">
                  {{ reviewLabel(row.reviewStatus) }}
                </el-tag>
              </template>
            </el-table-column>
            <el-table-column label="操作" width="130" align="center">
              <template #default="{ row }">
                <template v-if="row.reviewStatus !== 'confirmed' && row.reviewStatus !== 'rejected'">
                  <el-button link type="success" @click="reviewChange(row, 'confirmed')">确认</el-button>
                  <el-button link type="danger" @click="reviewChange(row, 'rejected')">驳回</el-button>
                </template>
                <span v-else class="muted">—</span>
              </template>
            </el-table-column>
          </el-table>
        </el-tab-pane>

        <el-tab-pane label="用例变化" name="cases">
          <div class="table-toolbar">
            <el-select v-model="caseFilter" size="small" style="width: 170px">
              <el-option label="全部用例去向" value="all" />
              <el-option label="新增" value="created" />
              <el-option label="更新" value="updated" />
              <el-option label="复用" value="reused" />
              <el-option label="待删除" value="pending_delete" />
              <el-option label="缺失/待确认" value="missing" />
            </el-select>
            <span class="muted small">共 {{ filteredCaseChanges.length }} 条</span>
          </div>
          <el-table :data="filteredCaseChanges" border stripe size="small" empty-text="尚未生成增量用例">
            <el-table-column label="去向" width="100">
              <template #default="{ row }">
                <el-tag size="small" :type="actionTag(row.action)">{{ actionLabel(row.action) }}</el-tag>
              </template>
            </el-table-column>
            <el-table-column label="二期用例" min-width="210" show-overflow-tooltip>
              <template #default="{ row }">{{ row.targetCaseTitle || '—' }}</template>
            </el-table-column>
            <el-table-column label="历史用例" min-width="210" show-overflow-tooltip>
              <template #default="{ row }">{{ row.baselineCaseTitle || '—' }}</template>
            </el-table-column>
            <el-table-column label="回归影响" width="100" align="center">
              <template #default="{ row }">
                <el-tag size="small" :type="impactTag(row.regressionImpact)" effect="plain">
                  {{ impactLabel(row.regressionImpact) }}
                </el-tag>
              </template>
            </el-table-column>
            <el-table-column prop="reason" label="说明" min-width="240" show-overflow-tooltip />
            <el-table-column label="回归状态" width="105" align="center">
              <template #default="{ row }">
                <el-tag size="small" :type="regressionTag(row.regressionStatus)" effect="plain">
                  {{ regressionLabel(row.regressionStatus) }}
                </el-tag>
              </template>
            </el-table-column>
          </el-table>
        </el-tab-pane>

        <el-tab-pane label="回归推荐" name="regression">
          <el-alert
            title="回归推荐基于接口变更、关联业务规则、公共鉴权和共享 Controller 推断；没有完整依赖图，不确定项会标注原因，需人工确认。"
            type="info"
            show-icon
            :closable="false"
            class="mb"
          />
          <el-table :data="regressionRows" border stripe size="small" empty-text="暂无回归推荐">
            <el-table-column label="历史用例" min-width="260" show-overflow-tooltip>
              <template #default="{ row }">{{ row.baselineCaseTitle || row.baselineCaseId }}</template>
            </el-table-column>
            <el-table-column label="影响" width="100" align="center">
              <template #default="{ row }">
                <el-tag size="small" :type="impactTag(row.regressionImpact)" effect="plain">
                  {{ impactLabel(row.regressionImpact) }}
                </el-tag>
              </template>
            </el-table-column>
            <el-table-column prop="regressionReason" label="推荐原因" min-width="300" show-overflow-tooltip />
            <el-table-column label="状态" width="105" align="center">
              <template #default="{ row }">
                <el-tag size="small" :type="regressionTag(row.regressionStatus)" effect="plain">
                  {{ regressionLabel(row.regressionStatus) }}
                </el-tag>
              </template>
            </el-table-column>
            <el-table-column label="操作" width="130" align="center">
              <template #default="{ row }">
                <el-button link type="success" @click="reviewRegression(row, 'accepted')">采纳</el-button>
                <el-button link type="danger" @click="reviewRegression(row, 'rejected')">忽略</el-button>
              </template>
            </el-table-column>
          </el-table>
        </el-tab-pane>
      </el-tabs>
    </template>
  </div>
</template>

<script setup>
import { computed, onMounted, ref, watch } from 'vue'
import { ElMessage } from 'element-plus'
import { listRequirements } from '../api/requirements'
import { listLlmProfiles } from '../api/settings'
import {
  analyzeIncremental,
  createRequirementSnapshot,
  generateIncremental,
  getIncrementalRun,
  getRequirementSnapshot,
  listIncrementalRuns,
  reviewIncrementalChange,
  reviewIncrementalRegression,
} from '../api/incremental'

const props = defineProps({
  projectId: { type: String, required: true },
  requirementId: { type: String, default: '' },
  refreshKey: { type: Number, default: 0 },
})
const emit = defineEmits(['update:requirementId'])

const requirements = ref([])
const baselineId = ref('')
const currentId = ref('')
const mode = ref('auto')
const profileId = ref(null)
const profiles = ref([])
const baselineSnapshot = ref(null)
const currentSnapshot = ref(null)
const run = ref(null)
const runId = ref('')
const runs = ref([])
const loading = ref(false)
const snapshotLoading = ref(false)
const analyzing = ref(false)
const generating = ref(false)
const detailTab = ref('changes')
const changeFilter = ref('all')
const caseFilter = ref('all')

const textProfiles = computed(() => profiles.value.filter((item) => item.kind === 'text' && item.enabled))
const orderedRequirements = computed(() => {
  const rank = (value) => (String(value || '').match(/\d+/g) || []).map(Number)
  return [...requirements.value].sort((a, b) => {
    const ar = rank(a.version)
    const br = rank(b.version)
    for (let i = 0; i < Math.max(ar.length, br.length); i += 1) {
      const diff = (ar[i] || 0) - (br[i] || 0)
      if (diff) return diff
    }
    return new Date(a.createdAt || 0) - new Date(b.createdAt || 0)
  })
})
const baselineBeforeCurrent = computed(() => {
  const base = orderedRequirements.value.findIndex((item) => item.id === baselineId.value)
  const current = orderedRequirements.value.findIndex((item) => item.id === currentId.value)
  return base >= 0 && current >= 0 && base < current
})
const summary = computed(() => run.value?.summary || {})
const endpointCounts = computed(() => summary.value.endpointCounts || {})
const caseSummary = computed(() => summary.value.caseChanges || {})
const ruleChangeCount = computed(() =>
  Object.values(summary.value.ruleCounts || {}).reduce((sum, value) => sum + Number(value || 0), 0),
)
const permissionChangeCount = computed(
  () => (run.value?.changes || []).filter((row) => row.entityType === 'permission').length,
)
const transitionChangeCount = computed(
  () => (run.value?.changes || []).filter((row) => row.entityType === 'state_transition').length,
)
const canAnalyze = computed(
  () => !!baselineId.value && !!currentId.value && baselineId.value !== currentId.value
    && baselineBeforeCurrent.value && !analyzing.value,
)

const filteredChanges = computed(() => {
  const rows = run.value?.changes || []
  if (changeFilter.value === 'pending') {
    return rows.filter((row) => row.reviewStatus === 'pending' || row.requiresConfirmation)
  }
  if (changeFilter.value === 'endpoint') {
    return rows.filter((row) => row.entityType === 'endpoint')
  }
  if (changeFilter.value === 'rule') {
    return rows.filter((row) => row.entityType === 'business_rule')
  }
  if (changeFilter.value === 'conflict') {
    return rows.filter((row) => row.entityType === 'conflict')
  }
  if (changeFilter.value === 'misc') {
    return rows.filter((row) => ['permission', 'state_transition'].includes(row.entityType))
  }
  return rows
})

const filteredCaseChanges = computed(() => {
  const rows = run.value?.caseChanges || []
  return caseFilter.value === 'all'
    ? rows
    : rows.filter((row) => row.action === caseFilter.value)
})

const regressionRows = computed(() => {
  const rows = run.value?.caseChanges || []
  const selected = new Map()
  for (const row of rows) {
    if (!row.baselineCaseId || !['direct', 'indirect'].includes(row.regressionImpact)) continue
    if (row.regressionStatus === 'rejected') continue
    const existing = selected.get(row.baselineCaseId)
    // 优先展示历史用例的复用/待删除行，避免更新行与复用行重复推荐
    if (!existing || row.action === 'reused') {
      selected.set(row.baselineCaseId, row)
    }
  }
  return [...selected.values()]
})

function formatTime(value) {
  return value ? new Date(value).toLocaleString() : '—'
}

function percent(value) {
  return `${Math.round((Number(value) || 0) * 100)}%`
}

function statusLabel(value) {
  return {
    analyzing: '分析中',
    review: '待确认',
    generating: '生成中',
    completed: '已完成',
    failed: '失败',
  }[value] || value
}

function statusTag(value) {
  return {
    analyzing: 'warning',
    review: 'warning',
    generating: 'primary',
    completed: 'success',
    failed: 'danger',
  }[value] || 'info'
}

function entityLabel(value) {
  return {
    endpoint: '接口',
    business_rule: '业务规则',
    state_transition: '状态流转',
    permission: '鉴权',
    conflict: '冲突',
  }[value] || value
}

function changeTypeLabel(value) {
  return {
    added: '新增',
    modified: '修改',
    deleted: '删除',
    unchanged: '未变化',
    uncertain: '待确认',
  }[value] || value
}

function changeTypeTag(value) {
  return {
    added: 'success',
    modified: 'warning',
    deleted: 'danger',
    unchanged: 'info',
    uncertain: 'warning',
  }[value] || 'info'
}

function reviewLabel(value) {
  return {
    auto: '自动通过',
    pending: '待确认',
    confirmed: '已确认',
    rejected: '已驳回',
  }[value] || value
}

function reviewTag(value) {
  return { auto: 'success', pending: 'warning', confirmed: 'success', rejected: 'danger' }[value] || 'info'
}

function actionLabel(value) {
  return {
    created: '新增',
    updated: '更新',
    reused: '复用',
    pending_delete: '待删除',
    missing: '缺失',
  }[value] || value
}

function actionTag(value) {
  return {
    created: 'success',
    updated: 'warning',
    reused: 'info',
    pending_delete: 'danger',
    missing: 'danger',
  }[value] || 'info'
}

function impactLabel(value) {
  return { direct: '直接受影响', indirect: '间接受影响', none: '未发现影响' }[value] || value
}

function impactTag(value) {
  return { direct: 'danger', indirect: 'warning', none: 'info' }[value] || 'info'
}

function regressionLabel(value) {
  return { suggested: '待确认', accepted: '已采纳', rejected: '已忽略' }[value] || value
}

function regressionTag(value) {
  return { suggested: 'warning', accepted: 'success', rejected: 'info' }[value] || 'info'
}

function changeTarget(row) {
  const newData = row.newData
  const oldData = row.oldData
  if (row.entityType === 'endpoint') {
    const before = oldData && !Array.isArray(oldData) ? `${oldData.method || ''} ${oldData.path || ''}`.trim() : ''
    const after = newData && !Array.isArray(newData) ? `${newData.method || ''} ${newData.path || ''}`.trim() : ''
    if (before && after && before !== after) return `${before} → ${after}`
    return after || before || row.matchKey
  }
  if (row.entityType === 'business_rule') {
    return (newData?.description || oldData?.description || row.matchKey || '').trim()
  }
  if (row.entityType === 'conflict') {
    return row.matchKey
  }
  if (row.entityType === 'permission') {
    return '公共鉴权配置'
  }
  return row.matchKey
}

async function loadRequirements() {
  try {
    requirements.value = await listRequirements(props.projectId)
  } catch {
    requirements.value = []
  }
  const ordered = orderedRequirements.value
  const valid = ordered.some((item) => item.id === currentId.value)
  if (!valid) {
    const preferred = props.requirementId && ordered.some((item) => item.id === props.requirementId)
      ? props.requirementId
      : [...ordered].reverse().find((item) => item.status === 'ready')?.id
      || ordered[ordered.length - 1]?.id
      || ''
    currentId.value = preferred
  }
  if (!baselineId.value || baselineId.value === currentId.value) {
    const index = ordered.findIndex((item) => item.id === currentId.value)
    baselineId.value = index > 0 ? ordered[index - 1].id : ''
  }
}

async function loadProfiles() {
  try {
    profiles.value = await listLlmProfiles()
    const defaultProfile = profiles.value.find((item) => item.kind === 'text' && item.isDefault && item.enabled)
    if (!profileId.value && defaultProfile) profileId.value = defaultProfile.id
  } catch {
    profiles.value = []
  }
}

async function loadSnapshots() {
  snapshotLoading.value = true
  try {
    const [base, current] = await Promise.all([
      baselineId.value ? getRequirementSnapshot(baselineId.value) : Promise.resolve(null),
      currentId.value ? getRequirementSnapshot(currentId.value) : Promise.resolve(null),
    ])
    baselineSnapshot.value = base || null
    currentSnapshot.value = current || null
  } finally {
    snapshotLoading.value = false
  }
}

async function loadRuns() {
  if (!currentId.value) {
    runs.value = []
    run.value = null
    return
  }
  try {
    runs.value = await listIncrementalRuns(currentId.value)
  } catch {
    runs.value = []
  }
  if (!runs.value.length) {
    run.value = null
    runId.value = ''
    return
  }
  const preferred = runs.value.some((item) => item.id === runId.value) ? runId.value : runs.value[0].id
  await loadRun(preferred)
}

async function loadRun(id) {
  if (!id) return
  try {
    run.value = await getIncrementalRun(id)
    runId.value = id
  } catch {
    run.value = null
  }
}

async function refreshAll() {
  loading.value = true
  try {
    await loadRequirements()
    await Promise.all([loadSnapshots(), loadRuns()])
  } finally {
    loading.value = false
  }
}

async function createBaseline() {
  if (!baselineId.value) return ElMessage.warning('请先选择基线版本')
  snapshotLoading.value = true
  try {
    baselineSnapshot.value = await createRequirementSnapshot(baselineId.value)
    ElMessage.success('基线快照已建立')
  } finally {
    snapshotLoading.value = false
  }
}

async function analyze() {
  if (!canAnalyze.value) {
    return ElMessage.warning('请选择不同的基线和当前版本，且基线必须早于当前版本')
  }
  analyzing.value = true
  try {
    run.value = await analyzeIncremental(currentId.value, {
      baselineRequirementId: baselineId.value,
      mode: mode.value,
      profileId: mode.value === 'rule' ? null : profileId.value,
    })
    runId.value = run.value.id
    await Promise.all([loadSnapshots(), loadRuns()])
    ElMessage.success('变更分析完成')
  } finally {
    analyzing.value = false
  }
}

async function generate() {
  if (!run.value) return
  generating.value = true
  try {
    run.value = await generateIncremental(run.value.id, {
      mode: mode.value,
      profileId: mode.value === 'rule' ? null : profileId.value,
    })
    await loadRuns()
    detailTab.value = 'cases'
    ElMessage.success('增量用例生成完成')
  } finally {
    generating.value = false
  }
}

async function reviewChange(row, status) {
  if (!run.value) return
  try {
    run.value = await reviewIncrementalChange(run.value.id, row.id, status)
    await loadRuns()
    ElMessage.success(status === 'confirmed' ? '已确认变更' : '已驳回变更')
  } catch {
    /* 请求层已提示 */
  }
}

async function reviewRegression(row, status) {
  try {
    run.value = await reviewIncrementalRegression(row.id, status)
    ElMessage.success(status === 'accepted' ? '已采纳回归推荐' : '已忽略回归推荐')
  } catch {
    /* 请求层已提示 */
  }
}

watch(currentId, async (value) => {
  if (value) emit('update:requirementId', value)
  const ordered = orderedRequirements.value
  const index = ordered.findIndex((item) => item.id === value)
  if (index > 0 && (!baselineId.value || baselineId.value === value || !ordered.some((item) => item.id === baselineId.value))) {
    baselineId.value = ordered[index - 1].id
  }
  run.value = null
  runId.value = ''
  await Promise.all([loadSnapshots(), loadRuns()])
})

watch(baselineId, async () => {
  run.value = null
  runId.value = ''
  await Promise.all([loadSnapshots(), loadRuns()])
})

watch(
  () => props.requirementId,
  async (value) => {
    if (value && value !== currentId.value) {
      currentId.value = value
    }
  },
)

watch(
  () => props.projectId,
  async () => {
    requirements.value = []
    baselineId.value = ''
    currentId.value = ''
    run.value = null
    runId.value = ''
    await refreshAll()
  },
)

watch(
  () => props.refreshKey,
  () => refreshAll(),
)

onMounted(async () => {
  loading.value = true
  try {
    await loadProfiles()
    await loadRequirements()
    await Promise.all([loadSnapshots(), loadRuns()])
  } finally {
    loading.value = false
  }
})
</script>

<style scoped>
.incremental-page {
  min-height: 260px;
}
.toolbar-card {
  padding: 12px;
  margin-bottom: 12px;
  border: 1px solid var(--el-border-color-lighter);
  border-radius: 8px;
  background: var(--el-bg-color);
}
.toolbar-row,
.toolbar-actions,
.snapshot-row,
.run-meta,
.pending-row,
.table-toolbar {
  display: flex;
  align-items: center;
  gap: 10px;
  flex-wrap: wrap;
}
.toolbar-row {
  margin-bottom: 10px;
}
.toolbar-actions {
  margin-left: auto;
}
.toolbar-card .el-button + .el-button {
  margin-left: 0;
}
.field-group {
  display: flex;
  align-items: center;
  gap: 6px;
}
.field-label {
  font-size: 12px;
  color: var(--el-text-color-secondary);
  white-space: nowrap;
}
.select-requirement {
  width: 220px;
}
.snapshot-row {
  padding-top: 10px;
  border-top: 1px dashed var(--el-border-color-lighter);
}
.snapshot-item {
  display: flex;
  align-items: center;
  gap: 6px;
}
.run-meta {
  margin-left: auto;
}
.metric-grid {
  display: grid;
  grid-template-columns: repeat(4, minmax(0, 1fr));
  gap: 10px;
  margin-bottom: 12px;
}
.metric-card {
  padding: 12px 14px;
  border: 1px solid var(--el-border-color-lighter);
  border-radius: 8px;
  background: var(--el-bg-color);
}
.metric-label {
  display: block;
  margin-bottom: 4px;
  font-size: 12px;
  color: var(--el-text-color-secondary);
}
.metric-value {
  font-size: 22px;
}
.metric-value.success {
  color: var(--el-color-success);
}
.metric-value.warning {
  color: var(--el-color-warning);
}
.metric-value.danger {
  color: var(--el-color-danger);
}
.pending-row {
  margin-bottom: 12px;
}
.table-toolbar {
  margin-bottom: 10px;
}
.detail-tabs {
  margin-top: 4px;
}
.mb {
  margin-bottom: 12px;
}
.muted {
  color: var(--el-text-color-secondary);
}
.small {
  font-size: 12px;
}
@media (max-width: 1100px) {
  .metric-grid {
    grid-template-columns: repeat(2, minmax(0, 1fr));
  }
}
@media (max-width: 900px) {
  .toolbar-row {
    align-items: stretch;
  }
  .field-group {
    width: 100%;
  }
  .field-group .el-select {
    flex: 1;
    width: auto;
  }
  .toolbar-actions {
    width: 100%;
    margin-left: 0;
  }
  .toolbar-actions .el-button {
    flex: 1 1 auto;
  }
  .run-meta {
    width: 100%;
    margin-left: 0;
  }
}
</style>
