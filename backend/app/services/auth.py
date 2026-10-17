"""访问认证 — 单用户, 自托管场景。

设计:
  - 用户名 + 密码组合鉴权。默认凭证 admin / gxk2026 首次启动自动写入。
  - 密码用 PBKDF2-HMAC-SHA256 哈希(标准库 hashlib, 无新依赖), 加随机 salt。
    即使 auth.json 泄露, 也无法逆向出明文密码。
  - 会话用随机 token(token_urlsafe), 内存 + 文件双存(支持多进程/重启不丢失)。
  - 存储: data/user_data/auth.json (chmod 0600), 仿 secrets_store 模式。

安全要点:
  - 设密码接口必须限制本机/内网(见 auth router), 防黑客抢占域名抢先设密码。
  - 登录限流: 错5次锁5分钟(见 auth router 内存计数)。
  - 单用户, 不做多用户(避免重构全项目数据层)。

凭证变更:
  - 用户名: 默认 admin, 可在 auth.json 中手动修改(无 UI 入口)
  - 密码: 默认 gxk2026, 可在 auth.json 中手动修改或经 AUTH_PASSWORD 环境变量
    首次启动覆盖; 此后用户改的密码不再被环境变量覆盖
"""
from __future__ import annotations

import hashlib
import json
import logging
import os
import secrets as _secrets
import threading
import time
from pathlib import Path

logger = logging.getLogger(__name__)

# 默认用户名 (非敏感, 硬编码 OK; 如需改可在 .env 设 AUTH_USERNAME 覆盖)
DEFAULT_USERNAME = "admin"

# 密码不在代码里写默认值! 明文密码只允许存在于 .env (gitignored),
# auth.json 只存 PBKDF2 哈希 (单向, 不可逆)。这样:
#   1. 任何 git history / docker image / 代码审计都不会泄露密码
#   2. 改密码只需编辑服务器 .env + 重启, 不必 SSH 算 PBKDF2 hash
#   3. 凭证配置集中在一处, 不会散落在代码/数据库/配置文件
# 启动时若 .env 没设 AUTH_PASSWORD 且 auth.json 也不存在, 后端拒绝启动并报清晰错误。

# PBKDF2 参数(NIST 推荐, 单次校验 ~100ms, 兼顾安全与响应)
_PBKDF2_ITER = 200_000
_SALT_LEN = 16
_TOKEN_BYTES = 32

# 会话有效期: 3 天 (cookie 持久时间, 到期前端会被 401 拦截器跳回 /login)
SESSION_TTL = 3 * 24 * 3600

_lock = threading.Lock()
# 内存中的有效会话: { token: expire_ts }。进程重启后从磁盘恢复。
_sessions: dict[str, float] = {}

# 「是否已设密码」缓存: 每个 /api/ 请求都要判定, auth_middleware 原先每次 read_text
# 磁盘 (阻塞事件循环)。此处懒加载缓存, set_password 后失效重算 (仍返回最新真值)。
_configured_cache: bool | None = None


def _path() -> Path:
    from app.config import settings
    p = settings.data_dir / "user_data" / "auth.json"
    p.parent.mkdir(parents=True, exist_ok=True)
    return p


def _load() -> dict:
    p = _path()
    if p.exists():
        try:
            return json.loads(p.read_text(encoding="utf-8"))
        except Exception as e:  # noqa: BLE001
            logger.warning("auth.json malformed: %s", e)
    return {}


def _save(data: dict) -> None:
    p = _path()
    p.write_text(json.dumps(data, indent=2, ensure_ascii=False), encoding="utf-8")
    try:
        os.chmod(p, 0o600)
    except OSError:
        pass


def _hash_password(password: str, salt: bytes | None = None) -> tuple[str, str]:
    """返回 (salt_hex, hash_hex)。salt 为 None 时生成新 salt。"""
    if salt is None:
        salt = os.urandom(_SALT_LEN)
    dk = hashlib.pbkdf2_hmac("sha256", password.encode("utf-8"), salt, _PBKDF2_ITER)
    return salt.hex(), dk.hex()


def _verify_password(password: str, salt_hex: str, hash_hex: str) -> bool:
    """恒定时间比较, 防时序攻击。"""
    try:
        salt = bytes.fromhex(salt_hex)
        expected = bytes.fromhex(hash_hex)
    except ValueError:
        return False
    actual = hashlib.pbkdf2_hmac("sha256", password.encode("utf-8"), salt, _PBKDF2_ITER)
    return _secrets.compare_digest(actual, expected)


