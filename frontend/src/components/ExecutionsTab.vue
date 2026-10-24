<template>
  <div>
    <div class="toolbar">
      <el-select
        v-model="requirementId"
        placeholder="全部需求版本"
        clearable
        style="width: 170px"
        @change="onRequirementChange"
      >
        <el-option v-for="r in requirements" :key="r.id" :label="`${r.version} · ${r.name}`" :value="r.id" />
        <el-option label="未关联版本" value="__none__" />
      </el-select>
      <el-tag v-if="!historyMode && isLatestVersion" size="small" type="success" effect="plain">最新</el-tag>
      <el-tag size="small" effect="plain">{{ historyMode ? '按执行记录' : '按用例去重' }}</el-tag>
      <el-select v-model="status" placeholder="全部状态" clearable style="width: 130px" @change="applyFilter">
        <el-option v-for="s in STATUS" :key="s.v" :label="s.l" :value="s.v" />
      </el-select>
      <el-button type="primary" @click="applyFilter">查询</el-button>
      <el-button :loading="loading" @click="load">
        <el-icon><Refresh /></el-icon>刷新
      </el-button>
      <el-button
        v-if="!historyMode"
        type="warning"
        plain
        :disabled="!selectedRows.length"
        :loading="batchReviewing"
        @click="onBatchReview"
      >
        AI 审核{{ selectedRows.length ? `(${selectedRows.length})` : '' }}
      </el-button>
      <div class="spacer" />
      <el-tag v-if="!historyMode && overview" type="info">全局通过率 {{ Math.round((overview.passRate || 0) * 100) }}%</el-tag>
      <el-button v-if="!historyMode" :loading="reporting" @click="downloadReport">下载 HTML 报告</el-button>
    </div>

    <!-- 覆盖矩阵 -->
    <el-card v-if="!historyMode && matrix" shadow="never" class="card">
      <template #header>
        <div class="cov-head">
          <b>覆盖矩阵</b>
          <span class="muted">规划覆盖来自用例引用；执行覆盖只统计通过记录</span>
        </div>
      </template>
      <el-alert
        v-if="coverageWarning"
        :title="coverageWarning"
        type="warning"
        :closable="false"
        show-icon
        class="coverage-alert"
      />
      <div v-for="b in blocks" :key="b.key" class="cov-block">
        <div class="cov-title">
          <strong>{{ b.label }}</strong>
          <span class="muted">
            规划 {{ b.value.plannedCovered }}/{{ b.value.total }}
            · 执行 {{ b.value.executedCovered }}/{{ b.value.total }}
          </span>
        </div>
        <div class="cov-progress">
          <span class="cov-kind">规划</span>
          <el-progress
            :percentage="Math.round((b.value.plannedRate || 0) * 100)"
            :format="() => `${b.value.plannedCovered}/${b.value.total}`"
            color="#409eff"
          />
        </div>
        <div class="cov-progress">
          <span class="cov-kind">执行</span>
          <el-progress
            :percentage="Math.round((b.value.executedRate || 0) * 100)"
            :format="() => `${b.value.executedCovered}/${b.value.total}`"
            color="#67c23a"
          />
        </div>
        <div v-if="b.value.uncovered?.length" class="cov-uncovered">
          未覆盖：
          {{ b.value.uncovered.slice(0, 4).map((item) => item.label).join('；') }}
          <span v-if="b.value.uncovered.length > 4"> 等 {{ b.value.uncovered.length }} 项</span>
        </div>
      </div>
    </el-card>

    <el-table
      ref="tableRef"
      :data="rows"
      border
      stripe
      row-key="id"
      v-loading="loading"
      class="table"
      :empty-text="emptyText"
      @expand-change="onExpand"
      @selection-change="(v) => (selectedRows = v)"
    >
      <el-table-column v-if="!isNarrow && !historyMode" type="selection" width="45" />
      <el-table-column type="expand">
        <template #default="{ row }">
          <div class="expand-detail">
            <div v-if="detailMap[row.id]?.loading" class="loading-tip">加载步骤中…</div>
            <template v-else-if="detailMap[row.id]">
              <el-alert
                v-if="row.status === 'needs_review'"
                title="该执行需要人工复核"
                :description="reviewHint(row)"
                type="warning"
                show-icon
                :closable="false"
                class="alert"
              />
              <el-alert
                v-if="detailMap[row.id].detail?.errorCategory"
                :title="`错误分类：${detailMap[row.id].detail.errorCategoryLabel || detailMap[row.id].detail.errorCategory} (${detailMap[row.id].detail.errorCategory}) — ${detailMap[row.id].detail.errorCategoryDescription || '执行失败'}${detailMap[row.id].detail.errorMessage ? '；详情：' + detailMap[row.id].detail.errorMessage : ''}`"
                type="error"
                :closable="false"
                class="alert"
              />
              <div v-if="detailMap[row.id].detail" class="meta">
                <span>目标：{{ detailMap[row.id].detail.targetUrl || '—' }}</span>
                <span>步骤：{{ detailMap[row.id].detail.passedSteps }}/{{ detailMap[row.id].detail.totalSteps }}</span>
                <a v-if="detailMap[row.id].detail.videoPath" :href="`/api/executions/${row.id}/video`" target="_blank" class="video-link">▶ 查看录屏</a>
              </div>

              <!-- AI 截图审核 -->
              <div v-if="reviewOf(row)" class="review-box">
                <div class="review-head">
                  <el-tag
                    size="small"
                    :type="reviewOf(row).reviewer === 'manual'
                      ? (row.status === 'passed' ? 'success' : 'danger')
                      : reviewTag(reviewOf(row).verdict)"
                  >
                    {{ reviewOf(row).reviewer === 'manual'
                      ? (row.status === 'passed' ? '人工通过' : '人工失败')
                      : `AI 审核：${reviewLabel(reviewOf(row).verdict)}` }}
                  </el-tag>
                  <span class="review-meta">
                    {{ reviewOf(row).model || '—' }} · {{ reviewOf(row).imageCount }} 张截图
                  </span>
                  <el-button link type="primary" size="small" @click="openReviewReport(row)">详情报告</el-button>
                  <el-button link type="primary" size="small" @click="rerunReview(row)">重新 AI 审核</el-button>
                </div>
                <div v-if="reviewOf(row).reason" class="review-reason">{{ reviewOf(row).reason }}</div>
                <el-alert
                  v-if="reviewOf(row).recommendedAction"
                  :title="reviewOf(row).recommendedAction"
                  type="info"
                  :closable="false"
                  class="review-action-alert"
                />

                <!-- 问题截图 + 红框定位 -->
                <div v-if="reviewOf(row).issues?.length" class="issue-list">
                  <div v-for="(it, idx) in reviewOf(row).issues" :key="idx" class="issue-item">
                    <div class="issue-head">
                      <el-tag size="small" :type="it.imageIndex ? 'danger' : 'warning'" effect="plain">
                        {{ it.label || (it.imageIndex ? `图${it.imageIndex}` : 'AI 复核要点') }}<template v-if="it.stepIndex"> · 步骤{{ it.stepIndex }}</template>
                      </el-tag>
                      <span class="issue-problem">{{ it.problem }}</span>
                    </div>
                    <div v-if="it.screenshot && it.bbox" class="bbox-wrap">
                      <img :src="fileUrl(row.id, it.screenshot)" class="bbox-img" :alt="it.label || '问题截图'" />
                      <span class="bbox" :style="bboxStyle(it.bbox)"></span>
                    </div>
                  </div>
                </div>

                <div v-if="reviewOf(row).reviewer === 'manual'" class="review-meta">
                  人工复核：{{ reviewOf(row).reviewerNote || '无备注' }}
                </div>
                <div v-if="row.status === 'needs_review'" class="review-actions">
                  <el-button size="small" type="success" :loading="reviewingId === row.id" @click="onManualReview(row, 'pass')">
                    人工通过
                  </el-button>
                  <el-button size="small" type="danger" :loading="reviewingId === row.id" @click="onManualReview(row, 'fail')">
                    判为失败
                  </el-button>
                  <span class="review-hint">AI 判定有异常，需人工确认</span>
                </div>
              </div>
              <div v-else-if="detailMap[row.id].detail" class="review-box">
                <div class="review-head">
                  <span class="review-meta">该次执行还没有 AI 审核记录</span>
                  <el-button
                    size="small"
                    type="primary"
                    :loading="reviewingId === row.id"
                    @click="rerunReview(row)"
                  >
                    开始 AI 审核
                  </el-button>
                  <span class="review-meta">使用当前 LLM 审核关键截图</span>
                </div>
                <div v-if="row.status === 'needs_review'" class="review-actions">
                  <el-button size="small" type="success" :loading="reviewingId === row.id" @click="onManualReview(row, 'pass')">
                    人工通过
                  </el-button>
                  <el-button size="small" type="danger" :loading="reviewingId === row.id" @click="onManualReview(row, 'fail')">
                    判为失败
                  </el-button>
                  <span class="review-hint">缺少 AI 审核记录，请先发起 AI 审核，或直接查看录屏后人工判定</span>
                </div>
              </div>

              <!-- 同一用例的历史执行：主列表只展示最新一次，历史从这里跳转到独立页面查看 -->
              <div v-if="row.executionCount > 1" class="history-tip">
                <span class="muted">该用例共执行 {{ row.executionCount }} 次，最新记录在此展示</span>
                <el-button link type="primary" size="small" @click="goHistory(row)">查看全部执行历史</el-button>
              </div>

              <div v-if="detailMap[row.id].steps?.length" class="steps">
                <div v-for="s in detailMap[row.id].steps" :key="s.stepIndex" class="step">
                  <div class="step-head">
                    <el-tag size="small" :type="stepTag(s.result)">{{ stepLabel(s.result) }}</el-tag>
                    <span class="step-action">{{ s.stepIndex }}. {{ s.action || s.stepType }}</span>
                    <span v-if="s.selectorUsed" class="step-selector">定位：{{ s.selectorUsed }}</span>
                  </div>
                  <div v-if="s.error" class="step-error">{{ s.error }}</div>
                  <div v-if="s.screenshotBefore || s.screenshotAfter" class="shots">
                    <div v-if="s.screenshotBefore" class="shot">
                      <div class="shot-label">操作前</div>
                      <el-image
                        :src="fileUrl(row.id, s.screenshotBefore)"
                        :preview-src-list="[fileUrl(row.id, s.screenshotBefore)]"
                        fit="contain"
                        class="shot-img"
                        preview-teleported
                      />
                    </div>
                    <div v-if="s.screenshotAfter" class="shot">
                      <div class="shot-label">操作后</div>
                      <el-image
                        :src="fileUrl(row.id, s.screenshotAfter)"
                        :preview-src-list="[fileUrl(row.id, s.screenshotAfter)]"
                        fit="contain"
                        class="shot-img"
                        preview-teleported
                      />
                    </div>
                  </div>
                </div>
              </div>
              <div v-else class="loading-tip">该次执行没有步骤记录</div>
            </template>
          </div>
        </template>
      </el-table-column>
      <el-table-column
        prop="testCaseTitle"
        :label="historyMode ? '执行记录' : '用例标题'"
        :min-width="isNarrow ? 160 : 200"
      >
        <template #default="{ row }">
          <div class="case-cell">
            <template v-if="historyMode">
              <span class="case-title" :title="row.id">{{ row.id }}</span>
            </template>
            <template v-else>
              <span class="case-title" :title="row.testCaseTitle">{{ row.testCaseTitle }}</span>
              <span class="case-id">{{ row.id }}</span>
            </template>
          </div>
        </template>
      </el-table-column>
      <el-table-column v-if="historyMode" label="步骤" width="90">
        <template #default="{ row }">
          <span v-if="row.totalSteps != null" class="muted">{{ row.passedSteps ?? 0 }}/{{ row.totalSteps }}</span>
          <span v-else class="muted">—</span>
        </template>
      </el-table-column>
      <el-table-column v-if="!historyMode" label="执行次数" width="90">
        <template #default="{ row }">
          <el-tag
            size="small"
            :type="row.executionCount > 1 ? 'warning' : 'info'"
            effect="plain"
          >
            {{ row.executionCount || 1 }}
          </el-tag>
        </template>
      </el-table-column>
      <el-table-column v-if="!isNarrow" label="需求版本" width="90">
        <template #default="{ row }">
          <el-tooltip v-if="row.requirementVersion" :content="row.requirementName || row.requirementVersion" placement="top">
            <el-tag size="small" effect="plain">{{ row.requirementVersion }}</el-tag>
          </el-tooltip>
          <span v-else class="muted">—</span>
        </template>
      </el-table-column>
      <el-table-column label="状态" width="100">
        <template #default="{ row }">
          <div class="status-cell">
            <el-tooltip
              :content="row.status === 'needs_review'
                ? reviewTooltip(row)
                : (row.statusDescription || statusDescription(row.status))"
              placement="top"
            >
              <el-tag size="small" :type="row.statusTag || execTag(row.status)">
                {{ row.statusLabel || statusLabel(row.status) }}
              </el-tag>
            </el-tooltip>
            <span v-if="row.durationMs != null" class="duration">{{ row.durationMs }}ms</span>
          </div>
        </template>
      </el-table-column>
      <el-table-column v-if="!isNarrow" label="AI 审核" width="118">
        <template #default="{ row }">
          <el-tooltip :content="reviewTooltip(row)" placement="top">
            <span class="review-tag" @click.stop="openReviewReport(row)">
              <el-tag size="small" :type="reviewDisplay(row).tag">{{ reviewDisplay(row).label }}</el-tag>
            </span>
          </el-tooltip>
        </template>
      </el-table-column>
      <el-table-column v-if="!isNarrow" label="错误分类" width="120" show-overflow-tooltip>
        <template #default="{ row }">
          <el-tooltip
            v-if="row.errorCategory"
            :content="`${row.errorCategory}：${row.errorCategoryDescription || '执行失败'}`"
            placement="top"
          >
            <el-tag size="small" :type="row.errorCategoryTag || 'info'">
              {{ row.errorCategoryLabel || row.errorCategory }}
            </el-tag>
          </el-tooltip>
          <span v-else class="muted">—</span>
        </template>
      </el-table-column>
      <el-table-column label="开始时间" width="160">
        <template #default="{ row }">{{ row.startTime ? new Date(row.startTime).toLocaleString() : '—' }}</template>
      </el-table-column>
      <el-table-column label="操作" width="210" :fixed="isNarrow ? false : 'right'">
        <template #default="{ row }">
          <el-button link type="primary" @click="toggleDetail(row)">详情</el-button>
          <el-button
            v-if="!historyMode && row.executionCount > 1"
            link
            type="warning"
            @click="goHistory(row)"
          >历史</el-button>
          <el-button
            link
            type="success"
            :loading="rerunningId === row.id"
            :disabled="row.status === 'running' || rerunningId === row.id"
            @click="rerun(row)"
          >重新执行</el-button>
        </template>
      </el-table-column>
    </el-table>

    <el-pagination
      v-model:current-page="page"
      v-model:page-size="pageSize"
      :total="total"
      :page-sizes="[20, 50, 100]"
      layout="total, sizes, prev, pager, next"
      class="pager"
      @current-change="load"
      @size-change="onSizeChange"
    />

    <!-- AI 审核详情报告：逐图分析 -->
    <el-dialog
      v-model="reviewDialog"
      :title="`AI 审核详情 · ${reviewDialogRow?.id || ''}`"
      width="860px"
      top="6vh"
      destroy-on-close
    >
      <div v-if="reportReview" class="report">
        <div class="report-head">
          <el-tag :type="reviewTag(reportReview.verdict)" size="small">
            AI 结论：{{ reviewLabel(reportReview.verdict) }}
          </el-tag>
          <span class="muted">
            模型 {{ reportReview.model || '—' }} · {{ reportReview.imageCount }} 张截图
          </span>
        </div>

        <el-alert
          :title="reportReview.reason || '无结论说明'"
          type="info"
          :closable="false"
          show-icon
          class="report-reason"
        />

        <div v-for="img in reportImages" :key="img.imageIndex" class="report-image">
          <div class="report-image-head">
            <strong>图{{ img.imageIndex }}</strong>
            <el-tag size="small" :type="img.verdict === 'suspect' ? 'danger' : 'success'">
              {{ img.verdict === 'suspect' ? '疑似异常' : '正常' }}
            </el-tag>
            <span class="muted">{{ img.label }}</span>
          </div>
          <div v-if="img.screenshot" class="bbox-wrap">
            <img
              :src="fileUrl(reviewDialogRow?.id, img.screenshot)"
              class="bbox-img report-img"
              :alt="img.label || '截图'"
            />
            <span v-if="reportBbox(img)" class="bbox" :style="bboxStyle(reportBbox(img))"></span>
          </div>
          <p class="report-analysis">{{ img.analysis }}</p>
          <p v-if="issueForImage(img)" class="issue-problem">问题：{{ issueForImage(img).problem }}</p>
        </div>

        <div v-if="!reportImages.length" class="report-empty">
          <p class="muted">该次审核没有逐图分析（历史审核记录）。</p>
          <div v-for="(it, idx) in reportReview.issues || []" :key="idx" class="issue-item">
            <div class="issue-head">
              <el-tag size="small" type="danger" effect="plain">图{{ it.imageIndex || '?' }}</el-tag>
              <span class="issue-problem">{{ it.problem }}</span>
            </div>
            <div v-if="it.screenshot && it.bbox" class="bbox-wrap">
              <img :src="fileUrl(reviewDialogRow?.id, it.screenshot)" class="bbox-img" alt="问题截图" />
              <span class="bbox" :style="bboxStyle(it.bbox)"></span>
            </div>
          </div>
        </div>
      </div>
    </el-dialog>
  </div>
