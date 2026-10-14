import { useRef } from 'react'
import { ExternalLink, RefreshCw } from 'lucide-react'
import { PageHeader } from './PageHeader'

type EmbeddedToolProps = {
  title: string
  url: string
  hint?: string
}

/**
 * 内嵌常用股票工具站点的通用 iframe 容器。
 *
 * 设计要点:
 * - 顶部 PageHeader 提供标题 + 刷新 + "在新窗口打开" 按钮(始终可见)
 * - iframe 占满剩余高度,使用 sandbox 限制权限,allow-same-origin 保留登录 cookie
 * - referrerPolicy="no-referrer" 不泄露用户当前 URL
 * - loading="lazy" 减轻首屏开销
 */
export function EmbeddedTool({ title, url, hint }: EmbeddedToolProps) {
  const iframeRef = useRef<HTMLIFrameElement>(null)

  const handleRefresh = () => {
    // 强制重新加载(绕过缓存): 给 src 加一个 #tick 参数再立刻去掉
    const node = iframeRef.current
    if (!node) return
    node.src = `${url}#tick=${Date.now()}`
  }

  const openInNewTab = () => {
    window.open(url, '_blank', 'noopener,noreferrer')
  }

  return (
    <div className="flex flex-col h-full">
      <PageHeader
        title={title}
        subtitle={hint}
        right={
          <div className="flex items-center gap-1">
            <button
              type="button"
              onClick={handleRefresh}
              title="刷新"
              aria-label={`刷新 ${title}`}
              className="inline-flex items-center justify-center h-7 w-7 rounded-btn border border-border bg-surface text-muted hover:text-foreground hover:border-accent/40 transition-colors cursor-pointer"
            >
              <RefreshCw className="h-3.5 w-3.5" />
            </button>
            <button
              type="button"
              onClick={openInNewTab}
              title="在新窗口打开"
              aria-label={`在新窗口打开 ${title}`}
              className="inline-flex items-center justify-center h-7 w-7 rounded-btn border border-border bg-surface text-muted hover:text-foreground hover:border-accent/40 transition-colors cursor-pointer"
            >
              <ExternalLink className="h-3.5 w-3.5" />
            </button>
          </div>
        }
      />
      <iframe
        ref={iframeRef}
        src={url}
        title={title}
        loading="lazy"
        referrerPolicy="no-referrer"
        sandbox="allow-scripts allow-same-origin allow-forms allow-popups allow-popups-to-escape-sandbox"
        allow=""
        className="flex-1 w-full border-0 bg-base"
      />
    </div>
  )
}