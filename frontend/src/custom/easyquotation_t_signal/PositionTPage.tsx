import { useEffect, useMemo, useState } from 'react'
import { useMutation, useQuery, useQueryClient } from '@tanstack/react-query'
import { useSearchParams } from 'react-router-dom'
import {
  AlertTriangle,
  Bell,
  BellRing,
  ChevronDown,
  ChevronRight,
  Plus,
  Settings2,
  Trash2,
  XCircle,
} from 'lucide-react'
import { EmptyState } from '@/components/EmptyState'
import { StockPanel } from '@/components/StockPanel'
import {
  useFreeRealtimeIntradayRefetchMs,
  useFreeRealtimeTestStatus,
} from '@/custom/free_realtime/hooks'
import { AddPositionDialog } from './AddPositionDialog'
import {
  tSignalApi,
  type TSignalConfig,
} from './api'

const QK = ['t-signal'] as const

const statusKey = () => [...QK, 'status'] as const
const configKey = () => [...QK, 'config'] as const
const positionsKey = () => [...QK, 'positions'] as const
const signalsKey = () => [...QK, 'signals', 'recent'] as const

interface ConfigDraft {
  price_change_pct_threshold: string
  volume_ratio_threshold: string
  big_order_imbalance_threshold: string
  cooldown_seconds: string
  strategies: string[]
}
const toDraft = (c: TSignalConfig): ConfigDraft => ({
  price_change_pct_threshold: String(c.price_change_pct_threshold),
  volume_ratio_threshold: String(c.volume_ratio_threshold),
  big_order_imbalance_threshold: String(Math.round(c.big_order_imbalance_threshold * 100)),
  cooldown_seconds: String(c.cooldown_seconds),
  strategies: [...(c.strategies ?? [])],
})

// 策略选项：key 与后端 STRATEGY_KINDS 对应
const STRATEGY_OPTIONS: Array<{ key: string; label: string; desc: string }> = [
  { key: 'price_change', label: '涨跌幅阈值', desc: '现价相对昨收波动超阈值' },
  { key: 'volume_surge', label: '量比异动', desc: '即时成交额相对近期窗口放量' },
  { key: 'big_order_imbalance', label: '买卖盘失衡', desc: '买一/卖一挂单量失衡' },
  { key: 'vwap_revert', label: 'VWAP均价回归', desc: '现价偏离当日均价过大，回归倾向做T' },
  { key: 'rsi_extreme', label: 'RSI极值反转', desc: '短周期RSI超买/超卖反转倾向' },
  { key: 'range_break', label: '分时区间突破', desc: '突破/跌破近期分时区间沿，顺势做T' },
]

