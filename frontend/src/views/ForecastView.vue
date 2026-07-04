<script setup>
import { computed, onMounted, reactive, ref, watch } from 'vue'
import {
  NButton,
  NCheckbox,
  NDataTable,
  NForm,
  NFormItem,
  NInputNumber,
  NSelect,
  NSpin,
} from 'naive-ui'
import { getConfig, getRiskSimulation, getWealthForecast } from '../api/client'
import { formatMoney, formatPercent } from '../utils/format'

const loading = ref(true)
const forecast = ref(null)
const risk = ref(null)

const form = reactive({
  years: 20,
  pessimistic: 4,
  neutral: 8,
  optimistic: 12,
  useInflation: false,
  inflationPct: 2,
  useMonteCarlo: false,
  mcVolatility: 15,
  mcPaths: 500,
})

const yearOptions = [
  { label: '5 年', value: 5 },
  { label: '10 年', value: 10 },
  { label: '15 年', value: 15 },
  { label: '20 年', value: 20 },
  { label: '30 年', value: 30 },
]

const scenarioColors = {
  pessimistic: '#e88080',
  neutral: '#4f8cff',
  optimistic: '#4ade80',
}

const assetKey = computed(() => (form.useInflation ? 'assets_real_cny' : 'assets_cny'))
const finalAssetKey = computed(() =>
  form.useInflation ? 'final_assets_real_cny' : 'final_assets_cny',
)
const finalPrincipalKey = computed(() =>
  form.useInflation ? 'final_principal_real_cny' : 'final_principal_cny',
)
const finalProfitKey = computed(() =>
  form.useInflation ? 'final_profit_real_cny' : 'final_profit_cny',
)
const finalReturnKey = computed(() =>
  form.useInflation ? 'final_return_rate_real' : 'final_return_rate',
)
const mcKey = (pct) => (form.useInflation ? `p${pct}_real_cny` : `p${pct}_cny`)
const mcFinalKey = (pct) => (form.useInflation ? `final_p${pct}_real_cny` : `final_p${pct}_cny`)

async function loadDefaults() {
  const config = await getConfig()
  form.years = config.forecast_years || 20
  form.pessimistic = config.forecast_return_pessimistic ?? 4
  form.neutral = config.forecast_return_neutral ?? 8
  form.optimistic = config.forecast_return_optimistic ?? 12
  form.inflationPct = config.forecast_inflation_pct ?? 2
  form.mcVolatility = config.forecast_mc_volatility ?? 15
  form.mcPaths = config.forecast_mc_paths ?? 500
}

async function loadForecast() {
  loading.value = true
  try {
    const params = {
      years: form.years,
      pessimistic: form.pessimistic,
      neutral: form.neutral,
      optimistic: form.optimistic,
      use_inflation: form.useInflation,
      use_monte_carlo: form.useMonteCarlo,
    }
    if (form.useInflation) params.inflation_pct = form.inflationPct
    if (form.useMonteCarlo) {
      params.mc_volatility = form.mcVolatility
      params.mc_paths = form.mcPaths
    }
    const [forecastData, riskData] = await Promise.all([
      getWealthForecast(params),
      getRiskSimulation({
        years: form.years,
        recovery_return: form.neutral,
        drawdowns: '30,40,50',
      }),
    ])
    forecast.value = forecastData
    risk.value = riskData
  } finally {
    loading.value = false
  }
}

onMounted(async () => {
  await loadDefaults()
  await loadForecast()
})

let reloadTimer = null
watch(
  () => [
    form.years,
    form.pessimistic,
    form.neutral,
    form.optimistic,
    form.useInflation,
    form.inflationPct,
    form.useMonteCarlo,
    form.mcVolatility,
    form.mcPaths,
  ],
  () => {
    clearTimeout(reloadTimer)
    reloadTimer = setTimeout(loadForecast, 300)
  },
)

