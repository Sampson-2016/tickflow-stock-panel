# Tick Stock Panel 工程上下文索引

> 用途：作为以后 AI 与人协作时的快速上下文同步文件。修改后端工程前，先读完本文件，再按需深入 [`CONTRIBUTING.md`](CONTRIBUTING.md) 与 [`docs/secondary-development.md`](docs/secondary-development.md)。

## 1. 一句话定位

自托管的多市场（A 股 / 港股 / 美股）选股 + 监控 + 回测量化工作台。基于上游 `shy3130/tickflow-stock-panel` 二次开发，新增多市场抽象层。后端单服务同时跑 FastAPI 与前端 dist。

## 2. 核心技术栈

### 后端

| 维度 | 选型 |
| --- | --- |
| 语言 | Python ≥ 3.11（Apple Silicon pin `cpython-3.12-macos-aarch64-none`，见 `backend/.python-version`） |
| Web | FastAPI · Pydantic v2 · sse-starlette · uvicorn[standard] |
| 数据 | Polars（主计算） · DuckDB（查询） · PyArrow · Parquet（存储） |
| 调度 | APScheduler |
| 回测 | vectorbt（可选 extras，全项目唯一 pandas 边界） · numba/llvmlite（Matrix 引擎，macOS Intel 走纯 Python fallback） |
| AI | OpenAI 兼容适配器（DeepSeek / 通义 / Ollama 等） |
| 数据源 | TickFlow 官方 SDK · 插件化扩展（stock-sdk 示例 + YAML 自定义源） · 港美股指数走腾讯/东财公开免费接口 |
| 桌面（可选） | pywebview（PyInstaller 打包） |
| 包管理 | uv（pyproject.toml + uv.lock） |

### 前端

| 维度 | 选型 |
| --- | --- |
| 框架 | React 18 · TypeScript · Vite |
| 状态 | TanStack Query（查询键集中 `frontend/src/lib/queryKeys.ts`） |
| UI | Tailwind CSS · ECharts · lightweight-charts · dnd-kit |
| 包管理 | pnpm |

### 部署

Docker 两阶段构建：前端 dist 拷进后端镜像，单容器运行；镜像已发布 GHCR（`ghcr.io/hzy1522/tickflow-stock-panel:latest`，amd64 + arm64）。

## 3. 关键目录结构

