// 持仓 T API 类型 re-export — 二开模块通过 lib/api.ts 复用项目统一入口。
import { api } from '@/lib/api'

export type TSignalConfig = Awaited<ReturnType<typeof api.tSignalGetConfig>>
export type TSignalStatus = Awaited<ReturnType<typeof api.tSignalStatus>>
export type TSignalItem = Awaited<ReturnType<typeof api.tSignalRecent>>['items'][number]
export type TSignalKind = TSignalItem['kind']

export type PositionTItem = Awaited<ReturnType<typeof api.tSignalListPositions>>['items'][number]
export type IntradayBar = Awaited<ReturnType<typeof api.tSignalIntraday>>['bars'][number]

export const tSignalApi = {
  status: api.tSignalStatus,
  start: api.tSignalStart,
  stop: api.tSignalStop,
  getConfig: api.tSignalGetConfig,
  putConfig: api.tSignalPutConfig as (patch: Partial<TSignalConfig>) => Promise<TSignalConfig>,
  recent: api.tSignalRecent,
  listPositions: api.tSignalListPositions,
  addPosition: api.tSignalAddPosition,
  removePosition: api.tSignalRemovePosition,
  setPositionEnabled: api.tSignalSetPositionEnabled,
  intraday: api.tSignalIntraday,
  positionSignals: api.tSignalPositionSignals,
}

export const T_SIGNAL_KIND_LABELS: Record<TSignalKind, string> = {
  price_change: '涨跌幅阈值',
  volume_surge: '量比异动',
  big_order_imbalance: '买卖盘失衡',
}

// 视觉规则 (颜色 / 图标 / side): 单一事实源
export interface SignalVisual {
  color: string        // 红/黄/蓝/紫
  bgColor: string      // 卡片背景
  hint: 'buy' | 'sell' | 'info'  // 详情页综合倾向 (空 volume/big_order 是 info)
}

export const T_SIGNAL_VISUAL: Record<TSignalKind, SignalVisual> = {
  price_change: {
    color: 'text-bull',       // 涨红
    bgColor: 'bg-bull/10',
    hint: 'buy',              // 上涨 → 买入提示 (用户预期: 跟涨加仓 / 减仓兑现)
  },
  volume_surge: {
    color: 'text-amber-500',
    bgColor: 'bg-amber-500/10',
    hint: 'info',
  },
  big_order_imbalance: {
    color: 'text-accent',
    bgColor: 'bg-accent/10',
    hint: 'info',             // 实际方向 (买/卖) 在 detail 文案里, 这里仅做视觉
  },
}