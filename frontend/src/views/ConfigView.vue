<script setup>
import { onMounted, reactive, ref } from 'vue'
import {
  NButton,
  NDatePicker,
  NDivider,
  NForm,
  NFormItem,
  NInput,
  NInputNumber,
  NModal,
  NSpace,
  NSpin,
  NSwitch,
  useMessage,
} from 'naive-ui'
import { getConfig, getSyncStatus, runSyncNow, updateConfig } from '../api/client'
import { formatLocalDate } from '../utils/format'

const message = useMessage()
const loading = ref(true)
const syncing = ref(false)
const syncStatus = ref(null)
const showEffectiveModal = ref(false)
const pendingPayload = ref(null)
const dcaEffectiveFrom = ref(Date.now())

const loadedDcaAmounts = reactive({
  dca_monthly_amount: null,
})

const form = reactive({
  nasdaq: 70,
  sp500: 30,
  a_share: 0,
  gold: 0,
  cash: 0,
  qdii: 0,
  mainland: 60,
  hk: 40,
  usd_cny_rate: 7.2,
  plan_start_date: Date.now(),
  building_first_month_amount: 100000,
  building_monthly_amount: 50000,
  building_months: 8,
  building_target_amount: null,
  dca_monthly_amount: 10000,
  weeks_per_month: 4,
  plan_horizon_years: 20,
  mainland_nasdaq_code: '513100',
  mainland_sp500_code: '513500',
  hk_nasdaq_code: 'QQQM',
  hk_sp500_code: 'VOO',
  forecast_years: 20,
  forecast_return_pessimistic: 4,
  forecast_return_neutral: 8,
  forecast_return_optimistic: 12,
  forecast_inflation_pct: 2,
  forecast_mc_volatility: 15,
  forecast_mc_paths: 500,
  sync_enabled: false,
  sync_interval_hours: 24,
  plan_rebalance_enabled: true,
  plan_rebalance_threshold: 5,
  dca_boost_enabled: true,
  dca_boost_20_pct_amount: 10000,
  dca_boost_30_pct_amount: 20000,
  dca_boost_40_pct_amount: 30000,
  dca_boost_monthly_cap: 30000,
  dca_boost_cash_available: 0,
  dca_boost_lookback_days: 365,
  dca_ma_enabled: false,
  dca_ma_window_days: 200,
  dca_ma_min_factor: 0.7,
  dca_ma_max_factor: 1.3,
  dca_ma_band_pct: 20,
  dca_ma_center_pct: 8,
  hk_whole_share_only: true,
  hk_share_price_buffer_pct: 2,
  hk_nasdaq: null,
  hk_sp500: null,
  mainland_nasdaq: null,
  mainland_sp500: null,
})

async function loadData() {
  loading.value = true
  try {
    const [config, status] = await Promise.all([getConfig(), getSyncStatus()])
    Object.assign(form, config)
    normalizeAccountIndexTargets('mainland')
    normalizeAccountIndexTargets('hk')
    loadedDcaAmounts.dca_monthly_amount = config.dca_monthly_amount
    if (config.plan_start_date) {
      form.plan_start_date = new Date(config.plan_start_date).getTime()
    }
    syncStatus.value = status
  } finally {
    loading.value = false
  }
}

onMounted(loadData)

const OPTIONAL_NUMERIC_KEYS = [
  'building_target_amount',
  'hk_nasdaq',
  'hk_sp500',
  'mainland_nasdaq',
  'mainland_sp500',
]

function buildConfigPayload() {
  const payload = { ...form }
  for (const key of OPTIONAL_NUMERIC_KEYS) {
    if (payload[key] === '' || payload[key] === undefined) {
      payload[key] = null
    }
  }
  const planStart = formatLocalDate(form.plan_start_date)
  if (!planStart) {
    return null
  }
  payload.plan_start_date = planStart
  return payload
}

function normAmount(v) {
  if (v === '' || v === undefined || v === null) {
    return null
  }
  const n = Number(v)
  return Number.isNaN(n) ? null : n
}

function normalizeAccountIndexTargets(accountKey) {
  const nasdaq = normAmount(form[`${accountKey}_nasdaq`])
  const sp500 = normAmount(form[`${accountKey}_sp500`])
  if (nasdaq === null && sp500 === null) {
    form[`${accountKey}_nasdaq`] = null
    form[`${accountKey}_sp500`] = null
    return
  }
  if (nasdaq === null || sp500 === null || Math.round(nasdaq + sp500) !== 100) {
    form[`${accountKey}_nasdaq`] = null
    form[`${accountKey}_sp500`] = null
  }
}

