<template>
  <el-result icon="warning" title="页面不存在" sub-title="链接可能已失效，或该资源已被删除">
    <template #extra>
      <el-button type="primary" @click="$router.push('/dashboard')">返回总览看板</el-button>
      <el-button @click="$router.back()">返回上一页</el-button>
    </template>
  </el-result>
</template>
