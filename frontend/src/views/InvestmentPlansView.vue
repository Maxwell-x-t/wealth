<script setup>
import { computed, h, onMounted, ref } from 'vue'
import { useRouter } from 'vue-router'
import {
  NAlert,
  NButton,
  NDataTable,
  NSelect,
  NSpace,
  NSpin,
  NTabPane,
  NTabs,
  NTag,
  useMessage,
} from 'naive-ui'
import {
  getInvestmentPlanOverview,
  getInvestmentPlans,
  skipInvestmentPlan,
} from '../api/client'
import { formatMoney } from '../utils/format'

const router = useRouter()
const message = useMessage()

const loading = ref(true)
const overview = ref(null)
const plans = ref([])
const phaseFilter = ref(null)
const actionLoading = ref(false)

const phaseOptions = [
  { label: '全部', value: null },
  { label: '建仓', value: 'building' },
  { label: '定投', value: 'dca' },
]

const statusMap = {
  pending: { label: '待执行', type: 'default' },
  today: { label: '今日', type: 'warning' },
  done: { label: '已完成', type: 'success' },
  partial: { label: '部分完成', type: 'warning' },
  overdue: { label: '已逾期', type: 'error' },
  merged: { label: '已合并', type: 'info' },
  skipped: { label: '已跳过', type: 'default' },
}

async function loadData() {
  loading.value = true
  try {
    const today = new Date()
    const end = new Date(today.getFullYear(), today.getMonth() + 3, today.getDate())
    const params = {
      start: today.toISOString().slice(0, 10),
      end: end.toISOString().slice(0, 10),
    }
    if (phaseFilter.value) params.phase = phaseFilter.value
    const [overviewData, planRows] = await Promise.all([
      getInvestmentPlanOverview(),
      getInvestmentPlans(params),
    ])
    overview.value = overviewData
    plans.value = planRows
  } finally {
    loading.value = false
  }
}

onMounted(loadData)

function renderAmount(row) {
  const lines = [formatMoney(row.amount_cny)]
  if (row.rolled_over_amount_cny > 0) {
    lines.push(
      `含补投 ${formatMoney(row.rolled_over_amount_cny)}（${row.rolled_over_count} 笔）`,
    )
  } else if (row.base_amount_cny && row.base_amount_cny !== row.amount_cny) {
    lines.push(`原计划 ${formatMoney(row.base_amount_cny)}`)
  }
  if (row.matched_amount_cny > 0 && row.status !== 'done') {
    lines.push(`已投入 ${formatMoney(row.matched_amount_cny)}`)
  }
  return h('div', { style: 'line-height: 1.4' }, lines.map((text, index) =>
    h('div', { style: index > 0 ? 'font-size: 12px; color: #8b98a5' : '' }, text),
  ))
}

function renderMatched(row) {
  if (!row.matched_amount_cny) return '-'
  const lines = [formatMoney(row.matched_amount_cny)]
  if (row.shortfall_cny > 0) {
    lines.push(`欠 ${formatMoney(row.shortfall_cny)}`)
  }
  return h('div', { style: 'line-height: 1.4' }, lines.map((text, index) =>
    h('div', { style: index > 0 ? 'font-size: 12px; color: #e88080' : '' }, text),
  ))
}

function goRecord(row) {
  router.push({
    path: '/transactions',
    query: {
      from_plan: '1',
      account: row.account,
      category: row.category,
      plan_date: row.plan_date,
      note: `计划:${row.phase_label} ${row.target_label}`,
    },
  })
}

async function toggleSkip(row, skipped) {
  actionLoading.value = true
  try {
    await skipInvestmentPlan({
      plan_date: row.plan_date,
      account: row.account,
      category: row.category,
      skipped,
    })
    message.success(skipped ? '已跳过，金额不顺延到下一笔' : '已取消跳过')
    await loadData()
  } catch (error) {
    message.error(error.response?.data?.detail || '操作失败')
  } finally {
    actionLoading.value = false
  }
}

function renderStatus(row) {
  const meta = statusMap[row.status] || statusMap.pending
  return h(NTag, { type: meta.type, size: 'small' }, { default: () => meta.label })
}

function renderActions(row) {
  if (row.status === 'done' || row.status === 'merged' || row.status === 'partial') {
    return '-'
  }
  if (row.status === 'skipped') {
    return h(
      NButton,
      {
        size: 'small',
        quaternary: true,
        disabled: actionLoading.value,
        onClick: () => toggleSkip(row, false),
      },
      { default: () => '取消跳过' },
    )
  }
  return h(NSpace, { size: 6 }, {
    default: () => [
      h(
        NButton,
        { size: 'small', type: 'primary', onClick: () => goRecord(row) },
        { default: () => '录入' },
      ),
      h(
        NButton,
        {
          size: 'small',
          quaternary: true,
          disabled: actionLoading.value,
          onClick: () => toggleSkip(row, true),
        },
        { default: () => '跳过' },
      ),
    ],
  })
}

