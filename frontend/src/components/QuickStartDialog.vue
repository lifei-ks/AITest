<template>
  <el-dialog
    :model-value="modelValue"
    title="快速创建项目并上传需求"
    width="680px"
    :close-on-click-modal="false"
    @update:model-value="$emit('update:modelValue', $event)"
    @closed="reset"
  >
    <el-form ref="formRef" :model="form" :rules="rules" label-width="98px">
      <div class="section-title">1. 项目</div>
      <el-form-item label="项目名称" prop="name">
        <el-input v-model="form.name" maxlength="120" placeholder="如：订单系统" />
      </el-form-item>
      <el-form-item label="源码路径" prop="sourcePath">
        <el-input v-model="form.sourcePath" placeholder="D:/workspace/order-system" />
        <div class="hint">当前代码分析读取本机目录，暂不执行 Git 拉取或源码压缩包解压。</div>
      </el-form-item>
      <el-form-item label="被测地址" prop="targetUrl">
        <el-input v-model="form.targetUrl" placeholder="http://127.0.0.1:8200" />
      </el-form-item>

      <div class="section-title">2. 需求文档</div>
      <el-form-item label="版本号" prop="version">
        <el-input v-model="form.version" maxlength="40" placeholder="V1.0" />
      </el-form-item>
      <el-form-item label="需求名称" prop="requirementName">
        <el-input v-model="form.requirementName" maxlength="120" placeholder="如：订单主流程" />
      </el-form-item>
      <el-form-item label="文档来源">
        <el-radio-group v-model="prdMode">
          <el-radio-button value="file">上传文件</el-radio-button>
          <el-radio-button value="text">粘贴文本</el-radio-button>
        </el-radio-group>
      </el-form-item>
      <el-form-item v-if="prdMode === 'file'" label="需求文档">
        <PrdDropUpload
          ref="prdDropRef"
          v-model="prdFile"
          title="拖拽需求文档到这里"
          hint="支持 .md、.txt、.pdf、.docx"
          :disabled="saving"
        />
      </el-form-item>
      <el-form-item v-else label="需求内容" prop="prdText">
        <el-input
          v-model="form.prdText"
          type="textarea"
          :rows="7"
          placeholder="粘贴本期需求文档（Markdown / 纯文本）"
        />
      </el-form-item>
    </el-form>

    <el-alert
      type="info"
      :closable="false"
      show-icon
      title="创建后会选中该需求版本；点击 Pipeline 页面的「一键执行 Pipeline」即可从代码分析跑到执行结果。"
      class="tip"
    />

    <template #footer>
      <el-button @click="$emit('update:modelValue', false)">取消</el-button>
      <el-button type="primary" :loading="saving" @click="submit">创建并继续</el-button>
    </template>
  </el-dialog>
</template>

<script setup>
import { computed, reactive, ref } from 'vue'
import { ElMessage } from 'element-plus'
import { createProject } from '../api/projects'
import { createRequirement, uploadRequirementPrd } from '../api/requirements'
import PrdDropUpload from './PrdDropUpload.vue'
import { useConfigStore } from '../stores/config'

const props = defineProps({
  modelValue: { type: Boolean, default: false },
})
const emit = defineEmits(['update:modelValue', 'created'])

const store = useConfigStore()
const formRef = ref()
const prdDropRef = ref()
const prdFile = ref(null)
const prdMode = ref('file')
const saving = ref(false)

const form = reactive({
  name: '',
  sourcePath: '',
  targetUrl: 'http://127.0.0.1:8200',
  version: 'V1.0',
  requirementName: '',
  prdText: '',
})

const URL_RE = /^https?:\/\/[^\s]+$/i

function validateUrl(_rule, value, callback) {
  if (!value) return callback()
  return URL_RE.test(value) ? callback() : callback(new Error('请输入 http:// 或 https:// 开头的地址'))
}

const rules = computed(() => ({
  name: [{ required: true, message: '请填写项目名称', trigger: 'blur' }],
  sourcePath: [{ required: true, message: '请填写源码路径', trigger: 'blur' }],
  targetUrl: [{ validator: validateUrl, trigger: 'blur' }],
  version: [{ required: true, message: '请填写版本号', trigger: 'blur' }],
  requirementName: [{ required: true, message: '请填写需求名称', trigger: 'blur' }],
  prdText:
    prdMode.value === 'text'
      ? [{ required: true, message: '请粘贴需求内容', trigger: 'blur' }]
      : [],
}))

function reset() {
  saving.value = false
  prdFile.value = null
  prdMode.value = 'file'
  prdDropRef.value?.clear()
  form.name = ''
  form.sourcePath = ''
  form.targetUrl = 'http://127.0.0.1:8200'
  form.version = 'V1.0'
  form.requirementName = ''
  form.prdText = ''
  formRef.value?.clearValidate()
}

async function submit() {
  const valid = await formRef.value?.validate().catch(() => false)
  if (!valid) return
  if (prdMode.value === 'file' && !prdFile.value) {
    ElMessage.warning('请选择需求文档，或改为粘贴文本')
    return
  }
  if (!form.name.trim()) return ElMessage.warning('项目名称不能只包含空白')
  if (!form.sourcePath.trim()) return ElMessage.warning('源码路径不能只包含空白')
  if (!form.version.trim()) return ElMessage.warning('版本号不能只包含空白')
  if (!form.requirementName.trim()) return ElMessage.warning('需求名称不能只包含空白')
  if (prdMode.value === 'text' && !form.prdText.trim()) {
    return ElMessage.warning('需求内容不能只包含空白')
  }

  saving.value = true
  let project = null
  let requirement = null
  try {
    project = await createProject({
      name: form.name.trim(),
      sourceType: 'local_path',
      sourcePath: form.sourcePath.trim(),
      targetUrl: form.targetUrl.trim(),
    })
    requirement = await createRequirement(project.id, {
      name: form.requirementName.trim(),
      version: form.version.trim(),
      content: prdMode.value === 'text' ? form.prdText : '',
    })
    if (prdMode.value === 'file') {
      await uploadRequirementPrd(requirement.id, prdFile.value)
    }

    await store.loadProjects().catch(() => {})
    store.setProject(project.id)
    ElMessage.success('项目与需求已创建，已选中新需求版本')
    emit('created', { projectId: project.id, requirementId: requirement.id })
    emit('update:modelValue', false)
  } catch {
    if (project) {
      await store.loadProjects().catch(() => {})
      store.setProject(project.id)
      ElMessage.warning(
        requirement
          ? '项目与需求版本已创建，但文档上传失败，请到「需求版本」重新上传'
          : '项目已创建，但需求版本创建失败，请到「需求版本」补充',
      )
    }
  } finally {
    saving.value = false
  }
}
</script>

<style scoped>
.section-title {
  font-weight: 600;
  font-size: 13px;
  margin: 4px 0 12px;
  padding-bottom: 6px;
  border-bottom: 1px solid var(--el-border-color-lighter);
}
.hint {
  font-size: 12px;
  color: var(--el-text-color-secondary);
  margin-top: 4px;
}
.tip {
  margin-top: 4px;
}
</style>
