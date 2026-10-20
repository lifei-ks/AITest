<template>
  <el-table :data="orders">
    <el-table-column prop="orderNo" label="订单号" />
    <el-table-column prop="status" label="状态" />
  </el-table>
</template>

<script setup>
import { ElMessage } from 'element-plus'

const pay = () => {
  ElMessage.success('支付成功')
}
</script>