</template>

<script setup>
import { computed, onBeforeUnmount, onMounted, ref, watch } from 'vue'
import { useRouter } from 'vue-router'
import { ElMessage, ElMessageBox } from 'element-plus'
import { getCoverageMatrix, getExecution, getReport, getSteps, listExecutions } from '../api/mindmap'
import { batchLlmReview, executeCase, manualReview, rerunLlmReview } from '../api/executions'
import { listRequirements } from '../api/requirements'
import { getOverview } from '../api/stats'

const props = defineProps({
  projectId: { type: String, required: true },
  // 历史模式：只展示某个用例的全部执行记录（不再按用例去重）
  caseId: { type: String, default: '' },
  historyMode: { type: Boolean, default: false },
})
const router = useRouter()

const STATUS = [
  { v: 'passed', l: '通过' },
  { v: 'failed', l: '失败' },
  { v: 'needs_review', l: '待复核' },
  { v: 'running', l: '执行中' },
  { v: 'skipped', l: '跳过' },
  { v: 'cancelled', l: '已取消' },
]
const STATUS_TEXT = {
  passed: '通过',
  failed: '失败',
  needs_review: '待复核',
  running: '执行中',
  skipped: '跳过',
  cancelled: '已取消',
}
const STATUS_DESCRIPTION = {
  passed: '确定性检查通过，或证据经 AI/人工复核确认符合预期',
  failed: '出现确定错误，或人工复核确认不符合预期',
  needs_review: '证据不足、AI 判断有疑点或审核不可用，需要人工确认',
  running: '正在真实浏览器中执行，尚无最终结论',
  skipped: '存在无法验证的步骤，缺少充分证据',
  cancelled: '执行被中止，不能视为通过',
}