function ConfigCard({ config }: { config: TSignalConfig }) {
  const qc = useQueryClient()
  const [draft, setDraft] = useState<ConfigDraft>(() => toDraft(config))
  const [error, setError] = useState<string | null>(null)
  const dirty = useMemo(() => JSON.stringify(draft) !== JSON.stringify(toDraft(config)), [draft, config])
  const mutation = useMutation({
    mutationFn: (patch: Partial<TSignalConfig>) => tSignalApi.putConfig(patch),
    onSuccess: (next) => {
      setError(null)
      qc.setQueryData(configKey(), next)
    },
    onError: (e) => setError(e instanceof Error ? e.message : '保存失败'),
  })
  const save = () => {
    mutation.mutate({
      price_change_pct_threshold: Number(draft.price_change_pct_threshold),
      volume_ratio_threshold: Number(draft.volume_ratio_threshold),
      big_order_imbalance_threshold: Number(draft.big_order_imbalance_threshold) / 100,
      cooldown_seconds: Number(draft.cooldown_seconds),
      strategies: draft.strategies,
    })
  }
  const toggleStrategy = (key: string) => {
    setDraft((d) => ({
      ...d,
      strategies: d.strategies.includes(key)
        ? d.strategies.filter((s) => s !== key)
        : [...d.strategies, key],
    }))
  }
  return (
    <div className="bg-surface border border-border rounded-card p-4">
      <div className="flex items-center gap-2 mb-3">
        <Settings2 className="h-4 w-4 text-accent" />
        <h2 className="text-sm font-medium text-foreground">信号策略</h2>
      </div>
      {/* 策略多选：勾选哪些算法，盘中就实时计算哪些信号 */}
      <div className="grid grid-cols-1 md:grid-cols-2 gap-2 mb-3">
        {STRATEGY_OPTIONS.map((s) => {
          const checked = draft.strategies.includes(s.key)
          return (
            <label
              key={s.key}
              className={`flex items-start gap-2 px-3 py-2 rounded-btn border cursor-pointer ${
                checked ? 'bg-accent/10 border-accent/40' : 'bg-elevated border-border'
              }`}
            >
              <input
                type="checkbox"
                className="mt-0.5"
                checked={checked}
                onChange={() => toggleStrategy(s.key)}
              />
              <span className="min-w-0">
                <span className="block text-xs font-medium text-foreground">{s.label}</span>
                <span className="block text-[11px] text-muted">{s.desc}</span>
              </span>
            </label>
          )
        })}
      </div>
      <div className="grid grid-cols-1 md:grid-cols-2 gap-3">
        <label className="block">
          <span className="text-xs text-muted">涨跌幅阈值 (%)</span>
          <input
            type="number" step="0.1" min="0.1"
            value={draft.price_change_pct_threshold}
            onChange={(e) => setDraft({ ...draft, price_change_pct_threshold: e.target.value })}
            className="mt-1 w-full bg-elevated border border-border rounded-btn px-2 py-1.5 text-sm text-foreground focus:outline-none focus:ring-1 focus:ring-accent"
          />
        </label>
        <label className="block">
          <span className="text-xs text-muted">量比阈值 (x)</span>
          <input
            type="number" step="0.1" min="1"
            value={draft.volume_ratio_threshold}
            onChange={(e) => setDraft({ ...draft, volume_ratio_threshold: e.target.value })}
            className="mt-1 w-full bg-elevated border border-border rounded-btn px-2 py-1.5 text-sm text-foreground focus:outline-none focus:ring-1 focus:ring-accent"
          />
        </label>
        <label className="block">
          <span className="text-xs text-muted">买卖盘失衡阈值 (%)</span>
          <input
            type="number" step="1" min="1" max="99"
            value={draft.big_order_imbalance_threshold}
            onChange={(e) => setDraft({ ...draft, big_order_imbalance_threshold: e.target.value })}
            className="mt-1 w-full bg-elevated border border-border rounded-btn px-2 py-1.5 text-sm text-foreground focus:outline-none focus:ring-1 focus:ring-accent"
          />
        </label>
        <label className="block">
          <span className="text-xs text-muted">Cooldown (秒)</span>
          <input
            type="number" step="1" min="0" max="3600"
            value={draft.cooldown_seconds}
            onChange={(e) => setDraft({ ...draft, cooldown_seconds: e.target.value })}
            className="mt-1 w-full bg-elevated border border-border rounded-btn px-2 py-1.5 text-sm text-foreground focus:outline-none focus:ring-1 focus:ring-accent"
          />
        </label>
      </div>
      {error && <p className="mt-2 text-xs text-warn flex items-center gap-1.5"><AlertTriangle className="h-3.5 w-3.5" /> {error}</p>}
      <div className="mt-3 flex items-center justify-end gap-2">
        <button
          type="button" onClick={() => setDraft(toDraft(config))}
          disabled={!dirty || mutation.isPending}
          className="inline-flex items-center h-8 px-3 rounded-btn text-xs text-secondary hover:bg-elevated disabled:opacity-50"
        >
          还原
        </button>
        <button
          type="button" onClick={save}
          disabled={!dirty || mutation.isPending}
          className="inline-flex items-center h-8 px-3 rounded-btn bg-accent text-xs text-white hover:bg-accent/90 disabled:opacity-50"
        >
          {mutation.isPending ? '保存中...' : '保存配置'}
        </button>
      </div>
    </div>
  )
}

