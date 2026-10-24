"""Tests for app.data_providers.free_realtime.normalizer.

P0 重点：单位转换口径断言（避免百分数/小数、股/手混用）:
  - 新浪 volume 是"股"，/100 转"手"
  - 东财价格字段是"分"，/100 转"元"
  - 东财 f170 涨跌幅是百分数，/100 转小数制
  - 新浪/腾讯 change_pct 自算且为小数制
  - 新浪卖1量在索引20（不是22），日期在30（不是29）
"""
from __future__ import annotations

import json

import pytest

from app.data_providers.free_realtime.normalizer import (
    normalize_eastmoney,
    normalize_sina,
    normalize_tencent,
    project_symbol_to_eastmoney_secid,
    project_symbol_to_sina_key,
    sina_key_to_symbol,
)
from app.data_providers.free_realtime.schemas import FREE_REALTIME_COLUMNS

# ── 新浪样本 ────────────────────────────────────────────────
# 33 字段（含五档买卖盘），浦发银行 600000.SH
_SINA_SAMPLE = (
    'var hq_str_sh600000="浦发银行,10.500,10.400,10.450,10.600,10.300,'
    '10.450,10.460,12345600,108000000,'
    '200,10.450,300,10.440,400,10.430,500,10.420,600,10.410,'
    '700,10.470,800,10.480,900,10.490,1000,10.500,1100,10.510,'
    '2024-01-02,15:00:00,00";'
)


class TestNormalizeSina:
    def test_basic_fields(self):
        df = normalize_sina(_SINA_SAMPLE)
        assert df.height == 1
        row = df.row(0, named=True)
        assert row["symbol"] == "600000.SH"
        assert row["name"] == "浦发银行"
        assert row["last_price"] == pytest.approx(10.45)
        assert row["open"] == pytest.approx(10.50)
        assert row["prev_close"] == pytest.approx(10.40)
        assert row["high"] == pytest.approx(10.60)
        assert row["low"] == pytest.approx(10.30)

    def test_volume_shares_to_hands(self):
        """P0: 新浪成交量是"股"，/100 转"手"。"""
        df = normalize_sina(_SINA_SAMPLE)
        row = df.row(0, named=True)
        assert row["volume"] == pytest.approx(123456.0)  # 12345600 股 → 123456 手

    def test_bid_ask_volume_shares_to_hands(self):
        """P0: 新浪买卖量也是"股"，/100 转"手"。买1量=200股→2手, 卖1量=700股→7手。"""
        df = normalize_sina(_SINA_SAMPLE)
        row = df.row(0, named=True)
        assert row["bid1_volume"] == pytest.approx(2.0)    # 200 股 → 2 手
        assert row["ask1_volume"] == pytest.approx(7.0)    # 700 股 → 7 手

    def test_change_pct_decimal(self):
        """P0: change_pct 是小数制（不是百分数）。
        (10.45-10.40)/10.40 = 0.004807... 表示 0.48%。
        """
        df = normalize_sina(_SINA_SAMPLE)
        row = df.row(0, named=True)
        assert row["change_pct"] == pytest.approx((10.45 - 10.40) / 10.40, rel=1e-6)
        # 确保不是百分数 0.48
        assert row["change_pct"] < 0.01

    def test_bid_ask_price(self):
        df = normalize_sina(_SINA_SAMPLE)
        row = df.row(0, named=True)
        assert row["bid1_price"] == pytest.approx(10.45)
        assert row["ask1_price"] == pytest.approx(10.46)

    def test_source_column(self):
        df = normalize_sina(_SINA_SAMPLE)
        assert df.row(0, named=True)["source"] == "sina"

    def test_ts_ms_and_trade_date(self):
        df = normalize_sina(_SINA_SAMPLE)
        row = df.row(0, named=True)
        # 2024-01-02 15:00:00 北京时间
        assert row["trade_date"] is not None
        assert row["ts_ms"] is not None

    def test_empty_input(self):
        assert normalize_sina("").is_empty()

    def test_malformed_input(self):
        assert normalize_sina('garbage text').is_empty()

    def test_multiple_symbols(self):
        text = _SINA_SAMPLE + (
            'var hq_str_sz000001="平安银行,12.0,11.8,12.1,12.2,11.9,'
            '12.1,12.11,5000,60000,100,12.1,200,12.0,'
            '300,11.9,400,11.8,500,11.7,'
            '600,12.12,700,12.13,800,12.14,900,12.15,1000,12.16,'
            '2024-01-02,15:00:00,00";'
        )
        df = normalize_sina(text)
        assert df.height == 2
        syms = df["symbol"].to_list()
        assert "600000.SH" in syms
        assert "000001.SZ" in syms

    def test_columns_match_schema(self):
        df = normalize_sina(_SINA_SAMPLE)
        assert df.columns == FREE_REALTIME_COLUMNS


