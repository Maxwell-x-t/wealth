<script setup>
import { computed, h, onMounted, ref } from "vue";
import {
  NAlert,
  NButton,
  NDataTable,
  NEmpty,
  NModal,
  NPopconfirm,
  NSelect,
  NSpin,
  NTabPane,
  NTabs,
  NTag,
  NTooltip,
  useMessage,
} from "naive-ui";
import {
  ArrowDownUp,
  ArrowUpRight,
  Check,
  CirclePlus,
  Pencil,
  RefreshCw,
  Trash2,
  Wallet,
  X,
} from "@lucide/vue";
import TradeEntry from "../components/TradeEntry.vue";
import {
  createCashEvent,
  deleteCashEvent,
  deleteTransaction,
  getCashEvents,
  getStrategyAccount,
  getStrategyAccounts,
  getStrategySignals,
  getTransactions,
  refreshPrices,
  refreshStrategySignals,
} from "../api/client";

const message = useMessage();
const accounts = ref([]),
  accountId = ref(null),
  account = ref(null),
  transactions = ref([]),
  events = ref([]),
  signals = ref(null);
const loading = ref(true),
  pageError = ref(""),
  refreshing = ref(false),
  analyzing = ref(false),
  removing = ref(false);
const tradeOpen = ref(false),
  editing = ref(null),
  selectedInstrument = ref(null),
  cashOpen = ref(false),
  savingCash = ref(false);
const cashError = ref(""),
  cashForm = ref({}),
  activeTab = ref("holdings"),
  showAll = ref(false);
const money = (value) =>
  value == null
    ? "--"
    : new Intl.NumberFormat("zh-CN", {
        minimumFractionDigits: 2,
        maximumFractionDigits: 2,
      }).format(value);
const percent = (value) => (value == null ? "--" : `${value.toFixed(2)}%`);
const today = () => new Date().toLocaleDateString("sv-SE");
const kindNames = {
  deposit: "转入",
  withdrawal: "转出",
  dividend: "现金分红",
  fee: "税费扣款",
  shares: "送转调整",
};
const errorText = (error) =>
  typeof error.response?.data?.detail === "string"
    ? error.response.data.detail
    : "服务暂不可用，请重试";
const rows = computed(
  () =>
    account.value?.holdings.filter(
      (h) => showAll.value || h.quantity > 0 || h.code === "sh512890",
    ) || [],
);
const allocations = computed(() => {
  const a = account.value;
  if (!a?.equity) return [];
  return [
    ["现金", a.cash, "#63e2b7"],
    [
      "红利个股",
      a.holdings
        .filter((h) => h.sleeve === "grid")
        .reduce((n, h) => n + (h.market_value || 0), 0),
      "#7caff1",
    ],
    [
      "红利低波ETF",
      a.holdings
        .filter((h) => h.sleeve === "rsi")
        .reduce((n, h) => n + (h.market_value || 0), 0),
      "#e5c76b",
    ],
    [
      "其他ETF",
      a.holdings
        .filter((h) => h.sleeve === "other")
        .reduce((n, h) => n + (h.market_value || 0), 0),
      "#e799a5",
    ],
  ].map(([name, value, color]) => ({
    name,
    value,
    color,
    pct: (value / a.equity) * 100,
  }));
});
const iconButton = (icon, title, onClick) =>
  h(
    NTooltip,
    {},
    {
      trigger: () =>
        h(
          NButton,
          {
            quaternary: true,
            size: "small",
            "aria-label": title,
            onClick,
            disabled: removing.value,
          },
          { icon: () => h(icon, { size: 16 }) },
        ),
      default: () => title,
    },
  );
const deleteButton = (title, action) =>
  h(
    NPopconfirm,
    { onPositiveClick: action, positiveText: "删除", negativeText: "取消" },
    {
      trigger: () =>
        h(
          NButton,
          {
            quaternary: true,
            size: "small",
            "aria-label": title,
            title,
            disabled: removing.value,
          },
          { icon: () => h(Trash2, { size: 16 }) },
        ),
      default: () => "删除这条记录并重算余额？",
    },
  );
