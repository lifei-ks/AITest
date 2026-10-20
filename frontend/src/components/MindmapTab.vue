<template>
  <div v-loading="loading">
    <div class="toolbar">
      <el-select v-model="requirementId" size="small" clearable placeholder="全部需求版本" style="width: 220px">
        <el-option
          v-for="r in requirements"
          :key="r.id"
          :label="`${r.version} · ${r.name}`"
          :value="r.id"
        />
      </el-select>
      <el-radio-group v-model="view" size="small">
        <el-radio-button value="outline">大纲视图</el-radio-button>
        <el-radio-button value="tree">脑图视图</el-radio-button>
      </el-radio-group>
      <template v-if="view === 'outline'">
        <el-button size="small" @click="expandAll = 999">全部展开</el-button>
        <el-button size="small" @click="expandAll = 2">全部收起</el-button>
      </template>
      <el-divider direction="vertical" />
      <el-button :loading="exportingMd" @click="exportMd">导出 Markdown</el-button>
      <el-button :loading="exportingXmind" @click="exportXmind">导出 XMind</el-button>
      <el-button :loading="loading" @click="load">
        <el-icon><Refresh /></el-icon>
      </el-button>
      <el-tag v-if="stat" type="info" class="tag">
        模块 {{ stat.modules }} / 类型 {{ stat.types }} / 用例 {{ stat.cases }}
      </el-tag>
    </div>

    <el-empty
      v-if="!loading && !tree.children?.length"
      description="暂无用例，请先在「用例生成」中生成"
    />

    <!-- 大纲视图（默认，清晰） -->
    <div v-else-if="view === 'outline'" class="outline">
      <MindmapTreeNode v-for="c in tree.children" :key="c.id" :node="c" :depth="0" :expand-level="expandAll" />
    </div>

    <!-- 脑图视图（ECharts tree，辐射状） -->
    <div v-else ref="chart" class="chart" />
  </div>
</template>

<script setup>
import { nextTick, onBeforeUnmount, onMounted, ref, watch } from 'vue'
import * as echarts from 'echarts'
import { downloadMindmap, previewMindmap } from '../api/mindmap'
import { listRequirements } from '../api/requirements'
import MindmapTreeNode from './MindmapTreeNode.vue'

const props = defineProps({
  projectId: { type: String, required: true },
  requirementId: { type: String, default: '' },
  refreshKey: { type: Number, default: 0 },
})
const requirements = ref([])
const requirementId = ref(props.requirementId || '')
const view = ref('outline')
const expandAll = ref(3) // 默认展开 3 层（模块/类型/用例）
const stat = ref(null)
const tree = ref({ children: [] })
const loading = ref(false)
const exportingMd = ref(false)
const exportingXmind = ref(false)
const chart = ref(null)
let instance = null

async function load() {
  loading.value = true
  try {
    const data = await previewMindmap(props.projectId, requirementId.value || undefined)
    stat.value = data.statistics
    tree.value = data.tree || { children: [] }
  } finally {
    loading.value = false
  }
}

async function loadRequirements() {
  try {
    requirements.value = await listRequirements(props.projectId)
  } catch {
    requirements.value = []
  }
}

function toEcharts(node) {
  return { name: node.title, children: (node.children || []).map(toEcharts) }
}

function shortLabel(name, max = 14) {
  return name.length > max ? name.slice(0, max) + '…' : name
}

function renderTree() {
  if (!chart.value || !tree.value.children?.length) return
  if (instance && instance.getDom() !== chart.value) {
    instance.dispose()
    instance = null
  }
  if (!instance) instance = echarts.init(chart.value)
  instance.setOption({
    tooltip: { trigger: 'item', formatter: (p) => p.name },
    series: [
      {
        type: 'tree',
        data: [toEcharts(tree.value)],
        layout: 'radial',
        roam: true,
        expandAndCollapse: true,
        initialTreeDepth: 2,
        symbolSize: 8,
        label: { fontSize: 13, formatter: (p) => shortLabel(p.name) },
        lineStyle: { width: 1.5, curveness: 0.5 },
        emphasis: { focus: 'descendant' },
      },
    ],
  })
}

function resize() {
  instance?.resize()
}

watch(view, async (v) => {
  if (v === 'tree') {
    await nextTick()
    renderTree()
  }
})

watch(tree, async () => {
  if (view.value === 'tree') {
    await nextTick()
    renderTree()
  }
})

function downloadBlob(blob, name) {
  const url = URL.createObjectURL(blob)
  const a = document.createElement('a')
  a.href = url
  a.download = name
  a.click()
  URL.revokeObjectURL(url)
}

async function exportMd() {
  exportingMd.value = true
  try {
    downloadBlob(await downloadMindmap(props.projectId, 'md', requirementId.value || undefined), 'mindmap.md')
  } finally {
    exportingMd.value = false
  }
}

async function exportXmind() {
  exportingXmind.value = true
  try {
    downloadBlob(await downloadMindmap(props.projectId, 'xmind', requirementId.value || undefined), 'mindmap.xmind')
  } finally {
    exportingXmind.value = false
  }
}

watch(requirementId, () => load())
watch(
  () => props.requirementId,
  (v) => {
    if ((v || '') !== requirementId.value) requirementId.value = v || ''
  },
)
watch(
  () => props.projectId,
  () => {
    requirementId.value = ''
    loadRequirements()
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
  window.addEventListener('resize', resize)
  await Promise.all([loadRequirements(), load()])
})

onBeforeUnmount(() => {
  window.removeEventListener('resize', resize)
  if (instance) {
    instance.dispose()
    instance = null
  }
})
</script>

<style scoped>
.toolbar {
  display: flex;
  gap: 8px;
  align-items: center;
  margin-bottom: 12px;
  flex-wrap: wrap;
}
.tag {
  margin-left: 4px;
}
.outline {
  border: 1px solid var(--el-border-color-light);
  border-radius: 6px;
  padding: 12px;
  max-height: 720px;
  overflow: auto;
}
.chart {
  width: 100%;
  height: 720px;
  border: 1px solid var(--el-border-color-light);
  border-radius: 6px;
}
</style>
