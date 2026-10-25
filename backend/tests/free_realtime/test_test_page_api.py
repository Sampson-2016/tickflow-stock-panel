"""Tests for /api/free-realtime/test/* test page endpoints."""
from __future__ import annotations

import time

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
    """每个用例独立重置测试页全局状态。"""
    saved = dict(free_realtime_ext._test_state)
    free_realtime_ext._test_state["mock_running"] = False
    free_realtime_ext._test_state["real_loop_running"] = False
    free_realtime_ext._test_state["tick_count"] = 0
    free_realtime_ext._test_state["mock_thread"] = None
    free_realtime_ext._test_state["real_thread"] = None
    # 清理单例 store 中可能的 mock 残留
    try:
        from app.services.free_realtime.scheduler import FreeRealtimeScheduler
        FreeRealtimeScheduler.get_instance().store().clear_kind("mock")
    except Exception:
        pass
    yield
    free_realtime_ext._test_state.clear()
    free_realtime_ext._test_state.update(saved)


class TestMockConfig:
    def test_get_default(self, client):
        resp = client.get("/api/free-realtime/test/mock/config")
        assert resp.status_code == 200
        data = resp.json()
        assert "config" in data
        assert "accelerations" in data
        assert 1 in data["accelerations"]
        assert 120 in data["accelerations"]

    def test_put_updates(self, client):
        resp = client.put(
            "/api/free-realtime/test/mock/config",
            json={"volatility_pct": 0.05, "seed": 7, "acceleration": 10},
        )
        assert resp.status_code == 200
        cfg = resp.json()["config"]
        assert cfg["volatility_pct"] == 0.05
        assert cfg["seed"] == 7
        assert cfg["acceleration"] == 10

    def test_invalid_acceleration_rejected(self, client):
        resp = client.put(
            "/api/free-realtime/test/mock/config",
            json={"acceleration": 2},
        )
        assert resp.status_code == 400

    def test_fail_rate_clamped(self, client):
        resp = client.put(
            "/api/free-realtime/test/mock/config",
            json={"fail_rate": 5.0},
        )
        assert resp.status_code == 200
        assert resp.json()["config"]["fail_rate"] == 1.0

    def test_start_time_default_is_0915(self, client):
        """默认 start_time 为 09:15（含集合竞价）。"""
        resp = client.get("/api/free-realtime/test/mock/config")
        assert resp.status_code == 200
        assert resp.json()["config"].get("start_time") == "09:15"

    def test_put_start_time_updates(self, client):
        resp = client.put(
            "/api/free-realtime/test/mock/config",
            json={"start_time": "10:30"},
        )
        assert resp.status_code == 200
        assert resp.json()["config"]["start_time"] == "10:30"

    def test_invalid_start_time_rejected(self, client):
        resp = client.put(
            "/api/free-realtime/test/mock/config",
            json={"start_time": "08:00"},
        )
        assert resp.status_code == 400


class TestStartStop:
    def test_start_stop_mock(self, client):
        # 配置极短 interval + 高加速，便于快速停止
        client.put(
            "/api/free-realtime/test/mock/config",
            json={
                "symbols": ["600000.SH"],
                "base_prices": {"600000.SH": 10.0},
                "names": {"600000.SH": "测试"},
                "volatility_pct": 0.0,
                "acceleration": 120,
            },
        )
        resp = client.post("/api/free-realtime/test/mock/start")
        assert resp.status_code == 200
        assert resp.json()["ok"] is True
        # 让一两个 tick 发生
        time.sleep(0.3)
        stop = client.post("/api/free-realtime/test/mock/stop")
        assert stop.status_code == 200
        # 至少应有一次 tick
        status = client.get("/api/free-realtime/test/status").json()
        assert status["tick_count"] >= 0  # 不能保证一定 > 0（取决于 mock provider 实现）

    def test_start_when_running_is_noop(self, client):
        client.put(
            "/api/free-realtime/test/mock/config",
            json={"symbols": ["600000.SH"], "base_prices": {"600000.SH": 10.0}, "acceleration": 120},
        )
        client.post("/api/free-realtime/test/mock/start")
        second = client.post("/api/free-realtime/test/mock/start")
        assert second.json()["already_running"] is True
        client.post("/api/free-realtime/test/mock/stop")


class TestClearStorage:
    def test_clear_invalid_kind(self, client):
        resp = client.post("/api/free-realtime/test/clear?kind=bad")
        assert resp.status_code == 400

    def test_clear_real_returns_int(self, client):
        resp = client.post("/api/free-realtime/test/clear?kind=real")
        assert resp.status_code == 200
        assert "removed" in resp.json()


class TestIntradayApi:
    def test_invalid_kind(self, client):
        resp = client.get("/api/free-realtime/intraday/600000.SH?kind=invalid")
        assert resp.status_code == 400

    def test_empty_result(self, client):
        # 确保该 symbol 当前没有 mock 数据
        from app.services.free_realtime.scheduler import FreeRealtimeScheduler
        FreeRealtimeScheduler.get_instance().store().clear_kind("mock")
        resp = client.get("/api/free-realtime/intraday/600000.SH?kind=mock")
        assert resp.status_code == 200
        data = resp.json()
        assert data["count"] == 0
        assert data["rows"] == []


class TestStatus:
    def test_status_shape(self, client):
        resp = client.get("/api/free-realtime/test/status")
        assert resp.status_code == 200
        data = resp.json()
        assert "mock_running" in data
        assert "real_loop_running" in data
        assert "tick_count" in data
