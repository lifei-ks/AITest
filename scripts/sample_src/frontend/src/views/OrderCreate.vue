<template>
  <el-form :model="form" :rules="rules">
    <el-form-item label="收货人" prop="receiverName">
      <el-input v-model="form.receiverName" placeholder="请输入收货人" />
    </el-form-item>
    <el-form-item label="手机号" prop="phone">
      <el-input v-model="form.phone" placeholder="请输入手机号" />
    </el-form-item>
    <el-form-item label="收货地址" prop="address">
      <el-input v-model="form.address" placeholder="请输入收货地址" />
    </el-form-item>
    <el-form-item label="金额" prop="amount">
      <el-input v-model="form.amount" placeholder="请输入订单金额" />
    </el-form-item>
    <el-form-item label="备注" prop="remark">
      <el-input v-model="form.remark" placeholder="请输入备注" />
    </el-form-item>
    <el-button type="primary" @click="submit">提交订单</el-button>
  </el-form>
</template>

<script setup>
import { ElMessage } from 'element-plus'

const submit = () => {
  ElMessage.success('订单创建成功')
}
</script>
