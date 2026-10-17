"""Tests for app.custom.easyquotation_t_signal.

策略:
  - 不依赖真实 easyquotation, 不依赖真实 webhook, 不依赖真实 watchlist / preferences。
  - 通过 monkeypatch / fixture 注入桩实现, 验证业务逻辑与边界。
  - SignalEvaluator 三件套规则必须有数值断言 (避免「返回了 N 条就 pass」的无效覆盖)。
"""
from __future__ import annotations

import json
import sys
import types
from datetime import datetime
from unittest.mock import MagicMock

import pytest

# ── fixtures ────────────────────────────────────────────────


@pytest.fixture
def fake_easyquotation(monkeypatch):
    """模拟 easyquotation 包, use('sina') 和 use('hkquote') 返回不同 mock 对象。

    返回 (cn_quotation, hk_quotation) 两个 MagicMock, 让 CNFetcher/HKFetcher 分别命中。
    """
    fake_pkg = types.ModuleType("easyquotation")
    cn_quotation = MagicMock(name="cn_sina")
    hk_quotation = MagicMock(name="hk_quote")

    def _use(name: str):
        if name == "sina":
            return cn_quotation
        if name == "hkquote":
            return hk_quotation
        return MagicMock()

    fake_pkg.use = MagicMock(side_effect=_use)
    monkeypatch.setitem(sys.modules, "easyquotation", fake_pkg)
    import app.custom.easyquotation_t_signal as mod
    monkeypatch.setattr(mod, "HAS_EASYQUOTATION", True)
    monkeypatch.setattr(mod, "easyquotation", fake_pkg)
    return cn_quotation, hk_quotation


@pytest.fixture
def tmp_data_dir(tmp_path, monkeypatch):
    """重定向 settings.data_dir 到 tmp_path, 隔离文件落盘。"""
    from app.config import settings
    monkeypatch.setattr(settings, "data_dir", tmp_path)
    return tmp_path


@pytest.fixture
def fresh_module(fake_easyquotation, tmp_data_dir, monkeypatch):
    """重置模块级单例, 让每个测试干净开始。"""
    import app.custom.easyquotation_t_signal as mod
    # 停掉上一个 fixture 创建的 POLLER 后台线程, 避免 daemon 线程在测试间残留
    try:
        old = mod.POLLER
        if old.running:
            old.stop()
    except Exception:
        pass
    monkeypatch.setattr(mod, "POLLER", mod.TSignalPoller())
    return mod


# ── helpers ────────────────────────────────────────────────


def _quote(symbol: str, **fields) -> dict:
    """构造一条 easyquotation 风格 quote dict。"""
    base = {"name": "测试股", "now": 10.0, "close": 10.0, "open": 10.0, "high": 10.5, "low": 9.8,
            "volume": 100000.0, "turnover": 10000,
            "bid1": 9.99, "bid1_volume": 100, "ask1": 10.01, "ask1_volume": 100}
    base.update(fields)
    return base


# ── 配置 ──────────────────────────────────────────────────


class TestConfig:
    def test_default_config_has_all_keys(self, fresh_module):
        cfg = fresh_module.load_config()
        assert set(cfg.keys()) == fresh_module.CONFIG_KEYS
        assert cfg["auto_push_to_feishu"] is True
        assert cfg["trading_hours_only"] is True
        assert cfg["price_change_pct_threshold"] == 3.0
        assert cfg["volume_ratio_threshold"] == 5.0
        assert cfg["big_order_imbalance_threshold"] == 0.4

    def test_save_config_persists_to_disk(self, fresh_module, tmp_data_dir):
        fresh_module.save_config({"auto_push_to_feishu": False, "interval_sec": 5})
        # 磁盘文件存在
        path = tmp_data_dir / "user_data" / "t_signal_config.json"
        assert path.exists()
        loaded = json.loads(path.read_text())
        assert loaded["auto_push_to_feishu"] is False
        assert loaded["interval_sec"] == 5
        # 未指定的字段保持默认
        assert loaded["price_change_pct_threshold"] == 3.0

    def test_save_config_rejects_unknown_keys_silently(self, fresh_module, tmp_data_dir):
        cleaned = fresh_module.save_config({"auto_push_to_feishu": False, "evil_field": "x"})
        assert "evil_field" not in cleaned
        assert cleaned["auto_push_to_feishu"] is False

    def test_save_config_validates_interval_sec(self, fresh_module):
        # 0 / 负数仍拒绝 (会让 sleep 死循环)
        with pytest.raises(ValueError, match="interval_sec"):
            fresh_module.save_config({"interval_sec": 0})
        with pytest.raises(ValueError, match="interval_sec"):
            fresh_module.save_config({"interval_sec": -1})
        # 上限放开: 任意 >= 1 都接受
        for sec in (1, 60, 120, 3600, 86400):
            cfg = fresh_module.save_config({"interval_sec": sec})
            assert cfg["interval_sec"] == sec

    def test_save_config_validates_thresholds(self, fresh_module):
        with pytest.raises(ValueError, match="price_change_pct_threshold"):
            fresh_module.save_config({"price_change_pct_threshold": 0})
        with pytest.raises(ValueError, match="volume_ratio_threshold"):
            fresh_module.save_config({"volume_ratio_threshold": -1})

    def test_save_config_validates_cooldown(self, fresh_module):
        with pytest.raises(ValueError, match="cooldown_seconds"):
            fresh_module.save_config({"cooldown_seconds": 9999})

    def test_save_config_validates_auto_push_type(self, fresh_module):
        with pytest.raises(ValueError, match="auto_push_to_feishu"):
            fresh_module.save_config({"auto_push_to_feishu": "yes"})


