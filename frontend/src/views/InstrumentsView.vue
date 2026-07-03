<script setup>
import { h, onMounted, reactive, ref } from 'vue'
import {
  NButton,
  NDataTable,
  NForm,
  NFormItem,
  NInput,
  NModal,
  NSelect,
  NSpace,
  NSpin,
  NTag,
  useMessage,
} from 'naive-ui'
import {
  createInstrument,
  deleteInstrument,
  getAccounts,
  getInstruments,
  updateInstrument,
} from '../api/client'

const message = useMessage()
const loading = ref(true)
const showModal = ref(false)
const editingId = ref(null)
const instruments = ref([])
const accounts = ref([])

const categoryOptions = [
  { label: '纳指', value: 'nasdaq' },
  { label: '标普', value: 'sp500' },
  { label: 'A股', value: 'a_share' },
  { label: '黄金', value: 'gold' },
  { label: '现金', value: 'cash' },
  { label: 'QDII', value: 'qdii' },
  { label: '其他', value: 'other' },
]

const currencyOptions = [
  { label: '人民币 (CNY)', value: 'CNY' },
  { label: '美元 (USD)', value: 'USD' },
]

const categoryLabelMap = Object.fromEntries(categoryOptions.map((item) => [item.value, item.label]))

const form = reactive({
  code: '',
  name: '',
  category: 'other',
  account_id: null,
  currency: 'CNY',
})

async function loadData() {
  loading.value = true
  try {
    const [instrumentRows, accountRows] = await Promise.all([
      getInstruments(),
      getAccounts(),
    ])
    instruments.value = instrumentRows
    accounts.value = accountRows
  } finally {
    loading.value = false
  }
}

onMounted(loadData)

function resetForm() {
  editingId.value = null
  form.code = ''
  form.name = ''
  form.category = 'other'
  form.account_id = accounts.value[0]?.id ?? null
  form.currency = 'CNY'
}

function openCreate() {
  resetForm()
  showModal.value = true
}

function openEdit(row) {
  editingId.value = row.id
  form.code = row.code
  form.name = row.name
  form.category = row.category
  form.account_id = row.account_id
  form.currency = row.currency
  showModal.value = true
}

async function submitForm() {
  if (!form.code.trim() || !form.name.trim()) {
    message.error('请填写代码和名称')
    return
  }

  const payload = {
    code: form.code.trim(),
    name: form.name.trim(),
    category: form.category,
    account_id: form.account_id,
    currency: form.currency,
  }

  try {
    if (editingId.value) {
      await updateInstrument(editingId.value, payload)
      message.success('品种已更新')
    } else {
      await createInstrument(payload)
      message.success('品种已添加')
    }
    showModal.value = false
    await loadData()
  } catch (error) {
    message.error(error.response?.data?.detail || '保存失败')
  }
}

async function toggleActive(row) {
  try {
    await updateInstrument(row.id, { is_active: !row.is_active })
    message.success(row.is_active ? '品种已停用' : '品种已启用')
    await loadData()
  } catch (error) {
    message.error(error.response?.data?.detail || '操作失败')
  }
}

async function removeRow(id) {
  try {
    await deleteInstrument(id)
    message.success('品种已删除')
    await loadData()
  } catch (error) {
    message.error(error.response?.data?.detail || '删除失败')
  }
}

const columns = [
  { title: '代码', key: 'code', width: 100 },
  { title: '名称', key: 'name', width: 160 },
  {
    title: '分类',
    key: 'category',
    width: 90,
    render: (row) => categoryLabelMap[row.category] || row.category,
  },
  { title: '账户', key: 'account_name', width: 80 },
  { title: '币种', key: 'currency', width: 70 },
  {
    title: '状态',
    key: 'is_active',
    width: 80,
    render: (row) =>
      h(
        NTag,
        { type: row.is_active ? 'success' : 'default', size: 'small' },
        { default: () => (row.is_active ? '启用' : '停用') },
      ),
  },
  {
    title: '操作',
    key: 'actions',
    width: 200,
    render: (row) =>
      h(NSpace, null, {
        default: () => [
          h(
            NButton,
            { size: 'small', onClick: () => openEdit(row) },
            { default: () => '编辑' },
          ),
          h(
            NButton,
            { size: 'small', onClick: () => toggleActive(row) },
            { default: () => (row.is_active ? '停用' : '启用') },
          ),
          h(
            NButton,
            { size: 'small', type: 'error', onClick: () => removeRow(row.id) },
            { default: () => '删除' },
          ),
        ],
      }),
  },
]
</script>

<template>
  <NSpin :show="loading">
    <div class="header-row">
      <div>
        <h1 class="page-title">品种管理</h1>
        <p class="page-desc">支持自定义添加 ETF / QDII 代码，名称和分类可随时修改</p>
      </div>
      <NButton type="primary" @click="openCreate">新增品种</NButton>
    </div>

    <div class="panel">
      <NDataTable :columns="columns" :data="instruments" :bordered="false" size="small" />
    </div>

    <NModal
      v-model:show="showModal"
      preset="card"
      :title="editingId ? '编辑品种' : '新增品种'"
      style="width: 520px"
    >
      <NForm label-placement="left" label-width="80">
        <NFormItem label="代码">
          <NInput v-model:value="form.code" placeholder="如 159696" />
        </NFormItem>
        <NFormItem label="名称">
          <NInput v-model:value="form.name" placeholder="如 恒生科技ETF" />
        </NFormItem>
        <NFormItem label="分类">
          <NSelect v-model:value="form.category" :options="categoryOptions" />
        </NFormItem>
        <NFormItem label="账户">
          <NSelect
            v-model:value="form.account_id"
            :options="accounts.map((item) => ({ label: item.name, value: item.id }))"
          />
        </NFormItem>
        <NFormItem label="币种">
          <NSelect v-model:value="form.currency" :options="currencyOptions" />
        </NFormItem>
      </NForm>
      <template #footer>
        <NSpace justify="end">
          <NButton @click="showModal = false">取消</NButton>
          <NButton type="primary" @click="submitForm">保存</NButton>
        </NSpace>
      </template>
    </NModal>
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
</style>
