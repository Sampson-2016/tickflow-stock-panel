// 超买超卖模块页面 — 标的列表 + 信号 + 日K/分时详情（复用 StockPanel）
import { useMutation, useQuery, useQueryClient } from '@tanstack/react-query'
import {
  Activity,
  AlertTriangle,
  ChevronDown,
  ChevronRight,
  Gauge,
  Pause,
  Play,
  Plus,
  RefreshCcw,
  Search,
  Trash2,
  TrendingDown,
  TrendingUp,
} from 'lucide-react'
import { useMemo, useState } from 'react'
import { EmptyState } from '@/components/EmptyState'
import { StockPanel } from '@/components/StockPanel'
import { toast } from '@/components/Toast'
import { api } from '@/lib/api'
import { AddPositionDialog } from '@/custom/easyquotation_t_signal/AddPositionDialog'
import {
  BTN_GHOST,
  BTN_PRIMARY,
  BTN_SECONDARY,
  BTN_SM_PRIMARY,
  BTN_SM_SECONDARY,
  freeRealtimeApi,
  INPUT_CLS,
  type FreeRealtimeOverboughtSignal,
  type FreeRealtimeOverboughtSymbol,
  type FreeRealtimeStatus,
} from './api'

const QK = ['free-realtime-overbought'] as const
const symbolsKey = () => [...QK, 'symbols'] as const
const signalsKey = () => [...QK, 'signals'] as const
const statusKey = () => [...QK, 'status'] as const

