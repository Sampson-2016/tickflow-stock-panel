"""新浪财经实时行情 provider。

接口: http://hq.sinajs.cn/list=sh600000,sz000001
陷阱:
  - 必须带 Referer: https://finance.sina.com.cn，否则 403
  - 返回 GBK 编码（中文股票名）
  - 成交量是"股"，normalizer 已 /100 转"手"
  - 不返回涨跌幅，normalizer 自算
"""
from __future__ import annotations

import logging

import httpx
import polars as pl

from app.data_providers.free_realtime.base import DEFAULT_TIMEOUT_SEC, FreeRealtimeProvider
from app.data_providers.free_realtime.normalizer import (
    normalize_sina,
    project_symbol_to_sina_key,
)
from app.data_providers.free_realtime.schemas import empty_realtime_df

logger = logging.getLogger(__name__)

_SINA_URL = "http://hq.sinajs.cn/list"
# 必须带 Referer，否则新浪返回 403
_SINA_HEADERS = {"Referer": "https://finance.sina.com.cn"}


class SinaProvider(FreeRealtimeProvider):
    """新浪实时报价 provider。"""

    name = "sina"

    def __init__(
        self,
        timeout: float = DEFAULT_TIMEOUT_SEC,
        client: httpx.Client | None = None,
    ) -> None:
        self._timeout = timeout
        # 注入 client 便于单测 mock；生产用默认 client
        self._client = client if client is not None else httpx.Client(
            timeout=timeout, headers=_SINA_HEADERS,
        )

    def get_realtime(self, symbols: list[str]) -> pl.DataFrame:
        if not symbols:
            return empty_realtime_df()
        keys = [project_symbol_to_sina_key(s) for s in symbols]
        url = f"{_SINA_URL}={','.join(keys)}"
        try:
            resp = self._client.get(url)
            # 新浪返回 GBK，显式指定避免乱码
            resp.encoding = "gbk"
            df = normalize_sina(resp.text, source=self.name)
            return self._stamp_poll_time(df)
        except (httpx.HTTPError, ValueError) as e:
            logger.warning("sina fetch failed: %s", e)
            return empty_realtime_df()
