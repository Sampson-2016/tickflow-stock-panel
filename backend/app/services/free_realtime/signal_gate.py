"""边沿触发的信号推送闸门（持仓T / 超买超卖共用）。

替代纯时间冷却：信号条件持续期间不再按冷却周期重复轰炸飞书。
放行动作只有两个时刻：
  - ``fire``     条件由不成立 → 成立（解除后再进入同样走 fire）
  - ``escalate`` 条件持续成立但恶化跨档（档位 = 偏离量 ÷ 阈值，超过历史最深档）

解除（条件不成立）只静默复位状态、不产生推送（产品决策：解除不通知）；
``min_refire_sec`` 为防抖间隔：fire/escalate 之后的再次放行至少间隔该秒数，
用于抑制阈值附近来回横跳造成的连环触发。
"""
from __future__ import annotations

import threading
import time

ACTIONS = ("fire", "escalate")  # feed() 可能返回的放行动作


class SignalGate:
    """按 key (如 股票+方向/信号种) 维护 active/最深档位/上次放行时间。"""

    def __init__(self) -> None:
        self._lock = threading.Lock()
        # key -> {"max_level": int}，仅在条件成立时存在；解除即弹出
        self._active: dict[str, int] = {}
        # key -> 上次 fire/escalate 的 time()，独立于 active 保存（防抖跨解除生效）
        self._last_fire: dict[str, float] = {}

    def feed(
        self,
        key: str,
        condition_true: bool,
        level: int = 0,
        now: float | None = None,
        min_refire_sec: float = 60.0,
    ) -> str | None:
        """输入本轮信号状态，返回放行动作（'fire' / 'escalate' / None）。

        level: 本轮恶化档位（偏离量÷阈值取整；无跨档概念的信号传 0，
        持续期间即完全静默直到解除）。
        """
        if now is None:
            now = time.time()
        with self._lock:
            if not condition_true:
                # 解除：静默复位，下次进入重新走 fire（不推送）
                self._active.pop(key, None)
                return None

            max_level = self._active.get(key)
            if max_level is None:
                # 新进入（含解除后再进入）：受防抖约束，窗口内静默建立状态
                self._active[key] = level
                if now - self._last_fire.get(key, 0.0) < min_refire_sec:
                    return None
                self._last_fire[key] = now
                return "fire"

            # 条件持续：仅恶化跨新档才放行；被防抖抑制时保持旧档位，
            # 窗口过后下轮补推；档位回落不降 max_level（回落再恶化不重报）
            if level > max_level and now - self._last_fire.get(key, 0.0) >= min_refire_sec:
                self._active[key] = level
                self._last_fire[key] = now
                return "escalate"
            return None

    def sweep(self, active_keys: set[str] | frozenset[str]) -> None:
        """本轮信号集合之外的 key 视为解除，静默复位。"""
        with self._lock:
            for k in list(self._active):
                if k not in active_keys:
                    del self._active[k]
