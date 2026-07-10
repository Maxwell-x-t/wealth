<script setup>
import { computed, onMounted, reactive, ref, watch } from 'vue'
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
  currencyView: null,
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
    })
  } catch (error) {
    result.value = null
    message.error(error.response?.data?.detail || '回测加载失败')
  } finally {
    loading.value = false
  }
}

onMounted(loadBacktest)

let reloadTimer = null
watch(
  () => [form.start, form.end],
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
      axisLabel: { color: '#94a3b8', interval: Math.floor(labels.length / 8) },
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
          以 ^IXIC / ^GSPC 月收盘价模拟建仓+定投；大陆按 CNY、香港按 USD 分别统计（不折算汇率）
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
        <NFormItem label="图表币种">
          <NSelect v-model:value="form.currencyView" :options="currencyOptions" style="width: 160px" />
        </NFormItem>
        <NButton quaternary type="primary" @click="loadBacktest">刷新</NButton>
      </NForm>
      <p class="hint-text">
        投入金额与建仓/定投节奏读取「参数配置」；组合按纳指/标普目标比例分配。每月末按收盘价买入，仅为历史参考，不代表 QDII 实际收益。
      </p>
    </div>

    <template v-if="result">
      <div class="metric-grid">
        <div class="metric-card">
          <div class="metric-label">回测区间</div>
          <div class="metric-value" style="font-size: 16px">
            {{ result.start_date }} ~ {{ result.end_date }}
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
</style>
