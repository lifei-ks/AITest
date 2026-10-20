import { createRouter, createWebHistory } from 'vue-router'
import OrderList from '../views/OrderList.vue'
import OrderCreate from '../views/OrderCreate.vue'

const routes = [
  { path: '/', redirect: '/orders/list' },
  { path: '/orders/list', name: 'OrderList', component: OrderList, meta: { title: '订单列表' } },
  { path: '/orders/create', name: 'OrderCreate', component: OrderCreate, meta: { title: '新建订单' } }
]

const router = createRouter({
  history: createWebHistory(),
  routes
})

router.afterEach((to) => {
  document.title = to.meta.title ? `${to.meta.title} - 订单管理系统` : '订单管理系统'
})

export default router
