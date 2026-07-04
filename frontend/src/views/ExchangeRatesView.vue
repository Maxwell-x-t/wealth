<script setup>
import { onMounted, reactive, ref } from 'vue'
import {
  NButton,
  NDataTable,
  NDatePicker,
  NForm,
  NFormItem,
  NInputNumber,
  NModal,
  NSpace,
  NSpin,
  useMessage,
} from 'naive-ui'
import {
  createExchangeRate,
  getExchangeRateHistory,
  getLatestExchangeRate,
  refreshExchangeRate,
} from '../api/client'
import { formatNumber } from '../utils/format'

const message = useMessage()
const loading = ref(true)
const refreshing = ref(false)
const showModal = ref(false)
const latest = ref(null)
const history = ref([])
const lastSource = ref(null)

const form = reactive({
  rate: null,
  snapshot_date: Date.now(),
})

async function loadData() {
  loading.value = true
  try {
    const [latestRate, rows] = await Promise.all([
      getLatestExchangeRate(),
      getExchangeRateHistory(),
    ])
    latest.value = latestRate
    history.value = rows
    form.rate = latestRate.rate
  } finally {
    loading.value = false
  }
}

onMounted(loadData)

function openCreate() {
  form.rate = latest.value?.rate ?? 7.2
  form.snapshot_date = Date.now()
  showModal.value = true
}

async function submitForm() {
  try {
    await createExchangeRate({
      rate: form.rate,
      snapshot_date: new Date(form.snapshot_date).toISOString().slice(0, 10),
    })
    message.success('汇率已更新')
    showModal.value = false
    lastSource.value = '手动录入'
    await loadData()
  } catch (error) {
    message.error(error.response?.data?.detail || '更新失败')
  }
}

async function handleRefresh() {
  refreshing.value = true
  try {
    const result = await refreshExchangeRate()
    lastSource.value = result.source || '自动拉取'
    await loadData()
    message.success(`已拉取汇率 ${formatNumber(result.rate, 4)}（${lastSource.value}）`)
  } catch (error) {
    message.error(error.response?.data?.detail || '拉取失败')
  } finally {
    refreshing.value = false
  }
}

const columns = [
  { title: '日期', key: 'snapshot_date', width: 120 },
  { title: '货币对', key: 'pair', width: 100 },
  { title: '汇率', key: 'rate', render: (row) => formatNumber(row.rate, 4) },
]
</script>

<template>
  <NSpin :show="loading || refreshing">
    <div class="header-row">
      <div>
        <h1 class="page-title">汇率更新</h1>
        <p class="page-desc">
          用于美元持仓市值折算人民币；优先新浪在岸，失败回退离岸。交易当日汇率请在录入交易时填写。
        </p>
      </div>
      <NSpace>
        <NButton type="primary" :loading="refreshing" @click="handleRefresh">一键拉取汇率</NButton>
        <NButton @click="openCreate">手动更新</NButton>
      </NSpace>
    </div>

    <div v-if="latest" class="metric-grid" style="margin-bottom: 16px">
      <div class="metric-card">
        <div class="metric-label">当前美元汇率</div>
        <div class="metric-value">{{ formatNumber(latest.rate, 4) }}</div>
        <div class="metric-sub">
          更新日期 {{ latest.snapshot_date }}
          <span v-if="lastSource"> · {{ lastSource }}</span>
        </div>
      </div>
    </div>

    <div class="panel">
      <h3 style="margin-top: 0">历史记录</h3>
      <NDataTable :columns="columns" :data="history" :bordered="false" size="small" />
    </div>

    <NModal v-model:show="showModal" preset="card" title="手动更新美元汇率" style="width: 480px">
      <NForm label-placement="left" label-width="100">
        <NFormItem label="美元兑人民币">
          <NInputNumber v-model:value="form.rate" :min="0" :step="0.0001" style="width: 100%" />
        </NFormItem>
        <NFormItem label="日期">
          <NDatePicker v-model:value="form.snapshot_date" type="date" style="width: 100%" />
        </NFormItem>
      </NForm>
      <template #footer>
        <NSpace justify="end">
          <NButton @click="showModal = false">取消</NButton>
          <NButton type="primary" @click="submitForm">保存</NButton>
        </NSpace>
      </template>
    </NModal>
  </NSpin>
</template>

<style scoped>
.header-row {
  display: flex;
  justify-content: space-between;
  align-items: flex-start;
  margin-bottom: 16px;
}

.header-row .page-title {
  margin-bottom: 4px;
}

.page-desc {
  margin: 0;
  color: #8b98a5;
  font-size: 13px;
}
</style>
