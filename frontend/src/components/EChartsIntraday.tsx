import { useEffect, useMemo, useRef, useState } from 'react'
import * as echarts from 'echarts'
import type { ECharts, EChartsOption } from 'echarts'
import type { MinuteKlineRow, PriceLimitInfo } from '@/lib/api'
import {
  buildPriceMarkLines,
  computeIntradayAverage,
  crosshairLabelStyle,
  dashedCrosshairStyle,
  formatMinuteTime,
  FULL_DAY_TIMES,
  FULL_DAY_TIMES_WITH_CALL,
  intradayAreaStyle,
  intradayGrids,
  minuteDatetimeToMs,
  volItemStyle,
} from '@/lib/intraday-chart'
import { useChartTheme, type ChartTheme } from '@/lib/theme'

type YMode = 'adaptive' | 'limit'

// 均价线颜色 (双主题通用); 画布轴/网格/十字线等主题相关色走 ChartTheme
const THEME = {
  avgLine: '#F59E0B',
}

/** 最新价角标: 右上角展示 当前股价 + 相对昨收涨跌幅 (涨红跌绿)。 */
function latestPriceGraphic(latestClose: number | undefined, prevClose: number | undefined): any {
  if (latestClose == null || !Number.isFinite(latestClose)) return undefined
  if (!prevClose || prevClose <= 0 || !Number.isFinite(prevClose)) return undefined
  const pct = (latestClose - prevClose) / prevClose * 100
  const color = pct > 0.005 ? '#F04438' : pct < -0.005 ? '#12B76A' : '#98A2B3'
  const pctText = `${pct >= 0 ? '+' : ''}${pct.toFixed(2)}%`
  return {
    type: 'text',
    right: 62,
    top: 6,
    silent: true,
    style: {
      text: `${latestClose.toFixed(2)}  ${pctText}`,
      fill: color,
      fontSize: 12,
      fontWeight: 600,
      fontFamily: 'JetBrains Mono, monospace',
    },
  }
}

export interface IntradayTradeMark {
  ts_ms: number
  side: 'buy' | 'sell'
  price: number
}

interface Props {
  data: MinuteKlineRow[]
  height?: number
  prevClose?: number
  date?: string
  priceLimit?: PriceLimitInfo
  onPriceHover?: (price: number | null) => void
  onPriceDoubleClick?: (price: number, currentPrice: number) => void
  currentPrice?: number
  priceLines?: { value: number; label?: string; color?: string }[]
  showLimitLines?: boolean
  showAvgLine?: boolean
  /** T 信号 B/S 标记: 主图散点, B(红)=买入倾向在点下方, S(绿)=卖出倾向在点上方。 */
  tradeMarks?: IntradayTradeMark[]
  /** tick 视图模式：true 时只画 data 中已发生的分钟点，不铺满全日时间轴。
   *  用于 RealtimeStore tick 路径（mock/实时盘中增量生成），避免 areaStyle 把 9:30 一直拉到 14:57。
   *  默认 false（历史落库完整分时K，用全日时间轴）。 */
  tickMode?: boolean
}

const MARK_BUY = '#F04438'
const MARK_SELL = '#12B76A'

/** epoch ms → 北京时间 'HH:MM' (浏览器本地时区, 与用户环境一致)。 */
function tsToTimeLabel(ts: number): string {
  const d = new Date(ts)
  return `${String(d.getHours()).padStart(2, '0')}:${String(d.getMinutes()).padStart(2, '0')}`
}

/** B/S 标记散点 series: silent 不干扰十字光标与双击画线交互。 */
function tradeMarkSeries(marks: IntradayTradeMark[] | undefined, mapTs: (ts: number) => any): any[] {
  if (!marks || marks.length === 0) return []
  const data = marks
    .filter((m) => Number.isFinite(m.ts_ms) && isValidPrice(m.price))
    .map((m) => ({
      value: [mapTs(m.ts_ms), m.price],
      itemStyle: { color: m.side === 'sell' ? MARK_SELL : MARK_BUY },
      label: {
        show: true,
        formatter: m.side === 'sell' ? 'S' : 'B',
        position: m.side === 'sell' ? 'top' : 'bottom',
        color: m.side === 'sell' ? MARK_SELL : MARK_BUY,
        fontSize: 10,
        fontWeight: 700,
        fontFamily: 'JetBrains Mono, monospace',
        distance: 4,
      },
    }))
  if (data.length === 0) return []
  return [{
    name: 'T信号',
    type: 'scatter',
    xAxisIndex: 0,
    yAxisIndex: 0,
    symbol: 'circle',
    symbolSize: 12,
    z: 12,
    silent: true,
    cursor: 'default',
    data,
  }]
}

function fmtAmt(v: number): string {
  if (v >= 1_000_000_000) return `${(v / 1_000_000_000).toFixed(2)}亿`
  if (v >= 10_000) return `${(v / 10_000).toFixed(0)}万`
  return v.toFixed(0)
}

function isValidPrice(v: number | null | undefined): v is number {
  return typeof v === 'number' && Number.isFinite(v) && v > 0
}