const tableRef = ref()
const rows = ref([])
const loading = ref(false)
const matrix = ref(null)
const overview = ref(null)
const requirements = ref([])
const requirementId = ref('')
const status = ref('')
const page = ref(1)
const pageSize = ref(20)
const total = ref(0)
const reporting = ref(false)
const reviewingId = ref('')
const rerunningId = ref('')
const batchReviewing = ref(false)
const selectedRows = ref([])
const isNarrow = ref(false)
const narrowMq = window.matchMedia('(max-width: 900px)')
function onNarrowChange(e) {
  isNarrow.value = e.matches
}
// 展开行详情缓存：{ [executionId]: { steps, detail, loading } }
const detailMap = ref({})
let refreshTimer = null

const blocks = computed(() => {
  if (!matrix.value) return []
  return [
    { key: 'apiEndpoint', label: '接口', value: matrix.value.apiEndpoint },
    { key: 'stateTransition', label: '状态转换', value: matrix.value.stateTransition },
    { key: 'businessRule', label: '业务规则', value: matrix.value.businessRule },
  ]
})
const coverageWarning = computed(() => {
  if (!matrix.value) return ''
  const items = blocks.value.filter((b) => b.value?.total > 0)
  if (!items.length) return '当前没有可统计的代码事实，请先完成代码分析'
  if (items.every((b) => b.value.plannedCovered === 0)) {
    return '当前范围内没有用例关联覆盖点。请按需求版本重新生成用例，或检查测试点是否关联了真实接口/状态转换/规则'
  }
  if (items.every((b) => b.value.executedCovered === 0)) {
    return '已有规划覆盖，但还没有通过的执行记录；失败、跳过、待复核和取消都不会计入执行覆盖'
  }
  return ''
})