const holdingColumns = [
  {
    title: "证券",
    key: "name",
    width: 192,
    fixed: "left",
    render: (row) =>
      h("div", [
        h("div", { class: "security-name" }, row.name),
        h("div", { class: "security-code" }, row.code.slice(2)),
      ]),
  },
  {
    title: "实际股数 / 份额",
    key: "quantity",
    align: "right",
    width: 140,
    render: (r) => r.quantity.toLocaleString("zh-CN"),
  },
  {
    title: "参考价",
    key: "price",
    align: "right",
    width: 110,
    render: (r) =>
      r.price == null ? "--" : r.price.toFixed(r.sleeve === "grid" ? 2 : 3),
  },
  {
    title: "市值（元）",
    key: "market_value",
    align: "right",
    width: 144,
    render: (r) => money(r.market_value),
  },
  {
    title: "资产占比",
    key: "weight",
    align: "right",
    width: 100,
    render: (r) => percent(r.weight),
  },
  {
    title: "策略",
    key: "sleeve",
    width: 115,
    render: (r) =>
      h(
        NTag,
        {
          size: "small",
          bordered: false,
          type:
            r.sleeve === "grid"
              ? "info"
              : r.sleeve === "rsi"
                ? "warning"
                : "default",
        },
        {
          default: () =>
            ({ grid: "红利网格", rsi: "周线 RSI6", other: "仅记账" })[r.sleeve],
        },
      ),
  },
  {
    title: "行情日期",
    key: "price_date",
    width: 110,
    render: (r) => r.price_date || "--",
  },
  {
    title: "",
    key: "actions",
    width: 52,
    render: (r) =>
      iconButton(CirclePlus, `录入${r.name}成交`, () =>
        openTrade(null, r.instrument_id),
      ),
  },
];
const transactionColumns = [
  { title: "日期", key: "trade_date", width: 112 },
  { title: "证券", key: "instrument_name", width: 148 },
  {
    title: "方向",
    key: "side",
    width: 72,
    render: (r) =>
      h(
        "span",
        { class: r.side === "buy" ? "positive" : "negative" },
        r.side === "buy" ? "买入" : "卖出",
      ),
  },
  { title: "数量", key: "quantity", width: 100, align: "right" },
  { title: "价格", key: "price", width: 94, align: "right" },
  {
    title: "成交金额",
    key: "amount",
    width: 122,
    align: "right",
    render: (r) => money(r.amount),
  },
  {
    title: "税费",
    key: "fee",
    width: 80,
    align: "right",
    render: (r) => money(r.fee),
  },
  { title: "备注", key: "note", width: 160, ellipsis: { tooltip: true } },
  {
    title: "",
    key: "actions",
    width: 84,
    render: (r) =>
      h("div", { class: "row-tools" }, [
        iconButton(Pencil, "修改成交", () => openTrade(r)),
        deleteButton("删除成交", () => remove("transaction", r.id)),
      ]),
  },
];
const cashColumns = [
  { title: "日期", key: "event_date", width: 110 },
  { title: "类型", key: "kind", width: 116, render: (r) => kindNames[r.kind] },
  {
    title: "证券",
    key: "instrument_id",
    width: 150,
    render: (r) =>
      account.value?.holdings.find((h) => h.instrument_id === r.instrument_id)
        ?.name || "--",
  },
  {
    title: "金额 / 股数变动",
    key: "amount",
    width: 160,
    align: "right",
    render: (r) =>
      r.kind === "shares"
        ? `${r.quantity > 0 ? "+" : ""}${r.quantity} 股`
        : `${["withdrawal", "fee"].includes(r.kind) ? "-" : "+"}${money(r.amount)}`,
  },
  { title: "备注", key: "note", width: 180 },
  {
    title: "",
    key: "actions",
    width: 52,
    render: (r) => deleteButton("删除资金记录", () => remove("cash", r.id)),
  },
];
const signalColumns = [
  { title: "证券", key: "stock.name", width: 140, render: (r) => r.stock.name },
  {
    title: "股息率",
    key: "stock.dividend_yield",
    width: 96,
    align: "right",
    render: (r) =>
      r.stock.dividend_available ? percent(r.stock.dividend_yield) : "--",
  },
  {
    title: "信号",
    key: "action",
    width: 100,
    render: (r) =>
      ({ BUY: "买入", SELL: "卖出", HOLD: "维持", HOLD_BAND: "持有带" })[
        r.action
      ],
  },
  {
    title: "目标占比",
    key: "target_shares",
    width: 100,
    align: "right",
    render: (r) => percent(r.target_shares * r.share_value_pct),
  },
  { title: "判定", key: "band_label", minWidth: 280 },
];

