<script setup>
import { computed, h, onMounted, reactive, ref, watch } from 'vue'
import { useRoute, useRouter } from 'vue-router'
import {
  NButton,
  NDataTable,
  NDatePicker,
  NForm,
  NFormItem,
  NInput,
  NInputNumber,
  NModal,
  NSelect,
  NSpace,
  NSpin,
  NTag,
  useMessage,
} from 'naive-ui'
import {
  createTransaction,
  deleteTransaction,
  getAccounts,
  getConfig,
  getInstruments,
  getLatestExchangeRate,
  getTransactions,
  updateTransaction,
} from '../api/client'
import { formatMoney, formatNumber } from '../utils/format'

const message = useMessage()
const route = useRoute()
const router = useRouter()
const loading = ref(true)
const showModal = ref(false)
const editingId = ref(null)
const transactions = ref([])
const accounts = ref([])
const instruments = ref([])
const defaultUsdRate = ref(7.2)

const form = reactive({
  trade_date: Date.now(),
  account_id: null,
  instrument_id: null,
  side: 'buy',
  quantity: null,
  price: null,
  fee: 0,
  exchange_rate: 1,
  note: '',
})

const sideOptions = [
  { label: '买入', value: 'buy' },
  { label: '卖出', value: 'sell' },
]

const selectedInstrument = computed(() =>
  instruments.value.find((item) => item.id === form.instrument_id),
)

const isUsd = computed(() => selectedInstrument.value?.currency === 'USD')
const currencySymbol = computed(() => (isUsd.value ? 'USD' : 'CNY'))

async function loadData() {
  loading.value = true
  try {
    const [txRows, accountRows, instrumentRows, config, latestFx] = await Promise.all([
      getTransactions(),
      getAccounts(),
      getInstruments(true),
      getConfig(),
      getLatestExchangeRate(),
    ])
    transactions.value = txRows
    accounts.value = accountRows
    instruments.value = instrumentRows
    defaultUsdRate.value = latestFx?.rate || config.usd_cny_rate || 7.2
  } finally {
    loading.value = false
  }
}

onMounted(async () => {
  await loadData()
  await applyPlanFromQuery()
})

async function applyPlanFromQuery() {
  if (route.query.from_plan !== '1' || !route.query.instrument_id) return

  const instrumentId = Number(route.query.instrument_id)
  const instrument = instruments.value.find((item) => item.id === instrumentId)
  if (!instrument) return

  editingId.value = null
  form.instrument_id = instrumentId
  form.account_id = instrument.account_id
  form.side = 'buy'
  form.quantity = null
  form.price = null
  form.fee = 0
  form.note = route.query.note ? String(route.query.note) : ''
  if (route.query.plan_date) {
    form.trade_date = new Date(String(route.query.plan_date)).getTime()
  }
  applyInstrumentDefaults()
  showModal.value = true
  router.replace({ path: '/transactions' })
}

function applyInstrumentDefaults() {
  if (isUsd.value) {
    if (!editingId.value || form.exchange_rate <= 1) {
      form.exchange_rate = defaultUsdRate.value
    }
  } else {
    form.exchange_rate = 1
  }
}

watch(() => form.instrument_id, applyInstrumentDefaults)

function resetForm() {
  editingId.value = null
  form.trade_date = Date.now()
  form.account_id = accounts.value[0]?.id ?? null
  form.instrument_id = instruments.value[0]?.id ?? null
  form.side = 'buy'
  form.quantity = null
  form.price = null
  form.fee = 0
  form.exchange_rate = 1
  form.note = ''
  applyInstrumentDefaults()
}

function openCreate() {
  resetForm()
  showModal.value = true
}

function openEdit(row) {
  editingId.value = row.id
  form.trade_date = new Date(row.trade_date).getTime()
  form.account_id = row.account_id
  form.instrument_id = row.instrument_id
  form.side = row.side
  form.quantity = row.quantity
  form.price = row.price
  form.fee = row.fee
  form.exchange_rate = row.exchange_rate
  form.note = row.note || ''
  showModal.value = true
}

async function submitForm() {
  const payload = {
    trade_date: new Date(form.trade_date).toISOString().slice(0, 10),
    account_id: form.account_id,
    instrument_id: form.instrument_id,
    side: form.side,
    quantity: form.quantity,
    price: form.price,
    fee: form.fee || 0,
    exchange_rate: isUsd.value ? form.exchange_rate : 1,
    note: form.note || null,
  }

  try {
    if (editingId.value) {
      await updateTransaction(editingId.value, payload)
      message.success('交易已更新')
    } else {
      await createTransaction(payload)
      message.success('交易已添加')
    }
    showModal.value = false
    await loadData()
  } catch (error) {
    message.error(error.response?.data?.detail || '保存失败')
  }
}

