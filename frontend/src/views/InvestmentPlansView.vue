<script setup>
import { computed, h, onMounted, onUnmounted, ref } from 'vue'
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
  getDcaLiveSignal,
  getInvestmentPlanOverview,
  getInvestmentPlans,
  deferInvestmentPlan,
  skipInvestmentPlan,
} from '../api/client'
import { formatMoney } from '../utils/format'

const router = useRouter()
const message = useMessage()

const loading = ref(true)
const overview = ref(null)
const plans = ref([])
const liveSignal = ref(null)
const signalLoading = ref(false)
const phaseFilter = ref(null)
const accountFilter = ref(null)
const actionLoading = ref(false)

const phaseOptions = [
  { label: '全部', value: null },
  { label: '建仓', value: 'building' },
  { label: '定投', value: 'dca' },
]

const accountOptions = [
  { label: '全部账户', value: null },
  { label: '大陆', value: '大陆' },
  { label: '香港', value: '香港' },
]

function matchesAccount(row) {
  return !accountFilter.value || row.account === accountFilter.value
}

const statusMap = {
  pending: { label: '待执行', type: 'default' },
  today: { label: '今日', type: 'warning' },
  done: { label: '已完成', type: 'success' },
  partial: { label: '部分完成', type: 'warning' },
  overdue: { label: '已逾期', type: 'error' },
  merged: { label: '已合并', type: 'info' },
  skipped: { label: '已跳过', type: 'default' },
  deferred: { label: '已延期', type: 'info' },
  accumulating: { label: '累积中', type: 'info' },
  ready: { label: '可买入', type: 'success' },
}

function rowCurrency(row) {
  return row.currency || (row.account === '香港' ? 'USD' : 'CNY')
}

function planAmounts(row) {
  const weeklyBase = Number(row.base_amount_cny ?? row.amount_cny ?? 0)
  const rollover = Number(row.rolled_over_amount_cny ?? 0)
  const credit = Number(row.credit_offset_cny ?? 0)

  if (rollover > 0 || credit > 0) {
    const total = Math.max(0, Number(row.amount_cny ?? weeklyBase + rollover - credit))
    const own = Math.max(0, total - rollover + credit)
    return {
      base: own,
      weeklyBase: own,
      rollover,
      credit,
      total,
    }
  }

  const amount = Number(row.amount_cny ?? weeklyBase)
  return { base: amount, weeklyBase, rollover: 0, credit: 0, total: amount }
}