# ── 腾讯样本 ────────────────────────────────────────────────
# 46 字段，浦发银行 600000.SH
_TENCENT_SAMPLE = (
    'v_sh600000="1~浦发银行~600000~10.450~10.400~10.500~10.600~10.300~'
    '10.450~10.460~123456~108000000~'
    '200~10.450~300~10.440~400~10.430~500~10.420~600~10.410~'
    '700~10.470~800~10.480~900~10.490~1000~10.500~1100~10.510~'
    '20240102~15:00:00~0~0~0~0~0~0~0~0~0~0~0~20240102150000~";'
)


class TestNormalizeTencent:
    def test_basic_fields(self):
        df = normalize_tencent(_TENCENT_SAMPLE)
        assert df.height == 1
        row = df.row(0, named=True)
        assert row["symbol"] == "600000.SH"
        assert row["name"] == "浦发银行"
        assert row["last_price"] == pytest.approx(10.45)
        assert row["open"] == pytest.approx(10.50)
        assert row["prev_close"] == pytest.approx(10.40)
        assert row["high"] == pytest.approx(10.60)
        assert row["low"] == pytest.approx(10.30)

    def test_volume_already_hands(self):
        """P0: 腾讯成交量已是"手"，不转换。123456 手直接用。"""
        df = normalize_tencent(_TENCENT_SAMPLE)
        row = df.row(0, named=True)
        assert row["volume"] == pytest.approx(123456.0)

    def test_bid_ask_volume_hands(self):
        """腾讯买卖量已是"手"。买1量=200手, 卖1量=700手。"""
        df = normalize_tencent(_TENCENT_SAMPLE)
        row = df.row(0, named=True)
        assert row["bid1_volume"] == pytest.approx(200.0)
        assert row["ask1_volume"] == pytest.approx(700.0)

    def test_change_pct_decimal(self):
        """P0: change_pct 小数制。"""
        df = normalize_tencent(_TENCENT_SAMPLE)
        row = df.row(0, named=True)
        assert row["change_pct"] == pytest.approx((10.45 - 10.40) / 10.40, rel=1e-6)

    def test_source_column(self):
        df = normalize_tencent(_TENCENT_SAMPLE)
        assert df.row(0, named=True)["source"] == "tencent"

    def test_empty_input(self):
        assert normalize_tencent("").is_empty()

    def test_columns_match_schema(self):
        df = normalize_tencent(_TENCENT_SAMPLE)
        assert df.columns == FREE_REALTIME_COLUMNS


# ── 东财样本 ────────────────────────────────────────────────
_EM_BATCH_SAMPLE = {
    "rc": 0,
    "data": {
        "diff": [
            {
                "f43": 1045, "f44": 1060, "f45": 1030, "f46": 1050,
                "f47": 123456, "f48": 108000000,
                "f57": "600000", "f58": "浦发银行", "f60": 1040,
                "f170": 0.48, "f19": 200, "f20": 1045, "f17": 700, "f18": 1046,
            },
        ]
    },
}
_EM_SECID_MAP = {"1.600000": "600000.SH"}


