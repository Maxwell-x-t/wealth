<script setup>
import { computed, reactive, ref, watch } from "vue";
import { NModal, NButton, NAlert, useMessage } from "naive-ui";
import { Check, X } from "@lucide/vue";
import { createTransaction, updateTransaction } from "../api/client";

const props = defineProps({
  show: Boolean,
  account: Object,
  transaction: Object,
  instrumentId: Number,
});
const emit = defineEmits(["update:show", "saved"]);
const message = useMessage();
const saving = ref(false);
const error = ref("");
const form = reactive({});
const today = () => new Date().toLocaleDateString("sv-SE");
const selected = computed(() =>
  props.account?.holdings.find(
    (h) => h.instrument_id === Number(form.instrument_id),
  ),
);
const total = computed(
  () => (Number(form.quantity) || 0) * (Number(form.price) || 0),
);
const cashDelta = computed(
  () =>
    (form.side === "buy" ? -total.value : total.value) -
    (Number(form.fee) || 0),
);
const cashAfter = computed(() => {
  const old = props.transaction;
  const oldDelta = old
    ? (old.side === "buy" ? -old.amount : old.amount) - old.fee
    : 0;
  return (props.account?.cash || 0) + cashDelta.value - oldDelta;
});
const money = (value) =>
  new Intl.NumberFormat("zh-CN", {
    minimumFractionDigits: 2,
    maximumFractionDigits: 2,
  }).format(value === 0 ? 0 : value);
watch(
  () => props.show,
  (show) => {
    if (!show) return;
    error.value = "";
    const tx = props.transaction;
    Object.assign(form, {
      trade_date: tx?.trade_date || today(),
      side: tx?.side || "buy",
      instrument_id:
        tx?.instrument_id ||
        props.instrumentId ||
        props.account?.holdings[0]?.instrument_id,
      quantity: tx?.quantity || "",
      price: tx?.price || "",
      fee: tx?.fee ?? 0,
      note: tx?.note || "",
      etf_layers_after: tx?.etf_layers_after ?? props.account?.etf_layers ?? 0,
      request_id: crypto.randomUUID(),
    });
  },
);
async function save() {
  if (saving.value) return;
  saving.value = true;
  error.value = "";
  try {
    const payload = {
      trade_date: form.trade_date,
      account_id: props.account.account_id,
      instrument_id: Number(form.instrument_id),
      side: form.side,
      quantity: Number(form.quantity),
      price: Number(form.price),
      fee: Number(form.fee),
      exchange_rate: 1,
      note: form.note || null,
      etf_layers_after:
        selected.value?.code === "sh512890"
          ? Number(form.etf_layers_after)
          : null,
    };
    if (props.transaction)
      await updateTransaction(props.transaction.id, payload);
    else await createTransaction({ ...payload, request_id: form.request_id });
    message.success("成交已登记");
    emit("update:show", false);
    emit("saved");
  } catch (e) {
    error.value =
      typeof e.response?.data?.detail === "string"
        ? e.response.data.detail
        : "保存失败，请核对输入后重试";
  } finally {
    saving.value = false;
  }
}
</script>

