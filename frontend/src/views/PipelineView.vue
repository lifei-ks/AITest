<template>
  <div class="page-layout">
    <ProjectSidebar />

    <div class="page-content">
      <div class="pipeline-head">
        <div>
          <h2>Pipeline 执行</h2>
          <p>一键从代码分析到执行结果；也可以在这里快速创建项目并上传需求文档。</p>
        </div>
        <el-button type="primary" @click="quickVisible = true">
          <el-icon><Plus /></el-icon>快速创建项目 + 需求
        </el-button>
      </div>

      <el-empty v-if="!projectId" description="请在左侧选择项目，或快速创建一个新项目">
        <el-button type="primary" @click="quickVisible = true">快速创建项目</el-button>
        <el-button @click="$router.push('/projects')">去项目列表</el-button>
      </el-empty>

      <WorkflowTab
        v-else
        :key="projectId"
        :project-id="projectId"
        :requirement-id="preferredRequirementId"
        :target-url-default="projectTargetUrl"
        pipeline-mode
      />
    </div>

    <QuickStartDialog v-model="quickVisible" @created="onCreated" />
  </div>
</template>

<script setup>
import { computed, ref } from 'vue'
import ProjectSidebar from '../components/ProjectSidebar.vue'
import QuickStartDialog from '../components/QuickStartDialog.vue'
import WorkflowTab from '../components/WorkflowTab.vue'
import { useConfigStore } from '../stores/config'

const store = useConfigStore()
const projectId = computed(() => store.currentProjectId)
const projectTargetUrl = computed(
  () => store.projects.find((project) => project.id === projectId.value)?.targetUrl || '',
)
const quickVisible = ref(false)
const preferredRequirementId = ref(null)

function onCreated({ requirementId }) {
  preferredRequirementId.value = requirementId
}
</script>

<style scoped>
.page-layout {
  display: flex;
  gap: 16px;
  align-items: flex-start;
}
.page-content {
  flex: 1;
  min-width: 0;
}
.pipeline-head {
  display: flex;
  align-items: center;
  justify-content: space-between;
  gap: 16px;
  margin-bottom: 14px;
}
.pipeline-head h2 {
  margin: 0;
  font-size: 20px;
}
.pipeline-head p {
  margin: 6px 0 0;
  color: var(--el-text-color-secondary);
  font-size: 13px;
}

@media (max-width: 900px) {
  .page-layout {
    flex-direction: column;
  }
  .page-content {
    width: 100%;
  }
  .pipeline-head {
    align-items: flex-start;
    flex-direction: column;
  }
}
</style>
