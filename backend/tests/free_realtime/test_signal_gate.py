"""SignalGate（边沿触发推送闸门）单元测试。

覆盖产品决策：条件持续期间静默、恶化跨档才重报、解除不推送、防抖间隔。
"""
import pytest

from app.services.free_realtime.signal_gate import SignalGate


class TestSignalGate:
    def test_fire_on_enter_then_silent_while_persisting(self):
        g = SignalGate()
        assert g.feed("k", True, level=1, now=100.0) == "fire"
        # 条件持续、档位不变 → 静默（不再按冷却周期轰炸）
        for t in (101.0, 130.0, 200.0):
            assert g.feed("k", True, level=1, now=t) is None

    def test_escalate_only_on_new_deeper_level(self):
        g = SignalGate()
        assert g.feed("k", True, level=1, now=100.0) == "fire"
        # 档位回落再回到 1 → 不重报（max_level 只升不降）
        assert g.feed("k", True, level=0, now=101.0) is None
        assert g.feed("k", True, level=1, now=102.0) is None
        # 恶化跨新档: 防抖窗口内（距上次放行 < 60s）→ 静默, 窗口外补推
        assert g.feed("k", True, level=2, now=103.0) is None
        assert g.feed("k", True, level=2, now=161.0) == "escalate"
        assert g.feed("k", True, level=2, now=162.0) is None
        # 继续恶化第三档（防抖窗口内 → 静默；窗口外 → escalate）
        assert g.feed("k", True, level=3, now=163.0) is None
        assert g.feed("k", True, level=3, now=222.0) == "escalate"

    def test_release_resets_and_no_push(self):
        g = SignalGate()
        assert g.feed("k", True, level=1, now=100.0) == "fire"
        # 解除：不推送，状态复位
        assert g.feed("k", False, now=101.0) is None
        # 防抖窗口内再进入：静默建立状态
        assert g.feed("k", True, level=1, now=110.0) is None
        # 解除等过防抖窗口后再次进入：重新 fire
        assert g.feed("k", False, now=120.0) is None
        assert g.feed("k", True, level=1, now=200.0) == "fire"

    def test_zero_min_refire_always_allows(self):
        g = SignalGate()
        assert g.feed("k", True, level=1, now=100.0, min_refire_sec=0) == "fire"
        assert g.feed("k", False, now=100.1) is None
        assert g.feed("k", True, level=1, now=100.2, min_refire_sec=0) == "fire"

    def test_sweep_releases_absent_keys(self):
        g = SignalGate()
        assert g.feed("a", True, level=1, now=100.0) == "fire"
        assert g.feed("b", True, level=1, now=100.0) == "fire"
        # 本轮只有 a 出现 → b 解除复位
        g.sweep({"a"})
        assert g.feed("b", True, level=1, now=160.0) == "fire"
        assert g.feed("a", True, level=1, now=160.0) is None

    def test_level_zero_never_escalates(self):
        """无跨档概念的信号 (level 恒 0) 持续期间完全静默。"""
        g = SignalGate()
        assert g.feed("k", True, level=0, now=100.0) == "fire"
        for t in (101.0, 300.0):
            assert g.feed("k", True, level=0, now=t) is None

    @pytest.mark.parametrize("dev,thr,level", [
        (10.0, 5.0, 2),
        (10.4, 5.0, 2),
        (9.9, 5.0, 1),
        (-21.0, 10.0, 2),
    ])
    def test_level_formula_matches_push_layer(self, dev, thr, level):
        """档位口径与推送层 int(|偏离|/阈值) 一致（示例性校验）。"""
        assert int(abs(dev) / thr) == level