function PositionRow({
  symbol,
  name,
  enabled,
  active,
  dataStatus,
  onSelect,
  onToggleEnabled,
  onRemove,
}: {
  symbol: string
  name: string
  enabled: boolean
  active: boolean
  dataStatus: 'ok' | 'failed' | 'never'
  onSelect: () => void
  onToggleEnabled: () => void
  onRemove: () => void
}) {
  // 拉取状态点: 绿=拉到 / 红=失败 / 灰=未拉过; 鼠标悬停给原因提示
  const statusDot = (() => {
    if (dataStatus === 'ok') {
      return { color: 'bg-bull', title: '正常拉取数据' }
    }
    if (dataStatus === 'failed') {
      return { color: 'bg-warn', title: '拉取失败 — 请检查代码是否正确 (如 A 股应是 300308.SZ 不是 03308.HK)' }
    }
    return { color: 'bg-muted-foreground/40', title: '尚未拉取 (启动监控或等待下一轮)' }
  })()
  return (
    <div
      className={`flex items-center gap-3 px-3 py-2 border-b border-border last:border-b-0 cursor-pointer hover:bg-elevated/50 ${
        active ? 'bg-accent/10' : ''
      }`}
      onClick={onSelect}
    >
      <button
        type="button"
        onClick={(e) => { e.stopPropagation(); onToggleEnabled() }}
        className={`shrink-0 inline-flex items-center h-6 px-2 rounded-full text-[11px] border ${
          enabled ? 'bg-bull/10 border-bull/30 text-bull' : 'bg-elevated border-border text-muted'
        }`}
        title={enabled
            ? '点击停止本标的监控 (全局线程保持运行)'
            : '点击启用本标的监控 (需全局线程已启动)'}
      >
        {enabled ? '监控中' : '已暂停'}
      </button>
      <div className="flex-1 min-w-0">
        <div className="flex items-center gap-2 min-w-0">
          <span
            className={`inline-block w-1.5 h-1.5 rounded-full shrink-0 ${statusDot.color}`}
            title={statusDot.title}
          />
          <span className="font-medium text-foreground truncate">{name || symbol}</span>
        </div>
        <div className="text-xs text-muted">{symbol}</div>
      </div>
      <button
        type="button"
        onClick={(e) => { e.stopPropagation(); if (confirm(`移除 ${name || symbol}?`)) onRemove() }}
        className="h-7 w-7 grid place-items-center rounded-btn text-muted hover:bg-elevated hover:text-warn"
        title="移除"
      >
        <Trash2 className="h-3.5 w-3.5" />
      </button>
      <ChevronRight className="h-4 w-4 text-muted shrink-0" />
    </div>
  )
}

