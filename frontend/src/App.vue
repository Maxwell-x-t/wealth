<script setup>
import { h, ref } from 'vue'
import { RouterLink, RouterView, useRoute, useRouter } from 'vue-router'
import { Activity, CalendarDays, ChartNoAxesCombined, ClipboardList, Coins, Gauge, Landmark, List, Settings, TrendingUp, Wallet } from '@lucide/vue'
import {
  NConfigProvider,
  NLayout,
  NLayoutSider,
  NLayoutContent,
  NMenu,
  darkTheme,
  NMessageProvider,
  NSelect,
} from 'naive-ui'

const route = useRoute()
const router = useRouter()
const collapsed = ref(false)

const menuOptions = [
  { label: () => h(RouterLink, { to: '/strategy' }, { default: () => '策略账户' }), key: 'strategy' },
  { label: () => h(RouterLink, { to: '/' }, { default: () => '总览' }), key: 'dashboard' },
  { label: () => h(RouterLink, { to: '/plans' }, { default: () => '投资计划' }), key: 'plans' },
  { label: () => h(RouterLink, { to: '/forecast' }, { default: () => '财富预测' }), key: 'forecast' },
  { label: () => h(RouterLink, { to: '/backtest' }, { default: () => '历史回测' }), key: 'backtest' },
  { label: () => h(RouterLink, { to: '/annual-review' }, { default: () => '年度检查' }), key: 'annual-review' },
  { label: () => h(RouterLink, { to: '/transactions' }, { default: () => '交易记录' }), key: 'transactions' },
  { label: () => h(RouterLink, { to: '/instruments' }, { default: () => '品种管理' }), key: 'instruments' },
  { label: () => h(RouterLink, { to: '/prices' }, { default: () => '行情更新' }), key: 'prices' },
  { label: () => h(RouterLink, { to: '/exchange-rates' }, { default: () => '汇率更新' }), key: 'exchange-rates' },
  { label: () => h(RouterLink, { to: '/config' }, { default: () => '参数配置' }), key: 'config' },
]
const icons = [Wallet, Gauge, CalendarDays, TrendingUp, ChartNoAxesCombined, ClipboardList, List, Landmark, Activity, Coins, Settings]
menuOptions.forEach((option, i) => { option.icon = () => h(icons[i], { size: 18 }) })
const mobileOptions = menuOptions.map(option => ({ label: option.label().children.default(), value: option.key }))

const activeKey = () => route.name
</script>

<template>
  <NConfigProvider :theme="darkTheme">
    <NMessageProvider :max="1" :duration="1800">
      <div class="mobile-navigation"><b>Wealth</b><NSelect :value="String(route.name || 'strategy')" :options="mobileOptions" @update:value="name => router.push({ name })" aria-label="页面导航" style="width: 152px" /></div>
      <NLayout has-sider style="min-height: 100vh; background: #171a1c">
        <NLayoutSider
          class="desktop-navigation"
          bordered
          collapse-mode="width"
          :collapsed-width="64"
          :width="200"
          :collapsed="collapsed"
          show-trigger
          @collapse="collapsed = true"
          @expand="collapsed = false"
          style="background: #1b1e20"
        >
          <div class="brand">
            <div class="brand-title">{{ collapsed ? 'W' : 'Wealth' }}</div>
            <div v-if="!collapsed" class="brand-sub">个人投资账本</div>
          </div>
          <NMenu
            :collapsed="collapsed"
            :collapsed-width="64"
            :collapsed-icon-size="22"
            :options="menuOptions"
            :value="activeKey()"
          />
        </NLayoutSider>
        <NLayoutContent content-style="padding: 20px 24px; min-width: 0;" class="app-content" style="background: #171a1c; min-width: 0;">
          <RouterView />
        </NLayoutContent>
      </NLayout>
    </NMessageProvider>
  </NConfigProvider>
</template>

<style scoped>
.brand {
  padding: 18px 16px 12px;
  border-bottom: 1px solid #1f2a37;
  margin-bottom: 8px;
}

.brand-title {
  font-size: 16px;
  font-weight: 700;
  color: #f5f7fa;
}

.brand-sub {
  margin-top: 4px;
  font-size: 12px;
  color: #8b98a5;
}
.mobile-navigation { display: none; }
@media(max-width: 760px) {
  .desktop-navigation { display: none; }
  .mobile-navigation { display: flex; justify-content: space-between; align-items: center; padding: 12px 16px; background: #1b1e20; border-bottom: 1px solid #363d40; }
  .mobile-navigation b { font-size: 18px; }
  :deep(.app-content > .n-layout-scroll-container) { padding: 16px !important; }
}
</style>
