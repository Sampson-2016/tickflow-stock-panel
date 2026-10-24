import { Gauge, FlaskConical } from 'lucide-react'
import type { FrontendExtension } from '@/extensions/types'
import { FreeRealtimeConfigPage } from './FreeRealtimeConfigPage'
import { FreeRealtimeTestPage } from './FreeRealtimeTestPage'

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
      id: 'free-realtime-test-nav',
      routeId: 'free-realtime-test',
      label: '免费行情测试',
      icon: FlaskConical,
      order: 480,
    },
  ],
}

export default extension