async function removeRow(id) {
  try {
    await deleteTransaction(id)
    message.success('交易已删除')
    await loadData()
  } catch (error) {
    message.error(error.response?.data?.detail || '删除失败')
  }
}

const columns = [
  { title: '日期', key: 'trade_date', width: 110 },
  { title: '账户', key: 'account_name', width: 80 },
  { title: '品种', key: 'instrument_name', width: 120 },
  { title: '代码', key: 'instrument_code', width: 90 },
  {
    title: '方向',
    key: 'side',
    width: 70,
    render: (row) => (row.side === 'buy' ? '买入' : '卖出'),
  },
  {
    title: '币种',
    key: 'currency',
    width: 60,
    render: (row) =>
      h(NTag, { size: 'small', type: row.currency === 'USD' ? 'info' : 'default' }, {
        default: () => row.currency,
      }),
  },
  { title: '数量', key: 'quantity', render: (row) => formatNumber(row.quantity, 4) },
  {
    title: '成交价',
    key: 'price',
    render: (row) => formatMoney(row.price, row.currency),
  },
  {
    title: '成交金额',
    key: 'amount',
    render: (row) => formatMoney(row.amount, row.currency),
  },
  {
    title: '折合人民币',
    key: 'amount_cny',
    render: (row) => (row.currency === 'USD' ? formatMoney(row.amount_cny) : '-'),
  },
  {
    title: '手续费',
    key: 'fee',
    render: (row) => formatMoney(row.fee, row.currency),
  },
  {
    title: '汇率',
    key: 'exchange_rate',
    render: (row) => (row.currency === 'USD' ? formatNumber(row.exchange_rate, 4) : '-'),
  },
  { title: '备注', key: 'note', ellipsis: true },
  {
    title: '操作',
    key: 'actions',
    width: 140,
    render: (row) =>
      h(NSpace, null, {
        default: () => [
          h(
            NButton,
            { size: 'small', onClick: () => openEdit(row) },
            { default: () => '编辑' },
          ),
          h(
            NButton,
            { size: 'small', type: 'error', onClick: () => removeRow(row.id) },
            { default: () => '删除' },
          ),
        ],
      }),
  },
]
</script>

<template>
  <NSpin :show="loading">
    <div class="header-row">
      <div>
        <h1 class="page-title">交易记录</h1>
        <p class="page-desc">按品种原币记账：大陆品种用人民币，香港品种用美元；人民币市值由汇率折算</p>
      </div>
      <NButton type="primary" @click="openCreate">新增交易</NButton>
    </div>

    <div class="panel">
      <NDataTable :columns="columns" :data="transactions" :bordered="false" size="small" scroll-x="1200" />
    </div>

    <NModal
      v-model:show="showModal"
      preset="card"
      :title="editingId ? '编辑交易' : '新增交易'"
      style="width: 580px"
    >
      <NForm label-placement="left" label-width="120">
        <NFormItem label="日期">
          <NDatePicker v-model:value="form.trade_date" type="date" style="width: 100%" />
        </NFormItem>
        <NFormItem label="账户">
          <NSelect
            v-model:value="form.account_id"
            :options="accounts.map((item) => ({ label: item.name, value: item.id }))"
          />
        </NFormItem>
        <NFormItem label="品种">
          <NSelect
            v-model:value="form.instrument_id"
            :options="instruments.map((item) => ({ label: `${item.code} ${item.name} (${item.currency})`, value: item.id }))"
          />
        </NFormItem>
        <NFormItem label="记账币种">
          <NTag :type="isUsd ? 'info' : 'default'">{{ currencySymbol }}</NTag>
        </NFormItem>
        <NFormItem label="方向">
          <NSelect v-model:value="form.side" :options="sideOptions" />
        </NFormItem>
        <NFormItem label="数量">
          <NInputNumber v-model:value="form.quantity" :min="0" style="width: 100%" />
        </NFormItem>
        <NFormItem :label="`成交价 (${currencySymbol})`">
          <NInputNumber v-model:value="form.price" :min="0" style="width: 100%" />
        </NFormItem>
        <NFormItem :label="`手续费 (${currencySymbol})`">
          <NInputNumber v-model:value="form.fee" :min="0" style="width: 100%" />
        </NFormItem>
        <NFormItem v-if="isUsd" label="美元兑人民币">
          <NInputNumber
            v-model:value="form.exchange_rate"
            :min="0"
            :step="0.01"
            style="width: 100%"
            placeholder="成交当日汇率"
          />
        </NFormItem>
        <NFormItem label="备注">
          <NInput v-model:value="form.note" />
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
