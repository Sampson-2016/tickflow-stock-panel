/**
 * 品牌中央注册表 — 应用名 + Tagline 的唯一来源。
 *
 * 改名字: 改这里, 全站 (Layout/Onboarding/Auth/Branding/Logo/index.html/...) 自动更新。
 * 浏览器 Tab 标题: index.html 仍需手改 (静态 HTML 不走 Vite 解析), 见 index.html:21。
 *
 * 不在此处的:
 *  - 仓库 slug `tickflow-stock-panel` (GitHub 地址, 不要改)
 *  - 打包产物名 `TickFlowStockPanel` (PyInstaller / Inno Setup / DMG)
 *  - 上游 SDK 名 `TickFlow` (数据源, 与品牌名无关)
 */
export const BRAND_NAME = '观心台'
/** 副标语 (登录页底部等) */
export const BRAND_TAGLINE = '自托管量化工作台 · 数据完全掌握在自己手里'
