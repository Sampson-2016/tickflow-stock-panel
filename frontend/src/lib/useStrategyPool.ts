/**
 * 策略池 (选股页已拉出的策略卡片) — 持久化到后端 preferences, 跨浏览器一致。
 * localStorage 仅作后端不可用时的离线兜底。
 */
import { useCallback } from 'react'
import { useQuery, useQueryClient } from '@tanstack/react-query'
import { api } from '@/lib/api'
import { QK } from '@/lib/queryKeys'
import { storage } from '@/lib/storage'

export function useStrategyPool() {
  const qc = useQueryClient()

  const query = useQuery({
    queryKey: QK.strategyPool,
    staleTime: Infinity,
    queryFn: async (): Promise<string[]> => {
      try {
        const res = await api.strategyPool()
        storage.strategyPool.set(res.ids)
        return res.ids
      } catch {
        // 后端不可用: 退回 localStorage
        return storage.strategyPool.get([])
      }
    },
  })

  const pool = query.data ?? []

  // 本地立即更新 + 异步写后端 (失败不打断交互, 下次操作会再写)
  const update = useCallback((next: string[]) => {
    qc.setQueryData(QK.strategyPool, next)
    storage.strategyPool.set(next)
    api.strategyPoolSave(next).catch(() => {})
  }, [qc])

  const addToPool = useCallback((id: string) => {
    if (pool.includes(id)) return
    update([...pool, id])
  }, [pool, update])

  const removeFromPool = useCallback((id: string) => {
    if (!pool.includes(id)) return
    update(pool.filter(x => x !== id))
  }, [pool, update])

  const reorderPool = useCallback((newOrder: string[]) => {
    update(newOrder)
  }, [update])

  // 清除池中不存在于 validIds 的失效策略(如本地开发残留的自定义策略)。
  // 仅当确实有失效项时才更新,避免无谓重渲染与重复写盘。
  const prune = useCallback((validIds: Iterable<string>) => {
    if (pool.length === 0) return
    const validSet = validIds instanceof Set ? validIds : new Set(validIds)
    const next = pool.filter(id => validSet.has(id))
    if (next.length !== pool.length) update(next)
  }, [pool, update])

  const isInPool = useCallback((id: string) => pool.includes(id), [pool])

  return { pool, addToPool, removeFromPool, reorderPool, prune, isInPool }
}
