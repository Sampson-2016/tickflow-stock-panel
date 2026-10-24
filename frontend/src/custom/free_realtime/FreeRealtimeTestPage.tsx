// 免费实时行情测试页 - mock 循环 + 真实循环 + 一键清空
import { useMutation, useQuery, useQueryClient } from '@tanstack/react-query'
import {
  Pause,
  Play,
  RefreshCcw,
  Trash2,
} from 'lucide-react'
import { useState } from 'react'
import { toast } from '@/components/Toast'
import {
  ACCELERATIONS,
  BTN_GHOST,
  BTN_PRIMARY,
  BTN_SECONDARY,
  BTN_SM_PRIMARY,
  BTN_SM_SECONDARY,
  freeRealtimeApi,
  INPUT_CLS,
  type FreeRealtimeTestStatus,
} from './api'
import { IntradayChart } from './IntradayChart'

const QK = ['free-realtime-test'] as const
const mockKey = () => [...QK, 'mock-config'] as const
const statusKey = () => [...QK, 'status'] as const

interface MockDraft {
  symbols: string
  base_prices: string
  names: string
  volatility_pct: string
  fail_rate: string
  seed: string
  acceleration: number
}

function parseSymbols(s: string): string[] {
  return s
    .split(/[\s,;]+/)
    .map((x) => x.trim().toUpperCase())
    .filter(Boolean)
}

function parseMap(s: string): Record<string, number> | null {
  const out: Record<string, number> = {}
  for (const part of s.split(/[\s,;]+/).filter(Boolean)) {
    const [k, v] = part.split('=')
    if (!k || !v) return null
    out[k.trim().toUpperCase()] = parseFloat(v)
  }
  return out
}

function parseNameMap(s: string): Record<string, string> {
  const out: Record<string, string> = {}
  for (const part of s.split(/[\s,;]+/).filter(Boolean)) {
    const [k, v] = part.split('=')
    if (k && v) out[k.trim().toUpperCase()] = v
  }
  return out
}

