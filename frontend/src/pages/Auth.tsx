/**
 * 登录页 — 单用户固定凭证 (admin / gxk2026)。
 *
 * 设计:
 *   - 凭证在 backend/app/services/auth.py 的 DEFAULT_USERNAME / DEFAULT_PASSWORD,
 *     首次启动自动写入 data/user_data/auth.json (PBKDF2 哈希, chmod 0600)。
 *   - 用户名固定, 不可改: 控件 read-only 锁图标, 防止误以为是注册页。
 *   - 密码唯一可变项: eye-toggle 切换明文/密文。
 *   - 错用户名/密码统一返回 401 + 同一文案, 防用户名枚举。
 */
import { useEffect, useState, type FormEvent } from 'react'
import { useNavigate } from 'react-router-dom'
import { useMutation } from '@tanstack/react-query'
import { motion } from 'framer-motion'
import { Eye, EyeOff, Loader2, Lock, ShieldAlert, Sparkles, User } from 'lucide-react'
import { api } from '@/lib/api'
import { Logo } from '@/components/Logo'
import { BRAND_NAME, BRAND_TAGLINE } from '@/lib/brand'
import { cn } from '@/lib/cn'

export function Auth() {
  const navigate = useNavigate()
  const [username, setUsername] = useState('')         // 从 /api/auth/status 回填
  const [password, setPassword] = useState('')
  const [showPwd, setShowPwd] = useState(false)
  const [localError, setLocalError] = useState('')

  // 取认证状态 (用户名 + 是否已登录)
  useEffect(() => {
    api.authStatus().then((s) => {
      if (s.authenticated) {
        // 已登录: 直接进面板 (避免登录页死循环)
        navigate('/', { replace: true })
        return
      }
      // 回填用户名 (永远展示当前 auth.json 里的 username, 默认 admin)
      setUsername(s.username || '')
    }).catch(() => {
      // /api/auth/status 失败 (极端情况: 后端未启/反代配置错) — 用户名留空,
      // 让表单可继续填; 提交时后端会按实际配置校验。
    })
  }, [navigate])

  const submitMut = useMutation({
    mutationFn: () => api.authLogin(username, password),
    onSuccess: () => {
      const redirect = new URLSearchParams(window.location.search).get('redirect') || '/'
      navigate(redirect, { replace: true })
    },
    onError: (err: any) => {
      // 后端对用户名错/密码错统一返回 "用户名或密码错误" (api.ts:38 静默 401 toast)
      setLocalError(err?.message || '登录失败')
    },
  })

  const handleSubmit = (e: FormEvent) => {
    e.preventDefault()
    setLocalError('')
    if (!username || !password) {
      setLocalError('请填写用户名和密码')
      return
    }
    submitMut.mutate()
  }

  return (
    <div className="relative flex min-h-screen items-center justify-center overflow-hidden bg-base px-4">
      {/* 背景辉光 (与 Onboarding 一致的紫蓝光晕) */}
      <div className="pointer-events-none absolute inset-0 bg-[radial-gradient(circle_at_30%_20%,rgba(139,92,246,0.15),transparent_40%),radial-gradient(circle_at_70%_80%,rgba(59,130,246,0.12),transparent_40%)]" />

      <motion.div
        initial={{ opacity: 0, y: 16 }}
        animate={{ opacity: 1, y: 0 }}
        transition={{ duration: 0.4, ease: [0.16, 1, 0.3, 1] }}
        className="relative w-full max-w-sm"
      >
        {/* Logo + 品牌名 */}
        <div className="mb-6 flex flex-col items-center gap-2">
          <Logo className="h-10 w-10" />
          <h1 className="text-lg font-semibold text-foreground tracking-tight">{BRAND_NAME}</h1>
        </div>

        <div className="rounded-card border border-border bg-surface/90 p-6 shadow-2xl backdrop-blur">
          {/* 标题区 */}
          <div className="mb-5 flex items-center gap-2.5">
            <div className="grid h-9 w-9 place-items-center rounded-lg bg-purple-500/15 text-purple-400">
              <Lock className="h-5 w-5" />
            </div>
            <div>
              <div className="text-sm font-medium text-foreground">登录访问</div>
              <div className="text-[11px] text-muted">请输入用户名和密码以继续</div>
            </div>
          </div>

          <form onSubmit={handleSubmit} className="space-y-3">
            {/* 用户名 (read-only, 锁图标表示不可改) */}
            <div className="relative">
              <User className="pointer-events-none absolute left-3 top-1/2 h-4 w-4 -translate-y-1/2 text-muted" />
              <input
                type="text"
                value={username}
                readOnly
                aria-label="用户名 (固定, 不可修改)"
                title="用户名由服务端配置, 不可修改。如需更换请编辑 data/user_data/auth.json"
                placeholder="用户名"
                className={cn(
                  'h-10 w-full rounded-btn border border-border bg-base/50 pl-9 pr-9 text-sm text-foreground',
                  'outline-none transition-colors focus:border-accent/50',
                  'cursor-not-allowed select-none',
                )}
              />
              {/* 锁图标, 提示用户名不可改 */}
              <Lock className="pointer-events-none absolute right-3 top-1/2 h-3.5 w-3.5 -translate-y-1/2 text-muted/50" />
            </div>

            {/* 密码 (唯一可编辑项) */}
            <div className="relative">
              <input
                type={showPwd ? 'text' : 'password'}
                value={password}
                onChange={e => setPassword(e.target.value)}
                placeholder="访问密码"
                autoFocus
                autoComplete="current-password"
                className="h-10 w-full rounded-btn border border-border bg-base px-3 pr-9 text-sm text-foreground outline-none transition-colors focus:border-accent/50"
              />
              <button
                type="button"
                onClick={() => setShowPwd(s => !s)}
                className="absolute right-2 top-1/2 -translate-y-1/2 p-1 text-muted hover:text-foreground"
                tabIndex={-1}
                aria-label={showPwd ? '隐藏密码' : '显示密码'}
              >
                {showPwd ? <EyeOff className="h-4 w-4" /> : <Eye className="h-4 w-4" />}
              </button>
            </div>

            {/* 错误提示 */}
            {(localError || submitMut.error) && (
              <div className="flex items-start gap-1.5 rounded-btn bg-danger/10 px-3 py-2 text-[11px] text-danger">
                <ShieldAlert className="mt-px h-3.5 w-3.5 shrink-0" />
                <span>{localError}</span>
              </div>
            )}

            <button
              type="submit"
              disabled={submitMut.isPending || !password}
              className="inline-flex h-10 w-full items-center justify-center gap-1.5 rounded-btn bg-accent text-sm font-medium text-white transition-colors hover:bg-accent/90 disabled:opacity-50"
            >
              {submitMut.isPending ? (
                <><Loader2 className="h-4 w-4 animate-spin" />登录中…</>
              ) : (
                '登录'
              )}
            </button>
          </form>
        </div>

        <div className="mt-4 flex items-center justify-center gap-1.5 text-[10px] text-muted/60">
          <Sparkles className="h-3 w-3" />
          {BRAND_TAGLINE}
        </div>
      </motion.div>
    </div>
  )
}
