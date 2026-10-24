"""Tests for app.data_providers.free_realtime.schemas.

重点: infer_asset_type 必须结合交易所后缀，不能仅凭代码前缀。
"""
from __future__ import annotations

from app.data_providers.free_realtime.schemas import (
    FREE_REALTIME_COLUMNS,
    FREE_REALTIME_SCHEMA,
    empty_realtime_df,
    infer_asset_type,
)


class TestInferAssetType:
    def test_sh_index(self):
        """000001.SH 是上证指数。"""
        assert infer_asset_type("000001.SH") == "index"
        assert infer_asset_type("000300.SH") == "index"
        assert infer_asset_type("000016.SH") == "index"

    def test_sz_stock_not_index(self):
        """000001.SZ 是平安银行（股票），不是指数。"""
        assert infer_asset_type("000001.SZ") == "stock"
        assert infer_asset_type("000002.SZ") == "stock"

    def test_sz_index(self):
        """399xxx.SZ 是深证指数。"""
        assert infer_asset_type("399001.SZ") == "index"
        assert infer_asset_type("399006.SZ") == "index"

    def test_sh_etf(self):
        assert infer_asset_type("510300.SH") == "etf"
        assert infer_asset_type("512760.SH") == "etf"
        assert infer_asset_type("518880.SH") == "etf"

    def test_sz_etf(self):
        assert infer_asset_type("159915.SZ") == "etf"
        assert infer_asset_type("159901.SZ") == "etf"

    def test_stock(self):
        assert infer_asset_type("600000.SH") == "stock"
        assert infer_asset_type("300750.SZ") == "stock"
        assert infer_asset_type("688981.SH") == "stock"

    def test_bj_stock(self):
        assert infer_asset_type("920344.BJ") == "stock"

    def test_case_insensitive(self):
        assert infer_asset_type(" 600000.sh ") == "stock"


class TestSchema:
    def test_empty_df_columns(self):
        df = empty_realtime_df()
        assert df.is_empty()
        assert df.columns == FREE_REALTIME_COLUMNS

    def test_empty_df_schema(self):
        df = empty_realtime_df()
        assert df.schema == FREE_REALTIME_SCHEMA

    def test_columns_order(self):
        expected = [
            "symbol", "name", "asset_type", "last_price", "open", "prev_close",
            "high", "low", "change_pct", "volume", "amount",
            "bid1_price", "ask1_price", "bid1_volume", "ask1_volume",
            "source", "ts_ms", "trade_date",
        ]
        assert expected == FREE_REALTIME_COLUMNS
