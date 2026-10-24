// 免费实时行情分时图组件
// 时间轴映射到交易时段 9:30-11:30 / 13:00-15:00（跳过午休）
// 图表类型：分时图(默认) / 1分钟图 / 5分钟图
// 双 Y 轴：左价格，右涨跌幅%，prev_close 与 0% 对齐在中间
import { useQuery } from '@tanstack/react-query'
import * as echarts from 'echarts'
import { useEffect, useMemo, useRef, useState } from 'react'
import { BTN_SM_PRIMARY, BTN_SM_SECONDARY, freeRealtimeApi } from './api'

interface RawRow {
  ts_ms: number
  last_price: number
  prev_close: number
  volume: number
}

interface Props {
  symbol: string
  kind?: 'real' | 'mock'
  days?: number
  height?: number
}

// 交易时段常量（秒数，从 0:00 起）
const SEC = {
  MORNING_START: 9 * 3600 + 30 * 60, // 34200
  MORNING_END: 11 * 3600 + 30 * 60, // 41400
  AFTERNOON_START: 13 * 3600, // 46800
  AFTERNOON_END: 15 * 3600, // 54000
}
const MORNING_DUR = SEC.MORNING_END - SEC.MORNING_START // 7200
const AFTERNOON_DUR = SEC.AFTERNOON_END - SEC.AFTERNOON_START // 7200
const TOTAL_DUR = MORNING_DUR + AFTERNOON_DUR // 14400

// 图表类型
const CHART_TYPES = ['intraday', 'm1', 'm5'] as const
type ChartType = (typeof CHART_TYPES)[number]
const CHART_TYPE_LABELS: Record<ChartType, string> = {
  intraday: '分时',
  m1: '1分钟',
  m5: '5分钟',
}

// 副图指标
const SUB_INDICATORS = ['volume', 'rsi'] as const
type SubIndicator = (typeof SUB_INDICATORS)[number]
const SUB_LABELS: Record<SubIndicator, string> = {
  volume: '成交量',
  rsi: 'RSI(14)',
}

/** 把 tick 均匀映射到交易时段，返回盘中秒数 + Date */
function mapTick(i: number, total: number): { secOfDay: number; label: string } {
  if (total <= 1) {
    return {
      secOfDay: SEC.MORNING_START,
      label: '09:30',
    }
  }
  const ratio = i / (total - 1)
  const elapsed = ratio * TOTAL_DUR
  let sec: number
  if (elapsed < MORNING_DUR) {
    sec = SEC.MORNING_START + elapsed
  } else {
    sec = SEC.AFTERNOON_START + (elapsed - MORNING_DUR)
  }
  return { secOfDay: sec, label: formatSec(sec) }
}

function formatSec(sec: number): string {
  const h = Math.floor(sec / 3600)
  const m = Math.floor((sec % 3600) / 60)
  const s = Math.floor(sec % 60)
  return `${String(h).padStart(2, '0')}:${String(m).padStart(2, '0')}:${String(s).padStart(2, '0')}`
}

// 检测是否跨越午休边界（上午 → 下午）
function crossedNoon(secA: number, secB: number): boolean {
  return secA < SEC.MORNING_END && secB > SEC.AFTERNOON_START
}

interface MappedRow {
  secOfDay: number
  label: string
  price: number
  volume: number
}

interface Candle {
  label: string
  open: number
  close: number
  high: number
  low: number
  volume: number
}

/** 按分钟桶聚合为蜡烛 */
function aggregate(rows: MappedRow[], periodMin: number): Candle[] {
  if (!rows.length) return []
  const buckets = new Map<number, MappedRow[]>()
  for (const r of rows) {
    const minOfDay = Math.floor(r.secOfDay / 60)
    // 下午时段减去午休偏移，让桶连续
    const adjustedMin =
      r.secOfDay >= SEC.AFTERNOON_START
        ? minOfDay - (SEC.AFTERNOON_START - SEC.MORNING_END) / 60
        : minOfDay
    const bucket = Math.floor(adjustedMin / periodMin) * periodMin
    if (!buckets.has(bucket)) buckets.set(bucket, [])
    buckets.get(bucket)!.push(r)
  }
  const sortedKeys = [...buckets.keys()].sort((a, b) => a - b)
  const candles: Candle[] = []
  for (const k of sortedKeys) {
    const rs = buckets.get(k)!
    const prices = rs.map((r) => r.price)
    candles.push({
      label: rs[0].label,
      open: prices[0],
      close: prices[prices.length - 1],
      high: Math.max(...prices),
      low: Math.min(...prices),
      volume: rs.reduce((s, r) => s + r.volume, 0),
    })
  }
  return candles
}

