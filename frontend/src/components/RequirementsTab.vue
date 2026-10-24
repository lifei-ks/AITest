<template>
  <div v-loading="loading" class="req-tab">
    <div class="req-layout">
      <!-- 版本列表 -->
      <div class="req-list">
        <div class="list-head">
          <span>需求版本（{{ requirements.length }}）</span>
          <el-button size="small" type="primary" @click="openCreate">新建</el-button>
        </div>
        <el-scrollbar class="list-body">
          <div
            v-for="r in requirements"
            :key="r.id"
            class="req-item"
            :class="{ active: r.id === selectedId }"
            @click="select(r)"
          >
            <div class="item-top">
              <span class="ver">{{ r.version }}</span>
              <el-tag size="small" :type="statusTag(r.status)">{{ r.statusText || statusText(r.status) }}</el-tag>
            </div>
            <div class="item-name" :title="r.name">{{ r.name }}</div>
            <div class="item-meta">测试点 {{ r.testPointCount }} · 用例 {{ r.caseCount }}</div>
          </div>
          <el-empty v-if="!requirements.length" :image-size="60" description="暂无需求版本" />
        </el-scrollbar>
      </div>

      <!-- 版本详情 -->
      <div class="req-main">
        <el-empty v-if="!current" description="新建或选择一个需求版本，再解析测试点" />

        <template v-else>
          <div class="main-head">
            <div class="head-info">
              <span class="main-title">{{ current.version }} · {{ current.name }}</span>
              <el-tag size="small" :type="statusTag(current.status)">{{ current.statusText || statusText(current.status) }}</el-tag>
              <span class="muted">
                {{ current.status === 'ready' ? `已解析 ${current.testPointCount} 个测试点` : '解析需求文档后可生成用例' }}
              </span>
            </div>
            <div class="main-actions">
              <el-tooltip content="自动：优先用 LLM 解析，失败时降级规则" placement="top">
                <el-select v-model="analyzeMode" size="small" style="width: 96px" :disabled="analyzing">
                  <el-option label="自动" value="auto" />
                  <el-option label="LLM" value="llm" />
                  <el-option label="规则" value="rule" />
                </el-select>
              </el-tooltip>
              <el-tooltip content="选择本次解析测试点使用的文本 LLM；留空使用默认配置" placement="top">
                <el-select
                  v-model="llmProfileId"
                  size="small"
                  style="width: 170px"
                  clearable
                  placeholder="默认文本 LLM"
                  :disabled="analyzing || analyzeMode === 'rule'"
                >
                  <el-option
                    v-for="p in textProfiles"
                    :key="p.id"
                    :label="`${p.name} · ${p.model}`"
                    :value="p.id"
                  />
                </el-select>
              </el-tooltip>
              <el-button
                size="small"
                type="primary"
                :loading="analyzing"
                :disabled="!prd.trim() || current.status === 'analyzing'"
                @click="analyze"
              >解析测试点</el-button>
              <el-button size="small" type="success" :disabled="!current.testPointCount" @click="$emit('generate', current.id)">
                生成用例
              </el-button>
              <el-button size="small" @click="openEdit">编辑信息</el-button>
              <el-button size="small" type="danger" plain @click="removeCurrent">删除</el-button>
            </div>
          </div>

          <el-alert
            v-if="current.status === 'analyzing'"
            title="正在解析需求文档并生成测试点，可切换到其他标签页等待"
            type="warning"
            :closable="false"
            class="mb"
          />
          <el-alert
            v-if="current.errorMessage"
            :title="`解析失败：${current.errorMessage}`"
            type="error"
            :closable="false"
            class="mb"
          />
          <el-alert v-if="current.sourceFile" :title="`来源文件：${current.sourceFile}`" type="info" :closable="false" class="mb" />

          <div class="section-head">
            <span>需求文档</span>
            <div class="doc-actions">
              <el-button size="small" type="primary" :loading="saving" :disabled="!dirty" @click="saveDoc">保存内容</el-button>
              <span class="muted">{{ prd.length }} 字符</span>
            </div>
          </div>
          <PrdDropUpload
            ref="prdDropRef"
            title="拖拽需求文档到这里上传"
            hint="支持 .md、.txt、.pdf、.docx；上传后会替换当前文档并作废旧测试点"
            :disabled="uploading"
            @update:model-value="onUpload"
          />
          <el-input
            v-model="prd"
            type="textarea"
            :rows="10"
            resize="vertical"
            placeholder="粘贴本期需求文档（Markdown / 纯文本），或上传 .md/.txt/.pdf/.docx"
            @input="dirty = true"
          />

          <div class="section-head">
            <span>测试点（{{ points.length }}）</span>
            <span class="muted">由需求文档 + 代码事实解析，生成用例时逐点消费</span>
          </div>
          <el-table
            :data="points"
            v-loading="detailLoading"
            size="small"
            border
            max-height="420"
            :empty-text="pointsEmptyText"
          >
            <el-table-column prop="seq" label="#" width="50" />
            <el-table-column prop="title" label="测试点" min-width="220" show-overflow-tooltip />
            <el-table-column label="分类" width="100">
              <template #default="{ row }">
                <el-tag size="small" :type="categoryTag(row.category)">{{ categoryLabel(row.category) }}</el-tag>
              </template>
            </el-table-column>
            <el-table-column prop="priority" label="优先级" width="80" />
            <el-table-column label="目标" min-width="140" show-overflow-tooltip>
              <template #default="{ row }">{{ row.pageRoute || row.endpoint || '—' }}</template>
            </el-table-column>
            <el-table-column prop="expected" label="预期" min-width="180" show-overflow-tooltip />
            <el-table-column label="来源" width="80">
              <template #default="{ row }">
                <el-tag size="small" effect="plain" :type="row.source === 'llm' ? 'success' : 'info'">
                  {{ row.source === 'llm' ? 'LLM' : '规则' }}
                </el-tag>
              </template>
            </el-table-column>
          </el-table>
        </template>
      </div>
    </div>

    <!-- 新建 / 编辑版本 -->
    <el-dialog v-model="dialogVisible" :title="dialogMode === 'create' ? '新建需求版本' : '编辑需求版本'" width="560px" :close-on-click-modal="false">
      <el-form label-width="90px">
        <el-form-item label="版本号" required>
          <el-input v-model="form.version" placeholder="V1.0 / V2.1" maxlength="40" />
        </el-form-item>
        <el-form-item label="需求名称" required>
          <el-input v-model="form.name" placeholder="如：订单主流程" maxlength="120" />
        </el-form-item>
        <el-form-item v-if="dialogMode === 'create'" label="需求内容">
          <el-input v-model="form.content" type="textarea" :rows="6" placeholder="可直接粘贴需求文档，也可以创建后上传文件" />
        </el-form-item>
        <el-form-item v-if="dialogMode === 'create'" label="上传文件">
          <PrdDropUpload
            ref="reqMetaDropRef"
            v-model="requirementFile"
            title="拖拽需求文档"
            hint="可选；创建版本后自动上传，支持 .md、.txt、.pdf、.docx"
            :disabled="savingMeta"
          />
        </el-form-item>
      </el-form>
      <template #footer>
        <el-button @click="dialogVisible = false">取消</el-button>
        <el-button type="primary" :loading="savingMeta" @click="saveMeta">保存</el-button>
      </template>
    </el-dialog>
  </div>
