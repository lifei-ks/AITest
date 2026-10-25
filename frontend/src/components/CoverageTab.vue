<template>
  <div v-loading="loading" class="coverage-page">
    <div class="toolbar-card">
      <div class="toolbar-row">
        <div class="field-group">
          <span class="field-label">需求</span>
          <el-select
            v-model="selectedRequirementId"
            clearable
            placeholder="全部需求版本"
            class="select-requirement"
            @change="load"
          >
            <el-option
              v-for="r in requirements"
              :key="r.id"
              :label="`${r.version} · ${r.name}`"
              :value="r.id"
            />
          </el-select>
        </div>
        <el-button :loading="loading" @click="load">
          <el-icon><Refresh /></el-icon>刷新
        </el-button>
        <div class="toolbar-spacer" />
        <template v-if="matrix">
          <el-tag type="info" effect="plain">当前用例 {{ summary.total }}</el-tag>
          <el-tag :type="summary.passed ? 'success' : 'info'" effect="plain">
            执行成功 {{ summary.passed }}
          </el-tag>
          <el-tag :type="summary.passRate >= 0.8 ? 'success' : summary.passRate > 0 ? 'warning' : 'info'" effect="plain">
            通过率 {{ percent(summary.passRate) }}
          </el-tag>
        </template>
      </div>
    </div>

    <el-alert
      v-if="matrix"
      title="执行成功覆盖只统计 status=passed 且属于当前实时用例的执行记录；历史版本与已删除用例不计入。规划覆盖基于当前实时用例的 coverageRefs 与最新代码事实。"
      type="info"
      show-icon
      :closable="false"
      class="mb"
    />

    <el-empty
      v-if="!loading && !matrix"
      description="暂无覆盖数据，请先完成代码分析并生成测试用例"
    />

    <template v-else-if="matrix">
      <div class="metric-grid">
        <div class="metric-card">
          <span class="metric-label">当前实时用例</span>
          <strong class="metric-value">{{ summary.total }}</strong>
          <span class="metric-hint">按当前需求版本筛选</span>
        </div>
        <div class="metric-card">
          <span class="metric-label">执行成功</span>
          <strong class="metric-value success">{{ summary.passed }}</strong>
          <span class="metric-hint">通过率 {{ percent(summary.passRate) }}</span>
        </div>
        <div class="metric-card">
          <span class="metric-label">失败</span>
          <strong class="metric-value danger">{{ summary.failed }}</strong>
          <span class="metric-hint">待复核 {{ summary.needsReview }}</span>
        </div>
        <div class="metric-card">
          <span class="metric-label">未执行</span>
          <strong class="metric-value">{{ summary.notRun }}</strong>
          <span class="metric-hint">执行中 {{ summary.running }} · 其他 {{ summary.other }}</span>
        </div>
      </div>

      <div class="coverage-grid">
        <div v-for="block in blocks" :key="block.key" class="coverage-card">
          <div class="card-head">
            <strong>{{ block.title }}</strong>
            <span class="muted">{{ block.total }} 项</span>
          </div>
          <div class="rate-line">
            <span>规划覆盖</span>
            <span>{{ block.plannedCovered }}/{{ block.total }} · {{ percent(block.plannedRate) }}</span>
          </div>
          <el-progress
            :percentage="toPercent(block.plannedRate)"
            :show-text="false"
            :stroke-width="7"
            color="#409eff"
          />
          <div class="rate-line">
            <span>执行成功覆盖</span>
            <span>{{ block.executedCovered }}/{{ block.total }} · {{ percent(block.executedRate) }}</span>
          </div>
          <el-progress
            :percentage="toPercent(block.executedRate)"
            :show-text="false"
            :stroke-width="7"
            color="#67c23a"
          />
          <div class="muted small">尚未规划覆盖：{{ block.uncoveredCount }} 项</div>
        </div>
      </div>

      <div class="section-card">
        <div class="section-head">
          <strong>按模块执行成功矩阵</strong>
          <span class="muted">当前实时用例的最新执行状态</span>
        </div>
        <el-table :data="summary.modules" border stripe size="small" empty-text="当前范围暂无实时用例">
          <el-table-column prop="module" label="模块" min-width="180" show-overflow-tooltip />
          <el-table-column prop="total" label="用例" width="80" align="center" />
          <el-table-column prop="passed" label="通过" width="80" align="center" />
          <el-table-column prop="failed" label="失败" width="80" align="center" />
          <el-table-column prop="needsReview" label="待复核" width="90" align="center" />
          <el-table-column prop="notRun" label="未执行" width="90" align="center" />
          <el-table-column prop="other" label="其他" width="80" align="center" />
          <el-table-column label="通过率" width="170" align="center">
            <template #default="{ row }">
              <el-progress
                :percentage="toPercent(row.passRate)"
                :stroke-width="6"
                :color="row.passRate >= 0.8 ? '#67c23a' : '#e6a23c'"
              />
            </template>
          </el-table-column>
        </el-table>
      </div>

      <el-collapse class="uncovered">
        <el-collapse-item
          v-for="group in uncoveredGroups"
          :key="group.key"
          :title="`${group.title}未规划覆盖（${group.items.length}）`"
          :name="group.key"
        >
          <div v-if="group.items.length" class="uncovered-tags">
            <el-tag v-for="item in group.items" :key="item.id" type="info" effect="plain">
              {{ item.label }}
            </el-tag>
          </div>
          <span v-else class="muted">全部已规划覆盖</span>
        </el-collapse-item>
      </el-collapse>
    </template>
  </div>
</template>

<script setup>
import { computed, onMounted, ref, watch } from 'vue'
import { getCoverageMatrix } from '../api/mindmap'
import { listRequirements } from '../api/requirements'

