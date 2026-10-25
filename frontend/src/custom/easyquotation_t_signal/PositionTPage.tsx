import { useMemo, useState } from 'react'
import { useMutation, useQuery, useQueryClient } from '@tanstack/react-query'
import { useSearchParams } from 'react-router-dom'
import {
  Activity,
  AlertTriangle,
  Bell,
  BellRing,
  ChevronDown,
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
import { StockPanel } from '@/components/StockPanel'
import { api } from '@/lib/api'
import { AddPositionDialog } from './AddPositionDialog'
import {
  tSignalApi,
  type TSignalConfig,
  type TSignalStatus,
} from './api'

const QK = ['t-signal'] as const

const statusKey = () => [...QK, 'status'] as const
const configKey = () => [...QK, 'config'] as const
const positionsKey = () => [...QK, 'positions'] as const

function relativeAge(tsMs: number): string {
  if (!tsMs) return '—'
  const delta = Math.max(0, Date.now() - tsMs)
  if (delta < 60_000) return `${Math.round(delta / 1000)}s 前`
  return `${Math.round(delta / 60_000)}m 前`
}

function formatTime(tsMs: number): string {
  if (!tsMs) return '—'
  const d = new Date(tsMs)
  return d.toLocaleTimeString('zh-CN', {
    hour: '2-digit', minute: '2-digit', second: '2-digit', hour12: false,
  })
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
  const blockedReason = !status.enabled_position_count
    ? '请先添加持仓 T 标的并启用'
    : null
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
            disabled={start.isPending || !status.enabled_position_count}
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
  const [dataSource, setDataSource] = useState<'real' | 'mock'>('real')

  const statusQ = useQuery({
    queryKey: statusKey(),
    queryFn: tSignalApi.status,
    refetchInterval: (q) => (q.state.data?.running ? 5000 : 15000),
  })
  // 免费实时行情测试页状态: mock/真实循环运行时, 持仓T 详情分时图自动持续刷新
  // 运行时 3s 高频；停止时 15s 低频仅用于感知启动
  const frTestStatusQ = useQuery({
    queryKey: ['free-realtime-test', 'status'] as const,
    queryFn: api.freeRealtimeTestStatus,
    refetchInterval: (q) => {
      const d = q.state.data
      return d && (d.mock_running || d.real_loop_running) ? 3000 : 15000
    },
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
  const frTestRunning = !!(frTestStatusQ.data?.mock_running || frTestStatusQ.data?.real_loop_running)
  // 分时图轮询节奏：调度器或测试页循环运行时按配置 interval_sec 轮询（与抓取节奏对齐，避免高频拉前端）
  // 停止时返回 false：不轮询，无新数据
  const intradayRefetchMs = (status?.running || frTestRunning)
    ? Math.max(1000, (status?.interval_sec ?? 3) * 1000)
    : false
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
                onClick={() => setDataSource(k)}
              >
                {k === 'real' ? '真实' : 'Mock'}
              </button>
            ))}
          </div>
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
            {/* 可折叠配置区：折叠后只显示状态条 */}
            <div className="bg-surface border border-border rounded-card overflow-hidden">
              <button
                type="button"
                onClick={() => setConfigCollapsed((v) => !v)}
                className="w-full flex items-center justify-between px-4 py-2.5 hover:bg-elevated/50"
              >
                <div className="flex items-center gap-3 min-w-0">
                  <BellRing className="h-4 w-4 text-accent shrink-0" />
                  <span className="text-sm font-medium text-foreground">
                    全局轮询 · {status.running ? '运行中' : '已停止'}
                  </span>
                  <span className="text-[11px] text-muted shrink-0">
                    间隔 {status.interval_sec}s · {status.enabled_position_count}/{status.position_count} 启用 · 今日信号 {status.signals_today}
                  </span>
                  {status.last_fetch_error && (
                    <span className="text-[11px] text-warn shrink-0 truncate" title={status.last_fetch_error}>
                      · 上轮失败
                    </span>
                  )}
                </div>
                <ChevronDown
                  className={`h-4 w-4 text-muted shrink-0 transition-transform ${configCollapsed ? '' : 'rotate-180'}`}
                />
              </button>
              {!configCollapsed && (
                <div className="border-t border-border">
                  <div className="grid grid-cols-1 lg:grid-cols-2 gap-0">
                    <ControlCard status={status} />
                    <ConfigCard config={config} />
                  </div>
                </div>
              )}
            </div>

            {/* 列表 + 详情：右侧整体复用 StockPanel（日K+分时） */}
            <div className="grid grid-cols-1 lg:grid-cols-[300px_1fr] gap-4 flex-1 min-h-0">
              {/* 持仓 T 列表 */}
              <div className="bg-surface border border-border rounded-card flex flex-col overflow-hidden">
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