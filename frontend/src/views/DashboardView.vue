<template>
  <div v-loading="loading" class="dashboard">
    <!-- 统计卡 -->
    <el-row :gutter="16">
      <el-col v-for="c in cards" :key="c.label" :xs="12" :sm="12" :md="6" :lg="6">
        <el-card shadow="hover" class="stat-card">
          <div class="stat-icon" :style="{ background: c.bg, color: c.color }">
            <el-icon><component :is="c.icon" /></el-icon>
          </div>
          <div class="stat-body">
            <div class="stat-num">{{ c.value }}</div>
            <div class="stat-label">{{ c.label }}</div>
          </div>
        </el-card>
      </el-col>
    </el-row>

    <!-- 项目列表概览 -->
    <el-card shadow="never" class="proj-card">
      <template #header>
        <div class="card-head">
          <span>项目概览</span>
          <div class="head-actions">
            <el-button size="small" :loading="loading" @click="load">刷新</el-button>
            <el-button type="primary" size="small" @click="$router.push('/projects')">新建项目</el-button>
          </div>
        </div>
      </template>

      <el-table :data="projects" border stripe :empty-text="loading ? '加载中...' : '还没有项目，先新建一个'">
        <el-table-column label="项目名称" min-width="180" show-overflow-tooltip>
          <template #default="{ row }">
            <el-button link type="primary" @click="gotoProject(row)">{{ row.name }}</el-button>
          </template>
        </el-table-column>
        <el-table-column label="源码类型" width="120">
          <template #default="{ row }">{{ row.sourceType === 'git' ? 'Git 仓库' : '本地路径' }}</template>
        </el-table-column>
        <el-table-column label="技术栈" min-width="200">
          <template #default="{ row }">
            <template v-if="row.techStack && Object.keys(row.techStack).length">
              <el-tag v-for="(v, k) in row.techStack" :key="k" size="small" class="tag" type="info">{{ k }}: {{ v }}</el-tag>
            </template>
            <span v-else class="muted">—</span>
          </template>
        </el-table-column>
        <el-table-column label="分析状态" width="110">
          <template #default="{ row }">
            <el-tag v-if="row.analysisStatus" size="small" :type="tagType(row.analysisStatus)">
              {{ statusLabel(row.analysisStatus) }}
            </el-tag>
            <el-tag v-else size="small" type="info">未分析</el-tag>
          </template>
        </el-table-column>
        <el-table-column label="操作" width="120" fixed="right">
          <template #default="{ row }">
            <el-button link type="primary" @click="gotoProject(row)">进入项目</el-button>
          </template>
        </el-table-column>
      </el-table>
    </el-card>
  </div>
</template>

<script setup>
import { computed, onMounted, ref } from 'vue'
import { useRouter } from 'vue-router'
import { getOverview } from '../api/stats'
import { listProjects } from '../api/projects'
import { useConfigStore } from '../stores/config'

const router = useRouter()
const store = useConfigStore()
const loading = ref(false)
const overview = ref({ projects: 0, testCases: 0, executions: 0, passRate: 0 })
const projects = ref([])

const STATUS_TEXT = { pending: '待分析', running: '分析中', success: '分析完成', failed: '分析失败' }

const cards = computed(() => [
  { label: '项目总数', value: overview.value.projects ?? 0, icon: 'Folder', bg: '#ecf5ff', color: '#409eff' },
  { label: '用例总数', value: overview.value.testCases ?? 0, icon: 'Document', bg: '#f0f9eb', color: '#67c23a' },
  { label: '执行总数', value: overview.value.executions ?? 0, icon: 'VideoPlay', bg: '#fdf6ec', color: '#e6a23c' },
  { label: '执行通过率', value: `${Math.round((overview.value.passRate || 0) * 100)}%`, icon: 'TrendCharts', bg: '#fef0f0', color: '#f56c6c' },
])

async function load() {
  loading.value = true
  try {
    const [o, p] = await Promise.all([getOverview(), listProjects()])
    overview.value = o || overview.value
    projects.value = p || []
  } finally {
    loading.value = false
  }
}

function gotoProject(row) {
  store.setProject(row.id)
  router.push(`/projects/${row.id}`)
}

function tagType(s) {
  return { success: 'success', failed: 'danger', running: 'warning', pending: 'info' }[s] || 'info'
}

function statusLabel(s) {
  return STATUS_TEXT[s] || s
}

onMounted(load)
</script>

<style scoped>
.dashboard {
  min-height: 200px;
}
.stat-card {
  margin-bottom: 16px;
}
.stat-card :deep(.el-card__body) {
  display: flex;
  align-items: center;
  gap: 14px;
}
.stat-icon {
  width: 48px;
  height: 48px;
  flex: none;
  border-radius: 8px;
  display: flex;
  align-items: center;
  justify-content: center;
  font-size: 24px;
}
.stat-body {
  min-width: 0;
}
.stat-num {
  font-size: 26px;
  font-weight: 700;
  line-height: 1.2;
}
.stat-label {
  color: var(--el-text-color-secondary);
  font-size: 13px;
  white-space: nowrap;
}
.card-head {
  display: flex;
  justify-content: space-between;
  align-items: center;
  gap: 8px;
}
.head-actions {
  display: flex;
  gap: 8px;
}
.tag {
  margin: 2px 4px 2px 0;
}
.muted {
  color: var(--el-text-color-secondary);
}

@media (max-width: 768px) {
  .stat-card :deep(.el-card__body) {
    gap: 10px;
    padding: 12px;
  }
  .stat-num {
    font-size: 20px;
  }
  .stat-label {
    font-size: 12px;
  }
  .stat-icon {
    width: 36px;
    height: 36px;
    font-size: 18px;
  }
}
</style>