/** 计算实际涨跌停价 (四舍五入到2位小数) 和实际涨跌停幅度 */
function getLimitPrices(prevClose: number, priceLimit?: PriceLimitInfo): {
  limitUp: number      // 涨停价 (四舍五入)
  limitDown: number    // 跌停价 (四舍五入)
  upPct: number        // 实际涨停幅度 (如 9.97)
  downPct: number      // 实际跌停幅度 (如 -9.97)
} {
  const pct = priceLimit && Number.isFinite(priceLimit.rate) ? priceLimit.rate : 0.10
  const rawUp = prevClose * (1 + pct)
  const rawDown = prevClose * (1 - pct)
  // A股涨跌停价四舍五入到分 (2位小数)
  const limitUp = isValidPrice(priceLimit?.limit_up)
    ? priceLimit.limit_up
    : Math.round(rawUp * 100) / 100
  const limitDown = isValidPrice(priceLimit?.limit_down)
    ? priceLimit.limit_down
    : Math.round(rawDown * 100) / 100
  const upPct = (limitUp - prevClose) / prevClose * 100
  const downPct = (limitDown - prevClose) / prevClose * 100
  return { limitUp, limitDown, upPct, downPct }
}

/**
 * tick 模式：用 type:'time' 轴 + [timestamp, value] 二元组数据。
 *
 * 为什么不能用 category 轴：category 轴把 data 数组的元素数等分到轴宽度，
 * 单个点会被画成贯穿整条 x 轴的横线（即使 closes 数组其余位置是 null 也一样）。
 *
 * time 轴按真实时间戳定位点位置，单点就是单点，新 tick 进来后从左往右逐步增长，
 * 符合分时图"按频率逐步生成"的需求。
 */
