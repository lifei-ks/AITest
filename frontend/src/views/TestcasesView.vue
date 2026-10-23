<template>
  <div class="page-layout">
    <ProjectSidebar v-if="showSidebar" />

    <div class="page-content">
    <el-empty v-if="!projectId" description="请在左侧选择项目，或到「项目列表」进入项目">
      <el-button type="primary" @click="$router.push('/projects')">去项目列表</el-button>
    </el-empty>

    <template v-else>
      <!-- 筛选栏 -->
      <div class="filter-bar">
        <div class="filters">
          <el-select v-model="filters.requirementId" placeholder="全部需求版本" clearable style="width: 160px" @change="onRequirementChange">
            <el-option v-for="r in requirements" :key="r.id" :label="`${r.version} · ${r.name}`" :value="r.id" />
            <el-option label="未关联版本" value="__none__" />
          </el-select>
          <el-tag v-if="isLatestVersion" size="small" type="success" effect="plain">最新</el-tag>
          <el-select v-model="filters.type" placeholder="类型" clearable style="width: 120px" @change="applyFilter">
            <el-option v-for="t in TYPES" :key="t.v" :label="t.l" :value="t.v" />
          </el-select>
          <el-select v-model="filters.priority" placeholder="优先级" clearable style="width: 110px" @change="applyFilter">
            <el-option v-for="p in ['P0', 'P1', 'P2']" :key="p" :label="p" :value="p" />
          </el-select>
          <el-select v-model="filters.executionStatus" placeholder="执行状态" clearable style="width: 130px" @change="applyFilter">
            <el-option v-for="s in STATUS" :key="s.v" :label="s.l" :value="s.v" />
          </el-select>
          <el-input
            v-model="filters.keyword"
            placeholder="标题关键词"
            clearable
            style="width: 200px"
            @keyup.enter="applyFilter"
            @clear="applyFilter"
          />
          <el-button type="primary" @click="applyFilter">查询</el-button>
        </div>
        <div class="actions">
          <el-button :loading="loading" title="刷新" @click="load">
            <el-icon><Refresh /></el-icon>
          </el-button>
          <el-upload :show-file-list="false" accept=".csv" :before-upload="onImport" :disabled="importing">
            <el-button :loading="importing">导入 CSV</el-button>
          </el-upload>
          <el-button :loading="exporting" @click="onExport">导出 CSV</el-button>
          <el-button
            type="warning"
            plain
            :loading="aiReviewing"
            :disabled="!rows.length"
            @click="onAiReview"
          >
            AI 质检{{ selection.length ? `(${selection.length})` : '' }}
          </el-button>
          <el-button type="success" :loading="batchExecuting" @click="onExecuteAll">全部执行</el-button>
          <el-button v-if="selection.length" type="primary" :loading="batchExecuting" @click="onBatchExecute">
            批量执行({{ selection.length }})
          </el-button>
          <el-button v-if="selection.length" type="danger" @click="onBatchDelete">批量删除({{ selection.length }})</el-button>
        </div>
      </div>

      <el-table
        ref="tableRef"
        :data="rows"
        border
        stripe
        row-key="id"
        v-loading="loading"
        :empty-text="emptyText"
        @selection-change="(v) => (selection = v)"
      >
        <el-table-column v-if="!isNarrow" type="selection" width="45" />
        <el-table-column prop="projectSeq" label="#" width="55" />
        <el-table-column v-if="!isNarrow" label="需求版本" width="110">
          <template #default="{ row }">
            <el-tag v-if="row.requirementId" size="small" effect="plain">{{ requirementLabel(row.requirementId) }}</el-tag>
            <span v-else class="muted">—</span>
          </template>
        </el-table-column>
        <el-table-column prop="title" label="标题" min-width="200" show-overflow-tooltip />
        <el-table-column v-if="!isNarrow" label="类型" width="80">
          <template #default="{ row }"><el-tag size="small" :type="typeTag(row.type)">{{ typeLabel(row.type) }}</el-tag></template>
        </el-table-column>
        <el-table-column prop="priority" label="优先级" width="76" />
        <el-table-column v-if="!isNarrow" label="质量分" width="80">
          <template #default="{ row }">
            <el-tag
              size="small"
              :type="row.qualityScore >= 80 ? 'success' : row.qualityScore >= 40 ? 'warning' : 'danger'"
            >{{ row.qualityScore }}</el-tag>
          </template>
        </el-table-column>
        <el-table-column v-if="!isNarrow" label="AI 质检" width="104">
          <template #default="{ row }">
            <el-tooltip
              v-if="caseReviewMap[row.id]"
              :content="caseReviewMap[row.id].reason || reviewLabel(caseReviewMap[row.id].verdict)"
              placement="top"
            >
              <el-tag
                size="small"
                :type="reviewTag(caseReviewMap[row.id].verdict)"
                class="review-link"
                @click="openAiReviewDialog(row.id)"
              >
                {{ reviewLabel(caseReviewMap[row.id].verdict) }}
              </el-tag>
            </el-tooltip>
            <span v-else class="muted">—</span>
          </template>
        </el-table-column>
        <el-table-column label="执行状态" width="90">
          <template #default="{ row }">
            <el-tooltip :content="row.executionStatusDescription || execDescription(row.executionStatus)" placement="top">
              <el-tag size="small" :type="row.executionStatusTag || execTag(row.executionStatus)">
                {{ row.executionStatusLabel || execLabel(row.executionStatus) }}
              </el-tag>
            </el-tooltip>
          </template>
        </el-table-column>
        <el-table-column label="操作" :width="isNarrow ? 155 : 210" :fixed="isNarrow ? false : 'right'">
          <template #default="{ row }">
            <el-button link type="success" :loading="executingId === row.id" @click="onExecute(row)">执行</el-button>
            <el-button link type="primary" @click="openEdit(row)">编辑</el-button>
            <el-button v-if="!isNarrow" link @click="openVersions(row)">版本</el-button>
            <el-button link type="danger" @click="onDelete(row)">删除</el-button>
          </template>
        </el-table-column>
      </el-table>

      <el-pagination
        v-model:current-page="page"
        v-model:page-size="pageSize"
        :total="total"
        :page-sizes="[10, 20, 50, 100]"
        layout="total, sizes, prev, pager, next"
        class="pager"
        @current-change="load"
        @size-change="onSizeChange"
      />

      <!-- AI 用例质检与修复 -->
      <el-dialog
        v-model="aiReviewVisible"
        title="AI 用例质检与修复"
        width="860px"
        top="5vh"
        destroy-on-close
      >
        <el-alert
          :title="aiReviewSummary"
          :type="aiReviewSummaryType"
          :closable="false"
          show-icon
          class="mb"
        />
        <div v-for="result in aiReviewResults" :key="result.caseId" class="ai-review-item">
          <div class="review-head">
            <div class="review-title">
              <strong>{{ result.title }}</strong>
              <span class="muted">{{ result.caseId }}</span>
            </div>
            <div class="review-actions">
              <el-tag size="small" :type="reviewTag(result.verdict)">
                {{ reviewLabel(result.verdict) }}
              </el-tag>
              <el-button
                v-if="result.verdict !== 'ok' && result.verdict !== 'unavailable'"
                size="small"
                type="primary"
                plain
                :loading="aiFixingId === result.caseId"
                @click="onAiRepair(result)"
              >
                AI 修复
              </el-button>
            </div>
          </div>
          <p class="review-reason">{{ result.reason }}</p>
          <p v-if="result.model" class="muted review-model">模型：{{ result.model }}</p>
          <div v-if="result.repairSummary" class="repair-summary">
            <el-tag size="small" type="success">已修复</el-tag>
            <span>{{ result.repairSummary }}</span>
          </div>
          <div v-if="result.issues?.length" class="issue-list">
            <div v-for="(issue, index) in result.issues" :key="index" class="issue-item">
              <el-tag size="small" :type="issueTag(issue.severity)">
                {{ issueLabel(issue.severity) }}
              </el-tag>
              <div class="issue-body">
                <div class="issue-problem">{{ issue.problem }}</div>
                <div v-if="issue.suggestion" class="issue-suggestion">建议：{{ issue.suggestion }}</div>
                <div v-if="issue.evidence" class="issue-evidence">依据：{{ issue.evidence }}</div>
              </div>
            </div>
          </div>
        </div>
      </el-dialog>

      <!-- 编辑抽屉 -->
      <el-drawer v-model="editVisible" title="编辑用例" size="760px" :close-on-click-modal="false">
        <el-form label-width="90px">
          <el-form-item label="标题" required>
            <el-input v-model="edit.title" maxlength="200" show-word-limit />
          </el-form-item>
          <el-row :gutter="12">
            <el-col :span="12">
              <el-form-item label="模块"><el-input v-model="edit.module" /></el-form-item>
            </el-col>
            <el-col :span="12">
              <el-form-item label="类型">
                <el-select v-model="edit.type" style="width: 100%">
                  <el-option v-for="t in TYPES" :key="t.v" :label="t.l" :value="t.v" />
                </el-select>
              </el-form-item>
            </el-col>
          </el-row>
          <el-form-item label="优先级">
            <el-radio-group v-model="edit.priority">
              <el-radio v-for="p in ['P0', 'P1', 'P2']" :key="p" :value="p">{{ p }}</el-radio>
            </el-radio-group>
          </el-form-item>
          <el-form-item label="前置条件">
            <el-input v-model="edit.preconditionsText" type="textarea" :rows="2" placeholder="一行一条" />
          </el-form-item>
          <el-form-item label="预期结果">
            <el-input v-model="edit.expectedText" type="textarea" :rows="2" placeholder="一行一条" />
          </el-form-item>

          <el-divider content-position="left">结构化步骤（执行器消费）</el-divider>
          <el-empty v-if="!edit.structuredSteps.length" description="暂无结构化步骤" :image-size="60" />
          <div v-for="(s, i) in edit.structuredSteps" :key="i" class="step-item">
            <div class="step-head">
              <span class="step-no">#{{ i + 1 }}</span>
              <el-select v-model="s.type" size="small" style="width: 120px">
                <el-option label="点击" value="ui_action" />
                <el-option label="输入" value="input" />
                <el-option label="断言" value="state_assert" />
              </el-select>
              <el-input v-model="s.action" placeholder="动作（如 点击登录按钮）" size="small" class="grow" />
              <el-button link type="primary" :disabled="i === 0" @click="moveStep(i, -1)">上移</el-button>
              <el-button link type="primary" :disabled="i === edit.structuredSteps.length - 1" @click="moveStep(i, 1)">下移</el-button>
              <el-button link type="danger" @click="removeStep(i)">删除</el-button>
            </div>
            <div class="step-body">
              <el-input v-model="s.target" placeholder="目标" size="small" style="width: 160px" />
              <el-select v-model="s.uiSelector.type" size="small" style="width: 120px">
                <el-option v-for="st in SELECTOR_TYPES" :key="st" :label="st" :value="st" />
              </el-select>
              <el-input v-model="s.uiSelector.value" placeholder="定位值" size="small" style="width: 180px" />
              <el-input v-if="s.type === 'input'" v-model="s.inputValue" placeholder="输入值" size="small" style="width: 160px" />
              <el-input v-if="s.type === 'state_assert'" v-model="s.expected" placeholder="断言文本" size="small" style="width: 200px" />
            </div>
          </div>
          <el-button size="small" @click="addStep">+ 添加步骤</el-button>
        </el-form>

        <template #footer>
          <el-button @click="editVisible = false">取消</el-button>
          <el-button type="primary" :loading="saving" @click="saveEdit">保存</el-button>
        </template>
      </el-drawer>

      <!-- 版本对话框 -->
      <el-dialog v-model="versionVisible" title="版本历史" width="600px">
        <el-table :data="versions" size="small" border :empty-text="versionsLoading ? '加载中...' : '暂无版本记录'">
          <el-table-column prop="version" label="版本" width="70" />
          <el-table-column prop="changeNote" label="说明" show-overflow-tooltip />
          <el-table-column prop="createdAt" label="时间" width="170">
            <template #default="{ row }">{{ row.createdAt ? new Date(row.createdAt).toLocaleString() : '—' }}</template>
          </el-table-column>
          <el-table-column label="操作" width="90">
            <template #default="{ row }">
              <el-button link type="primary" @click="onRollback(row)">回滚</el-button>
            </template>
          </el-table-column>
        </el-table>
      </el-dialog>
    </template>
    </div>
  </div>
