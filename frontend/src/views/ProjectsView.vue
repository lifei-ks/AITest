<template>
  <div>
    <div class="toolbar">
      <div class="filters">
        <el-input
          v-model="keyword"
          placeholder="按名称搜索"
          clearable
          style="width: 240px"
          @keyup.enter="search"
          @clear="search"
        >
          <template #prefix><el-icon><Search /></el-icon></template>
        </el-input>
        <el-button @click="search">查询</el-button>
      </div>
      <div class="actions">
        <el-button :loading="loading" @click="load">
          <el-icon><Refresh /></el-icon>
        </el-button>
        <el-button type="primary" @click="openCreate">
          <el-icon><Plus /></el-icon>新建项目
        </el-button>
      </div>
    </div>

    <div v-loading="loading" class="content">
      <el-empty
        v-if="!loading && projects.length === 0"
        :description="keyword ? `没有匹配「${keyword}」的项目` : '还没有项目，点击「新建项目」开始'"
      >
        <el-button v-if="keyword" @click="clearKeyword">清空搜索</el-button>
        <el-button v-else type="primary" @click="openCreate">新建项目</el-button>
      </el-empty>

      <el-row v-else :gutter="16">
        <el-col v-for="p in projects" :key="p.id" :xs="24" :sm="12" :md="8" :lg="6">
          <el-card class="proj-card" shadow="hover" @click="gotoDetail(p.id)">
            <template #header>
              <div class="card-head">
                <span class="name" :title="p.name">{{ p.name }}</span>
                <el-dropdown trigger="click" @click.stop>
                  <el-button link type="primary" @click.stop>操作</el-button>
                  <template #dropdown>
                    <el-dropdown-menu>
                      <el-dropdown-item @click.stop="openEdit(p)">编辑</el-dropdown-item>
                      <el-dropdown-item divided @click.stop="confirmDelete(p)">
                        <span class="danger">删除</span>
                      </el-dropdown-item>
                    </el-dropdown-menu>
                  </template>
                </el-dropdown>
              </div>
            </template>

            <p class="desc">{{ p.description || '暂无描述' }}</p>
            <div class="meta">
              <el-tag size="small" effect="plain">{{ sourceLabel(p.sourceType) }}</el-tag>
              <el-tag v-if="p.analysisStatus" size="small" :type="analysisTagType(p.analysisStatus)">
                {{ analysisLabel(p.analysisStatus) }}
              </el-tag>
              <el-tag v-else size="small" type="info">未分析</el-tag>
            </div>
            <div class="meta path" :title="p.sourceType === 'git' ? p.gitUrl : p.sourcePath">
              {{ p.sourceType === 'git' ? p.gitUrl || '—' : p.sourcePath || '—' }}
            </div>
          </el-card>
        </el-col>
      </el-row>
    </div>

    <!-- 新建 / 编辑对话框 -->
    <el-dialog
      v-model="dialogVisible"
      :title="form.id ? '编辑项目' : '新建项目'"
      width="560px"
      :close-on-click-modal="false"
      @closed="formRef?.clearValidate()"
    >
      <el-form ref="formRef" :model="form" :rules="rules" label-width="100px">
        <el-form-item label="项目名称" prop="name">
          <el-input v-model="form.name" maxlength="120" show-word-limit />
        </el-form-item>
        <el-form-item label="描述" prop="description">
          <el-input v-model="form.description" type="textarea" :rows="2" maxlength="500" show-word-limit />
        </el-form-item>
        <el-form-item label="源码类型">
          <el-radio-group v-model="form.sourceType">
            <el-radio value="local_path">本地路径</el-radio>
            <el-radio value="git">Git 仓库</el-radio>
          </el-radio-group>
        </el-form-item>
        <el-form-item v-if="form.sourceType === 'local_path'" label="源码路径" prop="sourcePath">
          <el-input v-model="form.sourcePath" placeholder="D:/somewhere/project-src" />
        </el-form-item>
        <template v-else>
          <el-form-item label="Git 地址" prop="gitUrl">
            <el-input v-model="form.gitUrl" placeholder="https://github.com/xxx/xxx.git" />
          </el-form-item>
          <el-form-item label="分支" prop="gitBranch">
            <el-input v-model="form.gitBranch" placeholder="main" />
          </el-form-item>
        </template>
        <el-form-item label="被测地址" prop="targetUrl">
          <el-input v-model="form.targetUrl" placeholder="http://127.0.0.1:8200" />
        </el-form-item>
      </el-form>
      <template #footer>
        <el-button @click="dialogVisible = false">取消</el-button>
        <el-button type="primary" :loading="saving" @click="save">保存</el-button>
      </template>
    </el-dialog>
  </div>
</template>

<script setup>
import { computed, onMounted, ref } from 'vue'
import { useRouter } from 'vue-router'
import { ElMessage, ElMessageBox } from 'element-plus'
import { listProjects, createProject, updateProject, deleteProject } from '../api/projects'
import { useConfigStore } from '../stores/config'

