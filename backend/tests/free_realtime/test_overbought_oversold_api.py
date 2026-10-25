"""Tests for /api/free-realtime/overbought-oversold/* endpoints.

契约要点（2026-10 修正后）：
  - 超买超卖模块维护自己的独立标的清单（持久化于 preferences）
  - GET /symbols 不再合并持仓T + 自选股
  - POST /symbols 添加；DELETE /symbols/{symbol} 删除
  - PUT /symbols/{symbol} 切换 enabled
  - GET /signals 只评估本模块 enabled 标的（real 优先，mock 兜底）
"""
from __future__ import annotations

from unittest.mock import patch

import pytest
from fastapi import FastAPI
from fastapi.testclient import TestClient

from app.custom import free_realtime_ext


@pytest.fixture
def app() -> FastAPI:
    app = FastAPI()
    app.include_router(free_realtime_ext.router)
    app.include_router(free_realtime_ext.test_router)
    return app


@pytest.fixture
def client(app):
    return TestClient(app)


@pytest.fixture(autouse=True)
def _reset_test_state():
    saved = dict(free_realtime_ext._test_state)
    free_realtime_ext._test_state["mock_running"] = False
    free_realtime_ext._test_state["real_loop_running"] = False
    free_realtime_ext._test_state["tick_count"] = 0
    free_realtime_ext._test_state["mock_thread"] = None
    free_realtime_ext._test_state["real_thread"] = None
    try:
        from app.services.free_realtime.scheduler import FreeRealtimeScheduler
        FreeRealtimeScheduler.get_instance().store().clear_kind("mock")
    except Exception:
        pass
    yield
    free_realtime_ext._test_state.clear()
    free_realtime_ext._test_state.update(saved)


class TestSymbolsList:
    def test_returns_empty_when_no_config(self, client):
        """fr_config.list_overbought_symbols 返回空时给出空列表。"""
        with patch(
            "app.services.free_realtime.config.list_overbought_symbols",
            return_value=[],
        ):
            resp = client.get("/api/free-realtime/overbought-oversold/symbols")
        assert resp.status_code == 200
        data = resp.json()
        assert data["count"] == 0
        assert data["items"] == []

    def test_returns_independent_list(self, client):
        """直接返回 fr_config.list_overbought_symbols 的清单。"""
        stored = [
            {"symbol": "600000.SH", "name": "浦发银行", "enabled": True},
            {"symbol": "300308.SZ", "name": "中际旭创", "enabled": False},
        ]
        with patch(
            "app.services.free_realtime.config.list_overbought_symbols",
            return_value=stored,
        ):
            resp = client.get("/api/free-realtime/overbought-oversold/symbols")
        assert resp.status_code == 200
        data = resp.json()
        assert data["count"] == 2
        assert data["items"] == stored


class TestSymbolAdd:
    def test_add_new_symbol_persists(self, client):
        """POST /symbols 添加新标的。"""
        updated = [{"symbol": "600000.SH", "name": "浦发银行", "enabled": True}]
        with patch(
            "app.services.free_realtime.config.add_overbought_symbol",
            return_value=updated,
        ) as m_add:
            resp = client.post(
                "/api/free-realtime/overbought-oversold/symbols",
                json={"symbol": "600000.sh", "name": "浦发银行"},
            )
        assert resp.status_code == 200
        data = resp.json()
        assert data["count"] == 1
        assert data["items"] == updated
        # symbol 在端点层已归一化为大写
        m_add.assert_called_once_with("600000.SH", "浦发银行")

    def test_add_empty_symbol_rejected(self, client):
        resp = client.post(
            "/api/free-realtime/overbought-oversold/symbols",
            json={"symbol": "   "},
        )
        assert resp.status_code == 400


class TestSymbolDelete:
    def test_delete_calls_remove(self, client):
        updated: list[dict] = []
        with patch(
            "app.services.free_realtime.config.remove_overbought_symbol",
            return_value=updated,
        ) as m_rm:
            resp = client.delete("/api/free-realtime/overbought-oversold/symbols/600000.SH")
        assert resp.status_code == 200
        assert resp.json() == {"items": [], "count": 0}
        m_rm.assert_called_once_with("600000.SH")


class TestSymbolToggle:
    def test_toggle_endpoint_persists(self, client):
        """PUT /overbought-oversold/symbols/{symbol} 切换 enabled。"""
        updated = [{"symbol": "600000.SH", "name": "", "enabled": False}]
        with patch(
            "app.services.free_realtime.config.set_overbought_symbol_enabled",
            return_value=updated,
        ) as m_set:
            resp = client.put(
                "/api/free-realtime/overbought-oversold/symbols/600000.SH",
                json={"enabled": False},
            )
        assert resp.status_code == 200
        body = resp.json()
        assert body["symbol"] == "600000.SH"
        assert body["enabled"] is False
        assert body["items"] == updated
        m_set.assert_called_once_with("600000.SH", False)

    def test_empty_symbol_rejected(self, client):
        resp = client.put(
            "/api/free-realtime/overbought-oversold/symbols/%20",
            json={"enabled": True},
        )
        assert resp.status_code == 400


class TestSignalsEvaluate:
    def test_returns_empty_when_no_symbols(self, client):
        """fr_config.list_overbought_enabled_symbols 返回空时返回空。"""
        with patch(
            "app.services.free_realtime.config.list_overbought_enabled_symbols",
            return_value=[],
        ):
            resp = client.get("/api/free-realtime/overbought-oversold/signals")
        assert resp.status_code == 200
        data = resp.json()
        assert data["count"] == 0
        assert data["items"] == []

    def test_returns_empty_when_no_ticks(self, client):
        """有标的但 store 无 tick → 返回空。"""
        from app.services.free_realtime.scheduler import FreeRealtimeScheduler
        scheduler = FreeRealtimeScheduler.get_instance()
        # 清空 store
        scheduler.store().clear_kind("real")
        scheduler.store().clear_kind("mock")
        with patch(
            "app.services.free_realtime.config.list_overbought_enabled_symbols",
            return_value=["600000.SH"],
        ):
            resp = client.get("/api/free-realtime/overbought-oversold/signals")
        assert resp.status_code == 200
        data = resp.json()
        assert data["count"] == 0
        assert data["evaluated"] == 0