<template>
  <NModal
    :show="show"
    @update:show="(value) => !saving && emit('update:show', value)"
    preset="card"
    :title="transaction ? '修改成交' : '录入成交'"
    :mask-closable="!saving"
    :close-on-esc="!saving"
    style="
      width: min(540px, calc(100vw - 28px));
      max-height: calc(100dvh - 32px);
    "
    :content-style="{ overflowY: 'auto' }"
    :bordered="false"
  >
    <form class="journal-form" @submit.prevent="save">
      <div class="side-switch" role="group" aria-label="交易方向">
        <label :class="{ selected: form.side === 'buy' }"
          ><input
            v-model="form.side"
            type="radio"
            value="buy"
            name="side"
          />买入</label
        >
        <label :class="{ selected: form.side === 'sell' }"
          ><input
            v-model="form.side"
            type="radio"
            value="sell"
            name="side"
          />卖出</label
        >
      </div>
      <label
        >证券<select
          v-model="form.instrument_id"
          aria-label="证券"
          required
          :disabled="saving"
        >
          <option
            v-for="holding in account?.holdings"
            :key="holding.instrument_id"
            :value="holding.instrument_id"
          >
            {{ holding.code.slice(2) }} {{ holding.name }}
          </option>
        </select></label
      >
      <div class="form-pair">
        <label
          >成交日期<input
            v-model="form.trade_date"
            type="date"
            :min="account?.opening_date"
            :max="today()"
            required
            :disabled="saving"
        /></label>
        <label
          >数量（股／份）<input
            v-model="form.quantity"
            type="number"
            min="1"
            max="1000000000"
            step="1"
            required
            :disabled="saving"
        /></label>
      </div>
      <div class="form-pair">
        <label
          >成交价格（元）<input
            v-model="form.price"
            type="number"
            min="0.000001"
            step="0.000001"
            required
            :disabled="saving"
        /></label>
        <label
          >税费合计（元）<input
            v-model="form.fee"
            type="number"
            min="0"
            step="0.01"
            required
            :disabled="saving"
        /></label>
      </div>
      <label v-if="selected?.code === 'sh512890'"
        >成交后策略层数<select
          v-model="form.etf_layers_after"
          :disabled="saving"
        >
          <option v-for="n in 11" :key="n" :value="n - 1">
            {{ n - 1 }} 层
          </option>
        </select></label
      >
      <label
        >备注<input v-model="form.note" maxlength="1000" :disabled="saving"
      /></label>
      <dl class="trade-preview">
        <div>
          <dt>成交金额</dt>
          <dd>¥ {{ money(total) }}</dd>
        </div>
        <div>
          <dt>现金变动</dt>
          <dd>{{ cashDelta >= 0 ? "+" : "" }}{{ money(cashDelta) }}</dd>
        </div>
        <div>
          <dt>登记后现金</dt>
          <dd :class="{ negative: cashAfter < 0 }">¥ {{ money(cashAfter) }}</dd>
        </div>
        <div>
          <dt>当前持有</dt>
          <dd>{{ selected?.quantity?.toLocaleString("zh-CN") || 0 }} 股／份</dd>
        </div>
      </dl>
      <NAlert v-if="error" type="error" :show-icon="false" role="alert">{{
        error
      }}</NAlert>
      <div class="form-actions">
        <NButton :disabled="saving" @click="emit('update:show', false)"
          ><template #icon><X :size="16" /></template>取消</NButton
        ><NButton type="primary" attr-type="submit" :loading="saving"
          ><template #icon><Check :size="16" /></template>保存成交</NButton
        >
      </div>
    </form>
  </NModal>
</template>

<style>
.journal-form {
  display: grid;
  gap: 16px;
}
.journal-form label {
  display: grid;
  gap: 6px;
  color: #b7bfc1;
  font-size: 13px;
}
.journal-form input,
.journal-form select {
  width: 100%;
  min-width: 0;
  height: 38px;
  border: 1px solid #43474b;
  border-radius: 4px;
  padding: 7px 10px;
  background: #202326;
  color: #f3f4f4;
  font: inherit;
  color-scheme: dark;
}
.journal-form input:focus,
.journal-form select:focus {
  outline: 2px solid #63e2b7;
  outline-offset: 1px;
}
.form-pair {
  display: grid;
  grid-template-columns: 1fr 1fr;
  gap: 14px;
}
.side-switch {
  display: flex;
  gap: 0;
  border-bottom: 1px solid #43474b;
}
.side-switch label {
  display: flex;
  align-items: center;
  justify-content: center;
  gap: 8px;
  width: 50%;
  padding: 10px;
  border-bottom: 2px solid transparent;
}
.side-switch label.selected {
  color: #63e2b7;
  border-color: #63e2b7;
}
.side-switch input {
  width: 14px;
  height: 14px;
  accent-color: #63e2b7;
}
.trade-preview {
  display: grid;
  grid-template-columns: 1fr 1fr;
  gap: 12px;
  padding: 16px 0;
  margin: 0;
  border-block: 1px solid #43474b;
  font-variant-numeric: tabular-nums;
}
.trade-preview dt {
  color: #9ca6aa;
  font-size: 12px;
}
.trade-preview dd {
  margin: 4px 0 0;
  font-size: 15px;
}
.form-actions {
  display: flex;
  gap: 10px;
  justify-content: flex-end;
}
@media (max-width: 420px) {
  .form-pair {
    gap: 10px;
  }
  .journal-form input {
    padding-inline: 6px;
  }
}
</style>
