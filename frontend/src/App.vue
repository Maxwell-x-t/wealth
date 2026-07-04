<script setup>
import { h, ref } from 'vue'
import { RouterLink, RouterView, useRoute } from 'vue-router'
import {
  NConfigProvider,
  NLayout,
  NLayoutSider,
  NLayoutContent,
  NMenu,
  darkTheme,
  NMessageProvider,
} from 'naive-ui'

const route = useRoute()
const collapsed = ref(false)

const menuOptions = [
  { label: () => h(RouterLink, { to: '/' }, { default: () => '总览' }), key: 'dashboard' },
  { label: () => h(RouterLink, { to: '/plans' }, { default: () => '投资计划' }), key: 'plans' },
  { label: () => h(RouterLink, { to: '/forecast' }, { default: () => '财富预测' }), key: 'forecast' },
  { label: () => h(RouterLink, { to: '/annual-review' }, { default: () => '年度检查' }), key: 'annual-review' },
  { label: () => h(RouterLink, { to: '/transactions' }, { default: () => '交易记录' }), key: 'transactions' },
  { label: () => h(RouterLink, { to: '/instruments' }, { default: () => '品种管理' }), key: 'instruments' },
  { label: () => h(RouterLink, { to: '/prices' }, { default: () => '行情更新' }), key: 'prices' },
  { label: () => h(RouterLink, { to: '/exchange-rates' }, { default: () => '汇率更新' }), key: 'exchange-rates' },
  { label: () => h(RouterLink, { to: '/config' }, { default: () => '参数配置' }), key: 'config' },
]

const activeKey = () => route.name
</script>

<template>
  <NConfigProvider :theme="darkTheme">
    <NMessageProvider>
      <NLayout has-sider style="min-height: 100vh; background: #0b0f14">
        <NLayoutSider
          bordered
          collapse-mode="width"
          :collapsed-width="64"
          :width="200"
          :collapsed="collapsed"
          show-trigger
          @collapse="collapsed = true"
          @expand="collapsed = false"
          style="background: #0f141b"
        >
          <div class="brand">
            <div class="brand-title">Investment OS</div>
            <div class="brand-sub">个人长期投资系统</div>
          </div>
          <NMenu
            :collapsed="collapsed"
            :collapsed-width="64"
            :collapsed-icon-size="22"
            :options="menuOptions"
            :value="activeKey()"
          />
        </NLayoutSider>
        <NLayoutContent content-style="padding: 20px 24px;">
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
</style>
