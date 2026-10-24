"""Tests for app.data_providers.free_realtime.registry."""
from __future__ import annotations

import pytest

from app.data_providers.free_realtime.eastmoney import EastmoneyProvider
from app.data_providers.free_realtime.mock import MockProvider
from app.data_providers.free_realtime.registry import (
    AVAILABLE_SOURCES,
    SOURCE_LABELS,
    get_provider,
)
from app.data_providers.free_realtime.sina import SinaProvider
from app.data_providers.free_realtime.tencent import TencentProvider


class TestRegistry:
    def test_available_sources(self):
        assert set(AVAILABLE_SOURCES) == {"sina", "tencent", "eastmoney", "mock"}

    def test_source_labels(self):
        assert SOURCE_LABELS["sina"] == "新浪财经"
        assert SOURCE_LABELS["tencent"] == "腾讯证券"
        assert SOURCE_LABELS["eastmoney"] == "东方财富"
        assert SOURCE_LABELS["mock"] == "模拟数据"

    def test_get_provider_sina(self):
        assert isinstance(get_provider("sina"), SinaProvider)

    def test_get_provider_tencent(self):
        assert isinstance(get_provider("tencent"), TencentProvider)

    def test_get_provider_eastmoney(self):
        assert isinstance(get_provider("eastmoney"), EastmoneyProvider)

    def test_get_provider_mock(self):
        assert isinstance(get_provider("mock"), MockProvider)

    def test_get_provider_unknown_raises(self):
        with pytest.raises(ValueError):
            get_provider("unknown_source")

    def test_singleton(self):
        """同一源多次调用返回同一实例。"""
        p1 = get_provider("sina")
        p2 = get_provider("sina")
        assert p1 is p2

    def test_all_providers_have_consistent_name(self):
        for src in AVAILABLE_SOURCES:
            provider = get_provider(src)
            assert provider.name == src
