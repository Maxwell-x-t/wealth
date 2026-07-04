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
  NSpin,
  useMessage,
} from 'naive-ui'
import { getConfig, updateConfig } from '../api/client'

const message = useMessage()
const loading = ref(true)

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
})

async function loadData() {
  loading.value = true
  try {
    const config = await getConfig()
    Object.assign(form, config)
    if (config.plan_start_date) {
      form.plan_start_date = new Date(config.plan_start_date).getTime()
    }
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
  } catch (error) {
    message.error(error.response?.data?.detail || '保存失败')
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
        <p class="hint-text">财富预测页可临时改年限与收益率；此处保存为默认值。</p>

        <NFormItem>
          <NButton type="primary" @click="save">保存配置</NButton>
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
</style>
