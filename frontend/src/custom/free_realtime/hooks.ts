// 免费实时行情共享轮询 hooks — 统一 status query key 与轮询节奏
import { useQuery } from '@tanstack/react-query'
import { freeRealtimeApi } from './api'

export const FR_STATUS_KEY = ['free-realtime', 'status'] as const
export const FR_TEST_STATUS_KEY = ['free-realtime-test', 'status'] as const

/** 免费实时行情调度器状态（运行时 5s / 停止时 15s 轮询） */
export function useFreeRealtimeSchedulerStatus(enabled = true) {
  return useQuery({
    queryKey: FR_STATUS_KEY,
    queryFn: freeRealtimeApi.status,
    enabled,
    refetchInterval: (q) => (q.state.data?.running ? 5000 : 15000),
  })
}

/** 测试页 mock/真实循环状态（任一运行 3s 高频 / 停止 15s 低频仅用于感知启动） */
export function useFreeRealtimeTestStatus(enabled = true) {
  return useQuery({
    queryKey: FR_TEST_STATUS_KEY,
    queryFn: freeRealtimeApi.testStatus,
    enabled,
    refetchInterval: (q) => {
      const d = q.state.data
      return d && (d.mock_running || d.real_loop_running) ? 3000 : 15000
    },
  })
}

/** 分时图轮询节奏：调度器或测试页循环运行时按 interval_sec 轮询（与抓取节奏对齐，避免高频拉前端）；
 *  停止时返回 false：不轮询，无新数据。 */
export function useFreeRealtimeIntradayRefetchMs(enabled = true): number | false {
  const statusQ = useFreeRealtimeSchedulerStatus(enabled)
  const testQ = useFreeRealtimeTestStatus(enabled)
  const frTestRunning = !!(testQ.data?.mock_running || testQ.data?.real_loop_running)
  return (statusQ.data?.running || frTestRunning)
    ? Math.max(1000, (statusQ.data?.interval_sec ?? 3) * 1000)
    : false
}