```
tickflow-stock-panel/
├── AGENTS.md                      # AI 开发入口规则
├── CONTRIBUTING.md                # 项目架构、数据契约、缓存与测试矩阵
├── Dockerfile / docker-compose.yml # 两阶段构建 + 单容器运行
├── dev.sh / dev.ps1               # 一键起前后端（开发模式）
├── .env.example                   # 配置模板
├── tiers.yaml                     # 涨停梯队配置
├── README.md / NOTICE              # 衍生关系声明
├── docs/                          # 领域文档（部署/配置/功能/扩展等）
├── data/                           # 运行时数据（不提交）
├── frontend/                       # React 前端
│   └── src/
│       ├── lib/api.ts             # 前端 API 请求与类型契约（统一入口）
│       ├── lib/queryKeys.ts       # TanStack Query 查询键集中维护
│       ├── pages/                  # 页面级编排
│       ├── components/             # 可复用交互组件
│       └── custom/<namespace>/extension.tsx  # 前端二开注册入口
└── backend/
    ├── pyproject.toml             # 后端依赖（含 backtest/desktop/legacy-cpu/dev extras）
    ├── uv.lock
    └── app/
        ├── main.py                # FastAPI 入口 + lifespan + 认证中间件 + 路由注册
        ├── config.py              # Pydantic settings（DATA_DIR/STATIC_DIR/AUTH_*）
        ├── markets.py             # 市场注册表（CN_TZ 常量、symbol→market、印花税、交易时段）
        ├── market_time.py         # 北京时间交易时段判断
        ├── price_limits.py        # 涨跌停单一事实源（ST 压主板 + 时间切换）
        ├── parquet.py / db_safe.py # 存储与 DuckDB 安全访问
        ├── enriched_generation.py # enriched 表版本管理
        ├── desktop.py             # 桌面版入口（PyInstaller frozen）
        │
        ├── api/                   # FastAPI 路由（薄层，参数校验与响应映射）
        │   ├── auth.py / settings.py / kline.py / watchlist.py / screener.py
        │   ├── backtest.py / mining.py / intraday.py / indices*.py
        │   ├── overview.py / abnormal.py / regime.py / analysis.py
        │   ├── pipeline.py / data.py / ext_data.py / financials.py
        │   ├── stock_analysis.py / market_recap.py / strategy.py
        │   ├── signals.py / monitor_rules.py / alerts.py / rps.py
        │   └── routes.py          # 核心路由聚合
        │
        ├── services/              # 业务编排层
        │   ├── auth.py / quote_service.py / kline_sync.py / instrument_sync.py
        │   ├── backtest.py / screener.py / strategy_cache.py
        │   ├── regime_builder.py / market_phase.py / market_mainline.py
        │   ├── market_recap*.py / alert_store.py / data_integrity.py
        │   ├── depth_service.py / sector_monitor.py / abnormal_moves.py
        │   ├── financial_sync.py / financial_analyzer.py
        │   ├── index_sync*.py / ext_pull.py / ext_presets.py / ext_data.py
        │   ├── mining_*.py / heavy_job_limiter.py / matrix_prewarm_owner.py
        │   ├── ai_provider.py / ai_reports.py / stock_analyzer.py / stock_reports.py
        │   ├── watchlist.py / preferences.py / repair_daily.py / extend_history.py
        │   ├── notify_adapter.py / webhook_adapter.py / wecom_bot_service.py
        │   ├── pipeline_jobs.py / rps_rotation.py / concept_rotation_analyzer.py
        │   ├── market_overview_builder.py / market_recap_reports.py
        │   ├── json_report_store.py / mining_process_lock.py / mining_schedule.py
        │   └── free_realtime/    # 免费实时行情服务（mock 双接口、超买超卖等）
        │       ├── config.py / scheduler.py / store.py
        │       ├── overbought_oversold.py（单股阈值配置）
        │       └── __init__.py
        │
        ├── tickflow/               # 数据层抽象（仓库、能力检测、策略、限速、池）
        │   ├── repository.py      # DataStore / KlineRepository
        │   ├── capabilities.py / client.py / policy.py / pools.py
        │   ├── rate_limits.py / scheduler.py
        │   └── __init__.py
        │
        ├── data_providers/        # 数据源插件化
        │   ├── base.py / schemas.py / normalizer.py / registry.py
        │   ├── tickflow_provider.py
        │   ├── custom/            # YAML 自定义数据源
        │   │   ├── loader.py / mapper.py / provider.py / config.py
        │   │   └── __init__.py
        │   └── free_realtime/    # 免费实时行情 provider（sina/tencent/eastmoney + mock）
        │       ├── base.py / schemas.py / normalizer.py / registry.py
        │       ├── sina.py / tencent.py / eastmoney.py / mock.py
        │       └── __init__.py
        │
        ├── strategy/              # 选股策略引擎
        │   ├── engine.py         # 高冲突热点（不要继承覆盖内部方法）
        │   ├── monitor.py        # StrategyMonitorService（高冲突热点）
        │   ├── config.py / custom_signals*.py / composite.py / scoring.py
        │   ├── intraday_signals.py / prompt_builder.py / ai_generator.py
        │   ├── monitor_rules.py
        │   └── builtin/          # 18 个内置策略
        │
        ├── backtest/             # 回测引擎（高冲突热点 engine.py）
        │   ├── engine.py / strategy.py / matrix.py / optimizer.py
        │   ├── walkforward.py / minute_trigger.py / candidates.py
        │   ├── factor.py / fundamentals.py / worker.py
        │   ├── mining.py / mining_runtime.py / numba_runtime.py
        │   └── regime_alignment.py
        │
        ├── indicators/            # enriched 指标流水线
        │   ├── pipeline.py / levels.py / __init__.py
        │
        ├── jobs/                  # 定时任务
        │   └── daily_pipeline.py  # 盘后管道（日 K 同步 + enriched 落盘）
        │
        ├── extensions/             # 后端二开扩展机制
        │   ├── contracts.py       # BackendExtensionRegistrar / NotificationFormatter 契约
        │   ├── loader.py / registry.py / __init__.py
        │
        ├── plugins/               # 插件（stock-sdk 示例）
        │   └── stocksdk/ (Python + node bridge.mjs)
        │
        └── custom/                # 源码内二开模块（FastAPI 路由 + 启动钩子）
            ├── free_realtime_ext.py    # A 股免费实时行情 APIRouter + 测试页端点
            ├── easyquotation_t_signal.py
            └── _template.py.example
```