const latestRequirementId = computed(() =>
  requirements.value.length ? requirements.value[requirements.value.length - 1].id : null,
)
const isLatestVersion = computed(
  () => !!requirementId.value && requirementId.value === latestRequirementId.value,
)
const selectedVersionLabel = computed(() => {
  if (requirementId.value === '__none__') return '未关联版本'
  if (!requirementId.value) return ''
  return requirements.value.find((r) => r.id === requirementId.value)?.version || '当前版本'
})
const emptyText = computed(() => {
  if (loading.value) return '加载中...'
  if (props.historyMode) return '该用例暂无执行记录'
  if (selectedVersionLabel.value) return `${selectedVersionLabel.value} 暂无执行记录`
  return '暂无执行记录，先在「用例列表」执行用例'
})

async function loadRequirements() {
  if (!props.projectId) return
  try {
    requirements.value = await listRequirements(props.projectId)
  } catch {
    requirements.value = []
  }
}

function onRequirementChange() {
  page.value = 1
  load()
}

async function load() {
  if (!props.projectId) return
  if (props.historyMode && !props.caseId) return
  loading.value = true
  try {
    const data = await listExecutions(props.projectId, {
      page: page.value,
      pageSize: pageSize.value,
      groupBy: props.historyMode ? 'execution' : 'case',
      testCaseId: props.historyMode ? props.caseId : undefined,
      status: status.value || undefined,
      requirementId: requirementId.value || undefined,
    })
    const items = data.items || []
    if (props.historyMode) {
      rows.value = items
      total.value = data.total || 0
    } else {
      const isGrouped = data.groupedBy === 'case'
      rows.value = isGrouped ? items : dedupExecutionItems(items)
      total.value = isGrouped ? (data.total || 0) : rows.value.length
      matrix.value = await getCoverageMatrix(props.projectId, requirementId.value || undefined)
      overview.value = await getOverview()
    }
  } finally {
    loading.value = false
    scheduleRefresh()
  }
}

