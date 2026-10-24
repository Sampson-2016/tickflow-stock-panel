"""Tests for app.data_providers.free_realtime.tencent.TencentProvider."""
from __future__ import annotations

from unittest.mock import MagicMock

import httpx
import pytest

from app.data_providers.free_realtime.tencent import TencentProvider

_TENCENT_SAMPLE = (
    'v_sh600000="1~浦发银行~600000~10.450~10.400~10.500~10.600~10.300~'
    '10.450~10.460~123456~108000000~'
    '200~10.450~300~10.440~400~10.430~500~10.420~600~10.410~'
    '700~10.470~800~10.480~900~10.490~1000~10.500~1100~10.510~'
    '20240102~15:00:00~0~0~0~0~0~0~0~0~0~0~0~20240102150000~";'
)


def _mock_resp(text: str) -> MagicMock:
    resp = MagicMock()
    resp.text = text
    resp.encoding = "gbk"
    return resp


class TestTencentProvider:
    def test_name(self):
        assert TencentProvider().name == "tencent"

    def test_default_timeout_3s(self):
        provider = TencentProvider()
        assert provider._timeout == 3.0

    def test_get_realtime_normal(self):
        mock_client = MagicMock()
        mock_client.get.return_value = _mock_resp(_TENCENT_SAMPLE)
        provider = TencentProvider(client=mock_client)
        df = provider.get_realtime(["600000.SH"])
        assert df.height == 1
        row = df.row(0, named=True)
        assert row["symbol"] == "600000.SH"
        assert row["last_price"] == pytest.approx(10.45)
        assert row["volume"] == pytest.approx(123456.0)  # 手，不转换
        assert row["source"] == "tencent"

    def test_url_contains_tencent_keys(self):
        mock_client = MagicMock()
        mock_client.get.return_value = _mock_resp(_TENCENT_SAMPLE)
        provider = TencentProvider(client=mock_client)
        provider.get_realtime(["600000.SH", "000001.SZ"])
        url = mock_client.get.call_args[0][0]
        assert "sh600000" in url
        assert "sz000001" in url

    def test_empty_symbols_returns_empty(self):
        provider = TencentProvider(client=MagicMock())
        assert provider.get_realtime([]).is_empty()

    def test_http_error_returns_empty(self):
        mock_client = MagicMock()
        mock_client.get.side_effect = httpx.ConnectError("timeout")
        provider = TencentProvider(client=mock_client)
        assert provider.get_realtime(["600000.SH"]).is_empty()

    def test_timeout_error_returns_empty(self):
        mock_client = MagicMock()
        mock_client.get.side_effect = httpx.ReadTimeout("3s")
        provider = TencentProvider(client=mock_client)
        assert provider.get_realtime(["600000.SH"]).is_empty()
