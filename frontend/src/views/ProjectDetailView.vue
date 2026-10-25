<template>
  <div class="page">
    <div v-if="loading && !project" v-loading="loading" class="loading-block" element-loading-text="加载项目信息..." />

    <el-result
      v-else-if="error"
      icon="warning"
      title="项目加载失败"
      :sub-title="error"
    >
      <template #extra>
        <el-button type="primary" @click="$router.push('/projects')">返回项目列表</el-button>
        <el-button @click="load">重试</el-button>
      </template>
    </el-result>

    <template v-else-if="project">
      <el-page-header @back="$router.push('/projects')">
        <template #content>
          <span class="title">{{ project.name }}</span>
        </template>
        <template #extra>
          <el-button :loading="loading" @click="load">
            <el-icon><Refresh /></el-icon>刷新
          </el-button>
        </template>
      </el-page-header>

      <el-tabs v-model="tab" class="tabs" @tab-change="onTabChange">
        <el-tab-pane label="概览" name="overview">
          <el-descriptions :column="2" border>
            <el-descriptions-item label="项目名称">{{ project.name }}</el-descriptions-item>
            <el-descriptions-item label="源码类型">
              {{ project.sourceType === 'git' ? 'Git 仓库' : '本地路径' }}
            </el-descriptions-item>
            <el-descriptions-item label="源码位置" :span="2">
              {{ project.sourceType === 'git' ? `${project.gitUrl} (${project.gitBranch || '默认分支'})` : project.sourcePath }}
            </el-descriptions-item>
            <el-descriptions-item label="技术栈" :span="2">
              <el-tag v-for="(v, k) in project.techStack" :key="k" size="small" class="tag">
                {{ k }}: {{ v }}
              </el-tag>
              <span v-if="!project.techStack" class="muted">未识别（代码分析后回填）</span>
            </el-descriptions-item>
            <el-descriptions-item label="被测地址">{{ project.targetUrl || '—' }}</el-descriptions-item>
            <el-descriptions-item label="分析状态">
              <el-tag v-if="project.analysisStatus" :type="tagType(project.analysisStatus)">
                {{ statusLabel(project.analysisStatus) }}
              </el-tag>
              <el-tag v-else type="info">未分析</el-tag>
            </el-descriptions-item>
            <el-descriptions-item label="创建时间">{{ formatTime(project.createdAt) }}</el-descriptions-item>
            <el-descriptions-item label="更新时间">{{ formatTime(project.updatedAt) }}</el-descriptions-item>
            <el-descriptions-item label="描述" :span="2">{{ project.description || '—' }}</el-descriptions-item>
          </el-descriptions>
        </el-tab-pane>

        <el-tab-pane label="代码分析" name="analysis" lazy>
          <AnalysisTab :project-id="project.id" @analyzed="onAnalyzed" />
        </el-tab-pane>

        <el-tab-pane label="需求版本" name="requirements" lazy>
          <RequirementsTab
            v-model="activeRequirementId"
            :project-id="project.id"
            @generate="onGenerate"
          />
        </el-tab-pane>

        <el-tab-pane label="用例生成" name="generate" lazy>
          <GenerationTab
            v-model:requirement-id="activeRequirementId"
            :project-id="project.id"
            :refresh-key="generationKey"
            @go-requirements="tab = 'requirements'"
            @generated="onGenerated"
          />
        </el-tab-pane>

        <el-tab-pane label="测试用例" name="testcases" lazy>
          <TestcasesView :project-id="project.id" :refresh-key="caseRefreshKey" />
        </el-tab-pane>

        <el-tab-pane label="覆盖矩阵" name="coverage" lazy>
          <CoverageTab
            v-model:requirement-id="activeRequirementId"
            :project-id="project.id"
            :refresh-key="coverageKey"
          />
        </el-tab-pane>

        <el-tab-pane label="脑图" name="mindmap" lazy>
          <MindmapTab
            v-model:requirement-id="activeRequirementId"
            :project-id="project.id"
            :refresh-key="mindmapKey"
          />
        </el-tab-pane>

        <el-tab-pane label="执行结果" name="executions" lazy>
          <ExecutionsTab :project-id="project.id" />
        </el-tab-pane>
      </el-tabs>
    </template>
  </div>
