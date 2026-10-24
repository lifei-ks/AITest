<template>
  <div v-loading="loading" class="settings-page">
    <div class="page-head">
      <div>
        <h2>LLM 配置</h2>
        <p>文本 LLM 用于测试点/用例生成与审核；多模态 LLM 用于执行截图审核。两类都可以配置多个，生成时可选择具体配置。</p>
      </div>
      <el-button :loading="loading" @click="load">刷新</el-button>
    </div>

    <el-row :gutter="16">
      <el-col :xs="24" :lg="12">
        <el-card shadow="never" class="kind-card">
          <template #header>
            <div class="card-head">
              <div>
                <span class="card-title">文本 LLM</span>
                <div class="muted">测试点、用例生成、用例审核、结果分析</div>
              </div>
              <el-button type="primary" size="small" @click="openCreate('text')">新增文本配置</el-button>
            </div>
          </template>

          <div v-if="!textProfiles.length" class="empty">
            <el-empty description="还没有文本 LLM 配置" :image-size="60">
              <el-button type="primary" size="small" @click="openCreate('text')">添加配置</el-button>
            </el-empty>
          </div>

          <div v-for="profile in textProfiles" :key="profile.id" class="profile-row">
            <div class="profile-main">
              <div class="profile-title">
                <span>{{ profile.name }}</span>
                <el-tag v-if="profile.isDefault" size="small" type="success" effect="plain">默认</el-tag>
                <el-tag v-if="!profile.enabled" size="small" type="info">已停用</el-tag>
              </div>
              <div class="profile-meta">
                <span class="model">{{ profile.model || '未填模型' }}</span>
                <span class="dot">·</span>
                <span :title="profile.baseUrl">{{ profile.baseUrl || '默认 Base URL' }}</span>
              </div>
              <div class="profile-key">
                API Key：{{ profile.apiKeyConfigured ? profile.apiKey : '未配置' }}
              </div>
            </div>
            <div class="profile-actions">
              <el-button link type="primary" :loading="testingId === profile.id" @click="testProfile(profile)">测试</el-button>
              <el-button link @click="openEdit(profile)">编辑</el-button>
              <el-button v-if="!profile.isDefault" link @click="setDefault(profile)">设为默认</el-button>
              <el-button link type="danger" @click="removeProfile(profile)">删除</el-button>
            </div>
          </div>
        </el-card>
      </el-col>

      <el-col :xs="24" :lg="12">
        <el-card shadow="never" class="kind-card">
          <template #header>
            <div class="card-head">
              <div>
                <span class="card-title">多模态 LLM（VLM）</span>
                <div class="muted">执行截图审核；没有可用 VLM 时回退默认文本 LLM</div>
              </div>
              <el-button type="primary" size="small" @click="openCreate('vision')">新增 VLM 配置</el-button>
            </div>
          </template>

          <div v-if="!visionProfiles.length" class="empty">
            <el-empty description="还没有独立 VLM 配置；截图审核将复用默认文本 LLM" :image-size="60">
              <el-button type="primary" size="small" @click="openCreate('vision')">添加 VLM 配置</el-button>
            </el-empty>
          </div>

          <div v-for="profile in visionProfiles" :key="profile.id" class="profile-row">
            <div class="profile-main">
              <div class="profile-title">
                <span>{{ profile.name }}</span>
                <el-tag v-if="profile.isDefault" size="small" type="success" effect="plain">默认</el-tag>
                <el-tag v-if="!profile.enabled" size="small" type="info">已停用</el-tag>
              </div>
              <div class="profile-meta">
                <span class="model">{{ profile.model || '未填模型' }}</span>
                <span class="dot">·</span>
                <span :title="profile.baseUrl">{{ profile.baseUrl || '默认 Base URL' }}</span>
              </div>
              <div class="profile-key">
                API Key：{{ profile.apiKeyConfigured ? profile.apiKey : '未配置' }}
              </div>
            </div>
            <div class="profile-actions">
              <el-button link type="primary" :loading="testingId === profile.id" @click="testProfile(profile)">测试</el-button>
              <el-button link @click="openEdit(profile)">编辑</el-button>
              <el-button v-if="!profile.isDefault" link @click="setDefault(profile)">设为默认</el-button>
              <el-button link type="danger" @click="removeProfile(profile)">删除</el-button>
            </div>
          </div>
        </el-card>
      </el-col>
    </el-row>

    <el-alert
      type="info"
      :closable="false"
      show-icon
      title="API Key 只保存在服务端，接口仅返回掩码。测试点/用例生成页面可以选择具体文本 LLM；未选择时使用默认文本配置。"
      class="tip"
    />

    <el-dialog
      v-model="dialogVisible"
      :title="`${editing.id ? '编辑' : '新增'}${form.kind === 'vision' ? ' VLM' : '文本 LLM'} 配置`"
      width="560px"
      :close-on-click-modal="false"
      @closed="resetForm"
    >
      <el-form ref="formRef" :model="form" :rules="rules" label-width="100px">
        <el-form-item label="名称" prop="name">
          <el-input v-model="form.name" maxlength="120" placeholder="如：DeepSeek 主账号 / 备用模型" />
        </el-form-item>
        <el-form-item label="模型" prop="model">
          <el-input v-model="form.model" maxlength="120" placeholder="deepseek-chat / gpt-4o / qwen-vl-max" />
        </el-form-item>
        <el-form-item label="Base URL">
          <el-input v-model="form.baseUrl" placeholder="https://api.deepseek.com/v1" />
          <div class="tip">
            OpenAI 兼容地址只填到版本前缀，例如智谱填 https://open.bigmodel.cn/api/paas/v4；
            系统会自动追加 /chat/completions，不要重复填写。
          </div>
        </el-form-item>
        <el-form-item label="API Key" prop="apiKey">
          <el-input
            v-model="form.apiKey"
            show-password
            clearable
            :placeholder="editing.id ? '留空/保持掩码表示不修改' : 'sk-...'"
          />
        </el-form-item>
        <el-form-item label="启用">
          <el-switch v-model="form.enabled" />
        </el-form-item>
        <el-form-item label="设为默认">
          <el-switch v-model="form.isDefault" />
          <span class="muted hint">同类配置只能有一个默认项</span>
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
import { computed, onMounted, reactive, ref } from 'vue'
import { ElMessage, ElMessageBox } from 'element-plus'
import {
  createLlmProfile,
  deleteLlmProfile,
  listLlmProfiles,
  setDefaultLlmProfile,
  testLlmProfile,
  updateLlmProfile,
} from '../api/settings'

