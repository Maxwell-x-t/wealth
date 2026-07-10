import axios from 'axios'

const client = axios.create({
  baseURL: '/api',
  timeout: 15000,
})

export const getDashboard = () => client.get('/dashboard').then((r) => r.data)
export const getDashboardHistory = () => client.get('/dashboard/history').then((r) => r.data)
export const getConfig = () => client.get('/config').then((r) => r.data)
export const updateConfig = (data) => client.put('/config', data).then((r) => r.data)

export const getAccounts = () => client.get('/accounts').then((r) => r.data)
export const getInstruments = (activeOnly = false) =>
  client.get('/instruments', { params: activeOnly ? { active_only: true } : {} }).then((r) => r.data)
export const createInstrument = (data) => client.post('/instruments', data).then((r) => r.data)
export const updateInstrument = (id, data) => client.put(`/instruments/${id}`, data).then((r) => r.data)
export const deleteInstrument = (id) => client.delete(`/instruments/${id}`).then((r) => r.data)

export const getTransactions = (params = {}) => client.get('/transactions', { params }).then((r) => r.data)
export const createTransaction = (data) => client.post('/transactions', data).then((r) => r.data)
export const updateTransaction = (id, data) => client.put(`/transactions/${id}`, data).then((r) => r.data)
export const deleteTransaction = (id) => client.delete(`/transactions/${id}`).then((r) => r.data)

export const getInvestmentPlans = (params = {}) =>
  client.get('/investment-plans', { params }).then((r) => r.data)
export const getInvestmentPlanOverview = () =>
  client.get('/investment-plans/overview').then((r) => r.data)
export const skipInvestmentPlan = (data) =>
  client.post('/investment-plans/skip', data).then((r) => r.data)

export const getWealthForecast = (params = {}) =>
  client.get('/forecast', { params }).then((r) => r.data)
export const getRiskSimulation = (params = {}) =>
  client.get('/forecast/risk', { params }).then((r) => r.data)

export const getHistoricalBacktest = (params = {}) =>
  client.get('/backtest', { params, timeout: 60000 }).then((r) => r.data)

export const getAnnualReview = (params = {}) =>
  client.get('/annual-review', { params }).then((r) => r.data)
export const updateAnnualReview = (data) =>
  client.put('/annual-review', data).then((r) => r.data)

export const getSyncStatus = () => client.get('/sync/status').then((r) => r.data)
export const runSyncNow = () =>
  client.post('/sync/run', null, { timeout: 60000 }).then((r) => r.data)

export const getPrices = () => client.get('/prices').then((r) => r.data)
export const updatePrice = (data) => client.post('/prices', data).then((r) => r.data)
export const refreshPrices = () =>
  client.post('/prices/refresh', null, { timeout: 60000 }).then((r) => r.data)

export const getLatestExchangeRate = () => client.get('/exchange-rates/latest').then((r) => r.data)
export const getExchangeRateHistory = () => client.get('/exchange-rates').then((r) => r.data)
export const createExchangeRate = (data) => client.post('/exchange-rates', data).then((r) => r.data)
export const refreshExchangeRate = () =>
  client.post('/exchange-rates/refresh', null, { timeout: 20000 }).then((r) => r.data)