const router = useRouter()
const store = useConfigStore()
const projects = ref([])
const keyword = ref('')
const loading = ref(false)
const dialogVisible = ref(false)
const saving = ref(false)
const formRef = ref()
const form = ref({})

const ANALYSIS_TEXT = { pending: '待分析', running: '分析中', success: '分析完成', failed: '分析失败' }
const ANALYSIS_TAG = { pending: 'info', running: 'warning', success: 'success', failed: 'danger' }

const URL_RE = /^https?:\/\/[^\s]+$/i

function validateTargetUrl(_rule, value, callback) {
  if (!value) return callback()
  return URL_RE.test(value) ? callback() : callback(new Error('请输入 http:// 或 https:// 开头的地址'))
}

const rules = computed(() => ({
  name: [{ required: true, message: '请填写项目名称', trigger: 'blur' }],
  sourcePath:
    form.value.sourceType === 'local_path'
      ? [{ required: true, message: '请填写源码路径', trigger: 'blur' }]
      : [],
  gitUrl:
    form.value.sourceType === 'git'
      ? [
          { required: true, message: '请填写 Git 地址', trigger: 'blur' },
          { pattern: /^(https?:\/\/|git@|ssh:\/\/)/i, message: '请输入合法的 Git 地址', trigger: 'blur' },
        ]
      : [],
  targetUrl: [{ validator: validateTargetUrl, trigger: 'blur' }],
}))

async function load() {
  loading.value = true
  try {
    projects.value = await listProjects(keyword.value)
  } finally {
    loading.value = false
  }
}

function search() {
  load()
}

function clearKeyword() {
  keyword.value = ''
  load()
}

function openCreate() {
  form.value = {
    name: '',
    description: '',
    sourceType: 'local_path',
    sourcePath: '',
    gitUrl: '',
    gitBranch: '',
    targetUrl: 'http://127.0.0.1:8200',
  }
  dialogVisible.value = true
}

function openEdit(p) {
  form.value = { ...p }
  dialogVisible.value = true
}

async function save() {
  const valid = await formRef.value?.validate().catch(() => false)
  if (!valid) return
  saving.value = true
  try {
    const payload = {
      name: form.value.name?.trim(),
      description: form.value.description,
      sourceType: form.value.sourceType,
      sourcePath: form.value.sourcePath?.trim(),
      gitUrl: form.value.gitUrl?.trim(),
      gitBranch: form.value.gitBranch?.trim() || 'main',
      targetUrl: form.value.targetUrl?.trim(),
    }
    if (form.value.id) await updateProject(form.value.id, payload)
    else await createProject(payload)
    ElMessage.success(form.value.id ? '已更新' : '已创建')
    dialogVisible.value = false
    await load()
    await store.loadProjects().catch(() => {})
  } finally {
    saving.value = false
  }
}

async function confirmDelete(p) {
  try {
    await ElMessageBox.confirm(
      `删除项目「${p.name}」？其分析结果、用例、执行记录将一并删除。`,
      '确认删除',
      { type: 'warning', confirmButtonText: '删除', cancelButtonText: '取消' },
    )
  } catch {
    return // 用户取消
  }
  await deleteProject(p.id)
  ElMessage.success('已删除')
  if (store.currentProjectId === p.id) store.setProject(null)
  await load()
  await store.loadProjects().catch(() => {})
}

function gotoDetail(id) {
  router.push(`/projects/${id}`)
}

function sourceLabel(type) {
  return type === 'git' ? 'Git 仓库' : '本地路径'
}

function analysisLabel(status) {
  return ANALYSIS_TEXT[status] || status
}

function analysisTagType(status) {
  return ANALYSIS_TAG[status] || 'info'
}

onMounted(load)
</script>

<style scoped>
.toolbar {
  display: flex;
  justify-content: space-between;
  align-items: center;
  gap: 12px;
  flex-wrap: wrap;
  margin-bottom: 16px;
}
.filters,
.actions {
  display: flex;
  align-items: center;
  gap: 10px;
}
.content {
  min-height: 200px;
}
.proj-card {
  margin-bottom: 16px;
  cursor: pointer;
  transition: transform 0.15s ease;
}
.proj-card:hover {
  transform: translateY(-2px);
}
.card-head {
  display: flex;
  justify-content: space-between;
  align-items: center;
  gap: 8px;
  min-width: 0;
}
.name {
  font-weight: 600;
  overflow: hidden;
  text-overflow: ellipsis;
  white-space: nowrap;
}
.desc {
  color: var(--el-text-color-secondary);
  min-height: 40px;
  margin: 0 0 8px;
  display: -webkit-box;
  -webkit-line-clamp: 2;
  -webkit-box-orient: vertical;
  overflow: hidden;
}
.meta {
  display: flex;
  gap: 8px;
  margin-bottom: 6px;
  flex-wrap: wrap;
}
.path {
  color: var(--el-text-color-secondary);
  font-size: 12px;
  word-break: break-all;
  display: -webkit-box;
  -webkit-line-clamp: 1;
  -webkit-box-orient: vertical;
  overflow: hidden;
}
.danger {
  color: var(--el-color-danger);
}
</style>
