export function formatMoney(value, currency = 'CNY') {
  if (value === null || value === undefined || Number.isNaN(value)) return '-'
  const prefix = currency === 'USD' ? '$' : '¥'
  return `${prefix}${Number(value).toLocaleString('zh-CN', {
    minimumFractionDigits: 2,
    maximumFractionDigits: 2,
  })}`
}

export function formatPercent(value) {
  if (value === null || value === undefined) return '-'
  return `${Number(value).toFixed(2)}%`
}

export function formatNumber(value, digits = 2) {
  if (value === null || value === undefined) return '-'
  return Number(value).toLocaleString('zh-CN', {
    minimumFractionDigits: digits,
    maximumFractionDigits: digits,
  })
}
