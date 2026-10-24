<template>
  <div class="workflow-tab">
    <el-card shadow="never" class="config-card" :class="{ 'pipeline-card': pipelineMode }">
      <template #header>
        <div class="card-head">
          <span>{{ pipelineMode ? 'Pipeline 执行' : '多 Agent 测试工作流' }}</span>
          <span class="muted">{{ pipelineMode ? '一键从代码分析到执行结果，全自动串联' : '需求 → 测试点 → 用例 → 独立审核/修订 → 执行 → 证据分析' }}</span>
        </div>
      </template>

      <div v-if="pipelineMode" class="pipeline-steps">
        <span v-for="(step, index) in PIPELINE_STEPS" :key="step" class="pipeline-step">
          <i>{{ index + 1 }}</i>{{ step }}<em v-if="index < PIPELINE_STEPS.length - 1">→</em>
        </span>
      </div>

      <el-form label-width="88px" :class="pipelineMode ? 'pipeline-form' : 'config-form'">
        <el-form-item label="需求版本">
          <el-select v-model="form.requirementId" placeholder="选择需求版本" style="width: 100%" :disabled="starting">
            <el-option
              v-for="r in requirements"
              :key="r.id"
              :label="`${r.version} · ${r.name}（测试点 ${r.testPointCount}）`"
              :value="r.id"
            />
          </el-select>
        </el-form-item>
        <el-form-item v-if="pipelineMode" label="被测地址">
          <el-input
            v-model="form.targetUrl"
            placeholder="留空使用项目配置的 targetUrl"
            clearable
            :disabled="starting"
          />
        </el-form-item>

        <template v-if="!pipelineMode || showAdvanced">
          <el-form-item label="生成模式">
            <el-select v-model="form.mode" style="width: 100%" :disabled="starting">
              <el-option label="自动（LLM 优先，失败降级规则）" value="auto" />
              <el-option label="LLM" value="llm" />
              <el-option label="规则" value="rule" />
            </el-select>
          </el-form-item>
          <el-form-item label="测试点 LLM">
            <el-select
              v-model="form.testPointLlmProfileId"
              clearable
              placeholder="默认文本 LLM"
              style="width: 100%"
              :disabled="starting || form.mode === 'rule'"
            >
              <el-option
                v-for="p in textProfiles"
                :key="p.id"
                :label="`${p.name} · ${p.model}`"
                :value="p.id"
              />
            </el-select>
          </el-form-item>
          <el-form-item label="用例 LLM">
            <el-select
              v-model="form.caseLlmProfileId"
              clearable
              placeholder="默认文本 LLM"
              style="width: 100%"
              :disabled="starting || form.mode === 'rule'"
            >
              <el-option
                v-for="p in textProfiles"
                :key="p.id"
                :label="`${p.name} · ${p.model}`"
                :value="p.id"
              />
            </el-select>
          </el-form-item>
          <el-form-item label="执行与审核">
            <el-switch v-model="form.autoExecute" active-text="执行通过用例" />
            <el-switch v-model="form.reviewEnabled" active-text="启用用例审核" class="switch-gap" />
            <el-switch v-model="form.analyzeEnabled" active-text="分析执行结果" class="switch-gap" />
          </el-form-item>
          <el-form-item label="代码分析">
            <el-switch v-model="form.analyzeCode" active-text="缺分析时自动分析源码" :disabled="starting" />
            <el-switch v-model="form.reanalyzeCode" active-text="强制重新分析" class="switch-gap" :disabled="starting" />
          </el-form-item>
          <el-form-item label="修订轮次">
            <el-input-number v-model="form.maxRevisions" :min="0" :max="2" :disabled="starting" />
            <span class="muted hint">超过上限仍不通过 → 拒绝，不执行</span>
          </el-form-item>
          <el-form-item label="用例上限">
            <el-input-number v-model="form.maxCases" :min="1" :max="100" :disabled="starting" />
            <el-switch v-model="form.reuseExistingCases" active-text="复用已生成用例" class="switch-gap" />
          </el-form-item>
          <el-form-item label="重解析">
            <el-switch v-model="form.reanalyzePoints" active-text="重新生成测试点（会强制重生成用例）" :disabled="starting" />
          </el-form-item>
        </template>

        <el-form-item>
          <el-button
            type="primary"
            :size="pipelineMode ? 'large' : 'default'"
            :loading="starting"
            :disabled="!form.requirementId"
            @click="start"
          >
            {{ pipelineMode ? '一键执行 Pipeline' : '启动工作流' }}
          </el-button>
          <el-button v-if="pipelineMode" link @click="showAdvanced = !showAdvanced">
            {{ showAdvanced ? '收起高级配置' : '打开高级配置' }}
          </el-button>
          <span class="muted hint">后台串行执行，页面每 2 秒刷新阶段状态</span>
        </el-form-item>
      </el-form>
    </el-card>

    <div v-if="runs.length" class="run-list">
      <div class="section-title">运行记录</div>
      <el-table
        :data="runs"
        size="small"
        border
        highlight-current-row
        :current-row-key="selectedRunId"
        row-key="id"
        @row-click="selectRun"
      >
        <el-table-column label="需求版本" min-width="170">
          <template #default="{ row }">{{ row.requirementVersion }} · {{ row.requirementName }}</template>
        </el-table-column>
        <el-table-column label="状态" width="100">
          <template #default="{ row }">
            <el-tag size="small" :type="runStatusTag(row.status)">{{ runStatusLabel(row.status) }}</el-tag>
          </template>
        </el-table-column>
        <el-table-column label="当前阶段" width="130">
          <template #default="{ row }">{{ row.currentStageLabel || '—' }}</template>
        </el-table-column>
        <el-table-column label="创建时间" width="170">
          <template #default="{ row }">{{ formatTime(row.createdAt) }}</template>
        </el-table-column>
        <el-table-column label="报告" width="80">
          <template #default="{ row }">
            <a :href="workflowReportUrl(row.id)" target="_blank" rel="noopener" @click.stop>查看</a>
          </template>
        </el-table-column>
      </el-table>
    </div>

    <el-empty v-if="!runs.length && !loadingRuns" description="还没有工作流运行记录，选择需求版本后启动" />

    <div v-if="detail" v-loading="loadingDetail" class="detail">
      <div class="detail-head">
        <div>
          <span class="detail-title">{{ detail.requirementVersion }} · {{ detail.requirementName }}</span>
          <el-tag size="small" :type="runStatusTag(detail.status)" class="ml">{{ runStatusLabel(detail.status) }}</el-tag>
        </div>
        <div class="detail-actions">
          <a :href="workflowReportUrl(detail.id)" target="_blank" rel="noopener">
            <el-button size="small" type="primary" plain>打开报告</el-button>
          </a>
          <el-button size="small" :loading="loadingDetail" @click="loadDetail">刷新</el-button>
        </div>
      </div>

      <el-alert v-if="detail.errorMessage" :title="detail.errorMessage" type="error" :closable="false" class="mb" />

      <div class="statbar">
        <div class="stat"><b>{{ detail.summary?.testPoints ?? '—' }}</b><span>测试点</span></div>
        <div class="stat"><b>{{ detail.summary?.cases ?? '—' }}</b><span>用例</span></div>
        <div class="stat"><b>{{ detail.summary?.review?.pass ?? 0 }}</b><span>审核通过</span></div>
        <div class="stat"><b>{{ detail.summary?.review?.reject ?? 0 }}</b><span>拒绝</span></div>
        <div class="stat"><b>{{ detail.summary?.executions ?? 0 }}</b><span>执行</span></div>
        <div class="stat"><b>{{ detail.summary?.avgQuality ?? 0 }}</b><span>平均质量分</span></div>
      </div>

      <div class="section-title">阶段</div>
      <el-table :data="detail.stages" size="small" border>
        <el-table-column prop="label" label="阶段" width="140" />
        <el-table-column label="状态" width="100">
          <template #default="{ row }">
            <el-tag size="small" :type="stageStatusTag(row.status)">{{ stageStatusLabel(row.status) }}</el-tag>
          </template>
        </el-table-column>
        <el-table-column prop="outputSummary" label="结果" min-width="260" show-overflow-tooltip />
        <el-table-column prop="errorMessage" label="错误" min-width="200" show-overflow-tooltip />
        <el-table-column prop="model" label="模型" width="150" show-overflow-tooltip />
        <el-table-column label="耗时" width="90">
          <template #default="{ row }">{{ row.durationMs }}ms</template>
        </el-table-column>
      </el-table>

      <div class="section-title">用例审核与结果分析（{{ detail.cases.length }}）</div>
      <el-table :data="detail.cases" size="small" border>
        <el-table-column type="expand">
          <template #default="{ row }">
            <div class="expand">
              <div v-if="row.finalReview">
                <div class="expand-title">最终审核（第 {{ row.finalReview.round }} 轮 · {{ row.finalReview.reviewStatus }} · {{ row.finalReview.qualityScore }} 分 · 来源 {{ row.finalReview.source || '-' }}{{ row.finalReview.model ? ` / ${row.finalReview.model}` : '' }}）</div>
                <el-table v-if="row.finalReview.issues?.length" :data="row.finalReview.issues" size="small" border>
                  <el-table-column prop="severity" label="级别" width="80" />
                  <el-table-column prop="field" label="字段" width="120" />
                  <el-table-column prop="problem" label="问题" min-width="220" />
                  <el-table-column prop="suggestion" label="建议" min-width="200" />
                  <el-table-column prop="evidence" label="依据" min-width="160" />
                </el-table>
                <div v-else class="muted">没有问题记录</div>
                <div v-if="row.finalReview.missingCoverage?.length" class="kv">
                  未覆盖：{{ row.finalReview.missingCoverage.join('；') }}
                </div>
                <div v-if="row.finalReview.suggestions?.length" class="kv">
                  建议：{{ row.finalReview.suggestions.join('；') }}
                </div>
              </div>

              <div v-if="row.reviews?.length > 1" class="rounds">
                <div class="expand-title">审核轮次</div>
                <el-tag
                  v-for="item in row.reviews"
                  :key="item.id"
                  size="small"
                  :type="reviewTag(item.reviewStatus)"
                  class="round-tag"
                >
                  第{{ item.round }}轮 {{ item.reviewStatus }} / {{ item.qualityScore }}分
                </el-tag>
              </div>

              <div v-if="row.analysis" class="analysis">
                <div class="expand-title">结果分析：{{ categoryLabel(row.analysis.failureCategory) }}</div>
                <div class="kv">结论：{{ row.analysis.failureReason || '—' }}</div>
                <div class="kv">置信度：{{ Math.round((row.analysis.confidence || 0) * 100) }}% · 来源：{{ row.analysis.source }} · 需人工复核：{{ row.analysis.needsHumanReview ? '是' : '否' }}</div>
                <div class="kv">建议：{{ row.analysis.recommendedAction || '—' }}</div>
                <ul class="evidence">
                  <li v-for="(e, idx) in row.analysis.evidence" :key="idx">{{ e }}</li>
                </ul>
              </div>
            </div>
          </template>
        </el-table-column>
        <el-table-column prop="position" label="#" width="50" />
        <el-table-column prop="title" label="用例" min-width="240" show-overflow-tooltip />
        <el-table-column label="自动化" width="90">
          <template #default="{ row }">
            <el-tag size="small" :type="automationTag(row.automationStatus)">{{ automationLabel(row.automationStatus) }}</el-tag>
          </template>
        </el-table-column>
        <el-table-column label="审核" width="90">
          <template #default="{ row }">
            <el-tag size="small" :type="reviewTag(row.reviewStatus)">{{ reviewLabel(row.reviewStatus) }}</el-tag>
          </template>
        </el-table-column>
        <el-table-column prop="qualityScore" label="质量分" width="80" />
        <el-table-column prop="revisionCount" label="修订" width="60" />
        <el-table-column label="执行" width="100">
          <template #default="{ row }">
            <span v-if="row.execution">{{ row.execution.statusLabel || row.execution.status }}</span>
            <span v-else class="muted">未执行</span>
          </template>
        </el-table-column>
        <el-table-column label="分析" width="140">
          <template #default="{ row }">
            <span v-if="row.analysis">{{ categoryLabel(row.analysis.failureCategory) }}</span>
            <span v-else class="muted">—</span>
          </template>
        </el-table-column>
      </el-table>
    </div>
  </div>
