<template>
  <el-container class="layout">
    <!-- 侧边栏（可折叠，小屏自动收起） -->
    <el-aside :width="collapsed ? '64px' : '200px'" class="aside">
      <div class="logo" :class="{ mini: collapsed }" :title="collapsed ? 'AICase Core' : ''">
        {{ collapsed ? 'AC' : 'AICase Core' }}
      </div>
      <el-menu
        :default-active="activeMenu"
        :collapse="collapsed"
        :collapse-transition="false"
        router
        class="menu"
        background-color="#001529"
        text-color="#c0c4cc"
        active-text-color="#ffffff"
      >
        <el-menu-item v-for="m in MENUS" :key="m.path" :index="m.path">
          <el-icon><component :is="m.icon" /></el-icon>
          <template #title>{{ m.title }}</template>
        </el-menu-item>
      </el-menu>
    </el-aside>

    <el-container class="body">
      <!-- 顶栏：页面标题 + 在线状态 -->
      <el-header class="header">
        <div class="header-left">
          <el-button text class="collapse-btn" :title="collapsed ? '展开菜单' : '收起菜单'" @click="toggleCollapse">
            <el-icon :size="18"><Expand v-if="collapsed" /><Fold v-else /></el-icon>
          </el-button>
          <span class="page-title">{{ pageTitle }}</span>
        </div>

        <div class="header-right">
          <el-tooltip :content="`最近检查：${lastCheckAt || '—'}`" placement="bottom">
            <el-tag :type="store.backendOnline ? 'success' : 'danger'" effect="plain" size="small" class="status">
              <span class="dot" :class="store.backendOnline ? 'on' : 'off'" />
              {{ store.backendOnline ? '后端在线' : '后端离线' }}
            </el-tag>
          </el-tooltip>
        </div>
      </el-header>

      <el-main class="main">
        <router-view />
      </el-main>
    </el-container>
  </el-container>
</template>

<script setup>
import { computed, onBeforeUnmount, onMounted, ref } from 'vue'
import { useRoute } from 'vue-router'
import request from './api/request'
import { useConfigStore } from './stores/config'

const MENUS = [
  { path: '/dashboard', title: '总览看板', icon: 'Odometer' },
  { path: '/projects', title: '项目列表', icon: 'Folder' },
  { path: '/pipeline', title: 'Pipeline 执行', icon: 'Promotion' },
  { path: '/testcases', title: '用例列表', icon: 'Document' },
  { path: '/executions', title: '执行结果', icon: 'VideoPlay' },
  { path: '/settings', title: '设置', icon: 'Setting' },
]

const store = useConfigStore()
const route = useRoute()

const collapsed = ref(false)
const lastCheckAt = ref('')
let healthTimer = null
let userToggled = false

// 详情类路由（/projects/:id）高亮父级菜单
const activeMenu = computed(() => (route.path.startsWith('/projects') ? '/projects' : route.path))
const pageTitle = computed(() => route.meta?.title || MENUS.find((m) => m.path === activeMenu.value)?.title || '')

function toggleCollapse() {
  userToggled = true
  collapsed.value = !collapsed.value
}

function syncCollapse() {
  if (userToggled) return
  collapsed.value = window.innerWidth < 992
}

async function checkHealth() {
  try {
    const data = await request.get('/health', { timeout: 5000, silent: true })
    store.backendOnline = data?.status === 'up'
  } catch {
    store.backendOnline = false
  } finally {
    lastCheckAt.value = new Date().toLocaleTimeString()
  }
}

onMounted(() => {
  syncCollapse()
  window.addEventListener('resize', syncCollapse)
  checkHealth()
  healthTimer = setInterval(checkHealth, 30000)
})

onBeforeUnmount(() => {
  window.removeEventListener('resize', syncCollapse)
  if (healthTimer) clearInterval(healthTimer)
})
</script>

<style>
html,
body,
#app {
  height: 100%;
  margin: 0;
}

* {
  box-sizing: border-box;
}

body {
  font-family: -apple-system, BlinkMacSystemFont, 'Segoe UI', 'Microsoft YaHei', sans-serif;
  background: #f0f2f5;
  color: var(--el-text-color-primary);
}
</style>

<style scoped>
.layout {
  min-height: 100vh;
}
.aside {
  background: #001529;
  transition: width 0.2s ease;
  overflow-x: hidden;
}
.logo {
  color: #fff;
  font-weight: 700;
  font-size: 17px;
  height: 56px;
  line-height: 56px;
  padding: 0 20px;
  white-space: nowrap;
  overflow: hidden;
}
.logo.mini {
  padding: 0;
  text-align: center;
}
.menu {
  border-right: none;
}
.body {
  min-width: 0;
}
.header {
  height: 56px;
  display: flex;
  align-items: center;
  justify-content: space-between;
  gap: 12px;
  border-bottom: 1px solid var(--el-border-color-light);
  background: #fff;
  padding: 0 16px;
}
.header-left {
  display: flex;
  align-items: center;
  gap: 8px;
  min-width: 0;
}
.collapse-btn {
  padding: 6px;
  color: var(--el-text-color-regular);
}
.page-title {
  font-weight: 600;
  font-size: 15px;
  white-space: nowrap;
  overflow: hidden;
  text-overflow: ellipsis;
}
.header-right {
  display: flex;
  align-items: center;
  gap: 10px;
}

.status {
  display: inline-flex;
  align-items: center;
  gap: 6px;
}
.dot {
  width: 6px;
  height: 6px;
  border-radius: 50%;
  display: inline-block;
}
.dot.on {
  background: #67c23a;
}
.dot.off {
  background: #f56c6c;
}
.main {
  padding: 20px;
  background: #f0f2f5;
  overflow-x: hidden;
}

@media (max-width: 768px) {
  .header {
    padding: 0 8px;
  }
  .page-title {
    display: none;
  }
  .main {
    padding: 12px;
  }
}
</style>