const props = defineProps({
  projectId: { type: String, required: true },
  requirementId: { type: String, default: '' },
  refreshKey: { type: Number, default: 0 },
})
const emit = defineEmits(['update:requirementId'])

const requirements = ref([])
const selectedRequirementId = ref(props.requirementId || '')
const matrix = ref(null)
const loading = ref(false)
const initialized = ref(false)

const BLOCKS = [
  { key: 'apiEndpoint', title: '接口覆盖' },
  { key: 'stateTransition', title: '状态流转覆盖' },
  { key: 'businessRule', title: '业务规则覆盖' },
]

const summary = computed(() => {
  const value = matrix.value?.caseExecution || {}
  return {
    total: value.total || 0,
    passed: value.passed || 0,
    failed: value.failed || 0,
    needsReview: value.needsReview || 0,
    notRun: value.notRun || 0,
    running: value.running || 0,
    other: Math.max(
      0,
      (value.total || 0)
        - (value.passed || 0)
        - (value.failed || 0)
        - (value.needsReview || 0)
        - (value.notRun || 0),
    ),
    passRate: value.passRate || 0,
    modules: value.modules || [],
  }
})

const blocks = computed(() =>
  BLOCKS.map((item) => ({
    ...item,
    ...(matrix.value?.[item.key] || {
      total: 0,
      plannedCovered: 0,
      executedCovered: 0,
      uncoveredCount: 0,
      plannedRate: 0,
      executedRate: 0,
      uncovered: [],
    }),
  })),
)

const uncoveredGroups = computed(() =>
  BLOCKS.map((item) => ({
    ...item,
    items: matrix.value?.[item.key]?.uncovered || [],
  })),
)

function toPercent(value) {
  return Math.max(0, Math.min(100, Math.round((Number(value) || 0) * 100)))
}

function percent(value) {
  return `${toPercent(value)}%`
}

async function loadRequirements() {
  try {
    requirements.value = await listRequirements(props.projectId)
  } catch {
    requirements.value = []
  }
  // 与用例生成/测试用例页保持一致：首次进入默认选最新需求版本；
  // 用户之后主动清空为“全部需求版本”时，不再自动覆盖。
  if (!initialized.value && !selectedRequirementId.value && requirements.value.length) {
    const latest = requirements.value[requirements.value.length - 1]
    selectedRequirementId.value = latest.id
    emit('update:requirementId', latest.id)
  }
  initialized.value = true
}

async function load() {
  loading.value = true
  try {
    matrix.value = await getCoverageMatrix(
      props.projectId,
      selectedRequirementId.value || undefined,
    )
  } catch {
    matrix.value = null
  } finally {
    loading.value = false
  }
}

watch(selectedRequirementId, (value) => {
  emit('update:requirementId', value || '')
})

watch(
  () => props.requirementId,
  (value) => {
    if ((value || '') !== selectedRequirementId.value) {
      selectedRequirementId.value = value || ''
    }
  },
)

watch(
  () => props.projectId,
  async () => {
    selectedRequirementId.value = ''
    matrix.value = null
    initialized.value = false
    await loadRequirements()
    load()
  },
)

watch(
  () => props.refreshKey,
  () => {
    loadRequirements()
    load()
  },
)

onMounted(async () => {
  await loadRequirements()
  load()
})
</script>

<style scoped>
.coverage-page {
  min-height: 240px;
}
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
.toolbar-row {
  display: flex;
  align-items: center;
  gap: 10px;
  flex-wrap: wrap;
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
  width: 260px;
}
.toolbar-spacer {
  flex: 1;
}
.mb {
  margin-bottom: 12px;
}
.metric-grid {
  display: grid;
  grid-template-columns: repeat(4, minmax(0, 1fr));
  gap: 12px;
  margin-bottom: 12px;
}
.metric-card {
  display: flex;
  flex-direction: column;
  gap: 4px;
  padding: 14px 16px;
  border: 1px solid var(--el-border-color-lighter);
  border-radius: 8px;
  background: var(--el-bg-color);
}
.metric-label {
  font-size: 12px;
  color: var(--el-text-color-secondary);
}
.metric-value {
  font-size: 26px;
  line-height: 1.2;
}
.metric-value.success {
  color: var(--el-color-success);
}
.metric-value.danger {
  color: var(--el-color-danger);
}
.metric-hint {
  font-size: 12px;
  color: var(--el-text-color-secondary);
}
.coverage-grid {
  display: grid;
  grid-template-columns: repeat(3, minmax(0, 1fr));
  gap: 12px;
  margin-bottom: 12px;
}
.coverage-card,
.section-card {
  padding: 14px 16px;
  border: 1px solid var(--el-border-color-lighter);
  border-radius: 8px;
  background: var(--el-bg-color);
}
.card-head,
.section-head {
  display: flex;
  align-items: center;
  justify-content: space-between;
  gap: 8px;
  margin-bottom: 12px;
}
.rate-line {
  display: flex;
  align-items: center;
  justify-content: space-between;
  margin: 12px 0 6px;
  font-size: 12px;
  color: var(--el-text-color-regular);
}
.small {
  font-size: 12px;
}
.section-card {
  margin-bottom: 12px;
}
.uncovered-tags {
  display: flex;
  gap: 8px;
  flex-wrap: wrap;
}
.muted {
  color: var(--el-text-color-secondary);
}
@media (max-width: 1100px) {
  .coverage-grid {
    grid-template-columns: 1fr;
  }
}
@media (max-width: 900px) {
  .metric-grid {
    grid-template-columns: repeat(2, minmax(0, 1fr));
  }
  .select-requirement {
    width: 100%;
  }
  .field-group {
    width: 100%;
  }
  .field-group .el-select {
    flex: 1;
  }
}
</style>