function buildTickOption(
  data: MinuteKlineRow[],
  prevClose: number | undefined,
  avgPrices: number[],
  lineColor: string,
  areaColor: string,
  _yMode: YMode,
  ct: ChartTheme,
  _priceLimit?: PriceLimitInfo,
  _showLimitLines = true,
  showAvgLine = true,
  priceLines: Props['priceLines'] = [],
  showCallAuction = true,
  tradeMarks: IntradayTradeMark[] = [],
): EChartsOption {
  // closePairs/avgPairs/volPairs 的时间戳与 xAxis min/max（Date.UTC 构造的当日
  // 北京 09:15/09:30）对齐，详见 minuteDatetimeToMs
  const closePairs: [number, number][] = []
  const avgPairs: [number, number][] = []
  const volPairs: [number, number][] = []
  for (let i = 0; i < data.length; i++) {
    const ms = minuteDatetimeToMs(data[i].datetime)
    closePairs.push([ms, data[i].close])
    avgPairs.push([ms, avgPrices[i]])
    volPairs.push([ms, data[i].volume])
  }

  // Y 轴范围: 有昨收时按「日内最小涨跌幅-1% ~ 最大涨跌幅+1%」(含 0% 昨收基准线),
  // 左价格轴与右百分比轴共用同一刻度段, 网格线严格对齐; 无昨收回退到原自适应范围
  const pctAxis = (() => {
    if (!prevClose || prevClose <= 0 || !Number.isFinite(prevClose) || data.length === 0) return null
    const pcts: number[] = [0] // 0% = 昨收基准线, 保证 markLine 可见
    for (const row of data) {
      if (Number.isFinite(row.close)) pcts.push((row.close - prevClose) / prevClose * 100)
    }
    for (const a of avgPrices) {
      if (a != null && Number.isFinite(a)) pcts.push((a - prevClose) / prevClose * 100)
    }
    const lo = Math.min(...pcts) - 1
    const hi = Math.max(...pcts) + 1
    return {
      lo,
      hi,
      priceMin: prevClose * (1 + lo / 100),
      priceMax: prevClose * (1 + hi / 100),
    }
  })()

  let yMinAdj: number
  let yMaxAdj: number
  if (pctAxis) {
    yMinAdj = pctAxis.priceMin
    yMaxAdj = pctAxis.priceMax
  } else {
    const allValues: number[] = []
    if (prevClose != null) allValues.push(prevClose)
    for (const row of data) allValues.push(row.close)
    for (const a of avgPrices) if (a != null) allValues.push(a)
    const yMin = Math.min(...allValues)
    const yMax = Math.max(...allValues)
    const yPad = (yMax - yMin) * 0.1 || 1
    yMinAdj = yMin - yPad
    yMaxAdj = yMax + yPad
  }

  // 涨跌停 markLine（仅在 limit 模式下显示，与 buildOption 行为一致）
  const markLineData = buildPriceMarkLines(prevClose, priceLines, ct.crosshair, ct.text, ct.tooltipBg)

  const series: any[] = [
    {
      name: '价格',
      type: 'line',
      data: closePairs,
      smooth: false,
      symbol: 'none',
      cursor: 'crosshair',
      lineStyle: { width: 1.2, color: lineColor },
      areaStyle: intradayAreaStyle(areaColor),
      // tick 模式下不要 connectNulls，让 echarts 在 gap 处断开
      connectNulls: false,
      markLine: markLineData.length > 0 ? { symbol: 'none', data: markLineData, animation: false, silent: true } : undefined,
    },
  ]
  if (showAvgLine) {
    series.push({
      name: '均价',
      type: 'line',
      data: avgPairs,
      smooth: false,
      symbol: 'none',
      cursor: 'crosshair',
      lineStyle: { width: 1, color: THEME.avgLine },
      connectNulls: false,
    })
  }
  series.push({
    name: '成交量',
    type: 'bar',
    xAxisIndex: 1,
    yAxisIndex: 1,
    // 分钟模式同样给柱宽上限，防止数据稀疏时巨柱
    barMaxWidth: 6,
    barMinWidth: 1,
    data: volPairs.map(([t, v], i) => ({
      value: [t, v],
      itemStyle: { color: volItemStyle(data[i].close, data[i].open) },
    })),
  })
  // T 信号 B/S 标记 (time 轴直接用 ts_ms 定位)
  series.push(...tradeMarkSeries(tradeMarks, (ts) => ts))

  return {
    backgroundColor: 'transparent',
    tooltip: {
      trigger: 'axis',
      axisPointer: { type: 'cross' },
    },
    axisPointer: { link: [{ xAxisIndex: 'all' }] },
    grid: intradayGrids(),
    xAxis: [
      {
        type: 'time',
        // x 轴范围：showCallAuction 时从 9:15 开始（含集合竞价），否则从 9:30 开始
        min: (() => {
          const d = new Date()
          d.setHours(9, showCallAuction ? 15 : 30, 0, 0)
          return d.getTime()
        })(),
        max: (() => { const d = new Date(); d.setHours(15, 0, 0, 0); return d.getTime() })(),
        // 关键时间刻度：9:15 竞价 / 9:30 开盘 / 10:30 / 11:30 午休 / 13:00 午续 / 14:00 / 15:00 收盘
        // @ts-expect-error customValues 为 ECharts 5.5 运行时特性, 类型定义尚未收录
        customValues: (() => {
          const mk = (h: number, m: number) => {
            const d = new Date(); d.setHours(h, m, 0, 0); return d.getTime()
          }
          const ticks = [mk(9, 30), mk(10, 30), mk(11, 30), mk(13, 0), mk(14, 0), mk(15, 0)]
          return showCallAuction ? [mk(9, 15), ...ticks] : ticks
        })(),
        axisPointer: {
          show: true,
          lineStyle: dashedCrosshairStyle(ct.crosshair),
          label: crosshairLabelStyle(ct.tooltipBg, ct.tooltipBorder, ct.tooltipText),
        },
        axisLine: { show: false },
        axisLabel: {
          color: ct.text,
          fontSize: 10,
          fontFamily: 'JetBrains Mono, monospace',
          formatter: (params: any) => {
            // 防御 NaN/undefined：空数据或 hover 在数据外时 params.value 可能为 NaN
            // new Date(NaN) 是 Invalid Date，getHours() 返回 NaN，会渲染成 "NaN:NaN"
            const v = params?.value
            if (typeof v !== 'number' || !Number.isFinite(v)) return ''
            const d = new Date(v)
            const h = String(d.getHours()).padStart(2, '0')
            const m = String(d.getMinutes()).padStart(2, '0')
            if (h === 'NaN' || m === 'NaN') return ''
            return `${h}:${m}`
          },
        },
        axisTick: { show: false },
        splitLine: { show: true, lineStyle: { color: ct.grid } },
      },
      {
        type: 'time',
        gridIndex: 1,
        min: (() => {
          const d = new Date()
          d.setHours(9, showCallAuction ? 15 : 30, 0, 0)
          return d.getTime()
        })(),
        max: (() => { const d = new Date(); d.setHours(15, 0, 0, 0); return d.getTime() })(),
        axisLine: { show: false },
        axisLabel: { show: false },
        axisTick: { show: false },
        splitLine: { show: false },
      },
    ],
    yAxis: [
      {
        type: 'value',
        min: yMinAdj,
        max: yMaxAdj,
        // 有昨收时左右轴各分 5 段, 网格线严格对齐
        interval: pctAxis ? (yMaxAdj - yMinAdj) / 5 : undefined,
        splitArea: { show: false },
        axisLine: { show: false },
        axisTick: { show: false },
        splitLine: { lineStyle: { color: ct.grid } },
        axisPointer: {
          label: {
            formatter: (params: any) => {
              const v = params?.value
              return typeof v === 'number' && Number.isFinite(v) ? v.toFixed(2) : ''
            },
          },
        },
        axisLabel: { color: ct.text, fontSize: 10, fontFamily: 'JetBrains Mono, monospace' },
      },
      {
        type: 'value',
        gridIndex: 1,
        splitNumber: 2,
        splitArea: { show: false },
        axisLine: { show: false },
        axisTick: { show: false },
        splitLine: { lineStyle: { color: ct.grid } },
        axisLabel: { color: ct.text, fontSize: 10, fontFamily: 'JetBrains Mono, monospace' },
      },
      // 右侧百分比轴: 与左侧价格轴同范围, 刻度即涨跌幅。
      // 注意必须追加在数组末尾(yAxisIndex 2): 成交量 series 固定引用 yAxisIndex 1,
      // 插在中间会把成交量轴挤到 2 导致 xAxis/yAxis grid 不一致, setOption 抛错
      ...(pctAxis ? [{
        type: 'value' as const,
        position: 'right' as const,
        gridIndex: 0,
        min: pctAxis.lo,
        max: pctAxis.hi,
        interval: (pctAxis.hi - pctAxis.lo) / 5,
        splitArea: { show: false },
        axisLine: { show: false },
        axisTick: { show: false },
        splitLine: { show: false },
        axisPointer: {
          label: {
            formatter: (params: any) => {
              const v = params?.value
              return typeof v === 'number' && Number.isFinite(v) ? `${v >= 0 ? '+' : ''}${v.toFixed(2)}%` : ''
            },
          },
        },
        axisLabel: {
          color: ct.text,
          fontSize: 10,
          fontFamily: 'JetBrains Mono, monospace',
          formatter: (v: number) => `${v >= 0 ? '+' : ''}${v.toFixed(2)}%`,
        },
      }] : []),
    ],
    dataZoom: [
      { type: 'inside', xAxisIndex: [0, 1], start: 0, end: 100 },
      { type: 'inside', yAxisIndex: [0], start: 0, end: 100, zoomOnMouseWheel: false, moveOnMouseWheel: false },
    ],
    graphic: latestPriceGraphic(data[data.length - 1]?.close, prevClose),
    series,
  }
}