</template>

<script setup>
import { computed, onBeforeUnmount, onMounted, reactive, ref, watch } from 'vue'
import { ElMessage, ElMessageBox } from 'element-plus'
import {
  aiFixCase, aiReviewCases, batchDelete, deleteTestcase, exportCsv, importCsv, listTestcases, listVersions,
  rollbackVersion, updateTestcase,
} from '../api/testcases'
import { batchExecute, executeCase } from '../api/executions'
import { listRequirements } from '../api/requirements'
import ProjectSidebar from '../components/ProjectSidebar.vue'
import { useConfigStore } from '../stores/config'

// 独立页面不传 projectId，读全局；嵌入项目详情页时以路由项目为准
const props = defineProps({
  projectId: { type: String, default: null },
  refreshKey: { type: Number, default: 0 },
})
const store = useConfigStore()
const projectId = computed(() => props.projectId || store.currentProjectId)
// 嵌入项目详情页时不显示项目侧边栏
const showSidebar = computed(() => !props.projectId)

const executingId = ref('')
const batchExecuting = ref(false)
const importing = ref(false)
const exporting = ref(false)

const TYPES = [
  { v: 'positive', l: '正例' }, { v: 'negative', l: '反例' },
  { v: 'boundary', l: '边界' }, { v: 'state', l: '流转' },
]
const STATUS = [
  { v: 'not_run', l: '未执行' }, { v: 'passed', l: '通过' },
  { v: 'failed', l: '失败' }, { v: 'skipped', l: '跳过' },
  { v: 'running', l: '执行中' }, { v: 'needs_review', l: '待复核' },
  { v: 'cancelled', l: '已取消' },
]
const SELECTOR_TYPES = ['route', 'placeholder', 'text', 'label', 'role', 'css', 'xpath', 'testid']
const EXEC_TEXT = {
  not_run: '未执行',
  passed: '通过',
  failed: '失败',
  skipped: '跳过',
  running: '执行中',
  needs_review: '待复核',
  cancelled: '已取消',
}
const EXEC_DESCRIPTION = {
  not_run: '用例尚未产生执行记录',
  passed: '确定性检查通过，或证据经复核确认符合预期',
  failed: '出现确定错误，或人工复核确认不符合预期',
  skipped: '存在无法验证的步骤，缺少充分证据',
  running: '正在浏览器中执行，尚无最终结论',
  needs_review: '证据不足或 AI 判断有疑点，需要人工确认',
  cancelled: '执行被中止，不能视为通过',
}

