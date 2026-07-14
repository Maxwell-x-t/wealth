<script setup>
import { computed, onMounted, ref } from 'vue'
import { NAlert, NDataTable, NSpin, NTag } from 'naive-ui'
import { getDashboard, getDashboardHistory } from '../api/client'
import { formatMoney, formatPercent, formatNumber, formatPrice } from '../utils/format'

const loading = ref(true)
const summary = ref(null)
const history = ref([])

async function loadData() {
  loading.value = true
  try {
    const [dashboard, chartHistory] = await Promise.all([
      getDashboard(),
      getDashboardHistory(),
    ])
    summary.value = dashboard
    history.value = chartHistory
  } finally {
    loading.value = false
  }
}

onMounted(loadData)

const holdingColumns = [
  { title: '代码', key: 'code', width: 90 },
  { title: '名称', key: 'name', width: 120 },
  { title: '账户', key: 'account_name', width: 80 },
  { title: '数量', key: 'quantity', render: (row) => formatNumber(row.quantity, 4) },
  {
    title: '成本价',
    key: 'avg_cost',
    render: (row) => formatPrice(row.avg_cost, row.currency),
  },
  {
    title: '现价',
    key: 'current_price',
    render: (row) => formatPrice(row.current_price, row.currency),
  },
  {
    title: '市值(原币)',
    key: 'market_value',
    render: (row) => formatMoney(row.market_value, row.currency),
  },
  {
    title: '市值(CNY)',
    key: 'market_value_cny',
    render: (row) => formatMoney(row.market_value_cny),
  },
  {
    title: '浮盈(原币)',
    key: 'unrealized_pnl',
    render: (row) => formatMoney(row.unrealized_pnl, row.currency),
  },
  {
    title: '浮盈(CNY)',
    key: 'unrealized_pnl_cny',
    render: (row) => formatMoney(row.unrealized_pnl_cny),
  },
  { title: '收益率', key: 'unrealized_pnl_rate', render: (row) => formatPercent(row.unrealized_pnl_rate) },
  { title: '占比', key: 'weight', render: (row) => formatPercent(row.weight) },
]

const assetChartOption = computed(() => {
  if (!history.value.length) return null
  return {
    backgroundColor: 'transparent',
    tooltip: { trigger: 'axis' },
    legend: { textStyle: { color: '#cbd5e1' } },
    grid: { left: 50, right: 20, top: 40, bottom: 30 },
    xAxis: {
      type: 'category',
      data: history.value.map((item) => item.date),
      axisLabel: { color: '#94a3b8' },
    },
    yAxis: {
      type: 'value',
      axisLabel: { color: '#94a3b8' },
    },
    series: [
      {
        name: '总资产',
        type: 'line',
        smooth: true,
        data: history.value.map((item) => item.total_assets_cny),
        color: '#4f8cff',
      },
      {
        name: '净投入',
        type: 'line',
        smooth: true,
        data: history.value.map((item) => item.net_investment_cny),
        color: '#94a3b8',
      },
    ],
  }
})

const allocationChartOption = computed(() => {
  if (!summary.value?.category_allocations?.length) return null
  const data = summary.value.category_allocations
    .filter((item) => item.current_pct > 0)
    .map((item) => ({ name: item.label, value: item.current_pct }))
  return {
    backgroundColor: 'transparent',
    tooltip: { trigger: 'item' },
    legend: { bottom: 0, textStyle: { color: '#cbd5e1' } },
    series: [
      {
        type: 'pie',
        radius: ['42%', '68%'],
        data,
        label: { color: '#e2e8f0' },
      },
    ],
  }
})

const accountIndexSections = computed(() => {
  const allocations = summary.value?.account_category_allocations
  if (!allocations) return []
  return Object.entries(allocations)
    .map(([account, items]) => ({
      account,
      items: items.filter(
        (item) =>
          item.category === 'nasdaq'
          || item.category === 'sp500'
          || item.target_pct > 0
          || item.current_pct > 0,
      ),
    }))
    .filter((section) => section.items.length > 0)
})

