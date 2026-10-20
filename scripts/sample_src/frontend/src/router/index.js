import { createRouter, createWebHistory } from 'vue-router'

const routes = [
  {
    path: '/orders/create',
    component: () => import('../views/OrderCreate.vue'),
    meta: { title: '创建订单' },
  },
  {
    path: '/orders/list',
    component: () => import('../views/OrderList.vue'),
    meta: { title: '订单列表' },
  },
]

export default createRouter({
  history: createWebHistory(),
  routes,
})