function applyFilter() {
  page.value = 1
  load()
}

async function rerun(row) {
  const caseId = row.testCaseId || props.caseId
  if (!caseId) {
    ElMessage.warning('找不到对应测试用例，无法重新执行')
    return
  }
  try {
    await ElMessageBox.confirm(
      `重新执行用例「${row.testCaseTitle || caseId}」？会创建一条新的执行记录，原记录保留。`,
      '重新执行',
      { type: 'info', confirmButtonText: '开始执行', cancelButtonText: '取消' },
    )
  } catch {
    return
  }
  const targetUrl = detailMap.value[row.id]?.detail?.targetUrl || undefined
  rerunningId.value = row.id
  try {
    const result = await executeCase(props.projectId, caseId, targetUrl)
    ElMessage.success(
      `执行完成：${result.statusLabel || result.status}${result.executionId ? `（${result.executionId}）` : ''}`,
    )
    await load()
  } finally {
    rerunningId.value = ''
  }
}

function onSizeChange() {
  page.value = 1
  load()
}

function dedupExecutionItems(items) {
  const byCase = new Map()
  const executionIds = new Map()
  items.forEach((item) => {
    const key = item.testCaseId || item.id
    executionIds.set(key, new Set([...(executionIds.get(key) || []), item.id]))
    const current = byCase.get(key)
    const currentTime = new Date(item.startTime || 0).getTime()
    const selectedTime = current ? new Date(current.startTime || 0).getTime() : -1
    if (!current || currentTime > selectedTime || (currentTime === selectedTime && item.id > current.id)) {
      byCase.set(key, item)
    }
  })
  return [...byCase.values()]
    .map((item) => ({
      ...item,
      executionCount: item.executionCount || executionIds.get(item.testCaseId || item.id)?.size || 1,
    }))
    .sort((a, b) => new Date(b.startTime || 0) - new Date(a.startTime || 0))
}

