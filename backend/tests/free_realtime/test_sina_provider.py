"""Tests for app.data_providers.free_realtime.sina.SinaProvider.

策略: 注入 mock httpx.Client，验证 URL 拼接、Referer 头、GBK 解码、
fail-closed（HTTP 异常返回空 DataFrame）。
"""
from __future__ import annotations

from unittest.mock import MagicMock

import httpx
import pytest

from app.data_providers.free_realtime.sina import SinaProvider

_SINA_SAMPLE = (
    'var hq_str_sh600000="浦发银行,10.500,10.400,10.450,10.600,10.300,'
    '10.450,10.460,12345600,108000000,'
    '200,10.450,300,10.440,400,10.430,500,10.420,600,10.410,'
    '700,10.470,800,10.480,900,10.490,1000,10.500,1100,10.510,'
    '2024-01-02,15:00:00,00";'
)


def _mock_resp(text: str) -> MagicMock:
    resp = MagicMock()
    resp.text = text
    resp.encoding = "gbk"
    return resp


class TestSinaProvider:
    def test_name(self):
        assert SinaProvider().name == "sina"

    def test_referer_header_set(self):
        """P0: 新浪必须带 Referer，否则 403。"""
        provider = SinaProvider()
        assert provider._client.headers.get("Referer") == "https://finance.sina.com.cn"

    def test_default_timeout_3s(self):
        """方案约束: 超时不超过 3s。"""
        provider = SinaProvider()
        assert provider._timeout == 3.0

    def test_get_realtime_normal(self):
        mock_client = MagicMock()
        mock_client.get.return_value = _mock_resp(_SINA_SAMPLE)
        provider = SinaProvider(client=mock_client)
        df = provider.get_realtime(["600000.SH"])
        assert df.height == 1
        row = df.row(0, named=True)
        assert row["symbol"] == "600000.SH"
        assert row["last_price"] == pytest.approx(10.45)
        assert row["volume"] == pytest.approx(123456.0)  # 股→手
        assert row["source"] == "sina"

    def test_url_contains_sina_keys(self):
        mock_client = MagicMock()
        mock_client.get.return_value = _mock_resp(_SINA_SAMPLE)
        provider = SinaProvider(client=mock_client)
        provider.get_realtime(["600000.SH", "000001.SZ"])
        url = mock_client.get.call_args[0][0]
        assert "sh600000" in url
        assert "sz000001" in url

    def test_empty_symbols_returns_empty(self):
        provider = SinaProvider(client=MagicMock())
        df = provider.get_realtime([])
        assert df.is_empty()
        provider._client.get.assert_not_called()

    def test_http_error_returns_empty(self):
        """fail-closed: HTTP 异常返回空 DataFrame，不抛出。"""
        mock_client = MagicMock()
        mock_client.get.side_effect = httpx.ConnectError("timeout")
        provider = SinaProvider(client=mock_client)
        df = provider.get_realtime(["600000.SH"])
        assert df.is_empty()

    def test_timeout_error_returns_empty(self):
        mock_client = MagicMock()
        mock_client.get.side_effect = httpx.ReadTimeout("3s timeout")
        provider = SinaProvider(client=mock_client)
        df = provider.get_realtime(["600000.SH"])
        assert df.is_empty()

    def test_multiple_symbols(self):
        text = _SINA_SAMPLE + (
            'var hq_str_sz000001="平安银行,12.0,11.8,12.1,12.2,11.9,'
            '12.1,12.11,5000,60000,100,12.1,200,12.0,'
            '300,11.9,400,11.8,500,11.7,'
            '600,12.12,700,12.13,800,12.14,900,12.15,1000,12.16,'
            '2024-01-02,15:00:00,00";'
        )
        mock_client = MagicMock()
        mock_client.get.return_value = _mock_resp(text)
        provider = SinaProvider(client=mock_client)
        df = provider.get_realtime(["600000.SH", "000001.SZ"])
        assert df.height == 2