## 4. 关键约定与高冲突热点

### 4.1 时区与交易日

- `backend/app/markets.py` 定义 `CN_TZ = timezone(timedelta(hours=8))`，业务逻辑显式使用北京时间，服务器时区不得参与判断。
- 窗口、前 N 日、批次回算均按实际交易日，不得用自然日替代。
- 分钟 K 按 股票/ETF/指数 分目录存储与路由，不得仅凭代码格式猜资产类型。

### 4.2 数据契约（不可混用）

- `change_pct`：自定义实时数据源入口为小数制（`0.0366` = `3.66%`）；指数缓存存在百分数口径；enriched 内通常为小数制。跨边界必须显式转换，禁止「<1 就 ×100」启发式。
- `turnover_rate`：实时源入口为小数制；enriched 为百分数值（`5` = `5%`）。
- `open/high/low/close`：enriched 为前复权；`raw_close/raw_high/raw_low` 为不复权原始价。涨停/跌停判断必须基于原始价。
- 详见 `CONTRIBUTING.md` §3。

### 4.3 双接口模式（免费实时行情 mock）

`backend/app/data_providers/free_realtime/mock.py` 与 `services/free_realtime/scheduler.py` 实现「批量回填 + 流式增量」两阶段：

1. `MockProvider.backfill(...)`：一次性生成 9:15 到用户选择起始时间的全部历史 tick。
2. 流式接口按配置间隔逐个推送增量 tick，与历史段无缝衔接，时间轴无间隙无重叠。

Mock 与真实循环共用同一标的列表，由三个模块开关统一控制。

### 4.4 高冲突热点（直接修改时重点复核）

```
backend/app/main.py
backend/app/strategy/engine.py
backend/app/strategy/monitor.py
backend/app/backtest/engine.py
frontend/src/router.tsx
frontend/src/components/Layout.tsx
frontend/src/lib/api.ts
frontend/src/lib/queryKeys.ts
```

### 4.5 二开扩展点（已实现）

- 前端插槽：`layout.navigation.extra` / `stock-preview.footer` / `watchlist.toolbar`
- 前端路由/导航：`frontend/src/custom/<namespace>/extension.tsx`
- 后端路由/启动钩子：`backend/app/custom/<module>.py`（`registrar.include_router(router)` / `startup(context)`）
- 后端策略接口：`NotificationFormatter`（仅通知文案格式化，不改事件结构）
- 不要继承 `StrategyEngine` / `BacktestEngine` / `ScreenerService` / `StrategyMonitorService` / `DataStore` 等大型核心类。

## 5. 启动 / 测试 / 部署命令

> **本地 mac 开发一句话：直接 `./dev.sh`，不需要 Docker。**
> Docker 仅用于服务器部署 / CI，详见 §5.5（本地 mac 可跳过）。

### 5.1 开发模式（本地 mac 唯一推荐方式）

```bash
# 前置：Python ≥ 3.11 · Node ≥ 20 · uv · pnpm
cp .env.example .env          # 按需填 TICKFLOW_API_KEY / AI_API_KEY（留空 = None 模式）
./dev.sh                      # Windows: .\dev.ps1
# 后端 → http://localhost:3018（uvicorn --reload）
# 前端 → http://localhost:3011（pnpm dev）
# Ctrl-C 同时关闭两端
```

