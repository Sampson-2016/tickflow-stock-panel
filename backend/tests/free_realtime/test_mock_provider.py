"""Tests for app.data_providers.free_realtime.mock.MockProvider."""
from __future__ import annotations

import pytest

from app.data_providers.free_realtime.mock import MockProvider


def _make_provider(**kwargs) -> MockProvider:
    config = {
        "symbols": ["600000.SH"],
        "base_prices": {"600000.SH": 10.0},
        "names": {"600000.SH": "浦发银行"},
        "volatility_pct": 0.01,  # 1%
        "fail_rate": 0.0,
        "seed": 42,
        "interval_seconds": 1.0,
        "acceleration": 1,
    }
    config.update(kwargs)
    return MockProvider(config)


class TestMockProvider:
    def test_name(self):
        assert _make_provider().name == "mock"

    def test_get_realtime_basic(self):
        provider = _make_provider()
        df = provider.get_realtime(["600000.SH"])
        assert df.height == 1
        row = df.row(0, named=True)
        assert row["symbol"] == "600000.SH"
        assert row["name"] == "浦发银行"
        assert row["last_price"] is not None
        assert row["source"] == "mock"

    def test_empty_symbols(self):
        provider = _make_provider()
        assert provider.get_realtime([]).is_empty()

    def test_base_price_initial(self):
        """首次调用应以 base_price 为起点。"""
        provider = _make_provider(base_prices={"600000.SH": 10.0})
        df = provider.get_realtime(["600000.SH"])
        assert df.row(0, named=True)["last_price"] == pytest.approx(10.0)

    def test_random_walk_step_size(self):
        """单步变动 ≤ 区间 5%（volatility_pct=1% 时，|Δ| ≤ 0.01*price）。"""
        provider = _make_provider(
            base_prices={"600000.SH": 100.0}, volatility_pct=0.01, seed=7
        )
        prev = 100.0
        for _ in range(50):
            df = provider.get_realtime(["600000.SH"])
            cur = df.row(0, named=True)["last_price"]
            if prev is not None and prev > 0:
                # 变动幅度应 ≤ 5%（保守上限）
                assert abs(cur - prev) / prev <= 0.05
            prev = cur

    def test_deterministic_with_same_seed(self):
        """相同 seed 应产生确定性序列。"""
        p1 = _make_provider(seed=123)
        p2 = _make_provider(seed=123)
        seq1 = [p1.get_realtime(["600000.SH"]).row(0, named=True)["last_price"] for _ in range(10)]
        seq2 = [p2.get_realtime(["600000.SH"]).row(0, named=True)["last_price"] for _ in range(10)]
        assert seq1 == seq2

    def test_fail_injection(self):
        """fail_rate=1.0 时所有标的失败，返回空 DataFrame。"""
        provider = _make_provider(fail_rate=1.0)
        df = provider.get_realtime(["600000.SH"])
        assert df.is_empty()

    def test_partial_fail_injection(self):
        """fail_rate=0.5 时多次调用应有成功也有失败（统计性）。"""
        provider = _make_provider(fail_rate=0.5, seed=42)
        successes = 0
        for _ in range(200):
            df = provider.get_realtime(["600000.SH"])
            if not df.is_empty():
                successes += 1
        # 不是全失败也不是全成功
        assert 0 < successes < 200

    def test_unknown_symbol_uses_fallback_price(self):
        """未配置 base_price 的标的应用 10.0 作为兜底价。"""
        provider = MockProvider({
            "symbols": ["000001.SZ"],
            "base_prices": {},
            "names": {"000001.SZ": "平安银行"},
            "volatility_pct": 0.01,
            "fail_rate": 0.0,
            "seed": 1,
            "interval_seconds": 1.0,
            "acceleration": 1,
        })
        df = provider.get_realtime(["000001.SZ"])
        assert df.row(0, named=True)["last_price"] == pytest.approx(10.0)

    def test_multiple_symbols(self):
        provider = _make_provider(
            symbols=["600000.SH", "000001.SZ"],
            base_prices={"600000.SH": 10.0, "000001.SZ": 12.0},
            names={"600000.SH": "浦发银行", "000001.SZ": "平安银行"},
        )
        df = provider.get_realtime(["600000.SH", "000001.SZ"])
        assert df.height == 2
        assert set(df["symbol"].to_list()) == {"600000.SH", "000001.SZ"}
