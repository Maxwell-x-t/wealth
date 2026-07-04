<script setup>
import { computed, onMounted, reactive, ref, watch } from 'vue'
import {
  NButton,
  NDataTable,
  NForm,
  NFormItem,
  NInputNumber,
  NSelect,
  NSpin,
} from 'naive-ui'
import { getConfig, getWealthForecast } from '../api/client'
import { formatMoney, formatPercent } from '../utils/format'

const loading = ref(true)
const forecast = ref(null)

const form = reactive({
  years: 20,
  pessimistic: 4,
  neutral: 8,
  optimistic: 12,
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

async function loadDefaults() {
  const config = await getConfig()
  form.years = config.forecast_years || 20
  form.pessimistic = config.forecast_return_pessimistic ?? 4
  form.neutral = config.forecast_return_neutral ?? 8
  form.optimistic = config.forecast_return_optimistic ?? 12
}

async function loadForecast() {
  loading.value = true
  try {
    forecast.value = await getWealthForecast({
      years: form.years,
      pessimistic: form.pessimistic,
      neutral: form.neutral,
      optimistic: form.optimistic,
    })
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
  () => [form.years, form.pessimistic, form.neutral, form.optimistic],
  () => {
    clearTimeout(reloadTimer)
    reloadTimer = setTimeout(loadForecast, 300)
  },
)

const chartOption = computed(() => {
  if (!forecast.value?.scenarios?.length) return null
  const labels = forecast.value.scenarios[0].points.map((p) => p.year_label)
  const series = forecast.value.scenarios.map((scenario) => ({
    name: `${scenario.label} ${scenario.annual_return_pct}%`,
    type: 'line',
    smooth: true,
    data: scenario.points.map((p) => p.assets_cny),
    color: scenarioColors[scenario.key],
  }))
  const principal = forecast.value.scenarios[0].points.map((p) => p.principal_cny)
  series.push({
    name: '累计本金',
    type: 'line',
    smooth: true,
    data: principal,
    color: '#94a3b8',
    lineStyle: { type: 'dashed' },
  })
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

const summaryColumns = [
  { title: '情景', key: 'label', width: 80 },
  {
    title: '年化收益',
    key: 'annual_return_pct',
    width: 90,
    render: (row) => `${row.annual_return_pct}%`,
  },
  {
    title: '预计资产',
    key: 'final_assets_cny',
    render: (row) => formatMoney(row.final_assets_cny),
  },
  {
    title: '预计本金',
    key: 'final_principal_cny',
    render: (row) => formatMoney(row.final_principal_cny),
  },
  {
    title: '预计收益',
    key: 'final_profit_cny',
    render: (row) => formatMoney(row.final_profit_cny),
  },
  {
    title: '预计收益率',
    key: 'final_return_rate',
    render: (row) => formatPercent(row.final_return_rate),
  },
]
</script>

<template>
  <NSpin :show="loading">
    <div class="header-row">
      <div>
        <h1 class="page-title">财富预测</h1>
        <p class="page-desc">基于当前资产、净投入与未来投资计划，按年化收益做确定性复利推演</p>
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
      <p class="hint-text">默认值可在「参数配置」中修改并保存；此处调整仅影响当前页面试算。</p>
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
          <div class="metric-label">中性终点资产</div>
          <div class="metric-value">
            {{ formatMoney(forecast.scenarios.find((s) => s.key === 'neutral')?.final_assets_cny) }}
          </div>
        </div>
      </div>

      <div class="panel" style="margin-bottom: 16px">
        <h3>资产预测曲线</h3>
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
</style>
