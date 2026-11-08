"""策略配置持久化 — 读写用户覆盖值。

职责: 将每个策略的用户定制设置（基础参数、策略参数、评分、买卖信号）持久化到 JSON。
不知道: 引擎、AI、前端、回测。
存储: data/user_data/strategy_overrides/{strategy_id}.json

自 v2 起支持按资产类型分别保存:

    {
      "schema": 2,
      "common": {...},                 # 公共配置(兜底)
      "asset": {"stock": {...}, "etf": {...}}   # 各资产类型的独立配置
    }

读取时按「公共配置 + 当前资产类型配置」深度合并, 字段级覆盖。
旧版扁平配置自动视为 stock 配置 (etf 从策略代码默认值开始)。
"""
from __future__ import annotations

import copy
import json
import logging
from pathlib import Path

logger = logging.getLogger(__name__)

_SCHEMA_VERSION = 2
_DEFAULT_ASSET_TYPE = "stock"

# 进程内缓存: 监控引擎每轮对每条策略规则调用 load_override, 每次读盘+parse 纯重复;
# override 仅在用户编辑时变化, 以 (mtime_ns, size) 签名判断是否重读。
# 键为 override 文件路径 (进程内可能有多个 data_dir, 如测试)。
_override_cache: dict[str, dict] = {}
_override_cache_sig: dict[str, tuple[int, int]] = {}


def _invalidate_override_cache(path: Path) -> None:
    key = str(path)
    _override_cache.pop(key, None)
    _override_cache_sig.pop(key, None)


def _overrides_dir(data_dir: Path) -> Path:
    d = data_dir / "user_data" / "strategy_overrides"
    d.mkdir(parents=True, exist_ok=True)
    return d


def _path(data_dir: Path, strategy_id: str, *, ensure_dir: bool = True) -> Path:
    # ensure_dir=False 供热路径读取: mkdir 系统调用在 Windows 上 ~0.07ms,
    # 读缓存命中时跳过它 (目录由写路径保证存在)。
    if ensure_dir:
        d = _overrides_dir(data_dir)
    else:
        d = data_dir / "user_data" / "strategy_overrides"
    return d / f"{strategy_id}.json"


def _read_raw(path: Path, strategy_id: str) -> dict:
    """读取原始 override JSON (带 mtime 签名缓存, 返回深拷贝)"""
    key = str(path)
    try:
        st = path.stat()
        sig = (st.st_mtime_ns, st.st_size)
    except OSError:
        _invalidate_override_cache(path)
        return {}
    cached = _override_cache.get(key)
    if cached is not None and sig == _override_cache_sig.get(key):
        return copy.deepcopy(cached)
    try:
        data = json.loads(path.read_text(encoding="utf-8"))
        _override_cache[key] = data
        _override_cache_sig[key] = sig
        return copy.deepcopy(data)
    except Exception as e:
        logger.warning("load override %s failed: %s", strategy_id, e)
        return {}


def _split_raw(raw: dict) -> tuple[dict, dict[str, dict]]:
    """拆分为 (公共配置, 各资产类型配置); 旧版扁平配置视为 stock 配置。"""
    if not isinstance(raw, dict):
        return {}, {}
    if raw.get("schema") == _SCHEMA_VERSION:
        common = raw.get("common")
        variants = raw.get("asset")
        common = copy.deepcopy(common) if isinstance(common, dict) else {}
        variants = (
            {k: copy.deepcopy(v) for k, v in variants.items() if isinstance(v, dict)}
            if isinstance(variants, dict)
            else {}
        )
        return common, variants
    # 旧版扁平结构 → 只归入股票配置, ETF 从代码默认值开始
    return {}, ({"stock": copy.deepcopy(raw)} if raw else {})


def _merge(base: dict, extra: dict) -> dict:
    """深度合并: dict 逐键递归, 其他类型(含 list)整体覆盖。"""
    out = copy.deepcopy(base)
    for key, value in extra.items():
        if isinstance(value, dict) and isinstance(out.get(key), dict):
            out[key] = _merge(out[key], value)
        else:
            out[key] = copy.deepcopy(value)
    return out


