<script setup>
import { computed, h, onMounted, reactive, ref, watch } from 'vue'
import {
  NButton,
  NDataTable,
  NDatePicker,
  NForm,
  NFormItem,
  NSelect,
  NSpin,
  useMessage,
} from 'naive-ui'
import { getHistoricalBacktest } from '../api/client'
import { formatMoney, formatPercent } from '../utils/format'

const message = useMessage()
const loading = ref(true)
const result = ref(null)

const today = new Date()
const defaultStart = new Date(2000, 0, 1)

const form = reactive({
  start: defaultStart.getTime(),
  end: today.getTime(),
  frequency: 'weekly',
  currencyView: null,
  centerCenters: [0, 5, 8, 10],
})

const presetOptions = [
  { label: '2000 年至今', value: '2000' },
  { label: '2008 危机后 (2009)', value: '2009' },
  { label: '2015 年至今', value: '2015' },
  { label: '2020 疫情后 (2020-04)', value: '2020-04' },
]

const currencyOptions = [
  { label: '全部币种', value: null },
  { label: '人民币 CNY（大陆）', value: 'CNY' },
  { label: '美元 USD（香港）', value: 'USD' },
]

const frequencyOptions = [
  { label: '周频（默认）', value: 'weekly' },
  { label: '日频', value: 'daily' },
]

const centerOptions = [
  { label: '中性区 0%', value: 0 },
  { label: '中性区 5%', value: 5 },
  { label: '中性区 8%（默认）', value: 8 },
  { label: '中性区 10%', value: 10 },
]

const CENTER_COLORS = {
  0: '#60a5fa',
  5: '#f59e0b',
  8: '#f97316',
  10: '#a78bfa',
}

function applyPreset(value) {
  if (!value) return
  if (value === '2020-04') {
    form.start = new Date(2020, 3, 1).getTime()
  } else {
    form.start = new Date(Number(value), 0, 1).getTime()
  }
  form.end = today.getTime()
}

async function loadBacktest() {
  loading.value = true
  try {
    result.value = await getHistoricalBacktest({
      start: new Date(form.start).toISOString().slice(0, 10),
      end: new Date(form.end).toISOString().slice(0, 10),
      frequency: form.frequency,
    })
  } catch (error) {
    result.value = null
    const detail = error.response?.data?.detail
    if (error.code === 'ECONNABORTED' || String(error.message || '').includes('timeout')) {
      message.error('回测超时，请缩小区间后重试')
    } else {
      message.error(detail || '回测加载失败')
    }
  } finally {
    loading.value = false
  }
}

onMounted(loadBacktest)

let reloadTimer = null
watch(
  () => [form.start, form.end, form.frequency],
  () => {
    clearTimeout(reloadTimer)
    reloadTimer = setTimeout(loadBacktest, 300)
  },
)

const summaryByCurrency = computed(() => {
  const map = {}
  for (const row of result.value?.currency_summaries || []) {
    map[row.currency] = row
  }
  return map
})

const chartLabelInterval = computed(() => {
  const count = result.value?.points?.length || 0
  if (!count) return 0
  if (form.frequency === 'daily') return Math.max(1, Math.floor(count / 12))
  return Math.floor(count / 8)
})