</template>

<script setup>
import { computed, onBeforeUnmount, onMounted, reactive, ref, watch } from 'vue'
import { ElMessage } from 'element-plus'
import { listRequirements } from '../api/requirements'
import {
  createWorkflow,
  getWorkflow,
  listWorkflows,
  workflowReportUrl,
} from '../api/workflows'
import { listLlmProfiles } from '../api/settings'

const props = defineProps({
  projectId: { type: String, required: true },
  requirementId: { type: String, default: null },
  pipelineMode: { type: Boolean, default: false },
  targetUrlDefault: { type: String, default: '' },
})

const PIPELINE_STEPS = ['代码分析', '测试点', '用例生成', '审核修订', '自动执行', '结果分析', '报告']
const requirements = ref([])
const llmProfiles = ref([])
const textProfiles = computed(() => llmProfiles.value.filter((p) => p.kind === 'text' && p.enabled))
const runs = ref([])
const selectedRunId = ref('')
const detail = ref(null)
const loadingRuns = ref(false)
const loadingDetail = ref(false)
const starting = ref(false)
const showAdvanced = ref(false)

const form = reactive({
  requirementId: '',
  mode: 'auto',
  autoExecute: true,
  maxRevisions: 2,
  maxCases: 20,
  reuseExistingCases: true,
  reanalyzePoints: false,
  reviewEnabled: true,
  analyzeEnabled: true,
  analyzeCode: true,
  testPointLlmProfileId: null,
  caseLlmProfileId: null,
  reanalyzeCode: false,
  targetUrl: '',
})