# ── Symbol 转换 ────────────────────────────────────────────


class TestSymbolConversion:
    def test_to_cn_key_sh(self, fresh_module):
        assert fresh_module.to_cn_key("600519.SH") == "sh600519"

    def test_to_cn_key_sz(self, fresh_module):
        assert fresh_module.to_cn_key("000001.SZ") == "sz000001"

    def test_to_cn_key_bj(self, fresh_module):
        assert fresh_module.to_cn_key("920344.BJ") == "bj920344"

    def test_to_cn_key_unsupported(self, fresh_module):
        assert fresh_module.to_cn_key("00700.HK") is None
        assert fresh_module.to_cn_key("AAPL.US") is None
        assert fresh_module.to_cn_key("600519") is None

    def test_to_hk_key(self, fresh_module):
        assert fresh_module.to_hk_key("00700.HK") == "00700"
        assert fresh_module.to_hk_key("09988.HK") == "09988"

    def test_to_hk_key_unsupported(self, fresh_module):
        assert fresh_module.to_hk_key("600519.SH") is None
        assert fresh_module.to_hk_key("AAPL.US") is None

    def test_is_a_stock(self, fresh_module):
        assert fresh_module.is_a_stock("600519.SH") is True
        assert fresh_module.is_a_stock("301666.SZ") is True
        assert fresh_module.is_a_stock("920344.BJ") is True
        assert fresh_module.is_a_stock("00700.HK") is False
        assert fresh_module.is_a_stock("AAPL.US") is False

    def test_is_hk_stock(self, fresh_module):
        assert fresh_module.is_hk_stock("00700.HK") is True
        assert fresh_module.is_hk_stock("09988.HK") is True
        assert fresh_module.is_hk_stock("600519.SH") is False
        assert fresh_module.is_hk_stock("AAPL.US") is False

    def test_is_supported_symbol(self, fresh_module):
        assert fresh_module.is_supported_symbol("600519.SH") is True
        assert fresh_module.is_supported_symbol("00700.HK") is True
        assert fresh_module.is_supported_symbol("AAPL.US") is False
        # 指数: 本期也接受 (走 A 股通道), 阈值在评估器内仍按 change_pct 算
        assert fresh_module.is_supported_symbol("000300.SH") is True


# ── QuoteFetcher ────────────────────────────────────────────


class TestQuoteFetcher:
    def test_fetch_empty_returns_empty(self, fresh_module):
        fetcher = fresh_module.QuoteFetcher()
        assert fetcher.fetch([]) == {}

    def test_fetch_returns_empty_when_easyquotation_missing(self, fresh_module, monkeypatch):
        monkeypatch.setattr(fresh_module, "HAS_EASYQUOTATION", False)
        fetcher = fresh_module.QuoteFetcher()
        # 强制重走 _ensure
        fetcher.cn._quotation = None
        fetcher.hk._quotation = None
        assert fetcher.fetch(["600519.SH", "00700.HK"]) == {}

    def test_fetch_converts_keys_and_remaps_response(self, fresh_module, fake_easyquotation):
        cn_q, _hk_q = fake_easyquotation
        fetcher = fresh_module.QuoteFetcher()
        cn_q.real.return_value = {
            "sh600519": _quote("600519", now=1500.0, name="贵州茅台"),
        }
        out = fetcher.fetch(["600519.SH"])
        assert "600519.SH" in out
        assert out["600519.SH"]["name"] == "贵州茅台"

    def test_fetch_routes_by_market(self, fresh_module, fake_easyquotation):
        """A 股走 sina, 港股走 hkquote, 美股被跳过。"""
        cn_q, hk_q = fake_easyquotation
        cn_q.real.return_value = {}
        hk_q.real.return_value = {}
        fetcher = fresh_module.QuoteFetcher()
        fetcher.fetch(["600519.SH", "00700.HK", "AAPL.US"])
        # CN sina 只拿到 A 股 key
        assert cn_q.real.call_args.args[0] == ["sh600519"]
        # HK hkquote 只拿到港股 key
        assert hk_q.real.call_args.args[0] == ["00700"]

    def test_fetch_hk_remaps_response(self, fresh_module, fake_easyquotation):
        """港股 quote 的 key 是纯数字 (无 sh/sz 前缀), 反向映射回项目格式。"""
        _, hk_q = fake_easyquotation
        hk_q.real.return_value = {
            "00700": {"name": "腾讯控股", "price": "413.20", "lastPrice": "419.20"},
        }
        fetcher = fresh_module.QuoteFetcher()
        out = fetcher.fetch(["00700.HK"])
        assert "00700.HK" in out
        assert out["00700.HK"]["name"] == "腾讯控股"

    def test_fetch_swallows_exception(self, fresh_module, fake_easyquotation):
        cn_q, _ = fake_easyquotation
        fetcher = fresh_module.QuoteFetcher()
        cn_q.real.side_effect = RuntimeError("network down")
        out = fetcher.fetch(["600519.SH"])
        assert out == {}
        assert "network down" in fetcher.last_error


# ── SignalEvaluator 三件套规则 ─────────────────────────────


