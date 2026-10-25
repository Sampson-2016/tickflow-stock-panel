// 超买超卖收盘报告区块 — 配置（默认收缩）+ 报告展示 + 历史归档
import { useMutation, useQuery, useQueryClient } from '@tanstack/react-query'
import {
  Activity,
  ChevronDown,
  ChevronRight,
  Clock,
  FileText,
  RefreshCcw,
} from 'lucide-react'
import { useState } from 'react'
import { toast } from '@/components/Toast'
import {
  BTN_SECONDARY,
  BTN_SM_PRIMARY,
  BTN_SM_SECONDARY,
  INPUT_CLS,
  freeRealtimeApi,
  type FreeRealtimeStatus,
} from './api'
import type { OoReportFull, OoReportRow } from '@/lib/api'

const CATEGORY_META = [
  { key: 'reached', label: '已到达阈值' },
  { key: 'near', label: '接近阈值' },
  { key: 'far', label: '远离阈值' },
] as const

type CategoryKey = (typeof CATEGORY_META)[number]['key']

function RowTable({ rows }: { rows: OoReportRow[] }) {
  if (rows.length === 0) {
    return <p className="px-3 py-2 text-xs text-muted">无</p>
  }
  return (
    <table className="w-full text-xs">
      <thead>
        <tr className="text-left text-muted border-b border-border">
          <th className="py-1.5 px-3 font-normal">标的</th>
          <th className="py-1.5 px-2 font-normal">收盘</th>
          <th className="py-1.5 px-2 font-normal">MA5</th>
          <th className="py-1.5 px-2 font-normal">偏离</th>
          <th className="py-1.5 px-2 font-normal">阈值</th>
          <th className="py-1.5 px-2 font-normal">方向</th>
          <th className="py-1.5 px-3 font-normal">提示</th>
        </tr>
      </thead>
      <tbody>
        {rows.map((r) => (
          <tr key={r.symbol} className="border-b border-border/50">
            <td className="py-1.5 px-3">
              <span className="text-foreground">{r.name || r.symbol}</span>
              <span className="ml-1.5 text-muted">{r.symbol}</span>
              {r.price_source === 'tick' && (
                <span className="ml-1 text-[10px] text-warn">(tick口径)</span>
              )}
            </td>
            <td className="py-1.5 px-2 text-secondary">{r.close.toFixed(2)}</td>
            <td className="py-1.5 px-2 text-secondary">{r.ma5.toFixed(2)}</td>
            <td className={`py-1.5 px-2 font-medium ${r.deviation_pct >= 0 ? 'text-danger' : 'text-success'}`}>
              {r.deviation_pct >= 0 ? '+' : ''}{r.deviation_pct.toFixed(2)}%
            </td>
            <td className="py-1.5 px-2 text-muted">{r.threshold_pct.toFixed(2)}%</td>
            <td className="py-1.5 px-2 text-muted">{r.direction === 'overbought' ? '超买' : '超卖'}</td>
            <td className="py-1.5 px-3 text-muted">
              {r.category === 'reached'
                ? r.direction === 'overbought' ? '可考虑卖出/止盈' : '可考虑买入/关注'
                : r.category === 'near' ? '加快观察' : '暂无要求'}
            </td>
          </tr>
        ))}
      </tbody>
    </table>
  )
}