</template>

<script setup>
import { computed, onBeforeUnmount, onMounted, ref, watch } from 'vue'
import { ElMessage, ElMessageBox } from 'element-plus'
import {
  analyzeRequirement,
  createRequirement,
  deleteRequirement,
  getRequirement,
  listRequirements,
  listTestPoints,
  updateRequirement,
  uploadRequirementPrd,
} from '../api/requirements'
import { listLlmProfiles } from '../api/settings'
import PrdDropUpload from './PrdDropUpload.vue'

const props = defineProps({
  projectId: { type: String, required: true },
  modelValue: { type: String, default: null },
})
const emit = defineEmits(['update:modelValue', 'generate'])

const STATUS_TAG = { draft: 'info', analyzing: 'warning', ready: 'success', failed: 'danger' }
const STATUS_TEXT = { draft: '待解析', analyzing: '解析中', ready: '已解析', failed: '解析失败' }
const CAT_TEXT = {
  functional: '功能', negative: '异常校验', boundary: '边界',
  state: '状态流转', api: '接口', permission: '权限',
}
const CAT_TAG = { functional: 'primary', negative: 'danger', boundary: 'warning', state: 'success', api: 'info', permission: 'info' }

const requirements = ref([])
const loading = ref(false)
const detailLoading = ref(false)
const points = ref([])
const prd = ref('')
const dirty = ref(false)
const saving = ref(false)
const uploading = ref(false)
const prdDropRef = ref()
const analyzing = ref(false)
const analyzeMode = ref('auto')
const llmProfiles = ref([])
const llmProfileId = ref(null)
const textProfiles = computed(() => llmProfiles.value.filter((p) => p.kind === 'text' && p.enabled))

const dialogVisible = ref(false)
const dialogMode = ref('create')
const savingMeta = ref(false)
const requirementFile = ref(null)
const reqMetaDropRef = ref()
const form = ref({ name: '', version: '', content: '' })