class TestSignalEvaluator:
    def test_price_change_threshold_buy(self, fresh_module):
        ev = fresh_module.SignalEvaluator()
        # 涨 3.5%, 阈值 3.0% → 应触发
        quotes = {"600519.SH": _quote("600519", now=10.35, close=10.0)}
        sigs = ev.evaluate(quotes, fresh_module.DEFAULT_CONFIG, now_ms=1)
        kinds = [s.kind for s in sigs]
        assert fresh_module.KIND_PRICE in kinds
        s = next(s for s in sigs if s.kind == fresh_module.KIND_PRICE)
        assert s.severity == "high"
        assert s.change_pct == pytest.approx(3.5)

    def test_price_change_threshold_sell(self, fresh_module):
        ev = fresh_module.SignalEvaluator()
        quotes = {"600519.SH": _quote("600519", now=9.5, close=10.0)}
        sigs = ev.evaluate(quotes, fresh_module.DEFAULT_CONFIG, now_ms=1)
        s = next((s for s in sigs if s.kind == fresh_module.KIND_PRICE), None)
        assert s is not None
        assert s.change_pct < 0
        assert "下跌" in s.detail

    def test_price_change_below_threshold_no_signal(self, fresh_module):
        ev = fresh_module.SignalEvaluator()
        # 涨 2%, 阈值 3% → 不触发
        quotes = {"600519.SH": _quote("600519", now=10.2, close=10.0)}
        sigs = ev.evaluate(quotes, fresh_module.DEFAULT_CONFIG, now_ms=1)
        assert all(s.kind != fresh_module.KIND_PRICE for s in sigs)

    def test_volume_surge_after_window_fills(self, fresh_module):
        ev = fresh_module.SignalEvaluator()
        # 准备: 用 20 次「正常」成交量把窗口填满, 然后一次巨量
        normal = _quote("600519", now=10.0, close=10.0, volume=1000.0)
        for _ in range(ev.WINDOW):
            ev.evaluate({"600519.SH": normal}, fresh_module.DEFAULT_CONFIG, now_ms=1)
        # 窗口满后, 一次 10x 巨量
        surge = _quote("600519", now=10.0, close=10.0, volume=10000.0)
        sigs = ev.evaluate({"600519.SH": surge}, fresh_module.DEFAULT_CONFIG, now_ms=2)
        volume_sigs = [s for s in sigs if s.kind == fresh_module.KIND_VOLUME]
        assert len(volume_sigs) == 1
        assert "量比" in volume_sigs[0].detail

    def test_volume_surge_skipped_during_warmup(self, fresh_module):
        ev = fresh_module.SignalEvaluator()
        # 第 1 次就巨量 → 窗口未满 → 不应触发
        surge = _quote("600519", now=10.0, close=10.0, volume=99999.0)
        sigs = ev.evaluate({"600519.SH": surge}, fresh_module.DEFAULT_CONFIG, now_ms=1)
        assert all(s.kind != fresh_module.KIND_VOLUME for s in sigs)

    def test_big_order_imbalance_buy_pressure(self, fresh_module):
        ev = fresh_module.SignalEvaluator()
        # 买一是卖一的 5 倍 → 失衡度 0.8 > 0.4 → 触发
        quotes = {"600519.SH": _quote("600519", bid1_volume=500, ask1_volume=100)}
        sigs = ev.evaluate(quotes, fresh_module.DEFAULT_CONFIG, now_ms=1)
        s = next((s for s in sigs if s.kind == fresh_module.KIND_BIG_ORDER), None)
        assert s is not None
        assert "买盘强势" in s.detail
        assert "买入" in s.detail

    def test_big_order_imbalance_sell_pressure(self, fresh_module):
        ev = fresh_module.SignalEvaluator()
        # 卖一是买一的 5 倍
        quotes = {"600519.SH": _quote("600519", bid1_volume=100, ask1_volume=500)}
        sigs = ev.evaluate(quotes, fresh_module.DEFAULT_CONFIG, now_ms=1)
        s = next((s for s in sigs if s.kind == fresh_module.KIND_BIG_ORDER), None)
        assert s is not None
        assert "卖盘强势" in s.detail

    def test_big_order_balanced_no_signal(self, fresh_module):
        ev = fresh_module.SignalEvaluator()
        # 买一卖一相等 → 失衡度 0 → 不触发
        quotes = {"600519.SH": _quote("600519", bid1_volume=100, ask1_volume=100)}
        sigs = ev.evaluate(quotes, fresh_module.DEFAULT_CONFIG, now_ms=1)
        assert all(s.kind != fresh_module.KIND_BIG_ORDER for s in sigs)

    def test_no_price_change_no_signal(self, fresh_module):
        ev = fresh_module.SignalEvaluator()
        # 涨跌 0
        quotes = {"600519.SH": _quote("600519", now=10.0, close=10.0)}
        sigs = ev.evaluate(quotes, fresh_module.DEFAULT_CONFIG, now_ms=1)
        assert all(s.kind != fresh_module.KIND_PRICE for s in sigs)

    def test_missing_now_price_skips_evaluation(self, fresh_module):
        ev = fresh_module.SignalEvaluator()
        quotes = {"600519.SH": _quote("600519", now=None, close=10.0)}
        sigs = ev.evaluate(quotes, fresh_module.DEFAULT_CONFIG, now_ms=1)
        assert sigs == []

    def test_three_signals_can_coexist_for_same_symbol(self, fresh_module):
        ev = fresh_module.SignalEvaluator()
        # 把量比窗口填满
        normal = _quote("600519", now=10.35, close=10.0, volume=1000.0,
                        bid1_volume=500, ask1_volume=100)
        for _ in range(ev.WINDOW):
            ev.evaluate({"600519.SH": normal}, fresh_module.DEFAULT_CONFIG, now_ms=1)
        # 一次同时满足三个规则
        quotes = {"600519.SH": _quote("600519", now=10.35, close=10.0, volume=10000.0,
                                       bid1_volume=500, ask1_volume=100)}
        sigs = ev.evaluate(quotes, fresh_module.DEFAULT_CONFIG, now_ms=2)
        kinds = {s.kind for s in sigs}
        assert fresh_module.KIND_PRICE in kinds
        assert fresh_module.KIND_VOLUME in kinds
        assert fresh_module.KIND_BIG_ORDER in kinds

    def test_hk_price_change_threshold_buy(self, fresh_module):
        """港股: 涨 4% 触发涨跌幅阈值, 字段是 price/lastPrice (腾讯格式, 字符串)。"""
        ev = fresh_module.SignalEvaluator()
        # 腾讯 hkquote 返回字符串字段
        quotes = {"00700.HK": {
            "name": "腾讯控股", "price": "417.96", "lastPrice": "400.00",
            "openPrice": "402.00", "high": "420.00", "low": "398.00",
            "amount": "12345.0", "lotSize": "100", "time": "2026/09/29 10:30:00",
        }}
        sigs = ev.evaluate(quotes, fresh_module.DEFAULT_CONFIG, now_ms=1)
        kinds = [s.kind for s in sigs]
        assert fresh_module.KIND_PRICE in kinds
        # 其他规则港股跳过
        assert fresh_module.KIND_VOLUME not in kinds
        assert fresh_module.KIND_BIG_ORDER not in kinds
        s = next(s for s in sigs if s.kind == fresh_module.KIND_PRICE)
        assert s.change_pct == pytest.approx(4.49, rel=0.01)
        assert s.prev_close == 400.0
        assert s.price == pytest.approx(417.96)

    def test_hk_price_change_below_threshold_no_signal(self, fresh_module):
        """港股: 涨 1% 不触发 (阈值 3%)。"""
        ev = fresh_module.SignalEvaluator()
        quotes = {"00700.HK": {"name": "腾讯控股", "price": "404.00", "lastPrice": "400.00"}}
        sigs = ev.evaluate(quotes, fresh_module.DEFAULT_CONFIG, now_ms=1)
        assert sigs == []

    def test_hk_missing_last_price_no_signal(self, fresh_module):
        """港股: lastPrice 缺失 / 0 不评估, 不抛异常。"""
        ev = fresh_module.SignalEvaluator()
        for missing in [None, "", 0, "0", "abc"]:
            quotes = {"00700.HK": {"name": "腾讯控股", "price": "417.96", "lastPrice": missing}}
            sigs = ev.evaluate(quotes, fresh_module.DEFAULT_CONFIG, now_ms=1)
            assert sigs == [], f"lastPrice={missing!r} 应无信号, 实际 {sigs}"

    def test_hk_handles_string_numeric_fields(self, fresh_module):
        """港股字段是 str ('413.20'), 评估器要正确转 float。"""
        ev = fresh_module.SignalEvaluator()
        # 价格 500 港币 → 涨 25%, 远高于阈值
        quotes = {"00700.HK": {"name": "测试", "price": "500.00", "lastPrice": "400.00"}}
        sigs = ev.evaluate(quotes, fresh_module.DEFAULT_CONFIG, now_ms=1)
        s = next((x for x in sigs if x.kind == fresh_module.KIND_PRICE), None)
        assert s is not None
        assert s.change_pct == pytest.approx(25.0)
        assert s.price == 500.0

    def test_mixed_cn_and_hk_quotes_evaluated_separately(self, fresh_module):
        """A 股和港股混合时各自评估, 互不影响。"""
        ev = fresh_module.SignalEvaluator()
        cn_q = {"600519.SH": _quote("600519", now=10.5, close=10.0)}  # 涨 5%
        hk_q = {"00700.HK": {"name": "腾讯", "price": "415.0", "lastPrice": "400.0"}}  # 涨 3.75%
        sigs = ev.evaluate({**cn_q, **hk_q}, fresh_module.DEFAULT_CONFIG, now_ms=1)
        by_symbol = {s.symbol: s for s in sigs if s.kind == fresh_module.KIND_PRICE}
        assert "600519.SH" in by_symbol
        assert "00700.HK" in by_symbol
        # 港股不应该出现 KIND_VOLUME / KIND_BIG_ORDER
        assert all(s.kind == fresh_module.KIND_PRICE for s in sigs if s.symbol.endswith(".HK"))