function formatPlanAmountPair(row) {
  const currency = rowCurrency(row)
  const { base, weeklyBase, total, credit, rollover } = planAmounts(row)
  if (credit > 0 || rollover > 0) {
    return `应投 ${formatMoney(weeklyBase, currency)} · 实际 ${formatMoney(total, currency)}`
  }
  return `应投 ${formatMoney(base, currency)} · 实际 ${formatMoney(total, currency)}`
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
    if (accountFilter.value) params.account = accountFilter.value
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

const SIGNAL_POLL_OPEN_MS = 5 * 60 * 1000
const SIGNAL_POLL_CLOSED_MS = 15 * 60 * 1000
let signalPollTimer = null
let notificationPermissionRequested = false

const sessionLabels = {
  morning: '上午盘',
  afternoon: '下午盘',
  pre_market: '开盘前',
  lunch_break: '午休',
  after_hours: '已收盘',
  closed: '休市',
}

const actionTagTypes = {
  execute: 'success',
  early: 'success',
  defer: 'warning',
  neutral: 'default',
  market_closed: 'default',
}

function shouldNotify(signal) {
  if (!signal?.market?.open || !signal?.notification) return false
  const key = `dca-signal-${signal.as_of}-${signal.notification.level}-${signal.action.code}`
  const sent = localStorage.getItem(key)
  return !sent
}

function markNotified(signal) {
  const key = `dca-signal-${signal.as_of}-${signal.notification.level}-${signal.action.code}`
  localStorage.setItem(key, String(Date.now()))
}

async function maybeBrowserNotify(signal) {
  if (!signal?.notification || !shouldNotify(signal)) return
  if (typeof window === 'undefined' || !('Notification' in window)) return

  if (Notification.permission === 'default' && !notificationPermissionRequested) {
    notificationPermissionRequested = true
    await Notification.requestPermission()
  }
  if (Notification.permission !== 'granted') return

  const { title, body } = signal.notification
  new Notification(title, { body, tag: `dca-signal-${signal.action.code}` })
  markNotified(signal)
}

async function loadLiveSignal() {
  signalLoading.value = true
  try {
    const data = await getDcaLiveSignal()
    liveSignal.value = data
    await maybeBrowserNotify(data)
    scheduleSignalPoll()
  } catch {
    liveSignal.value = null
    scheduleSignalPoll()
  } finally {
    signalLoading.value = false
  }
}

function scheduleSignalPoll() {
  clearTimeout(signalPollTimer)
  const delay = liveSignal.value?.market?.open ? SIGNAL_POLL_OPEN_MS : SIGNAL_POLL_CLOSED_MS
  signalPollTimer = setTimeout(loadLiveSignal, delay)
}

const showLiveSignalPanel = computed(() => {
  if (!liveSignal.value) return false
  if (accountFilter.value && accountFilter.value !== '大陆') return false
  return liveSignal.value.in_dca_phase || liveSignal.value.has_today_plan
})

const liveSessionLabel = computed(() => {
  const session = liveSignal.value?.market?.session
  return sessionLabels[session] || session || '—'
})

async function loadAll() {
  await Promise.all([loadData(), loadLiveSignal()])
}

onMounted(loadAll)
onUnmounted(() => {
  clearTimeout(signalPollTimer)
})

function renderAmount(row) {
  const currency = rowCurrency(row)
  const { base, rollover, credit, total } = planAmounts(row)
  const lines = [
    h('div', { style: 'display: flex; gap: 12px; flex-wrap: wrap; align-items: baseline' }, [
      h('span', {}, `应投 ${formatMoney(base, currency)}`),
      h(
        'span',
        { style: total !== base ? 'color: #e8b86d' : 'color: #c9d1d9' },
        `实际 ${formatMoney(total, currency)}`,
      ),
    ]),
  ]

  if (rollover > 0) {
    lines.push(
      `顺延 ${formatMoney(rollover, currency)}${row.rolled_over_count ? `（${row.rolled_over_count} 笔）` : ''}`,
    )
  }
  if (credit > 0) {
    lines.push(`结余抵扣 ${formatMoney(credit, currency)}`)
  } else if (
    row.base_amount_cny
    && Math.abs(Number(row.base_amount_cny) - Number(row.amount_cny ?? 0)) > 0.01
  ) {
    lines.push(`原比例 ${formatMoney(row.base_amount_cny, currency)}`)
  }
  if (row.whole_share_mode && row.execution_pool_usd > 0) {
    lines.push(`执行池 ${formatMoney(row.execution_pool_usd, 'USD')}`)
  }
  if (row.adjustment_note && credit <= 0) {
    lines.push(row.adjustment_note)
  }
  if (row.phase === 'dca' && row.month_boost_cny > 0) {
    lines.push(
      h(
        'div',
        { style: 'font-size: 12px; color: #e8b86d' },
        `本月危机加仓 ${formatMoney(row.month_boost_cny, currency)}`,
      ),
    )
  }
  if (row.matched_amount_cny > 0 && ['partial', 'today', 'done'].includes(row.status)) {
    lines.push(`已投入 ${formatMoney(row.matched_amount_cny, currency)}`)
  }
  return h('div', { style: 'line-height: 1.4' }, lines.map((text, index) => {
    const isNote = typeof text === 'string' && row.adjustment_note && text === row.adjustment_note
    if (typeof text !== 'string') {
      return text
    }
    return h(
      'div',
      { style: index > 0 ? `font-size: 12px; color: ${isNote ? '#e8b86d' : '#8b98a5'}` : '' },
      text,
    )
  }))
}

function renderMatched(row) {
  if (!row.matched_amount_cny) return '-'
  const currency = rowCurrency(row)
  const lines = [formatMoney(row.matched_amount_cny, currency)]
  if (row.shortfall_cny > 0) {
    lines.push(`欠 ${formatMoney(row.shortfall_cny, currency)}`)
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
      phase: row.phase,
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
      phase: row.phase,
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

async function toggleDefer(row, deferred) {
  actionLoading.value = true
  try {
    await deferInvestmentPlan({
      plan_date: row.plan_date,
      account: row.account,
      category: row.category,
      phase: row.phase,
      deferred,
    })
    message.success(deferred ? '已延期，金额顺延到下一笔未到期计划' : '已取消延期')
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
  if (row.status === 'deferred') {
    return h(
      NButton,
      {
        size: 'small',
        quaternary: true,
        disabled: actionLoading.value,
        onClick: () => toggleDefer(row, false),
      },
      { default: () => '取消延期' },
    )
  }
  const recordLabel = row.status === 'ready' ? '录入买入' : '录入'
  return h(NSpace, { size: 6 }, {
    default: () => [
      h(
        NButton,
        {
          size: 'small',
          type: row.status === 'ready' ? 'success' : 'primary',
          onClick: () => goRecord(row),
        },
        { default: () => recordLabel },
      ),
      h(
        NButton,
        {
          size: 'small',
          quaternary: true,
          disabled: actionLoading.value,
          onClick: () => toggleDefer(row, true),
        },
        { default: () => '延期' },
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
      {
        title: '应投 / 实际',
        key: 'amount_cny',
        width: 180,
        render: (row) => renderAmount(row),
      },
      { title: '已投入', key: 'matched_amount_cny', width: 120, render: (row) => renderMatched(row) },
    )
  } else {
    cols.push(
      { title: '应投 / 实际', key: 'amount_cny', width: 200, render: (row) => renderAmount(row) },
    )
  }

  cols.push(
    { title: '状态', key: 'status', width: 100, render: (row) => renderStatus(row) },
    { title: '操作', key: 'actions', width: 210, render: (row) => renderActions(row) },
  )
  return cols
}

const upcomingColumns = buildColumns()
const historyColumns = buildColumns({ history: true })

const buildingProgress = computed(() => {
  if (!overview.value?.building_total) return 0
  return Math.round((overview.value.building_done / overview.value.building_total) * 100)
})

const maFactors = computed(() =>
  (overview.value?.dca_ma || []).filter((ma) => Math.abs((ma.factor ?? 1) - 1) > 0.005),
)

const showStrategyPanel = computed(() => {
  const o = overview.value
  if (!o) return false
  return (
    o.dca_tilt_active ||
    (o.dca_boost?.enabled && (o.dca_boost?.note || o.dca_boost?.max_drawdown_pct > 0)) ||
    maFactors.value.length > 0
  )
})

const displaySummaries = computed(() => {
  const rows = overview.value?.account_summaries || []
  if (!accountFilter.value) return rows
  return rows.filter((row) => row.account === accountFilter.value)
})

const filteredUpcoming = computed(() =>
  (overview.value?.upcoming || []).filter(matchesAccount),
)

const filteredHistory = computed(() =>
  (overview.value?.history || []).filter(matchesAccount),
)

const filteredOverdue = computed(() =>
  (overview.value?.overdue || []).filter(matchesAccount),
)

const displayNextItem = computed(() => {
  if (accountFilter.value) {
    return filteredUpcoming.value[0] || null
  }
  return overview.value?.next_item || null
})

const ACCOUNT_CURRENCY = { 大陆: 'CNY', 香港: 'USD' }

function accountCurrency(summary) {
  return summary.currency || ACCOUNT_CURRENCY[summary.account] || 'CNY'
}

function accountBuildingInvested(summary) {
  return summary.building_invested ?? summary.building_invested_cny ?? 0
}

function accountDcaInvested(summary) {
  return summary.dca_invested ?? summary.dca_invested_cny ?? 0
}

function accountBuildingProgress(summary) {
  if (summary.building_target_amount > 0) {
    const pct = (accountBuildingInvested(summary) / summary.building_target_amount) * 100
    return Math.min(100, Math.round(pct))
  }
  if (!summary?.building_total) return 0
  return Math.round((summary.building_done / summary.building_total) * 100)
}

function accountBuildingSub(summary) {
  const currency = accountCurrency(summary)
  if (summary.building_target_amount > 0) {
    return `已投入 ${formatMoney(accountBuildingInvested(summary), currency)} / ${formatMoney(summary.building_target_amount, currency)}`
  }
  return `${summary.building_done} / ${summary.building_total} 笔`
}
</script>

<template>
  <NSpin :show="loading">
    <div class="header-row">
      <div>
        <h1 class="page-title">投资计划</h1>
        <p class="page-desc">
          统一月额计划按账户比例拆分：大陆（CNY）与香港（USD）同步建仓/定投；香港整股模式下金额逐期入池
        </p>
      </div>
      <NSpace>
        <NSelect
          v-model:value="accountFilter"
          :options="accountOptions"
          style="width: 120px"
          @update:value="loadData"
        />
        <NSelect
          v-model:value="phaseFilter"
          :options="phaseOptions"
          style="width: 140px"
          @update:value="loadData"
        />
      </NSpace>
    </div>

    <template v-if="overview">
      <div v-if="showLiveSignalPanel" class="panel live-signal-panel" style="margin-bottom: 16px">
        <div class="live-signal-head">
          <div>
            <div class="panel-title">盘中信号（大陆 ETF）</div>
            <div class="metric-sub">
              {{ liveSignal.market.local_time }} · {{ liveSessionLabel }}
              <span v-if="liveSignal.market.open" class="live-dot">交易中</span>
            </div>
          </div>
          <NSpace>
            <NTag :type="actionTagTypes[liveSignal.action.code] || 'default'" size="small">
              {{ liveSignal.action.label }}
            </NTag>
            <NButton quaternary size="small" :loading="signalLoading" @click="loadLiveSignal">
              刷新信号
            </NButton>
          </NSpace>
        </div>

        <p class="live-signal-summary">{{ liveSignal.action.summary }}</p>

        <div class="live-signal-metrics">
          <div v-for="cat in liveSignal.categories" :key="cat.category" class="live-signal-metric">
            <div class="metric-label">{{ cat.category_label }}</div>
            <div class="metric-value-sm">
              ×{{ cat.effective_factor.toFixed(2) }}
              <span v-if="cat.deviation_pct != null" class="metric-sub">
                （{{ cat.deviation_pct >= 0 ? '+' : '' }}{{ cat.deviation_pct.toFixed(1) }}%）
              </span>
            </div>
            <div v-if="cat.live_price != null" class="metric-sub">指数 {{ cat.live_price }}</div>
            <div v-else-if="cat.error" class="metric-sub error-text">{{ cat.error }}</div>
          </div>
          <div class="live-signal-metric">
            <div class="metric-label">VIX / 危机</div>
            <div class="metric-value-sm">
              <template v-if="liveSignal.vix.level != null">
                {{ liveSignal.vix.level.toFixed(1) }}
              </template>
              <template v-else>—</template>
              <span v-if="liveSignal.crisis?.triggered" class="metric-sub">
                · ×{{ liveSignal.crisis.multiplier.toFixed(2) }}
              </span>
            </div>
            <div v-if="liveSignal.crisis?.note" class="metric-sub">
              {{ liveSignal.crisis.note }}
            </div>
            <div v-if="liveSignal.crisis?.annual_remaining_amount != null" class="metric-sub">
              年度剩余额度
              {{ formatMoney(liveSignal.crisis.annual_remaining_amount, 'CNY') }}
            </div>
            <div v-else-if="liveSignal.crisis?.drawdown_pct != null" class="metric-sub">
              回撤 {{ liveSignal.crisis.drawdown_pct.toFixed(1) }}%，未触发
            </div>
            <div v-if="liveSignal.vix.source === 'local_monthly'" class="metric-sub">
              本地月线兜底
            </div>
            <div v-else-if="liveSignal.vix.error" class="metric-sub error-text">
              {{ liveSignal.vix.error }}
            </div>
          </div>
          <div class="live-signal-metric">
            <div class="metric-label">本月剩余</div>
            <div class="metric-value-sm">
              {{ formatMoney(liveSignal.month_remaining_cny, 'CNY') }}
            </div>
            <div class="metric-sub">
              已投 {{ formatMoney(liveSignal.month_matched_cny, 'CNY') }}
              / 计划 {{ formatMoney(liveSignal.month_planned_cny, 'CNY') }}
            </div>
          </div>
        </div>

        <div v-if="liveSignal.today_suggestions.length" class="live-suggestions">
          <div class="metric-label" style="margin-bottom: 8px">今日建议</div>
          <div
            v-for="item in liveSignal.today_suggestions"
            :key="`${item.category}-${item.phase}`"
            class="live-suggestion-row"
          >
            <span>{{ item.target_label || item.category_label }}</span>
            <span>
              原计划 {{ formatMoney(item.planned_amount_cny, 'CNY') }}
              → 建议 {{ formatMoney(item.suggested_amount_cny, 'CNY') }}
              <template v-if="item.crisis_extra_cny > 0">
                （含危机 +{{ formatMoney(item.crisis_extra_cny, 'CNY') }}）
              </template>
            </span>
          </div>
          <div v-if="liveSignal.suggested_total_cny > 0" class="metric-sub" style="margin-top: 8px">
            合计建议 {{ formatMoney(liveSignal.suggested_total_cny, 'CNY') }}
            · 综合因子 ×{{ liveSignal.avg_effective_factor.toFixed(2) }}
          </div>
        </div>

        <p class="hint-text" style="margin-bottom: 0; margin-top: 12px">{{ liveSignal.disclaimer }}</p>
      </div>

      <div v-if="displaySummaries.length" class="account-summary-grid">
        <div v-for="summary in displaySummaries" :key="summary.account" class="account-summary-card">
          <div class="account-summary-title">{{ summary.account }}</div>
          <div class="account-summary-metrics">
            <div>
              <div class="metric-label">建仓</div>
              <div class="metric-value-sm">{{ accountBuildingProgress(summary) }}%</div>
              <div class="metric-sub">{{ accountBuildingSub(summary) }}</div>
            </div>
            <div>
              <div class="metric-label">定投执行率</div>
              <div class="metric-value-sm">{{ summary.dca_execution_rate }}%</div>
              <div class="metric-sub">
                {{ summary.dca_done }} / {{ summary.dca_elapsed }}
              </div>
            </div>
            <div>
              <div class="metric-label">建仓已投入</div>
              <div class="metric-value-sm">
                {{ formatMoney(accountBuildingInvested(summary), accountCurrency(summary)) }}
              </div>
            </div>
            <div>
              <div class="metric-label">定投已投入</div>
              <div class="metric-value-sm">
                {{ formatMoney(accountDcaInvested(summary), accountCurrency(summary)) }}
              </div>
            </div>
          </div>
          <div v-if="summary.next_item" class="account-next">
            下一笔：{{ summary.next_item.plan_date }} {{ summary.next_item.target_label }}
            {{ formatPlanAmountPair(summary.next_item) }}
          </div>
        </div>
      </div>

      <div
        v-if="showStrategyPanel"
        class="panel"
        style="margin-bottom: 16px"
      >
        <div class="panel-title">本月定投策略</div>
        <div v-if="overview.dca_tilt_active" class="metric-sub" style="margin-bottom: 8px">
          低配倾斜已启用：偏离超阈值时 100% 投入偏离最大指数
        </div>
        <div v-if="overview.dca_boost?.note" class="metric-sub">
          {{ overview.dca_boost.note }}
        </div>
        <div v-if="overview.dca_boost?.enabled && overview.dca_boost?.annual_cap_amount > 0" class="metric-sub">
          危机加仓年度额度：
          已用约 {{ formatMoney(overview.dca_boost.annual_used_amount, 'CNY') }}
          / 上限 {{ formatMoney(overview.dca_boost.annual_cap_amount, 'CNY') }}
        </div>
        <div v-else-if="overview.dca_boost?.enabled && overview.dca_boost?.max_drawdown_pct > 0" class="metric-sub">
          当前最大指数回撤 {{ overview.dca_boost.max_drawdown_pct }}%
          <template v-if="overview.dca_boost?.vix_level != null">
            · VIX {{ overview.dca_boost.vix_level }}
          </template>
          ，未触发危机加仓
        </div>
        <div
          v-for="ma in maFactors"
          :key="ma.category"
          class="metric-sub"
          style="margin-top: 4px"
        >
          {{ ma.category_label }}均线因子 ×{{ ma.factor.toFixed(2) }}（相对均线
          {{ ma.deviation_pct >= 0 ? '+' : '' }}{{ ma.deviation_pct?.toFixed(1) }}%）
        </div>
      </div>

      <div class="metric-grid">
        <div class="metric-card">
          <div class="metric-label">建仓进度</div>
          <div class="metric-value">{{ buildingProgress }}%</div>
          <div class="metric-sub">{{ overview.building_done }} / {{ overview.building_total }} 笔</div>
        </div>
        <div class="metric-card">
          <div class="metric-label">建仓已投入</div>
          <div class="metric-value" style="font-size: 18px">
            {{ formatMoney(overview.phase_investment?.building_invested_cny) }}
          </div>
          <div class="metric-sub">
            计划匹配 {{ formatMoney(overview.phase_investment?.building_matched_cny) }}
          </div>
        </div>
        <div class="metric-card">
          <div class="metric-label">定投执行率</div>
          <div class="metric-value">{{ overview.dca_execution_rate }}%</div>
          <div class="metric-sub">
            完成 {{ overview.dca_done }} · 部分 {{ overview.dca_partial }} / {{ overview.dca_elapsed }}
          </div>
        </div>
        <div class="metric-card">
          <div class="metric-label">定投已投入</div>
          <div class="metric-value" style="font-size: 18px">
            {{ formatMoney(overview.phase_investment?.dca_invested_cny) }}
          </div>
          <div class="metric-sub">
            计划匹配 {{ formatMoney(overview.phase_investment?.dca_matched_cny) }}
          </div>
        </div>
      </div>

      <div class="metric-grid">
        <div class="metric-card">
          <div class="metric-label">逾期计划</div>
          <div class="metric-value" :class="overview.overdue_count > 0 ? 'negative' : ''">
            {{ overview.overdue_count }}
          </div>
        </div>
        <div class="metric-card">
          <div class="metric-label">下一笔计划</div>
          <div class="metric-value" style="font-size: 16px">
            <template v-if="displayNextItem">
              {{ displayNextItem.plan_date }} {{ displayNextItem.target_label }}
            </template>
            <template v-else>-</template>
          </div>
          <div v-if="displayNextItem" class="metric-sub">
            {{ formatPlanAmountPair(displayNextItem) }}
            <NButton
              v-if="!['done', 'skipped', 'deferred'].includes(displayNextItem.status)"
              size="tiny"
              type="primary"
              style="margin-left: 8px"
              @click="goRecord(displayNextItem)"
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
              :data="filteredUpcoming"
              :bordered="false"
              size="small"
            />
          </div>
        </NTabPane>
        <NTabPane name="history" tab="历史记录">
          <div class="panel">
            <NDataTable
              :columns="historyColumns"
              :data="filteredHistory"
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
        <NTabPane v-if="filteredOverdue.length" name="overdue" tab="逾期">
          <div class="panel">
            <NDataTable
              :columns="upcomingColumns"
              :data="filteredOverdue"
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

.account-summary-grid {
  display: grid;
  grid-template-columns: repeat(auto-fit, minmax(280px, 1fr));
  gap: 12px;
  margin-bottom: 16px;
}

.account-summary-card {
  padding: 14px 16px;
  border-radius: 8px;
  background: rgba(255, 255, 255, 0.03);
  border: 1px solid rgba(255, 255, 255, 0.06);
}

.account-summary-title {
  font-weight: 600;
  margin-bottom: 10px;
}

.live-signal-panel {
  border: 1px solid rgba(79, 140, 255, 0.25);
}

.live-signal-head {
  display: flex;
  justify-content: space-between;
  align-items: flex-start;
  gap: 12px;
  margin-bottom: 8px;
}

.live-signal-summary {
  margin: 0 0 12px;
  color: #cbd5e1;
  font-size: 13px;
}

.live-signal-metrics {
  display: grid;
  grid-template-columns: repeat(auto-fit, minmax(160px, 1fr));
  gap: 12px;
}

.live-signal-metric {
  padding: 10px 12px;
  border-radius: 8px;
  background: rgba(255, 255, 255, 0.03);
}

.live-suggestions {
  margin-top: 14px;
  padding-top: 12px;
  border-top: 1px solid rgba(255, 255, 255, 0.06);
}

.live-suggestion-row {
  display: flex;
  justify-content: space-between;
  gap: 12px;
  font-size: 13px;
  color: #cbd5e1;
  margin-bottom: 6px;
}

.live-dot {
  margin-left: 8px;
  color: #4ade80;
}

.error-text {
  color: #f87171;
}

.hint-text {
  color: #8b98a5;
  font-size: 12px;
}

.account-summary-metrics {
  display: grid;
  grid-template-columns: repeat(2, 1fr);
  gap: 10px 16px;
}

.metric-value-sm {
  font-size: 16px;
  font-weight: 600;
}

.account-next {
  margin-top: 10px;
  font-size: 12px;
  color: #8b98a5;
}
</style>
