import { createRouter, createWebHistory } from 'vue-router'
import DashboardView from '../views/DashboardView.vue'
import TransactionsView from '../views/TransactionsView.vue'
import PricesView from '../views/PricesView.vue'
import ConfigView from '../views/ConfigView.vue'
import InstrumentsView from '../views/InstrumentsView.vue'
import InvestmentPlansView from '../views/InvestmentPlansView.vue'
import ExchangeRatesView from '../views/ExchangeRatesView.vue'

const router = createRouter({
  history: createWebHistory(),
  routes: [
    { path: '/', name: 'dashboard', component: DashboardView },
    { path: '/plans', name: 'plans', component: InvestmentPlansView },
    { path: '/transactions', name: 'transactions', component: TransactionsView },
    { path: '/instruments', name: 'instruments', component: InstrumentsView },
    { path: '/prices', name: 'prices', component: PricesView },
    { path: '/exchange-rates', name: 'exchange-rates', component: ExchangeRatesView },
    { path: '/config', name: 'config', component: ConfigView },
  ],
})

export default router