# ── CooldownTracker ────────────────────────────────────────


class TestCooldown:
    def test_allow_first_time(self, fresh_module):
        cd = fresh_module.CooldownTracker()
        assert cd.allow("600519.SH", fresh_module.KIND_PRICE, cooldown_sec=60) is True

    def test_block_second_time_within_window(self, fresh_module):
        cd = fresh_module.CooldownTracker()
        cd.allow("600519.SH", fresh_module.KIND_PRICE, cooldown_sec=60)
        assert cd.allow("600519.SH", fresh_module.KIND_PRICE, cooldown_sec=60) is False

    def test_different_kind_not_blocked(self, fresh_module):
        cd = fresh_module.CooldownTracker()
        cd.allow("600519.SH", fresh_module.KIND_PRICE, cooldown_sec=60)
        # 不同 kind 不冲突
        assert cd.allow("600519.SH", fresh_module.KIND_VOLUME, cooldown_sec=60) is True

    def test_different_symbol_not_blocked(self, fresh_module):
        cd = fresh_module.CooldownTracker()
        cd.allow("600519.SH", fresh_module.KIND_PRICE, cooldown_sec=60)
        assert cd.allow("000001.SZ", fresh_module.KIND_PRICE, cooldown_sec=60) is True

    def test_zero_cooldown_always_allows(self, fresh_module):
        cd = fresh_module.CooldownTracker()
        for _ in range(5):
            assert cd.allow("600519.SH", fresh_module.KIND_PRICE, cooldown_sec=0) is True