export function FreeRealtimeTestPage() {
  const qc = useQueryClient()
  const mockQ = useQuery({
    queryKey: mockKey(),
    queryFn: freeRealtimeApi.testMockConfig,
  })
  const statusQ = useQuery({
    queryKey: statusKey(),
    queryFn: freeRealtimeApi.testStatus,
    refetchInterval: 2000,
  })
  const [draft, setDraft] = useState<MockDraft | null>(null)
  const [previewSymbol, setPreviewSymbol] = useState('600000.SH')
  const [previewKind, setPreviewKind] = useState<'real' | 'mock'>('mock')
  const [kindAutoSelected, setKindAutoSelected] = useState(false)

  // 首次拿到 status 后，根据运行状态自动选 kind（仅一次，不覆盖用户后续手动切换）
  if (statusQ.data && !kindAutoSelected) {
    setKindAutoSelected(true)
    if (statusQ.data.mock_running) {
      setPreviewKind('mock')
    } else if (statusQ.data.real_loop_running) {
      setPreviewKind('real')
    }
  }

  if (mockQ.data && !draft) {
    const c = mockQ.data.config
    setDraft({
      symbols: (c.symbols || []).join(', '),
      base_prices: Object.entries(c.base_prices || {})
        .map(([k, v]) => `${k}=${v}`)
        .join(', '),
      names: Object.entries(c.names || {})
        .map(([k, v]) => `${k}=${v}`)
        .join(', '),
      volatility_pct: String(c.volatility_pct ?? 0.01),
      fail_rate: String(c.fail_rate ?? 0.0),
      seed: String(c.seed ?? 42),
      acceleration: c.acceleration ?? 1,
    })
  }

  const putMock = useMutation({
    mutationFn: (patch: Record<string, unknown>) =>
      freeRealtimeApi.testMockPutConfig(patch),
    onSuccess: () => {
      toast('mock 配置已保存', 'success')
      qc.invalidateQueries({ queryKey: mockKey() })
    },
    onError: (e: Error) => toast(e.message, 'error'),
  })

  const startMock = useMutation({
    mutationFn: freeRealtimeApi.testMockStart,
    onSuccess: () => qc.invalidateQueries({ queryKey: statusKey() }),
  })
  const stopMock = useMutation({
    mutationFn: freeRealtimeApi.testMockStop,
    onSuccess: () => qc.invalidateQueries({ queryKey: statusKey() }),
  })
  const startReal = useMutation({
    mutationFn: freeRealtimeApi.testRealStart,
    onSuccess: () => qc.invalidateQueries({ queryKey: statusKey() }),
  })
  const stopReal = useMutation({
    mutationFn: freeRealtimeApi.testRealStop,
    onSuccess: () => qc.invalidateQueries({ queryKey: statusKey() }),
  })
  const clearMut = useMutation({
    mutationFn: (kind: 'real' | 'mock') => freeRealtimeApi.testClear(kind),
    onSuccess: (r) => toast(`已清空 ${r.kind} 落盘 (${r.removed} 项)`, 'success'),
  })

  function saveMock() {
    if (!draft) return
    const symbols = parseSymbols(draft.symbols)
    if (symbols.length === 0) {
      toast('symbols 不能为空', 'error')
      return
    }
    const base_prices = parseMap(draft.base_prices)
    if (base_prices === null) {
      toast('base_prices 格式错误，应为 SYMBOL=价格', 'error')
      return
    }
    const names = parseNameMap(draft.names)
    putMock.mutate({
      symbols,
      base_prices,
      names,
      volatility_pct: parseFloat(draft.volatility_pct),
      fail_rate: parseFloat(draft.fail_rate),
      seed: parseInt(draft.seed, 10),
      acceleration: draft.acceleration,
    })
  }

  const status: FreeRealtimeTestStatus | undefined = statusQ.data

  return (
    <div className="mx-auto max-w-5xl space-y-6 p-4">
      <header>
        <h1 className="text-xl font-semibold">免费实时行情测试页</h1>
        <p className="text-sm text-muted-foreground">
          Mock 模式绕过交易时段、平缓随机游走、失败注入、加速倍率；mock 与真实落盘目录隔离。
        </p>
      </header>

      <section className="rounded border p-4">
        <h2 className="mb-3 text-lg font-medium">Mock 配置</h2>
        {!draft ? (
          <div className="text-muted-foreground">加载中…</div>
        ) : (
          <div className="space-y-3">
            <div className="grid grid-cols-1 gap-3 sm:grid-cols-2">
              <label className="block">
                <span className="text-sm text-muted-foreground">symbols (逗号分隔)</span>
                <input
                  className={INPUT_CLS}
                  value={draft.symbols}
                  onChange={(e) => setDraft({ ...draft, symbols: e.target.value })}
                />
              </label>
              <label className="block">
                <span className="text-sm text-muted-foreground">seed</span>
                <input
                  className={INPUT_CLS}
                  type="number"
                  value={draft.seed}
                  onChange={(e) => setDraft({ ...draft, seed: e.target.value })}
                />
              </label>
              <label className="block">
                <span className="text-sm text-muted-foreground">base_prices (SYM=价)</span>
                <input
                  className={INPUT_CLS}
                  value={draft.base_prices}
                  onChange={(e) => setDraft({ ...draft, base_prices: e.target.value })}
                />
              </label>
              <label className="block">
                <span className="text-sm text-muted-foreground">names (SYM=名称)</span>
                <input
                  className={INPUT_CLS}
                  value={draft.names}
                  onChange={(e) => setDraft({ ...draft, names: e.target.value })}
                />
              </label>
              <label className="block">
                <span className="text-sm text-muted-foreground">volatility_pct (0~0.2)</span>
                <input
                  className={INPUT_CLS}
                  type="number"
                  step="0.005"
                  value={draft.volatility_pct}
                  onChange={(e) => setDraft({ ...draft, volatility_pct: e.target.value })}
                />
              </label>
              <label className="block">
                <span className="text-sm text-muted-foreground">fail_rate (0~1)</span>
                <input
                  className={INPUT_CLS}
                  type="number"
                  step="0.05"
                  value={draft.fail_rate}
                  onChange={(e) => setDraft({ ...draft, fail_rate: e.target.value })}
                />
              </label>
            </div>

            <div>
              <span className="text-sm text-muted-foreground">加速倍率</span>
              <div className="mt-1 flex flex-wrap gap-2">
                {ACCELERATIONS.map((a) => (
                  <button
                    key={a}
                    type="button"
                    className={
                      draft.acceleration === a ? BTN_SM_PRIMARY : BTN_SM_SECONDARY
                    }
                    onClick={() => setDraft({ ...draft, acceleration: a })}
                  >
                    {a}x
                  </button>
                ))}
              </div>
            </div>

            <div className="flex flex-wrap gap-2">
              <button className={BTN_PRIMARY} onClick={saveMock} disabled={putMock.isPending}>
                保存 mock 配置
              </button>
              {status?.mock_running ? (
                <button className={BTN_SECONDARY} onClick={() => stopMock.mutate()}>
                  <Pause className="size-4" /> 停止 mock
                </button>
              ) : (
                <button
                  className={BTN_PRIMARY}
                  onClick={() => startMock.mutate()}
                  disabled={startMock.isPending}
                >
                  <Play className="size-4" /> 启动 mock
                </button>
              )}
              <button
                className={BTN_SECONDARY}
                onClick={() => clearMut.mutate('mock')}
              >
                <Trash2 className="size-4" /> 清空 mock 落盘
              </button>
            </div>
          </div>
        )}
      </section>

      <section className="rounded border p-4">
        <h2 className="mb-3 text-lg font-medium">真实循环查询</h2>
        <p className="mb-3 text-sm text-muted-foreground">
          绕过全局开关与交易时段，按配置的间隔轮询勾选源（仅测试用）。
        </p>
        <div className="flex flex-wrap gap-2">
          {status?.real_loop_running ? (
            <button className={BTN_SECONDARY} onClick={() => stopReal.mutate()}>
              <Pause className="size-4" /> 停止真实循环
            </button>
          ) : (
            <button className={BTN_PRIMARY} onClick={() => startReal.mutate()}>
              <Play className="size-4" /> 启动真实循环
            </button>
          )}
          <button
            className={BTN_SECONDARY}
            onClick={() => clearMut.mutate('real')}
          >
            <Trash2 className="size-4" /> 清空 real 落盘
          </button>
          <button
            className={BTN_GHOST}
            onClick={() => {
              mockQ.refetch()
              statusQ.refetch()
            }}
          >
            <RefreshCcw className="size-4" />
          </button>
        </div>
      </section>

      <section className="rounded border p-4">
        <h2 className="mb-3 text-lg font-medium">运行状态</h2>
        <div className="grid grid-cols-2 gap-3 text-sm sm:grid-cols-4">
          <Stat label="mock 循环" value={status?.mock_running ? '运行中' : '停止'} />
          <Stat
            label="real 循环"
            value={status?.real_loop_running ? '运行中' : '停止'}
          />
          <Stat label="累计 tick" value={String(status?.tick_count ?? 0)} />
          <Stat
            label="最近 tick"
            value={
              status?.last_tick_ms
                ? new Date(status.last_tick_ms).toLocaleTimeString('zh-CN', {
                    hour12: false,
                  })
                : '—'
            }
          />
        </div>
      </section>

      <section className="rounded border p-4">
        <h2 className="mb-3 text-lg font-medium">分时数据预览</h2>
        <div className="mb-3 flex flex-wrap items-end gap-2">
          <label className="block">
            <span className="text-sm text-muted-foreground">标的代码</span>
            <input
              className={INPUT_CLS}
              value={previewSymbol}
              onChange={(e) => setPreviewSymbol(e.target.value)}
              placeholder="如 600000.SH / 300308.SZ"
            />
          </label>
          <div className="flex gap-2">
            {(['real', 'mock'] as const).map((k) => (
              <button
                key={k}
                type="button"
                className={previewKind === k ? BTN_SM_PRIMARY : BTN_SM_SECONDARY}
                onClick={() => setPreviewKind(k)}
              >
                {k === 'real' ? '真实落盘' : 'mock 落盘'}
              </button>
            ))}
          </div>
        </div>
        <IntradayChart
          symbol={previewSymbol.trim().toUpperCase()}
          kind={previewKind}
        />
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