const tableRef = ref()
const rows = ref([])
const loading = ref(false)
const page = ref(1)
const pageSize = ref(20)
const total = ref(0)
const selection = ref([])
const requirements = ref([])
const filters = reactive({ requirementId: '', type: '', priority: '', executionStatus: '', keyword: '' })
const requirementMap = computed(() => Object.fromEntries(requirements.value.map((r) => [r.id, r])))
// 用户是否手动改过版本筛选；未改过时始终跟随最新版本
const versionTouched = ref(false)
const latestRequirementId = computed(() =>
  requirements.value.length ? requirements.value[requirements.value.length - 1].id : null,
)
const isLatestVersion = computed(
  () => !!filters.requirementId && filters.requirementId === latestRequirementId.value,
)

const emptyText = computed(() => {
  if (loading.value) return '加载中...'
  if (filters.requirementId === '__none__') return '没有未关联需求版本的用例'
  if (filters.requirementId) {
    const r = requirementMap.value[filters.requirementId]
    return r ? `${r.version} 暂无测试用例` : '该需求版本暂无测试用例'
  }
  return '暂无用例，可先到「项目详情 → 用例生成」生成'
})

const editVisible = ref(false)
const saving = ref(false)
const edit = reactive({ structuredSteps: [] })
const versionVisible = ref(false)
const versions = ref([])
const versionsLoading = ref(false)
const versionCaseId = ref('')
const aiReviewing = ref(false)
const aiFixingId = ref('')
const aiReviewVisible = ref(false)
const aiReviewResults = ref([])
const caseReviewMap = ref({})
let aiReviewRequestId = 0