const loading = ref(false)
const saving = ref(false)
const testingId = ref('')
const profiles = ref([])
const dialogVisible = ref(false)
const editing = ref({})
const formRef = ref()

const form = reactive({
  kind: 'text',
  name: '',
  model: '',
  baseUrl: '',
  apiKey: '',
  enabled: true,
  isDefault: false,
})

const rules = computed(() => ({
  name: [{ required: true, message: '请填写配置名称', trigger: 'blur' }],
  model: [{ required: true, message: '请填写模型名称', trigger: 'blur' }],
  apiKey: editing.value.id ? [] : [{ required: true, message: '请填写 API Key', trigger: 'blur' }],
}))

const textProfiles = computed(() => profiles.value.filter((item) => item.kind === 'text'))
const visionProfiles = computed(() => profiles.value.filter((item) => item.kind === 'vision'))

async function load() {
  loading.value = true
  try {
    profiles.value = await listLlmProfiles()
  } finally {
    loading.value = false
  }
}

function openCreate(kind) {
  editing.value = {}
  Object.assign(form, {
    kind,
    name: '',
    model: '',
    baseUrl: '',
    apiKey: '',
    enabled: true,
    isDefault: !profiles.value.some((item) => item.kind === kind),
  })
  dialogVisible.value = true
}