class TestNormalizeEastmoney:
    def test_price_fen_to_yuan(self):
        """P0: 东财价格字段是"分"，/100 转"元"。
        f43=1045→10.45, f44=1060→10.60, f60=1040→10.40。
        """
        df = normalize_eastmoney(_EM_BATCH_SAMPLE, _EM_SECID_MAP)
        row = df.row(0, named=True)
        assert row["last_price"] == pytest.approx(10.45)
        assert row["high"] == pytest.approx(10.60)
        assert row["low"] == pytest.approx(10.30)
        assert row["open"] == pytest.approx(10.50)
        assert row["prev_close"] == pytest.approx(10.40)

    def test_change_pct_from_f170(self):
        """P0: 东财 f170 是百分数（0.48=0.48%），/100 转小数制=0.0048。"""
        df = normalize_eastmoney(_EM_BATCH_SAMPLE, _EM_SECID_MAP)
        row = df.row(0, named=True)
        assert row["change_pct"] == pytest.approx(0.0048)
        # 确保不是百分数 0.48
        assert row["change_pct"] < 0.01

    def test_change_pct_fallback_self_calc(self):
        """f170 缺失时自算小数制。"""
        sample = json.loads(json.dumps(_EM_BATCH_SAMPLE))
        del sample["data"]["diff"][0]["f170"]
        df = normalize_eastmoney(sample, _EM_SECID_MAP)
        row = df.row(0, named=True)
        assert row["change_pct"] == pytest.approx((10.45 - 10.40) / 10.40, rel=1e-6)

    def test_volume_already_hands(self):
        """东财成交量已是"手"。"""
        df = normalize_eastmoney(_EM_BATCH_SAMPLE, _EM_SECID_MAP)
        row = df.row(0, named=True)
        assert row["volume"] == pytest.approx(123456.0)

    def test_bid_ask_price_fen_to_yuan(self):
        """东财买卖价也是"分"，/100 转"元"。"""
        df = normalize_eastmoney(_EM_BATCH_SAMPLE, _EM_SECID_MAP)
        row = df.row(0, named=True)
        assert row["bid1_price"] == pytest.approx(10.45)
        assert row["ask1_price"] == pytest.approx(10.46)

    def test_source_column(self):
        df = normalize_eastmoney(_EM_BATCH_SAMPLE, _EM_SECID_MAP)
        assert df.row(0, named=True)["source"] == "eastmoney"

    def test_symbol_resolved(self):
        df = normalize_eastmoney(_EM_BATCH_SAMPLE, _EM_SECID_MAP)
        assert df.row(0, named=True)["symbol"] == "600000.SH"

    def test_empty_input(self):
        assert normalize_eastmoney("", _EM_SECID_MAP).is_empty()
        assert normalize_eastmoney({}, _EM_SECID_MAP).is_empty()

    def test_malformed_json(self):
        assert normalize_eastmoney("not json", _EM_SECID_MAP).is_empty()

    def test_single_item_format(self):
        """东财单只返回 data 是单个 dict（非 diff）。"""
        single = {"rc": 0, "data": _EM_BATCH_SAMPLE["data"]["diff"][0]}
        df = normalize_eastmoney(single, _EM_SECID_MAP)
        assert df.height == 1
        assert df.row(0, named=True)["last_price"] == pytest.approx(10.45)

    def test_columns_match_schema(self):
        df = normalize_eastmoney(_EM_BATCH_SAMPLE, _EM_SECID_MAP)
        assert df.columns == FREE_REALTIME_COLUMNS


# ── 符号转换 ────────────────────────────────────────────────
class TestSymbolConversion:
    def test_sina_key_to_symbol_sh(self):
        assert sina_key_to_symbol("sh600000") == "600000.SH"

    def test_sina_key_to_symbol_sz(self):
        assert sina_key_to_symbol("sz000001") == "000001.SZ"

    def test_sina_key_to_symbol_bj(self):
        assert sina_key_to_symbol("bj920344") == "920344.BJ"

    def test_project_symbol_to_sina_key(self):
        assert project_symbol_to_sina_key("600000.SH") == "sh600000"
        assert project_symbol_to_sina_key("000001.SZ") == "sz000001"

    def test_project_symbol_to_eastmoney_secid_sh(self):
        assert project_symbol_to_eastmoney_secid("600000.SH") == "1.600000"

    def test_project_symbol_to_eastmoney_secid_sz(self):
        assert project_symbol_to_eastmoney_secid("000001.SZ") == "0.000001"

    def test_project_symbol_to_eastmoney_secid_bj(self):
        assert project_symbol_to_eastmoney_secid("920344.BJ") == "0.920344"

    def test_project_symbol_to_eastmoney_secid_etf(self):
        # ETF: 510300.SH → 1.510300, 159915.SZ → 0.159915
        assert project_symbol_to_eastmoney_secid("510300.SH") == "1.510300"
        assert project_symbol_to_eastmoney_secid("159915.SZ") == "0.159915"