# ================================================================
# 密码管理
# ================================================================

def is_configured() -> bool:
    """是否已设置访问凭证 (用户名 + 密码哈希都存在, 带缓存)。

    热路径 (auth_middleware 每请求调用): 命中缓存则不碰磁盘, 不阻塞事件循环。
    首次或 set_password / ensure_default_credentials 失效后, 懒加载重读一次
    auth.json, 保证返回最新真值。
    """
    global _configured_cache
    if _configured_cache is None:
        d = _load()
        _configured_cache = bool(d.get("username") and d.get("password_hash"))
    return _configured_cache


def get_username() -> str:
    """返回当前配置的登录用户名 (供 /api/auth/status 展示给前端)。"""
    d = _load()
    return d.get("username") or DEFAULT_USERNAME


def ensure_default_credentials() -> None:
    """确保 auth.json 至少存在 username 字段 (默认 admin)。

    本函数**不**设置密码! 密码必须从 .env (AUTH_PASSWORD) 读取, 见
    bootstrap_from_env()。这里只兜底 username 字段, 用于:
      1. 老版本升级 (auth.json 只有 password_hash 无 username) — 补 username
      2. 用户手动编辑 auth.json 不慎删了 username — 重启自动恢复
      3. username 在 .env 设了 AUTH_USERNAME 想覆盖默认 admin

    调用时机: 后端 main 启动钩子, 在 bootstrap_from_env 之前。
    """
    from app.config import _ENV_FILE

    d = _load()
    changed = False

    # 从 .env 读 AUTH_USERNAME, 默认 admin
    env_user = ""
    if _ENV_FILE.is_file():
        try:
            from dotenv import dotenv_values
            env = dotenv_values(_ENV_FILE, encoding="utf-8", interpolate=False)
            u = env.get("AUTH_USERNAME")
            if isinstance(u, str) and u.strip():
                env_user = u.strip()
        except Exception:  # noqa: BLE001
            pass

    target_user = env_user or d.get("username") or DEFAULT_USERNAME
    if d.get("username") != target_user:
        d["username"] = target_user
        changed = True
    if changed:
        _save(d)
        _configured_cache = None
        if env_user:
            logger.info("auth: username 已从 .env 的 AUTH_USERNAME 同步为 %s", target_user)