function calcRSI(rows: MappedRow[], period = 14): (number | null)[] {
  const closes = rows.map((r) => r.price)
  const out: (number | null)[] = new Array(closes.length).fill(null)
  if (closes.length < period + 1) return out
  let avgGain = 0
  let avgLoss = 0
  for (let i = 1; i <= period; i++) {
    const diff = closes[i] - closes[i - 1]
    if (diff >= 0) avgGain += diff
    else avgLoss -= diff
  }
  avgGain /= period
  avgLoss /= period
  out[period] =
    100 - 100 / (1 + (avgLoss === 0 ? Infinity : avgGain / avgLoss))
  for (let i = period + 1; i < closes.length; i++) {
    const diff = closes[i] - closes[i - 1]
    const gain = diff > 0 ? diff : 0
    const loss = diff < 0 ? -diff : 0
    avgGain = (avgGain * (period - 1) + gain) / period
    avgLoss = (avgLoss * (period - 1) + loss) / period
    const rs = avgLoss === 0 ? Infinity : avgGain / avgLoss
    out[i] = 100 - 100 / (1 + rs)
  }
  return out
}

export function IntradayChart({
  symbol,
  kind = 'real',
  days = 1,
  height = 480,
}: Props) {
  const [chartType, setChartType] = useState<ChartType>('intraday')
  const [subs, setSubs] = useState<SubIndicator[]>([])

  const q = useQuery({
    queryKey: ['free-realtime-intraday', symbol, kind, days],
    queryFn: () => freeRealtimeApi.intraday(symbol, kind, days),
    refetchInterval: 3000,
  })

  // 原始数据
  const { mapped, prevClose } = useMemo(() => {
    if (!q.data || !q.data.rows || !q.data.rows.length)
      return { mapped: [] as MappedRow[], prevClose: 0 }
    const raw = q.data.rows.map(
      (r) =>
        ({
          ts_ms: Number(r.ts_ms),
          last_price: Number(r.last_price),
          prev_close: Number(r.prev_close),
          volume: r.volume != null ? Number(r.volume) : 0,
        }) as RawRow,
    )
    // 排序
    raw.sort((a, b) => a.ts_ms - b.ts_ms)
    const total = raw.length
    const pc = raw[0].prev_close || raw[0].last_price
    const rows: MappedRow[] = raw.map((r, i) => {
      const { secOfDay, label } = mapTick(i, total)
      return {
        secOfDay,
        label,
        price: r.last_price,
        volume: r.volume,
      }
    })
    return { mapped: rows, prevClose: pc }
  }, [q.data])

  // 聚合蜡烛（1分钟/5分钟）
  const candles = useMemo(() => {
    if (chartType === 'intraday') return []
    const period = chartType === 'm1' ? 1 : 5
    return aggregate(mapped, period)
  }, [mapped, chartType])

  // RSI（基于 mapped 价格序列）
  const rsiArr = useMemo(() => calcRSI(mapped), [mapped])

  // 构建主图 X 轴 + series 数据
  const chartData = useMemo(() => {
    if (chartType === 'intraday') {
      // 分时折线，插入午休断点
      const xLabels: string[] = []
      const priceData: (number | null)[] = []
      for (let i = 0; i < mapped.length; i++) {
        const cur = mapped[i]
        if (i > 0) {
          const prev = mapped[i - 1]
          if (crossedNoon(prev.secOfDay, cur.secOfDay)) {
            // 插入午休断点
            xLabels.push('11:30/13:00')
            priceData.push(null)
          }
        }
        xLabels.push(cur.label)
        priceData.push(cur.price)
      }
      return { xLabels, priceData, candles: [] as Candle[] }
    }
    // 蜡烛图
    const xLabels = candles.map((c) => c.label)
    return { xLabels, priceData: [], candles }
  }, [mapped, candles, chartType])

  const subData = useMemo(() => {
    const volData: (number | null)[] = []
    const rsiData: (number | null)[] = []
    if (chartType === 'intraday') {
      for (let i = 0; i < mapped.length; i++) {
        const cur = mapped[i]
        if (i > 0) {
          const prev = mapped[i - 1]
          if (crossedNoon(prev.secOfDay, cur.secOfDay)) {
            volData.push(null)
            rsiData.push(null)
          }
        }
        volData.push(cur.volume)
        rsiData.push(rsiArr[i])
      }
    } else {
      // 蜡烛副图用聚合后 volume
      for (const c of candles) {
        volData.push(c.volume)
        rsiData.push(null)
      }
    }
    return { volData, rsiData }
  }, [mapped, candles, rsiArr, chartType])

  const containerRef = useRef<HTMLDivElement>(null)
  const chartRef = useRef<echarts.ECharts | null>(null)

  useEffect(() => {
    if (!containerRef.current) return
    chartRef.current = echarts.init(containerRef.current)
    return () => {
      chartRef.current?.dispose()
      chartRef.current = null
    }
  }, [])

  useEffect(() => {
    const chart = chartRef.current
    if (!chart) return

    if (!mapped.length) {
      chart.clear()
      return
    }

    // ── Y 轴对称：prev_close 居中，0% 居中 ──
    const allPrices =
      chartType === 'intraday'
        ? mapped.map((r) => r.price)
        : candles.flatMap((c) => [c.high, c.low])
    const maxDelta = Math.max(
      ...allPrices.map((p) => Math.abs(p - prevClose)),
      prevClose * 0.001,
    )
    const yMin = prevClose - maxDelta * 1.1
    const yMax = prevClose + maxDelta * 1.1
    const pctMax = (maxDelta / prevClose) * 100 * 1.1
    const pctMin = -pctMax

    // 是否有副图
    const hasSub = subs.length > 0
    const subHeight = hasSub ? 90 : 0

    // ── Series ──
    const series: any[] = []
    const yAxis: any[] = []

    // 左 Y 轴：价格
    yAxis.push({
      type: 'value',
      name: '价格',
      min: yMin,
      max: yMax,
      scale: true,
      position: 'left',
      axisLine: { show: true },
      axisLabel: { formatter: (v: number) => v.toFixed(2) },
      splitLine: { show: true, lineStyle: { type: 'dashed', opacity: 0.3 } },
    })
    // 右 Y 轴：涨跌幅%
    yAxis.push({
      type: 'value',
      name: '涨跌幅%',
      min: pctMin,
      max: pctMax,
      position: 'right',
      axisLabel: { formatter: (v: number) => v.toFixed(2) + '%' },
      splitLine: { show: false },
    })

    if (chartType === 'intraday') {
      // 分时折线
      series.push({
        type: 'line',
        name: '价格',
        data: chartData.priceData,
        xAxisIndex: 0,
        yAxisIndex: 0,
        symbol: 'none',
        smooth: false,
        lineStyle: { color: '#3B82F6', width: 1.5 },
        areaStyle: {
          color: new echarts.graphic.LinearGradient(0, 0, 0, 1, [
            { offset: 0, color: 'rgba(59,130,246,0.25)' },
            { offset: 1, color: 'rgba(59,130,246,0.02)' },
          ]),
        },
        connectNulls: false,
      })
      // prev_close 基准线（markLine 在 0% 处）
      series.push({
        type: 'line',
        name: '昨收',
        data: chartData.priceData.map(() => prevClose),
        xAxisIndex: 0,
        yAxisIndex: 0,
        symbol: 'none',
        lineStyle: { color: '#888', type: 'dashed', width: 1 },
        silent: true,
      })
    } else {
      // 蜡烛图
      const ohlc = candles.map((c) => ({
        name: c.label,
        value: [c.open, c.close, c.low, c.high],
      }))
      series.push({
        type: 'candlestick',
        name: 'K线',
        data: ohlc,
        xAxisIndex: 0,
        yAxisIndex: 0,
        itemStyle: {
          color: '#ef4444', // 涨红
          color0: '#22c55e', // 跌绿
          borderColor: '#ef4444',
          borderColor0: '#22c55e',
        },
      })
    }

    // 副图 series（单独 X 轴）
    if (hasSub) {
      const subXIdx = 1
      // 副图 Y 轴
      yAxis.push({
        type: 'value',
        scale: true,
        name: '',
        gridIndex: 1,
        axisLabel: { show: true },
        splitLine: { show: true, lineStyle: { type: 'dashed', opacity: 0.2 } },
      })
      if (subs.includes('volume')) {
        series.push({
          type: 'bar',
          name: '成交量',
          data: subData.volData,
          xAxisIndex: subXIdx,
          yAxisIndex: 2,
          itemStyle: { color: 'rgba(150,150,160,0.5)' },
        })
      }
      if (subs.includes('rsi')) {
        series.push({
          type: 'line',
          name: 'RSI(14)',
          data: subData.rsiData,
          xAxisIndex: subXIdx,
          yAxisIndex: 2,
          symbol: 'none',
          smooth: true,
          lineStyle: { color: '#F59E0B', width: 1.5 },
          connectNulls: false,
          markLine: {
            silent: true,
            symbol: 'none',
            data: [
              { yAxis: 30, lineStyle: { color: '#888', type: 'dashed' } },
              { yAxis: 70, lineStyle: { color: '#888', type: 'dashed' } },
            ],
          },
        })
      }
    }

    const grids = hasSub
      ? [
          { left: 55, right: 60, top: 20, height: height - subHeight - 60 },
          { left: 55, right: 60, top: height - subHeight - 25, height: subHeight },
        ]
      : [{ left: 55, right: 60, top: 20, bottom: 30 }]

    const xAxes: any[] = [
      {
        type: 'category',
        data: chartData.xLabels,
        boundaryGap: chartType !== 'intraday',
        axisLabel: {
          formatter: (val: string) => {
            // 只显示整点或半点，避免拥挤
            if (val.includes(':30:00') || val.includes(':00:00')) {
              return val.slice(0, 5)
            }
            return ''
          },
        },
      },
    ]
    if (hasSub) {
      xAxes.push({
        type: 'category',
        data: chartData.xLabels,
        gridIndex: 1,
        boundaryGap: chartType !== 'intraday',
        axisLabel: { show: false },
      })
    }

    chart.setOption(
      {
        tooltip: {
          trigger: 'axis',
          axisPointer: { type: 'cross' },
        },
        legend: {
          data: series.map((s: any) => s.name),
          top: 0,
        },
        grid: grids,
        xAxis: xAxes,
        yAxis,
        series,
      },
      { notMerge: true },
    )
  }, [mapped, candles, chartData, subData, subs, chartType, prevClose, height])

  function toggleSub(ind: SubIndicator) {
    setSubs((cur) =>
      cur.includes(ind) ? cur.filter((x) => x !== ind) : [...cur, ind],
    )
  }

  return (
    <div>
      <div className="mb-2 flex flex-wrap items-center justify-between gap-2">
        <div className="text-sm text-muted-foreground">
          {symbol} · {kind} · {mapped.length} 个点
          {q.isFetching ? ' · 拉取中…' : ''}
          {prevClose > 0 && (
            <span className="ml-2">昨收 {prevClose.toFixed(2)}</span>
          )}
        </div>
        <div className="flex flex-wrap gap-2">
          {CHART_TYPES.map((t) => (
            <button
              key={t}
              type="button"
              className={chartType === t ? BTN_SM_PRIMARY : BTN_SM_SECONDARY}
              onClick={() => setChartType(t)}
            >
              {CHART_TYPE_LABELS[t]}
            </button>
          ))}
          <span className="mx-1 text-muted-foreground">|</span>
          {SUB_INDICATORS.map((ind) => (
            <button
              key={ind}
              type="button"
              className={subs.includes(ind) ? BTN_SM_PRIMARY : BTN_SM_SECONDARY}
              onClick={() => toggleSub(ind)}
            >
              {SUB_LABELS[ind]}
            </button>
          ))}
        </div>
      </div>
      <div
        ref={containerRef}
        style={{ width: '100%', height: `${height}px` }}
      />
    </div>
  )
}