export function OoReportSection({ status }: { status?: FreeRealtimeStatus }) {
  const qc = useQueryClient()
  const [configOpen, setConfigOpen] = useState(false)
  const [historyOpen, setHistoryOpen] = useState(false)
  const [detail, setDetail] = useState<OoReportFull | null>(null)

  const moduleEnabled = !!status?.modules?.overbought_oversold

  // 配置
  const configQ = useQuery({
    queryKey: ['free-realtime-overbought', 'report-config'],
    queryFn: freeRealtimeApi.ooReportConfig,
  })
  const cfg = configQ.data
  const [draft, setDraft] = useState<{
    schedule_enabled: boolean
    schedule_time: string
    categories: CategoryKey[]
    ai_commentary: boolean
    prompt: string
  } | null>(null)
  const d = draft ?? (cfg
    ? {
        schedule_enabled: cfg.schedule_enabled,
        schedule_time: cfg.schedule_time,
        categories: cfg.categories as CategoryKey[],
        ai_commentary: cfg.ai_commentary,
        prompt: cfg.prompt,
      }
    : null)

  const saveConfigMut = useMutation({
    mutationFn: (patch: Record<string, unknown>) => freeRealtimeApi.ooReportPutConfig(patch),
    onSuccess: () => {
      qc.invalidateQueries({ queryKey: ['free-realtime-overbought', 'report-config'] })
      setDraft(null)
      toast('报告配置已保存', 'success')
    },
    onError: (e: Error) => toast(`保存失败: ${e.message}`),
  })

  // 生成报告
  const generateMut = useMutation({
    mutationFn: () => freeRealtimeApi.ooReportGenerate(),
    onSuccess: (rep) => {
      setDetail(rep)
      qc.invalidateQueries({ queryKey: ['free-realtime-overbought', 'reports'] })
      toast(`报告已生成（评估 ${rep.evaluated} 只）`, 'success')
    },
    onError: (e: Error) => toast(`生成失败: ${e.message}`),
  })

  // 历史归档
  const reportsQ = useQuery({
    queryKey: ['free-realtime-overbought', 'reports'],
    queryFn: freeRealtimeApi.ooReports,
    enabled: historyOpen,
  })

  const loadDetail = async (tradeDate: string) => {
    try {
      setDetail(await freeRealtimeApi.ooReportDetail(tradeDate))
    } catch (e) {
      toast(`加载失败: ${e instanceof Error ? e.message : e}`)
    }
  }

  return (
    <section className="rounded border border-border bg-elevated/30">
      {/* 头部：折叠开关 + 生成按钮 */}
      <button
        type="button"
        className="w-full flex items-center justify-between px-4 py-2.5"
        onClick={() => setConfigOpen((v) => !v)}
      >
        <span className="flex items-center gap-1.5 text-sm font-medium text-foreground">
          {configOpen ? <ChevronDown className="h-4 w-4 text-muted" /> : <ChevronRight className="h-4 w-4 text-muted" />}
          <FileText className="h-4 w-4 text-accent" />
          收盘报告
          {cfg?.schedule_enabled && (
            <span className="ml-1 text-[10px] text-muted font-normal">定时 {cfg.schedule_time}</span>
          )}
        </span>
      </button>

      {!configOpen && (
        <div className="px-4 pb-3 space-y-3">
          <div className="flex items-center gap-2">
            <button
              type="button"
              className={BTN_SM_PRIMARY}
              disabled={!moduleEnabled || generateMut.isPending}
              onClick={() => generateMut.mutate()}
              title={moduleEnabled ? '基于收盘日 K 生成三类报告' : '请先开启超买超卖模块'}
            >
              <RefreshCcw className={`h-3.5 w-3.5 ${generateMut.isPending ? 'animate-spin' : ''}`} />
              {generateMut.isPending ? '生成中…' : '生成报告'}
            </button>
            <button
              type="button"
              className={BTN_SM_SECONDARY}
              onClick={() => setHistoryOpen((v) => !v)}
            >
              <Clock className="h-3.5 w-3.5" /> 历史报告
            </button>
          </div>

          {/* 报告展示 */}
          {detail && (
            <div className="rounded border border-border bg-surface p-3 space-y-3">
              <div className="flex items-center justify-between">
                <span className="text-xs text-secondary">
                  <Activity className="inline h-3.5 w-3.5 text-accent mr-1" />
                  {detail.trade_date} · 生成于 {detail.as_of} · 评估 {detail.evaluated} 只
                </span>
                <button
                  type="button"
                  className={BTN_SM_SECONDARY}
                  onClick={() => setDetail(null)}
                  title="收起报告内容"
                >
                  收起
                </button>
              </div>
              {(['reached', 'near', 'far'] as CategoryKey[]).map((cat) => {
                const meta = CATEGORY_META.find((m) => m.key === cat)!
                const rows = detail[cat] ?? []
                if (!(detail.categories ?? []).includes(cat)) return null
                return (
                  <div key={cat} className="rounded border border-border/60 overflow-hidden">
                    <div className="px-3 py-1.5 bg-elevated/60 text-xs font-medium text-foreground">
                      {meta.label}（{rows.length} 只）
                    </div>
                    <RowTable rows={rows} />
                  </div>
                )
              })}
              {detail.ai_commentary && (
                <div className="rounded border border-border/60 p-3">
                  <p className="text-xs font-medium text-foreground mb-1.5">AI 总结</p>
                  <p className="text-xs text-secondary leading-relaxed whitespace-pre-wrap">
                    {detail.ai_commentary}
                  </p>
                </div>
              )}
            </div>
          )}

          {/* 历史归档列表 */}
          {historyOpen && (
            <div className="rounded border border-border bg-surface p-2 max-h-[240px] overflow-auto">
              {(reportsQ.data?.reports ?? []).length === 0 ? (
                <p className="px-2 py-4 text-center text-xs text-muted">暂无历史报告</p>
              ) : (
                <ul className="divide-y divide-border/60">
                  {reportsQ.data!.reports.map((r) => (
                    <li key={r.file}>
                      <button
                        type="button"
                        className="w-full text-left px-2 py-1.5 hover:bg-elevated flex items-center justify-between text-xs"
                        onClick={() => loadDetail(r.file)}
                      >
                        <span className="text-foreground">
                          {r.trade_date}
                          <span className="ml-1 text-muted">{(r.as_of ?? '').slice(11) || r.file.slice(11)}</span>
                        </span>
                        <span className="text-muted">
                          达 {r.counts.reached ?? 0} / 近 {r.counts.near ?? 0} / 远 {r.counts.far ?? 0}
                          {r.has_ai ? ' · AI' : ''}
                        </span>
                      </button>
                    </li>
                  ))}
                </ul>
              )}
            </div>
          )}
        </div>
      )}

      {/* 配置区（折叠） */}
      {configOpen && d && cfg && (
        <div className="border-t border-border px-4 py-3 space-y-3">
          {/* 定时 */}
          <div className="flex items-center gap-2">
            <label className="flex items-center gap-1.5 text-xs text-foreground">
              <input
                type="checkbox"
                checked={d.schedule_enabled}
                onChange={(e) => setDraft({ ...d, schedule_enabled: e.target.checked })}
              />
              定时生成
            </label>
            <input
              type="time"
              className={`${INPUT_CLS} mt-0 w-28`}
              value={d.schedule_time}
              onChange={(e) => setDraft({ ...d, schedule_time: e.target.value })}
            />
            <span className="text-[10px] text-muted">交易日收盘后自动生成并推送</span>
          </div>

          {/* 类别勾选 */}
          <div className="flex items-center gap-3">
            <span className="text-xs text-secondary shrink-0">报告类别</span>
            {CATEGORY_META.map((m) => (
              <label key={m.key} className="flex items-center gap-1 text-xs text-foreground">
                <input
                  type="checkbox"
                  checked={d.categories.includes(m.key)}
                  onChange={(e) => {
                    const next = e.target.checked
                      ? [...d.categories, m.key]
                      : d.categories.filter((c) => c !== m.key)
                    if (next.length === 0) return // 至少一类
                    setDraft({ ...d, categories: next })
                  }}
                />
                {m.label}
              </label>
            ))}
          </div>

          {/* AI 总结 */}
          <div className="space-y-1.5">
            <label className="flex items-center gap-1.5 text-xs text-foreground">
              <input
                type="checkbox"
                checked={d.ai_commentary}
                disabled={!cfg.ai_configured}
                onChange={(e) => setDraft({ ...d, ai_commentary: e.target.checked })}
              />
              附 AI 总结
              {!cfg.ai_configured && (
                <span className="text-[10px] text-muted">（未配置 AI Key，自动降级）</span>
              )}
            </label>
            {d.ai_commentary && (
              <>
                <textarea
                  className={`${INPUT_CLS} h-32 font-mono text-[11px] leading-relaxed`}
                  placeholder="自定义 AI 提示词（留空使用内置默认）"
                  value={d.prompt}
                  onChange={(e) => setDraft({ ...d, prompt: e.target.value })}
                />
                <p className="text-[10px] text-muted -mt-1">
                  AI 只解读清单不做新数字；留空用内置提示词。三类清单随类别勾选过滤。
                </p>
              </>
            )}
          </div>

          <div className="flex items-center gap-2">
            <button
              type="button"
              className={BTN_SM_PRIMARY}
              disabled={saveConfigMut.isPending}
              onClick={() =>
                saveConfigMut.mutate({
                  schedule_enabled: d.schedule_enabled,
                  schedule_time: d.schedule_time,
                  categories: d.categories,
                  ai_commentary: d.ai_commentary,
                  prompt: d.prompt,
                })
              }
            >
              保存配置
            </button>
            <button
              type="button"
              className={BTN_SECONDARY}
              onClick={() => { setDraft(null); setConfigOpen(false) }}
            >
              收起
            </button>
          </div>
        </div>
      )}
    </section>
  )
}