def _clean_basic_filter(data: dict) -> dict:
    # 清理 basic_filter 中值为 None 的键（避免固化无意义的空值）
    bf = data.get("basic_filter")
    if isinstance(bf, dict):
        cleaned = {k: v for k, v in bf.items() if v is not None}
        if cleaned:
            data["basic_filter"] = cleaned
        else:
            data.pop("basic_filter", None)
    return data


def _resolve(raw: dict, asset_type: str | None) -> dict:
    common, variants = _split_raw(raw)
    effective = _merge(common, variants.get(asset_type or _DEFAULT_ASSET_TYPE, {}))
    return _clean_basic_filter(effective)


def load_override(data_dir: Path, strategy_id: str, asset_type: str | None = None) -> dict:
    """读取当前资产类型生效的覆盖配置 (公共配置兜底), 不存在返回空 dict。

    asset_type 省略时按 stock 处理, 兼容既有调用方与旧版配置。
    """
    raw = _read_raw(_path(data_dir, strategy_id, ensure_dir=False), strategy_id)
    return _resolve(raw, asset_type)


def load_config_bundle(data_dir: Path, strategy_id: str) -> dict:
    """读取完整配置结构 {schema, common, asset}, 供导出使用。"""
    common, variants = _split_raw(
        _read_raw(_path(data_dir, strategy_id, ensure_dir=False), strategy_id)
    )
    return {"schema": _SCHEMA_VERSION, "common": common, "asset": variants}


def save_override(
    data_dir: Path,
    strategy_id: str,
    overrides: dict,
    asset_type: str = _DEFAULT_ASSET_TYPE,
) -> None:
    """保存指定资产类型的覆盖配置（该类型全量覆盖, 其他类型保持不变）。"""
    p = _path(data_dir, strategy_id)
    p.parent.mkdir(parents=True, exist_ok=True)
    common, variants = _split_raw(_read_raw(p, strategy_id))
    variants[asset_type] = copy.deepcopy(overrides)
    payload = {"schema": _SCHEMA_VERSION, "common": common, "asset": variants}
    p.write_text(json.dumps(payload, ensure_ascii=False, indent=2), encoding="utf-8")
    # 写入后直接更新缓存, 避免紧随其后的读取再 parse 一次
    try:
        st = p.stat()
        _override_cache[str(p)] = payload
        _override_cache_sig[str(p)] = (st.st_mtime_ns, st.st_size)
    except OSError:
        _invalidate_override_cache(p)


def delete_override(
    data_dir: Path,
    strategy_id: str,
    asset_type: str | None = None,
) -> None:
    """删除覆盖配置。

    asset_type=None 删除整个文件(所有资产类型); 指定时只重置该类型的配置。
    """
    p = _path(data_dir, strategy_id)
    if asset_type is not None:
        raw = _read_raw(p, strategy_id)
        common, variants = _split_raw(raw)
        variants.pop(asset_type, None)
        if common or variants:
            p.write_text(
                json.dumps(
                    {"schema": _SCHEMA_VERSION, "common": common, "asset": variants},
                    ensure_ascii=False,
                    indent=2,
                ),
                encoding="utf-8",
            )
            try:
                st = p.stat()
                _override_cache[str(p)] = {"schema": _SCHEMA_VERSION, "common": common, "asset": variants}
                _override_cache_sig[str(p)] = (st.st_mtime_ns, st.st_size)
            except OSError:
                _invalidate_override_cache(p)
            return
    _invalidate_override_cache(p)
    if p.exists():
        p.unlink()


def list_overrides(data_dir: Path, asset_type: str | None = None) -> dict[str, dict]:
    """返回当前资产类型生效的全部策略覆盖配置 {strategy_id: overrides}"""
    d = _overrides_dir(data_dir)
    result: dict[str, dict] = {}
    for f in d.glob("*.json"):
        try:
            sid = f.stem
            effective = _resolve(_read_raw(f, sid), asset_type)
            if effective:
                result[sid] = effective
        except Exception:
            continue
    return result
