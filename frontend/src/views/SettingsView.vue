<template>
  <el-card v-loading="loading" class="card">
    <template #header>
      <div class="head">
        <el-icon color="#409eff"><Setting /></el-icon>
        <span class="title">LLM 供应商配置</span>
        <el-tag v-if="configured" size="small" type="success">已配置</el-tag>
        <el-tag v-else size="small" type="info">未配置（规则模式）</el-tag>
        <el-tag size="small" effect="plain" :type="visionConfigured ? 'success' : 'info'">
          {{ visionConfigured
            ? (visionSameAsText ? '截图审核：复用文本 LLM' : '截图审核：独立多模态 LLM')
            : '截图审核不可用' }}
        </el-tag>
      </div>
    </template>

    <el-form label-width="120px" class="form">
      <el-form-item label="API Key">
        <div class="field">
          <el-input v-model="form.llmApiKey" placeholder="sk-..." show-password clearable :disabled="loading" />
          <div class="tip">留空保存即清除 Key；未配置时系统走纯规则模式（功能完整可用）</div>
        </div>
      </el-form-item>
      <el-form-item label="Base URL">
        <el-input v-model="form.llmBaseUrl" placeholder="https://api.deepseek.com/v1" :disabled="loading" />
      </el-form-item>
      <el-form-item label="模型">
        <el-input v-model="form.llmModel" placeholder="deepseek-chat" :disabled="loading" />
      </el-form-item>

      <el-divider content-position="left">多模态 LLM（执行截图审核）</el-divider>
      <el-form-item label="复用文本 LLM">
        <div class="field">
          <el-switch v-model="visionSameAsText" :disabled="loading" @change="onVisionSameChange" />
          <div class="tip">
            默认与文本 LLM 相同。关闭后可单独填写多模态模型；Key / Base URL 留空时仍复用文本配置。
          </div>
        </div>
      </el-form-item>
      <template v-if="!visionSameAsText">
        <el-form-item label="多模态模型">
          <el-input v-model="form.llmVisionModel" placeholder="如 gpt-4o / qwen-vl-max" :disabled="loading" />
        </el-form-item>
        <el-form-item label="Base URL">
          <el-input v-model="form.llmVisionBaseUrl" placeholder="留空复用文本 LLM Base URL" :disabled="loading" />
        </el-form-item>
        <el-form-item label="API Key">
          <el-input
            v-model="form.llmVisionApiKey"
            placeholder="留空复用文本 LLM API Key"
            show-password
            clearable
            :disabled="loading"
          />
        </el-form-item>
      </template>
      <el-form-item v-else>
        <span class="tip">
          当前使用：{{ form.llmModel || '未配置模型' }}（{{ form.llmBaseUrl || '默认 Base URL' }}）
        </span>
      </el-form-item>

      <el-form-item>
        <el-button type="primary" :loading="saving" :disabled="loading" @click="save">保存</el-button>
        <el-button :loading="testing" :disabled="loading || !hasKey" @click="test">
          <el-icon class="mr4"><Connection /></el-icon>测试连通性
        </el-button>
      </el-form-item>
    </el-form>

    <el-alert v-if="hint" :title="hint" :type="hintType" :closable="false" class="alert" />
    <el-alert
      type="info"
      :closable="false"
      title="LLM 用于需求测试点/用例措辞，并复用同一模型审核执行截图（需支持多模态图片输入）；静态分析/状态机/质量闸/覆盖率仍为纯规则。"
    />
  </el-card>
</template>

<script setup>
import { computed, onMounted, ref } from 'vue'
import { ElMessage } from 'element-plus'
import { getSettings, testLlm, updateSettings } from '../api/settings'

const form = ref({
  llmApiKey: '',
  llmBaseUrl: '',
  llmModel: '',
  llmVisionApiKey: '',
  llmVisionBaseUrl: '',
  llmVisionModel: '',
})
const loading = ref(false)
const saving = ref(false)
const testing = ref(false)
const configured = ref(false)
const visionConfigured = ref(false)
// 默认多模态与文本 LLM 相同
const visionSameAsText = ref(true)
const hint = ref('')
const hintType = ref('success')

const hasKey = computed(() => configured.value || !!form.value.llmApiKey)

async function load() {
  loading.value = true
  try {
    const data = await getSettings()
    form.value = {
      llmApiKey: data.llmApiKey || '',
      llmBaseUrl: data.llmBaseUrl || '',
      llmModel: data.llmModel || '',
      llmVisionApiKey: data.llmVisionApiKey || '',
      llmVisionBaseUrl: data.llmVisionBaseUrl || '',
      llmVisionModel: data.llmVisionModel || '',
    }
    configured.value = !!data.llmApiKeyConfigured
    visionConfigured.value = !!data.visionConfigured
    visionSameAsText.value = data.visionUsesText !== false
  } finally {
    loading.value = false
  }
}

function onVisionSameChange(sameAsText) {
  // 关闭“复用”时用文本配置预填，方便只改模型名
  if (!sameAsText) {
    if (!form.value.llmVisionModel) form.value.llmVisionModel = form.value.llmModel
    if (!form.value.llmVisionBaseUrl) form.value.llmVisionBaseUrl = form.value.llmBaseUrl
  }
}

async function save() {
  saving.value = true
  try {
    await updateSettings({
      llmApiKey: form.value.llmApiKey || '',
      llmBaseUrl: form.value.llmBaseUrl,
      llmModel: form.value.llmModel,
      // 复用文本 LLM 时清空独立配置，后端按空值回退
      llmVisionApiKey: visionSameAsText.value ? '' : (form.value.llmVisionApiKey || ''),
      llmVisionBaseUrl: visionSameAsText.value ? '' : form.value.llmVisionBaseUrl,
      llmVisionModel: visionSameAsText.value ? '' : form.value.llmVisionModel,
    })
    ElMessage.success('已保存')
    hint.value = ''
    await load()
  } finally {
    saving.value = false
  }
}

async function test() {
  testing.value = true
  hint.value = ''
  try {
    const r = await testLlm()
    hintType.value = 'success'
    hint.value = `连接成功（模型：${r.model}）`
  } catch (e) {
    hintType.value = 'error'
    hint.value = e.message || '连接失败'
  } finally {
    testing.value = false
  }
}

onMounted(load)
</script>

<style scoped>
.card {
  max-width: 760px;
}
.head {
  display: flex;
  align-items: center;
  gap: 8px;
}
.title {
  font-weight: 600;
}
.form {
  max-width: 620px;
}
.field {
  width: 100%;
}
.tip {
  color: var(--el-text-color-secondary);
  font-size: 12px;
  line-height: 1.6;
  margin-top: 4px;
}
.alert {
  margin-bottom: 12px;
}
.mr4 {
  margin-right: 4px;
}
</style>
