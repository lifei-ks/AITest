<template>
  <div class="page-layout">
    <ProjectSidebar />

    <div class="page-content">
      <el-empty v-if="!projectId" description="请在左侧选择项目，或到「项目列表」进入项目">
        <el-button type="primary" @click="$router.push('/projects')">去项目列表</el-button>
      </el-empty>

      <ExecutionsTab v-else :project-id="projectId" />
    </div>
  </div>
</template>

<script setup>
import { computed } from 'vue'
import ProjectSidebar from '../components/ProjectSidebar.vue'
import ExecutionsTab from '../components/ExecutionsTab.vue'
import { useConfigStore } from '../stores/config'

const store = useConfigStore()
const projectId = computed(() => store.currentProjectId)
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

@media (max-width: 900px) {
  .page-layout {
    flex-direction: column;
  }
  .page-content {
    width: 100%;
  }
}
</style>