# ── SignalStore ────────────────────────────────────────────


class TestSignalStore:
    def test_append_and_list_recent(self, fresh_module, tmp_data_dir):
        store = fresh_module.SignalStore(tmp_data_dir, max_records=10)
        sig = fresh_module.TSignal(
            kind=fresh_module.KIND_PRICE, symbol="600519.SH", name="茅台",
            ts_ms=1000, price=10.0, prev_close=9.5, change_pct=5.0,
            detail="test", severity="high",
        )
        store.append(sig)
        items = store.list_recent(limit=10)
        assert len(items) == 1
        assert items[0]["symbol"] == "600519.SH"
        # JSONL 落盘
        path = tmp_data_dir / "user_data" / "t_signals.jsonl"
        assert path.exists()
        lines = path.read_text(encoding="utf-8").strip().splitlines()
        assert len(lines) == 1

    def test_list_recent_returns_newest_first(self, fresh_module, tmp_data_dir):
        store = fresh_module.SignalStore(tmp_data_dir, max_records=10)
        for i in range(3):
            sig = fresh_module.TSignal(
                kind=fresh_module.KIND_PRICE, symbol=f"S{i}", name=f"n{i}",
                ts_ms=1000 + i, price=10.0, prev_close=9.5, change_pct=5.0,
                detail="x", severity="high",
            )
            store.append(sig)
        items = store.list_recent(limit=10)
        # 最新 (ts_ms=1002) 在前
        assert items[0]["ts_ms"] == 1002
        assert items[-1]["ts_ms"] == 1000

    def test_list_recent_respects_limit(self, fresh_module, tmp_data_dir):
        store = fresh_module.SignalStore(tmp_data_dir, max_records=10)
        for i in range(5):
            sig = fresh_module.TSignal(
                kind=fresh_module.KIND_PRICE, symbol=f"S{i}", name=f"n{i}",
                ts_ms=1000 + i, price=10.0, prev_close=9.5, change_pct=5.0,
                detail="x", severity="high",
            )
            store.append(sig)
        assert len(store.list_recent(limit=2)) == 2


# ── POLLER 状态 + API 端点 ────────────────────────────────


class TestPollerAndAPI:
    def test_status_shape(self, fresh_module):
        s = fresh_module.POLLER.status()
        assert set(s.keys()) == {
            "running", "interval_sec", "last_fetch_ms",
            "last_fetch_error", "signals_today", "position_count",
            "enabled_position_count", "auto_push_to_feishu",
            "easyquotation_installed", "feishu_webhook_configured",
        }
        assert s["running"] is False
        assert s["easyquotation_installed"] is True
        assert s["position_count"] == 0
        assert s["enabled_position_count"] == 0

    def test_start_requires_easyquotation(self, fresh_module, monkeypatch):
        monkeypatch.setattr(fresh_module, "HAS_EASYQUOTATION", False)
        with pytest.raises(RuntimeError, match="easyquotation 未安装"):
            fresh_module.POLLER.start()

    def test_start_stop_cycle(self, fresh_module):
        result = fresh_module.POLLER.start()
        assert result["ok"] is True
        assert fresh_module.POLLER.running is True
        # 二次 start 应答 already_running
        result2 = fresh_module.POLLER.start()
        assert result2.get("already_running") is True
        # stop
        stop_result = fresh_module.POLLER.stop()
        assert stop_result["ok"] is True
        assert fresh_module.POLLER.running is False

    def test_start_then_stop_returns_to_not_running(self, fresh_module):
        fresh_module.POLLER.start()
        fresh_module.POLLER.stop()
        assert fresh_module.POLLER.running is False


# ── 集成: FastAPI 路由 (用 TestClient, 隔离 settings) ─────────


