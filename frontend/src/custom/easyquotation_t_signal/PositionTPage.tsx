import { useMemo, useState } from 'react'
import { useMutation, useQuery, useQueryClient } from '@tanstack/react-query'
import { useSearchParams } from 'react-router-dom'
import {
  Activity,
  AlertTriangle,
  Bell,
  BellRing,
  CheckCircle2,
  ChevronRight,
  Pause,
  Play,
  Plus,
  PowerOff,
  RefreshCcw,
  Settings2,
  Trash2,
  XCircle,
} from 'lucide-react'
import { EmptyState } from '@/components/EmptyState'
import { fmtPrice, priceColorClass } from '@/lib/format'
import { AddPositionDialog } from './AddPositionDialog'
import { IntradayTChart } from './IntradayTChart'
import {
  T_SIGNAL_KIND_LABELS,
  tSignalApi,
  type TSignalConfig,
  type TSignalItem,
  type TSignalStatus,
} from './api'

const QK = ['t-signal'] as const

const statusKey = () => [...QK, 'status'] as const
const configKey = () => [...QK, 'config'] as const
const positionsKey = () => [...QK, 'positions'] as const
const intradayKey = (sym: string) => [...QK, 'intraday', sym] as const
const positionSignalsKey = (sym: string) => [...QK, 'signals', sym] as const

function formatTime(tsMs: number): string {
  if (!tsMs) return '—'
  const d = new Date(tsMs)
  return d.toLocaleTimeString('zh-CN', {
    hour: '2-digit', minute: '2-digit', second: '2-digit', hour12: false,
  })
}
function relativeAge(tsMs: number): string {
  if (!tsMs) return '—'
  const delta = Math.max(0, Date.now() - tsMs)
  if (delta < 60_000) return `${Math.round(delta / 1000)}s 前`
  return `${Math.round(delta / 60_000)}m 前`
}

interface ConfigDraft {
  interval_sec: string
  price_change_pct_threshold: string
  volume_ratio_threshold: string
  big_order_imbalance_threshold: string
  cooldown_seconds: string
}
const toDraft = (c: TSignalConfig): ConfigDraft => ({
  interval_sec: String(c.interval_sec),
  price_change_pct_threshold: String(c.price_change_pct_threshold),
  volume_ratio_threshold: String(c.volume_ratio_threshold),
  big_order_imbalance_threshold: String(Math.round(c.big_order_imbalance_threshold * 100)),
  cooldown_seconds: String(c.cooldown_seconds),
})

function StatusPill({ status }: { status: TSignalStatus }) {
  if (!status.easyquotation_installed) {
    return (
      <span className="inline-flex items-center gap-1.5 px-2 py-0.5 rounded-full bg-elevated text-xs text-warn">
        <XCircle className="h-3.5 w-3.5" /> 未安装 easyquotation
      </span>
    )
  }
  if (!status.feishu_webhook_configured) {
    return (
      <span className="inline-flex items-center gap-1.5 px-2 py-0.5 rounded-full bg-elevated text-xs text-warn">
        <Bell className="h-3.5 w-3.5" /> 飞书 webhook 未配置
      </span>
    )
  }
  if (status.running) {
    return (
      <span className="inline-flex items-center gap-1.5 px-2 py-0.5 rounded-full bg-bull/10 text-xs text-bull">
        <Activity className="h-3.5 w-3.5" /> 运行中
      </span>
    )
  }
  return (
    <span className="inline-flex items-center gap-1.5 px-2 py-0.5 rounded-full bg-elevated text-xs text-muted">
      <Pause className="h-3.5 w-3.5" /> 已停止
    </span>
  )
}

