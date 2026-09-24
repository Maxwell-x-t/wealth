export function formatMoney(value, currency = 'CNY') {
  if (value === null || value === undefined || Number.isNaN(value)) return '-'
  const prefix = currency === 'USD' ? '$' : '¥'
  return `${prefix}${Number(value).toLocaleString('zh-CN', {
    minimumFractionDigits: 2,
    maximumFractionDigits: 2,
  })}`
}

/** 单价（成本价/现价/成交价/行情价），固定 3 位小数 */
export function formatPrice(value, currency = 'CNY') {
  if (value === null || value === undefined || Number.isNaN(value)) return '-'
  const prefix = currency === 'USD' || currency === '$' ? '$' : '¥'
  return `${prefix}${Number(value).toLocaleString('zh-CN', {
    minimumFractionDigits: 3,
    maximumFractionDigits: 3,
  })}`
}

/** 大陆 ETF/LOF。个股不展示溢价。 */
export function isCnEtf(code) {
  const normalized = String(code || '').trim().replace(/^(sh|sz)/i, '')
  return /^(15|16|50|51|56|58)/.test(normalized)
}

/** ETF 溢价率：正=溢价，负=折价。传入代码时，非大陆 ETF 不展示。 */
export function formatPremiumRate(value, code) {
  if (code !== undefined && !isCnEtf(code)) return null
  if (value === null || value === undefined || Number.isNaN(Number(value))) return null
  const n = Number(value)
  const sign = n > 0 ? '+' : ''
  return `${sign}${n.toFixed(2)}%`
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

/** 日期选择器时间戳 → 本地 YYYY-MM-DD（避免 toISOString 在 UTC+8 差一天） */
export function formatLocalDate(value) {
  if (value === null || value === undefined || value === '') return null
  const date = new Date(value)
  if (Number.isNaN(date.getTime())) return null
  const year = date.getFullYear()
  const month = String(date.getMonth() + 1).padStart(2, '0')
  const day = String(date.getDate()).padStart(2, '0')
  return `${year}-${month}-${day}`
}
