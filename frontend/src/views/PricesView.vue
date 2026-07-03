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
  NSelect,
  NSpace,
  NSpin,
  useMessage,
} from 'naive-ui'
import { getInstruments, getPrices, updatePrice } from '../api/client'
import { formatMoney, formatNumber } from '../utils/format'

const message = useMessage()
const loading = ref(true)
const showModal = ref(false)
const prices = ref([])
const instruments = ref([])

const form = reactive({
  instrument_id: null,
  price: null,
  snapshot_date: Date.now(),
})

async function loadData() {
  loading.value = true
  try {
    const [priceRows, instrumentRows] = await Promise.all([
      getPrices(),
      getInstruments(true),
    ])
    prices.value = priceRows
    instruments.value = instrumentRows
  } finally {
    loading.value = false
  }
}

onMounted(loadData)

function openCreate() {
  form.instrument_id = instruments.value[0]?.id ?? null
  form.price = null
  form.snapshot_date = Date.now()
  showModal.value = true
}

async function submitForm() {
  try {
    await updatePrice({
      instrument_id: form.instrument_id,
      price: form.price,
      snapshot_date: new Date(form.snapshot_date).toISOString().slice(0, 10),
    })
    message.success('行情已更新')
    showModal.value = false
    await loadData()
  } catch (error) {
    message.error(error.response?.data?.detail || '更新失败')
  }
}

const columns = [
  { title: '代码', key: 'instrument_code', width: 100 },
  { title: '名称', key: 'instrument_name', width: 140 },
  { title: '币种', key: 'currency', width: 80 },
  { title: '最新价', key: 'price', render: (row) => formatNumber(row.price, 4) },
  { title: '更新日期', key: 'snapshot_date', width: 120 },
]
</script>

<template>
  <NSpin :show="loading">
    <div class="header-row">
      <h1 class="page-title">行情更新</h1>
      <NButton type="primary" @click="openCreate">更新价格</NButton>
    </div>

    <div class="panel">
      <NDataTable :columns="columns" :data="prices" :bordered="false" size="small" />
    </div>

    <NModal v-model:show="showModal" preset="card" title="更新价格" style="width: 480px">
      <NForm label-placement="left" label-width="90">
        <NFormItem label="品种">
          <NSelect
            v-model:value="form.instrument_id"
            :options="instruments.map((item) => ({ label: `${item.code} ${item.name}`, value: item.id }))"
          />
        </NFormItem>
        <NFormItem label="价格">
          <NInputNumber v-model:value="form.price" :min="0" style="width: 100%" />
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
  align-items: center;
  margin-bottom: 16px;
}

.header-row .page-title {
  margin-bottom: 0;
}
</style>
