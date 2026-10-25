<template>
  <div class="page" v-loading="loading">
    <div class="page-head">
      <div>
        <h2>订单统计</h2>
        <p>统计当前全部订单的数量、金额与状态分布。</p>
      </div>
      <el-button type="primary" plain @click="load">刷新统计</el-button>
    </div>

    <el-row :gutter="16" class="stats">
      <el-col :xs="24" :sm="8">
        <el-card shadow="never">
          <div class="metric-label">订单总数</div>
          <div class="metric-value">{{ data.totalOrders || 0 }}</div>
          <div class="metric-hint">当前内存中的全部订单</div>
        </el-card>
      </el-col>
      <el-col :xs="24" :sm="8">
        <el-card shadow="never">
          <div class="metric-label">订单总额</div>
          <div class="metric-value">¥{{ formatAmount(data.totalAmount) }}</div>
          <div class="metric-hint">包含已取消与已退款订单</div>
        </el-card>
      </el-col>
      <el-col :xs="24" :sm="8">
        <el-card shadow="never">
          <div class="metric-label">平均金额</div>
          <div class="metric-value">¥{{ formatAmount(data.averageAmount) }}</div>
          <div class="metric-hint">订单总额 / 订单总数</div>
        </el-card>
      </el-col>
    </el-row>

    <el-card shadow="never" class="section">
      <template #header>
        <span>状态分布</span>
      </template>
      <el-table :data="statusRows" border stripe>
        <el-table-column prop="label" label="订单状态" min-width="160" />
        <el-table-column prop="count" label="数量" width="120" align="center" />
        <el-table-column label="占比" min-width="260">
          <template #default="{ row }">
            <el-progress
              :percentage="row.percent"
              :stroke-width="8"
              :color="row.percent >= 50 ? '#409eff' : '#67c23a'"
            />
          </template>
        </el-table-column>
      </el-table>
    </el-card>

    <el-card shadow="never" class="section">
      <div class="valid-line">
        <span>有效订单金额（不含已取消、已退款）</span>
        <strong>¥{{ formatAmount(data.validAmount) }}</strong>
      </div>
    </el-card>
  </div>
</template>

<script setup>
import { computed, onMounted, ref } from 'vue'
import { fetchStatistics } from '../api/order'

const loading = ref(false)
const data = ref({})

const STATUS_ORDER = ['CREATED', 'PAID', 'SHIPPED', 'COMPLETED', 'CANCELLED', 'REFUNDED']

const statusRows = computed(() => {
  const counts = data.value.statusCounts || {}
  const labels = data.value.statusLabels || {}
  const total = Number(data.value.totalOrders || 0)
  return STATUS_ORDER.map((status) => {
    const count = Number(counts[status] || 0)
    return {
      status,
      label: labels[status] || status,
      count,
      percent: total ? Math.round((count / total) * 1000) / 10 : 0
    }
  })
})

const formatAmount = (value) => Number(value || 0).toFixed(2)

const load = async () => {
  loading.value = true
  try {
    const res = await fetchStatistics()
    data.value = res.data || {}
  } finally {
    loading.value = false
  }
}

onMounted(load)
</script>

<style scoped>
.page {
  display: flex;
  flex-direction: column;
  gap: 16px;
}
.page-head {
  display: flex;
  align-items: flex-start;
  justify-content: space-between;
  gap: 16px;
  flex-wrap: wrap;
}
.page-head h2 {
  margin: 0 0 6px;
  font-size: 20px;
  color: #303133;
}
.page-head p {
  margin: 0;
  color: #909399;
  font-size: 13px;
}
.stats :deep(.el-card__body) {
  padding: 18px 20px;
}
.metric-label {
  color: #909399;
  font-size: 13px;
}
.metric-value {
  margin: 8px 0 6px;
  color: #303133;
  font-size: 28px;
  font-weight: 600;
}
.metric-hint {
  color: #c0c4cc;
  font-size: 12px;
}
.section {
  margin-top: 0;
}
.valid-line {
  display: flex;
  align-items: center;
  justify-content: space-between;
  color: #606266;
}
.valid-line strong {
  color: #67c23a;
  font-size: 20px;
}
</style>
