"""Tests for app.data_providers.free_realtime.eastmoney.EastmoneyProvider."""
from __future__ import annotations

from unittest.mock import MagicMock

import httpx
import pytest

from app.data_providers.free_realtime.eastmoney import EastmoneyProvider

_EM_BATCH = {
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


def _mock_json_resp(data: dict) -> MagicMock:
    resp = MagicMock()
    resp.json.return_value = data
    return resp


class TestEastmoneyProvider:
    def test_name(self):
        assert EastmoneyProvider().name == "eastmoney"

    def test_default_timeout_3s(self):
        assert EastmoneyProvider()._timeout == 3.0

    def test_get_realtime_normal(self):
        mock_client = MagicMock()
        mock_client.get.return_value = _mock_json_resp(_EM_BATCH)
        provider = EastmoneyProvider(client=mock_client)
        df = provider.get_realtime(["600000.SH"])
        assert df.height == 1
        row = df.row(0, named=True)
        assert row["symbol"] == "600000.SH"
        assert row["last_price"] == pytest.approx(10.45)  # 1045分→10.45元
        assert row["change_pct"] == pytest.approx(0.0048)  # 0.48%→0.0048
        assert row["source"] == "eastmoney"

    def test_secids_param(self):
        """验证 secid 拼接: 沪市=1., 深市=0.。"""
        mock_client = MagicMock()
        mock_client.get.return_value = _mock_json_resp(_EM_BATCH)
        provider = EastmoneyProvider(client=mock_client)
        provider.get_realtime(["600000.SH", "000001.SZ"])
        params = mock_client.get.call_args.kwargs.get("params", {})
        assert "1.600000" in params["secids"]
        assert "0.000001" in params["secids"]

    def test_empty_symbols_returns_empty(self):
        provider = EastmoneyProvider(client=MagicMock())
        assert provider.get_realtime([]).is_empty()

    def test_http_error_returns_empty(self):
        mock_client = MagicMock()
        mock_client.get.side_effect = httpx.ConnectError("fail")
        provider = EastmoneyProvider(client=mock_client)
        assert provider.get_realtime(["600000.SH"]).is_empty()

    def test_json_decode_error_returns_empty(self):
        mock_client = MagicMock()
        resp = MagicMock()
        resp.json.side_effect = ValueError("not json")
        mock_client.get.return_value = resp
        provider = EastmoneyProvider(client=mock_client)
        assert provider.get_realtime(["600000.SH"]).is_empty()

    def test_etf_secid(self):
        """ETF secid: 510300.SH→1.510300, 159915.SZ→0.159915。"""
        mock_client = MagicMock()
        mock_client.get.return_value = _mock_json_resp({"rc": 0, "data": {"diff": []}})
        provider = EastmoneyProvider(client=mock_client)
        provider.get_realtime(["510300.SH", "159915.SZ"])
        params = mock_client.get.call_args.kwargs.get("params", {})
        assert "1.510300" in params["secids"]
        assert "0.159915" in params["secids"]
