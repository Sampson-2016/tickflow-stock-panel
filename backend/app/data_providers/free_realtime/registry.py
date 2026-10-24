"""免费实时行情 provider 注册表。

生产环境用工厂函数按 name 创建 provider 单例。单测可直接 new 具体
provider 并注入 mock httpx.Client，不走此注册表。

mock 源仅用于测试页，通过 set_config 配置后使用。
"""
from __future__ import annotations

from app.data_providers.free_realtime.base import FreeRealtimeProvider
from app.data_providers.free_realtime.eastmoney import EastmoneyProvider
from app.data_providers.free_realtime.mock import MockProvider
from app.data_providers.free_realtime.sina import SinaProvider
from app.data_providers.free_realtime.tencent import TencentProvider

# 可用源列表（顺序无关，调度器按用户配置的顺序轮换）
AVAILABLE_SOURCES: tuple[str, ...] = ("sina", "tencent", "eastmoney", "mock")

# 源中文名（前端展示用）
SOURCE_LABELS: dict[str, str] = {
    "sina": "新浪财经",
    "tencent": "腾讯证券",
    "eastmoney": "东方财富",
    "mock": "模拟数据",
}

# 生产源（不含 mock）
PRODUCTION_SOURCES: tuple[str, ...] = ("sina", "tencent", "eastmoney")

# 单例缓存（provider 无状态，除 httpx.Client 外）
_instances: dict[str, FreeRealtimeProvider] = {}


def get_provider(name: str) -> FreeRealtimeProvider:
    """按 name 获取 provider 单例。

    Args:
        name: sina / tencent / eastmoney / mock

    Raises:
        ValueError: 未知源名。
    """
    if name in _instances:
        return _instances[name]
    if name == "sina":
        provider: FreeRealtimeProvider = SinaProvider()
    elif name == "tencent":
        provider = TencentProvider()
    elif name == "eastmoney":
        provider = EastmoneyProvider()
    elif name == "mock":
        provider = MockProvider()
    else:
        raise ValueError(
            f"unknown free realtime source: {name} (available: {AVAILABLE_SOURCES})"
        )
    _instances[name] = provider
    return provider


def reset_registry() -> None:
    """清空单例缓存（单测隔离用）。"""
    _instances.clear()


def list_sources() -> list[str]:
    """返回可用源列表。"""
    return list(AVAILABLE_SOURCES)