const chartOption = computed(() => {
  if (!result.value?.points?.length) return null
  const labels = result.value.points.map((p) => p.label)
  const currencies = form.currencyView ? [form.currencyView] : ['CNY', 'USD']
  const colors = { CNY: '#4f8cff', USD: '#4ade80' }
  const series = []

  for (const currency of currencies) {
    const summary = summaryByCurrency.value[currency]
    if (!summary && currency === 'CNY' && !result.value.points.some((p) => p.currencies?.CNY?.principal > 0)) {
      continue
    }
    series.push({
      name: `${currency} 资产`,
      type: 'line',
      smooth: true,
      data: result.value.points.map((p) => p.currencies?.[currency]?.assets ?? null),
      color: colors[currency],
    })
    series.push({
      name: `${currency} 本金`,
      type: 'line',
      smooth: true,
      data: result.value.points.map((p) => p.currencies?.[currency]?.principal ?? null),
      color: colors[currency],
      lineStyle: { type: 'dashed', opacity: 0.65 },
    })
  }

  return {
    backgroundColor: 'transparent',
    tooltip: {
      trigger: 'axis',
      formatter: (items) => {
        const lines = [items[0]?.axisValueLabel || items[0]?.axisValue]
        for (const item of items) {
          if (item.value == null) continue
          const cur = item.seriesName.startsWith('CNY') ? 'CNY' : 'USD'
          lines.push(`${item.seriesName}: ${formatMoney(item.value, cur)}`)
        }
        return lines.join('<br/>')
      },
    },
    legend: { textStyle: { color: '#cbd5e1' }, top: 0 },
    grid: { left: 72, right: 24, top: 56, bottom: 32 },
    xAxis: {
      type: 'category',
      data: labels,
      axisLabel: { color: '#94a3b8', interval: chartLabelInterval.value },
    },
    yAxis: {
      type: 'value',
      axisLabel: {
        color: '#94a3b8',
        formatter: (value) => {
          if (Math.abs(value) >= 10000) return `${(value / 10000).toFixed(0)}万`
          return String(value)
        },
      },
      splitLine: { lineStyle: { color: '#1f2937' } },
    },
    series,
  }
})

const compareCurrency = computed(() => {
  if (form.currencyView) return form.currencyView
  const withData = result.value?.currency_summaries?.find((s) => s.total_contributed > 0)
  return withData?.currency || 'CNY'
})

const selectedCenterVariants = computed(() => {
  const all = result.value?.ma_center_comparisons || []
  const selected = new Set(form.centerCenters.map(Number))
  return all
    .filter((item) => selected.has(Number(item.center_pct)))
    .sort((a, b) => a.center_pct - b.center_pct)
})

const maCompareTableColumns = computed(() => {
  const cols = [
    { title: '指标', key: 'label', width: 100 },
    { title: '关因子', key: 'base' },
  ]
  for (const variant of selectedCenterVariants.value) {
    const c = Number(variant.center_pct)
    cols.push({ title: `中性区 ${c}%`, key: `c${c}` })
  }
  return cols
})

const maCompareTableData = computed(() => {
  const cur = compareCurrency.value
  const base = summaryByCurrency.value[cur]
  const variants = selectedCenterVariants.value
  if (!base || !variants.length) return []

  const rows = [
    {
      label: '期末资产',
      base: formatMoney(base.final_assets, cur),
      better: (v, b) => v >= b,
      pick: (s) => s.final_assets,
      format: (v) => formatMoney(v, cur),
    },
    {
      label: '收益率',
      base: formatPercent(base.return_rate),
      better: (v, b) => (v ?? 0) >= (b ?? 0),
      pick: (s) => s.return_rate,
      format: (v) => formatPercent(v),
    },
    {
      label: '年化 CAGR',
      base: formatPercent(base.cagr),
      better: (v, b) => (v ?? 0) >= (b ?? 0),
      pick: (s) => s.cagr,
      format: (v) => formatPercent(v),
    },
    {
      label: '最大回撤',
      base: `${base.max_drawdown_pct}%`,
      better: (v, b) => v <= b,
      pick: (s) => s.max_drawdown_pct,
      format: (v) => `${v}%`,
    },
  ]

  return rows.map((row) => {
    const out = { label: row.label, base: row.base }
    for (const variant of variants) {
      const summary = (variant.currency_summaries || []).find((s) => s.currency === cur)
      const key = `c${Number(variant.center_pct)}`
      if (!summary) {
        out[key] = '—'
        continue
      }
      const value = row.pick(summary)
      const deltaBetter = row.better(value, row.pick(base))
      out[key] = row.format(value)
      out[`${key}_class`] = deltaBetter ? 'positive' : 'negative'
    }
    return out
  })
})

function renderMaCompareCell(row, key) {
  const text = row[key]
  const cls = row[`${key}_class`]
  if (!cls) return text
  return h('span', { class: cls }, text)
}

const maCompareColumnsRendered = computed(() =>
  maCompareTableColumns.value.map((col) => {
    if (col.key === 'label' || col.key === 'base') return col
    return {
      ...col,
      render: (row) => renderMaCompareCell(row, col.key),
    }
  }),
)

