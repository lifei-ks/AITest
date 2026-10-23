import { createRouter, createWebHistory } from 'vue-router'

const routes = [
  { path: '/', redirect: '/dashboard' },
  {
    path: '/dashboard',
    name: 'Dashboard',
    component: () => import('../views/DashboardView.vue'),
    meta: { title: '总览看板' },
  },
  {
    path: '/projects',
    name: 'Projects',
    component: () => import('../views/ProjectsView.vue'),
    meta: { title: '项目列表' },
  },
  {
    path: '/projects/:id/testcases/:caseId/executions',
    name: 'CaseHistory',
    component: () => import('../views/CaseHistoryView.vue'),
    meta: { title: '执行历史' },
  },
  {
    path: '/projects/:id',
    name: 'ProjectDetail',
    component: () => import('../views/ProjectDetailView.vue'),
    meta: { title: '项目详情' },
  },
  {
    path: '/testcases',
    name: 'Testcases',
    component: () => import('../views/TestcasesView.vue'),
    meta: { title: '用例列表' },
  },
  {
    path: '/executions',
    name: 'Executions',
    component: () => import('../views/ExecutionsView.vue'),
    meta: { title: '执行结果' },
  },
  {
    path: '/settings',
    name: 'Settings',
    component: () => import('../views/SettingsView.vue'),
    meta: { title: '设置' },
  },
  {
    path: '/:pathMatch(.*)*',
    name: 'NotFound',
    component: () => import('../views/NotFoundView.vue'),
    meta: { title: '页面不存在' },
  },
]

const router = createRouter({
  history: createWebHistory(),
  routes,
  scrollBehavior: () => ({ top: 0 }),
})

router.afterEach((to) => {
  const title = to.meta?.title
  document.title = title ? `${title} · AICase Core` : 'AICase Core'
})

export default router
