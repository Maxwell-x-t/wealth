<script setup>
import { h, onMounted, reactive, ref } from 'vue'
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
  NTag,
  useMessage,
} from 'naive-ui'
import { getInstruments, getPrices, refreshPrices, updatePrice } from '../api/client'
import { formatNumber, formatPrice, formatPremiumRate } from '../utils/format'

function signedClass(value) {
  if (value === null || value === undefined || Number.isNaN(Number(value))) return ''
  return Number(value) >= 0 ? 'positive' : 'negative'
}

function renderPriceWithPremium(price, currency, premiumRate) {
  if (price == null) return '-'
  const priceText = formatPrice(price, currency)
  const premiumText = formatPremiumRate(premiumRate)
  if (!premiumText) return priceText
  return h('span', {}, [
    priceText,
    h(
      'span',
      { class: signedClass(premiumRate), style: 'margin-left: 6px; font-size: 12px' },
      premiumText,
    ),
  ])
}

const message = useMessage()
const loading = ref(true)
const refreshing = ref(false)
const showModal = ref(false)
const prices = ref([])
const instruments = ref([])
const lastRefresh = ref(null)

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

async function handleRefresh() {
  refreshing.value = true
  try {
    const result = await refreshPrices()
    lastRefresh.value = result
    await loadData()
    if (result.fail_count === 0) {
      message.success(`已拉取 ${result.success_count} 个品种行情`)
    } else if (result.success_count === 0) {
      message.error('全部拉取失败，请检查网络或稍后重试')
    } else {
      message.warning(`成功 ${result.success_count}，失败 ${result.fail_count}`)
    }
  } catch (error) {
    message.error(error.response?.data?.detail || '拉取失败')
  } finally {
    refreshing.value = false
  }
}

const columns = [
  { title: '代码', key: 'instrument_code', width: 100 },
  { title: '名称', key: 'instrument_name', width: 140 },
  {
    title: '币种',
    key: 'currency',
    width: 70,
    render: (row) =>
      h(NTag, { size: 'small', type: row.currency === 'USD' ? 'info' : 'default' }, {
        default: () => row.currency,
      }),
  },
  {
    title: '最新价',
    key: 'price',
    render: (row) => renderPriceWithPremium(row.price, row.currency, row.premium_rate),
  },
  {
    title: '更新日期',
    key: 'snapshot_date',
    width: 120,
    render: (row) => row.snapshot_date || '-',
  },
]

const refreshColumns = [
  { title: '代码', key: 'instrument_code', width: 90 },
  { title: '名称', key: 'instrument_name', width: 120 },
  {
    title: '结果',
    key: 'success',
    width: 80,
    render: (row) =>
      h(NTag, { size: 'small', type: row.success ? 'success' : 'error' }, {
        default: () => (row.success ? '成功' : '失败'),
      }),
  },
  {
    title: '价格',
    key: 'price',
    render: (row) => {
      if (row.price == null) return '-'
      const premiumText = formatPremiumRate(row.premium_rate)
      if (!premiumText) return formatNumber(row.price, 3)
      return h('span', {}, [
        formatNumber(row.price, 3),
        h(
          'span',
          { class: signedClass(row.premium_rate), style: 'margin-left: 6px; font-size: 12px' },
          premiumText,
        ),
      ])
    },
  },
  { title: '来源', key: 'source', width: 120, render: (row) => row.source || '-' },
  {
    title: '说明',
    key: 'error',
    render: (row) => row.error || (row.snapshot_date ? row.snapshot_date : '-'),
  },
]
</script>

<template>
  <NSpin :show="loading || refreshing">
    <div class="header-row">
      <div>
        <h1 class="page-title">行情更新</h1>
        <p class="page-desc">大陆 ETF 走东方财富/新浪，美股 ETF 走新浪美股/Yahoo；失败可手动补录</p>
      </div>
      <NSpace>
        <NButton type="primary" :loading="refreshing" @click="handleRefresh">一键拉取行情</NButton>
        <NButton @click="openCreate">手动更新</NButton>
      </NSpace>
    </div>

    <div class="panel" style="margin-bottom: 16px">
      <NDataTable :columns="columns" :data="prices" :bordered="false" size="small" />
    </div>

    <div v-if="lastRefresh" class="panel">
      <h3>
        最近一次拉取：成功 {{ lastRefresh.success_count }}，失败 {{ lastRefresh.fail_count }}
      </h3>
      <NDataTable
        :columns="refreshColumns"
        :data="lastRefresh.items"
        :bordered="false"
        size="small"
      />
    </div>

    <NModal v-model:show="showModal" preset="card" title="手动更新价格" style="width: 480px">
      <NForm label-placement="left" label-width="90">
        <NFormItem label="品种">
          <NSelect
            v-model:value="form.instrument_id"
            :options="instruments.map((item) => ({ label: `${item.code} ${item.name}`, value: item.id }))"
          />
        </NFormItem>
        <NFormItem label="价格">
          <NInputNumber v-model:value="form.price" :min="0" :precision="3" style="width: 100%" />
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

h3 {
  margin: 0 0 12px;
  font-size: 15px;
  font-weight: 600;
}
</style>