function dcaAmountChanged() {
  return normAmount(form.dca_monthly_amount) !== normAmount(loadedDcaAmounts.dca_monthly_amount)
}

async function submitConfig(payload) {
  await updateConfig(payload)
  message.success('配置已保存')
  syncStatus.value = await getSyncStatus()
  await loadData()
}

function validateAccountIndexTargets(accountKey, label) {
  const nasdaq = normAmount(form[`${accountKey}_nasdaq`])
  const sp500 = normAmount(form[`${accountKey}_sp500`])
  if ((nasdaq === null && sp500 === null) || (nasdaq === 0 && sp500 === 0)) {
    return true
  }
  if (nasdaq === null || sp500 === null) {
    message.error(`${label}账户纳指与标普比例需同时填写，或均留空以使用全局配置`)
    return false
  }
  if (Math.round(nasdaq + sp500) !== 100) {
    message.error(`${label}账户纳指与标普比例之和必须为 100`)
    return false
  }
  return true
}

function validateForm() {
  const assetTotal = Math.round(form.nasdaq + form.sp500 + form.a_share + form.gold + form.cash + form.qdii)
  if (assetTotal !== 100) {
    message.error('纳指、标普、A股、黄金、现金、QDII 比例之和必须为 100')
    return false
  }
  if (Math.round(form.mainland + form.hk) !== 100) {
    message.error('大陆与香港比例之和必须为 100')
    return false
  }
  if (!validateAccountIndexTargets('hk', '香港')) {
    return false
  }
  if (!validateAccountIndexTargets('mainland', '大陆')) {
    return false
  }
  return true
}

async function save() {
  if (!validateForm()) {
    return
  }

  try {
    const payload = buildConfigPayload()
    if (!payload) {
      message.error('请填写计划开始日期')
      return
    }
    if (dcaAmountChanged()) {
      pendingPayload.value = payload
      dcaEffectiveFrom.value = Date.now()
      showEffectiveModal.value = true
      return
    }
    await submitConfig(payload)
  } catch (error) {
    message.error(error.response?.data?.detail || '保存失败')
  }
}

async function confirmEffectiveSave() {
  if (!pendingPayload.value) return
  const payload = { ...pendingPayload.value }
  const effectiveDate = formatLocalDate(dcaEffectiveFrom.value)
  if (!effectiveDate) {
    message.error('请选择定投生效日期')
    return
  }
  payload.dca_effective_from = effectiveDate
  try {
    await submitConfig(payload)
    showEffectiveModal.value = false
    pendingPayload.value = null
  } catch (error) {
    message.error(error.response?.data?.detail || '保存失败')
  }
}

async function handleSyncNow() {
  syncing.value = true
  try {
    syncStatus.value = await runSyncNow()
    if (syncStatus.value.last_status === 'error') {
      message.error(syncStatus.value.last_error || '同步失败')
    } else {
      message.success(
        `同步完成：行情成功 ${syncStatus.value.prices_success}，失败 ${syncStatus.value.prices_fail}，汇率 ${syncStatus.value.fx_rate}`,
      )
    }
  } catch (error) {
    message.error(error.response?.data?.detail || '同步失败')
  } finally {
    syncing.value = false
  }
}
</script>