export function OverboughtOversoldPage() {
  const qc = useQueryClient()
  const [selectedSymbol, setSelectedSymbol] = useState('')
  const [configCollapsed, setConfigCollapsed] = useState(true)
  const [filter, setFilter] = useState<'all' | 'signals' | 'enabled'>('all')
  const [search, setSearch] = useState('')
  const [addOpen, setAddOpen] = useState(false)

  // 调度器状态（用于分时图持续刷新）
  const statusQ = useQuery({
    queryKey: statusKey(),
    queryFn: freeRealtimeApi.status,
    refetchInterval: (q) => (q.state.data?.running ? 5000 : 15000),
  })
  // 测试页 mock/真实循环运行时也强制刷新分时图
  // 运行时 3s 高频；停止时 15s 低频仅用于感知启动
  const frTestStatusQ = useQuery({
    queryKey: ['free-realtime-test', 'status'] as const,
    queryFn: api.freeRealtimeTestStatus,
    refetchInterval: (q) => {
      const d = q.state.data
      return d && (d.mock_running || d.real_loop_running) ? 3000 : 15000
    },
  })

  // 候选标的列表（持仓T + 自选股）
  const symbolsQ = useQuery({
    queryKey: symbolsKey(),
    queryFn: freeRealtimeApi.overboughtSymbols,
    refetchInterval: 10000,
  })

  // 在线评估信号
  const signalsQ = useQuery({
    queryKey: signalsKey(),
    queryFn: freeRealtimeApi.overboughtSignals,
    refetchInterval: (q) => {
      const frRunning = !!q.state.data?.evaluated && q.state.data?.evaluated > 0
      return frRunning ? 5000 : 15000
    },
  })

  const toggleMut = useMutation({
    mutationFn: ({ symbol, enabled }: { symbol: string; enabled: boolean }) =>
      freeRealtimeApi.overboughtSymbolToggle(symbol, enabled),
    onSuccess: () => qc.invalidateQueries({ queryKey: symbolsKey() }),
    onError: (e: Error) => {
      console.error('toggle overbought symbol failed', e)
    },
  })

  const addMut = useMutation({
    mutationFn: ({ symbol, name }: { symbol: string; name?: string }) =>
      freeRealtimeApi.overboughtSymbolAdd(symbol, name),
    onSuccess: () => {
      qc.invalidateQueries({ queryKey: symbolsKey() })
    },
    onError: (e: Error) => console.error('add overbought symbol failed', e),
  })

  const deleteMut = useMutation({
    mutationFn: (symbol: string) => freeRealtimeApi.overboughtSymbolDelete(symbol),
    onSuccess: () => qc.invalidateQueries({ queryKey: symbolsKey() }),
    onError: (e: Error) => console.error('delete overbought symbol failed', e),
  })

  const status = statusQ.data
  const frTestRunning = !!(frTestStatusQ.data?.mock_running || frTestStatusQ.data?.real_loop_running)
  // 分时图轮询节奏：调度器或测试页循环运行时按配置 interval_sec 轮询（与抓取节奏对齐，避免高频拉前端）
  // 停止时返回 false：不轮询，无新数据
  const intradayRefetchMs = (status?.running || frTestRunning)
    ? Math.max(1000, (status?.interval_sec ?? 3) * 1000)
    : false
  const symbols = symbolsQ.data?.items ?? []
  const signals = signalsQ.data?.items ?? []
  const moduleEnabled = !!status?.modules?.overbought_oversold

  // 信号按 symbol 索引，便于列表加徽章
  const signalBySymbol = useMemo(() => {
    const m = new Map<string, FreeRealtimeOverboughtSignal>()
    for (const s of signals) m.set(s.symbol, s)
    return m
  }, [signals])

  // 过滤后的列表
  const filteredSymbols = useMemo(() => {
    const q = search.trim().toUpperCase()
    return symbols.filter((s) => {
      if (q && !s.symbol.includes(q) && !s.name.toUpperCase().includes(q)) return false
      if (filter === 'enabled' && !s.enabled) return false
      if (filter === 'signals' && !signalBySymbol.has(s.symbol)) return false
      return true
    })
  }, [symbols, search, filter, signalBySymbol])

  // 首次有标的时自动选中第一个
  if (!selectedSymbol && filteredSymbols.length > 0) {
    setSelectedSymbol(filteredSymbols[0].symbol)
  }

  const enabledCount = symbols.filter((s) => s.enabled).length
  const todayOverbought = signals.filter((s) => s.direction === 'overbought').length
  const todayOversold = signals.filter((s) => s.direction === 'oversold').length
  const selectedItem = symbols.find((s) => s.symbol === selectedSymbol)

  return (
    <div className="flex h-full flex-col">
      <header className="flex items-center justify-between px-6 py-3 border-b border-border">
        <div className="flex items-center gap-2">
          <Gauge className="h-5 w-5 text-accent" />
          <h1 className="text-base font-semibold text-foreground">超买超卖</h1>
        </div>
        <div className="flex items-center gap-2">
          <button
            className={BTN_GHOST}
            onClick={() => {
              statusQ.refetch()
              symbolsQ.refetch()
              signalsQ.refetch()
            }}
            title="刷新"
          >
            <RefreshCcw className="h-4 w-4" />
          </button>
        </div>
      </header>

      <div className="flex-1 overflow-auto px-6 py-4 space-y-4">
        {statusQ.isLoading || symbolsQ.isLoading ? (
          <div className="text-sm text-muted py-8 text-center">加载中...</div>
        ) : !status ? (
          <EmptyState icon={AlertTriangle} title="加载失败" hint={statusQ.error?.message ?? '未知错误'} />
        ) : (
          <>
            {/* 可折叠配置条 */}
            <div className="rounded border border-border bg-elevated/30">
              <button
                type="button"
                className="w-full flex items-center justify-between px-4 py-2.5 hover:bg-elevated/50"
                onClick={() => setConfigCollapsed((v) => !v)}
              >
                <div className="flex items-center gap-3 min-w-0">
                  <Gauge className="h-4 w-4 text-accent shrink-0" />
                  <span className="text-sm font-medium text-foreground">
                    超买超卖模块 · {moduleEnabled ? '已开启' : '未开启'}
                  </span>
                  <span className="text-[11px] text-muted shrink-0">
                    候选 {symbols.length} · 启用 {enabledCount} · 今日超买 {todayOverbought} · 超卖 {todayOversold}
                  </span>
                  {!moduleEnabled && (
                    <span className="text-[11px] text-warn shrink-0">（模块未开启，调度器不评估）</span>
                  )}
                </div>
                {configCollapsed ? (
                  <ChevronRight className="h-4 w-4 text-muted shrink-0" />
                ) : (
                  <ChevronDown className="h-4 w-4 text-muted shrink-0" />
                )}
              </button>
              {!configCollapsed && (
                <div className="border-t border-border px-4 py-3 space-y-3">
                  <ConfigSection status={status} />
                </div>
              )}
            </div>

            {/* 列表 + 详情 */}
            <div className="grid grid-cols-1 lg:grid-cols-[320px_1fr] gap-3">
              {/* 左侧标的列表 — 对齐持仓 T 列表样式 */}
              <div className="rounded border border-border bg-elevated/30 flex flex-col min-h-[560px]">
                <div className="p-2 border-b border-border space-y-2">
                  <div className="relative">
                    <Search className="absolute left-2 top-1/2 -translate-y-1/2 h-3.5 w-3.5 text-muted" />
                    <input
                      className={`${INPUT_CLS} pl-7`}
                      placeholder="代码 / 名称"
                      value={search}
                      onChange={(e) => setSearch(e.target.value)}
                    />
                  </div>
                  <div className="flex gap-1">
                    {([
                      ['all', '全部'],
                      ['signals', '有信号'],
                      ['enabled', '已启用'],
                    ] as const).map(([k, label]) => (
                      <button
                        key={k}
                        type="button"
                        className={`flex-1 h-7 rounded-btn text-[11px] ${
                          filter === k
                            ? 'bg-accent text-white'
                            : 'bg-elevated text-secondary hover:text-foreground'
                        }`}
                        onClick={() => setFilter(k)}
                      >
                        {label}
                      </button>
                    ))}
                  </div>
                  <div className="flex gap-1.5 items-center">
                    <button
                      type="button"
                      className={BTN_SM_PRIMARY + ' flex-1'}
                      onClick={() => setAddOpen(true)}
                      title="添加到超买超卖监控"
                    >
                      <Plus className="h-3.5 w-3.5" /> 添加标的
                    </button>
                  </div>
                </div>

                <div className="flex-1 overflow-auto max-h-[600px]">
                  {filteredSymbols.length === 0 ? (
                    <div className="px-3 py-8 text-center text-xs text-muted">
                      {symbols.length === 0
                        ? '暂无监控标的 — 点击下方添加标的代码'
                        : '无匹配标的'}
                    </div>
                  ) : (
                    <ul className="divide-y divide-border">
                      {filteredSymbols.map((s) => {
                        const sig = signalBySymbol.get(s.symbol)
                        const isSel = selectedSymbol === s.symbol
                        return (
                          <SymbolRow
                            key={s.symbol}
                            item={s}
                            signal={sig}
                            active={isSel}
                            onSelect={() => setSelectedSymbol(s.symbol)}
                            onToggleEnabled={() =>
                              toggleMut.mutate({ symbol: s.symbol, enabled: !s.enabled })
                            }
                            onRemove={() => {
                              deleteMut.mutate(s.symbol)
                              if (selectedSymbol === s.symbol) setSelectedSymbol('')
                            }}
                          />
                        )
                      })}
                    </ul>
                  )}
                </div>
              </div>

              {/* 右侧详情：个股阈值配置 + 日K + 分时 */}
              {selectedSymbol && selectedItem ? (
                <div className="flex flex-col gap-2 min-w-0">
                  <PerSymbolThresholdEditor
                    key={selectedItem.symbol}
                    item={selectedItem}
                    onSaved={() => {
                      qc.invalidateQueries({ queryKey: symbolsKey() })
                      qc.invalidateQueries({ queryKey: signalsKey() })
                    }}
                  />
                  <StockPanel
                    symbol={selectedSymbol}
                    showIntraday
                    intradayKind="real"
                    height={520}
                    className="min-w-0"
                    refetchIntervalMs={intradayRefetchMs}
                  />
                </div>
              ) : (
                <EmptyState
                  icon={Gauge}
                  title="点击左侧标的查看详情"
                  hint="选中后右侧展示个股阈值配置 + 日K + 分时图。"
                />
              )}
            </div>

            {/* 今日信号快照 */}
            {signals.length > 0 && (
              <section className="rounded border border-border p-3">
                <h3 className="text-sm font-medium text-foreground mb-2 flex items-center gap-1.5">
                  <Activity className="h-4 w-4 text-accent" />
                  实时信号 · 共 {signals.length} 条
                </h3>
                <div className="grid grid-cols-1 sm:grid-cols-2 lg:grid-cols-3 gap-2">
                  {signals
                    .slice()
                    .sort((a, b) => Math.abs(b.deviation_pct) - Math.abs(a.deviation_pct))
                    .map((sig) => (
                      <SignalCard
                        key={sig.symbol}
                        sig={sig}
                        selected={selectedSymbol === sig.symbol}
                        onClick={() => setSelectedSymbol(sig.symbol)}
                      />
                    ))}
                </div>
              </section>
            )}
          </>
        )}
      </div>
      <AddPositionDialog
        open={addOpen}
        onClose={() => setAddOpen(false)}
        title="添加超买超卖标的"
        hideHkInput
        onAdd={(symbol, name) => addMut.mutateAsync({ symbol, name })}
      />
    </div>
  )
}