function buildOption(data: MinuteKlineRow[], prevClose: number | undefined, avgPrices: number[], lineColor: string, areaColor: string, yMode: YMode, ct: ChartTheme, priceLimit?: PriceLimitInfo, showLimitLines = true, showAvgLine = true, priceLines: Props['priceLines'] = [], times: string[] = FULL_DAY_TIMES, tickMode: boolean = false, currentPrice?: number, tradeMarks?: IntradayTradeMark[]): EChartsOption {
  if (tickMode) {
    // 从 times 推断 showCallAuction：times[0] === "09:15" 表示含集合竞价段
    const showCallAuction = times.length > 0 && times[0] === '09:15'
    return buildTickOption(data, prevClose, avgPrices, lineColor, areaColor, yMode, ct, priceLimit, showLimitLines, showAvgLine, priceLines, showCallAuction, tradeMarks)
  }
  // 将数据映射到全天时间轴上的正确位置
  const timeIndexMap = new Map(times.map((t, i) => [t, i]))
  const closes = new Array(times.length).fill(null) as (number | null)[]
  const highs = new Array(times.length).fill(null) as (number | null)[]
  const lows = new Array(times.length).fill(null) as (number | null)[]
  const avgData = new Array(times.length).fill(null) as (number | null)[]
  const volumes = new Array(times.length).fill(null) as (any | null)[]

  for (let i = 0; i < data.length; i++) {
    const timeKey = formatMinuteTime(data[i].datetime)
    const idx = timeIndexMap.get(timeKey)
    if (idx !== undefined) {
      closes[idx] = data[i].close
      highs[idx] = data[i].high
      lows[idx] = data[i].low
      avgData[idx] = avgPrices[i]
      volumes[idx] = {
        value: data[i].volume,
        itemStyle: { color: volItemStyle(data[i].close, data[i].open) },
      }
    }
  }

  const markLineData = buildPriceMarkLines(prevClose, priceLines, ct.crosshair, ct.text, ct.tooltipBg)

  let yMin: number | undefined
  let yMax: number | undefined
  let maxDiff = 0
  if (isValidPrice(prevClose) && data.length > 0) {
    const priceArrays = showAvgLine ? [closes, highs, lows, avgData] : [closes, highs, lows]
    for (const arr of priceArrays) {
      for (const v of arr) {
        if (!isValidPrice(v)) continue
        const diff = Math.abs(v - prevClose)
        if (diff > maxDiff) maxDiff = diff
      }
    }

    const monitoredDiff = priceLines.reduce((largest, line) => (
      Number.isFinite(line.value) && line.value > 0
        ? Math.max(largest, Math.abs(line.value - prevClose))
        : largest
    ), 0) * 1.05

    if (showLimitLines && yMode === 'limit') {
      const { limitUp, limitDown } = getLimitPrices(prevClose, priceLimit)
      const limitDiffUp = limitUp - prevClose
      const limitDiffDown = prevClose - limitDown
      const limitDiff = Math.max(limitDiffUp, limitDiffDown)
      // 涨跌停模式: Y 轴按实际涨跌停价
      maxDiff = Math.max(limitDiff, monitoredDiff)
      yMin = prevClose - maxDiff
      yMax = prevClose + maxDiff
      // 加 markLine 标注涨停价和跌停价 (仅虚线, 不显示文字)
      markLineData.push(
        {
          yAxis: limitUp,
          lineStyle: { color: 'rgba(199,64,64,0.4)', type: 'dashed', width: 1 },
          label: { show: false },
          symbol: 'none',
        },
        {
          yAxis: limitDown,
          lineStyle: { color: 'rgba(45,155,101,0.4)', type: 'dashed', width: 1 },
          label: { show: false },
          symbol: 'none',
        },
      )
    } else {
      // 自适应模式: Y 轴按实际涨跌幅对称, 但不超出实际涨跌停范围
      if (showLimitLines) {
        const { limitUp, limitDown } = getLimitPrices(prevClose, priceLimit)
        const limitDiff = Math.max(limitUp - prevClose, prevClose - limitDown)
        maxDiff = Math.min(maxDiff, limitDiff)
      }
      if (!showLimitLines && maxDiff > 0) {
        maxDiff *= 1.1
      }
      // 至少保证一个可视范围 (防止数据平时 maxDiff=0)。指数不使用涨跌停范围，最小范围要更紧，否则低波动指数会被压成横线。
      const minDiff = showLimitLines ? prevClose * 0.01 : prevClose * 0.001
      if (maxDiff < minDiff) maxDiff = minDiff
      maxDiff = Math.max(maxDiff, monitoredDiff)
      yMin = prevClose - maxDiff
      yMax = prevClose + maxDiff
    }
  }

  // x 轴标签：根据是否含竞价段计算索引
  // 默认（不含竞价）：9:30(idx 0), 10:30(60), 11:30/13:00(120), 14:00(181), 15:00(241)
  // 含竞价：9:15(idx 0), 9:30(15), 10:30(75), 11:30/13:00(135), 14:00(196), 15:00(256)
  const hasCall = times.length > FULL_DAY_TIMES.length
  const xAxisLabelMap: Record<number, string> = hasCall
    ? { 0: '9:15', 15: '9:30', 75: '10:30', 135: '11:30/13:00', 196: '14:00', 256: '15:00' }
    : { 0: '9:30', 60: '10:30', 120: '11:30/13:00', 181: '14:00', 241: '15:00' }
  const xAxisLabelFormatter = (_value: string, idx: number) => {
    return xAxisLabelMap[idx] ?? ''
  }

  return {
    animation: false,
    backgroundColor: 'transparent',
    tooltip: {
      trigger: 'axis',
      backgroundColor: 'transparent',
      borderWidth: 0,
      textStyle: { fontSize: 0 },
      formatter: () => '',
      axisPointer: {
        type: 'cross',
        label: {
          show: true,
          backgroundColor: ct.tooltipBg,
          borderColor: ct.tooltipBorder,
          borderWidth: 1,
          padding: [2, 5],
          color: ct.tooltipText,
          fontSize: 10,
          fontFamily: 'JetBrains Mono, monospace',
        },
        crossStyle: dashedCrosshairStyle(ct.crosshair),
        lineStyle: dashedCrosshairStyle(ct.crosshair),
      },
    },
    axisPointer: {
      link: [{ xAxisIndex: 'all' }],
    },
    grid: intradayGrids(),
    xAxis: [
      {
        type: 'category',
        data: times,
        boundaryGap: false,
        axisPointer: {
          show: true,
          lineStyle: dashedCrosshairStyle(ct.crosshair),
          label: {
            ...crosshairLabelStyle(ct.tooltipBg, ct.tooltipBorder, ct.tooltipText),
            formatter: (params: any) => params.value ?? '',
          },
        },
        axisLine: { show: false },
        axisLabel: {
          color: ct.text,
          fontSize: 10,
          fontFamily: 'JetBrains Mono, monospace',
          formatter: xAxisLabelFormatter,
          interval: 0,
        },
        axisTick: { show: false },
        splitLine: {
          show: true,
          lineStyle: { color: ct.grid },
        },
      },
      {
        type: 'category',
        gridIndex: 1,
        data: times,
        boundaryGap: false,
        axisLine: { show: false },
        axisLabel: { show: false },
        axisTick: { show: false },
        splitLine: { show: false },
      },
    ],
    yAxis: [
      {
        type: 'value',
        min: yMin,
        max: yMax,
        interval: maxDiff || undefined,
        splitArea: { show: false },
        axisLine: { show: false },
        axisTick: { show: false },
        splitLine: { lineStyle: { color: ct.grid } },
        axisPointer: {
          label: {
            formatter: (params: any) => {
              const v = params.value
              return typeof v === 'number' ? v.toFixed(2) : ''
            },
          },
        },
        axisLabel: {
          color: ct.text,
          fontSize: 10,
          fontFamily: 'JetBrains Mono, monospace',
          formatter: (v: number) => v.toFixed(2),
        },
      },
      {
        scale: true,
        gridIndex: 1,
        splitNumber: 2,
        axisLine: { show: false },
        axisTick: { show: false },
        splitLine: { show: false },
        axisLabel: { show: false },
      },
      ...(isValidPrice(prevClose) && yMin != null && yMax != null ? [{
        type: 'value' as const,
        position: 'right' as const,
        gridIndex: 0,
        min: yMin,
        max: yMax,
        interval: maxDiff || undefined,
        splitArea: { show: false },
        axisLine: { show: false },
        axisTick: { show: false },
        splitLine: { show: false },
        axisPointer: {
          label: {
            formatter: (params: any) => {
              const v = params.value
              if (typeof v !== 'number') return ''
              const pct = (v - prevClose) / prevClose * 100
              if (Math.abs(pct) < 0.01) return '0.00%'
              return (pct > 0 ? '+' : '') + pct.toFixed(2) + '%'
            },
          },
        },
        axisLabel: {
          color: ct.text,
          fontSize: 10,
          fontFamily: 'JetBrains Mono, monospace',
          formatter: (v: number) => {
            const pct = (v - prevClose) / prevClose * 100
            if (Math.abs(pct) < 0.01) return '0.00%'
            return (pct > 0 ? '+' : '') + pct.toFixed(2) + '%'
          },
        },
      }] : []),
    ],
    series: [
      {
        name: '价格',
        type: 'line',
        data: closes,
        smooth: false,
        symbol: 'none',
        cursor: 'crosshair',
        lineStyle: { width: 1.2, color: lineColor },
        // tick 模式：禁用 areaStyle，避免单点被 echarts 拉满到 x 轴末端
        // （category 轴 + areaStyle 会把 1 个点的面积填充到整条 x 轴宽度）
        areaStyle: tickMode ? undefined : intradayAreaStyle(areaColor),
        // 关闭 connectNulls：tick 视图下只画已发生分钟点，
        // 不用直线把 null 间隙连接起来（否则只有 1 个点时会从 9:30 拉直线到 14:57）
        connectNulls: false,
        markLine: markLineData.length > 0 ? { symbol: 'none', data: markLineData, animation: false, silent: true } : undefined,
      },
      ...(showAvgLine ? [{
        name: '均价',
        type: 'line' as const,
        data: avgData,
        smooth: false,
        symbol: 'none',
        cursor: 'crosshair',
        lineStyle: { width: 1, color: THEME.avgLine },
        connectNulls: false,
      }] : []),
      {
        name: '成交量',
        type: 'bar',
        data: volumes,
        xAxisIndex: 1,
        yAxisIndex: 1,
        cursor: 'crosshair',
        // time 轴上 ECharts 按相邻点最小间隔自动推算柱宽：
        // tick 稀疏时（如 1x mock 首分钟只有 1-2 个点）会算出占轴 2/3 的巨柱，
        // 必须给上限；同时保底下限避免密集时不可见
        barMaxWidth: 6,
        barMinWidth: 1,
      },
      // T 信号 B/S 标记: ts_ms → 'HH:MM' 映射到类目轴, 不在全日时间轴上的点丢弃
      ...tradeMarkSeries(
        (tradeMarks ?? []).filter((m) => timeIndexMap.has(tsToTimeLabel(m.ts_ms))),
        tsToTimeLabel,
      ),
    ],
    graphic: latestPriceGraphic(currentPrice ?? data[data.length - 1]?.close, prevClose),
  }
}