const queryParams = () => ({
  page: page.value,
  pageSize: pageSize.value,
  requirementId: filters.requirementId || undefined,
  type: filters.type || undefined,
  priority: filters.priority || undefined,
  executionStatus: filters.executionStatus || undefined,
  keyword: filters.keyword || undefined,
})

function requirementLabel(id) {
  const r = requirementMap.value[id]
  return r ? r.version : id
}

async function loadRequirements() {
  if (!projectId.value) return
  try {
    requirements.value = await listRequirements(projectId.value)
  } catch {
    requirements.value = []
  }
  // 默认展示最新一期需求版本的用例；用户手动切换后不再覆盖
  if (!versionTouched.value) {
    const latest = requirements.value[requirements.value.length - 1]
    filters.requirementId = latest ? latest.id : ''
  }
}

function onRequirementChange() {
  versionTouched.value = true
  applyFilter()
}

async function load() {
  if (!projectId.value) return
  loading.value = true
  try {
    const data = await listTestcases(projectId.value, queryParams())
    rows.value = data.items || []
    total.value = data.total || 0
  } finally {
    loading.value = false
  }
}

function applyFilter() {
  page.value = 1
  load()
}

function onSizeChange() {
  page.value = 1
  load()
}

const aiReviewSummary = computed(() => {
  const results = aiReviewResults.value
  if (!results.length) return '暂无质检结果'
  const ok = results.filter((item) => item.verdict === 'ok').length
  const suspect = results.filter((item) => item.verdict === 'suspect').length
  const failed = results.filter((item) => item.verdict === 'fail').length
  const unavailable = results.filter((item) => item.verdict === 'unavailable').length
  return `AI 质检完成：通过 ${ok}，疑似异常 ${suspect}，失败 ${failed}${
    unavailable ? `，不可用 ${unavailable}` : ''
  }`
})
const aiReviewSummaryType = computed(() => {
  const results = aiReviewResults.value
  if (!results.length) return 'info'
  if (results.some((item) => item.verdict === 'fail')) return 'error'
  if (results.some((item) => item.verdict === 'suspect')) return 'warning'
  if (results.every((item) => item.verdict === 'ok')) return 'success'
  return 'info'
})