class TestFastAPIRoutes:
    def _client(self, fresh_module):
        from fastapi import FastAPI
        from fastapi.testclient import TestClient

        app = FastAPI()
        app.include_router(fresh_module.router)
        return TestClient(app)

    def test_get_status(self, fresh_module):
        client = self._client(fresh_module)
        r = client.get("/api/custom/t-signal/status")
        assert r.status_code == 200
        body = r.json()
        assert "running" in body
        assert "easyquotation_installed" in body

    def test_get_config_returns_defaults(self, fresh_module):
        client = self._client(fresh_module)
        r = client.get("/api/custom/t-signal/config")
        assert r.status_code == 200
        cfg = r.json()
        assert cfg["price_change_pct_threshold"] == 3.0

    def test_put_config_updates_and_returns_full(self, fresh_module):
        client = self._client(fresh_module)
        r = client.put("/api/custom/t-signal/config", json={"auto_push_to_feishu": False, "interval_sec": 5})
        assert r.status_code == 200
        cfg = r.json()
        assert cfg["auto_push_to_feishu"] is False
        assert cfg["interval_sec"] == 5
        # 其他字段保留默认
        assert cfg["price_change_pct_threshold"] == 3.0

    def test_put_config_rejects_invalid_value(self, fresh_module):
        client = self._client(fresh_module)
        r = client.put("/api/custom/t-signal/config", json={"interval_sec": -1})
        assert r.status_code == 400

    def test_put_config_drops_unknown_keys(self, fresh_module):
        client = self._client(fresh_module)
        r = client.put("/api/custom/t-signal/config", json={"auto_push_to_feishu": False, "injected": "x"})
        assert r.status_code == 200
        cfg = r.json()
        assert "injected" not in cfg

    def test_post_start_returns_503_when_easyquotation_missing(self, fresh_module, monkeypatch):
        monkeypatch.setattr(fresh_module, "HAS_EASYQUOTATION", False)
        client = self._client(fresh_module)
        r = client.post("/api/custom/t-signal/start")
        assert r.status_code == 503

    def test_post_start_stop(self, fresh_module):
        client = self._client(fresh_module)
        r = client.post("/api/custom/t-signal/start")
        assert r.status_code == 200
        assert fresh_module.POLLER.running is True
        # stop
        r = client.post("/api/custom/t-signal/stop")
        assert r.status_code == 200
        assert fresh_module.POLLER.running is False

    def test_get_recent_empty(self, fresh_module):
        client = self._client(fresh_module)
        r = client.get("/api/custom/t-signal/recent")
        assert r.status_code == 200
        body = r.json()
        assert body["items"] == []
        assert body["count"] == 0


# ── 软依赖: easyquotation 缺失时不破坏模块加载 ───────────────


class TestMissingDependency:
    def test_module_loads_without_easyquotation(self, monkeypatch):
        """easyquotation 缺失时, 模块仍能正常 import 且 HAS_EASYQUOTATION=False。"""
        # 移除 sys.modules 中的 easyquotation 缓存
        monkeypatch.delitem(sys.modules, "easyquotation", raising=False)
        # 重新 import 模块
        if "app.custom.easyquotation_t_signal" in sys.modules:
            monkeypatch.delitem(sys.modules, "app.custom.easyquotation_t_signal")
        import app.custom.easyquotation_t_signal as mod

        # 模拟 easyquotation 不可用
        monkeypatch.setattr(mod, "HAS_EASYQUOTATION", False)
        assert mod.HAS_EASYQUOTATION is False
        # 路由仍可注册
        from fastapi import FastAPI
        app = FastAPI()
        app.include_router(mod.router)
        # status 端点可见 easyquotation_installed=False
        from fastapi.testclient import TestClient
        client = TestClient(app)
        r = client.get("/api/custom/t-signal/status")
        assert r.status_code == 200
        assert r.json()["easyquotation_installed"] is False


# ── 集成: load_config / save_config 落盘一致性 ──────────────


class TestConfigPersistence:
    def test_corrupted_config_falls_back_to_defaults(self, fresh_module, tmp_data_dir):
        path = tmp_data_dir / "user_data" / "t_signal_config.json"
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_text("not valid json {{{", encoding="utf-8")
        cfg = fresh_module.load_config()
        # 不抛异常, 返回默认值
        assert cfg["auto_push_to_feishu"] is True

    def test_partial_config_merges_with_defaults(self, fresh_module, tmp_data_dir):
        path = tmp_data_dir / "user_data" / "t_signal_config.json"
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_text(json.dumps({"auto_push_to_feishu": False}), encoding="utf-8")
        cfg = fresh_module.load_config()
        assert cfg["auto_push_to_feishu"] is False
        # 其他字段是默认
        assert cfg["interval_sec"] == 3
        assert cfg["price_change_pct_threshold"] == 3.0


# ── 持仓 T 列表 (PositionTStore) ──────────────────────────────


