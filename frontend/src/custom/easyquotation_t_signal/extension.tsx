import { BellRing } from 'lucide-react'
import type { FrontendExtension } from '@/extensions/types'
import { PositionTPage } from './PositionTPage'

const extension: FrontendExtension = {
  id: 'easyquotation.t_signal',
  apiVersion: 1,
  routes: [
    { id: 'position-t', path: '/position-t', component: PositionTPage },
  ],
  navigation: [
    {
      id: 'position-t-nav',
      routeId: 'position-t',
      label: '持仓 T',
      icon: BellRing,
      order: 460,
    },
  ],
}

export default extension