const selectedId = computed({
  get: () => props.modelValue,
  set: (v) => emit('update:modelValue', v),
})
const current = computed(() => requirements.value.find((r) => r.id === selectedId.value) || null)
const pointsEmptyText = computed(() => {
  if (current.value?.status === 'analyzing') return '正在解析测试点…'
  return '暂无测试点，点击「解析测试点」从需求文档中提取'
})

let pollTimer = null
let loadingDetailId = null

function statusText(s) {
  return STATUS_TEXT[s] || s
}
function statusTag(s) {
  return STATUS_TAG[s] || 'info'
}
function categoryLabel(c) {
  return CAT_TEXT[c] || c
}
function categoryTag(c) {
  return CAT_TAG[c] || 'info'
}

function stopPoll() {
  if (pollTimer) {
    clearInterval(pollTimer)
    pollTimer = null
  }
}

function startPoll() {
  stopPoll()
  analyzing.value = true
  pollTimer = setInterval(async () => {
    if (!selectedId.value) return stopPoll()
    try {
      const d = await getRequirement(selectedId.value)
      const { content, ...meta } = d
      const idx = requirements.value.findIndex((r) => r.id === d.id)
      if (idx >= 0) requirements.value[idx] = { ...requirements.value[idx], ...meta }
      if (d.status === 'ready' || d.status === 'failed') {
        stopPoll()
        analyzing.value = false
        await loadDetail(d.id)
        if (d.status === 'ready') {
          ElMessage.success(`解析完成：${d.testPointCount} 个测试点`)
        } else {
          ElMessage.error(d.errorMessage || '需求解析失败')
        }
      }
    } catch {
      /* 请求层已提示，等待下一轮 */
    }
  }, 2500)
}

async function loadList() {
  loading.value = true
  try {
    requirements.value = await listRequirements(props.projectId)
    if (!selectedId.value || !requirements.value.some((r) => r.id === selectedId.value)) {
      selectedId.value = requirements.value.length ? requirements.value[requirements.value.length - 1].id : null
    }
    if (selectedId.value) await loadDetail(selectedId.value)
  } finally {
    loading.value = false
  }
}

async function loadDetail(id) {
  if (!id) {
    points.value = []
    prd.value = ''
    return
  }
  detailLoading.value = true
  loadingDetailId = id
  try {
    const d = await getRequirement(id)
    prd.value = d.content || ''
    dirty.value = false
    points.value = await listTestPoints(id)
    const { content, ...meta } = d
    const idx = requirements.value.findIndex((r) => r.id === id)
    if (idx >= 0) requirements.value[idx] = { ...requirements.value[idx], ...meta }
    if (d.status === 'analyzing') startPoll()
  } catch {
    points.value = []
  } finally {
    loadingDetailId = null
    detailLoading.value = false
  }
}

function select(r) {
  if (r.id === selectedId.value) return
  stopPoll()
  analyzing.value = false
  selectedId.value = r.id
}

watch(
  () => props.modelValue,
  (id, old) => {
    if (!id || id === old || id === loadingDetailId) return
    stopPoll()
    analyzing.value = false
    loadDetail(id)
  },
)

watch(
  () => props.projectId,
  () => {
    stopPoll()
    analyzing.value = false
    requirements.value = []
    points.value = []
    prd.value = ''
    selectedId.value = null
    loadList()
  },
)

async function saveDoc() {
  saving.value = true
  try {
    await updateRequirement(selectedId.value, { content: prd.value })
    dirty.value = false
    ElMessage.success('需求内容已保存，旧测试点已失效，请重新解析')
    await loadList()
  } finally {
    saving.value = false
  }
}

function openCreate() {
  dialogMode.value = 'create'
  requirementFile.value = null
  reqMetaDropRef.value?.clear()
  const next = requirements.value.length ? `V${requirements.value.length + 1}.0` : 'V1.0'
  form.value = { name: '', version: next, content: '' }
  dialogVisible.value = true
}

function openEdit() {
  if (!current.value) return
  dialogMode.value = 'edit'
  requirementFile.value = null
  reqMetaDropRef.value?.clear()
  form.value = { name: current.value.name, version: current.value.version, content: '' }
  dialogVisible.value = true
}

async function saveMeta() {
  if (!form.value.version?.trim()) return ElMessage.warning('请填写版本号')
  if (!form.value.name?.trim()) return ElMessage.warning('请填写需求名称')
  savingMeta.value = true
  try {
    if (dialogMode.value === 'create') {
      const created = await createRequirement(props.projectId, {
        name: form.value.name.trim(),
        version: form.value.version.trim(),
        content: form.value.content || '',
      })
      let uploadFailed = false
      if (requirementFile.value) {
        try {
          await uploadRequirementPrd(created.id, requirementFile.value)
        } catch {
          uploadFailed = true
        }
      }
      requirementFile.value = null
      dialogVisible.value = false
      selectedId.value = created.id
      await loadList()
      if (uploadFailed) {
        ElMessage.warning('需求版本已创建，但文件上传失败，请重新上传')
      } else {
        ElMessage.success('已创建需求版本')
      }
    } else {
      await updateRequirement(selectedId.value, { name: form.value.name.trim(), version: form.value.version.trim() })
      dialogVisible.value = false
      await loadList()
      ElMessage.success('已更新')
    }
  } finally {
    savingMeta.value = false
  }
}