class TestPositionTStore:
    def test_empty_init(self, fresh_module):
        store = fresh_module.PositionTStore()
        assert store.list_all() == []
        assert store.list_enabled_symbols() == []

    def test_add_creates_entry(self, fresh_module):
        store = fresh_module.PositionTStore()
        entry = store.add("300308.SZ", name="中际旭创")
        assert entry["symbol"] == "300308.SZ"
        assert entry["name"] == "中际旭创"
        assert entry["enabled"] is True
        assert entry["added_at"] != ""

    def test_add_uppercases_symbol(self, fresh_module):
        store = fresh_module.PositionTStore()
        entry = store.add("300308.sz")
        assert entry["symbol"] == "300308.SZ"

    def test_add_idempotent(self, fresh_module):
        """重复添加返回现有条目, 不创建副本。"""
        store = fresh_module.PositionTStore()
        e1 = store.add("300308.SZ", name="A")
        e2 = store.add("300308.SZ", name="B")  # name 被忽略
        assert e1["symbol"] == e2["symbol"]
        assert len(store.list_all()) == 1
        # 名字保留首次添加的 (不会因二次 add 覆盖)
        assert store.get("300308.SZ")["name"] == "A"

    def test_add_rejects_unsupported_market(self, fresh_module):
        store = fresh_module.PositionTStore()
        with pytest.raises(ValueError, match="不支持的市场"):
            store.add("AAPL.US")
        with pytest.raises(ValueError, match="不支持的市场"):
            store.add("600519")  # 无后缀, 不被识别

    def test_remove(self, fresh_module):
        store = fresh_module.PositionTStore()
        store.add("300308.SZ")
        assert store.remove("300308.SZ") is True
        assert store.list_all() == []
        # 二次 remove 返回 False
        assert store.remove("300308.SZ") is False

    def test_remove_nonexistent(self, fresh_module):
        store = fresh_module.PositionTStore()
        assert store.remove("999999.SH") is False

    def test_set_enabled(self, fresh_module):
        store = fresh_module.PositionTStore()
        store.add("300308.SZ")
        result = store.set_enabled("300308.SZ", False)
        assert result is not None
        assert result["enabled"] is False
        # enabled=False 不出现在 list_enabled_symbols
        assert store.list_enabled_symbols() == []

    def test_set_enabled_nonexistent_returns_none(self, fresh_module):
        store = fresh_module.PositionTStore()
        assert store.set_enabled("999999.SH", True) is None

    def test_list_enabled_symbols_filters_disabled(self, fresh_module):
        store = fresh_module.PositionTStore()
        store.add("300308.SZ")  # 默认 enabled=True
        store.add("688498.SH")
        store.set_enabled("688498.SH", False)
        assert store.list_enabled_symbols() == ["300308.SZ"]

    def test_persists_to_disk(self, fresh_module, tmp_data_dir):
        """添加 → 重建 store → 数据还在 (进程重启场景)。"""
        s1 = fresh_module.PositionTStore()
        s1.add("300308.SZ", name="中际旭创")
        s1.add("00700.HK", name="腾讯")

        s2 = fresh_module.PositionTStore()
        symbols = sorted(e["symbol"] for e in s2.list_all())
        assert symbols == ["00700.HK", "300308.SZ"]
        assert s2.get("300308.SZ")["name"] == "中际旭创"


# ── IntradayKlineStore ────────────────────────────────────────


class TestIntradayKlineStore:
    def test_empty_init(self, fresh_module):
        store = fresh_module.IntradayKlineStore()
        assert store.list_today("300308.SZ") == []

    def test_append_and_list(self, fresh_module):
        store = fresh_module.IntradayKlineStore()
        # 用 1738000000000 (2025-01-27 09:30 UTC+8) 避免跨日
        ts = int(datetime(2025, 1, 27, 9, 30, 0).timestamp() * 1000)
        store.append("300308.SZ", ts, 100.0, 50000.0)
        bars = store.list_today("300308.SZ")
        assert len(bars) == 1
        assert bars[0]["price"] == 100.0
        assert bars[0]["volume"] == 50000.0

    def test_append_merges_same_minute(self, fresh_module):
        """同一分钟内多次 append → 覆盖 (而非追加), 避免重复点。"""
        store = fresh_module.IntradayKlineStore()
        ts1 = int(datetime(2025, 1, 27, 9, 30, 0).timestamp() * 1000)
        ts2 = ts1 + 5000  # 同分钟
        ts3 = ts1 + 30_000  # 同分钟
        store.append("300308.SZ", ts1, 100.0)
        store.append("300308.SZ", ts2, 100.5)
        store.append("300308.SZ", ts3, 101.0)
        bars = store.list_today("300308.SZ")
        assert len(bars) == 1
        assert bars[0]["price"] == 101.0
        assert bars[0]["ts_ms"] == ts3  # 最新时间戳

    def test_append_different_minutes_appends(self, fresh_module):
        store = fresh_module.IntradayKlineStore()
        ts1 = int(datetime(2025, 1, 27, 9, 30, 0).timestamp() * 1000)
        ts2 = int(datetime(2025, 1, 27, 9, 31, 0).timestamp() * 1000)
        store.append("300308.SZ", ts1, 100.0)
        store.append("300308.SZ", ts2, 101.0)
        assert len(store.list_today("300308.SZ")) == 2

    def test_ignores_zero_or_negative_price(self, fresh_module):
        store = fresh_module.IntradayKlineStore()
        ts = int(datetime(2025, 1, 27, 9, 30, 0).timestamp() * 1000)
        store.append("300308.SZ", ts, 0.0)
        store.append("300308.SZ", ts, -1.0)
        store.append("300308.SZ", ts, None)
        assert store.list_today("300308.SZ") == []

    def test_separate_symbols(self, fresh_module):
        store = fresh_module.IntradayKlineStore()
        ts = int(datetime(2025, 1, 27, 9, 30, 0).timestamp() * 1000)
        store.append("300308.SZ", ts, 100.0)
        store.append("688498.SH", ts, 200.0)
        assert len(store.list_today("300308.SZ")) == 1
        assert len(store.list_today("688498.SH")) == 1


# ── 新增 API 路由 ─────────────────────────────────────────────


