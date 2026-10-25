import type { MinuteKlineRow } from '@/lib/api'

export function formatMinuteTime(datetime: string): string {
  const match = datetime.match(/(\d{2}):(\d{2})/)
  if (!match) return datetime.slice(11, 16)
  const hour = (parseInt(match[1]) + 8) % 24
  return `${String(hour).padStart(2, '0')}:${match[2]}`
}

export function computeIntradayAverage(data: MinuteKlineRow[]): number[] {
  const result: number[] = []
  let amount = 0
  let volume = 0
  for (const row of data) {
    amount += row.amount
    volume += row.volume * 100
    result.push(volume > 0 ? amount / volume : row.close)
  }
  return result
}

function generateFullDayTimes(): string[] {
  const times: string[] = []
  for (let hour = 9; hour <= 11; hour++) {
    const startMinute = hour === 9 ? 30 : 0
    const endMinute = hour === 11 ? 30 : 59
    for (let minute = startMinute; minute <= endMinute; minute++) {
      times.push(`${String(hour).padStart(2, '0')}:${String(minute).padStart(2, '0')}`)
    }
  }
  for (let hour = 13; hour <= 15; hour++) {
    const endMinute = hour === 15 ? 0 : 59
    for (let minute = 0; minute <= endMinute; minute++) {
      times.push(`${String(hour).padStart(2, '0')}:${String(minute).padStart(2, '0')}`)
    }
  }
  return times
}

export const FULL_DAY_TIMES = generateFullDayTimes()

/** 集合竞价时段 9:15-9:29（不含 9:30，9:30 已在 FULL_DAY_TIMES 中） */
export const CALL_AUCTION_TIMES: string[] = (() => {
  const times: string[] = []
  for (let minute = 15; minute <= 29; minute++) {
    times.push(`09:${String(minute).padStart(2, '0')}`)
  }
  return times
})()

/** 含集合竞价的完整一日时间轴 9:15-15:00 */
export const FULL_DAY_TIMES_WITH_CALL = [...CALL_AUCTION_TIMES, ...FULL_DAY_TIMES]
