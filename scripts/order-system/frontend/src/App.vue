<template>
  <el-container class="layout">
    <el-header class="header">
      <div class="brand">订单管理系统</div>
      <el-menu
        :default-active="activeMenu"
        mode="horizontal"
        router
        class="nav"
        :ellipsis="false"
      >
        <el-menu-item index="/orders/list">订单列表</el-menu-item>
        <el-menu-item index="/orders/create">新建订单</el-menu-item>
      </el-menu>
    </el-header>
    <el-main class="main">
      <router-view />
    </el-main>
  </el-container>
</template>

<script setup>
import { computed } from 'vue'
import { useRoute } from 'vue-router'

const route = useRoute()
const activeMenu = computed(() => route.path)
</script>

<style>
body {
  margin: 0;
  background: #f5f7fa;
  font-family: -apple-system, BlinkMacSystemFont, 'Segoe UI', 'PingFang SC', 'Microsoft YaHei', sans-serif;
}
.layout {
  min-height: 100vh;
}
.header {
  display: flex;
  align-items: center;
  gap: 32px;
  background: #fff;
  border-bottom: 1px solid #e4e7ed;
  padding: 0 32px;
}
.brand {
  font-size: 18px;
  font-weight: 600;
  color: #303133;
  white-space: nowrap;
}
.nav {
  border-bottom: none;
}
.main {
  padding: 24px 32px;
}
</style>