async function load() {
  if (!accountId.value) return;
  loading.value = true;
  pageError.value = "";
  try {
    const [a, t, e, s] = await Promise.all([
      getStrategyAccount(accountId.value),
      getTransactions({ account_id: accountId.value }),
      getCashEvents(accountId.value),
      getStrategySignals(accountId.value),
    ]);
    account.value = a;
    transactions.value = t;
    events.value = e;
    signals.value = s;
  } catch (error) {
    pageError.value = errorText(error);
  } finally {
    loading.value = false;
  }
}
onMounted(async () => {
  try {
    accounts.value = await getStrategyAccounts();
    accountId.value = accounts.value[0]?.id;
    await load();
  } catch (error) {
    pageError.value = errorText(error);
  } finally {
    loading.value = false;
  }
});
function openTrade(transaction = null, instrumentId = null) {
  editing.value = transaction;
  selectedInstrument.value = instrumentId;
  tradeOpen.value = true;
}
function openCash() {
  cashError.value = "";
  cashForm.value = {
    event_date: today(),
    kind: "deposit",
    amount: "",
    quantity: "",
    instrument_id: "",
    note: "",
    request_id: crypto.randomUUID(),
  };
  cashOpen.value = true;
}
async function saveCash() {
  if (savingCash.value) return;
  savingCash.value = true;
  cashError.value = "";
  try {
    const f = cashForm.value;
    await createCashEvent(accountId.value, {
      ...f,
      instrument_id: ["dividend", "shares"].includes(f.kind)
        ? Number(f.instrument_id)
        : null,
      quantity: f.kind === "shares" ? Number(f.quantity) : 0,
      amount: f.kind === "shares" ? 0 : Number(f.amount),
    });
    cashOpen.value = false;
    message.success("资金记录已保存");
    await load();
  } catch (error) {
    cashError.value = errorText(error);
  } finally {
    savingCash.value = false;
  }
}
async function remove(type, id) {
  removing.value = true;
  try {
    await (type === "cash"
      ? deleteCashEvent(accountId.value, id)
      : deleteTransaction(id));
    message.success("记录已删除，余额已重算");
    await load();
  } catch (error) {
    message.error(errorText(error));
  } finally {
    removing.value = false;
  }
}
async function refresh() {
  refreshing.value = true;
  try {
    const result = await refreshPrices();
    result.fail_count
      ? message.warning(
          `${result.success_count} 项更新，${result.fail_count} 项未更新`,
        )
      : message.success("行情已更新");
    await load();
  } catch (error) {
    message.error(errorText(error));
  } finally {
    refreshing.value = false;
  }
}
async function analyze() {
  analyzing.value = true;
  try {
    signals.value = await refreshStrategySignals(accountId.value);
  } catch (error) {
    message.error(errorText(error));
  } finally {
    analyzing.value = false;
  }
}
</script>

