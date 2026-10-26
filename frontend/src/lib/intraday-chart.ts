import type { MinuteKlineRow } from '@/lib/api'

/** 分时 datetime（如 "01:30:00Z"，HH:mm:ss 为 UTC 时分秒）→ 当日 UTC epoch 毫秒。
 *  与分时图 time 轴 min/max（Date.UTC 构造）对齐。 */
export function minuteDatetimeToMs(datetime: string): number {
  const m = datetime.match(/^(\d{2}):(\d{2}):(\d{2})/)
  if (!m) return 0
  const h = parseInt(m[1], 10)
  const min = parseInt(m[2], 10)
  const sec = parseInt(m[3], 10)
  const now = new Date()
  return Date.UTC(now.getFullYear(), now.getMonth(), now.getDate(), h, min, sec)
}

/** 成交量柱着色：A 股涨红跌绿，平盘中性灰 */
export function volItemStyle(close: number, open: number): string {
  return close > open
    ? 'rgba(240,68,56,0.6)'
    : close < open
      ? 'rgba(18,183,106,0.6)'
      : 'rgba(161,161,170,0.5)'
}

/** 成交量轴/悬浮提示格式化：≥1亿 → x.xx亿，≥1万 → x.x万，其余原值（单位：手） */
export function formatVolumeCN(v: number | null | undefined): string {
  if (v == null || !Number.isFinite(v)) return '—'
  const abs = Math.abs(v)
  if (abs >= 1e8) return `${(v / 1e8).toFixed(2)}亿`
  if (abs >= 1e4) return `${(v / 1e4).toFixed(1)}万`
  return String(Math.round(v))
}

/** 分时价格线下方渐变面积样式 */
export function intradayAreaStyle(areaColor: string): any {
  return {
    color: {
      type: 'linear',
      x: 0, y: 0, x2: 0, y2: 1,
      colorStops: [
        { offset: 0, color: areaColor },
        { offset: 1, color: 'rgba(0,0,0,0)' },
      ],
    },
  }
}

/** 分时图主图/副图 grid 配置（上下两栏） */
export function intradayGrids(): any[] {
  return [
    { left: 60, right: 55, top: 24, bottom: '34%' },
    { left: 60, right: 55, top: '69%', bottom: 20 },
  ]
}

/** 十字线虚线样式 */
export function dashedCrosshairStyle(color: string) {
  return { color, type: 'dashed' as const, width: 1 }
}

/** 十字线轴指示器标签样式 */
export function crosshairLabelStyle(
  tooltipBg: string,
  tooltipBorder: string,
  tooltipText: string,
  padding: [number, number] = [2, 4],
) {
  return {
    show: true,
    backgroundColor: tooltipBg,
    borderColor: tooltipBorder,
    borderWidth: 1,
    padding,
    color: tooltipText,
    fontSize: 10,
    fontFamily: 'JetBrains Mono, monospace',
  }
}

/** 分时图价格 markLine 数据组装：昨收基准线 + 用户画线（监控价格线等） */
export function buildPriceMarkLines(
  prevClose: number | undefined,
  priceLines: { value: number; label?: string; color?: string }[],
  crosshairColor: string,
  textColor: string,
  tooltipBg: string,
): any[] {
  const markLineData: any[] = []
  if (prevClose != null) {
    markLineData.push({
      yAxis: prevClose,
      lineStyle: dashedCrosshairStyle(crosshairColor),
      label: { show: false },
      symbol: 'none',
    })
  }
  for (const line of priceLines) {
    if (!Number.isFinite(line.value) || line.value <= 0) continue
    markLineData.push({
      yAxis: line.value,
      lineStyle: { color: line.color ?? textColor, type: 'dashed', width: 1, opacity: 0.92 },
      label: {
        show: !!line.label,
        formatter: line.label ?? '',
        position: 'insideEndTop',
        color: line.color ?? textColor,
        backgroundColor: tooltipBg,
        borderRadius: 4,
        padding: [2, 6],
        fontSize: 10,
        fontFamily: 'JetBrains Mono, monospace',
      },
      symbol: 'none',
    })
  }
  return markLineData
}

export function formatMinuteTime(datetime: string): string {
  const match = datetime.match(/(\d{2}):(\d{2})/)
  if (!match) return datetime.slice(11, 16)
  const hour = (parseInt(match[1]) + 8) % 24
  return `${String(hour).padStart(2, '0')}:${match[2]}`
}

export function computeIntradayAverage(data: MinuteKlineRow[]): number[] {
  const result: number[] = []
  let amount = 0
  let volume = 0
  for (const row of data) {
    amount += row.amount
    volume += row.volume * 100
    if (volume <= 0) {
      result.push(row.close)
      continue
    }
    const avg = amount / volume
    // 均价相对现价偏离 >30% 视为口径脏数据 (如成交额与成交量单位不匹配的
    // mock 历史数据), 该点回退为现价, 避免把价格/百分比轴拉到 0~680 这类
    // 离谱范围; 累计口径不重置, 数据源正常后均价自然贴合
    result.push(
      row.close > 0 && Math.abs(avg - row.close) / row.close <= 0.3 ? avg : row.close,
    )
  }
  return result
}

function generateFullDayTimes(): string[] {
  const times: string[] = []
  for (let hour = 9; hour <= 11; hour++) {
    const startMinute = hour === 9 ? 30 : 0
    const endMinute = hour === 11 ? 30 : 59
    for (let minute = startMinute; minute <= endMinute; minute++) {
      times.push(`${String(hour).padStart(2, '0')}:${String(minute).padStart(2, '0')}`)
    }
  }
  for (let hour = 13; hour <= 15; hour++) {
    const endMinute = hour === 15 ? 0 : 59
    for (let minute = 0; minute <= endMinute; minute++) {
      times.push(`${String(hour).padStart(2, '0')}:${String(minute).padStart(2, '0')}`)
    }
  }
  return times
}

export const FULL_DAY_TIMES = generateFullDayTimes()

/** 集合竞价时段 9:15-9:29（不含 9:30，9:30 已在 FULL_DAY_TIMES 中） */
export const CALL_AUCTION_TIMES: string[] = (() => {
  const times: string[] = []
  for (let minute = 15; minute <= 29; minute++) {
    times.push(`09:${String(minute).padStart(2, '0')}`)
  }
  return times
})()

/** 含集合竞价的完整一日时间轴 9:15-15:00 */
export const FULL_DAY_TIMES_WITH_CALL = [...CALL_AUCTION_TIMES, ...FULL_DAY_TIMES]
