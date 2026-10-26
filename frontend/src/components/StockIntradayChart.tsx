import { useEffect, useMemo, useState } from 'react'
import { useMutation, useQuery, useQueryClient } from '@tanstack/react-query'
import { Loader2 } from 'lucide-react'
import { api, type MinuteKlineRow } from '@/lib/api'
import { QK } from '@/lib/queryKeys'
import { EChartsIntraday, type IntradayTradeMark } from '@/components/EChartsIntraday'

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
  /** T 信号 B/S 标记 (透传给 EChartsIntraday 主图散点)。 */
  tradeMarks?: IntradayTradeMark[]
  /** 自动刷新间隔(ms)。undefined/0/false = 不轮询(默认)。个股对话框盘中实时刷新时传入。 */
  refetchIntervalMs?: number | false
  /**
   * 实时分时数据来源：
   * - "mock" / "real"：走 /free-realtime/intraday 接口，取 RealtimeStore 落盘的 tick，
   *   同分钟去重聚合，按 refetchIntervalMs 周期性 refetch 增量 tick。
   *   已发生 tick 不会再变（store 同分钟 keep=last，且过去分钟的不会重写）。
   * - undefined：默认走 klineMinute 落库分钟K（原有逻辑）。
   */
  intradayKind?: 'mock' | 'real'
}

/** 把 RealtimeStore 的 tick 行（last_price + ts_ms）按分钟聚合成 MinuteKlineRow。
 *
 * - 分钟内第一个/最后一个 tick → open/close，分钟内极值 → high/low
 * - 成交量/成交额口径自动判别：真实源（新浪/腾讯/东财快照）的 volume/amount
 *   是「当日累计值」，mock 是「单 tick 增量」。整日 volume 单调不减 → 累计口径，
 *   按 tick 差值折算分钟增量；否则直接求和。保证两种数据源的分钟量柱口径一致
 *   （专业软件的分钟成交量），同时分时均价线（Σamount ÷ Σvolume股）可真实计算
 * - 只输出当前已发生的分钟点（不会铺满全日）→ 体现"按频率逐步生成"
 */
