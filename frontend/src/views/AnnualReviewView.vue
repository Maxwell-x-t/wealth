<script setup>
import { computed, onMounted, reactive, ref } from 'vue'
import {
  NAlert,
  NButton,
  NCheckbox,
  NSpin,
  useMessage,
} from 'naive-ui'
import { getAnnualReview, updateAnnualReview } from '../api/client'

const message = useMessage()
const loading = ref(true)
const saving = ref(false)
const review = ref(null)
const checks = reactive({})

async function loadData() {
  loading.value = true
  try {
    const data = await getAnnualReview()
    review.value = data
    Object.keys(checks).forEach((key) => delete checks[key])
    data.items.forEach((item) => {
      checks[item.key] = item.checked
    })
  } finally {
    loading.value = false
  }
}

onMounted(loadData)

async function save() {
  saving.value = true
  try {
    review.value = await updateAnnualReview({
      year: review.value.year,
      checks: { ...checks },
    })
    message.success('年度检查已保存')
  } catch (error) {
    message.error(error.response?.data?.detail || '保存失败')
  } finally {
    saving.value = false
  }
}

const completionText = computed(() => {
  if (!review.value) return ''
  return `${review.value.done_count} / ${review.value.total_count}（${review.value.completion_rate}%）`
})
</script>

<template>
  <NSpin :show="loading">
    <div class="header-row">
      <div>
        <h1 class="page-title">年度检查</h1>
        <p class="page-desc">每年复盘定投纪律、资产配置与应急资金；勾选后保存即可</p>
      </div>
      <NButton type="primary" :loading="saving" @click="save">保存</NButton>
    </div>

    <template v-if="review">
      <div class="metric-grid" style="margin-bottom: 16px">
        <div class="metric-card">
          <div class="metric-label">检查年份</div>
          <div class="metric-value">{{ review.year }}</div>
        </div>
        <div class="metric-card">
          <div class="metric-label">完成进度</div>
          <div class="metric-value">{{ completionText }}</div>
        </div>
      </div>

      <div v-if="review.hints?.length" class="hints">
        <NAlert
          v-for="(hint, index) in review.hints"
          :key="`${hint.key}-${index}`"
          :type="hint.level === 'warning' ? 'warning' : hint.level === 'success' ? 'success' : 'info'"
          :title="hint.text"
          style="margin-bottom: 8px"
        />
      </div>

      <div class="panel">
        <h3>{{ review.year }} 年检查清单</h3>
        <div class="checklist">
          <label v-for="item in review.items" :key="item.key" class="check-item">
            <NCheckbox v-model:checked="checks[item.key]" />
            <span>{{ item.label }}</span>
          </label>
        </div>
      </div>
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

.hints {
  margin-bottom: 16px;
}

h3 {
  margin: 0 0 12px;
  font-size: 15px;
  font-weight: 600;
}

.checklist {
  display: flex;
  flex-direction: column;
  gap: 12px;
}

.check-item {
  display: flex;
  align-items: center;
  gap: 10px;
  cursor: pointer;
  color: #e2e8f0;
}
</style>
