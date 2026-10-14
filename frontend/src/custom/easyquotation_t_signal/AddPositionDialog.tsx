import { useEffect, useRef, useState } from 'react'
import { Plus, Search, X } from 'lucide-react'
import { useMutation, useQuery, useQueryClient } from '@tanstack/react-query'
import { api } from '@/lib/api'
import { tSignalApi, type PositionTItem } from './api'

interface Props {
  open: boolean
  onClose: () => void
  onAdded?: (item: PositionTItem) => void
}

interface SearchHit {
  symbol: string
  name: string
  code: string
  asset_type?: string
}

/**
 * 添加持仓 T 标的弹窗 — 复用项目 instrumentSearch 体验。
 *
 * A 股 / ETF 用搜索框（与自选添加一致）；港股用单独快捷输入（项目 search 端点
 * 默认 market=cn 不返回港股, 这是底层 API 的限制而非本模块选择)。
 */
export function AddPositionDialog({ open, onClose, onAdded }: Props) {
  const [query, setQuery] = useState('')
  const [open_, setOpen] = useState(false)
  const [activeIdx, setActiveIdx] = useState(-1)
  const [hkSymbol, setHkSymbol] = useState('')
  const [hkName, setHkName] = useState('')
  const [error, setError] = useState<string | null>(null)
  const containerRef = useRef<HTMLDivElement>(null)
  const inputRef = useRef<HTMLInputElement>(null)
  const qc = useQueryClient()

  // 搜索 A 股 + ETF (项目 instrumentSearch 默认 market=cn, 包含 A 股 stock + ETF)
  const search = useQuery({
    queryKey: ['instrument-search', query],
    queryFn: () => api.instrumentSearch(query, 20, 'stock,etf'),
    enabled: query.trim().length > 0,
    staleTime: 30_000,
  })
  const results: SearchHit[] = search.data?.results ?? []

  useEffect(() => {
    if (!open) return
    function handleClick(e: MouseEvent) {
      if (containerRef.current && !containerRef.current.contains(e.target as Node)) {
        setOpen(false)
      }
    }
    document.addEventListener('mousedown', handleClick)
    return () => document.removeEventListener('mousedown', handleClick)
  }, [open])

  useEffect(() => {
    if (open) {
      setQuery('')
      setOpen(true)
      setActiveIdx(-1)
      setHkSymbol('')
      setHkName('')
      setError(null)
      // 聚焦主搜索框
      setTimeout(() => inputRef.current?.focus(), 50)
    }
  }, [open])

  const addMutation = useMutation({
    mutationFn: ({ s, n }: { s: string; n?: string }) => tSignalApi.addPosition(s, n),
    onSuccess: (item) => {
      setError(null)
      void qc.invalidateQueries({ queryKey: ['t-signal', 'positions'] })
      onAdded?.(item)
      onClose()
    },
    onError: (e) => {
      setError(e instanceof Error ? e.message : '添加失败')
    },
  })

  if (!open) return null

  const submitFromSearch = (hit: SearchHit) => {
    addMutation.mutate({ s: hit.symbol, n: hit.name })
  }

  const submitHk = () => {
    const sym = hkSymbol.trim().toUpperCase()
    if (!sym) {
      setError('请输入港股代码')
      return
    }
    addMutation.mutate({ s: sym, n: hkName.trim() || undefined })
  }

  const onKeyDown = (e: React.KeyboardEvent) => {
    if (e.key === 'Escape') {
      setOpen(false)
      return
    }
    if (!open_ || results.length === 0) return
    if (e.key === 'ArrowDown') {
      e.preventDefault()
      setActiveIdx((i) => Math.min(i + 1, results.length - 1))
    } else if (e.key === 'ArrowUp') {
      e.preventDefault()
      setActiveIdx((i) => Math.max(i - 1, -1))
    } else if (e.key === 'Enter') {
      e.preventDefault()
      const target = activeIdx >= 0 ? results[activeIdx] : results[0]
      if (target) submitFromSearch(target)
    }
  }

  return (
    <div className="fixed inset-0 z-50 grid place-items-center bg-black/60" onClick={onClose}>
      <div
        className="bg-surface border border-border rounded-card p-5 w-[480px] max-w-[calc(100vw-32px)] shadow-xl"
        onClick={(e) => e.stopPropagation()}
      >
        <div className="flex items-center justify-between mb-3">
          <h3 className="text-sm font-medium text-foreground flex items-center gap-2">
            <Plus className="h-4 w-4 text-accent" /> 添加持仓 T 标的
          </h3>
          <button
            type="button"
            onClick={onClose}
            className="h-7 w-7 grid place-items-center rounded-btn text-muted hover:bg-elevated hover:text-foreground"
          >
            <X className="h-4 w-4" />
          </button>
        </div>

        {/* 主搜索框: A 股 + ETF */}
        <div ref={containerRef} className="relative mb-3">
          <div className="relative flex items-center">
            <Search className="absolute left-2.5 top-1/2 -translate-y-1/2 h-3.5 w-3.5 text-muted pointer-events-none" />
            <input
              ref={inputRef}
              type="text"
              placeholder="搜索 A 股 / ETF (代码 / 名称 / 拼音)"
              value={query}
              onChange={(e) => { setQuery(e.target.value); setOpen(true); setActiveIdx(-1) }}
              onFocus={() => { if (query.trim()) setOpen(true) }}
              onKeyDown={onKeyDown}
              className="w-full h-9 pl-8 pr-2.5 rounded-btn bg-elevated border border-border text-sm text-foreground placeholder:text-muted focus:outline-none focus:border-accent/50"
            />
          </div>
          {open_ && results.length > 0 && (
            <div className="absolute z-10 mt-1 w-full max-h-[260px] overflow-auto bg-surface border border-border rounded-card shadow-lg">
              {results.map((r, idx) => (
                <button
                  key={r.symbol}
                  type="button"
                  onMouseEnter={() => setActiveIdx(idx)}
                  onClick={() => submitFromSearch(r)}
                  className={`w-full text-left px-3 py-2 hover:bg-elevated flex items-center justify-between gap-2 ${
                    idx === activeIdx ? 'bg-elevated' : ''
                  }`}
                >
                  <div className="min-w-0">
                    <div className="text-sm text-foreground truncate">{r.name || r.symbol}</div>
                    <div className="text-xs text-muted">{r.symbol} · {r.code}</div>
                  </div>
                  <span className="text-[10px] text-muted shrink-0">
                    {r.asset_type === 'etf' ? 'ETF' : '股票'}
                  </span>
                </button>
              ))}
            </div>
          )}
          {open_ && query.trim() && results.length === 0 && !search.isLoading && (
            <div className="absolute z-10 mt-1 w-full px-3 py-2 bg-surface border border-border rounded-card text-xs text-muted">
              无匹配结果 — 若要添加港股请用下方输入框
            </div>
          )}
        </div>

        {/* 分隔线 + 港股快速输入 */}
        <div className="my-4 flex items-center gap-3">
          <div className="flex-1 h-px bg-border" />
          <span className="text-[11px] text-muted">或港股代码</span>
          <div className="flex-1 h-px bg-border" />
        </div>

        <div className="grid grid-cols-[120px_1fr_auto] gap-2 items-end mb-1">
          <label className="block">
            <span className="text-xs text-muted">代码</span>
            <input
              type="text"
              value={hkSymbol}
              onChange={(e) => setHkSymbol(e.target.value.toUpperCase())}
              placeholder="00700"
              className="mt-1 w-full bg-elevated border border-border rounded-btn px-2 py-1.5 text-sm text-foreground focus:outline-none focus:ring-1 focus:ring-accent"
            />
          </label>
          <label className="block">
            <span className="text-xs text-muted">名称 (可选)</span>
            <input
              type="text"
              value={hkName}
              onChange={(e) => setHkName(e.target.value)}
              placeholder="腾讯控股"
              className="mt-1 w-full bg-elevated border border-border rounded-btn px-2 py-1.5 text-sm text-foreground focus:outline-none focus:ring-1 focus:ring-accent"
            />
          </label>
          <button
            type="button"
            onClick={submitHk}
            disabled={addMutation.isPending || !hkSymbol.trim()}
            className="h-[34px] px-3 rounded-btn bg-elevated text-xs text-secondary hover:text-foreground disabled:opacity-50"
          >
            添加
          </button>
        </div>
        <p className="text-[11px] text-muted mb-3">
          项目 search 端点默认只返回 A 股/ETF; 港股 (.HK 后缀) 走此快捷入口。
        </p>

        {error && <p className="mb-2 text-xs text-warn">{error}</p>}

        <div className="flex items-center justify-end gap-2 pt-2 border-t border-border">
          <button
            type="button"
            onClick={onClose}
            className="inline-flex items-center h-8 px-3 rounded-btn text-xs text-secondary hover:bg-elevated"
          >
            取消
          </button>
        </div>
      </div>
    </div>
  )
}