export function aggregateTicksToMinuteRows(
  ticks: Array<Record<string, unknown>>,
): MinuteKlineRow[] {
  const sorted = ticks
    .map((t) => ({
      tsMs: Number(t.ts_ms ?? 0),
      price: Number(t.last_price ?? 0),
      volume: Number(t.volume ?? 0),
      amount: Number(t.amount ?? 0),
    }))
    .filter((t) => t.tsMs > 0 && t.price > 0)
    .sort((a, b) => a.tsMs - b.tsMs)
  if (!sorted.length) return []
  // 累计口径判别：volume 全程单调不减 → 快照累计值；少量 tick 时不判别（易误判）
  const cumulative =
    sorted.length >= 5 &&
    sorted.every((t, i) => i === 0 || t.volume >= sorted[i - 1].volume)
  interface MinuteAcc {
    open: number
    close: number
    high: number
    low: number
    volume: number
    amount: number
  }
  const byMinute = new Map<string, MinuteAcc>()
  let prevVol = 0
  let prevAmt = 0
  for (const t of sorted) {
    // 后端 ts_ms 是北京时间 tick 时间戳；EChartsIntraday 的 datetime 用 UTC
    // （组件内部 formatMinuteTime 会 +8 显示）。北京 HH:mm → UTC HH:mm-8。
    const d = new Date(t.tsMs)
    const key = `${String(d.getUTCHours()).padStart(2, '0')}:${String(d.getUTCMinutes()).padStart(2, '0')}`
    const volDelta = cumulative ? Math.max(0, t.volume - prevVol) : t.volume
    const amtDelta = cumulative ? Math.max(0, t.amount - prevAmt) : t.amount
    prevVol = t.volume
    prevAmt = t.amount
    let acc = byMinute.get(key)
    if (!acc) {
      acc = { open: t.price, close: t.price, high: t.price, low: t.price, volume: 0, amount: 0 }
      byMinute.set(key, acc)
    }
    acc.close = t.price
    if (t.price > acc.high) acc.high = t.price
    if (t.price < acc.low) acc.low = t.price
    acc.volume += volDelta
    acc.amount += amtDelta
  }
  const rows: MinuteKlineRow[] = []
  for (const [key, acc] of Array.from(byMinute.entries()).sort()) {
    rows.push({
      datetime: `${key}:00Z`,
      open: Number(acc.open.toFixed(2)),
      high: Number(acc.high.toFixed(2)),
      low: Number(acc.low.toFixed(2)),
      close: Number(acc.close.toFixed(2)),
      volume: Math.max(0, Math.floor(acc.volume)),
      amount: Math.max(0, Math.round(acc.amount)),
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
  tradeMarks,
  refetchIntervalMs,
  intradayKind,
}: Props) {
  const qc = useQueryClient()
  const [minuteDismissed, setMinuteDismissed] = useState(false)

  // intradayKind 提供 → 走 RealtimeStore.tick 路径；否则走 klineMinute（落库分钟K）
  const useTickPath = !!intradayKind && !!symbol && !!date

  const tickQ = useQuery({
    queryKey: ['free-realtime', 'intraday', symbol, intradayKind ?? ''] as const,
    queryFn: () => api.freeRealtimeIntraday(symbol, intradayKind as 'mock' | 'real'),
    enabled: useTickPath,
    refetchInterval: refetchIntervalMs,
  })

  const minute = useQuery({
    queryKey: QK.klineMinute(symbol, date ?? ''),
    queryFn: () => api.klineMinute(symbol, date ?? undefined),
    enabled: !!symbol && !!date && !useTickPath,
    refetchInterval: refetchIntervalMs,
  })

  // tick 路径下：把 RealtimeStore tick 聚合成分钟K行（与后端同分钟去重口径一致）。
  // 注意：useMemo 必须在组件顶层调用，不能放在 `if (useTickPath)` 块内（违反 Hooks 规则），
  // 否则条件切换时 React hook 调用顺序错乱，会导致 tickRows 不更新或走错分支。
  const ticks = (tickQ.data?.rows ?? []) as Array<Record<string, unknown>>
  const tickRows = useMemo(
    () => aggregateTicksToMinuteRows(ticks),
    [ticks],
  )

  const fetchMinute = useMutation({
    mutationFn: () => api.syncMinuteSingle(symbol),
    onSuccess: () => {
      qc.invalidateQueries({ queryKey: ['kline-minute', symbol] })
      qc.invalidateQueries({ queryKey: QK.klineMinute(symbol, date ?? '') })
      setMinuteDismissed(false)
    },
  })

  useEffect(() => {
    setMinuteDismissed(false)
    onPriceHover?.(null)
  }, [date, onPriceHover])

  if (!symbol || !date) return null

  // tick 路径渲染：只展示当前已发生的 tick，曲线随时间推进延伸
  if (useTickPath) {
    const isMock = intradayKind === 'mock'
    // 昨收基线（tick 路径）：优先用 tick 行自带的 prev_close（实时快照权威值），
    // 调用方传入的 prevClose（StockPanel 取自本地日K，可能滞后）仅作回退。
    // 之前反向优先级导致本地日K未更新时基线过期，出现如 -0.86% 的错误百分比。
    const tickPrevClose = (() => {
      for (const t of ticks) {
        const v = Number(t.prev_close ?? 0)
        if (v > 0 && Number.isFinite(v)) return v
      }
      return prevClose
    })()
    return (
      <div className={className} style={{ height, flexShrink: 0 }}>
        {isMock && (
          <div className="mb-1 inline-flex items-center gap-1 rounded bg-amber-500/15 px-2 py-0.5 text-[10px] font-medium text-amber-600 dark:text-amber-400">
            <span className="h-1.5 w-1.5 rounded-full bg-amber-500" />
            模拟数据（按频率生成）
          </div>
        )}
        {tickQ.isLoading && <div className="text-xs text-muted py-2">分时加载中…</div>}
        {!tickQ.isLoading && tickRows.length === 0 && (
          <div className="flex flex-col items-center justify-center h-full gap-2 text-xs text-muted">
            <span>暂无 tick 数据</span>
            <span className="text-[10px] opacity-70">
              {isMock ? '启动测试页 mock 循环后将按频率生成' : '盘中新加股票无法补抓此前数据'}
            </span>
          </div>
        )}
        {tickRows.length > 0 && (
          <EChartsIntraday
            data={tickRows}
            height={isMock ? height - 22 : height}
            prevClose={tickPrevClose}
            date={date}
            onPriceHover={onPriceHover}
            onPriceDoubleClick={onPriceDoubleClick}
            currentPrice={currentPrice}
            priceLines={priceLines}
            tradeMarks={tradeMarks}
            tickMode
          />
        )}
      </div>
    )
  }

  const minuteRows: MinuteKlineRow[] = minute.data?.rows ?? []
  const sourceIsNone = minute.data?.source === 'none'
  const isIndex = minute.data?.asset_type === 'index'

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
            <div className="flex items-center justify-center h-full text-xs text-muted">指数暂无分钟数据</div>
          ) : sourceIsNone ? (
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
                className="px-4 py-1.5 rounded-btn bg-elevated text-secondary text-xs font-medium hover:bg-elevated/80 transition-colors duration-150"
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