function reviewTag(verdict) {
  return { ok: 'success', suspect: 'warning', fail: 'danger', unavailable: 'info' }[verdict] || 'info'
}
function reviewLabel(verdict) {
  return {
    ok: '通过',
    suspect: '疑似异常',
    fail: '失败',
    unavailable: '不可用',
  }[verdict] || '未质检'
}
function issueTag(severity) {
  return { blocker: 'danger', warning: 'warning', info: 'info' }[severity] || 'info'
}
function issueLabel(severity) {
  return { blocker: '阻断', warning: '警告', info: '提示' }[severity] || severity
}

async function onAiReview() {
  const requestId = ++aiReviewRequestId
  const ids = selection.value.length
    ? selection.value.map((row) => row.id)
    : rows.value.map((row) => row.id)
  if (!ids.length) return
  if (ids.length > 20) return ElMessage.warning('单次最多质检 20 条，请先筛选或分页选择')
  try {
    await ElMessageBox.confirm(
      `将调用 AI 质检 ${ids.length} 条用例，依据是当前需求文档和源码分析事实。是否继续？`,
      'AI 用例质检',
      { type: 'warning', confirmButtonText: '开始质检', cancelButtonText: '取消' },
    )
  } catch {
    return
  }
  aiReviewing.value = true
  try {
    const data = await aiReviewCases(projectId.value, ids)
    if (requestId !== aiReviewRequestId) return
    aiReviewResults.value = data.results || []
    const nextMap = { ...caseReviewMap.value }
    aiReviewResults.value.forEach((result) => {
      nextMap[result.caseId] = result
    })
    caseReviewMap.value = nextMap
    aiReviewVisible.value = true
  } finally {
    aiReviewing.value = false
  }
}

