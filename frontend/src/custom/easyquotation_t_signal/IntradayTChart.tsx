import { useEffect, useMemo, useRef } from 'react'
import * as echarts from 'echarts/core'
import { LineChart, ScatterChart, BarChart } from 'echarts/charts'
import {
  GridComponent,
  TooltipComponent,
  AxisPointerComponent,
  MarkAreaComponent,
  MarkLineComponent,
  TitleComponent,
} from 'echarts/components'
import { CanvasRenderer } from 'echarts/renderers'
import type { TSignalItem } from './api'
import type { IntradayBar } from './api'
import { T_SIGNAL_KIND_LABELS } from './api'

echarts.use([
  LineChart, ScatterChart, BarChart,
  GridComponent, TooltipComponent, AxisPointerComponent,
  MarkAreaComponent, MarkLineComponent, TitleComponent,
  CanvasRenderer,
])

interface Props {
  bars: IntradayBar[]                  // 当日分时点 (ts_ms, price, volume)
  signals: TSignalItem[]               // 当日信号 (ts_ms, price, kind)
  prevClose?: number                   // 昨收 (画 0 轴基准线)
  height?: number
  dataStatus?: 'ok' | 'failed' | 'never'  // 拉取状态: 决定空态文案
}

// 信号 → marker 样式 (颜色按 kind; 形状: 涨三角/跌菱形/菱形/圆)
function markerFor(s: TSignalItem) {
  let symbol: 'triangle' | 'diamond' | 'circle' = 'circle'
  let color = '#3b82f6'
  if (s.kind === 'price_change') {
    symbol = 'triangle'
    color = s.change_pct > 0 ? '#ef4444' : '#22c55e'
  } else if (s.kind === 'volume_surge') {
    symbol = 'diamond'
    color = '#f59e0b'
  }
  return {
    symbol,
    symbolSize: 11,
    symbolOffset: [0, -2],
    itemStyle: {
      color,
      borderColor: '#0f172a',
      borderWidth: 1,
    },
    label: {
      show: true,
      formatter: () => {
        const pct = s.change_pct
        const sign = pct > 0 ? '+' : ''
        return `${sign}${pct.toFixed(2)}%`
      },
      position: 'top',
      fontSize: 10,
      color: '#fafafa',
      backgroundColor: 'rgba(15, 23, 42, 0.85)',
      padding: [2, 4],
      borderRadius: 3,
    },
  }
}

