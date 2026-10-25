import { Gauge, FlaskConical, TrendingUp } from 'lucide-react'
import type { FrontendExtension } from '@/extensions/types'
import { FreeRealtimeConfigPage } from './FreeRealtimeConfigPage'
import { FreeRealtimeTestPage } from './FreeRealtimeTestPage'
import { OverboughtOversoldPage } from './OverboughtOversoldPage'

const extension: FrontendExtension = {
  id: 'free_realtime.realtime',
  apiVersion: 1,
  routes: [
    {
      id: 'free-realtime-config',
      path: '/free-realtime',
      component: FreeRealtimeConfigPage,
    },
    {
      id: 'free-realtime-test',
      path: '/free-realtime-test',
      component: FreeRealtimeTestPage,
    },
    {
      id: 'free-realtime-overbought-oversold',
      path: '/free-realtime-overbought-oversold',
      component: OverboughtOversoldPage,
    },
  ],
  navigation: [
    {
      id: 'free-realtime-nav',
      routeId: 'free-realtime-config',
      label: 'A股免费实时行情',
      icon: Gauge,
      order: 470,
    },
    {
      id: 'free-realtime-overbought-nav',
      routeId: 'free-realtime-overbought-oversold',
      label: '超买超卖',
      icon: TrendingUp,
      order: 475,
    },
    {
      id: 'free-realtime-test-nav',
      routeId: 'free-realtime-test',
      label: '免费行情测试',
      icon: FlaskConical,
      order: 480,
    },
  ],
}

export default extension