// ── 列表行（对齐持仓 T 的 PositionRow 样式） ─────────────────────
function SymbolRow({
  item,
  signal,
  active,
  onSelect,
  onToggleEnabled,
  onRemove,
}: {
  item: FreeRealtimeOverboughtSymbol
  signal: FreeRealtimeOverboughtSignal | undefined
  active: boolean
  onSelect: () => void
  onToggleEnabled: () => void
  onRemove: () => void
}) {
  const sig = signal
  // 阈值展示：个股覆盖显示具体值，否则显示「默认」
  const thresholdText = (() => {
    const ob = item.overbought_pct
    const os_ = item.oversold_pct
    if (ob == null && os_ == null) return '默认阈值'
    const obText = ob != null ? `+${ob}%` : '默认'
    const osText = os_ != null ? `-${os_}%` : '默认'
    return `${obText} / ${osText}`
  })()
  return (
    <li
      className={`flex items-center gap-3 px-3 py-2 cursor-pointer hover:bg-elevated/50 ${
        active ? 'bg-accent/10' : ''
      }`}
      onClick={onSelect}
    >
      <button
        type="button"
        onClick={(e) => { e.stopPropagation(); onToggleEnabled() }}
        className={`shrink-0 inline-flex items-center h-6 px-2 rounded-full text-[11px] border ${
          item.enabled
            ? 'bg-bull/10 border-bull/30 text-bull'
            : 'bg-elevated border-border text-muted'
        }`}
        title={item.enabled
          ? '点击暂停本标的的超买超卖评估'
          : '点击启用本标的的超买超卖评估'}
      >
        {item.enabled ? '监控中' : '已暂停'}
      </button>
      <div className="flex-1 min-w-0">
        <div className="flex items-center gap-2 min-w-0">
          <span className="font-medium text-foreground truncate">{item.name || item.symbol}</span>
          {sig && (
            <span
              className={`shrink-0 inline-flex items-center gap-0.5 px-1.5 py-0.5 rounded text-[10px] ${
                sig.direction === 'overbought'
                  ? 'bg-bull/15 text-bull'
                  : 'bg-bear/15 text-bear'
              }`}
              title={`偏离 ${sig.deviation_pct.toFixed(2)}% · 阈值 ${sig.threshold_pct.toFixed(1)}%`}
            >
              {sig.direction === 'overbought' ? (
                <TrendingUp className="h-3 w-3" />
              ) : (
                <TrendingDown className="h-3 w-3" />
              )}
              {sig.deviation_pct > 0 ? '+' : ''}
              {sig.deviation_pct.toFixed(1)}%
            </span>
          )}
        </div>
        <div className="text-xs text-muted">
          {item.symbol} · {thresholdText}
        </div>
      </div>
      <button
        type="button"
        onClick={(e) => {
          e.stopPropagation()
          if (confirm(`移除 ${item.name || item.symbol}?`)) onRemove()
        }}
        className="h-7 w-7 grid place-items-center rounded-btn text-muted hover:bg-elevated hover:text-warn shrink-0"
        title="移除"
      >
        <Trash2 className="h-3.5 w-3.5" />
      </button>
      <ChevronRight className="h-4 w-4 text-muted shrink-0" />
    </li>
  )
}