// 有「执行中」记录时自动轮询，避免用户手动刷新
function scheduleRefresh() {
  clearTimer()
  if (rows.value.some((r) => r.status === 'running')) {
    refreshTimer = setTimeout(() => load(), 4000)
  }
}

function clearTimer() {
  if (refreshTimer) {
    clearTimeout(refreshTimer)
    refreshTimer = null
  }
}

async function refreshDetail(eid) {
  try {
    const [steps, detail] = await Promise.all([getSteps(eid), getExecution(eid)])
    detailMap.value[eid] = { steps: steps || [], detail, loading: false }
  } catch {
    detailMap.value[eid] = { steps: [], detail: null, loading: false }
  }
}

async function onExpand(row, expandedRows) {
  const isExpanded = expandedRows.some((r) => r.id === row.id)
  if (!isExpanded || detailMap.value[row.id]) return
  detailMap.value[row.id] = { steps: [], detail: null, loading: true }
  await refreshDetail(row.id)
}

// 跳转到该用例的独立执行历史页面
function goHistory(row) {
  router.push({
    name: 'CaseHistory',
    params: { id: props.projectId, caseId: row.testCaseId },
  })
}

// 优先取详情接口返回的审核结果（重新审核后是最新值），否则用列表里的
function reviewOf(row) {
  return detailMap.value[row.id]?.detail?.llmReview || row.llmReview || null
}

function reviewHint(row) {
  const review = reviewOf(row)
  if (!review) {
    return '缺少 AI 审核记录。请展开步骤和录屏检查关键结果，或点击「开始 AI 审核」生成复核要点。'
  }
  const points = review.reviewPoints || (review.issues || []).map((item) => item.problem).filter(Boolean)
  if (!points.length) {
    return review.reason || 'AI 证据不足或判断有疑点，请查看截图、步骤和录屏后人工判定。'
  }
  return `共 ${points.length} 个复核点：${points.join('；')}`
}

function reviewTag(verdict) {
  return { ok: 'success', suspect: 'danger', unavailable: 'info' }[verdict] || 'info'
}

function reviewLabel(verdict) {
  return {
    ok: '证据支持预期',
    suspect: '疑似异常',
    unavailable: '审核不可用',
  }[verdict] || '未审核'
}

function reviewDisplay(row) {
  const review = row.llmReview
  if (!review) return { label: '未审核', tag: 'info' }
  if (review.reviewer === 'manual') {
    return row.status === 'passed'
      ? { label: '人工通过', tag: 'success' }
      : { label: '人工失败', tag: 'danger' }
  }
  return { label: reviewLabel(review.verdict), tag: reviewTag(review.verdict) }
}

function reviewTooltip(row) {
  const review = reviewOf(row)
  if (!review) return '尚未审核，展开详情可手动发起 AI 审核'
  if (review.reviewer === 'manual') {
    return review.reviewerNote || `人工复核（AI 原判：${reviewLabel(review.verdict)}）`
  }
  const points = review.reviewPoints || (review.issues || []).map((item) => item.problem).filter(Boolean)
  const prefix = points.length ? `${points.length} 个复核点：` : ''
  return `${prefix}${points[0] || review.reason || reviewLabel(review.verdict)}`
}

// 审核详情报告
const reviewDialog = ref(false)
const reviewDialogRow = ref(null)
const reportReview = computed(() => {
  const row = reviewDialogRow.value
  if (!row) return null
  return detailMap.value[row.id]?.detail?.llmReview || row.llmReview || null
})
const reportImages = computed(() => reportReview.value?.imageReviews || [])

function openReviewReport(row) {
  const review = row.llmReview || detailMap.value[row.id]?.detail?.llmReview
  if (!review) {
    ElMessage.info('该执行还没有 AI 审核结果')
    return
  }
  reviewDialogRow.value = row
  reviewDialog.value = true
}

function issueForImage(img) {
  return (reportReview.value?.issues || []).find((it) => it.imageIndex === img.imageIndex) || null
}

function reportBbox(img) {
  return img.bbox || issueForImage(img)?.bbox || null
}

async function onManualReview(row, decision) {
  let note = ''
  try {
    const { value } = await ElMessageBox.prompt(
      decision === 'pass' ? '确认截图符合预期？可填写复核备注' : '确认判定为失败？建议填写原因',
      decision === 'pass' ? '人工通过' : '判为失败',
      { confirmButtonText: '确定', cancelButtonText: '取消', inputPlaceholder: '复核备注（可选）' },
    )
    note = (value || '').trim()
  } catch {
    return // 取消
  }
  reviewingId.value = row.id
  try {
    await manualReview(row.id, decision, note || undefined)
    ElMessage.success(decision === 'pass' ? '已标记为通过' : '已标记为失败')
    await load()
    await refreshDetail(row.id)
  } finally {
    reviewingId.value = ''
  }
}