function openAiReviewDialog(caseId) {
  if (caseId && !aiReviewResults.value.some((result) => result.caseId === caseId)) {
    const item = caseReviewMap.value[caseId]
    if (item) aiReviewResults.value = [item]
  }
  aiReviewVisible.value = true
}

async function onAiRepair(result) {
  if (!result?.caseId) return
  aiFixingId.value = result.caseId
  try {
    const data = await aiFixCase(projectId.value, result.caseId, result.issues || [])
    result.repairSummary = data.summary || 'AI 修复完成并通过源码事实校验'
    result.verdict = 'ok'
    result.reason = result.repairSummary
    result.issues = []
    caseReviewMap.value = {
      ...caseReviewMap.value,
      [result.caseId]: { ...result },
    }
    const index = rows.value.findIndex((row) => row.id === result.caseId)
    if (index >= 0) rows.value[index] = data.testCase
    ElMessage.success('AI 修复完成，已生成新版本')
  } finally {
    aiFixingId.value = ''
  }
}

async function onExecute(row) {
  executingId.value = row.id
  try {
    const r = await executeCase(projectId.value, row.id)
    const text = EXEC_TEXT[r.status] || r.status
    const summary = `执行完成：${text}（${r.passedSteps}/${r.totalSteps} 步通过）`
    if (r.status === 'passed') {
      ElMessage.success(summary)
    } else if (r.status === 'needs_review') {
      ElMessage.warning(`${summary}，AI 判定有异常，请到执行结果中人工复核`)
    } else {
      ElMessage.warning(summary)
    }
    await load()
  } finally {
    executingId.value = ''
  }
}

async function onBatchExecute() {
  if (!selection.value.length) return ElMessage.warning('请先勾选用例')
  batchExecuting.value = true
  try {
    const r = await batchExecute(projectId.value, selection.value.map((s) => s.id))
    ElMessage.success(`已提交批量执行，批次 ${r.batchId}，可到「执行结果」查看进度`)
  } finally {
    batchExecuting.value = false
  }
}

async function onExecuteAll() {
  if (!projectId.value) return
  batchExecuting.value = true
  try {
    // 拉取当前筛选条件下的所有用例（最多 200 条）
    const data = await listTestcases(projectId.value, {
      ...queryParams(),
      page: 1,
      pageSize: 200,
    })
    const ids = (data.items || []).map((t) => t.id)
    if (!ids.length) return ElMessage.warning('没有可执行的用例')
    if (ids.length > 100) return ElMessage.warning('单批上限 100 条，请用筛选缩小范围或分批执行')
    const r = await batchExecute(projectId.value, ids)
    ElMessage.success(`已提交全部执行（${ids.length} 条），批次 ${r.batchId}，可到「执行结果」查看进度`)
  } finally {
    batchExecuting.value = false
  }
}

function openEdit(row) {
  const copy = JSON.parse(JSON.stringify(row))
  Object.assign(edit, copy)
  edit.preconditionsText = (row.preconditions || []).join('\n')
  edit.expectedText = (row.expectedResults || []).join('\n')
  edit.structuredSteps = (copy.structuredSteps || []).map((s) => ({
    ...s,
    uiSelector: s.uiSelector || { type: 'text', value: '' },
  }))
  editVisible.value = true
}

function addStep() {
  edit.structuredSteps.push({
    order: edit.structuredSteps.length + 1,
    type: 'ui_action',
    action: '',
    target: '',
    uiSelector: { type: 'text', value: '' },
    expected: '',
    inputValue: '',
  })
}

function removeStep(i) {
  edit.structuredSteps.splice(i, 1)
}

