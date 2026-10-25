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
  type FreeRealtimeTestStatus,
} from './api'
import { StockIntradayChart } from '@/components/StockIntradayChart'

const QK = ['free-realtime-test'] as const
const statusKey = () => [...QK, 'status'] as const
const collectKey = () => [...QK, 'collect-symbols'] as const
// mock 虚拟时钟起点白名单（与后端 _ALLOWED_START_TIMES 一致）
const START_TIMES = ['09:15', '09:30', '10:30', '11:00', '13:00', '14:00'] as const

export function FreeRealtimeTestPage() {
  const qc = useQueryClient()
  const statusQ = useQuery({
    queryKey: statusKey(),
    queryFn: freeRealtimeApi.testStatus,
    // 运行时高频；停止时低频（10s）仅用于感知用户何时启动循环
    refetchInterval: (q) => {
      const d = q.state.data
      return d && (d.mock_running || d.real_loop_running) ? 2000 : 10000
    },
  })
  // 当前 mock/真实循环会拉的标的（来自三个模块 collector）
  // 仅在运行时轮询；停止时不轮询，避免无谓请求
  const collectQ = useQuery({
    queryKey: collectKey(),
    queryFn: freeRealtimeApi.testCollectSymbols,
    refetchInterval: () => {
      const s = statusQ.data
      return s && (s.mock_running || s.real_loop_running) ? 5000 : false
    },
  })
  const [acceleration, setAcceleration] = useState<number>(1)
  const [startTime, setStartTime] = useState<string>('09:15')
  const [previewSymbol, setPreviewSymbol] = useState('')

  // StockIntradayChart 的 tick 路径需要 date 才会激活；测试页传「今天」即可
  const todayStr = (() => {
    const d = new Date()
    return `${d.getFullYear()}-${String(d.getMonth() + 1).padStart(2, '0')}-${String(d.getDate()).padStart(2, '0')}`
  })()

  // 首次拿到 collect 结果，自动填预览标的（方便用户立刻看到图）
  if (collectQ.data && !previewSymbol && collectQ.data.symbols.length > 0) {
    setPreviewSymbol(collectQ.data.symbols[0])
  }

  const putMock = useMutation({
    mutationFn: (patch: Record<string, unknown>) =>
      freeRealtimeApi.testMockPutConfig(patch),
    onSuccess: () => toast('加速倍率已保存', 'success'),
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
    onSuccess: (r) => {
      toast(`已清空 ${r.kind} 落盘 (${r.removed} 项)`, 'success')
      // 刷新状态（tick_count/last_tick_ms 归零）+ 分时预览（曲线清空）
      // 不能只 invalidate statusKey，分时图用的是 StockIntradayChart 的独立 query
      // （['free-realtime', 'intraday', symbol, kind]），必须用模糊匹配清掉所有 intraday 缓存
      qc.invalidateQueries({ queryKey: statusKey() })
      qc.invalidateQueries({ queryKey: [...QK, 'intraday'], exact: false })
    },
  })

  function changeAcceleration(a: number) {
    setAcceleration(a)
    putMock.mutate({ acceleration: a })
  }
  function changeStartTime(t: string) {
    setStartTime(t)
    putMock.mutate({ start_time: t })
  }

  const status: FreeRealtimeTestStatus | undefined = statusQ.data
  const collectedSymbols = collectQ.data?.symbols ?? []

  return (
    <div className="mx-auto max-w-5xl space-y-6 p-4">
      <header>
        <h1 className="text-xl font-semibold">免费实时行情测试页</h1>
        <p className="text-sm text-muted-foreground">
          Mock 与真实循环均从「持仓T 已启用 / 自选股已启用 / 超买超卖已启用」三个模块自动拉取标的，无需在此重复配置。
        </p>
      </header>

      <section className="rounded border p-4">
        <h2 className="mb-3 text-lg font-medium">标的来源（共享）</h2>
        <p className="mb-2 text-sm text-muted-foreground">
          点击下方任一标的，下方「Mock 落盘」「真实落盘」两个模块将分别用其展示分时数据：
        </p>
        {collectedSymbols.length === 0 ? (
          <p className="text-warn text-sm">
            当前无已启用标的 — 请在「持仓T / 自选股 / 超买超卖」模块中开启至少一只。
          </p>
        ) : (
          <div className="flex flex-wrap gap-1.5">
            {collectedSymbols.map((s) => (
              <button
                key={s}
                type="button"
                className={
                  previewSymbol === s
                    ? 'inline-flex items-center px-2 py-0.5 rounded text-[11px] font-mono bg-primary text-primary-foreground'
                    : 'inline-flex items-center px-2 py-0.5 rounded text-[11px] font-mono bg-elevated hover:bg-elevated/80'
                }
                onClick={() => setPreviewSymbol(s)}
              >
                {s}
              </button>
            ))}
          </div>
        )}
      </section>

      {/* ── Mock 落盘模块 ── */}
      <section className="rounded border p-4">
        <div className="mb-3 flex items-center justify-between">
          <h2 className="text-lg font-medium">Mock 落盘</h2>
          <div className="flex items-center gap-2 text-xs text-muted-foreground">
            <span
              className={
                status?.mock_running
                  ? 'inline-flex items-center gap-1 text-emerald-500'
                  : 'inline-flex items-center gap-1'
              }
            >
              <span
                className={
                  status?.mock_running
                    ? 'size-1.5 rounded-full bg-emerald-500'
                    : 'size-1.5 rounded-full bg-muted-foreground/50'
                }
              />
              {status?.mock_running ? '运行中' : '停止'}
            </span>
            <span className="font-mono">
              累计 tick {status?.mock_tick_count ?? 0}
            </span>
          </div>
        </div>
        <div className="space-y-3">
          <div>
            <span className="text-sm text-muted-foreground">加速倍率</span>
            <div className="mt-1 flex flex-wrap gap-2">
              {ACCELERATIONS.map((a) => (
                <button
                  key={a}
                  type="button"
                  className={acceleration === a ? BTN_SM_PRIMARY : BTN_SM_SECONDARY}
                  onClick={() => changeAcceleration(a)}
                  disabled={putMock.isPending}
                >
                  {a}x
                </button>
              ))}
            </div>
          </div>

          <div>
            <span className="text-sm text-muted-foreground">
              mock 时间起点
              <span className="ml-1 text-xs text-muted-foreground/70">
                从该时刻按倍率节奏生成 tick（1x 即真实交易节奏，画完整天约 5.25 小时）
              </span>
            </span>
            <div className="mt-1 flex flex-wrap gap-2">
              {START_TIMES.map((t) => (
                <button
                  key={t}
                  type="button"
                  className={startTime === t ? BTN_SM_PRIMARY : BTN_SM_SECONDARY}
                  onClick={() => changeStartTime(t)}
                  disabled={putMock.isPending}
                >
                  {t}
                </button>
              ))}
            </div>
          </div>

          <div className="flex flex-wrap gap-2">
            {status?.mock_running ? (
              <button className={BTN_SECONDARY} onClick={() => stopMock.mutate()}>
                <Pause className="size-4" /> 停止 mock
              </button>
            ) : (
              <button
                className={BTN_PRIMARY}
                onClick={() => startMock.mutate()}
                disabled={startMock.isPending || collectedSymbols.length === 0}
                title={collectedSymbols.length === 0 ? '无已启用标的' : '启动 mock 循环'}
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

          {previewSymbol && (
            <div className="pt-2">
              <StockIntradayChart
                symbol={previewSymbol.trim().toUpperCase()}
                date={todayStr}
                intradayKind="mock"
                height={480}
                refetchIntervalMs={status?.mock_running ? 3000 : false}
              />
            </div>
          )}
        </div>
      </section>

      {/* ── 真实落盘模块 ── */}
      <section className="rounded border p-4">
        <div className="mb-3 flex items-center justify-between">
          <h2 className="text-lg font-medium">真实落盘</h2>
          <div className="flex items-center gap-2 text-xs text-muted-foreground">
            <span
              className={
                status?.real_loop_running
                  ? 'inline-flex items-center gap-1 text-emerald-500'
                  : 'inline-flex items-center gap-1'
              }
            >
              <span
                className={
                  status?.real_loop_running
                    ? 'size-1.5 rounded-full bg-emerald-500'
                    : 'size-1.5 rounded-full bg-muted-foreground/50'
                }
              />
              {status?.real_loop_running ? '运行中' : '停止'}
            </span>
            <span className="font-mono">
              累计 tick {status?.real_tick_count ?? 0}
            </span>
          </div>
        </div>
        <p className="mb-3 text-sm text-muted-foreground">
          绕过全局开关与交易时段，按「A股免费实时行情」配置的间隔轮询勾选源（仅测试用）。
        </p>
        <div className="flex flex-wrap gap-2">
          {status?.real_loop_running ? (
            <button className={BTN_SECONDARY} onClick={() => stopReal.mutate()}>
              <Pause className="size-4" /> 停止真实循环
            </button>
          ) : (
            <button
              className={BTN_PRIMARY}
              onClick={() => startReal.mutate()}
              disabled={startReal.isPending || collectedSymbols.length === 0}
              title={collectedSymbols.length === 0 ? '无已启用标的' : '启动真实循环'}
            >
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
              statusQ.refetch()
              collectQ.refetch()
            }}
          >
            <RefreshCcw className="size-4" />
          </button>
        </div>

        {previewSymbol && (
          <div className="pt-4">
            <StockIntradayChart
              symbol={previewSymbol.trim().toUpperCase()}
              date={todayStr}
              intradayKind="real"
              height={480}
              refetchIntervalMs={status?.real_loop_running ? 3000 : false}
            />
          </div>
        )}
      </section>
    </div>
  )
}

