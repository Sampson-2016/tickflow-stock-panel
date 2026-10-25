// 免费实时行情配置 + 状态面板
import { useMutation, useQuery, useQueryClient } from '@tanstack/react-query'
import {
  AlertTriangle,
  Pause,
  Play,
  RefreshCcw,
  Trash2,
} from 'lucide-react'
import { useState } from 'react'
import { toast } from '@/components/Toast'
import {
  BTN_GHOST,
  BTN_PRIMARY,
  BTN_SECONDARY,
  BTN_SM_PRIMARY,
  BTN_SM_SECONDARY,
  freeRealtimeApi,
  INPUT_CLS,
  MODULE_LABELS,
  SOURCE_LABELS,
  type FreeRealtimeConfig,
} from './api'

const QK = ['free-realtime'] as const
const configKey = () => [...QK, 'config'] as const
const statusKey = () => [...QK, 'status'] as const

/** 标的来源（共享）：调度器/测试页循环每轮实际拉取的标的。
 *  与测试页共用 collect-symbols 接口；运行中 5s 轮询，停止时 30s 慢轮询兜底。 */
function SharedSymbolsSection() {
  const statusQ = useQuery({
    queryKey: statusKey(),
    queryFn: freeRealtimeApi.status,
    refetchInterval: 5000,
  })
  const running = statusQ.data?.running ?? false
  const collectQ = useQuery({
    queryKey: ['free-realtime-test', 'collect-symbols'],
    queryFn: freeRealtimeApi.testCollectSymbols,
    refetchInterval: running ? 5000 : 30000,
  })
  const items = collectQ.data?.items ?? []
  return (
    <section className="rounded border p-4">
      <h2 className="mb-2 text-lg font-medium">标的来源（共享）</h2>
      <p className="mb-3 text-sm text-muted-foreground">
        调度器与测试页循环每轮实际拉取的标的，来自「持仓T / 自选股 / 超买超卖」三个模块已启用标的的合集。
      </p>
      {items.length === 0 ? (
        <p className="text-sm text-amber-500">
          当前无已启用标的 — 请在「持仓T / 自选股 / 超买超卖」模块中开启至少一只。
        </p>
      ) : (
        <div className="flex flex-wrap gap-1.5">
          {items.map((it) => (
            <span
              key={it.symbol}
              className="inline-flex items-center gap-1.5 px-2 py-0.5 rounded text-[11px] bg-elevated border border-border"
              title={it.source ? `来源: ${MODULE_LABELS[it.source as keyof typeof MODULE_LABELS] ?? it.source}` : undefined}
            >
              {it.name && <span className="text-foreground">{it.name}</span>}
              <span className="font-mono text-muted">{it.symbol}</span>
              {it.source && (
                <span className="px-1 rounded bg-accent/10 text-accent">
                  {MODULE_LABELS[it.source as keyof typeof MODULE_LABELS] ?? it.source}
                </span>
              )}
            </span>
          ))}
        </div>
      )}
    </section>
  )
}

interface Draft {
  interval_sec: string
  retention_days: string
  sources: string[]
  trading_hours_only: boolean
  modules: { position_t: boolean; overbought_oversold: boolean; watchlist: boolean }
}

function toDraft(c: FreeRealtimeConfig): Draft {
  return {
    interval_sec: String(c.interval_sec),
    retention_days: String(c.retention_days),
    sources: [...c.sources],
    trading_hours_only: c.trading_hours_only,
    modules: { ...c.modules },
  }
}