function moveStep(i, d) {
  const arr = edit.structuredSteps
  const j = i + d
  if (j < 0 || j >= arr.length) return
  ;[arr[i], arr[j]] = [arr[j], arr[i]]
}

async function saveEdit() {
  if (!edit.title?.trim()) return ElMessage.warning('请填写用例标题')
  if (!edit.structuredSteps.length) return ElMessage.warning('结构化步骤不能为空')
  const invalid = edit.structuredSteps.find(
    (s) => s.type !== 'state_assert' && !s.uiSelector?.value?.trim(),
  )
  if (invalid) return ElMessage.warning(`步骤 #${edit.structuredSteps.indexOf(invalid) + 1} 缺少定位值`)

  saving.value = true
  try {
    edit.structuredSteps.forEach((s, i) => (s.order = i + 1))
    await updateTestcase(projectId.value, edit.id, {
      title: edit.title.trim(),
      module: edit.module,
      type: edit.type,
      priority: edit.priority,
      preconditions: (edit.preconditionsText || '').split('\n').map((s) => s.trim()).filter(Boolean),
      expectedResults: (edit.expectedText || '').split('\n').map((s) => s.trim()).filter(Boolean),
      structuredSteps: edit.structuredSteps,
    })
    ElMessage.success('已保存')
    editVisible.value = false
    await load()
  } finally {
    saving.value = false
  }
}

async function openVersions(row) {
  versionCaseId.value = row.id
  versionVisible.value = true
  versionsLoading.value = true
  try {
    versions.value = await listVersions(projectId.value, row.id)
  } finally {
    versionsLoading.value = false
  }
}

async function confirmAction(message, title = '确认操作') {
  try {
    await ElMessageBox.confirm(message, title, { type: 'warning' })
    return true
  } catch {
    return false
  }
}

async function onRollback(v) {
  if (!(await confirmAction(`回滚到版本 ${v.version}？回滚后当前内容会被覆盖。`, '确认回滚'))) return
  await rollbackVersion(projectId.value, versionCaseId.value, v.id)
  ElMessage.success('已回滚')
  versionVisible.value = false
  await load()
}

async function onDelete(row) {
  if (!(await confirmAction(`删除「${row.title}」？`, '确认删除'))) return
  await deleteTestcase(projectId.value, row.id)
  ElMessage.success('已删除')
  if (rows.value.length === 1 && page.value > 1) page.value -= 1
  await load()
}

async function onBatchDelete() {
  if (!selection.value.length) return
  const count = selection.value.length
  if (!(await confirmAction(`删除选中的 ${count} 条用例？`, '确认删除'))) return
  await batchDelete(projectId.value, selection.value.map((s) => s.id))
  ElMessage.success('已删除')
  selection.value = []
  if (count >= rows.value.length && page.value > 1) page.value -= 1
  await load()
}

async function onExport() {
  exporting.value = true
  try {
    const blob = await exportCsv(projectId.value)
    const url = URL.createObjectURL(blob)
    const a = document.createElement('a')
    a.href = url
    a.download = 'testcases.csv'
    a.click()
    URL.revokeObjectURL(url)
  } finally {
    exporting.value = false
  }
}

async function onImport(file) {
  importing.value = true
  try {
    const r = await importCsv(projectId.value, file)
    ElMessage.success(`导入成功${r?.imported != null ? `：${r.imported} 条` : ''}`)
    await load()
  } finally {
    importing.value = false
  }
  return false
}

function typeTag(t) {
  return { positive: 'success', negative: 'danger', boundary: 'warning', state: 'primary' }[t] || 'info'
}
function typeLabel(t) {
  return { positive: '正例', negative: '反例', boundary: '边界', state: '流转' }[t] || t
}
function execTag(s) {
  return {
    passed: 'success',
    failed: 'danger',
    skipped: 'warning',
    not_run: 'info',
    running: 'primary',
    needs_review: 'warning',
    cancelled: 'info',
  }[s] || 'info'
}
function execLabel(s) {
  return EXEC_TEXT[s] || s
}
function execDescription(s) {
  return EXEC_DESCRIPTION[s] || ''
}