`dev.sh` 自动完成：依赖检查、占用端口 kill、`uv sync --frozen`（首次或换 extras 时）、`pnpm install`（首次）、前后端并行启动并加日志前缀。

环境变量覆盖：

```bash
BACKEND_PORT=8000 ./dev.sh
FRONTEND_PORT=5173 ./dev.sh
BACKEND_EXTRAS="backtest desktop" ./dev.sh
```

重建 venv（Apple Silicon 强制 arm64 native，避免 Polars 把 uvicorn worker 干成 zombie）：

```bash
cd backend
uv venv --python /Users/sampson/.local/share/uv/python/cpython-3.12-macos-aarch64-none/bin/python3.12 --clear
uv sync --frozen
```

### 5.2 单独起后端（联调/调试）

```bash
cd backend
uv run uvicorn app.main:app --env-file ../.env --reload --host 0.0.0.0 --port 3018
# 或 --no-sync 跳过依赖解析（仅用已装好的 .venv）
uv run --no-sync uvicorn app.main:app --env-file ../.env --reload --host 0.0.0.0 --port 3018
```

### 5.3 前端单独起

```bash
cd frontend
pnpm install   # 首次
pnpm dev --host 0.0.0.0 --port 3011
# 构建产物：frontend/dist
pnpm build
```

### 5.4 后端测试与 lint

```bash
cd backend
uv run pytest tests/path/to/test_x.py -q       # 定向测试
uv run pytest -q                                # 全量
uv run --frozen pytest tests/path/to/test_x.py -q  # 严格按 lockfile
uv run ruff check app/path.py tests/path.py     # lint
uv run ruff format app/path.py                  # 格式化
uv run mypy app/path.py                         # 类型检查（可选）
```

`pyproject.toml` 配置：`ruff line-length=100`、`target-version=py311`、`pytest asyncio_mode=auto`。

### 5.5 服务器部署 / CI（本地 mac 不用，仅远程场景）

> 本地 mac 开发**不使用** Docker，直接走 §5.1。本节仅用于服务器部署、CI 自构建、镜像分发等远程场景。

预构建镜像（服务器最快，无需本地编译）：

```bash
mkdir -p data
docker run -d --name tickflow \
  -p 3018:3018 \
  -e DATA_DIR=/app/data \
  -v "$PWD/data:/app/data" \
  ghcr.io/hzy1522/tickflow-stock-panel:latest
```

本地构建（含可选 stock-sdk 插件 + 国内镜像）：

```bash
cp .env.example .env
docker compose up --build
# 或带 extras / stock-sdk：
docker compose build --build-arg BACKEND_EXTRAS=backtest --build-arg INCLUDE_STOCKSDK=1
docker compose up
```

构建参数（见 Dockerfile 顶部 ARG）：

| ARG | 默认值 | 用途 |
| --- | --- | --- |
| `USE_CN_MIRROR` | `1` | 启用国内 npm/PyPI 镜像 |
| `INCLUDE_STOCKSDK` | `0` | 是否打包 stock-sdk 插件（合规风险，默认关闭） |
| `BACKEND_EXTRAS` | 空 | 额外 extras：`backtest` / `desktop` / `legacy-cpu` |
| `CODEX_CLI_VERSION` | `0.144.3` | 镜像内 Codex CLI 版本 |

容器内固定路径：`STATIC_DIR=/app/static` · `TIERS_YAML=/app/tiers.yaml` · `DATA_DIR=/app/data` · `TZ=Asia/Shanghai`。

### 5.6 提交前自检

```bash
cd backend && uv run ruff check . && uv run pytest tests/path -q
cd ../frontend && pnpm build
cd ..
git diff --check
git status --short --branch
```

## 6. 常用配置项（.env）

