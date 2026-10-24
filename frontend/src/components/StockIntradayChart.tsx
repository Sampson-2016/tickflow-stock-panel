import { useEffect, useMemo, useState } from 'react'
import { useMutation, useQuery, useQueryClient } from '@tanstack/react-query'
import { Loader2 } from 'lucide-react'
import { api, type MinuteKlineRow } from '@/lib/api'
import { QK } from '@/lib/queryKeys'
import { EChartsIntraday } from '@/components/EChartsIntraday'

interface Props {
  symbol: string
  date: string | null
  height?: number
  prevClose?: number
  className?: string
  onPriceHover?: (price: number | null) => void
  onPriceDoubleClick?: (price: number, currentPrice: number) => void
  currentPrice?: number
  priceLines?: { value: number; label?: string; color?: string }[]
  /** 自动刷新间隔(ms)。undefined/0 = 不轮询(默认)。个股对话框盘中实时刷新时传入。 */
  refetchIntervalMs?: number
  /**
   * 当数据源未提供该日分钟数据时，生成 mock 分钟数据填充 UI 用于预览。
   * 仅用于 UI 验证场景；真实使用不应开启。
   */
  allowMockFallback?: boolean
  /** mock 模式标记，用于在 UI 上提示"当前为模拟数据" */
  showMockBadge?: boolean
}

/**
 * 生成 mock 分钟K数据，覆盖完整交易日 9:30-11:30/13:00-15:00。
 * 基于前收做平缓随机游走，volume/amount 给出合理量级。
 */
function generateMockMinuteRows(
  prevClose: number,
  date: string,
  seed: number,
): MinuteKlineRow[] {
  // 简易 mulberry32 PRNG，保证同种子可复现
  let s = seed >>> 0
  const rand = () => {
    s |= 0
    s = (s + 0x6D2B79F5) | 0
    let t = Math.imul(s ^ (s >>> 15), 1 | s)
    t = (t + Math.imul(t ^ (t >>> 7), 61 | t)) ^ t
    return ((t ^ (t >>> 14)) >>> 0) / 4294967296
  }

  const rows: MinuteKlineRow[] = []
  // 与 FULL_DAY_TIMES 一致：9:30-11:30 + 13:00-15:00 共 240 个分钟点
  const times: string[] = []
  for (let h = 9, m = 30; h * 60 + m <= 11 * 60 + 30; m++) {
    if (m === 60) { h++; m = 0 }
    times.push(`${String(h).padStart(2, '0')}:${String(m).padStart(2, '0')}`)
  }
  for (let h = 13, m = 0; h * 60 + m <= 15 * 60; m++) {
    if (m === 60) { h++; m = 0 }
    times.push(`${String(h).padStart(2, '0')}:${String(m).padStart(2, '0')}`)
  }

  let price = prevClose
    // 单步变动上限为价格的 0.3%
    const stepMax = prevClose * 0.003
    // 总波动目标 ±2% 以内
    for (let i = 0; i < times.length; i++) {
      const t = times[i]
      // 开盘前 5 分钟波动更大
      const volatility = i < 5 ? 1.5 : 1.0
      const delta = (rand() - 0.5) * 2 * stepMax * volatility
      let newPrice = price + delta
      // 全日波动限制在 ±2%
      if (newPrice > prevClose * 1.02) newPrice = prevClose * 1.02
      if (newPrice < prevClose * 0.98) newPrice = prevClose * 0.98
      price = newPrice

      const open = i === 0 ? prevClose : rows[i - 1].close
      const close = price
      const high = Math.max(open, close) + Math.abs(delta) * 0.5
      const low = Math.min(open, close) - Math.abs(delta) * 0.5
      // 基础成交量 + 随机扰动；开盘/收盘附近略放量
      const baseVol = 5000 + rand() * 15000
      const hourMin = i < 5 || i > times.length - 10 ? 2.0 : 1.0
      const volume = Math.floor(baseVol * hourMin)
      const amount = Math.floor(volume * 100 * close)

      // datetime 用 UTC 时间（北京时间 - 8h），EChartsIntraday 的 formatMinuteTime
      // 会做 +8 时区转换，这样最终映射回北京时间的 HH:mm 与 FULL_DAY_TIMES 对齐。
      const [bh, bm] = t.split(':').map(Number)
      const utcH = (bh - 8 + 24) % 24
      const utcHH = String(utcH).padStart(2, '0')
      const utcMM = String(bm).padStart(2, '0')
      rows.push({
        datetime: `${date}T${utcHH}:${utcMM}:00Z`,
        open: Number(open.toFixed(2)),
        high: Number(high.toFixed(2)),
        low: Number(low.toFixed(2)),
        close: Number(close.toFixed(2)),
        volume,
        amount,
      })
    }
  return rows
}