export function EChartsIntraday({
  data,
  height = 320,
  prevClose,
  date,
  priceLimit,
  onPriceHover,
  onPriceDoubleClick,
  currentPrice,
  priceLines,
  showLimitLines = true,
  showAvgLine = true,
  tickMode = false,
  tradeMarks,
}: Props) {
  const containerRef = useRef<HTMLDivElement>(null)
  const chartRef = useRef<ECharts | null>(null)
  const roRef = useRef<ResizeObserver | null>(null)
  const moRef = useRef<MutationObserver | null>(null)
  const priceDoubleClickHandlerRef = useRef<((event: { offsetX: number; offsetY: number }) => void) | null>(null)
  const dataRef = useRef(data)
  dataRef.current = data
  // tick 模式 ref：updateAxisPointer 回调读取（避免重建 chart）
  const tickModeRef = useRef(tickMode)
  tickModeRef.current = tickMode
  const currentPriceRef = useRef(currentPrice)
  currentPriceRef.current = currentPrice
  const onPriceHoverRef = useRef(onPriceHover)
  onPriceHoverRef.current = onPriceHover
  const onPriceDoubleClickRef = useRef(onPriceDoubleClick)
  onPriceDoubleClickRef.current = onPriceDoubleClick
  // 全日索引 → 数据数组索引 的映射 (ref 避免重建 chart)
  const fullDayToDataIdx = useRef<Map<number, number>>(new Map())

  const [infoIdx, setInfoIdx] = useState(data.length - 1)
  const [yMode, setYMode] = useState<YMode>('adaptive')
  // 是否显示集合竞价段（9:15-9:29），默认开启，与测试页 mock 起点 09:15 对齐
  const [showCallAuction, setShowCallAuction] = useState(true)
  const ct = useChartTheme()
  const avgPrices = useMemo(() => computeIntradayAverage(data), [data])
  // times = 全日时间轴（240 个分钟点）。tick 模式下也用全日时间轴，
  // 让 closes 数组中"未发生分钟点"保持 null，配合 connectNulls:false + areaStyle:undefined，
  // echarts 只画已发生的点，新数据从左往右逐步增长。
  const times = showCallAuction ? FULL_DAY_TIMES_WITH_CALL : FULL_DAY_TIMES

  // 分时线颜色：基于最新价 vs 昨收
  const lastClose = data.length > 0 ? data[data.length - 1].close : null
  const lineIsUp = lastClose != null && prevClose != null ? lastClose > prevClose : true
  const lineIsFlat = lastClose != null && prevClose != null ? lastClose === prevClose : false
  const lineColor = lineIsFlat ? '#A1A1AA' : lineIsUp ? '#C74040' : '#2D9B65'
  const areaFill = lineIsFlat ? 'rgba(180,180,190,0.40)' : lineIsUp ? 'rgba(199,64,64,0.40)' : 'rgba(34,197,94,0.40)'

  useEffect(() => {
    setInfoIdx(data.length - 1)
  }, [data.length])

  useEffect(() => {
    const el = containerRef.current
    if (!el) return

    let chart = chartRef.current
    if (!chart) {
      chart = echarts.init(el, undefined, { renderer: 'canvas' })
      chartRef.current = chart
      // 强制 canvas 使用十字光标，覆盖 ECharts 默认的 pointer
      const forceCursor = () => {
        const canvases = el.querySelectorAll('canvas')
        canvases.forEach(c => {
          // 值未变时跳过写操作，避免触发 MutationObserver 反馈循环
          if (c.style.cursor !== 'crosshair') c.style.setProperty('cursor', 'crosshair', 'important')
        })
      }
      forceCursor()
      // MutationObserver: ECharts 内部可能重建/修改 canvas 属性，持续强制 cursor
      const mo = new MutationObserver(forceCursor)
      mo.observe(el, { childList: true, subtree: true, attributes: true, attributeFilter: ['style', 'class'] })
      moRef.current = mo
      roRef.current = new ResizeObserver(() => {
        chart!.resize()
        forceCursor()
      })
      roRef.current.observe(el)

      chart.on('updateAxisPointer', (event: any) => {
        const axesInfo = event.axesInfo
        if (!axesInfo) return
        for (const info of Object.values(axesInfo)) {
          const val = (info as any)?.value
          if (val == null) continue
          const d = dataRef.current
          if (tickModeRef.current) {
            // time 轴（tick 模式）：info.value 是毫秒时间戳而非 category 索引，
            // 按时间戳找最近的数据行（数据 ≤ 全天分钟数，线性扫描即可）
            if (typeof val !== 'number' || !Number.isFinite(val)) continue
            let nearest = -1
            let nearestDiff = Infinity
            for (let i = 0; i < d.length; i++) {
              const diff = Math.abs(minuteDatetimeToMs(d[i].datetime) - val)
              if (diff < nearestDiff) {
                nearestDiff = diff
                nearest = i
              }
            }
            if (nearest >= 0) {
              setInfoIdx(nearest)
              onPriceHoverRef.current?.(d[nearest].close)
            }
            return
          }
          const fullDayIdx = typeof val === 'number' ? val : -1
          if (fullDayIdx >= 0) {
            const dataIdx = fullDayToDataIdx.current.get(fullDayIdx) ?? -1
            setInfoIdx(dataIdx)
            if (dataIdx >= 0 && dataIdx < d.length) {
              onPriceHoverRef.current?.(d[dataIdx].close)
            }
            return
          }
        }
      })

      chart.on('globalout', () => {
        onPriceHoverRef.current?.(null)
      })

      const handlePriceDoubleClick = (event: { offsetX: number; offsetY: number }) => {
        const pixel: [number, number] = [event.offsetX, event.offsetY]
        if (!chart!.containPixel({ gridIndex: 0 }, pixel)) return
        const coordinate = chart!.convertFromPixel({ xAxisIndex: 0, yAxisIndex: 0 }, pixel)
        const clickedPrice = Array.isArray(coordinate) ? Number(coordinate[1]) : NaN
        const latestPrice = currentPriceRef.current ?? dataRef.current[dataRef.current.length - 1]?.close
        if (Number.isFinite(clickedPrice) && clickedPrice > 0 && Number.isFinite(latestPrice) && latestPrice > 0) {
          onPriceDoubleClickRef.current?.(clickedPrice, latestPrice)
        }
      }
      priceDoubleClickHandlerRef.current = handlePriceDoubleClick
      chart.getZr().on('dblclick', handlePriceDoubleClick)
    }

    if (data.length > 0) {
      // 构建全日索引 → 数据索引 的映射（基于当前 times 数组）
      const timeIndexMap = new Map(times.map((t, i) => [t, i]))
      const mapping = new Map<number, number>()
      for (let i = 0; i < data.length; i++) {
        const timeKey = formatMinuteTime(data[i].datetime)
        const fullDayIdx = timeIndexMap.get(timeKey)
        if (fullDayIdx !== undefined) {
          mapping.set(fullDayIdx, i)
        }
      }
      fullDayToDataIdx.current = mapping

      chart.setOption(buildOption(data, prevClose, avgPrices, lineColor, areaFill, yMode, ct, priceLimit, showLimitLines, showAvgLine, priceLines, times, tickMode, currentPrice, tradeMarks), true)
    } else {
      chart.clear()
    }
  }, [data, prevClose, lineColor, areaFill, yMode, ct, priceLimit, showLimitLines, showAvgLine, priceLines, times, tickMode, currentPrice, tradeMarks])

  useEffect(() => {
    return () => {
      chartRef.current?.off('updateAxisPointer')
      chartRef.current?.off('globalout')
      if (priceDoubleClickHandlerRef.current) {
        chartRef.current?.getZr().off('dblclick', priceDoubleClickHandlerRef.current)
      }
      moRef.current?.disconnect()
      roRef.current?.disconnect()
      chartRef.current?.dispose()
      chartRef.current = null
      moRef.current = null
      roRef.current = null
    }
  }, [])

  const d = infoIdx >= 0 && infoIdx < data.length ? data[infoIdx] : null
  const avg = d != null ? avgPrices[infoIdx] : null
  const chg = d && prevClose != null ? d.close - prevClose : null
  const isUp = chg != null ? chg > 0 : true
  const isFlat = chg != null ? chg === 0 : false
  const priceClr = isFlat ? '#A1A1AA' : isUp ? '#C74040' : '#2D9B65'

  return (
    <div className="w-full">
      {/* 按钮行: 左侧「显示竞价」切换，右侧 Y 轴模式切换 */}
      {showLimitLines && <div className="flex items-center justify-between px-1 pb-0.5">
        <button
          onClick={() => setShowCallAuction(v => !v)}
          className={`px-2.5 py-0.5 text-[10px] font-mono cursor-pointer transition-colors rounded border ${
            showCallAuction
              ? 'bg-accent/20 text-accent border-accent/40'
              : 'text-muted hover:text-secondary bg-elevated border-border/40'
          }`}
          title="集合竞价段 9:15-9:29，默认隐藏"
        >
          {showCallAuction ? '竞价 ✓' : '竞价'}
        </button>
        <div className="inline-flex items-center rounded bg-elevated overflow-hidden">
          <button
            onClick={() => setYMode('adaptive')}
            className={`px-2.5 py-0.5 text-[10px] font-mono cursor-pointer transition-colors ${
              yMode === 'adaptive'
                ? 'bg-accent/20 text-accent'
                : 'text-muted hover:text-secondary'
            }`}
          >
            自适应
          </button>
          <div className="w-px h-3 bg-border/40" />
          <button
            onClick={() => setYMode('limit')}
            className={`px-2.5 py-0.5 text-[10px] font-mono cursor-pointer transition-colors ${
              yMode === 'limit'
                ? 'bg-accent/20 text-accent'
                : 'text-muted hover:text-secondary'
            }`}
          >
            涨跌停
          </button>
        </div>
      </div>}
      <div style={{ backgroundColor: ct.infoBarBg }}>
        {/* 第一行: 日期 + OHLC */}
        <div className="flex items-center gap-x-2 px-2 font-mono text-[11px] select-none flex-wrap" style={{ height: 20 }}>
          {!d && <span className="text-muted">—</span>}
          {d && (
            <>
              {date && <span className="text-muted">{date}</span>}
              <span className="text-muted">开</span>
              <span style={{ color: priceClr }}>{d.open.toFixed(2)}</span>
              <span className="text-muted">高</span>
              <span style={{ color: priceClr }}>{d.high.toFixed(2)}</span>
              <span className="text-muted">低</span>
              <span style={{ color: priceClr }}>{d.low.toFixed(2)}</span>
              <span className="text-muted">收</span>
              <span style={{ color: priceClr }} className="font-semibold">{d.close.toFixed(2)}</span>
            </>
          )}
        </div>
        {/* 第二行: 价格+均价+量+额 */}
        <div className="flex items-center gap-x-4 px-2 font-mono text-[11px] select-none" style={{ height: 20 }}>
          {d && (
            <>
              <span className="flex items-center gap-x-1">
                <span style={{ display: 'inline-block', width: 14, height: 2, background: priceClr }} />
                <span style={{ color: priceClr }}>{d.close.toFixed(2)}</span>
              </span>
              {showAvgLine && <span className="flex items-center gap-x-1">
                <span style={{ display: 'inline-block', width: 14, height: 2, background: THEME.avgLine }} />
                <span style={{ color: THEME.avgLine }}>{avg?.toFixed(2)}</span>
              </span>}
              <span className="text-muted">量</span>
              <span className="text-secondary">{d.volume.toFixed(0)}</span>
              <span className="text-muted">额</span>
              <span className="text-secondary">{fmtAmt(d.amount)}</span>
            </>
          )}
        </div>
      </div>
      <div ref={containerRef} className="w-full" style={{ height: height - 42, cursor: 'crosshair' }} />
    </div>
  )
}
