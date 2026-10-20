<template>
  <div class="node">
    <div class="node-row" :style="{ paddingLeft: depth * 24 + 'px' }" @click="toggle">
      <span class="toggle" :class="{ has: hasChildren }">{{ hasChildren ? (expanded ? '▼' : '▶') : '·' }}</span>
      <span class="badge" :class="badgeClass">{{ badgeText }}</span>
      <span class="title">{{ node.title }}</span>
      <span v-if="hasChildren" class="count">({{ node.children.length }})</span>
    </div>
    <div v-if="expanded && hasChildren" class="children">
      <MindmapTreeNode v-for="c in node.children" :key="c.id" :node="c" :depth="depth + 1" :expand-level="expandLevel" />
    </div>
  </div>
</template>

<script setup>
import { computed, ref, watch } from 'vue'

defineOptions({ name: 'MindmapTreeNode' })

const props = defineProps({
  node: { type: Object, required: true },
  depth: { type: Number, default: 0 },
  expandLevel: { type: Number, default: 3 },
})

const expanded = ref(props.depth < props.expandLevel)
watch(() => props.expandLevel, (v) => { expanded.value = props.depth < v })

const hasChildren = computed(() => (props.node.children || []).length > 0)
function toggle() {
  if (hasChildren.value) expanded.value = !expanded.value
}

const badgeClass = computed(() => {
  const id = props.node.id || ''
  if (id === 'root') return 'badge-root'
  if (id.startsWith('module-')) return 'badge-module'
  if (id.startsWith('type-')) return 'badge-type'
  if (id.startsWith('case-')) return 'badge-case'
  if (id.startsWith('pre-')) return 'badge-pre'
  if (id.startsWith('steps-')) return 'badge-steps'
  if (id.startsWith('s-')) return 'badge-s'
  if (id.startsWith('exp-')) return 'badge-exp'
  return 'badge-default'
})

const badgeText = computed(() => {
  const id = props.node.id || ''
  if (id === 'root') return '根'
  if (id.startsWith('module-')) return '模块'
  if (id.startsWith('type-')) return '类型'
  if (id.startsWith('case-')) return '用例'
  if (id.startsWith('pre-')) return '前置'
  if (id.startsWith('steps-')) return '步骤'
  if (id.startsWith('s-')) return '步骤'
  if (id.startsWith('exp-')) return '预期'
  return ''
})
</script>

<style scoped>
.node-row { display: flex; align-items: center; gap: 8px; padding: 4px 0; cursor: pointer; border-radius: 4px; }
.node-row:hover { background: var(--el-fill-color-light); }
.toggle { width: 16px; color: var(--el-text-color-secondary); font-size: 12px; user-select: none; }
.toggle.has { color: var(--el-color-primary); }
.badge { font-size: 11px; padding: 1px 6px; border-radius: 4px; white-space: nowrap; }
.badge-root { background: #303133; color: #fff; }
.badge-module { background: #409eff; color: #fff; }
.badge-type { background: #67c23a; color: #fff; }
.badge-case { background: #ecf5ff; color: #409eff; border: 1px solid #409eff; }
.badge-pre { background: #f4f4f5; color: #909399; }
.badge-steps { background: #fdf6ec; color: #e6a23c; border: 1px solid #e6a23c; }
.badge-s { background: #fdf6ec; color: #e6a23c; }
.badge-exp { background: #f0f9eb; color: #67c23a; border: 1px solid #67c23a; }
.badge-default { background: #f4f4f5; color: #909399; }
.title { font-size: 14px; }
.count { color: var(--el-text-color-secondary); font-size: 12px; }
.children { }
</style>