function StatusRow({ label, value, ok = true }: { label: string; value: string; ok?: boolean }) {
  return (
    <div className="flex items-center justify-between py-1.5 text-sm">
      <span className="text-muted">{label}</span>
      <span className={ok ? 'text-foreground' : 'text-warn'}>{value}</span>
    </div>
  )
}

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
      interval_sec: Number(draft.interval_sec),
      price_change_pct_threshold: Number(draft.price_change_pct_threshold),
      volume_ratio_threshold: Number(draft.volume_ratio_threshold),
      big_order_imbalance_threshold: Number(draft.big_order_imbalance_threshold) / 100,
      cooldown_seconds: Number(draft.cooldown_seconds),
    })
  }
  return (
    <div className="bg-surface border border-border rounded-card p-4">
      <div className="flex items-center gap-2 mb-3">
        <Settings2 className="h-4 w-4 text-accent" />
        <h2 className="text-sm font-medium text-foreground">阈值配置</h2>
      </div>
      <div className="grid grid-cols-1 md:grid-cols-2 gap-3">
        <label className="block">
          <span className="text-xs text-muted">拉取间隔 (秒)</span>
          <input
            type="number" step="1" min="1"
            value={draft.interval_sec}
            onChange={(e) => setDraft({ ...draft, interval_sec: e.target.value })}
            className="mt-1 w-full bg-elevated border border-border rounded-btn px-2 py-1.5 text-sm text-foreground focus:outline-none focus:ring-1 focus:ring-accent"
          />
          <span className="text-[11px] text-muted mt-1 block">
            同时影响: 后端拉取频率 / 日内 K 点密度 / 信号检测延迟
          </span>
        </label>
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

function ControlCard({ status }: { status: TSignalStatus }) {
  const qc = useQueryClient()
  const invalidateAll = () => {
    void qc.invalidateQueries({ queryKey: statusKey() })
    void qc.invalidateQueries({ queryKey: positionsKey() })
  }
  const start = useMutation({ mutationFn: tSignalApi.start, onSuccess: invalidateAll })
  const stop = useMutation({ mutationFn: tSignalApi.stop, onSuccess: invalidateAll })
  const missingDeps = !status.easyquotation_installed
  const blockedReason = !status.enabled_position_count
    ? '请先添加持仓 T 标的并启用'
    : missingDeps ? '未安装 easyquotation' : null
  return (
    <div className="bg-surface border border-border rounded-card p-4">
      <div className="flex items-center justify-between mb-3">
        <div className="flex items-center gap-2">
          <BellRing className="h-4 w-4 text-accent" />
          <h2 className="text-sm font-medium text-foreground">全局轮询</h2>
        </div>
        <StatusPill status={status} />
      </div>
      <p className="text-[11px] text-muted mb-3 -mt-1">
        控制后台拉取线程; 启停后持仓 T 列表设置保留。
      </p>
      <StatusRow label="轮询间隔" value={`${status.interval_sec}s`} ok={status.interval_sec > 0} />
      <StatusRow
        label="最后拉取"
        value={status.last_fetch_ms ? `${relativeAge(status.last_fetch_ms)} (${formatTime(status.last_fetch_ms)})` : '尚未拉取'}
        ok={!status.last_fetch_error}
      />
      {status.last_fetch_error && (
        <div className="mt-1 text-xs text-warn flex items-start gap-1.5">
          <AlertTriangle className="h-3.5 w-3.5 mt-0.5 shrink-0" />
          <span className="break-all">{status.last_fetch_error}</span>
        </div>
      )}
      <StatusRow
        label="持仓 T 标的"
        value={`${status.enabled_position_count} / ${status.position_count} 已启用`}
        ok={status.enabled_position_count > 0}
      />
      <StatusRow label="今日信号" value={String(status.signals_today)} />
      <StatusRow
        label="飞书 webhook"
        value={status.feishu_webhook_configured ? '已配置' : '未配置 (设置页填写)'}
        ok={status.feishu_webhook_configured}
      />
      <div className="mt-3 flex items-center gap-2">
        {status.running ? (
          <button
            type="button" onClick={() => stop.mutate()}
            disabled={stop.isPending}
            className="inline-flex items-center h-8 px-3 rounded-btn bg-elevated text-xs text-secondary hover:text-foreground disabled:opacity-50"
          >
            <PowerOff className="h-3.5 w-3.5 mr-1" /> 停止
          </button>
        ) : (
          <button
            type="button" onClick={() => start.mutate()}
            disabled={start.isPending || !status.enabled_position_count || missingDeps}
            className="inline-flex items-center h-8 px-3 rounded-btn bg-accent text-xs text-white hover:bg-accent/90 disabled:opacity-50"
          >
            <Play className="h-3.5 w-3.5 mr-1" /> {start.isPending ? '启动中...' : '启动'}
          </button>
        )}
        <button
          type="button" onClick={invalidateAll}
          className="inline-flex items-center h-8 px-3 rounded-btn bg-elevated text-xs text-secondary hover:text-foreground"
        >
          <RefreshCcw className="h-3.5 w-3.5 mr-1" /> 刷新
        </button>
      </div>
      {blockedReason && !status.running && (
        <p className="mt-2 text-[11px] text-muted">{blockedReason}</p>
      )}
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

function SignalRow({ item }: { item: TSignalItem }) {
  const pct = item.change_pct
  const cls = priceColorClass(pct / 100)
  return (
    <div className="px-3 py-2 border-b border-border last:border-b-0">
      <div className="flex items-center justify-between gap-3">
        <div className="flex items-center gap-2 min-w-0">
          <span className="text-xs text-muted tabular-nums shrink-0">{formatTime(item.ts_ms)}</span>
          <span className="text-xs text-muted shrink-0">{T_SIGNAL_KIND_LABELS[item.kind]}</span>
        </div>
        <div className="flex items-center gap-3 shrink-0">
          <span className={`tabular-nums text-sm font-medium ${cls}`}>
            {pct > 0 ? '+' : ''}{pct.toFixed(2)}%
          </span>
          <span className="tabular-nums text-sm text-foreground">{fmtPrice(item.price)}</span>
        </div>
      </div>
      <p className="mt-1 text-xs text-secondary break-all">{item.detail}</p>
    </div>
  )
}

function DetailPanel({
  symbol,
  dataStatus,
  onClose,
}: {
  symbol: string
  dataStatus: 'ok' | 'failed' | 'never'
  onClose: () => void
}) {
  // 复用 Page 顶层 statusQ 的缓存 (同 key 自动共享), 避免重复请求
  const statusQ = useQuery({ queryKey: statusKey(), queryFn: tSignalApi.status })
  const isRunning = !!statusQ.data?.running

  const intradayQ = useQuery({
    queryKey: intradayKey(symbol),
    queryFn: () => tSignalApi.intraday(symbol),
    // 后端停止时不会有新点, 拉也拉不到 — 停止轮询避免刷空数据
    refetchInterval: isRunning ? 5000 : false,
    enabled: !!symbol,
  })
  const signalsQ = useQuery({
    queryKey: positionSignalsKey(symbol),
    queryFn: () => tSignalApi.positionSignals(symbol, 200),
    refetchInterval: isRunning ? 5000 : false,
    enabled: !!symbol,
  })
  // 用最新 signal 的 prev_close 作为昨收基准线
  const prevClose = useMemo(() => {
    const items = signalsQ.data?.items ?? []
    return items.length > 0 ? items[items.length - 1].prev_close : undefined
  }, [signalsQ.data])

  const items = signalsQ.data?.items ?? []
  const bars = intradayQ.data?.bars ?? []
  const lastBar = bars.length > 0 ? bars[bars.length - 1] : undefined

  return (
    <div className="bg-surface border border-border rounded-card overflow-hidden">
      <div className="flex items-center justify-between px-4 py-3 border-b border-border">
        <div className="flex items-center gap-2 min-w-0">
          <h3 className="text-sm font-medium text-foreground truncate">{symbol}</h3>
          {lastBar && (
            <span className="text-xs text-muted">
              最新 {lastBar.price.toFixed(2)} · {formatTime(lastBar.ts_ms)}
            </span>
          )}
        </div>
        <button
          type="button"
          onClick={onClose}
          className="h-7 w-7 grid place-items-center rounded-btn text-muted hover:bg-elevated hover:text-foreground"
        >
          <XCircle className="h-4 w-4" />
        </button>
      </div>

      <div className="p-4">
        <IntradayTChart bars={bars} signals={items} prevClose={prevClose} height={340} dataStatus={dataStatus} />
      </div>

      <div className="border-t border-border">
        <div className="px-4 py-2 flex items-center justify-between">
          <h4 className="text-xs font-medium text-muted">当日信号 ({items.length})</h4>
          {items.length > 0 && (
            <span className="text-[11px] text-muted">点位已在图上标注</span>
          )}
        </div>
        {items.length === 0 ? (
          <div className="px-4 py-6 text-center text-xs text-muted">
            暂无信号 — 启用监控后命中规则会自动出现
          </div>
        ) : (
          <div className="max-h-[280px] overflow-auto">
            {items.map((it, idx) => (
              <SignalRow key={`${it.ts_ms}-${it.kind}-${idx}`} item={it} />
            ))}
          </div>
        )}
      </div>
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

  const statusQ = useQuery({
    queryKey: statusKey(),
    queryFn: tSignalApi.status,
    refetchInterval: (q) => (q.state.data?.running ? 5000 : 15000),
  })
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
    onSuccess: () => {
      void qc.invalidateQueries({ queryKey: positionsKey() })
      // 如果删除的是当前选中的, 清除 ?symbol=
      if (selectedSymbol) setSelectedSymbol('')
    },
  })

  const status = statusQ.data
  const config = configQ.data
  const positions = positionsQ.data?.items ?? []

  // 选中标的如果不在列表里 (被删了), 自动清空
  if (selectedSymbol && !positions.some((p) => p.symbol === selectedSymbol)) {
    setSelectedSymbol('')
  }

  return (
    <div className="h-full flex flex-col">
      <header className="px-6 py-4 border-b border-border bg-surface/50">
        <div className="flex items-center gap-2">
          <BellRing className="h-5 w-5 text-accent" />
          <h1 className="text-lg font-medium text-foreground">持仓 T</h1>
        </div>
        <p className="mt-1 text-xs text-secondary">
          主动维护「要做 T 的持仓」列表 — 后端每 {status?.interval_sec ?? 3}s 拉一次行情, 命中规则即推飞书 + 落库。
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
            <div className="grid grid-cols-1 lg:grid-cols-2 gap-4">
              <ControlCard status={status} />
              <ConfigCard config={config} />
            </div>

            <div className="grid grid-cols-1 lg:grid-cols-[360px_1fr] gap-4">
              {/* 持仓 T 列表 */}
              <div className="bg-surface border border-border rounded-card">
                <div className="flex items-center justify-between px-4 py-2 border-b border-border">
                  <h2 className="text-sm font-medium text-foreground">
                    持仓 T 列表 ({positions.length})
                    <span className="text-[10px] text-muted ml-1">· 每只独立启停</span>
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
                  <div>
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

              {/* 单票详情 */}
              {selectedSymbol ? (
                <DetailPanel
                  symbol={selectedSymbol}
                  dataStatus={positions.find((p) => p.symbol === selectedSymbol)?.data_status ?? 'never'}
                  onClose={() => setSelectedSymbol('')}
                />
              ) : (
                <EmptyState
                  icon={CheckCircle2}
                  title="点击左侧标的查看详情"
                  hint="详情页展示当日分时图与历史信号点位 — 刷新页面或重新进入会保留历史。"
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