</template>

<script setup>
import { defineAsyncComponent, onMounted, ref, watch } from 'vue'
import { useRoute, useRouter } from 'vue-router'
import { getProject } from '../api/projects'
import { useConfigStore } from '../stores/config'
import AnalysisTab from '../components/AnalysisTab.vue'
import CoverageTab from '../components/CoverageTab.vue'
import ExecutionsTab from '../components/ExecutionsTab.vue'
import GenerationTab from '../components/GenerationTab.vue'
import RequirementsTab from '../components/RequirementsTab.vue'
import TestcasesView from './TestcasesView.vue'

// 依赖 echarts / 需求版本，体积较大：异步加载
const MindmapTab = defineAsyncComponent(() => import('../components/MindmapTab.vue'))

const route = useRoute()
const router = useRouter()
const store = useConfigStore()
const project = ref(null)
const TABS = ['overview', 'analysis', 'requirements', 'generate', 'testcases', 'coverage', 'mindmap', 'executions']
const tab = ref(TABS.includes(route.query.tab) ? route.query.tab : 'overview')
const loading = ref(false)
const error = ref('')
const activeRequirementId = ref(null)
const caseRefreshKey = ref(0)
const generationKey = ref(0)
const coverageKey = ref(0)
const mindmapKey = ref(0)

const STATUS_TEXT = { pending: '待分析', running: '分析中', success: '分析完成', failed: '分析失败' }

async function load() {
  loading.value = true
  error.value = ''
  try {
    const p = await getProject(route.params.id)
    project.value = p
    // 详情页以路由为准，同时同步顶部项目选择器
    store.setProject(p.id)
  } catch (e) {
    project.value = null
    error.value = e?.message || '项目不存在或已被删除'
  } finally {
    loading.value = false
  }
}

async function onAnalyzed() {
  // 分析完成后刷新概览：analysisStatus / techStack 已回填
  await load()
}

function onGenerate(requirementId) {
  activeRequirementId.value = requirementId
  tab.value = 'generate'
}

function onGenerated() {
  // 生成完成后让已挂载的用例列表和覆盖矩阵自动刷新
  caseRefreshKey.value += 1
  coverageKey.value += 1
}

function onTabChange(name) {
  // 标签页写入 URL，便于从执行历史等子页面返回时定位到原标签
  router.replace({ query: { ...route.query, tab: name } })
  // 标签页是懒加载且常驻内存：每次进入时刷新，避免看到旧数据
  if (name === 'generate') generationKey.value += 1
  else if (name === 'testcases') caseRefreshKey.value += 1
  else if (name === 'coverage') coverageKey.value += 1
  else if (name === 'mindmap') mindmapKey.value += 1
}

function tagType(status) {
  return { success: 'success', failed: 'danger', running: 'warning', pending: 'info' }[status] || 'info'
}

function statusLabel(status) {
  return STATUS_TEXT[status] || status
}

function formatTime(t) {
  return t ? new Date(t).toLocaleString() : '—'
}

onMounted(load)
watch(() => route.params.id, (id) => { if (id) load() })
watch(
  () => route.query.tab,
  (name) => {
    if (TABS.includes(name) && name !== tab.value) tab.value = name
  },
)
</script>

<style scoped>
.loading-block {
  height: 240px;
}
.title {
  font-weight: 600;
  font-size: 16px;
}
.tabs {
  margin-top: 16px;
}
.tag {
  margin-right: 6px;
}
.muted {
  color: var(--el-text-color-secondary);
  font-size: 13px;
}
</style>