function buildColumns({ history = false } = {}) {
  const cols = [
    { title: '日期', key: 'plan_date', width: 110 },
    { title: '阶段', key: 'phase_label', width: 70 },
    { title: '账户', key: 'account', width: 70 },
    { title: '标的', key: 'target_label', width: 120 },
  ]

  if (history) {
    cols.push(
      { title: '计划金额', key: 'base_amount_cny', width: 110, render: (row) => formatMoney(row.base_amount_cny) },
      { title: '实际投入', key: 'matched_amount_cny', width: 120, render: (row) => renderMatched(row) },
      { title: '应投合计', key: 'amount_cny', width: 110, render: (row) => formatMoney(row.amount_cny) },
    )
  } else {
    cols.push(
      { title: '应投金额', key: 'amount_cny', width: 160, render: (row) => renderAmount(row) },
    )
  }

  cols.push(
    { title: '状态', key: 'status', width: 100, render: (row) => renderStatus(row) },
    { title: '操作', key: 'actions', width: 150, render: (row) => renderActions(row) },
  )
  return cols
}

const upcomingColumns = buildColumns()
const historyColumns = buildColumns({ history: true })

const buildingProgress = computed(() => {
  if (!overview.value?.building_total) return 0
  return Math.round((overview.value.building_done / overview.value.building_total) * 100)
})
</script>

<template>
  <NSpin :show="loading">
    <div class="header-row">
      <div>
        <h1 class="page-title">投资计划</h1>
        <p class="page-desc">可跳过暂不执行（金额不顺延）；部分完成仅顺延差额</p>
      </div>
      <NSelect
        v-model:value="phaseFilter"
        :options="phaseOptions"
        style="width: 140px"
        @update:value="loadData"
      />
    </div>

    <template v-if="overview">
      <div class="metric-grid">
        <div class="metric-card">
          <div class="metric-label">建仓进度</div>
          <div class="metric-value">{{ buildingProgress }}%</div>
          <div class="metric-sub">{{ overview.building_done }} / {{ overview.building_total }} 笔</div>
        </div>
        <div class="metric-card">
          <div class="metric-label">定投执行率</div>
          <div class="metric-value">{{ overview.dca_execution_rate }}%</div>
          <div class="metric-sub">
            完成 {{ overview.dca_done }} · 部分 {{ overview.dca_partial }} / {{ overview.dca_elapsed }}
          </div>
        </div>
        <div class="metric-card">
          <div class="metric-label">逾期计划</div>
          <div class="metric-value" :class="overview.overdue_count > 0 ? 'negative' : ''">
            {{ overview.overdue_count }}
          </div>
        </div>
        <div class="metric-card">
          <div class="metric-label">下一笔计划</div>
          <div class="metric-value" style="font-size: 16px">
            <template v-if="overview.next_item">
              {{ overview.next_item.plan_date }} {{ overview.next_item.target_label }}
            </template>
            <template v-else>-</template>
          </div>
          <div v-if="overview.next_item" class="metric-sub">
            {{ formatMoney(overview.next_item.amount_cny) }}
            <span
              v-if="overview.next_item.rolled_over_amount_cny > 0"
              class="rollover-hint"
            >
              （含补投 {{ formatMoney(overview.next_item.rolled_over_amount_cny) }}）
            </span>
            <NButton
              v-if="overview.next_item.status !== 'done' && overview.next_item.status !== 'skipped'"
              size="tiny"
              type="primary"
              style="margin-left: 8px"
              @click="goRecord(overview.next_item)"
            >
              录入交易
            </NButton>
          </div>
        </div>
      </div>

      <NAlert
        v-if="overview.overdue_count > 0"
        type="warning"
        :title="`有 ${overview.overdue_count} 笔计划逾期未执行（未找到可合并的下一笔）`"
        style="margin-bottom: 16px"
      />
      <NAlert
        v-else-if="overview.merged_count > 0"
        type="info"
        :title="`已将 ${overview.merged_count} 笔逾期计划合并到后续应投金额`"
        style="margin-bottom: 16px"
      />

      <NTabs type="line" animated>
        <NTabPane name="upcoming" tab="即将执行">
          <div class="panel">
            <NDataTable
              :columns="upcomingColumns"
              :data="overview.upcoming"
              :bordered="false"
              size="small"
            />
          </div>
        </NTabPane>
        <NTabPane name="history" tab="历史记录">
          <div class="panel">
            <NDataTable
              :columns="historyColumns"
              :data="overview.history"
              :bordered="false"
              size="small"
            />
          </div>
        </NTabPane>
        <NTabPane name="calendar" tab="未来三个月">
          <div class="panel">
            <NDataTable :columns="upcomingColumns" :data="plans" :bordered="false" size="small" />
          </div>
        </NTabPane>
        <NTabPane v-if="overview.overdue.length" name="overdue" tab="逾期">
          <div class="panel">
            <NDataTable
              :columns="upcomingColumns"
              :data="overview.overdue"
              :bordered="false"
              size="small"
            />
          </div>
        </NTabPane>
      </NTabs>
    </template>
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

.rollover-hint {
  color: #8b98a5;
  font-size: 12px;
}
</style>