<template>
  <NSpin :show="loading">
    <h1 class="page-title">参数配置</h1>

    <div class="panel" style="max-width: 640px">
      <NForm label-placement="left" label-width="140">
        <NDivider title-placement="left">资产配置目标（全仓合计 100%）</NDivider>
        <p class="hint-text section-hint">
          统计大陆与香港全部持仓，用于 Dashboard 偏离度与定投倾斜判断。
        </p>
        <NFormItem label="纳指">
          <NInputNumber v-model:value="form.nasdaq" :min="0" :max="100" style="width: 100%" />
        </NFormItem>
        <NFormItem label="标普">
          <NInputNumber v-model:value="form.sp500" :min="0" :max="100" style="width: 100%" />
        </NFormItem>
        <NFormItem label="A股">
          <NInputNumber v-model:value="form.a_share" :min="0" :max="100" style="width: 100%" />
        </NFormItem>
        <NFormItem label="黄金">
          <NInputNumber v-model:value="form.gold" :min="0" :max="100" style="width: 100%" />
        </NFormItem>
        <NFormItem label="现金">
          <NInputNumber v-model:value="form.cash" :min="0" :max="100" style="width: 100%" />
        </NFormItem>
        <NFormItem label="QDII">
          <NInputNumber v-model:value="form.qdii" :min="0" :max="100" style="width: 100%" />
        </NFormItem>

        <NDivider title-placement="left">账户分布目标（合计 100%）</NDivider>
        <NFormItem label="大陆">
          <NInputNumber v-model:value="form.mainland" :min="0" :max="100" style="width: 100%" />
        </NFormItem>
        <NFormItem label="香港">
          <NInputNumber v-model:value="form.hk" :min="0" :max="100" style="width: 100%" />
        </NFormItem>

        <NDivider title-placement="left">汇率默认值</NDivider>
        <NFormItem label="美元汇率（市值折算）">
          <NInputNumber v-model:value="form.usd_cny_rate" :min="0" :step="0.01" style="width: 100%" />
        </NFormItem>
        <p class="hint-text">日常更新请使用左侧「汇率更新」；此处为默认值，无快照时生效。</p>

        <NDivider title-placement="left">统一建仓 / 定投计划</NDivider>
        <p class="hint-text section-hint">
          月额与建仓目标均为人民币总额；同一节奏同时生成大陆与香港计划，按上方账户比例拆分执行（香港按汇率换算为美元）。
        </p>
        <NFormItem label="计划开始日期">
          <NDatePicker v-model:value="form.plan_start_date" type="date" style="width: 100%" />
        </NFormItem>
        <p class="hint-text">
          修改开始日会重新生成投资计划与匹配结果，不会改动已录入的交易记录；未标阶段的交易会按新开始日重判建仓/定投。
        </p>
        <NFormItem label="首月建仓（¥）">
          <NInputNumber v-model:value="form.building_first_month_amount" :min="0" style="width: 100%" />
        </NFormItem>
        <NFormItem label="之后每月建仓（¥）">
          <NInputNumber v-model:value="form.building_monthly_amount" :min="0" style="width: 100%" />
        </NFormItem>
        <NFormItem label="建仓持续月数">
          <NInputNumber v-model:value="form.building_months" :min="1" :max="24" style="width: 100%" />
        </NFormItem>
        <NFormItem label="建仓目标总额（¥）">
          <NInputNumber v-model:value="form.building_target_amount" :min="0" clearable style="width: 100%" />
        </NFormItem>
        <p class="hint-text">达到目标总额或建仓月数后进入定投（先满足者生效）；最后一月自动截断至目标。</p>
        <NFormItem label="定投每月金额（¥）">
          <NInputNumber v-model:value="form.dca_monthly_amount" :min="0" style="width: 100%" />
        </NFormItem>
        <p class="hint-text">
          修改定投月额保存时可设置生效日；仅影响该日及之后的计划应投，历史计划目标不变。
        </p>
        <NFormItem label="每月买入次数">
          <NInputNumber v-model:value="form.weeks_per_month" :min="1" :max="5" style="width: 100%" />
        </NFormItem>
        <p class="hint-text">大陆按 A 股日历、香港按美股日历各自排期，每月选取相同周数。</p>
        <NFormItem label="计划跨度（年）">
          <NInputNumber v-model:value="form.plan_horizon_years" :min="1" :max="40" style="width: 100%" />
        </NFormItem>
        <NFormItem label="偏离度倾斜">
          <NSwitch v-model:value="form.plan_rebalance_enabled" />
        </NFormItem>
        <NFormItem label="倾斜触发阈值 %">
          <NInputNumber
            v-model:value="form.plan_rebalance_threshold"
            :min="0"
            :max="20"
            :step="0.1"
            style="width: 100%"
          />
        </NFormItem>
        <p class="hint-text">
          开启后，当全仓纳指或标普偏离目标 ≥ 阈值时，本月定投 100% 投入偏离最大的一类；各账户仅在其已配置的大类上参与倾斜。
        </p>

        <NDivider title-placement="left">香港执行设置</NDivider>
        <NFormItem label="香港纳指 %">
          <NInputNumber v-model:value="form.hk_nasdaq" :min="0" :max="100" clearable style="width: 100%" />
        </NFormItem>
        <NFormItem label="香港标普 %">
          <NInputNumber v-model:value="form.hk_sp500" :min="0" :max="100" clearable style="width: 100%" />
        </NFormItem>
        <p class="hint-text">
          留空则跟全局纳指/标普比例；设为 100/0 时香港只买 QQQM，后续改比例即可加入 VOO。持仓仍计入全仓纳指/标普统计。
        </p>
        <NFormItem label="整股执行模式">
          <NSwitch v-model:value="form.hk_whole_share_only" />
        </NFormItem>
        <NFormItem label="买入门槛缓冲 %">
          <NInputNumber
            v-model:value="form.hk_share_price_buffer_pct"
            :min="0"
            :max="20"
            :step="0.5"
            style="width: 100%"
          />
        </NFormItem>
        <p class="hint-text">
          Trade25 等不可碎股时：香港计划金额逐期入池，达「股价×(1+缓冲)」后标为可买入，建议 floor(池/股价) 股，手动下单。
        </p>

        <NDivider title-placement="left">跌幅加仓</NDivider>
        <NFormItem label="启用跌幅加仓">
          <NSwitch v-model:value="form.dca_boost_enabled" />
        </NFormItem>
        <NFormItem label="20% 档额外（元）">
          <NInputNumber v-model:value="form.dca_boost_20_pct_amount" :min="0" style="width: 100%" />
        </NFormItem>
        <NFormItem label="30% 档额外（元）">
          <NInputNumber v-model:value="form.dca_boost_30_pct_amount" :min="0" style="width: 100%" />
        </NFormItem>
        <NFormItem label="40% 档额外（元）">
          <NInputNumber v-model:value="form.dca_boost_40_pct_amount" :min="0" style="width: 100%" />
        </NFormItem>
        <NFormItem label="40% 档月上限（元）">
          <NInputNumber v-model:value="form.dca_boost_monthly_cap" :min="0" style="width: 100%" />
        </NFormItem>
        <NFormItem label="可用现金（元）">
          <NInputNumber v-model:value="form.dca_boost_cash_available" :min="0" style="width: 100%" />
        </NFormItem>
        <NFormItem label="高点回看（天）">
          <NInputNumber v-model:value="form.dca_boost_lookback_days" :min="30" :max="1095" style="width: 100%" />
        </NFormItem>
        <p class="hint-text">
          取 20/30/40% 最高档（不叠加），额外金额按账户定投比例分摊，并按低配倾斜分配；需填写可用现金后才会计入计划。
        </p>

        <NDivider title-placement="left">均线偏离因子（MA200）</NDivider>
        <NFormItem label="启用均线因子">
          <NSwitch v-model:value="form.dca_ma_enabled" />
        </NFormItem>
        <NFormItem label="均线窗口（交易日）">
          <NInputNumber v-model:value="form.dca_ma_window_days" :min="20" :max="500" style="width: 100%" />
        </NFormItem>
        <NFormItem label="最小因子（偏贵时）">
          <NInputNumber v-model:value="form.dca_ma_min_factor" :min="0.1" :max="1" :step="0.05" style="width: 100%" />
        </NFormItem>
        <NFormItem label="最大因子（偏便宜时）">
          <NInputNumber v-model:value="form.dca_ma_max_factor" :min="1" :max="3" :step="0.05" style="width: 100%" />
        </NFormItem>
        <NFormItem label="满档偏离带宽 %">
          <NInputNumber v-model:value="form.dca_ma_band_pct" :min="1" :max="100" :step="1" style="width: 100%" />
        </NFormItem>
        <NFormItem label="中性区上移 %">
          <NInputNumber v-model:value="form.dca_ma_center_pct" :min="0" :max="50" :step="1" style="width: 100%" />
        </NFormItem>
        <p class="hint-text">
          用指数月线相对约 {{ Math.round(form.dca_ma_window_days / 21) }} 个月均线的偏离度调整当月定投：偏贵少投（最低 ×{{ form.dca_ma_min_factor }}）、偏便宜多投（最高 ×{{ form.dca_ma_max_factor }}）。中性区上移 {{ form.dca_ma_center_pct }}% 是考虑市场长期多头、价格多数时间在均线上方。回测中少投的钱会入池，等便宜时再补投（预算守恒）；实盘仅对当月生效。
        </p>

        <NDivider title-placement="left">计划品种代码</NDivider>
        <NFormItem label="大陆纳指代码">
          <NInput v-model:value="form.mainland_nasdaq_code" />
        </NFormItem>
        <NFormItem label="大陆标普代码">
          <NInput v-model:value="form.mainland_sp500_code" />
        </NFormItem>
        <NFormItem label="香港纳指代码">
          <NInput v-model:value="form.hk_nasdaq_code" />
        </NFormItem>
        <NFormItem label="香港标普代码">
          <NInput v-model:value="form.hk_sp500_code" />
        </NFormItem>

        <NDivider title-placement="left">财富预测默认值</NDivider>
        <NFormItem label="默认预测年限">
          <NInputNumber v-model:value="form.forecast_years" :min="1" :max="40" style="width: 100%" />
        </NFormItem>
        <NFormItem label="悲观年化 %">
          <NInputNumber v-model:value="form.forecast_return_pessimistic" :step="0.5" style="width: 100%" />
        </NFormItem>
        <NFormItem label="中性年化 %">
          <NInputNumber v-model:value="form.forecast_return_neutral" :step="0.5" style="width: 100%" />
        </NFormItem>
        <NFormItem label="乐观年化 %">
          <NInputNumber v-model:value="form.forecast_return_optimistic" :step="0.5" style="width: 100%" />
        </NFormItem>
        <NFormItem label="默认通胀 %">
          <NInputNumber v-model:value="form.forecast_inflation_pct" :min="0" :max="20" :step="0.1" style="width: 100%" />
        </NFormItem>
        <NFormItem label="MC 波动率 %">
          <NInputNumber v-model:value="form.forecast_mc_volatility" :min="0" :max="80" style="width: 100%" />
        </NFormItem>
        <NFormItem label="MC 路径数">
          <NInputNumber v-model:value="form.forecast_mc_paths" :min="50" :max="2000" :step="50" style="width: 100%" />
        </NFormItem>
        <p class="hint-text">财富预测页可勾选通胀/蒙特卡洛并临时改参数；此处为默认值。</p>

        <NDivider title-placement="left">定时同步</NDivider>
        <NFormItem label="启用定时同步">
          <NSwitch v-model:value="form.sync_enabled" />
        </NFormItem>
        <NFormItem label="间隔（小时）">
          <NInputNumber v-model:value="form.sync_interval_hours" :min="1" :max="168" style="width: 100%" />
        </NFormItem>
        <p class="hint-text">
          开启后后端会按间隔自动拉取行情与汇率（需保持后端进程运行）。保存配置后生效。
        </p>
        <div v-if="syncStatus" class="sync-status">
          <div>调度器：{{ syncStatus.scheduler_alive ? '运行中' : '未启动' }}</div>
          <div>上次同步：{{ syncStatus.last_run_at || '尚未执行' }}</div>
          <div>状态：{{ syncStatus.last_status || '-' }}</div>
          <div v-if="syncStatus.last_error">错误：{{ syncStatus.last_error }}</div>
        </div>
        <NFormItem>
          <NSpace>
            <NButton type="primary" @click="save">保存配置</NButton>
            <NButton :loading="syncing" @click="handleSyncNow">立即同步行情+汇率</NButton>
          </NSpace>
        </NFormItem>
      </NForm>
    </div>

    <NModal
      v-model:show="showEffectiveModal"
      preset="card"
      title="定投金额生效日"
      style="width: 520px"
    >
      <p class="hint-text" style="margin: 0 0 12px 0">
        定投月额变更从所选生效日所在月份起按新金额生成计划，更早月份仍按原金额。
      </p>
      <NForm label-placement="left" label-width="120">
        <NFormItem label="生效日">
          <NDatePicker v-model:value="dcaEffectiveFrom" type="date" style="width: 100%" />
        </NFormItem>
      </NForm>
      <template #footer>
        <NSpace justify="end">
          <NButton @click="showEffectiveModal = false">取消</NButton>
          <NButton type="primary" @click="confirmEffectiveSave">确认保存</NButton>
        </NSpace>
      </template>
    </NModal>
  </NSpin>
</template>

<style scoped>
.hint-text {
  margin: -8px 0 16px 140px;
  color: #8b98a5;
  font-size: 12px;
}

.section-hint {
  margin-top: -4px;
}

.sync-status {
  margin: 0 0 16px 140px;
  color: #94a3b8;
  font-size: 12px;
  line-height: 1.6;
}
</style>