const comparisonChartOption = computed(() => {
  const variants = selectedCenterVariants.value
  if (!result.value?.points?.length || !variants.length) return null
  const cur = compareCurrency.value
  const labels = result.value.points.map((p) => p.label)
  const baseData = result.value.points.map((p) => p.currencies?.[cur]?.assets ?? null)
  if (!baseData.some((v) => v)) return null

  const series = [
    {
      name: '关因子',
      type: 'line',
      smooth: true,
      showSymbol: false,
      data: baseData,
      color: '#94a3b8',
      lineStyle: { width: 2 },
    },
  ]
  for (const variant of variants) {
    const c = Number(variant.center_pct)
    series.push({
      name: `中性区 ${c}%`,
      type: 'line',
      smooth: true,
      showSymbol: false,
      data: (variant.points || []).map((p) => p.currencies?.[cur]?.assets ?? null),
      color: CENTER_COLORS[c] || '#f59e0b',
      lineStyle: { width: c === 8 ? 2.5 : 1.8 },
    })
  }

  return {
    backgroundColor: 'transparent',
    tooltip: {
      trigger: 'axis',
      formatter: (items) => {
        const lines = [items[0]?.axisValueLabel || items[0]?.axisValue]
        for (const item of items) {
          if (item.value == null) continue
          lines.push(`${item.marker}${item.seriesName}: ${formatMoney(item.value, cur)}`)
        }
        return lines.join('<br/>')
      },
    },
    legend: { textStyle: { color: '#cbd5e1' }, top: 0 },
    grid: { left: 72, right: 24, top: 56, bottom: 32 },
    xAxis: {
      type: 'category',
      data: labels,
      axisLabel: { color: '#94a3b8', interval: chartLabelInterval.value },
    },
    yAxis: {
      type: 'value',
      axisLabel: {
        color: '#94a3b8',
        formatter: (value) => {
          if (Math.abs(value) >= 10000) return `${(value / 10000).toFixed(0)}万`
          return String(value)
        },
      },
      splitLine: { lineStyle: { color: '#1f2937' } },
    },
    series,
  }
})

const vixBaselineVariant = computed(() => {
  const all = result.value?.ma_center_comparisons || []
  return all.find((item) => Number(item.center_pct) === 8) || null
})

const vixOnRun = computed(() => result.value?.vix_on || null)

const vixComparisonChartOption = computed(() => {
  const base = vixBaselineVariant.value
  const vixRun = vixOnRun.value
  if (!base?.points?.length || !vixRun?.points?.length) return null
  if (!vixRun?.settings?.enabled) return null

  const cur = compareCurrency.value
  const labels = result.value.points.map((p) => p.label)
  const baseData = base.points.map((p) => p.currencies?.[cur]?.assets ?? null)
  const vixData = vixRun.points.map((p) => p.currencies?.[cur]?.assets ?? null)
  if (!baseData.some((v) => v)) return null

  return {
    backgroundColor: 'transparent',
    tooltip: {
      trigger: 'axis',
      formatter: (items) => {
        const lines = [items[0]?.axisValueLabel || items[0]?.axisValue]
        for (const item of items) {
          if (item.value == null) continue
          lines.push(`${item.marker}${item.seriesName}: ${formatMoney(item.value, cur)}`)
        }
        return lines.join('<br/>')
      },
    },
    legend: { textStyle: { color: '#cbd5e1' }, top: 0 },
    grid: { left: 72, right: 24, top: 56, bottom: 32 },
    xAxis: {
      type: 'category',
      data: labels,
      axisLabel: { color: '#94a3b8', interval: chartLabelInterval.value },
    },
    yAxis: {
      type: 'value',
      axisLabel: {
        color: '#94a3b8',
        formatter: (value) => {
          if (Math.abs(value) >= 10000) return `${(value / 10000).toFixed(0)}万`
          return String(value)
        },
      },
      splitLine: { lineStyle: { color: '#1f2937' } },
    },
    series: [
      {
        name: '关危机加仓 资产',
        type: 'line',
        smooth: true,
        showSymbol: false,
        data: baseData,
        color: '#94a3b8',
        lineStyle: { width: 2 },
      },
      {
        name: '开危机加仓 资产',
        type: 'line',
        smooth: true,
        showSymbol: false,
        data: vixData,
        color: '#f59e0b',
        lineStyle: { width: 2.2 },
      },
    ],
  }
})