// ── 个股阈值编辑（右侧详情顶部） ────────────────────────────────
function PerSymbolThresholdEditor({
  item,
  onSaved,
}: {
  item: FreeRealtimeOverboughtSymbol
  onSaved: () => void
}) {
  const [obText, setObText] = useState<string>(
    item.overbought_pct != null ? String(item.overbought_pct) : '',
  )
  const [osText, setOsText] = useState<string>(
    item.oversold_pct != null ? String(item.oversold_pct) : '',
  )

  const saveMut = useMutation({
    mutationFn: () =>
      freeRealtimeApi.overboughtSymbolSetThresholds(
        item.symbol,
        obText.trim() === '' ? null : parseFloat(obText),
        osText.trim() === '' ? null : parseFloat(osText),
      ),
    onSuccess: () => {
      toast(`已保存 ${item.symbol} 阈值`, 'success')
      onSaved()
    },
    onError: (e: Error) => toast(e.message, 'error'),
  })

  function validate(): boolean {
    for (const t of [obText, osText]) {
      if (t.trim() === '') continue
      const v = parseFloat(t)
      if (!(v > 0 && v <= 50)) return false
    }
    return true
  }

  return (
    <div className="rounded border border-border bg-elevated/30 p-3">
      <div className="flex flex-wrap items-end gap-3">
        <div className="min-w-0">
          <div className="text-xs text-muted mb-1">个股阈值（偏离 5 日线 %，留空跟随全局默认）</div>
          <div className="flex flex-wrap items-end gap-2">
            <label className="block">
              <span className="text-[11px] text-muted">超买 %</span>
              <input
                className={`${INPUT_CLS} w-24`}
                type="number"
                step="0.5"
                placeholder="默认"
                value={obText}
                onChange={(e) => setObText(e.target.value)}
              />
            </label>
            <label className="block">
              <span className="text-[11px] text-muted">超卖 %</span>
              <input
                className={`${INPUT_CLS} w-24`}
                type="number"
                step="0.5"
                placeholder="默认"
                value={osText}
                onChange={(e) => setOsText(e.target.value)}
              />
            </label>
            <button
              type="button"
              className={BTN_SM_PRIMARY}
              onClick={() => saveMut.mutate()}
              disabled={!validate() || saveMut.isPending}
            >
              保存阈值
            </button>
          </div>
        </div>
        <div className="text-[11px] text-muted ml-auto">
          {item.symbol} · {item.enabled ? '监控中' : '已暂停'}
        </div>
      </div>
    </div>
  )
}