const chartOption = computed(() => {
  if (!forecast.value?.scenarios?.length) return null
  const labels = forecast.value.scenarios[0].points.map((p) => p.year_label)
  const key = assetKey.value
  const series = forecast.value.scenarios.map((scenario) => ({
    name: `${scenario.label} ${scenario.annual_return_pct}%`,
    type: 'line',
    smooth: true,
    data: scenario.points.map((p) => p[key] ?? p.assets_cny),
    color: scenarioColors[scenario.key],
  }))

  if (!form.useMonteCarlo) {
    const principalKey = form.useInflation ? 'principal_real_cny' : 'principal_cny'
    series.push({
      name: form.useInflation ? '累计本金（实际）' : '累计本金',
      type: 'line',
      smooth: true,
      data: forecast.value.scenarios[0].points.map((p) => p[principalKey] ?? p.principal_cny),
      color: '#94a3b8',
      lineStyle: { type: 'dashed' },
    })
  }

  if (form.useMonteCarlo && forecast.value.monte_carlo) {
    const mc = forecast.value.monte_carlo
    series.push(
      {
        name: 'MC P10（较差）',
        type: 'line',
        smooth: true,
        data: mc.points.map((p) => p[mcKey(10)] ?? p.p10_cny),
        color: '#f87171',
        lineStyle: { type: 'dotted' },
      },
      {
        name: 'MC 中位数',
        type: 'line',
        smooth: true,
        data: mc.points.map((p) => p[mcKey(50)] ?? p.p50_cny),
        color: '#a78bfa',
      },
      {
        name: 'MC P90（较好）',
        type: 'line',
        smooth: true,
        data: mc.points.map((p) => p[mcKey(90)] ?? p.p90_cny),
        color: '#34d399',
        lineStyle: { type: 'dotted' },
      },
    )
  }

  return {
    backgroundColor: 'transparent',
    tooltip: {
      trigger: 'axis',
      valueFormatter: (value) => formatMoney(value),
    },
    legend: { textStyle: { color: '#cbd5e1' }, top: 0 },
    grid: { left: 60, right: 24, top: 56, bottom: 32 },
    xAxis: {
      type: 'category',
      data: labels,
      axisLabel: { color: '#94a3b8' },
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

const summaryColumns = computed(() => {
  const unit = form.useInflation ? '（实际）' : ''
  return [
    { title: '情景', key: 'label', width: 80 },
    {
      title: '年化收益',
      key: 'annual_return_pct',
      width: 90,
      render: (row) => `${row.annual_return_pct}%`,
    },
    {
      title: `预计资产${unit}`,
      key: 'final_assets',
      render: (row) => formatMoney(row[finalAssetKey.value] ?? row.final_assets_cny),
    },
    {
      title: `预计本金${unit}`,
      key: 'final_principal',
      render: (row) => formatMoney(row[finalPrincipalKey.value] ?? row.final_principal_cny),
    },
    {
      title: `预计收益${unit}`,
      key: 'final_profit',
      render: (row) => formatMoney(row[finalProfitKey.value] ?? row.final_profit_cny),
    },
    {
      title: '预计收益率',
      key: 'final_return_rate',
      render: (row) => formatPercent(row[finalReturnKey.value] ?? row.final_return_rate),
    },
  ]
})

const riskColumns = [
  { title: '情景', key: 'label', width: 140 },
  {
    title: '跌幅',
    key: 'drawdown_pct',
    width: 80,
    render: (row) => `${row.drawdown_pct}%`,
  },
  {
    title: '崩盘后资产',
    key: 'assets_after_crash_cny',
    render: (row) => formatMoney(row.assets_after_crash_cny),
  },
  {
    title: '浮亏',
    key: 'loss_cny',
    render: (row) => formatMoney(row.loss_cny),
  },
  {
    title: '恢复年限',
    key: 'recovery_years',
    width: 100,
    render: (row) => (row.recovery_years == null ? `>${row.horizon_years}年` : `${row.recovery_years} 年`),
  },
  {
    title: '终点资产',
    key: 'final_assets_cny',
    render: (row) => formatMoney(row.final_assets_cny),
  },
  {
    title: '终点收益率',
    key: 'final_return_rate',
    render: (row) => formatPercent(row.final_return_rate),
  },
]

const riskChartOption = computed(() => {
  if (!risk.value?.custom_scenarios?.length) return null
  const labels = risk.value.custom_scenarios[0].points.map((p) =>
    p.year_offset === 0 ? '崩盘后' : `${p.year_offset}年后`,
  )
  const colors = ['#e88080', '#f0a060', '#f5d76e']
  const series = risk.value.custom_scenarios.map((scenario, index) => ({
    name: scenario.label,
    type: 'line',
    smooth: true,
    data: scenario.points.map((p) => p.assets_cny),
    color: colors[index % colors.length],
  }))
  return {
    backgroundColor: 'transparent',
    tooltip: {
      trigger: 'axis',
      valueFormatter: (value) => formatMoney(value),
    },
    legend: { textStyle: { color: '#cbd5e1' }, top: 0 },
    grid: { left: 60, right: 24, top: 48, bottom: 32 },
    xAxis: {
      type: 'category',
      data: labels,
      axisLabel: { color: '#94a3b8' },
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
</script>

<template>
  <NSpin :show="loading">
    <div class="header-row">
      <div>
        <h1 class="page-title">财富预测</h1>
        <p class="page-desc">确定性复利路径；可勾选通胀（购买力）与蒙特卡洛（波动区间）</p>
      </div>
    </div>

    <div class="panel controls-panel">
      <NForm label-placement="left" label-width="90" class="controls-form">
        <NFormItem label="预测年限">
          <NSelect v-model:value="form.years" :options="yearOptions" style="width: 140px" />
        </NFormItem>
        <NFormItem label="悲观 %">
          <NInputNumber v-model:value="form.pessimistic" :step="0.5" style="width: 120px" />
        </NFormItem>
        <NFormItem label="中性 %">
          <NInputNumber v-model:value="form.neutral" :step="0.5" style="width: 120px" />
        </NFormItem>
        <NFormItem label="乐观 %">
          <NInputNumber v-model:value="form.optimistic" :step="0.5" style="width: 120px" />
        </NFormItem>
        <NButton quaternary type="primary" @click="loadDefaults().then(loadForecast)">
          恢复默认
        </NButton>
      </NForm>

      <div class="toggle-row">
        <NCheckbox v-model:checked="form.useInflation">通胀调整（显示实际购买力）</NCheckbox>
        <NFormItem v-if="form.useInflation" label="通胀 %" label-placement="left" :show-feedback="false">
          <NInputNumber v-model:value="form.inflationPct" :min="0" :max="20" :step="0.1" style="width: 110px" />
        </NFormItem>
      </div>
      <div class="toggle-row">
        <NCheckbox v-model:checked="form.useMonteCarlo">蒙特卡洛（波动区间）</NCheckbox>
        <template v-if="form.useMonteCarlo">
          <NFormItem label="波动率 %" label-placement="left" :show-feedback="false">
            <NInputNumber v-model:value="form.mcVolatility" :min="0" :max="80" :step="1" style="width: 110px" />
          </NFormItem>
          <NFormItem label="路径数" label-placement="left" :show-feedback="false">
            <NInputNumber v-model:value="form.mcPaths" :min="50" :max="2000" :step="50" style="width: 110px" />
          </NFormItem>
        </template>
      </div>
      <p class="hint-text">
        默认参数可在「参数配置」保存。通胀把名义金额折成今天购买力；蒙特卡洛以中性收益为均值、随机波动模拟多条路径，展示 P10 / 中位数 / P90。
      </p>
    </div>

    <template v-if="forecast">
      <div class="metric-grid">
        <div class="metric-card">
          <div class="metric-label">当前资产</div>
          <div class="metric-value">{{ formatMoney(forecast.current_assets_cny) }}</div>
        </div>
        <div class="metric-card">
          <div class="metric-label">当前净投入</div>
          <div class="metric-value">{{ formatMoney(forecast.current_net_investment_cny) }}</div>
        </div>
        <div class="metric-card">
          <div class="metric-label">预测年限</div>
          <div class="metric-value">{{ forecast.years }} 年</div>
        </div>
        <div class="metric-card">
          <div class="metric-label">
            {{ form.useMonteCarlo ? 'MC 中位数终点' : '中性终点资产' }}
            <span v-if="form.useInflation">（实际）</span>
          </div>
          <div class="metric-value">
            <template v-if="form.useMonteCarlo && forecast.monte_carlo">
              {{ formatMoney(forecast.monte_carlo[mcFinalKey(50)] ?? forecast.monte_carlo.final_p50_cny) }}
            </template>
            <template v-else>
              {{
                formatMoney(
                  forecast.scenarios.find((s) => s.key === 'neutral')?.[finalAssetKey] ??
                    forecast.scenarios.find((s) => s.key === 'neutral')?.final_assets_cny,
                )
              }}
            </template>
          </div>
        </div>
      </div>

      <div class="panel" style="margin-bottom: 16px">
        <h3>
          资产预测曲线
          <span v-if="form.useInflation" class="badge">实际购买力</span>
          <span v-if="form.useMonteCarlo" class="badge">蒙特卡洛</span>
        </h3>
        <VChart v-if="chartOption" :option="chartOption" style="height: 360px" />
      </div>

      <div class="panel" style="margin-bottom: 16px">
        <h3>{{ forecast.years }} 年后终点对比</h3>
        <NDataTable
          :columns="summaryColumns"
          :data="forecast.scenarios"
          :bordered="false"
          size="small"
        />
      </div>

      <div v-if="form.useMonteCarlo && forecast.monte_carlo" class="panel" style="margin-bottom: 16px">
        <h3>蒙特卡洛终点分布（均值 = 中性 {{ forecast.monte_carlo.mean_return_pct }}%，波动 {{ forecast.monte_carlo.volatility_pct }}%，{{ forecast.monte_carlo.paths }} 条路径）</h3>
        <div class="metric-grid">
          <div class="metric-card">
            <div class="metric-label">P10 较差</div>
            <div class="metric-value" style="font-size: 18px">
              {{ formatMoney(forecast.monte_carlo[mcFinalKey(10)] ?? forecast.monte_carlo.final_p10_cny) }}
            </div>
          </div>
          <div class="metric-card">
            <div class="metric-label">中位数</div>
            <div class="metric-value" style="font-size: 18px">
              {{ formatMoney(forecast.monte_carlo[mcFinalKey(50)] ?? forecast.monte_carlo.final_p50_cny) }}
            </div>
          </div>
          <div class="metric-card">
            <div class="metric-label">P90 较好</div>
            <div class="metric-value" style="font-size: 18px">
              {{ formatMoney(forecast.monte_carlo[mcFinalKey(90)] ?? forecast.monte_carlo.final_p90_cny) }}
            </div>
          </div>
        </div>
      </div>
    </template>

    <template v-if="risk">
      <div class="panel" style="margin-bottom: 16px">
        <h3>风险模拟（悲观路径）</h3>
        <p class="hint-text" style="margin-bottom: 12px">
          假设当前资产立刻下跌，之后按中性年化 {{ risk.recovery_return_pct }}% 继续定投（年投入约
          {{ formatMoney(risk.annual_contribution_cny) }}）。恢复年限指回到崩盘前资产所需时间。
        </p>
        <VChart v-if="riskChartOption" :option="riskChartOption" style="height: 300px; margin-bottom: 16px" />
        <h4>自定义跌幅</h4>
        <NDataTable
          :columns="riskColumns"
          :data="risk.custom_scenarios"
          :bordered="false"
          size="small"
          style="margin-bottom: 16px"
        />
        <h4>历史熊市参考</h4>
        <NDataTable
          :columns="riskColumns"
          :data="risk.historical_scenarios"
          :bordered="false"
          size="small"
        />
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

.toggle-row {
  display: flex;
  flex-wrap: wrap;
  align-items: center;
  gap: 12px 16px;
  margin-top: 12px;
}

.toggle-row :deep(.n-form-item) {
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

h4 {
  margin: 0 0 8px;
  font-size: 13px;
  font-weight: 600;
  color: #94a3b8;
}

.badge {
  display: inline-block;
  margin-left: 8px;
  padding: 2px 8px;
  border-radius: 999px;
  font-size: 12px;
  font-weight: 500;
  color: #93c5fd;
  background: #1e3a5f;
}
</style>
