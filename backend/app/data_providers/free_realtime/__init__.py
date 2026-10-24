"""A 股免费实时行情 provider 模块。

提供新浪/腾讯/东财三家免费实时行情源的统一封装，输出符合
FREE_REALTIME_SCHEMA 的 Polars DataFrame。

设计原则（对齐 CONTRIBUTING.md §4 数据源插件化）:
  - HTTP 调用只活在 provider 层
  - 上层服务依赖标准化的 pl.DataFrame
  - 异常 fail-closed：失败返回空 DataFrame，不抛出
"""
from __future__ import annotations

from app.data_providers.free_realtime.base import DEFAULT_TIMEOUT_SEC, FreeRealtimeProvider
from app.data_providers.free_realtime.eastmoney import EastmoneyProvider
from app.data_providers.free_realtime.mock import MockProvider
from app.data_providers.free_realtime.normalizer import (
    normalize_eastmoney,
    normalize_sina,
    normalize_tencent,
)
from app.data_providers.free_realtime.registry import (
    AVAILABLE_SOURCES,
    SOURCE_LABELS,
    get_provider,
    list_sources,
    reset_registry,
)
from app.data_providers.free_realtime.schemas import (
    FREE_REALTIME_COLUMNS,
    FREE_REALTIME_SCHEMA,
    empty_realtime_df,
    infer_asset_type,
)
from app.data_providers.free_realtime.sina import SinaProvider
from app.data_providers.free_realtime.tencent import TencentProvider

__all__ = [
    "AVAILABLE_SOURCES",
    "DEFAULT_TIMEOUT_SEC",
    "FREE_REALTIME_COLUMNS",
    "FREE_REALTIME_SCHEMA",
    "SOURCE_LABELS",
    "EastmoneyProvider",
    "FreeRealtimeProvider",
    "MockProvider",
    "SinaProvider",
    "TencentProvider",
    "empty_realtime_df",
    "get_provider",
    "infer_asset_type",
    "list_sources",
    "normalize_eastmoney",
    "normalize_sina",
    "normalize_tencent",
    "reset_registry",
]