let pollTimer = null

async function loadLlmProfiles() {
  try {
    llmProfiles.value = await listLlmProfiles()
    const defaultProfile = llmProfiles.value.find((p) => p.kind === 'text' && p.isDefault && p.enabled)
    if (defaultProfile) {
      if (!form.testPointLlmProfileId) form.testPointLlmProfileId = defaultProfile.id
      if (!form.caseLlmProfileId) form.caseLlmProfileId = defaultProfile.id
    }
  } catch {
    llmProfiles.value = []
  }
}

async function loadRequirements() {
  try {
    requirements.value = await listRequirements(props.projectId)
  } catch {
    requirements.value = []
  }
  const requested = requirements.value.find((r) => r.id === props.requirementId)?.id
  const preferred =
    requested ||
    requirements.value.find((r) => r.testPointCount > 0)?.id ||
    requirements.value[0]?.id
  if (preferred) form.requirementId = preferred
}

async function loadRuns() {
  loadingRuns.value = true
  try {
    runs.value = await listWorkflows(props.projectId)
    if (!selectedRunId.value && runs.value.length) {
      await selectRun(runs.value[0])
    }
  } finally {
    loadingRuns.value = false
  }
}

async function loadDetail() {
  if (!selectedRunId.value) return
  loadingDetail.value = true
  try {
    detail.value = await getWorkflow(selectedRunId.value)
    ensurePolling()
  } finally {
    loadingDetail.value = false
  }
}

