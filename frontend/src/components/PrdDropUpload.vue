<template>
  <el-upload
    ref="uploadRef"
    v-model:file-list="files"
    drag
    :auto-upload="false"
    :show-file-list="showFileList"
    :limit="1"
    accept=".md,.txt,.pdf,.docx"
    :disabled="disabled"
    :on-change="onChange"
    :on-remove="onRemove"
    :on-exceed="onExceed"
    class="prd-drop"
  >
    <el-icon class="upload-icon"><UploadFilled /></el-icon>
    <div class="upload-title">{{ title }}</div>
    <div class="upload-subtitle">拖拽到此处，或点击选择文件</div>
    <template #tip>
      <div class="el-upload__tip">{{ hint }}</div>
    </template>
  </el-upload>
</template>

<script setup>
import { ref } from 'vue'
import { ElMessage } from 'element-plus'

const props = defineProps({
  modelValue: { type: Object, default: null },
  title: { type: String, default: '上传需求文档' },
  hint: { type: String, default: '支持 .md、.txt、.pdf、.docx' },
  disabled: { type: Boolean, default: false },
  showFileList: { type: Boolean, default: true },
  maxSizeMB: { type: Number, default: 20 },
})

const emit = defineEmits(['update:modelValue'])
const uploadRef = ref()
const files = ref([])
const ALLOWED_EXTS = ['.md', '.txt', '.pdf', '.docx']

function clear() {
  files.value = []
  uploadRef.value?.clearFiles()
  emit('update:modelValue', null)
}

function acceptFile(raw) {
  if (!raw) return
  const lower = (raw.name || '').toLowerCase()
  if (!ALLOWED_EXTS.some((ext) => lower.endsWith(ext))) {
    ElMessage.error('仅支持 .md、.txt、.pdf、.docx 文件')
    clear()
    return
  }
  if (raw.size > props.maxSizeMB * 1024 * 1024) {
    ElMessage.error(`文件不能超过 ${props.maxSizeMB} MB`)
    clear()
    return
  }
  emit('update:modelValue', raw)
}

function onChange(uploadFile) {
  files.value = uploadFile ? [uploadFile] : []
  acceptFile(uploadFile?.raw)
}

function onRemove() {
  files.value = []
  emit('update:modelValue', null)
}

function onExceed() {
  ElMessage.warning('一次只能上传一个文件，请先移除当前文件')
}

defineExpose({ clear })
</script>

<style scoped>
.prd-drop {
  width: 100%;
}
.prd-drop :deep(.el-upload-dragger) {
  padding: 18px 14px;
  border-radius: 8px;
}
.upload-icon {
  font-size: 34px;
  color: var(--el-color-primary);
}
.upload-title {
  margin-top: 2px;
  color: var(--el-text-color-primary);
  font-size: 14px;
  font-weight: 600;
}
.upload-subtitle {
  margin-top: 4px;
  color: var(--el-text-color-secondary);
  font-size: 12px;
}
</style>