def bootstrap_from_env() -> bool:
    """从 .env 同步密码到 auth.json。.env 是密码唯一的真值源。

    设计:
      - 密码**只**在 .env 里以明文存在 (gitignored, 不会进 git history)
      - auth.json 只存 PBKDF2 哈希 (单向不可逆, 安全)
      - 代码里**不**写默认密码, 避免任何代码审计/镜像打包泄露

    行为 (按优先级):
      1. .env 里有 AUTH_PASSWORD → 用它 hash 写 auth.json; 若与现有 hash 不同则
         视为"改密码", 清空所有旧会话
      2. .env 没 AUTH_PASSWORD 但 auth.json 已有 → 沿用 auth.json 现有密码
      3. .env 没 AUTH_PASSWORD 且 auth.json 也没有 → 抛 RuntimeError, 拒绝启动

    用户名: 走 ensure_default_credentials() 的逻辑 (优先 .env 的 AUTH_USERNAME, 否则 admin)

    Returns:
        True 表示本次用 .env 更新了 auth.json (含首次写入); False 表示无需操作。

    Raises:
        RuntimeError: .env 没设密码且 auth.json 也不存在时, 给出明确指引。
    """
    from app.config import _ENV_FILE, settings

    # .env 优先 (避免 pydantic 把 $VAR 当插值), settings 兜底
    raw_pwd = ""
    if _ENV_FILE.is_file():
        from dotenv import dotenv_values

        env = dotenv_values(_ENV_FILE, encoding="utf-8", interpolate=False)
        p = env.get("AUTH_PASSWORD")
        if isinstance(p, str):
            raw_pwd = p  # 注意: 保留原始值, 下面再 strip 判空 (避免空白密码 hash 一份空串)
    if not raw_pwd:
        raw_pwd = (settings.auth_password or "").strip()

    pwd = raw_pwd.strip() if raw_pwd else ""

    d = _load()
    has_existing = bool(d.get("username") and d.get("password_hash"))

    if not pwd:
        if not has_existing:
            # 关键失败: 没有任何来源的密码, 拒绝启动
            raise RuntimeError(
                "AUTH_PASSWORD 未配置! 请在 .env 文件 (项目根, gitignored) 里设置:\n"
                "    AUTH_PASSWORD=<你的密码>\n"
                "然后重启后端。模板见 .env.example。\n"
                "(auth.json 不存在, 无法沿用; 代码里也没有默认密码, 因为明文密码不应该进 git。)"
            )
        # .env 没设但 auth.json 已有, 沿用现有密码 (用户可能想保留旧密码只更新代码)
        return False

    # 校验 .env 密码的强度 (与 set_password 一致)
    if len(pwd) < 6:
        raise RuntimeError(
            f"AUTH_PASSWORD 太短 ({len(pwd)} 位), 至少需要 6 位。请修改 .env 后重启。"
        )

    username = d.get("username") or DEFAULT_USERNAME

    # 密码未变 (与现有 hash 匹配) — 啥也不做, 保留所有会话
    if has_existing and _verify_password(pwd, d.get("password_salt", ""), d.get("password_hash", "")):
        return False

    # 密码变了 (或首次) — 写新 hash, 清空会话
    salt_hex, hash_hex = _hash_password(pwd)
    with _lock:
        _sessions.clear()
        _save({
            "username": username,
            "password_hash": hash_hex,
            "password_salt": salt_hex,
            "updated_at": int(time.time()),
            "sessions": {},
        })
    _configured_cache = None
    if has_existing:
        logger.warning("auth: 检测到 .env 密码与 auth.json 不一致, 已更新 (旧会话全部失效)")
    else:
        logger.info("auth: 已根据 .env 的 AUTH_PASSWORD 首次初始化密码哈希")
    return True


def verify_and_create_session(username: str, password: str) -> str | None:
    """验证用户名+密码, 成功则创建会话并返回 token, 失败返回 None。

    用户名错也算失败 (与密码错返回同样的 None, 避免通过响应时间区分用户名/密码
    是否存在的旁路攻击)。恒定时间比较由 _verify_password 负责。
    """
    d = _load()
    if not d.get("password_hash"):
        return None
    expected_user = d.get("username") or DEFAULT_USERNAME
    # 用户名/密码任一不匹配都返回 None; 不分支返回, 防时序侧信道
    user_match = _secrets.compare_digest(
        (username or "").encode("utf-8"),
        expected_user.encode("utf-8"),
    )
    pwd_match = _verify_password(password, d.get("password_salt", ""), d["password_hash"])
    if not (user_match and pwd_match):
        return None
    token = _secrets.token_urlsafe(_TOKEN_BYTES)
    expire = time.time() + SESSION_TTL
    with _lock:
        _sessions[token] = expire
        _persist_sessions_locked()
    return token


def revoke_session(token: str) -> None:
    """注销会话(登出)。"""
    with _lock:
        _sessions.pop(token, None)
        _persist_sessions_locked()


def is_valid_session(token: str) -> bool:
    """检查会话是否有效(存在且未过期)。过期则清理。"""
    if not token:
        return False
    with _lock:
        expire = _sessions.get(token)
        if expire is None:
            return False
        if time.time() > expire:
            _sessions.pop(token, None)
            _persist_sessions_locked()
            return False
        return True


def _persist_sessions_locked() -> None:
    """把当前内存会话写回 auth.json(需持锁调用)。"""
    d = _load()
    d["sessions"] = {t: exp for t, exp in _sessions.items()}
    _save(d)


def _restore_sessions() -> None:
    """启动时从 auth.json 恢复未过期会话(支持进程重启不丢登录态)。"""
    with _lock:
        d = _load()
        now = time.time()
        saved = d.get("sessions") or {}
        for token, expire in saved.items():
            if isinstance(expire, (int, float)) and expire > now:
                _sessions[token] = expire
        if len(_sessions) != len(saved):
            # 有过期会话被清理, 落盘一次
            _persist_sessions_locked()


# 模块加载时恢复会话
try:
    _restore_sessions()
except Exception as e:  # noqa: BLE001
    logger.warning("restore sessions failed: %s", e)
