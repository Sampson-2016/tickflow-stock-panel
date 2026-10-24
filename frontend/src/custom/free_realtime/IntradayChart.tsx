// 免费实时行情分时图组件
// 默认显示 2 个指标（RSI / 成交量）；用户可在面板上 toggle。
import { useQuery } from '@tanstack/react-query'
import * as echarts from 'echarts'
import { useEffect, useMemo, useRef, useState } from 'react'
import { BTN_SM_PRIMARY, BTN_SM_SECONDARY, freeRealtimeApi } from './api'

interface Row {
  ts_ms: number
  last_price: number
  volume?: number
  high?: number
  low?: number
  close?: number
}

interface Props {
  symbol: string
  kind?: 'real' | 'mock'
  days?: number
  height?: number
}

const INDICATORS = ['price', 'volume', 'rsi'] as const
type Indicator = (typeof INDICATORS)[number]
const INDICATOR_LABELS: Record<Indicator, string> = {
  price: '价格',
  volume: '成交量',
  rsi: 'RSI(14)',
}

export function IntradayChart({
  symbol,
  kind = 'real',
  days = 1,
  height = 420,
}: Props) {
  const [enabled, setEnabled] = useState<Indicator[]>(['price', 'volume'])
  // RSI 计算窗口
  const RSI_PERIOD = 14

  const q = useQuery({
    queryKey: ['free-realtime-intraday', symbol, kind, days],
    queryFn: () => freeRealtimeApi.intraday(symbol, kind, days),
    refetchInterval: 3000,
  })

  const rows: Row[] = useMemo(() => {
    if (!q.data || !q.data.rows) return []
    return q.data.rows
      .map((r) => ({
        ts_ms: Number(r.ts_ms),
        last_price: Number(r.last_price),
        volume: r.volume != null ? Number(r.volume) : undefined,
        high: r.high != null ? Number(r.high) : undefined,
        low: r.low != null ? Number(r.low) : undefined,
        close: r.last_price != null ? Number(r.last_price) : undefined,
      }))
      .filter((r) => r.ts_ms > 0)
      .sort((a, b) => a.ts_ms - b.ts_ms)
  }, [q.data])

  // 计算 RSI(14)
  const rsiValues = useMemo(() => {
    const closes = rows.map((r) => r.last_price)
    if (closes.length < RSI_PERIOD + 1) return []
    const out: { ts: number; value: number }[] = []
    let avgGain = 0
    let avgLoss = 0
    // 初始化：前 RSI_PERIOD 个 diff 的均值
    for (let i = 1; i <= RSI_PERIOD; i++) {
      const diff = closes[i] - closes[i - 1]
      if (diff >= 0) avgGain += diff
      else avgLoss -= diff
    }
    avgGain /= RSI_PERIOD
    avgLoss /= RSI_PERIOD
    out.push({
      ts: rows[RSI_PERIOD].ts_ms,
      value: 100 - 100 / (1 + (avgLoss === 0 ? Infinity : avgGain / avgLoss)),
    })
    for (let i = RSI_PERIOD + 1; i < closes.length; i++) {
      const diff = closes[i] - closes[i - 1]
      const gain = diff > 0 ? diff : 0
      const loss = diff < 0 ? -diff : 0
      avgGain = (avgGain * (RSI_PERIOD - 1) + gain) / RSI_PERIOD
      avgLoss = (avgLoss * (RSI_PERIOD - 1) + loss) / RSI_PERIOD
      const rs = avgLoss === 0 ? Infinity : avgGain / avgLoss
      out.push({ ts: rows[i].ts_ms, value: 100 - 100 / (1 + rs) })
    }
    return out
  }, [rows])

  const containerRef = useRef<HTMLDivElement>(null)
  const chartRef = useRef<echarts.ECharts | null>(null)

  useEffect(() => {
    if (!containerRef.current) return
    if (!chartRef.current) {
      chartRef.current = echarts.init(containerRef.current)
    }
    return () => {
      chartRef.current?.dispose()
      chartRef.current = null
    }
  }, [])

  useEffect(() => {
    const chart = chartRef.current
    if (!chart) return
    if (!rows.length) {
      chart.clear()
      return
    }
    const ts = rows.map((r) => new Date(r.ts_ms).toLocaleTimeString('zh-CN', { hour12: false }))
    const series: any[] = []
    const yAxis: any[] = []
    let yIdx = 0
    if (enabled.includes('price')) {
      series.push({
        type: 'line',
        name: '价格',
        data: rows.map((r) => r.last_price),
        smooth: false,
        symbol: 'none',
        yAxisIndex: yIdx,
        lineStyle: { color: '#3B82F6' },
        areaStyle: { color: 'rgba(59,130,246,0.2)' },
      })
      yAxis.push({ type: 'value', scale: true, name: '价格' })
      yIdx++
    }
    if (enabled.includes('volume')) {
      series.push({
        type: 'bar',
        name: '成交量',
        data: rows.map((r) => r.volume ?? 0),
        yAxisIndex: yIdx,
        itemStyle: { color: 'rgba(180,180,190,0.5)' },
      })
      yAxis.push({ type: 'value', scale: true, name: '成交量' })
      yIdx++
    }
    if (enabled.includes('rsi') && rsiValues.length) {
      series.push({
        type: 'line',
        name: 'RSI',
        data: rsiValues.map((r) => r.value),
        smooth: true,
        symbol: 'none',
        yAxisIndex: yIdx,
        lineStyle: { color: '#F59E0B' },
      })
      yAxis.push({
        type: 'value',
        min: 0,
        max: 100,
        name: 'RSI',
      })
      yIdx++
    }
    chart.setOption({
      tooltip: { trigger: 'axis', axisPointer: { type: 'cross' } },
      legend: { data: series.map((s: any) => s.name) },
      grid: { left: 50, right: 50, top: 30, bottom: 50 },
      xAxis: { type: 'category', data: ts },
      yAxis,
      series,
    })
  }, [rows, rsiValues, enabled])

  function toggle(ind: Indicator) {
    setEnabled((cur) =>
      cur.includes(ind) ? cur.filter((x) => x !== ind) : [...cur, ind],
    )
  }

  return (
    <div>
      <div className="mb-2 flex items-center justify-between">
        <div className="text-sm text-muted-foreground">
          {symbol} · {kind} · {rows.length} 个点
          {q.isFetching ? ' · 拉取中…' : ''}
        </div>
        <div className="flex gap-2">
          {INDICATORS.map((ind) => (
            <button
              key={ind}
              type="button"
              className={enabled.includes(ind) ? BTN_SM_PRIMARY : BTN_SM_SECONDARY}
              onClick={() => toggle(ind)}
            >
              {INDICATOR_LABELS[ind]}
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