async function rerunReview(row) {
  reviewingId.value = row.id
  try {
    const r = await rerunLlmReview(row.id)
    ElMessage.success(`AI 审核完成：${reviewLabel(r?.llmReview?.verdict)}`)
    await load()
    await refreshDetail(row.id)
  } finally {
    reviewingId.value = ''
  }
}

function bboxStyle(bbox) {
  if (!Array.isArray(bbox) || bbox.length !== 4) return {}
  const [x1, y1, x2, y2] = bbox.map(Number)
  return {
    left: `${x1 * 100}%`,
    top: `${y1 * 100}%`,
    width: `${Math.max((x2 - x1) * 100, 1)}%`,
    height: `${Math.max((y2 - y1) * 100, 1)}%`,
  }
}

async function onBatchReview() {
  const rows = selectedRows.value
  if (!rows.length) return
  if (rows.length > 20) {
    return ElMessage.warning('单次最多选择 20 条执行记录')
  }
  const ids = rows.map((r) => r.id)
  try {
    await ElMessageBox.confirm(
      `对选中的 ${ids.length} 条执行调用 AI 审核？每条约需数秒，请勿关闭页面。`,
      '批量 AI 审核',
      { type: 'warning', confirmButtonText: '开始审核', cancelButtonText: '取消' },
    )
  } catch {
    return
  }
  batchReviewing.value = true
  try {
    const r = await batchLlmReview(ids)
    const failed = (r?.results || []).filter((x) => x.error).length
    if (failed) {
      ElMessage.warning(`AI 审核完成：成功 ${r.reviewed}/${r.total} 条，失败 ${failed} 条`)
    } else {
      ElMessage.success(`AI 审核完成：${r.reviewed}/${r.total} 条`)
    }
    tableRef.value?.clearSelection()
    await load()
    await Promise.all(ids.filter((id) => detailMap.value[id]).map((id) => refreshDetail(id)))
  } finally {
    batchReviewing.value = false
  }
}

// 「详情」按钮：切换该行展开/收起（展开会触发 onExpand 懒加载步骤）
function toggleDetail(row) {
  tableRef.value?.toggleRowExpansion(row)
}

function fileUrl(eid, path) {
  return `/api/executions/${eid}/file?path=${encodeURIComponent(path)}`
}

async function downloadReport() {
  reporting.value = true
  try {
    const blob = await getReport(props.projectId, requirementId.value || undefined)
    const url = URL.createObjectURL(blob)
    const a = document.createElement('a')
    a.href = url
    a.download = 'report.html'
    a.click()
    URL.revokeObjectURL(url)
  } finally {
    reporting.value = false
  }
}

function statusLabel(s) {
  return STATUS_TEXT[s] || s
}
function statusDescription(s) {
  return STATUS_DESCRIPTION[s] || ''
}
function execTag(s) {
  return {
    passed: 'success',
    failed: 'danger',
    skipped: 'warning',
    running: 'primary',
    needs_review: 'warning',
    cancelled: 'info',
  }[s] || 'info'
}
function stepTag(s) {
  return { passed: 'success', failed: 'danger', skipped: 'warning' }[s] || 'info'
}
function stepLabel(s) {
  return { passed: '通过', failed: '失败', skipped: '跳过' }[s] || s
}

onMounted(async () => {
  isNarrow.value = narrowMq.matches
  narrowMq.addEventListener('change', onNarrowChange)
  // 先确定默认版本，再拉执行记录
  await loadRequirements()
  load()
})
onBeforeUnmount(() => {
  narrowMq.removeEventListener('change', onNarrowChange)
  clearTimer()
})
watch(
  () => [props.projectId, props.caseId],
  async () => {
    page.value = 1
    status.value = ''
    requirementId.value = ''
    detailMap.value = {}
    await loadRequirements()
    load()
  },
)
</script>