```ini
TICKFLOW_API_KEY=        # 留空 = None 模式（历史日 K 免费）；填 Key 解锁更多
AI_API_KEY=              # 留空 = 关闭 AI；填 Key 启用策略生成
PORT=3018                # 后端端口
HOST=0.0.0.0             # 后端监听地址
DATA_DIR=./data          # 开发模式相对路径；Docker 内固定为 /app/data
STATIC_DIR=../frontend/dist  # 开发模式；Docker 内为 /app/static
TIERS_YAML=../tiers.yaml     # Docker 内为 /app/tiers.yaml
AUTH_USERNAME=admin      # 默认登录用户名
# AUTH_PASSWORD=         # 必须在 .env 设置，无默认值；bootstrap_from_env 强制
# BACKEND_EXTRAS=        # 传给 uv sync --extra，如 "backtest desktop"
```

完整配置项见 [docs/configuration.md](docs/configuration.md)。

## 7. 验证矩阵（最小充分）

| 改动类型 | 最低验证 |
| --- | --- |
| 后端纯函数 / bug 修复 | 定向 `pytest`，含复现 + 边界用例 |
| API 契约 | service 测试 + API 测试（成功 / 无数据 / 错误响应） |
| 数据源 / 插件 | provider 契约测试、缺能力、空数据、字段/单位标准化 |
| 指标 / 复权 / 换手率 / 涨跌停 | 固定样本数值断言，覆盖历史边界 + 降级路径 |
| 策略 / 监控 | 参数变更、缓存失效、资产切换、重启/并发路径 |
| 回测 | 信号日 vs 成交日、T+1、费用、滑点、不可成交、数据缺口 |
| 缓存 / 性能 | 命中/失效测试、并发快照测试；性能主张附前后基准 |
| 前端组件 / 页面 | `pnpm build` + 手工检查加载/空/错/切换/实时刷新状态 |
| 前后端契约同变 | 后端定向测试 + 前端构建 + 对应页面联调 |
| 文件 / Docker / 跨平台 | 正常路径 + 权限/能力缺失 + 目标不可用失败路径 |

详见 `CONTRIBUTING.md` §9。

## 8. 文档导航

| 文档 | 内容 |
| --- | --- |
| [AGENTS.md](AGENTS.md) | AI 开发入口规则（必读） |
| [CONTRIBUTING.md](CONTRIBUTING.md) | 架构、数据契约、缓存、并发、测试矩阵、PR 复审 |
| [docs/secondary-development.md](docs/secondary-development.md) | 二次开发分级、前端插槽、后端策略接口、AI 工作流 |
| [docs/deployment.md](docs/deployment.md) | 部署方式（Dev / Docker / GH Actions）、老 CPU 兼容、访问密码 |
| [docs/configuration.md](docs/configuration.md) | `.env` 全量配置项 |
| [docs/features.md](docs/features.md) | 各功能模块详细说明 |
| [docs/custom-data-source.md](docs/custom-data-source.md) | 自定义数据源 YAML 接入 |
| [docs/strategy.md](docs/strategy.md) | 18 内置策略 + 三种扩展方式 |
| [docs/mining.md](docs/mining.md) | 因子与策略挖掘口径、防泄漏 |
| [docs/market-phase.md](docs/market-phase.md) | 情绪周期 6 阶段与主线识别 |
| [docs/plugin-development.md](docs/plugin-development.md) | 数据源插件开发规范 |
| [docs/upstream-merge.md](docs/upstream-merge.md) | 合并上游操作手册 |

## 9. 当前已落地的二开能力（仅供事实参考）

以下能力已实现且可用，新增同类需求时应优先复用而非平行实现：

- 多市场抽象层（`markets.py`）：A 股 / 港股 / 美股 symbol 解析、印花税、交易时段、T+N。
- 多市场管道复用：`POST /api/pipeline/run-market` 触发港股/美股日 K + enriched 落盘。
- 港美股指数：`/api/indices/market` 路由族 + `services/index_sync_market.py`。
- 强度梯队：`GET /api/screener/limit-ladder?market=hk|us` 替代 A 股连板语义。
- 市场环境/看板/AI 复盘：均按 `market` 参数路由，A 股默认 `cn` 行为不变。
- 免费实时行情（A 股）：`app/custom/free_realtime_ext.py` + `services/free_realtime/` + `data_providers/free_realtime/`，含 mock 双接口与单股超买超卖阈值。
