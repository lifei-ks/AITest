<template>
  <div class="page">
    <el-card shadow="never" class="filter-card">
      <el-form :inline="true" :model="query" @submit.prevent>
        <el-form-item label="关键字">
          <el-input
            v-model="query.keyword"
            placeholder="请输入订单号或收货人"
            clearable
            style="width: 220px"
          />
        </el-form-item>
        <el-form-item label="订单状态">
          <el-select
            v-model="query.status"
            placeholder="请选择订单状态"
            clearable
            style="width: 180px"
          >
            <el-option
              v-for="item in statusOptions"
              :key="item.value"
              :label="item.label"
              :value="item.value"
            />
          </el-select>
        </el-form-item>
        <el-form-item>
          <el-button type="primary" @click="handleSearch">查询</el-button>
          <el-button @click="handleReset">重置</el-button>
        </el-form-item>
      </el-form>
    </el-card>

    <el-card shadow="never">
      <div class="toolbar">
        <span class="total">共 {{ total }} 条订单</span>
        <el-button type="primary" @click="goCreate">新建订单</el-button>
      </div>

      <el-table :data="orders" v-loading="loading" border stripe style="width: 100%">
        <el-table-column prop="orderNo" label="订单号" min-width="170" />
        <el-table-column prop="receiverName" label="收货人" width="110" />
        <el-table-column prop="phone" label="手机号" width="130" />
        <el-table-column prop="address" label="收货地址" min-width="220" show-overflow-tooltip />
        <el-table-column prop="amount" label="金额" width="110">
          <template #default="{ row }">¥{{ row.amount }}</template>
        </el-table-column>
        <el-table-column label="状态" width="100">
          <template #default="{ row }">
            <el-tag :type="tagType(row.status)">{{ row.statusLabel }}</el-tag>
          </template>
        </el-table-column>
        <el-table-column label="创建时间" width="170">
          <template #default="{ row }">{{ formatTime(row.createTime) }}</template>
        </el-table-column>
        <el-table-column label="操作" width="220" fixed="right">
          <template #default="{ row }">
            <el-button
              v-if="row.status === 'CREATED'"
              type="primary"
              link
              @click="handleAction(row, 'pay')"
            >支付</el-button>
            <el-button
              v-if="row.status === 'PAID'"
              type="primary"
              link
              @click="handleAction(row, 'ship')"
            >发货</el-button>
            <el-button
              v-if="row.status === 'SHIPPED'"
              type="success"
              link
              @click="handleAction(row, 'complete')"
            >完成</el-button>
            <el-button
              v-if="row.status === 'CREATED'"
              type="danger"
              link
              @click="handleAction(row, 'cancel')"
            >取消</el-button>
            <el-button
              v-if="row.status === 'PAID'"
              type="warning"
              link
              @click="handleAction(row, 'refund')"
            >退款</el-button>
            <span v-if="['COMPLETED', 'CANCELLED', 'REFUNDED'].includes(row.status)" class="noop">—</span>
          </template>
        </el-table-column>
      </el-table>

      <div class="pager">
        <el-pagination
          v-model:current-page="query.page"
          v-model:page-size="query.pageSize"
          :total="total"
          :page-sizes="[10, 20, 50]"
          layout="total, sizes, prev, pager, next"
          @current-change="loadOrders"
          @size-change="loadOrders"
        />
      </div>
    </el-card>
  </div>
</template>

<script setup>
import { onMounted, reactive, ref } from 'vue'
import { useRouter } from 'vue-router'
import { ElMessage } from 'element-plus'
import {
  cancelOrder,
  completeOrder,
  fetchOrders,
  fetchStatusOptions,
  payOrder,
  refundOrder,
  shipOrder
} from '../api/order'

const router = useRouter()

const loading = ref(false)
const orders = ref([])
const total = ref(0)
const statusOptions = ref([])

const query = reactive({
  page: 1,
  pageSize: 10,
  keyword: '',
  status: ''
})

const tagType = (status) => {
  const map = {
    CREATED: 'warning',
    PAID: 'primary',
    SHIPPED: 'info',
    COMPLETED: 'success',
    CANCELLED: 'danger',
    REFUNDED: 'danger'
  }
  return map[status] || 'info'
}

const formatTime = (value) => (value ? String(value).replace('T', ' ').slice(0, 19) : '')

const loadOrders = async () => {
  loading.value = true
  try {
    const res = await fetchOrders({
      page: query.page,
      pageSize: query.pageSize,
      keyword: query.keyword || undefined,
      status: query.status || undefined
    })
    orders.value = res.data.items || []
    total.value = res.data.total || 0
  } finally {
    loading.value = false
  }
}

const handleSearch = () => {
  query.page = 1
  loadOrders()
}

const handleReset = () => {
  query.page = 1
  query.keyword = ''
  query.status = ''
  loadOrders()
}

const goCreate = () => router.push('/orders/create')

const handleAction = async (row, type) => {
  try {
    if (type === 'pay') {
      await payOrder(row.id)
      ElMessage.success('支付成功')
    } else if (type === 'ship') {
      await shipOrder(row.id)
      ElMessage.success('发货成功')
    } else if (type === 'complete') {
      await completeOrder(row.id)
      ElMessage.success('订单已完成')
    } else if (type === 'cancel') {
      await cancelOrder(row.id)
      ElMessage.success('订单已取消')
    } else if (type === 'refund') {
      await refundOrder(row.id)
      ElMessage.success('退款成功')
    }
    await loadOrders()
  } catch (e) {
    // 错误提示已在响应拦截器统一处理
  }
}

onMounted(async () => {
  const res = await fetchStatusOptions()
  statusOptions.value = res.data || []
  await loadOrders()
})
</script>

<style scoped>
.page {
  display: flex;
  flex-direction: column;
  gap: 16px;
}
.filter-card :deep(.el-form-item) {
  margin-bottom: 0;
}
.toolbar {
  display: flex;
  align-items: center;
  justify-content: space-between;
  margin-bottom: 16px;
}
.total {
  color: #606266;
  font-size: 14px;
}
.pager {
  display: flex;
  justify-content: flex-end;
  margin-top: 16px;
}
.noop {
  color: #c0c4cc;
}
</style>
