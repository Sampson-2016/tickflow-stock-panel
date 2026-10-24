"""腾讯财经实时行情 provider。

接口: http://qt.gtimg.cn/q=sh600000,sz000001
陷阱:
  - 返回 GBK 编码（中文股票名）
  - 成交量已是"手"，无需转换
  - 不返回涨跌幅，normalizer 自算
  - 字段用 "~" 分隔
"""
from __future__ import annotations

import logging

import httpx
import polars as pl

from app.data_providers.free_realtime.base import DEFAULT_TIMEOUT_SEC, FreeRealtimeProvider
from app.data_providers.free_realtime.normalizer import (
    normalize_tencent,
    project_symbol_to_tencent_key,
)
from app.data_providers.free_realtime.schemas import empty_realtime_df

logger = logging.getLogger(__name__)

_TENCENT_URL = "http://qt.gtimg.cn/q"


class TencentProvider(FreeRealtimeProvider):
    """腾讯实时报价 provider。"""

    name = "tencent"

    def __init__(
        self,
        timeout: float = DEFAULT_TIMEOUT_SEC,
        client: httpx.Client | None = None,
    ) -> None:
        self._timeout = timeout
        self._client = client if client is not None else httpx.Client(timeout=timeout)

    def get_realtime(self, symbols: list[str]) -> pl.DataFrame:
        if not symbols:
            return empty_realtime_df()
        keys = [project_symbol_to_tencent_key(s) for s in symbols]
        url = f"{_TENCENT_URL}={','.join(keys)}"
        try:
            resp = self._client.get(url)
            # 腾讯返回 GBK，显式指定避免中文乱码
            resp.encoding = "gbk"
            df = normalize_tencent(resp.text, source=self.name)
            return self._stamp_poll_time(df)
        except (httpx.HTTPError, ValueError) as e:
            logger.warning("tencent fetch failed: %s", e)
            return empty_realtime_df()