const vixCompareRows = computed(() => {
  const cur = compareCurrency.value
  const base = vixBaselineVariant.value
  const vixRun = vixOnRun.value
  if (!base || !vixRun) return []
  if (!vixRun?.settings?.enabled) return []

  const baseSummary = (base.currency_summaries || []).find((s) => s.currency === cur)
  const vixSummary = (vixRun.currency_summaries || []).find((s) => s.currency === cur)
  if (!baseSummary || !vixSummary) return []

  const rows = [
    {
      label: '期末资产',
      base: formatMoney(baseSummary.final_assets, cur),
      vix: formatMoney(vixSummary.final_assets, cur),
      delta: formatMoney(vixSummary.final_assets - baseSummary.final_assets, cur),
      positive: vixSummary.final_assets >= baseSummary.final_assets,
    },
    {
      label: '收益率',
      base: formatPercent(baseSummary.return_rate),
      vix: formatPercent(vixSummary.return_rate),
      delta: `${((vixSummary.return_rate ?? 0) - (baseSummary.return_rate ?? 0)).toFixed(2)} pt`,
      positive: (vixSummary.return_rate ?? 0) >= (baseSummary.return_rate ?? 0),
    },
    {
      label: '年化 CAGR',
      base: formatPercent(baseSummary.cagr),
      vix: formatPercent(vixSummary.cagr),
      delta: `${((vixSummary.cagr ?? 0) - (baseSummary.cagr ?? 0)).toFixed(2)} pt`,
      positive: (vixSummary.cagr ?? 0) >= (baseSummary.cagr ?? 0),
    },
    {
      label: '最大回撤',
      base: `${baseSummary.max_drawdown_pct}%`,
      vix: `${vixSummary.max_drawdown_pct}%`,
      delta: `${(vixSummary.max_drawdown_pct - baseSummary.max_drawdown_pct).toFixed(2)} pt`,
      positive: vixSummary.max_drawdown_pct <= baseSummary.max_drawdown_pct,
    },
  ]

  return rows
})

const summaryColumns = [
  { title: '账户', key: 'account_label', width: 80 },
  { title: '币种', key: 'currency', width: 70 },
  {
    title: '累计投入',
    key: 'total_contributed',
    render: (row) => formatMoney(row.total_contributed, row.currency),
  },
  {
    title: '期末资产',
    key: 'final_assets',
    render: (row) => formatMoney(row.final_assets, row.currency),
  },
  {
    title: '收益',
    key: 'profit',
    render: (row) => formatMoney(row.profit, row.currency),
  },
  {
    title: '收益率',
    key: 'return_rate',
    render: (row) => formatPercent(row.return_rate),
  },
  {
    title: '年化 CAGR',
    key: 'cagr',
    width: 100,
    render: (row) => formatPercent(row.cagr),
  },
  {
    title: '最大回撤',
    key: 'max_drawdown_pct',
    width: 100,
    render: (row) => `${row.max_drawdown_pct}%`,
  },
]

const yearlyColumns = [
  { title: '年份', key: 'year', width: 70 },
  {
    title: 'CNY 合计',
    key: 'cny_total',
    render: (row) => formatMoney(row.cny_total, 'CNY'),
  },
  {
    title: 'CNY 建仓',
    key: 'cny_building',
    render: (row) => formatMoney(row.cny_building, 'CNY'),
  },
  {
    title: 'CNY 定投',
    key: 'cny_dca',
    render: (row) => formatMoney(row.cny_dca, 'CNY'),
  },
  {
    title: 'USD 合计',
    key: 'usd_total',
    render: (row) => formatMoney(row.usd_total, 'USD'),
  },
  {
    title: 'USD 建仓',
    key: 'usd_building',
    render: (row) => formatMoney(row.usd_building, 'USD'),
  },
  {
    title: 'USD 定投',
    key: 'usd_dca',
    render: (row) => formatMoney(row.usd_dca, 'USD'),
  },
]

const displaySummaries = computed(() => {
  const rows = result.value?.currency_summaries || []
  if (!form.currencyView) return rows
  return rows.filter((row) => row.currency === form.currencyView)
})
</script>

