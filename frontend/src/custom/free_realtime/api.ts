// 免费实时行情 API 接口封装 — 复用项目统一 api 入口
import { api } from '@/lib/api'

export const freeRealtimeApi = {
  getConfig: api.freeRealtimeGetConfig,
  putConfig: api.freeRealtimePutConfig as (
    patch: Partial<{
      enabled: boolean
      interval_sec: number
      sources: string[]
      trading_hours_only: boolean
      retention_days: number
      overbought_pct: number
      oversold_pct: number
      modules: Partial<{ position_t: boolean; overbought_oversold: boolean; watchlist: boolean }>
    }>,
  ) => Promise<Awaited<ReturnType<typeof api.freeRealtimeGetConfig>>>,
  status: api.freeRealtimeStatus,
  start: api.freeRealtimeStart,
  stop: api.freeRealtimeStop,
  prune: api.freeRealtimePrune,
  intraday: api.freeRealtimeIntraday,
  // 测试页
  testMockConfig: api.freeRealtimeTestMockConfig,
  testMockPutConfig: api.freeRealtimeTestMockPutConfig as (
    patch: Record<string, unknown>,
  ) => Promise<{ config: Record<string, unknown>; running: boolean }>,
  testMockStart: api.freeRealtimeTestMockStart,
  testMockStop: api.freeRealtimeTestMockStop,
  testRealStart: api.freeRealtimeTestRealStart,
  testRealStop: api.freeRealtimeTestRealStop,
  testStatus: api.freeRealtimeTestStatus,
  testClear: api.freeRealtimeTestClear,
}

export type FreeRealtimeConfig = Awaited<ReturnType<typeof api.freeRealtimeGetConfig>>
export type FreeRealtimeStatus = Awaited<ReturnType<typeof api.freeRealtimeStatus>>
export type FreeRealtimeTestStatus = Awaited<ReturnType<typeof api.freeRealtimeTestStatus>>

export const SOURCE_LABELS: Record<string, string> = {
  sina: '新浪财经',
  tencent: '腾讯证券',
  eastmoney: '东方财富',
  mock: '模拟数据',
}

export const MODULE_LABELS = {
  position_t: '持仓 T',
  overbought_oversold: '超买超卖',
  watchlist: '自选股',
} as const

export const ACCELERATIONS = [1, 3, 10, 30, 60, 120] as const

// 共享样式（与项目 easyquotation_t_signal 同口径，避免暗色主题看不清）
export const INPUT_CLS =
  'mt-1 w-full bg-elevated border border-border rounded-btn px-2 py-1.5 text-sm text-foreground focus:outline-none focus:ring-1 focus:ring-accent'
export const BTN_PRIMARY =
  'inline-flex items-center gap-1 h-8 px-3 rounded-btn bg-accent text-xs text-white hover:bg-accent/90 disabled:opacity-50'
export const BTN_SECONDARY =
  'inline-flex items-center gap-1 h-8 px-3 rounded-btn bg-elevated text-xs text-secondary hover:text-foreground disabled:opacity-50'
export const BTN_GHOST =
  'inline-flex items-center justify-center h-8 w-8 rounded-btn text-secondary hover:text-foreground hover:bg-elevated disabled:opacity-50'
export const BTN_SM_PRIMARY =
  'inline-flex items-center h-7 px-2.5 rounded-btn bg-accent text-xs text-white hover:bg-accent/90'
export const BTN_SM_SECONDARY =
  'inline-flex items-center h-7 px-2.5 rounded-btn bg-elevated text-xs text-secondary hover:text-foreground'