function ConfigSection({ status }: { status: FreeRealtimeStatus }) {
  const qc = useQueryClient()
  const moduleOn = !!status.modules.overbought_oversold

  const toggleModuleMut = useMutation({
    mutationFn: (enabled: boolean) =>
      freeRealtimeApi.putConfig({ modules: { overbought_oversold: enabled } }),
    onSuccess: () => qc.invalidateQueries({ queryKey: statusKey() }),
  })

  const startMut = useMutation({
    mutationFn: freeRealtimeApi.start,
    onSuccess: () => qc.invalidateQueries({ queryKey: statusKey() }),
  })
  const stopMut = useMutation({
    mutationFn: freeRealtimeApi.stop,
    onSuccess: () => qc.invalidateQueries({ queryKey: statusKey() }),
  })

  return (
    <div className="grid grid-cols-1 lg:grid-cols-2 gap-3 text-sm">
      <div className="rounded border border-border p-2.5">
        <div className="text-xs text-muted mb-1.5">模块开关</div>
        <div className="flex items-center gap-2">
          <button
            type="button"
            className={moduleOn ? BTN_SM_PRIMARY : BTN_SM_SECONDARY}
            onClick={() => toggleModuleMut.mutate(!moduleOn)}
            disabled={toggleModuleMut.isPending}
          >
            {moduleOn ? (
              <>
                <Activity className="h-3 w-3" /> 已开启
              </>
            ) : (
              <>
                <Pause className="h-3 w-3" /> 已关闭
              </>
            )}
          </button>
          <span className="text-[11px] text-muted">
            关闭后调度器不再评估超买超卖，所有标的不再产生信号
          </span>
        </div>
      </div>

      <div className="rounded border border-border p-2.5">
        <div className="text-xs text-muted mb-1.5">调度器（统一控制实时行情采集）</div>
        <div className="flex items-center gap-2">
          {status.running ? (
            <button className={BTN_SECONDARY} onClick={() => stopMut.mutate()}>
              <Pause className="h-3.5 w-3.5" /> 停止采集
            </button>
          ) : (
            <button
              className={BTN_PRIMARY}
              onClick={() => startMut.mutate()}
              disabled={!status.enabled || startMut.isPending}
              title={!status.enabled ? '请先在 A 股免费实时行情页面打开全局开关' : '启动采集'}
            >
              <Play className="h-3.5 w-3.5" /> 启动采集
            </button>
          )}
          <span className="text-[11px] text-muted">
            {status.running ? `运行中 · 间隔 ${status.interval_sec}s · 来源 ${status.last_source || '-'}` : '已停止'}
            {!status.enabled && '（全局开关未开启）'}
          </span>
        </div>
      </div>
    </div>
  )
}