<style scoped>
.toolbar {
  display: flex;
  gap: 10px;
  align-items: center;
  margin-bottom: 12px;
  flex-wrap: wrap;
}
.spacer {
  flex: 1;
}
.card {
  margin-bottom: 12px;
}
.cov-head {
  display: flex;
  align-items: baseline;
  gap: 10px;
  flex-wrap: wrap;
}
.coverage-alert {
  margin-bottom: 12px;
}
.cov-block {
  padding: 10px 0;
  border-bottom: 1px dashed var(--el-border-color-lighter);
}
.cov-block:last-child {
  border-bottom: none;
}
.cov-title,
.cov-progress {
  display: flex;
  align-items: center;
  gap: 10px;
}
.cov-title {
  justify-content: space-between;
  margin-bottom: 6px;
}
.cov-kind {
  width: 42px;
  flex: none;
  color: var(--el-text-color-secondary);
}
.cov-progress {
  margin: 4px 0;
}
.cov-progress :deep(.el-progress) {
  flex: 1;
  min-width: 0;
}
.cov-uncovered {
  margin-top: 6px;
  color: var(--el-color-warning-dark-2);
  font-size: 12px;
  line-height: 1.5;
}
.table {
  margin-top: 4px;
}
.pager {
  margin-top: 12px;
  justify-content: flex-end;
}
.err {
  color: var(--el-color-danger);
}
.history-tip {
  display: flex;
  align-items: center;
  gap: 10px;
  margin-top: 12px;
  padding: 8px 12px;
  border: 1px dashed var(--el-border-color);
  border-radius: 6px;
  background: #fafafa;
}
.case-cell {
  display: flex;
  flex-direction: column;
  min-width: 0;
}
.case-title {
  overflow: hidden;
  text-overflow: ellipsis;
  white-space: nowrap;
}
.case-id {
  color: var(--el-text-color-secondary);
  font-size: 11px;
}
.muted {
  color: var(--el-text-color-secondary);
}
.status-cell {
  display: flex;
  align-items: center;
  gap: 6px;
  flex-wrap: wrap;
}
.duration {
  color: var(--el-text-color-secondary);
  font-size: 12px;
}
.review-box {
  border: 1px solid var(--el-border-color-light);
  border-radius: 6px;
  padding: 10px 12px;
  margin: 10px 0;
  background: #fff;
}
.review-head {
  display: flex;
  align-items: center;
  gap: 10px;
  flex-wrap: wrap;
}
.review-meta {
  color: var(--el-text-color-secondary);
  font-size: 12px;
}
.review-tag {
  display: inline-flex;
  align-items: center;
  gap: 6px;
  cursor: pointer;
}
.report {
  max-height: 72vh;
  overflow-y: auto;
  padding-right: 6px;
}
.report-head {
  display: flex;
  align-items: center;
  gap: 10px;
  flex-wrap: wrap;
  margin-bottom: 10px;
}
.report-reason {
  margin-bottom: 14px;
}
.report-image {
  border-top: 1px dashed var(--el-border-color);
  padding: 12px 0;
}
.report-image-head {
  display: flex;
  align-items: center;
  gap: 8px;
  margin-bottom: 8px;
  flex-wrap: wrap;
}
.report-img {
  max-width: 720px;
}
.report-analysis {
  margin: 8px 0 0;
  font-size: 13px;
  line-height: 1.6;
}
.report-empty {
  padding: 8px 0;
}
.review-reason {
  margin-top: 6px;
  font-size: 13px;
}
.review-action-alert {
  margin-top: 8px;
}
.review-actions {
  display: flex;
  align-items: center;
  gap: 8px;
  margin-top: 8px;
  flex-wrap: wrap;
}
.issue-list {
  display: flex;
  flex-direction: column;
  gap: 10px;
  margin-top: 8px;
}
.issue-head {
  display: flex;
  align-items: flex-start;
  gap: 8px;
  flex-wrap: wrap;
}
.issue-problem {
  color: var(--el-color-danger);
  font-size: 13px;
}
.bbox-wrap {
  position: relative;
  display: inline-block;
  max-width: 100%;
  margin-top: 6px;
}
.bbox-img {
  display: block;
  width: 100%;
  max-width: 640px;
  border: 1px solid var(--el-border-color);
  border-radius: 4px;
}
.bbox {
  position: absolute;
  border: 2px solid #f56c6c;
  background: rgba(245, 108, 108, 0.12);
  border-radius: 2px;
  pointer-events: none;
  box-shadow: 0 0 0 1px rgba(255, 255, 255, 0.6) inset;
}
.review-hint {
  color: var(--el-color-warning);
  font-size: 12px;
}
.no-review {
  margin: 6px 0;
}
.expand-detail {
  padding: 12px 24px;
  background: var(--el-fill-color-lighter);
}
.alert {
  margin-bottom: 8px;
}
.meta {
  display: flex;
  gap: 20px;
  flex-wrap: wrap;
  color: var(--el-text-color-secondary);
  font-size: 13px;
  margin-bottom: 8px;
}
.video-link {
  color: var(--el-color-primary);
}
.steps {
  display: flex;
  flex-direction: column;
  gap: 12px;
}
.step {
  border-left: 3px solid var(--el-border-color);
  padding-left: 12px;
}
.step-head {
  display: flex;
  align-items: center;
  gap: 10px;
  flex-wrap: wrap;
}
.step-action {
  font-weight: 600;
}
.step-selector {
  color: var(--el-text-color-secondary);
  font-size: 12px;
}
.step-error {
  color: var(--el-color-danger);
  font-size: 13px;
  margin-top: 4px;
}
.shots {
  display: flex;
  gap: 16px;
  margin-top: 8px;
  flex-wrap: wrap;
}
.shot-label {
  font-size: 12px;
  color: var(--el-text-color-secondary);
  margin-bottom: 4px;
}
.shot-img {
  width: 320px;
  max-height: 220px;
  border: 1px solid var(--el-border-color);
  border-radius: 4px;
  cursor: zoom-in;
}
.loading-tip {
  color: var(--el-text-color-secondary);
}

@media (max-width: 768px) {
  .shot-img {
    width: 100%;
  }
}
</style>