<template>
  <NSpin :show="loading">
    <div class="header-row">
      <div>
        <h1 class="page-title">历史回测</h1>
        <p class="page-desc">
          以 ^IXIC / ^GSPC 收盘价模拟建仓+定投（周频/日频）；大陆按 CNY、香港按 USD 分别统计（不折算汇率）
        </p>
      </div>
    </div>

    <div class="panel controls-panel">
      <NForm label-placement="left" label-width="80" class="controls-form">
        <NFormItem label="快捷区间">
          <NSelect
            :options="presetOptions"
            placeholder="选择预设"
            style="width: 160px"
            @update:value="applyPreset"
          />
        </NFormItem>
        <NFormItem label="开始">
          <NDatePicker v-model:value="form.start" type="date" style="width: 150px" />
        </NFormItem>
        <NFormItem label="结束">
          <NDatePicker v-model:value="form.end" type="date" style="width: 150px" />
        </NFormItem>
        <NFormItem label="回测频率">
          <NSelect v-model:value="form.frequency" :options="frequencyOptions" style="width: 140px" />
        </NFormItem>
        <NFormItem label="图表币种">
          <NSelect v-model:value="form.currencyView" :options="currencyOptions" style="width: 160px" />
        </NFormItem>
        <NFormItem label="中性区对比">
          <NSelect
            v-model:value="form.centerCenters"
            :options="centerOptions"
            multiple
            max-tag-count="responsive"
            style="width: 280px"
          />
        </NFormItem>
        <NButton quaternary type="primary" @click="loadBacktest">刷新</NButton>
      </NForm>
      <p class="hint-text">
        投入金额与建仓/定投节奏读取「指数配置」；组合按纳指/标普目标比例分配。买入按计划周执行，估值按所选频率（周末/每日收盘）；若日频源不可用会自动用本地月线兜底，此时 MA 按约 10 个月窗口计算。仅为历史参考，不代表 QDII 实际收益。
      </p>
    </div>

    <template v-if="result">
      <div class="metric-grid">
        <div class="metric-card">
          <div class="metric-label">回测区间</div>
          <div class="metric-value" style="font-size: 16px">
            {{ result.start_date }} ~ {{ result.end_date }}
            · {{ result.frequency === 'daily' ? '日频' : '周频' }}
            <span v-if="result.price_cadence === 'monthly'" style="color: #f59e0b">
              · 行情为月线兜底
            </span>
            <span v-else-if="result.ma_cadence">
              · MA按{{ result.ma_cadence === 'daily' ? '日' : result.ma_cadence === 'weekly' ? '周' : '月' }}线
            </span>
          </div>
        </div>
        <div class="metric-card">
          <div class="metric-label">指数组合</div>
          <div class="metric-value" style="font-size: 16px">
            纳指 {{ result.nasdaq_weight_pct }}% · 标普 {{ result.sp500_weight_pct }}%
          </div>
        </div>
        <div
          v-for="summary in displaySummaries"
          :key="summary.currency"
          class="metric-card"
        >
          <div class="metric-label">{{ summary.account_label }}（{{ summary.currency }}）期末</div>
          <div class="metric-value" style="font-size: 18px">
            {{ formatMoney(summary.final_assets, summary.currency) }}
          </div>
          <div class="metric-sub">
            投入 {{ formatMoney(summary.total_contributed, summary.currency) }}
            · CAGR {{ formatPercent(summary.cagr) }}
          </div>
        </div>
      </div>

      <div class="panel" style="margin-bottom: 16px">
        <h3>资产曲线（按买入币种）</h3>
        <VChart v-if="chartOption" :option="chartOption" autoresize style="height: 380px" />
      </div>

      <div v-if="comparisonChartOption" class="panel" style="margin-bottom: 16px">
        <h3>均线因子 · 中性区并排对比（{{ compareCurrency }}）</h3>
        <p class="hint-text" style="margin-top: 0; margin-bottom: 12px">
          同一投入节奏下对比「关因子」与不同中性区上移（0/5/8/10%）。偏贵少投、偏便宜从池中补投（预算守恒）；默认配置为 8%。
        </p>
        <VChart :option="comparisonChartOption" autoresize style="height: 360px" />
        <NDataTable
          v-if="maCompareTableData.length"
          :columns="maCompareColumnsRendered"
          :data="maCompareTableData"
          :bordered="false"
          size="small"
          style="margin-top: 16px"
        />
      </div>

      <div v-if="vixComparisonChartOption" class="panel" style="margin-bottom: 16px">
        <h3>危机加仓对比（{{ compareCurrency }}）</h3>
        <p class="hint-text" style="margin-top: 0; margin-bottom: 12px">
          MA(8) 关危机加仓 vs 开危机加仓：需 <b>VIX≥25 且指数回撤≥20%</b> 才追加预算（倍数×当次定投，不走 MA 池）。回撤 20/30/40% × VIX 25–35 / ≥35 → ×0.25/0.4/0.5/0.75/0.75/1.0；单次最多 ×1.0，年度上限为常规定投的 50%。
        </p>
        <VChart :option="vixComparisonChartOption" autoresize style="height: 360px" />
        <div v-if="vixCompareRows.length" class="vix-compare-grid">
          <div class="vix-compare-head">
            <span>指标</span><span>关危机</span><span>开危机</span><span>差异</span>
          </div>
          <div v-for="row in vixCompareRows" :key="row.label" class="vix-compare-row">
            <span class="vix-compare-label">{{ row.label }}</span>
            <span>{{ row.base }}</span>
            <span>{{ row.vix }}</span>
            <span :class="row.positive ? 'positive' : 'negative'">{{ row.delta }}</span>
          </div>
        </div>
      </div>

      <div class="panel" style="margin-bottom: 16px">
        <h3>分币种结果</h3>
        <NDataTable
          :columns="summaryColumns"
          :data="result.currency_summaries"
          :bordered="false"
          size="small"
        />
      </div>

      <div v-if="result.yearly_contributions?.length" class="panel" style="margin-bottom: 16px">
        <h3>年度投入（建仓 / 定投）</h3>
        <NDataTable
          :columns="yearlyColumns"
          :data="result.yearly_contributions"
          :bordered="false"
          size="small"
        />
      </div>

      <div class="panel">
        <h3>回测使用的计划金额（来自配置）</h3>
        <div class="plan-settings-grid">
          <div v-for="(settings, name) in result.plan_settings" :key="name" class="plan-card">
            <div class="plan-title">{{ name }}（{{ settings.currency }}）</div>
            <div>首月建仓 {{ formatMoney(settings.building_first_month_amount, settings.currency) }}</div>
            <div>每月建仓 {{ formatMoney(settings.building_monthly_amount, settings.currency) }}</div>
            <div>建仓 {{ settings.building_months }} 个月</div>
            <div>定投月额 {{ formatMoney(settings.dca_monthly_amount, settings.currency) }}</div>
          </div>
        </div>
      </div>
    </template>
  </NSpin>