const accountChartOption = computed(() => {
  if (!summary.value) return null
  return {
    backgroundColor: 'transparent',
    tooltip: { trigger: 'axis' },
    grid: { left: 50, right: 20, top: 20, bottom: 30 },
    xAxis: {
      type: 'category',
      data: ['大陆', '香港'],
      axisLabel: { color: '#94a3b8' },
    },
    yAxis: {
      type: 'value',
      axisLabel: { color: '#94a3b8' },
    },
    series: [
      {
        type: 'bar',
        data: [summary.value.mainland_assets_cny, summary.value.hk_assets_cny],
        color: '#3ddc97',
      },
    ],
  }
})
</script>

<template>
  <NSpin :show="loading">
    <h1 class="page-title">投资总览</h1>

    <template v-if="summary">
      <div class="metric-grid">
        <div class="metric-card">
          <div class="metric-label">总资产</div>
          <div class="metric-value">{{ formatMoney(summary.total_assets_cny) }}</div>
        </div>
        <div class="metric-card">
          <div class="metric-label">净投入</div>
          <div class="metric-value">{{ formatMoney(summary.net_investment_cny) }}</div>
        </div>
        <div class="metric-card">
          <div class="metric-label">总收益</div>
          <div class="metric-value" :class="summary.total_return_cny >= 0 ? 'positive' : 'negative'">
            {{ formatMoney(summary.total_return_cny) }}
          </div>
          <div class="metric-sub">收益率 {{ formatPercent(summary.return_rate) }}</div>
        </div>
        <div class="metric-card">
          <div class="metric-label">XIRR / 年化</div>
          <div class="metric-value">{{ formatPercent(summary.xirr) }}</div>
          <div class="metric-sub">年化 {{ formatPercent(summary.annualized_return) }}</div>
        </div>
      </div>

      <div class="metric-grid">
        <div class="metric-card">
          <div class="metric-label">已实现收益</div>
          <div class="metric-value">{{ formatMoney(summary.realized_pnl_cny) }}</div>
        </div>
        <div class="metric-card">
          <div class="metric-label">未实现收益</div>
          <div class="metric-value">{{ formatMoney(summary.unrealized_pnl_cny) }}</div>
        </div>
        <div class="metric-card">
          <div class="metric-label">大陆 / 香港</div>
          <div class="metric-value" style="font-size: 18px">
            {{ formatMoney(summary.mainland_assets_cny) }} / {{ formatMoney(summary.hk_assets_cny) }}
          </div>
        </div>
        <div class="metric-card">
          <div class="metric-label">人民币 / 美元资产</div>
          <div class="metric-value" style="font-size: 18px">
            {{ formatMoney(summary.cny_assets) }} / {{ formatMoney(summary.usd_assets, 'USD') }}
          </div>
        </div>
      </div>

      <div class="metric-grid">
        <div class="metric-card">
          <div class="metric-label">建仓投入</div>
          <div class="metric-value" style="font-size: 18px">
            {{ formatMoney(summary.phase_investment?.building_invested_cny) }}
          </div>
          <div class="metric-sub">
            计划匹配 {{ formatMoney(summary.phase_investment?.building_matched_cny) }}
          </div>
        </div>
        <div class="metric-card">
          <div class="metric-label">定投投入</div>
          <div class="metric-value" style="font-size: 18px">
            {{ formatMoney(summary.phase_investment?.dca_invested_cny) }}
          </div>
          <div class="metric-sub">
            计划匹配 {{ formatMoney(summary.phase_investment?.dca_matched_cny) }}
          </div>
        </div>
      </div>

      <NAlert
        v-if="summary.rebalance_suggestion"
        type="info"
        :title="summary.rebalance_suggestion"
        style="margin-bottom: 16px"
      />

      <div v-if="summary.rebalance" class="panel" style="margin-bottom: 16px">
        <h3>再平衡建议</h3>
        <div class="allocation-list">
          <div
            v-for="item in summary.rebalance.account_gaps"
            :key="item.key"
            class="allocation-item"
          >
            <div class="allocation-head">
              <span>{{ item.label }}账户</span>
              <span>{{ formatPercent(item.current_pct) }} / 目标 {{ formatPercent(item.target_pct) }}</span>
            </div>
            <div class="allocation-gap">
              <NTag :type="item.gap_pct > 0 ? 'warning' : 'success'" size="small">
                偏差 {{ formatPercent(item.gap_pct) }}
              </NTag>
            </div>
          </div>
        </div>
        <div v-if="summary.rebalance.recommendations?.length" class="recommend-list">
          <div
            v-for="item in summary.rebalance.recommendations"
            :key="`${item.account}-${item.code}`"
            class="recommend-item"
          >
            <strong>{{ item.account }} · {{ item.code }} {{ item.name }}</strong>
            <span>{{ item.reason }}</span>
          </div>
        </div>
      </div>

      <div v-if="accountIndexSections.length" class="panel-grid" style="margin-bottom: 16px">
        <div
          v-for="section in accountIndexSections"
          :key="section.account"
          class="panel"
        >
          <h3>{{ section.account }} · 指数配比</h3>
          <div class="allocation-list">
            <div
              v-for="item in section.items"
              :key="`${section.account}-${item.category}`"
              class="allocation-item"
            >
              <div class="allocation-head">
                <span>{{ item.label }}</span>
                <span>{{ formatPercent(item.current_pct) }} / 目标 {{ formatPercent(item.target_pct) }}</span>
              </div>
              <div class="allocation-gap">
                <NTag :type="item.gap_pct > 0 ? 'warning' : 'success'" size="small">
                  偏差 {{ formatPercent(item.gap_pct) }}
                </NTag>
              </div>
            </div>
          </div>
        </div>
      </div>

      <div class="panel-grid" style="margin-bottom: 16px">
        <div class="panel">
          <h3>资产增长曲线</h3>
          <VChart v-if="assetChartOption" :option="assetChartOption" style="height: 280px" />
          <div v-else class="empty-chart">更新行情后可查看资产曲线</div>
        </div>
        <div class="panel">
          <h3>资产配置</h3>
          <VChart v-if="allocationChartOption" :option="allocationChartOption" style="height: 280px" />
          <div v-else class="empty-chart">暂无持仓数据</div>
        </div>
      </div>

      <div class="panel-grid" style="margin-bottom: 16px">
        <div class="panel">
          <h3>全仓目标比例</h3>
          <div class="allocation-list">
            <div
              v-for="item in summary.category_allocations"
              :key="item.category"
              class="allocation-item"
            >
              <div class="allocation-head">
                <span>{{ item.label }}</span>
                <span>{{ formatPercent(item.current_pct) }} / 目标 {{ formatPercent(item.target_pct) }}</span>
              </div>
              <div class="allocation-gap">
                <NTag :type="item.gap_pct > 0 ? 'warning' : 'success'" size="small">
                  偏差 {{ formatPercent(item.gap_pct) }}
                </NTag>
              </div>
            </div>
          </div>
        </div>
        <div class="panel">
          <h3>账户分布</h3>
          <VChart v-if="accountChartOption" :option="accountChartOption" style="height: 280px" />
        </div>
      </div>

      <div class="panel">
        <h3>持仓明细</h3>
        <NDataTable :columns="holdingColumns" :data="summary.holdings" :bordered="false" size="small" />
      </div>
    </template>
  </NSpin>
</template>

<style scoped>
.empty-chart {
  height: 280px;
  display: flex;
  align-items: center;
  justify-content: center;
  color: #8b98a5;
}

.allocation-list {
  display: flex;
  flex-direction: column;
  gap: 12px;
}

.allocation-item {
  padding: 10px 0;
  border-bottom: 1px solid #1f2a37;
}

.allocation-head {
  display: flex;
  justify-content: space-between;
  margin-bottom: 6px;
}

.allocation-gap {
  display: flex;
  justify-content: flex-end;
}

.recommend-list {
  margin-top: 12px;
  padding-top: 12px;
  border-top: 1px solid #1f2a37;
}

.recommend-item {
  display: flex;
  flex-direction: column;
  gap: 4px;
  padding: 8px 0;
  color: #cbd5e1;
  font-size: 13px;
}

.recommend-item span {
  color: #8b98a5;
}
</style>