export function IntradayTChart({ bars, signals, prevClose, height = 360, dataStatus = 'never' }: Props) {
  const containerRef = useRef<HTMLDivElement | null>(null)
  const chartRef = useRef<echarts.ECharts | null>(null)

  const option = useMemo(() => {
    // x 轴用 ts_ms (数值), 但格式化显示 HH:MM
    const lineData = bars.map(b => [b.ts_ms, b.price])
    const volData = bars.map(b => [b.ts_ms, b.volume ?? 0])

    // signals: ts → marker (同一 ts 多个信号合并)
    const markersByTs: Record<number, TSignalItem[]> = {}
    for (const s of signals) {
      ;(markersByTs[s.ts_ms] ||= []).push(s)
    }
    const markerPoints = Object.entries(markersByTs).map(([ts, items]) => {
      const first = items[0]
      const allKinds = items.map(i => T_SIGNAL_KIND_LABELS[i.kind]).join(' / ')
      const lineParts = items.map(i => `• ${T_SIGNAL_KIND_LABELS[i.kind]}: ${i.detail}`).join('\n')
      return {
        name: allKinds,
        value: [Number(ts), first.price],
        ...markerFor(first),
        tooltip: {
          formatter: () =>
            `<b>${first.name} (${first.symbol})</b><br/>` +
            `${new Date(first.ts_ms).toLocaleTimeString('zh-CN', { hour: '2-digit', minute: '2-digit', second: '2-digit', hour12: false })}<br/>` +
            `价格: ${first.price.toFixed(2)} (${first.change_pct > 0 ? '+' : ''}${first.change_pct.toFixed(2)}%)<br/>` +
            `<br/>${lineParts}`,
        },
      }
    })

    const xMin = bars.length > 0 ? bars[0].ts_ms : undefined
    const xMax = bars.length > 0 ? bars[bars.length - 1].ts_ms : undefined

    return {
      animation: false,
      backgroundColor: 'transparent',
      tooltip: {
        trigger: 'axis',
        axisPointer: { type: 'cross', label: { backgroundColor: '#1e293b' } },
        backgroundColor: 'rgba(15, 23, 42, 0.95)',
        borderColor: '#334155',
        textStyle: { color: '#fafafa', fontSize: 12 },
      },
      axisPointer: { link: [{ xAxisIndex: 'all' }] },
      grid: [
        { left: 50, right: 20, top: 20, height: '65%' },
        { left: 50, right: 20, top: '75%', height: '15%' },
      ],
      xAxis: [
        {
          type: 'time',
          min: xMin,
          max: xMax,
          axisLabel: {
            formatter: (val: number) =>
              new Date(val).toLocaleTimeString('zh-CN', {
                hour: '2-digit',
                minute: '2-digit',
                hour12: false,
              }),
            color: '#94a3b8',
            fontSize: 10,
          },
          splitLine: { show: false },
          axisPointer: { label: { show: false } },
        },
        {
          type: 'time',
          gridIndex: 1,
          min: xMin,
          max: xMax,
          axisLabel: { show: false },
          splitLine: { show: false },
        },
      ],
      yAxis: [
        {
          scale: true,
          axisLabel: { color: '#94a3b8', fontSize: 10, formatter: (v: number) => v.toFixed(2) },
          splitLine: { lineStyle: { color: '#1e293b', type: 'dashed' } },
        },
        {
          gridIndex: 1,
          axisLabel: { show: false },
          splitLine: { show: false },
        },
      ],
      series: [
        // 折线
        {
          name: '价格',
          type: 'line',
          data: lineData,
          smooth: true,
          symbol: 'none',
          lineStyle: { width: 1.5, color: '#60a5fa' },
          areaStyle: {
            color: {
              type: 'linear',
              x: 0, y: 0, x2: 0, y2: 1,
              colorStops: [
                { offset: 0, color: 'rgba(96, 165, 250, 0.25)' },
                { offset: 1, color: 'rgba(96, 165, 250, 0.02)' },
              ],
            },
          },
          markLine: prevClose != null ? {
            symbol: 'none',
            silent: true,
            lineStyle: { color: '#64748b', type: 'dashed', width: 1 },
            data: [{ yAxis: prevClose, label: { formatter: `昨收 ${prevClose.toFixed(2)}`, color: '#94a3b8', position: 'insideEndTop', fontSize: 10 } }],
          } : undefined,
        },
        // 信号散点
        {
          name: '信号',
          type: 'scatter',
          data: markerPoints,
          z: 5,
          tooltip: { show: true, trigger: 'item' },
        },
        // 成交量柱
        {
          name: '成交量',
          type: 'bar',
          xAxisIndex: 1,
          yAxisIndex: 1,
          data: volData,
          itemStyle: { color: 'rgba(96, 165, 250, 0.35)' },
          barWidth: '60%',
        },
      ],
    } as echarts.EChartsCoreOption
  }, [bars, signals, prevClose])

  useEffect(() => {
    if (!containerRef.current) return
    const chart = echarts.init(containerRef.current, undefined, { renderer: 'canvas' })
    chartRef.current = chart
    chart.setOption(option)
    const resize = () => chart.resize()
    window.addEventListener('resize', resize)
    return () => {
      window.removeEventListener('resize', resize)
      chart.dispose()
      chartRef.current = null
    }
  }, [option])

  if (bars.length === 0) {
    // 根据 dataStatus 给精准诊断 (避免用户面对"空"不知道是啥原因)
    const emptyMsg = (() => {
      if (dataStatus === 'failed') {
        return (
          <span className="text-warn">
            后端已请求但未拉到数据 — 请检查代码是否正确 (A 股示例 300308.SZ / 港股示例 00700.HK)。
            可在持仓 T 列表左侧状态点查看详情。
          </span>
        )
      }
      if (dataStatus === 'ok') {
        return <span>暂无日内数据 — 标的已成功拉取, 但本时段内无价格变化</span>
      }
      return <span>暂无日内数据 — 启动监控后约 1 分钟开始绘制</span>
    })()
    return (
      <div className="h-48 grid place-items-center text-sm text-muted bg-surface border border-border rounded-card p-4 text-center">
        {emptyMsg}
      </div>
    )
  }
  return <div ref={containerRef} style={{ width: '100%', height }} />
}