class TestPositionRoutes:
    def _client(self, fresh_module):
        from fastapi import FastAPI
        from fastapi.testclient import TestClient

        app = FastAPI()
        app.include_router(fresh_module.router)
        return TestClient(app)

    def test_list_positions_empty(self, fresh_module):
        r = self._client(fresh_module).get("/api/custom/t-signal/positions")
        assert r.status_code == 200
        body = r.json()
        assert body == {"items": [], "count": 0}

    def test_add_then_list(self, fresh_module):
        client = self._client(fresh_module)
        r = client.post("/api/custom/t-signal/positions", json={"symbol": "300308.SZ", "name": "中际旭创"})
        assert r.status_code == 200
        body = r.json()
        assert body["symbol"] == "300308.SZ"
        assert body["enabled"] is True
        # 重复 add 幂等
        r2 = client.post("/api/custom/t-signal/positions", json={"symbol": "300308.SZ", "name": "X"})
        assert r2.status_code == 200
        # 列表只有 1 个
        r3 = client.get("/api/custom/t-signal/positions")
        assert r3.json()["count"] == 1

    def test_add_rejects_unsupported(self, fresh_module):
        client = self._client(fresh_module)
        r = client.post("/api/custom/t-signal/positions", json={"symbol": "AAPL.US"})
        assert r.status_code == 400

    def test_remove_position(self, fresh_module):
        client = self._client(fresh_module)
        client.post("/api/custom/t-signal/positions", json={"symbol": "300308.SZ"})
        r = client.delete("/api/custom/t-signal/positions/300308.SZ")
        assert r.status_code == 200
        assert client.get("/api/custom/t-signal/positions").json()["count"] == 0

    def test_remove_nonexistent_returns_404(self, fresh_module):
        client = self._client(fresh_module)
        r = client.delete("/api/custom/t-signal/positions/999999.SH")
        assert r.status_code == 404

    def test_update_position_enabled(self, fresh_module):
        client = self._client(fresh_module)
        client.post("/api/custom/t-signal/positions", json={"symbol": "300308.SZ"})
        r = client.put("/api/custom/t-signal/positions/300308.SZ", json={"enabled": False})
        assert r.status_code == 200
        assert r.json()["enabled"] is False

    def test_update_rejects_non_bool_enabled(self, fresh_module):
        client = self._client(fresh_module)
        client.post("/api/custom/t-signal/positions", json={"symbol": "300308.SZ"})
        r = client.put("/api/custom/t-signal/positions/300308.SZ", json={"enabled": "yes"})
        assert r.status_code == 400

    def test_intraday_endpoint_empty(self, fresh_module):
        client = self._client(fresh_module)
        r = client.get("/api/custom/t-signal/positions/300308.SZ/intraday")
        assert r.status_code == 200
        body = r.json()
        assert body["symbol"] == "300308.SZ"
        assert body["bars"] == []
        assert body["count"] == 0

    def test_signals_endpoint_filters_by_symbol(self, fresh_module):
        client = self._client(fresh_module)
        # 往 store 写两条不同 symbol 的信号
        store = fresh_module.POLLER.signal_store()
        for sym in ("300308.SZ", "688498.SH"):
            sig = fresh_module.TSignal(
                kind=fresh_module.KIND_PRICE, symbol=sym, name="X",
                ts_ms=1000, price=10.0, prev_close=9.5, change_pct=5.0,
                detail="x", severity="high",
            )
            store.append(sig)
        r = client.get("/api/custom/t-signal/positions/300308.SZ/signals")
        assert r.status_code == 200
        body = r.json()
        assert body["count"] == 1
        assert body["items"][0]["symbol"] == "300308.SZ"

    def test_recent_filters_by_symbol(self, fresh_module):
        client = self._client(fresh_module)
        store = fresh_module.POLLER.signal_store()
        for sym in ("300308.SZ", "688498.SH"):
            sig = fresh_module.TSignal(
                kind=fresh_module.KIND_PRICE, symbol=sym, name="X",
                ts_ms=1000, price=10.0, prev_close=9.5, change_pct=5.0,
                detail="x", severity="high",
            )
            store.append(sig)
        r = client.get("/api/custom/t-signal/recent?symbol=688498.SH")
        assert r.status_code == 200
        body = r.json()
        assert body["count"] == 1
        assert body["items"][0]["symbol"] == "688498.SH"


# ── Poller 行为: 用 PositionTStore 替代 watchlist ────────────


class TestPollerUsesPositionList:
    def test_tick_skips_when_no_positions(self, fresh_module):
        """空 position list → _tick 直接 return, 不调 fetcher。"""
        poller = fresh_module.POLLER
        # 直接调 _tick, 不该有拉取
        poller._tick()
        assert poller._fetcher.last_fetch_ms == 0

    def test_tick_only_polls_enabled_positions(self, fresh_module, fake_easyquotation, monkeypatch):
        cn_q, _hk_q = fake_easyquotation
        cn_q.real.return_value = {}
        # 关闭交易时段限制 (测试机器可能不在 A 股 / 港股交易时间)
        monkeypatch.setattr(fresh_module, "is_trading_now", lambda m, dt=None: True)
        poller = fresh_module.POLLER
        poller.positions().add("600519.SH", name="贵州茅台")
        poller.positions().add("688498.SH", name="源杰")
        poller.positions().add("999999.SH", name="假票")
        poller.positions().set_enabled("999999.SH", False)
        poller._tick()
        called = cn_q.real.call_args.args[0]
        assert "sh600519" in called
        assert "sh688498" in called
        assert "sh999999" not in called