function SignalCard({
  sig,
  selected,
  onClick,
}: {
  sig: FreeRealtimeOverboughtSignal
  selected: boolean
  onClick: () => void
}) {
  const overbought = sig.direction === 'overbought'
  return (
    <button
      type="button"
      onClick={onClick}
      className={`text-left rounded border p-2 hover:bg-elevated/50 ${
        selected ? 'border-accent' : 'border-border'
      } ${overbought ? 'bg-bull/5' : 'bg-bear/5'}`}
    >
      <div className="flex items-center justify-between gap-2">
        <span className="text-xs font-mono text-foreground truncate">{sig.symbol}</span>
        <span
          className={`inline-flex items-center gap-0.5 px-1.5 py-0.5 rounded text-[10px] ${
            overbought ? 'bg-bull/15 text-bull' : 'bg-bear/15 text-bear'
          }`}
        >
          {overbought ? <TrendingUp className="h-3 w-3" /> : <TrendingDown className="h-3 w-3" />}
          {overbought ? '超买' : '超卖'}
        </span>
      </div>
      <div className="mt-1.5 grid grid-cols-2 gap-1 text-[11px]">
        <div>
          <span className="text-muted">现价 </span>
          <span className="font-mono">{sig.last_price.toFixed(2)}</span>
        </div>
        <div>
          <span className="text-muted">MA5 </span>
          <span className="font-mono">{sig.ma5.toFixed(2)}</span>
        </div>
        <div className="col-span-2">
          <span className="text-muted">偏离 </span>
          <span className={`font-mono ${overbought ? 'text-bull' : 'text-bear'}`}>
            {sig.deviation_pct > 0 ? '+' : ''}
            {sig.deviation_pct.toFixed(2)}%
          </span>
          <span className="text-muted"> / 阈值 {sig.threshold_pct.toFixed(1)}%</span>
        </div>
      </div>
    </button>
  )
}