async function removeCurrent() {
  if (!current.value) return
  try {
    await ElMessageBox.confirm(
      `删除需求版本「${current.value.version}」？该版本自动生成的用例会一并删除，手工用例保留。`,
      '确认删除',
      { type: 'warning', confirmButtonText: '删除', cancelButtonText: '取消' },
    )
  } catch {
    return
  }
  await deleteRequirement(selectedId.value)
  ElMessage.success('已删除')
  selectedId.value = null
  await loadList()
}

async function analyze() {
  if (!prd.value.trim()) return ElMessage.warning('请先填写或上传需求文档')
  analyzing.value = true
  try {
    await analyzeRequirement(
      selectedId.value,
      analyzeMode.value,
      analyzeMode.value === 'rule' ? null : llmProfileId.value,
    )
    const idx = requirements.value.findIndex((r) => r.id === selectedId.value)
    if (idx >= 0) requirements.value[idx] = { ...requirements.value[idx], status: 'analyzing', statusText: '解析中' }
    ElMessage.info('已开始解析，完成后自动刷新')
    startPoll()
  } catch (e) {
    analyzing.value = false
    ElMessage.error(e.message || '触发解析失败')
  }
}

async function onUpload(file) {
  if (!file || !selectedId.value || uploading.value) return
  uploading.value = true
  try {
    const updated = await uploadRequirementPrd(selectedId.value, file)
    if (updated?.content != null) prd.value = updated.content
    ElMessage.success(`已解析 ${file.name} 并载入`)
    await loadList()
  } finally {
    uploading.value = false
    prdDropRef.value?.clear()
  }
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
  loadList()
  loadLlmProfiles()
})
onBeforeUnmount(stopPoll)
</script>

<style scoped>
.req-tab {
  min-height: 320px;
}
.prd-drop {
  margin-bottom: 12px;
}
.req-layout {
  display: flex;
  gap: 16px;
  align-items: flex-start;
}
.req-list {
  width: 280px;
  flex: none;
  border: 1px solid var(--el-border-color-light);
  border-radius: 6px;
  background: #fff;
  display: flex;
  flex-direction: column;
  max-height: 620px;
}
.list-head {
  display: flex;
  align-items: center;
  justify-content: space-between;
  gap: 8px;
  padding: 10px 12px;
  border-bottom: 1px solid var(--el-border-color-lighter);
  font-weight: 600;
  font-size: 13px;
}
.list-body {
  padding: 6px;
}
.req-item {
  padding: 10px;
  border-radius: 6px;
  cursor: pointer;
  border: 1px solid transparent;
  margin-bottom: 6px;
}
.req-item:hover {
  background: var(--el-fill-color-light);
}
.req-item.active {
  background: var(--el-color-primary-light-9);
  border-color: var(--el-color-primary-light-7);
}
.item-top {
  display: flex;
  align-items: center;
  justify-content: space-between;
  gap: 6px;
}
.ver {
  font-weight: 600;
  font-size: 13px;
}
.item-name {
  font-size: 13px;
  margin: 4px 0 2px;
  overflow: hidden;
  text-overflow: ellipsis;
  white-space: nowrap;
}
.item-meta {
  color: var(--el-text-color-secondary);
  font-size: 12px;
}
.req-main {
  flex: 1;
  min-width: 0;
}
.main-head {
  display: flex;
  align-items: center;
  justify-content: space-between;
  gap: 12px;
  flex-wrap: wrap;
  margin-bottom: 10px;
}
.head-info {
  display: flex;
  align-items: center;
  gap: 8px;
  flex-wrap: wrap;
}
.main-title {
  font-weight: 600;
  font-size: 15px;
}
.main-actions {
  display: flex;
  align-items: center;
  gap: 8px;
  flex-wrap: wrap;
}
.muted {
  color: var(--el-text-color-secondary);
  font-size: 12px;
}
.mb {
  margin-bottom: 10px;
}
.section-head {
  display: flex;
  align-items: center;
  justify-content: space-between;
  gap: 8px;
  margin: 14px 0 8px;
  font-weight: 600;
  font-size: 13px;
}
.doc-actions {
  display: flex;
  align-items: center;
  gap: 8px;
}

@media (max-width: 900px) {
  .req-layout {
    flex-direction: column;
  }
  .req-list {
    width: 100%;
    max-height: 240px;
  }
}
</style>
