<template>
  <div class="page">
    <el-card shadow="never">
      <template #header>
        <span>新建订单</span>
      </template>

      <el-form
        ref="formRef"
        :model="form"
        :rules="rules"
        label-width="100px"
        style="max-width: 620px"
      >
        <el-form-item label="收货人" prop="receiverName">
          <el-input v-model="form.receiverName" placeholder="请输入收货人" clearable />
        </el-form-item>

        <el-form-item label="手机号" prop="phone">
          <el-input v-model="form.phone" placeholder="请输入手机号" clearable />
        </el-form-item>

        <el-form-item label="收货地址" prop="address">
          <el-input v-model="form.address" placeholder="请输入收货地址" clearable />
        </el-form-item>

        <el-form-item label="订单金额" prop="amount">
          <el-input v-model="form.amount" placeholder="请输入订单金额" clearable />
        </el-form-item>

        <el-form-item label="备注" prop="remark">
          <el-input
            v-model="form.remark"
            type="textarea"
            :rows="3"
            placeholder="请输入备注（选填）"
          />
        </el-form-item>

        <el-form-item>
          <el-button type="primary" :loading="submitting" @click="handleSubmit">提交订单</el-button>
          <el-button @click="goBack">返回列表</el-button>
        </el-form-item>
      </el-form>
    </el-card>
  </div>
</template>

<script setup>
import { reactive, ref } from 'vue'
import { useRouter } from 'vue-router'
import { ElMessage } from 'element-plus'
import { createOrder } from '../api/order'

const router = useRouter()
const formRef = ref(null)
const submitting = ref(false)

const form = reactive({
  receiverName: '',
  phone: '',
  address: '',
  amount: '',
  remark: ''
})

const rules = {
  receiverName: [
    { required: true, message: '收货人不能为空', trigger: 'blur' },
    { max: 20, message: '收货人不能超过20个字符', trigger: 'blur' }
  ],
  phone: [
    { required: true, message: '手机号不能为空', trigger: 'blur' },
    { pattern: /^1[3-9]\d{9}$/, message: '手机号格式不正确', trigger: 'blur' }
  ],
  address: [
    { required: true, message: '收货地址不能为空', trigger: 'blur' },
    { max: 100, message: '收货地址不能超过100个字符', trigger: 'blur' }
  ],
  amount: [
    { required: true, message: '订单金额不能为空', trigger: 'blur' }
  ],
  remark: [
    { max: 200, message: '备注不能超过200个字符', trigger: 'blur' }
  ]
}

const handleSubmit = async () => {
  try {
    await formRef.value.validate()
  } catch (e) {
    return
  }
  submitting.value = true
  try {
    await createOrder(form)
    ElMessage.success('订单创建成功')
    router.push('/orders/list')
  } catch (e) {
    // 错误提示已在响应拦截器统一处理
  } finally {
    submitting.value = false
  }
}

const goBack = () => router.push('/orders/list')
</script>

<style scoped>
.page {
  max-width: 900px;
}
</style>