const isNarrow = ref(false)
const narrowMq = window.matchMedia('(max-width: 900px)')
function onNarrowChange(e) {
  isNarrow.value = e.matches
}

onMounted(async () => {
  isNarrow.value = narrowMq.matches
  narrowMq.addEventListener('change', onNarrowChange)
  // 先确定默认版本，再拉用例，避免先闪一次“全部版本”
  await loadRequirements()
  load()
})
onBeforeUnmount(() => narrowMq.removeEventListener('change', onNarrowChange))
watch(projectId, async () => {
  page.value = 1
  selection.value = []
  versionTouched.value = false
  filters.requirementId = ''
  aiReviewRequestId += 1
  aiReviewResults.value = []
  caseReviewMap.value = {}
  aiReviewVisible.value = false
  aiFixingId.value = ''
  await loadRequirements()
  load()
})
// 进入页面 / 生成完成后由父组件触发刷新（顺手刷新版本筛选项）
watch(() => props.refreshKey, async () => {
  await loadRequirements()
  load()
})
</script>

<style scoped>
.page-layout {
  display: flex;
  gap: 16px;
  align-items: flex-start;
}
.page-content {
  flex: 1;
  min-width: 0;
}
.filter-bar {
  display: flex;
  justify-content: space-between;
  align-items: center;
  gap: 10px;
  flex-wrap: wrap;
  margin-bottom: 12px;
}
.filters,
.actions {
  display: flex;
  gap: 10px;
  align-items: center;
  flex-wrap: wrap;
}
.actions {
  margin-left: auto;
}
.mb {
  margin-bottom: 12px;
}
.pager {
  margin-top: 12px;
  justify-content: flex-end;
}
.review-link {
  cursor: pointer;
}
.ai-review-item {
  padding: 14px 0;
  border-bottom: 1px solid var(--el-border-color-lighter);
}
.ai-review-item:last-child {
  border-bottom: none;
}
.review-head {
  display: flex;
  justify-content: space-between;
  align-items: flex-start;
  gap: 12px;
}
.review-title {
  display: flex;
  align-items: center;
  gap: 8px;
  min-width: 0;
}
.review-actions {
  display: flex;
  align-items: center;
  gap: 8px;
  flex: none;
}
.review-reason {
  margin: 8px 0;
  color: var(--el-text-color-regular);
  line-height: 1.6;
}
.review-model,
.issue-evidence {
  font-size: 12px;
}
.repair-summary {
  display: flex;
  align-items: center;
  gap: 8px;
  margin-bottom: 8px;
  color: var(--el-color-success);
}
.issue-list {
  display: flex;
  flex-direction: column;
  gap: 10px;
}
.issue-item {
  display: flex;
  gap: 10px;
  padding: 10px;
  border: 1px solid var(--el-border-color-lighter);
  border-radius: 6px;
  background: var(--el-fill-color-lighter);
}
.issue-body {
  min-width: 0;
}
.issue-problem,
.issue-suggestion {
  line-height: 1.6;
}
.issue-suggestion {
  color: var(--el-text-color-secondary);
}
.issue-evidence {
  margin-top: 4px;
  color: var(--el-text-color-secondary);
}
@media (max-width: 900px) {
  .page-layout {
    flex-direction: column;
    width: 100%;
  }
  .page-content {
    width: 100%;
  }
  .filter-bar {
    justify-content: flex-start;
  }
  .actions {
    width: 100%;
  }
}
.step-item {
  border: 1px solid var(--el-border-color-light);
  border-radius: 6px;
  padding: 8px 10px;
  margin-bottom: 8px;
  background: var(--el-fill-color-blank);
}
.step-head,
.step-body {
  display: flex;
  gap: 8px;
  align-items: center;
  flex-wrap: wrap;
}
.step-head {
  margin-bottom: 8px;
}
.step-no {
  font-weight: 600;
  color: var(--el-text-color-secondary);
  font-size: 13px;
}
.grow {
  flex: 1;
  min-width: 140px;
}
.muted {
  color: var(--el-text-color-secondary);
}
</style>
