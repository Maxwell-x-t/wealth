/**
 * 检测 ETF 交易是否可能将「数量」与「成交价」填反。
 * 返回建议值 { quantity, price }，否则 null。
 */
export function detectQuantityPriceSwap(quantity, price, currency = 'CNY') {
  const q = Number(quantity)
  const p = Number(price)
  if (!Number.isFinite(q) || !Number.isFinite(p) || q <= 0 || p <= 0) {
    return null
  }

  if (currency === 'USD') {
    // 美股 ETF 单价通常几十到几百；若数量很大而单价很小，可能填反
    if (q >= 20 && p < 20) {
      const swappedQty = p
      const swappedPrice = q
      if (swappedPrice >= 20 && swappedPrice <= 2000 && swappedQty >= 0.0001 && swappedQty < 20) {
        return { quantity: swappedQty, price: swappedPrice }
      }
    }
    return null
  }

  // 人民币 ETF 成交价多在 0.1–50 元
  const normalPriceMin = 0.1
  const normalPriceMax = 50
  const suspicious = p > normalPriceMax || (p > 10 && q < 100)
  if (!suspicious) {
    return null
  }

  const swappedQty = p
  const swappedPrice = q
  if (
    swappedPrice >= normalPriceMin
    && swappedPrice <= normalPriceMax
    && swappedQty >= 50
  ) {
    return { quantity: swappedQty, price: swappedPrice }
  }

  return null
}

export function isSuspiciousTransaction(row) {
  if (!row?.quantity || !row?.price) return false
  return Boolean(detectQuantityPriceSwap(row.quantity, row.price, row.currency || 'CNY'))
}
