"""东方财富实时行情 provider。

接口: https://push2.eastmoney.com/api/qt/ulist.np/get?secids=1.600000,0.000001&fields=...
陷阱:
  - secid 前缀: 沪市=1, 深市=0, 北交所=0
  - 价格字段（f43/f44/f45/f46/f60）是"分"，normalizer 已 /100 转"元"
  - f170 涨跌幅是百分数（0.48=0.48%），normalizer 已 /100 转小数制
  - fltt=2 返回整数（分），与 normalizer 的 /100 口径一致
  - 批量返回 data.diff 数组，每项有 f57（代码）但通常无 secid
    normalizer 靠 secid_map 反查 symbol
"""
from __future__ import annotations

import logging

import httpx
import polars as pl

from app.data_providers.free_realtime.base import DEFAULT_TIMEOUT_SEC, FreeRealtimeProvider
from app.data_providers.free_realtime.normalizer import (
    normalize_eastmoney,
    project_symbol_to_eastmoney_secid,
)
from app.data_providers.free_realtime.schemas import empty_realtime_df

logger = logging.getLogger(__name__)

_EM_URL = "https://push2.eastmoney.com/api/qt/ulist.np/get"
# f43=最新(分) f44=最高(分) f45=最低(分) f46=今开(分) f47=量(手) f48=额(元)
# f57=代码 f58=名称 f60=昨收(分) f170=涨跌幅(%) f19=买1量 f20=买1价(分)
# f17=卖1量 f18=卖1价(分)
_EM_FIELDS = "f43,f44,f45,f46,f47,f48,f57,f58,f60,f170,f19,f20,f17,f18"


class EastmoneyProvider(FreeRealtimeProvider):
    """东方财富实时报价 provider。"""

    name = "eastmoney"

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
        secid_map: dict[str, str] = {}
        secids: list[str] = []
        for sym in symbols:
            secid = project_symbol_to_eastmoney_secid(sym)
            secid_map[secid] = sym
            secids.append(secid)
        params = {
            "secids": ",".join(secids),
            "fields": _EM_FIELDS,
            "fltt": "2",  # 返回整数（分），normalizer /100 转"元"
        }
        try:
            resp = self._client.get(_EM_URL, params=params)
            df = normalize_eastmoney(resp.json(), secid_map=secid_map, source=self.name)
            return self._stamp_poll_time(df)
        except (httpx.HTTPError, ValueError) as e:
            logger.warning("eastmoney fetch failed: %s", e)
            return empty_realtime_df()
