<template>
  <aside class="proj-aside">
    <div class="aside-head">
      <span class="aside-title">项目</span>
      <el-button link :loading="loading" title="刷新项目列表" @click="load">
        <el-icon><Refresh /></el-icon>
      </el-button>
    </div>

    <el-input
      v-if="store.projects.length > 6"
      v-model="keyword"
      size="small"
      placeholder="搜索项目"
      clearable
      class="search"
    >
      <template #prefix><el-icon><Search /></el-icon></template>
    </el-input>

    <el-scrollbar class="aside-body">
      <div
        v-for="p in filtered"
        :key="p.id"
        class="proj-item"
        :class="{ active: p.id === store.currentProjectId }"
        @click="select(p)"
      >
        <span class="dot" :class="statusDot(p.analysisStatus)" />
        <div class="proj-text">
          <div class="proj-name" :title="p.name">{{ p.name }}</div>
          <div class="proj-meta">{{ statusLabel(p.analysisStatus) }}</div>
        </div>
        <el-icon v-if="p.id === store.currentProjectId" class="checked"><Check /></el-icon>
      </div>

      <el-empty v-if="!loading && !store.projects.length" :image-size="50" description="暂无项目">
        <el-button size="small" type="primary" @click="$router.push('/projects')">新建项目</el-button>
      </el-empty>
      <div v-else-if="!loading && !filtered.length" class="no-match">没有匹配的项目</div>
    </el-scrollbar>

    <div class="aside-foot">
      <el-button link class="manage" @click="$router.push('/projects')">
        <el-icon><Setting /></el-icon>管理项目
      </el-button>
    </div>
  </aside>
</template>

<script setup>
import { computed, onMounted, ref } from 'vue'
import { useConfigStore } from '../stores/config'

const store = useConfigStore()
const loading = ref(false)
const keyword = ref('')

const STATUS = {
  success: { dot: 'ok', text: '已分析' },
  failed: { dot: 'err', text: '分析失败' },
  running: { dot: 'warn', text: '分析中' },
  pending: { dot: 'idle', text: '待分析' },
}

const filtered = computed(() => {
  const kw = keyword.value.trim().toLowerCase()
  if (!kw) return store.projects
  return store.projects.filter((p) => (p.name || '').toLowerCase().includes(kw))
})

function statusDot(status) {
  return STATUS[status]?.dot || 'idle'
}

function statusLabel(status) {
  return STATUS[status]?.text || '未分析'
}

async function load() {
  loading.value = true
  try {
    await store.loadProjects()
    // 首次进入自动选中第一个项目，避免页面停在空态
    if (!store.currentProjectId && store.projects.length) {
      store.setProject(store.projects[0].id)
    }
  } catch {
    /* 请求层已提示 */
  } finally {
    loading.value = false
  }
}

function select(project) {
  if (project.id !== store.currentProjectId) store.setProject(project.id)
}

onMounted(load)
</script>

<style scoped>
.proj-aside {
  width: 200px;
  flex: none;
  display: flex;
  flex-direction: column;
  background: #fff;
  border: 1px solid var(--el-border-color-light);
  border-radius: 6px;
  /* 撑满可视区域，管理入口固定底部 */
  height: calc(100vh - 96px);
  min-height: 320px;
  position: sticky;
  top: 0;
  overflow: hidden;
}
.aside-head {
  display: flex;
  align-items: center;
  justify-content: space-between;
  padding: 10px 12px;
  border-bottom: 1px solid var(--el-border-color-lighter);
}
.aside-title {
  font-weight: 600;
  font-size: 13px;
}
.search {
  margin: 8px 10px 0;
}
.aside-body {
  flex: 1;
  padding: 6px;
  min-height: 120px;
}
.proj-item {
  display: flex;
  align-items: center;
  gap: 8px;
  padding: 8px 10px;
  border-radius: 6px;
  cursor: pointer;
  border: 1px solid transparent;
}
.proj-item:hover {
  background: var(--el-fill-color-light);
}
.proj-item.active {
  background: var(--el-color-primary-light-9);
  border-color: var(--el-color-primary-light-7);
}
.dot {
  width: 7px;
  height: 7px;
  border-radius: 50%;
  flex: none;
}
.dot.ok {
  background: #67c23a;
}
.dot.err {
  background: #f56c6c;
}
.dot.warn {
  background: #e6a23c;
}
.dot.idle {
  background: #c0c4cc;
}
.proj-text {
  flex: 1;
  min-width: 0;
}
.proj-name {
  font-size: 13px;
  font-weight: 500;
  overflow: hidden;
  text-overflow: ellipsis;
  white-space: nowrap;
}
.proj-meta {
  font-size: 11px;
  color: var(--el-text-color-secondary);
  margin-top: 2px;
}
.checked {
  color: var(--el-color-primary);
  font-size: 14px;
}
.no-match {
  padding: 16px;
  text-align: center;
  color: var(--el-text-color-secondary);
  font-size: 12px;
}
.aside-foot {
  border-top: 1px solid var(--el-border-color-lighter);
  padding: 6px 8px;
}
.manage {
  font-size: 12px;
}

@media (max-width: 900px) {
  .proj-aside {
    width: 100%;
    height: auto;
    max-height: 220px;
    min-height: 0;
    position: static;
  }
  .aside-body {
    min-height: 60px;
  }
}
</style>