<template>
  <main class="strategy-page">
    <header class="strategy-heading">
      <div>
        <p class="section-label">WEALTH / DIVIDEND ACCOUNT</p>
        <h1>红利账户</h1>
        <div class="account-meta">
          <NSelect
            v-if="accounts.length > 1"
            v-model:value="accountId"
            :options="accounts.map((a) => ({ label: a.name, value: a.id }))"
            @update:value="load"
            style="width: 160px"
          /><span v-else>{{ account?.name || "红利策略" }}</span
          ><span v-if="account">核对日 {{ account.as_of }}</span>
        </div>
      </div>
      <div class="page-actions">
        <NTooltip
          ><template #trigger
            ><NButton
              aria-label="刷新行情"
              :loading="refreshing"
              :disabled="!account"
              @click="refresh"
              ><template #icon
                ><RefreshCw :size="17" /></template></NButton></template
          >刷新行情</NTooltip
        ><NButton :disabled="!account" @click="openCash"
          ><template #icon><Wallet :size="17" /></template>资金记录</NButton
        ><NButton type="primary" :disabled="!account" @click="openTrade()"
          ><template #icon><CirclePlus :size="17" /></template>录入成交</NButton
        >
      </div>
    </header>
    <NAlert v-if="pageError" type="error" :show-icon="false"
      >{{ pageError }}<NButton text @click="load">重试</NButton></NAlert
    >
    <NSpin :show="loading">
      <template v-if="account">
        <NAlert
          v-if="account.valuation_error"
          type="warning"
          style="margin-top: 16px"
          >{{ account.valuation_error }}</NAlert
        >
        <NAlert
          v-if="account.demo"
          type="warning"
          :show-icon="false"
          style="margin-top: 16px"
          >演示账本 · 尚未接入现有交易数据库</NAlert
        >
        <section class="account-metrics" aria-label="账户余额">
          <div>
            <span>总资产（元）</span
            ><strong data-testid="equity">{{ money(account.equity) }}</strong
            ><small>期初 {{ account.opening_date }}</small>
          </div>
          <div>
            <span>可用现金（元）</span
            ><strong class="cash-value" data-testid="cash">{{
              money(account.cash)
            }}</strong
            ><small
              >现金比例
              {{
                account.equity
                  ? percent((account.cash / account.equity) * 100)
                  : "--"
              }}</small
            >
          </div>
          <div>
            <span>登记后收益（元）</span
            ><strong
              :class="
                account.pnl_since_opening > 0
                  ? 'positive'
                  : account.pnl_since_opening < 0
                    ? 'negative'
                    : ''
              "
              >{{ account.pnl_since_opening > 0 ? "+" : ""
              }}{{ money(account.pnl_since_opening) }}</strong
            ><small>起点：期初登记市值</small>
          </div>
        </section>
        <section class="allocation-band" aria-label="资产分布">
          <div
            class="allocation-track"
            role="img"
            :aria-label="
              allocations.map((a) => `${a.name} ${percent(a.pct)}`).join('，')
            "
          >
            <span
              v-for="item in allocations"
              :key="item.name"
              :style="{ width: `${item.pct}%`, background: item.color }"
              :title="`${item.name} ${money(item.value)}元`"
            ></span>
          </div>
          <div class="allocation-legend">
            <span v-for="item in allocations" :key="item.name"
              ><i :style="{ background: item.color }"></i>{{ item.name }}
              <b>{{ percent(item.pct) }}</b></span
            >
          </div>
        </section>
        <p class="strategy-config-title">红利策略配置 · 独立于指数配置</p>
        <section class="limit-band" aria-label="红利策略配置">
          <div>
            <span>现金底线</span><b>{{ account.min_cash_pct }}%</b>
          </div>
          <div>
            <span>512890 资金上限</span><b>{{ account.etf_budget_pct }}%</b>
          </div>
          <div>
            <span>512890 实际层数</span><b>{{ account.etf_layers }} / 10</b>
          </div>
          <div>
            <span>个股资金余量</span
            ><b>¥ {{ money(account.stock_cash_available) }}</b>
          </div>
        </section>
        <NTabs
          v-model:value="activeTab"
          type="line"
          animated
          class="journal-tabs"
        >
          <NTabPane name="holdings" tab="持仓"
            ><div class="table-heading">
              <span
                >{{
                  account.holdings.filter((h) => h.quantity > 0).length
                }}
                项持仓</span
              ><label class="list-toggle"
                ><input
                  v-model="showAll"
                  type="checkbox"
                />显示全部监控证券</label
              >
            </div>
            <NDataTable
              :columns="holdingColumns"
              :data="rows"
              :row-key="(row) => row.instrument_id"
              :scroll-x="1063"
              :bordered="false"
              size="small"
          /></NTabPane>
          <NTabPane name="transactions" :tab="`成交 (${transactions.length})`"
            ><NDataTable
              :columns="transactionColumns"
              :data="transactions"
              :scroll-x="1072"
              :bordered="false"
              :pagination="{ pageSize: 15 }"
              :row-key="(r) => r.id"
              ><template #empty
                ><NEmpty description="暂无登记后成交"
                  ><template #extra
                    ><NButton @click="openTrade()">录入成交</NButton></template
                  ></NEmpty
                ></template
              ></NDataTable
            ></NTabPane
          >
          <NTabPane name="cash" :tab="`资金 (${events.length})`"
            ><NDataTable
              :columns="cashColumns"
              :data="events"
              :scroll-x="870"
              :bordered="false"
              :pagination="{ pageSize: 15 }"
              :row-key="(r) => r.id"
              ><template #empty
                ><NEmpty description="暂无资金变动"
                  ><template #extra
                    ><NButton @click="openCash">资金记录</NButton></template
                  ></NEmpty
                ></template
              ></NDataTable
            ></NTabPane
          >
          <NTabPane name="signals" tab="策略信号"
            ><div class="table-heading">
              <span>{{
                signals
                  ? `计算时间 ${signals.as_of.replace("T", " ")}`
                  : "尚未计算"
              }}</span
              ><NButton :loading="analyzing" @click="analyze"
                ><template #icon><RefreshCw :size="16" /></template
                >{{ analyzing ? "计算中" : "刷新策略" }}</NButton
              >
            </div>
            <NAlert
              v-if="signals?.stale"
              type="warning"
              style="margin-bottom: 12px"
              >账本或交易日已变化，当前信号已过期</NAlert
            ><template v-if="signals && !signals.stale"
              ><NAlert
                v-for="error in signals.errors"
                :key="error"
                type="warning"
                style="margin-bottom: 12px"
                >{{ error }}</NAlert
              >
              <div v-if="signals.rsi" class="rsi-summary">
                <b>512890 · 周线 RSI6</b
                ><span>RSI {{ signals.rsi.rsi.toFixed(2) }}</span
                ><span>目标 {{ signals.rsi.target_layers }} 层</span
                ><span>{{
                  { BUY: "买入", SELL: "卖出", HOLD: "维持", FROZEN: "本周暂停买入" }[
                    signals.rsi.action
                  ] || signals.rsi.action
                }}</span>
              </div>
              <NDataTable
                v-if="signals.grid"
                :columns="signalColumns"
                :data="signals.grid.decisions"
                :scroll-x="850"
                :bordered="false"
              /><NAlert
                v-for="warning in signals.grid?.warnings || []"
                :key="warning"
                type="warning"
                style="margin-top: 12px"
                >{{ warning }}</NAlert
              ></template
            ><NEmpty
              v-else-if="!signals"
              description="暂无策略结果"
              style="padding-block: 48px"
          /></NTabPane>
        </NTabs>
      </template>
      <NEmpty
        v-else-if="!loading && !pageError"
        description="现有账本待接入"
        style="padding: 100px 0"
      />
    </NSpin>
    <TradeEntry
      v-model:show="tradeOpen"
      :account="account"
      :transaction="editing"
      :instrument-id="selectedInstrument"
      @saved="load"
    />
    <NModal
      v-model:show="cashOpen"
      preset="card"
      title="资金记录"
      style="width: min(500px, calc(100vw - 28px))"
      :mask-closable="!savingCash"
      :close-on-esc="!savingCash"
    >
      <form class="journal-form" @submit.prevent="saveCash">
        <div class="form-pair">
          <label
            >变动类型<select v-model="cashForm.kind" :disabled="savingCash">
              <option
                v-for="(label, kind) in kindNames"
                :key="kind"
                :value="kind"
              >
                {{ label }}
              </option>
            </select></label
          ><label
            >记账日期<input
              v-model="cashForm.event_date"
              type="date"
              :min="account?.opening_date"
              :max="today()"
              required
              :disabled="savingCash"
          /></label>
        </div>
        <label v-if="['dividend', 'shares'].includes(cashForm.kind)"
          >关联证券<select
            v-model="cashForm.instrument_id"
            required
            :disabled="savingCash"
          >
            <option value="" disabled>请选择证券</option>
            <option
              v-for="holding in account?.holdings"
              :key="holding.instrument_id"
              :value="holding.instrument_id"
            >
              {{ holding.code.slice(2) }} {{ holding.name }}
            </option>
          </select></label
        ><label v-if="cashForm.kind === 'shares'"
          >股数变动<input
            v-model="cashForm.quantity"
            type="number"
            step="1"
            required
            :disabled="savingCash" /></label
        ><label v-else
          >实际金额（元）<input
            v-model="cashForm.amount"
            type="number"
            min="0.01"
            step="0.01"
            required
            :disabled="savingCash" /></label
        ><label
          >备注<input
            v-model="cashForm.note"
            maxlength="1000"
            :disabled="savingCash" /></label
        ><NAlert v-if="cashError" type="error" role="alert">{{
          cashError
        }}</NAlert>
        <div class="form-actions">
          <NButton :disabled="savingCash" @click="cashOpen = false"
            ><template #icon><X :size="16" /></template>取消</NButton
          ><NButton type="primary" attr-type="submit" :loading="savingCash"
            ><template #icon><Check :size="16" /></template>保存记录</NButton
          >
        </div>
      </form>
    </NModal>
  </main>