export function StockIntradayChart({
  symbol,
  date,
  height = 520,
  prevClose,
  className,
  onPriceHover,
  onPriceDoubleClick,
  currentPrice,
  priceLines,
  refetchIntervalMs,
  allowMockFallback = false,
  showMockBadge = false,
}: Props) {
  const qc = useQueryClient()
  const [minuteDismissed, setMinuteDismissed] = useState(false)

  const minute = useQuery({
    queryKey: QK.klineMinute(symbol, date ?? ''),
    queryFn: () => api.klineMinute(symbol, date ?? undefined),
    enabled: !!symbol && !!date,
    refetchInterval: refetchIntervalMs,
  })

  const fetchMinute = useMutation({
    mutationFn: () => api.syncMinuteSingle(symbol),
    onSuccess: () => {
      qc.invalidateQueries({ queryKey: ['kline-minute', symbol] })
      qc.invalidateQueries({ queryKey: QK.klineMinute(symbol, date ?? '') })
      setMinuteDismissed(false)
    },
  })

  const minuteRows: MinuteKlineRow[] = useMemo(() => minute.data?.rows ?? [], [minute.data?.rows])
  // source=none 表示本地无数据且 TickFlow 也拉不到 (停牌/复牌延迟/非交易日)
  // 此时不弹"是否获取"询问窗, 只做静态提示, 避免误导用户去拉明知拉不到的数据
  const sourceIsNone = minute.data?.source === 'none'
  // 指数分钟K无本地存储且不支持落库获取 (后端 sync_minute_single 显式拒绝), 不显示获取按钮
  const isIndex = minute.data?.asset_type === 'index'

  // mock 兜底：数据源未提供时用模拟数据填充 UI
  const mockRows = useMemo<MinuteKlineRow[]>(() => {
    if (!allowMockFallback) return []
    if (!date) return []
    // 关键：等 minute 查询完成（成功或失败都行），避免在加载中触发
    if (minute.isLoading || minute.isPending) return []
    if (minuteRows.length > 0) return []
    // 优先用后端返回的 prev_close；退而求其次用 prop 传入的 prevClose
    const base = minute.data?.prev_close ?? prevClose
    if (!base || base <= 0) return []
    // 用 symbol+date 字符串生成稳定种子
    const seed = (symbol + date).split('').reduce((h, c) => (h * 31 + c.charCodeAt(0)) | 0, 0)
    const rows = generateMockMinuteRows(base, date, seed)
    // eslint-disable-next-line no-console
    console.log('[StockIntradayChart] mock fallback triggered', {
      symbol, date, base, rowCount: rows.length, minuteStatus: minute.status,
    })
    return rows
  }, [allowMockFallback, date, minute.isLoading, minute.isPending, minuteRows.length, symbol, minute.data?.prev_close, prevClose, minute.status])

  const showMock = mockRows.length > 0

  useEffect(() => {
    setMinuteDismissed(false)
    onPriceHover?.(null)
  }, [date, onPriceHover])

  if (!symbol || !date) return null

  // mock 兜底渲染：直接走 EChartsIntraday，UI 完全一致
  if (showMock) {
    return (
      <div className={className} style={{ height, flexShrink: 0 }}>
        {showMockBadge && (
          <div className="mb-1 inline-flex items-center gap-1 rounded bg-amber-500/15 px-2 py-0.5 text-[10px] font-medium text-amber-600 dark:text-amber-400">
            <span className="h-1.5 w-1.5 rounded-full bg-amber-500" />
            模拟数据（UI 预览）
          </div>
        )}
        <EChartsIntraday
          data={mockRows}
          height={showMockBadge ? height - 22 : height}
          prevClose={prevClose}
          date={date}
          onPriceHover={onPriceHover}
          onPriceDoubleClick={onPriceDoubleClick}
          currentPrice={currentPrice}
          priceLines={priceLines}
        />
      </div>
    )
  }

  return (
    <div className={className} style={{ height, flexShrink: 0 }}>
      {minute.isLoading && <div className="text-xs text-muted py-2">分时加载中…</div>}
      {!minute.isLoading && minuteRows.length === 0 && (
        <>
          {fetchMinute.isPending ? (
            <div className="flex items-center justify-center h-full gap-2 text-xs text-accent">
              <Loader2 className="h-3.5 w-3.5 animate-spin" />
              <span>正在获取分钟K数据…</span>
            </div>
          ) : isIndex ? (
            // 指数: 分钟K仅支持实时读取, 无落库获取入口
            <div className="flex items-center justify-center h-full text-xs text-muted">指数暂无分钟数据</div>
          ) : sourceIsNone ? (
            // 数据源确认无此日分钟数据 (停牌/复牌延迟等): 静态提示 + 保留重试
            <div className="flex flex-col items-center justify-center h-full gap-3">
              <div className="text-xs text-muted">该日暂无分钟数据（数据源未提供）</div>
              <button
                onClick={() => fetchMinute.mutate()}
                className="px-4 py-1.5 rounded-btn bg-elevated text-secondary text-xs font-medium hover:bg-elevated/80 transition-colors duration-150"
              >
                重新获取
              </button>
            </div>
          ) : minuteDismissed ? (
            <div className="flex flex-col items-center justify-center h-full gap-3">
              <div className="text-xs text-muted">暂无分钟数据</div>
              <button
                onClick={() => setMinuteDismissed(false)}
                className="px-4 py-1.5 rounded-btn bg-accent/90 text-base text-xs font-medium hover:bg-accent transition-colors duration-150"
              >
                获取分钟K
              </button>
            </div>
          ) : (
            <div className="flex flex-col items-center justify-center h-full gap-4">
              <div className="text-sm text-foreground">是否立即获取最近5日分钟K？</div>
              <div className="flex items-center gap-3">
                <button
                  onClick={() => fetchMinute.mutate()}
                  className="px-4 py-1.5 rounded-btn bg-accent/90 text-base text-xs font-medium hover:bg-accent transition-colors duration-150"
                >
                  确定
                </button>
                <button
                  onClick={() => setMinuteDismissed(true)}
                  className="px-4 py-1.5 rounded-btn bg-elevated text-secondary text-xs hover:bg-elevated/80 transition-colors duration-150"
                >
                  取消
                </button>
              </div>
            </div>
          )}
        </>
      )}
      {minuteRows.length > 0 && (
        <EChartsIntraday
          data={minuteRows}
          height={height}
          prevClose={prevClose}
          date={date}
          priceLimit={minute.data?.price_limit ?? undefined}
          onPriceHover={onPriceHover}
          onPriceDoubleClick={onPriceDoubleClick}
          currentPrice={currentPrice}
          priceLines={priceLines}
        />
      )}
    </div>
  )
}