async function selectRun(row) {
  selectedRunId.value = row.id
  await loadDetail()
}

async function start() {
  if (!form.requirementId) return
  starting.value = true
  try {
    const run = await createWorkflow(props.projectId, {
      ...form,
      requirementId: form.requirementId,
      targetUrl: form.targetUrl.trim() || null,
    })
    ElMessage.success(props.pipelineMode ? 'Pipeline 已启动' : '工作流已启动')
    selectedRunId.value = run.id
    await loadRuns()
    await loadDetail()
  } finally {
    starting.value = false
  }
}

function ensurePolling() {
  const running = detail.value && ['pending', 'running'].includes(detail.value.status)
  if (running && !pollTimer) {
    pollTimer = setInterval(async () => {
      if (!selectedRunId.value) return
      try {
        detail.value = await getWorkflow(selectedRunId.value)
        const list = await listWorkflows(props.projectId)
        runs.value = list
        if (!['pending', 'running'].includes(detail.value.status)) stopPolling()
      } catch {
        stopPolling()
      }
    }, 2000)
  } else if (!running) {
    stopPolling()
  }
}

function stopPolling() {
  if (pollTimer) {
    clearInterval(pollTimer)
    pollTimer = null
  }
}

const STATUS_LABEL = { pending: '待运行', running: '运行中', completed: '已完成', partial: '部分完成', failed: '失败', cancelled: '已取消' }
const RUN_TAG = { pending: 'info', running: 'warning', completed: 'success', partial: 'warning', failed: 'danger', cancelled: 'info' }
const STAGE_LABEL = { pending: '待运行', running: '运行中', completed: '完成', failed: '失败', skipped: '跳过' }
const STAGE_TAG = { pending: 'info', running: 'warning', completed: 'success', failed: 'danger', skipped: 'info' }
const REVIEW_LABEL = { pending: '待审核', pass: '通过', revise: '待修订', reject: '拒绝' }
const REVIEW_TAG = { pending: 'info', pass: 'success', revise: 'warning', reject: 'danger' }
const AUTOMATION_LABEL = { automated: '自动', manual: '手工', blocked: '阻塞' }
const AUTOMATION_TAG = { automated: 'success', manual: 'info', blocked: 'danger' }
const CATEGORY_LABEL = {
  product_defect: '产品缺陷',
  test_script_issue: '脚本问题',
  test_data_issue: '测试数据问题',
  environment_issue: '环境问题',
  unknown: '证据不足',
  none: '无失败',
}