export function PositionTPage() {
  const [searchParams, setSearchParams] = useSearchParams()
  const selectedSymbol = searchParams.get('symbol') || ''
  const setSelectedSymbol = (s: string) => {
    const next = new URLSearchParams(searchParams)
    if (s) next.set('symbol', s)
    else next.delete('symbol')
    setSearchParams(next, { replace: true })
  }
  const [addOpen, setAddOpen] = useState(false)
  // 配置区折叠状态：默认折叠，让列表+详情占主屏
  const [configCollapsed, setConfigCollapsed] = useState(true)
  // 数据源视图：real 真实行情 / mock 测试页虚拟行情
  // 用 sessionStorage 持久化：刷新页面后保持在原视图（同一标签页内有效）
  const [dataSource, setDataSource] = useState<'real' | 'mock'>(() => {
    try {
      return sessionStorage.getItem('positionT.dataSource') === 'mock' ? 'mock' : 'real'
    } catch {
      return 'real'
    }
  })
  const switchDataSource = (k: 'real' | 'mock') => {
    setDataSource(k)
    try {
      sessionStorage.setItem('positionT.dataSource', k)
    } catch {
      // sessionStorage 不可用时静默降级为纯内存 state
    }
  }

  const statusQ = useQuery({
    queryKey: statusKey(),
    queryFn: tSignalApi.status,
    refetchInterval: (q) => (q.state.data?.running ? 5000 : 15000),
  })
  // 免费实时行情测试页状态: 与其他页面共享同一 queryKey 与轮询节奏
  const frTestStatusQ = useFreeRealtimeTestStatus()
  const configQ = useQuery({ queryKey: configKey(), queryFn: tSignalApi.getConfig })
  const positionsQ = useQuery({
    queryKey: positionsKey(),
    queryFn: tSignalApi.listPositions,
    refetchInterval: 8000,
  })

  const qc = useQueryClient()
  const toggleEnabled = useMutation({
    mutationFn: ({ symbol, enabled }: { symbol: string; enabled: boolean }) =>
      tSignalApi.setPositionEnabled(symbol, enabled),
    onSuccess: () => void qc.invalidateQueries({ queryKey: positionsKey() }),
  })
  const remove = useMutation({
    mutationFn: (symbol: string) => tSignalApi.removePosition(symbol),
    onSuccess: (_data, deletedSymbol) => {
      void qc.invalidateQueries({ queryKey: positionsKey() })
      // 仅当删除的是当前选中标的时才清除 ?symbol=
      if (selectedSymbol === deletedSymbol) setSelectedSymbol('')
    },
  })

  // 分时图轮询节奏：跟随免费实时行情调度器/测试页循环（与超买超卖页同口径），
  // 不再用 t-signal 全局轮询状态做门控
  const intradayRefetchMs = useFreeRealtimeIntradayRefetchMs()
  // 今日信号列表（全部标的，real/mock 都含）；同时供分时图 B/S 标记取数
  const signalsQ = useQuery({
    queryKey: signalsKey(),
    queryFn: () => tSignalApi.recent(300),
    refetchInterval: intradayRefetchMs || 8000,
  })
  const todaySignals = useMemo(() => {
    const today = new Date().toDateString()
    return (signalsQ.data?.items ?? []).filter(
      (s) => new Date(s.ts_ms).toDateString() === today,
    )
  }, [signalsQ.data])
  // 选中标的的 B/S 标记（有方向的信号才上图标；价格取信号触发时点的现价）
  const tradeMarks = useMemo(
    () =>
      selectedSymbol
        ? todaySignals
            .filter((s) => s.symbol === selectedSymbol && s.price > 0)
            .map((s) => ({
              ts_ms: s.ts_ms,
              side: (s.side === 'sell' ? 'sell' : 'buy') as 'buy' | 'sell',
              price: s.price,
            }))
        : undefined,
    [todaySignals, selectedSymbol],
  )
  const status = statusQ.data
  const config = configQ.data
  const positions = positionsQ.data?.items ?? []

  // mock 循环停止后自动切回真实数据源，避免停在无数据的 mock 视图。
  // 注意：必须等测试页状态真正加载（isSuccess）后再判断，否则刷新瞬间
  // mock_running 还是 undefined，会把持久化的 Mock 视图错误地切回真实。
  useEffect(() => {
    if (frTestStatusQ.isSuccess && dataSource === 'mock' && !frTestStatusQ.data?.mock_running) {
      switchDataSource('real')
    }
  }, [frTestStatusQ.isSuccess, frTestStatusQ.data, dataSource])

  // 选中标的如果不在列表里 (被删了), 自动清空（useEffect 中执行，避免渲染期 setState）
  useEffect(() => {
    if (selectedSymbol && !positions.some((p) => p.symbol === selectedSymbol)) {
      setSelectedSymbol('')
    }
  }, [selectedSymbol, positions])

  return (
    <div className="h-full flex flex-col">
      <header className="px-6 py-4 border-b border-border bg-surface/50">
        <div className="flex items-center gap-2">
          <BellRing className="h-5 w-5 text-accent" />
          <h1 className="text-lg font-medium text-foreground">持仓 T</h1>
          <div className="ml-auto flex rounded-btn border border-border overflow-hidden text-[11px]" title="数据源视图">
            {(['real', 'mock'] as const).map((k) => (
              <button
                key={k}
                type="button"
                disabled={k === 'mock' && !frTestStatusQ.data?.mock_running}
                className={`px-2 h-7 ${
                  dataSource === k
                    ? 'bg-accent text-white'
                    : 'bg-elevated text-secondary hover:text-foreground disabled:opacity-40'
                }`}
                onClick={() => switchDataSource(k)}
              >
                {k === 'real' ? '真实' : 'Mock'}
              </button>
            ))}
          </div>
        </div>
        <p className="mt-1 text-xs text-secondary">
          主动维护「要做 T 的持仓」列表 — 行情拉取由「A 股免费实时行情」统一调度（间隔/时段/开关），命中规则即推飞书 + 落库。
          单票详情页可看当日分时图与历史信号点位。
        </p>
      </header>

      <div className="flex-1 overflow-auto px-6 py-4 space-y-4">
        {statusQ.isLoading || configQ.isLoading ? (
          <div className="text-sm text-muted py-8 text-center">加载中...</div>
        ) : !status || !config ? (
          <EmptyState icon={XCircle} title="加载失败" hint={statusQ.error?.message ?? configQ.error?.message ?? '未知错误'} />
        ) : (
          <>
            {/* 可折叠配置区：折叠后只显示状态条。
                行情拉取已由「A 股免费实时行情」统一收口（间隔/时段/模块开关），
                此处仅保留阈值与策略配置，不再有全局启停。 */}
            <div className="bg-surface border border-border rounded-card overflow-hidden">
              <button
                type="button"
                onClick={() => setConfigCollapsed((v) => !v)}
                className="w-full flex items-center justify-between px-4 py-2.5 hover:bg-elevated/50"
              >
                <div className="flex items-center gap-3 min-w-0">
                  <BellRing className="h-4 w-4 text-accent shrink-0" />
                  <span className="text-sm font-medium text-foreground">
                    信号配置 · 今日信号 {status.signals_today}
                  </span>
                  <span className="text-[11px] text-muted shrink-0">
                    {status.enabled_position_count}/{status.position_count} 启用
                    {status.last_fetch_error ? ' · 上轮失败' : ''}
                  </span>
                </div>
                <ChevronDown
                  className={`h-4 w-4 text-muted shrink-0 transition-transform ${configCollapsed ? '' : 'rotate-180'}`}
                />
              </button>
              {!configCollapsed && (
                <div className="border-t border-border">
                  <ConfigCard config={config} />
                </div>
              )}
            </div>

            {/* 列表 + 详情：右侧整体复用 StockPanel（日K+分时）。
                左列 = 持仓T列表(上半) + 今日信号(下半)，各占一半高度、各自滚动；右列 = 详情占满剩余宽度 */}
            <div className="grid grid-cols-1 lg:grid-cols-[300px_1fr] gap-4 flex-1 min-h-0">
              {/* 左列容器 */}
              <div className="flex flex-col gap-4 min-h-0">
                {/* 持仓 T 列表（上半） */}
                <div className="bg-surface border border-border rounded-card flex flex-col overflow-hidden flex-1 min-h-0">
                  <div className="flex items-center justify-between px-4 py-2 border-b border-border">
                    <h2 className="text-sm font-medium text-foreground">
                      持仓 T 列表 ({positions.length})
                    </h2>
                    <button
                      type="button"
                      onClick={() => setAddOpen(true)}
                      className="inline-flex items-center h-7 px-2 rounded-btn bg-accent text-[11px] text-white hover:bg-accent/90"
                    >
                      <Plus className="h-3 w-3 mr-0.5" /> 添加
                    </button>
                  </div>
                  {positions.length === 0 ? (
                    <EmptyState
                      icon={Bell}
                      title="持仓 T 列表为空"
                      hint="点击「添加」输入项目 symbol (300308.SZ / 00700.HK), 添加后启用即可开始日内监控。"
                    />
                  ) : (
                    <div className="overflow-auto">
                      {positions.map((p) => (
                        <PositionRow
                          key={p.symbol}
                          symbol={p.symbol}
                          name={p.name}
                          enabled={p.enabled}
                          active={p.symbol === selectedSymbol}
                          dataStatus={p.data_status}
                          onSelect={() => setSelectedSymbol(p.symbol)}
                          onToggleEnabled={() => toggleEnabled.mutate({ symbol: p.symbol, enabled: !p.enabled })}
                          onRemove={() => remove.mutate(p.symbol)}
                        />
                      ))}
                    </div>
                  )}
                </div>

                {/* 今日信号（下半）：全部标的的触发记录，按时间倒序；点击行跳转该标的详情 */}
                <div className="bg-surface border border-border rounded-card flex flex-col overflow-hidden flex-1 min-h-0">
                  <div className="flex items-center justify-between px-4 py-2 border-b border-border text-xs flex-shrink-0">
                    <h2 className="text-sm font-medium text-foreground flex items-center gap-1.5">
                      <BellRing className="h-3.5 w-3.5 text-accent" />
                      今日信号 ({todaySignals.length})
                    </h2>
                  </div>
                  <div className="overflow-auto">
                    {todaySignals.length === 0 ? (
                      <div className="px-3 py-4 text-xs text-muted">今日暂无信号</div>
                    ) : (
                      [...todaySignals]
                        .sort((a, b) => b.ts_ms - a.ts_ms)
                        .map((s, i) => {
                          const t = new Date(s.ts_ms)
                          const hhmmss = `${String(t.getHours()).padStart(2, '0')}:${String(t.getMinutes()).padStart(2, '0')}:${String(t.getSeconds()).padStart(2, '0')}`
                          const buy = s.side !== 'sell'
                          // 策略名与上方「信号配置」勾选项同名（key 与后端 TSignal.kind 一致）
                          const strategyLabel =
                            STRATEGY_OPTIONS.find((o) => o.key === s.kind)?.label ?? s.kind
                          return (
                            <button
                              key={`${s.ts_ms}-${s.symbol}-${s.kind}-${i}`}
                              onClick={() => setSelectedSymbol(s.symbol)}
                              className="w-full text-left px-3 py-1.5 border-b border-line/30 last:border-b-0 hover:bg-accent/5 flex items-start gap-2 text-xs"
                            >
                              <span
                                className={`flex-shrink-0 mt-0.5 px-1 rounded font-mono font-bold text-[10px] leading-4 ${
                                  s.side === ''
                                    ? 'bg-muted/40 text-muted'
                                    : buy
                                      ? 'bg-up/10 text-up'
                                      : 'bg-down/10 text-down'
                                }`}
                              >
                                {s.side === '' ? '·' : buy ? 'B' : 'S'}
                              </span>
                              <span className="min-w-0">
                                <span className="font-medium">{s.name || s.symbol}</span>
                                <span className="text-muted ml-1.5 font-mono">{hhmmss}</span>
                                <span className="ml-1.5 px-1 rounded bg-muted/40 text-muted text-[10px]">
                                  {strategyLabel}
                                </span>
                                <span className="block text-muted truncate" title={s.detail}>{s.detail}</span>
                              </span>
                            </button>
                          )
                        })
                    )}
                  </div>
                </div>
              </div>

              {/* 单票详情：直接复用 StockPanel（日K+分时）。
                  intradayKind 走 free_realtime 模块的 /api/free-realtime/intraday（real/mock 视图跟随页头切换），
                  而不是 /api/kline/minute（后者会回退 TickFlow 免费服务，但免费服务不支持分钟K，会失败）。 */}
              {selectedSymbol ? (
                <StockPanel
                  symbol={selectedSymbol}
                  showIntraday
                  intradayKind={dataSource}
                  height={560}
                  className="min-w-0"
                  refetchIntervalMs={intradayRefetchMs}
                  tradeMarks={tradeMarks}
                />
              ) : (
                <EmptyState
                  icon={BellRing}
                  title="点击左侧标的查看详情"
                  hint="选中后右侧会展示日K + 分时图（数据源未提供时用模拟数据预览 UI）。"
                />
              )}
            </div>
          </>
        )}
      </div>

      <AddPositionDialog open={addOpen} onClose={() => setAddOpen(false)} />
    </div>
  )
}