export function FreeRealtimeConfigPage() {
  const qc = useQueryClient()
  const configQ = useQuery({
    queryKey: configKey(),
    queryFn: freeRealtimeApi.getConfig,
  })
  const statusQ = useQuery({
    queryKey: statusKey(),
    queryFn: freeRealtimeApi.status,
    refetchInterval: 5000,
  })
  const [draft, setDraft] = useState<Draft | null>(null)

  // 配置加载后初始化 draft
  if (configQ.data && !draft) setDraft(toDraft(configQ.data))

  const putCfg = useMutation({
    mutationFn: (patch: Record<string, unknown>) => freeRealtimeApi.putConfig(patch),
    onSuccess: () => {
      toast('已保存', 'success')
      qc.invalidateQueries({ queryKey: configKey() })
      qc.invalidateQueries({ queryKey: statusKey() })
    },
    onError: (e: Error) => toast(e.message, 'error'),
  })

  const startMut = useMutation({
    mutationFn: freeRealtimeApi.start,
    onSuccess: () => qc.invalidateQueries({ queryKey: statusKey() }),
    onError: (e: Error) => toast(e.message, 'error'),
  })
  const stopMut = useMutation({
    mutationFn: freeRealtimeApi.stop,
    onSuccess: () => qc.invalidateQueries({ queryKey: statusKey() }),
  })
  const pruneMut = useMutation({
    mutationFn: freeRealtimeApi.prune,
    onSuccess: (r) => toast(`已清理 ${r.removed_dirs} 个旧目录`, 'success'),
  })

  function toggleSource(s: string) {
    if (!draft) return
    const exists = draft.sources.includes(s)
    setDraft({
      ...draft,
      sources: exists ? draft.sources.filter((x) => x !== s) : [...draft.sources, s],
    })
  }

  function save() {
    if (!draft) return
    const interval = parseInt(draft.interval_sec, 10)
    if (!(interval >= 6 && interval <= 60)) {
      toast('轮询间隔必须在 6-60 秒', 'error')
      return
    }
    if (draft.sources.length === 0) {
      toast('至少勾选一个数据源', 'error')
      return
    }
    // 超买/超卖阈值已迁到「超买超卖」页面按个股配置，本页不再提交
    putCfg.mutate({
      interval_sec: interval,
      retention_days: parseInt(draft.retention_days, 10),
      sources: draft.sources,
      trading_hours_only: draft.trading_hours_only,
      modules: draft.modules,
    })
  }

  function toggleModule(m: keyof Draft['modules']) {
    if (!draft) return
    setDraft({
      ...draft,
      modules: { ...draft.modules, [m]: !draft.modules[m] },
    })
  }

  const status = statusQ.data
  const running = status?.running ?? false

  return (
    <div className="mx-auto max-w-4xl space-y-6 p-4">
      <header className="flex items-center justify-between">
        <div>
          <h1 className="text-xl font-semibold">A股免费实时行情</h1>
          <p className="text-sm text-muted-foreground">
            统一调度新浪 / 腾讯 / 东财免费接口；多源交替轮询，失败自动熔断。
          </p>
        </div>
        <div className="flex gap-2">
          {running ? (
            <button
              className={BTN_SECONDARY}
              onClick={() => stopMut.mutate()}
              disabled={stopMut.isPending}
            >
              <Pause className="size-4" /> 停止
            </button>
          ) : (
            <button
              className={BTN_PRIMARY}
              onClick={() => startMut.mutate()}
              disabled={startMut.isPending || !draft || !draft.sources.length}
            >
              <Play className="size-4" /> 启动
            </button>
          )}
          <button className={BTN_SECONDARY} onClick={() => pruneMut.mutate()}>
            <Trash2 className="size-4" /> 清理
          </button>
          <button
            className={BTN_GHOST}
            onClick={() => {
              configQ.refetch()
              statusQ.refetch()
            }}
          >
            <RefreshCcw className="size-4" />
          </button>
        </div>
      </header>

      {status?.last_error && (
        <div className="rounded border border-amber-400/40 bg-amber-500/10 p-3 text-sm">
          <AlertTriangle className="mr-1 inline size-4 text-amber-500" />
          最近错误：{status.last_error}
        </div>
      )}

      {/* 标的来源（共享）：调度器/测试页循环每轮实际拉取的标的，中文名+代码+来源模块 */}
      <SharedSymbolsSection />

      <section className="rounded border p-4">
        <h2 className="mb-3 text-lg font-medium">全局配置</h2>
        {!draft ? (
          <div className="text-muted-foreground">加载中…</div>
        ) : (
          <div className="space-y-4">
            <div className="flex items-center justify-between">
              <div>
                <div className="font-medium">全局开关</div>
                <div className="text-sm text-muted-foreground">
                  关闭后调度器停止轮询；三个模块不再拉取数据
                </div>
              </div>
              <input
                type="checkbox"
                checked={configQ.data?.enabled ?? false}
                onChange={(e) =>
                  putCfg.mutate({ enabled: e.target.checked })
                }
              />
            </div>

            <div className="grid grid-cols-2 gap-3">
              <label className="block">
                <span className="text-sm text-muted-foreground">轮询间隔 (秒)</span>
                <input
                  className={INPUT_CLS}
                  type="number"
                  min={6}
                  max={60}
                  value={draft.interval_sec}
                  onChange={(e) =>
                    setDraft({ ...draft, interval_sec: e.target.value })
                  }
                />
              </label>
              <label className="block">
                <span className="text-sm text-muted-foreground">落盘保留天数</span>
                <input
                  className={INPUT_CLS}
                  type="number"
                  min={1}
                  max={30}
                  value={draft.retention_days}
                  onChange={(e) =>
                    setDraft({ ...draft, retention_days: e.target.value })
                  }
                />
              </label>
            </div>

            <div>
              <span className="text-sm text-muted-foreground">数据源（多选 = 交替轮询）</span>
              <div className="mt-1 flex flex-wrap gap-2">
                {configQ.data?.available_sources.map((s) => (
                  <button
                    key={s}
                    type="button"
                    className={
                      draft.sources.includes(s) ? BTN_SM_PRIMARY : BTN_SM_SECONDARY
                    }
                    onClick={() => toggleSource(s)}
                  >
                    {SOURCE_LABELS[s] ?? s}
                  </button>
                ))}
              </div>
            </div>

            <label className="flex items-center gap-2">
              <input
                type="checkbox"
                checked={draft.trading_hours_only}
                onChange={(e) =>
                  setDraft({ ...draft, trading_hours_only: e.target.checked })
                }
              />
              <span>仅在 A 股交易时段拉取</span>
            </label>

            <div>
              <h3 className="mb-2 text-base font-medium">模块开关</h3>
              <div className="space-y-2">
                {(['position_t', 'overbought_oversold', 'watchlist'] as const).map((m) => (
                  <label key={m} className="flex items-center gap-2">
                    <input
                      type="checkbox"
                      checked={draft.modules[m]}
                      onChange={() => toggleModule(m)}
                    />
                    <span>{MODULE_LABELS[m]}</span>
                  </label>
                ))}
              </div>
            </div>

            {/* 超买/超卖阈值已迁到「超买超卖」页面按个股配置，本页不再展示 */}

            <div className="flex gap-2">
              <button className={BTN_PRIMARY} onClick={save} disabled={putCfg.isPending}>
                保存配置
              </button>
            </div>
          </div>
        )}
      </section>

      <section className="rounded border p-4">
        <h2 className="mb-3 text-lg font-medium">运行状态</h2>
        {status ? (
          <div className="grid grid-cols-2 gap-3 text-sm sm:grid-cols-4">
            <Stat label="运行状态" value={running ? '运行中' : '停止'} />
            <Stat label="最近源" value={status.last_source || '—'} />
            <Stat label="最近标的数" value={String(status.last_symbol_count)} />
            <Stat
              label="最近 tick"
              value={
                status.last_tick_ms
                  ? new Date(status.last_tick_ms).toLocaleTimeString('zh-CN', {
                      hour12: false,
                    })
                  : '—'
              }
            />
            <Stat
              label="熔断源"
              value={
                Object.keys(status.breaker_stats?.broken_until ?? {}).join(',') || '无'
              }
            />
            <Stat
              label="real 落盘文件"
              value={String(status.store_stats?.files?.real ?? 0)}
            />
            {/* mock 落盘文件不在此处展示 — 本页面用于正式开发，mock 相关展示由测试页承担 */}
          </div>
        ) : (
          <div className="text-muted-foreground">加载中…</div>
        )}
      </section>
    </div>
  )
}

function Stat({ label, value }: { label: string; value: string }) {
  return (
    <div className="rounded border p-2">
      <div className="text-xs text-muted-foreground">{label}</div>
      <div className="font-medium">{value}</div>
    </div>
  )
}