</template>

<style scoped>
.strategy-page {
  max-width: 1440px;
  margin: 0 auto;
  color: #edf0ef;
}
.strategy-heading {
  display: flex;
  justify-content: space-between;
  align-items: center;
  gap: 20px;
  padding: 8px 0 24px;
}
.section-label {
  color: #98a2a5;
  font-size: 11px;
  margin: 0 0 7px;
}
h1 {
  font-size: 24px;
  line-height: 1.4;
  font-weight: 600;
  margin: 0 0 7px;
}
.account-meta,
.page-actions {
  display: flex;
  align-items: center;
  gap: 12px;
  flex-wrap: wrap;
}
.account-meta {
  font-size: 12px;
  color: #a8b0b2;
}
.account-meta span + span {
  padding-left: 12px;
  border-left: 1px solid #41474b;
}
.account-metrics {
  display: grid;
  grid-template-columns: repeat(3, minmax(0, 1fr));
  padding: 24px 0;
  border-top: 1px solid #363d40;
  gap: 20px;
}
.account-metrics > div {
  display: grid;
  gap: 6px;
}
.account-metrics span,
.account-metrics small {
  font-size: 12px;
  color: #a0aaad;
}
.account-metrics strong {
  font-size: 28px;
  line-height: 1.4;
  font-weight: 550;
  font-variant-numeric: tabular-nums;
  overflow-wrap: anywhere;
}
.cash-value {
  color: #63e2b7;
}
.allocation-band {
  padding-bottom: 24px;
}
.allocation-track {
  width: 100%;
  height: 10px;
  display: flex;
  background: #353d40;
  gap: 3px;
  overflow: hidden;
}
.allocation-track span {
  min-width: 0;
  flex-shrink: 1;
}
.allocation-legend {
  display: flex;
  flex-wrap: wrap;
  gap: 12px 24px;
  margin-top: 12px;
  font-size: 12px;
  color: #abb4b7;
}
.allocation-legend span {
  display: flex;
  gap: 6px;
  align-items: center;
}
.allocation-legend b {
  font-weight: 500;
  color: #e1e6e4;
  font-variant-numeric: tabular-nums;
}
.allocation-legend i {
  width: 7px;
  height: 7px;
  display: inline-block;
}
.limit-band {
  display: grid;
  grid-template-columns: repeat(4, minmax(0, 1fr));
  gap: 16px;
  padding: 16px 0;
  border-block: 1px solid #363d40;
}
.strategy-config-title {
  margin: 18px 0 0;
  color: #e1e6e4;
  font-size: 13px;
  font-weight: 600;
}
.limit-band div {
  display: flex;
  flex-wrap: wrap;
  align-items: baseline;
  gap: 6px 10px;
}
.limit-band span {
  font-size: 12px;
  color: #a0aaad;
}
.limit-band b {
  font-size: 14px;
  font-weight: 500;
}
.journal-tabs {
  margin-top: 18px;
}
.table-heading {
  display: flex;
  justify-content: space-between;
  align-items: center;
  gap: 12px;
  min-height: 42px;
  margin-bottom: 8px;
  font-size: 12px;
  color: #a0aaad;
}
.list-toggle {
  display: flex;
  gap: 7px;
  align-items: center;
}
.list-toggle input {
  accent-color: #63e2b7;
}
:deep(.security-code) {
  color: #969fa3;
  font-size: 11px;
  margin-top: 2px;
  font-variant-numeric: tabular-nums;
}
:deep(.security-name) {
  font-weight: 500;
}
:deep(.n-data-table-td) {
  font-variant-numeric: tabular-nums;
}
:deep(.row-tools) {
  display: flex;
  gap: 2px;
}
.rsi-summary {
  display: flex;
  flex-wrap: wrap;
  gap: 16px;
  padding: 18px 0;
  border-bottom: 1px solid #363d40;
  margin-bottom: 16px;
}
@media (max-width: 760px) {
  .strategy-heading {
    align-items: flex-start;
    flex-direction: column;
    gap: 16px;
  }
  .page-actions {
    gap: 8px;
    width: 100%;
  }
  .page-actions > :last-child {
    margin-left: auto;
  }
  .account-metrics {
    gap: 20px 12px;
    grid-template-columns: 1fr 1fr;
  }
  .account-metrics > div:first-child {
    grid-column: 1 / -1;
  }
  .account-metrics strong {
    font-size: 22px;
  }
  .account-metrics > div:first-child strong {
    font-size: 28px;
  }
  .allocation-legend {
    display: grid;
    grid-template-columns: 1fr 1fr;
    gap: 10px;
  }
  .allocation-legend span {
    gap: 5px;
    font-size: 11px;
  }
  .limit-band {
    grid-template-columns: 1fr 1fr;
    gap: 14px;
  }
  .limit-band div {
    display: grid;
    gap: 4px;
  }
  .table-heading {
    align-items: flex-start;
  }
}
</style>