const runStatusLabel = (s) => STATUS_LABEL[s] || s
const runStatusTag = (s) => RUN_TAG[s] || 'info'
const stageStatusLabel = (s) => STAGE_LABEL[s] || s
const stageStatusTag = (s) => STAGE_TAG[s] || 'info'
const reviewLabel = (s) => REVIEW_LABEL[s] || s
const reviewTag = (s) => REVIEW_TAG[s] || 'info'
const automationLabel = (s) => AUTOMATION_LABEL[s] || s
const automationTag = (s) => AUTOMATION_TAG[s] || 'info'
const categoryLabel = (s) => CATEGORY_LABEL[s] || s || '—'
const formatTime = (t) => (t ? new Date(t).toLocaleString() : '—')

watch(
  () => props.requirementId,
  (id) => {
    if (id) form.requirementId = id
  },
)

watch(
  () => props.targetUrlDefault,
  (url) => {
    if (!form.targetUrl) form.targetUrl = url || ''
  },
  { immediate: true },
)

watch(
  () => props.projectId,
  async () => {
    stopPolling()
    selectedRunId.value = ''
    detail.value = null
    runs.value = []
    form.requirementId = ''
    form.targetUrl = props.targetUrlDefault || ''
    await loadRequirements()
    await loadRuns()
  },
)

onMounted(async () => {
  await loadLlmProfiles()
  await loadRequirements()
  await loadRuns()
})

onBeforeUnmount(stopPolling)
</script>

<style scoped>
.workflow-tab { display: flex; flex-direction: column; gap: 14px; }
.card-head { display: flex; align-items: baseline; gap: 12px; }
.config-form { display: grid; grid-template-columns: repeat(auto-fit, minmax(320px, 1fr)); gap: 0 18px; }
.config-form :deep(.el-form-item) { margin-bottom: 12px; }
.pipeline-form { max-width: 820px; }
.pipeline-form :deep(.el-form-item) { margin-bottom: 14px; }
.pipeline-steps {
  display: flex;
  flex-wrap: wrap;
  gap: 8px;
  margin-bottom: 16px;
  padding: 10px 12px;
  background: var(--el-fill-color-lighter);
  border-radius: 6px;
}
.pipeline-step {
  display: inline-flex;
  align-items: center;
  gap: 5px;
  font-size: 12px;
  color: var(--el-text-color-regular);
}
.pipeline-step i {
  width: 18px;
  height: 18px;
  border-radius: 50%;
  background: var(--el-color-primary);
  color: #fff;
  font-style: normal;
  display: inline-flex;
  align-items: center;
  justify-content: center;
  font-size: 11px;
}
.pipeline-step em {
  font-style: normal;
  color: var(--el-text-color-placeholder);
  margin-left: 4px;
}
.switch-gap { margin-left: 14px; }
.hint { margin-left: 10px; font-size: 12px; }
.muted { color: #909399; font-size: 12px; }
.section-title { font-weight: 600; margin: 6px 0 8px; }
.run-list :deep(.el-table__row) { cursor: pointer; }
.detail { display: flex; flex-direction: column; gap: 8px; }
.detail-head { display: flex; align-items: center; justify-content: space-between; gap: 12px; }
.detail-title { font-size: 15px; font-weight: 600; }
.ml { margin-left: 8px; }
.mb { margin-bottom: 8px; }
.statbar { display: grid; grid-template-columns: repeat(auto-fit, minmax(110px, 1fr)); gap: 10px; }
.stat { background: #f5f7fa; border-radius: 6px; padding: 10px 14px; display: flex; flex-direction: column; gap: 2px; }
.stat b { font-size: 20px; }
.stat span { color: #909399; font-size: 12px; }
.expand { padding: 8px 16px 12px; display: flex; flex-direction: column; gap: 10px; }
.expand-title { font-weight: 600; margin-bottom: 6px; }
.kv { font-size: 13px; margin: 4px 0; color: #606266; }
.rounds .round-tag { margin-right: 6px; }
.evidence { margin: 6px 0 0 18px; color: #606266; font-size: 13px; }
</style>