function openEdit(profile) {
  editing.value = profile
  Object.assign(form, {
    kind: profile.kind,
    name: profile.name,
    model: profile.model,
    baseUrl: profile.baseUrl,
    apiKey: profile.apiKey,
    enabled: profile.enabled,
    isDefault: profile.isDefault,
  })
  dialogVisible.value = true
}

function resetForm() {
  formRef.value?.clearValidate()
}

async function save() {
  const valid = await formRef.value?.validate().catch(() => false)
  if (!valid) return
  saving.value = true
  try {
    const payload = {
      name: form.name.trim(),
      kind: form.kind,
      model: form.model.trim(),
      baseUrl: form.baseUrl.trim(),
      enabled: form.enabled,
      isDefault: form.isDefault,
    }
    // 编辑时留空表示保留原 Key；新建时必须填写
    if (!editing.value.id || form.apiKey) {
      payload.apiKey = form.apiKey
    }
    if (editing.value.id) {
      await updateLlmProfile(editing.value.id, payload)
    } else {
      await createLlmProfile(payload)
    }
    ElMessage.success('已保存')
    dialogVisible.value = false
    await load()
  } finally {
    saving.value = false
  }
}

async function testProfile(profile) {
  testingId.value = profile.id
  try {
    const result = await testLlmProfile(profile.id)
    ElMessage.success(`连接成功（模型：${result.model || profile.model}）`)
  } finally {
    testingId.value = ''
  }
}

async function setDefault(profile) {
  await setDefaultLlmProfile(profile.id)
  ElMessage.success(`已将「${profile.name}」设为默认`)
  await load()
}

async function removeProfile(profile) {
  try {
    await ElMessageBox.confirm(
      `删除 LLM 配置「${profile.name}」？如果它是唯一配置，请先新增替代配置。`,
      '删除配置',
      { type: 'warning' },
    )
    await deleteLlmProfile(profile.id)
    ElMessage.success('已删除')
    await load()
  } catch {
    /* 取消删除或请求层已提示 */
  }
}

onMounted(load)
</script>

<style scoped>
.settings-page {
  display: flex;
  flex-direction: column;
  gap: 14px;
}
.page-head {
  display: flex;
  align-items: flex-start;
  justify-content: space-between;
  gap: 16px;
}
.page-head h2 {
  margin: 0;
  font-size: 20px;
}
.page-head p {
  margin: 6px 0 0;
  color: var(--el-text-color-secondary);
  font-size: 13px;
}
.kind-card {
  min-height: 360px;
}
.card-head {
  display: flex;
  align-items: flex-start;
  justify-content: space-between;
  gap: 12px;
}
.card-title {
  font-weight: 600;
}
.muted {
  color: var(--el-text-color-secondary);
  font-size: 12px;
}
.hint {
  margin-left: 10px;
}
.profile-row {
  display: flex;
  justify-content: space-between;
  gap: 12px;
  padding: 12px 4px;
  border-bottom: 1px solid var(--el-border-color-lighter);
}
.profile-row:last-child {
  border-bottom: none;
}
.profile-main {
  min-width: 0;
}
.profile-title {
  display: flex;
  align-items: center;
  gap: 6px;
  font-weight: 600;
}
.profile-meta {
  margin-top: 4px;
  color: var(--el-text-color-secondary);
  font-size: 12px;
  white-space: nowrap;
  overflow: hidden;
  text-overflow: ellipsis;
}
.profile-meta .model {
  color: var(--el-text-color-regular);
}
.profile-key {
  margin-top: 4px;
  font-size: 12px;
  color: var(--el-text-color-placeholder);
}
.profile-actions {
  flex: none;
  display: flex;
  flex-wrap: wrap;
  align-content: flex-start;
  gap: 2px 8px;
}
.profile-actions :deep(.el-button + .el-button) {
  margin-left: 0;
}
.empty {
  padding: 24px 0;
}
.tip {
  margin-top: 2px;
}
</style>