</template>

<style scoped>
.header-row {
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

.controls-panel {
  margin-bottom: 16px;
}

.controls-form {
  display: flex;
  flex-wrap: wrap;
  gap: 8px 16px;
  align-items: center;
}

.controls-form :deep(.n-form-item) {
  margin-bottom: 0;
}

.hint-text {
  margin: 8px 0 0;
  color: #8b98a5;
  font-size: 12px;
}

h3 {
  margin: 0 0 12px;
  font-size: 15px;
  font-weight: 600;
}

.plan-settings-grid {
  display: grid;
  grid-template-columns: repeat(auto-fit, minmax(240px, 1fr));
  gap: 12px;
}

.plan-card {
  padding: 12px 14px;
  border-radius: 8px;
  background: rgba(255, 255, 255, 0.03);
  border: 1px solid rgba(255, 255, 255, 0.06);
  font-size: 13px;
  line-height: 1.7;
  color: #cbd5e1;
}

.plan-title {
  font-weight: 600;
  margin-bottom: 4px;
}

.vix-compare-grid {
  margin-top: 16px;
  font-size: 13px;
}

.vix-compare-head,
.vix-compare-row {
  display: grid;
  grid-template-columns: 1.2fr 1fr 1fr 1fr;
  gap: 8px;
  padding: 8px 4px;
}

.vix-compare-head {
  color: #8b98a5;
  border-bottom: 1px solid rgba(255, 255, 255, 0.08);
}

.vix-compare-row {
  border-bottom: 1px solid rgba(255, 255, 255, 0.04);
  color: #cbd5e1;
}

.vix-compare-label {
  color: #94a3b8;
}

</style>
