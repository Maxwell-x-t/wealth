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
  NSpace,
  NSpin,
  NSwitch,
  useMessage,
} from 'naive-ui'
import { getConfig, getSyncStatus, runSyncNow, updateConfig } from '../api/client'

const message = useMessage()
const loading = ref(true)
const syncing = ref(false)
const syncStatus = ref(null)

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
  dca_monthly_amount: 10000,
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
})

async function loadData() {
  loading.value = true
  try {
    const [config, status] = await Promise.all([getConfig(), getSyncStatus()])
    Object.assign(form, config)
    if (config.plan_start_date) {
      form.plan_start_date = new Date(config.plan_start_date).getTime()
    }
    syncStatus.value = status
  } finally {
    loading.value = false
  }
}

onMounted(loadData)

async function save() {
  const assetTotal = Math.round(form.nasdaq + form.sp500 + form.a_share + form.gold + form.cash + form.qdii)
  if (assetTotal !== 100) {
    message.error('纳指、标普、A股、黄金、现金、QDII 比例之和必须为 100')
    return
  }
  if (Math.round(form.mainland + form.hk) !== 100) {
    message.error('大陆与香港比例之和必须为 100')
    return
  }

  try {
    await updateConfig({
      ...form,
      plan_start_date: new Date(form.plan_start_date).toISOString().slice(0, 10),
    })
    message.success('配置已保存')
    syncStatus.value = await getSyncStatus()
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
        <NDivider title-placement="left">资产配置目标（合计 100%）</NDivider>
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

        <NDivider title-placement="left">建仓 / 定投计划</NDivider>
        <NFormItem label="计划开始日期">
          <NDatePicker v-model:value="form.plan_start_date" type="date" style="width: 100%" />
        </NFormItem>
        <NFormItem label="首月建仓金额">
          <NInputNumber v-model:value="form.building_first_month_amount" :min="0" style="width: 100%" />
        </NFormItem>
        <NFormItem label="之后每月建仓">
          <NInputNumber v-model:value="form.building_monthly_amount" :min="0" style="width: 100%" />
        </NFormItem>
        <NFormItem label="建仓持续月数">
          <NInputNumber v-model:value="form.building_months" :min="1" :max="24" style="width: 100%" />
        </NFormItem>
        <NFormItem label="定投每月金额">
          <NInputNumber v-model:value="form.dca_monthly_amount" :min="0" style="width: 100%" />
        </NFormItem>
        <NFormItem label="计划跨度（年）">
          <NInputNumber v-model:value="form.plan_horizon_years" :min="1" :max="40" style="width: 100%" />
        </NFormItem>

        <NDivider title-placement="left">计划默认品种代码</NDivider>
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
  </NSpin>
</template>

<style scoped>
.hint-text {
  margin: -8px 0 16px 140px;
  color: #8b98a5;
  font-size: 12px;
}

.sync-status {
  margin: 0 0 16px 140px;
  color: #94a3b8;
  font-size: 12px;
  line-height: 1.6;
}
</style>
