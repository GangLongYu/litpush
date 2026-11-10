"""HTTP 客户端：磁盘缓存 + 限速 + 指数退避重试。

设计目标：每天只跑一次，宁可慢一点也不要被 API 封；重复开发调试时靠缓存
避免反复打接口。
"""

from __future__ import annotations

import hashlib
import json
import random
import threading
import time
import urllib.error
import urllib.parse
import urllib.request
from pathlib import Path
from typing import Any


class HttpError(RuntimeError):
    def __init__(self, url: str, status: int, body: str = ""):
        super().__init__(f"HTTP {status} for {url} :: {body[:200]}")
        self.url = url
        self.status = status
        self.body = body


def brief_error(exc: Exception, limit: int = 120) -> str:
    """把异常压成一句人话，避免把整条 URL 塞进日记。"""
    if isinstance(exc, HttpError):
        message = ""
        try:
            payload = json.loads(exc.body or "{}")
            message = str(payload.get("message") or payload.get("error") or "")
        except (json.JSONDecodeError, TypeError):
            message = (exc.body or "").strip()
        label = {
            429: "限流/免费额度用尽",
            401: "需要 API key",
            403: "无权限",
            404: "路径不存在",
            400: "请求参数有误",
        }.get(exc.status, f"HTTP {exc.status}")
        text = f"{label}：{message}" if message else label
        return text[:limit]
    return f"{type(exc).__name__}: {exc}"[:limit]


class ApiClient:
    def __init__(
        self,
        cache_dir: Path,
        *,
        ttl_hours: float = 8,
        interval: float = 0.35,
        max_retries: int = 5,
        user_agent: str = "litpush/1.0",
        polite_email: str = "",
        timeout: float = 45.0,
    ):
        self.cache_dir = Path(cache_dir)
        self.cache_dir.mkdir(parents=True, exist_ok=True)
        self.ttl = ttl_hours * 3600
        self.interval = interval
        self.max_retries = max_retries
        self.user_agent = user_agent
        self.polite_email = polite_email
        self.timeout = timeout
        self._last = 0.0
        self._lock = threading.Lock()
        self._offline_streak = 0
        self.stats = {"hit": 0, "miss": 0, "error": 0}

    # ---------------------------------------------------------------- cache
    def _cache_path(self, url: str, kind: str) -> Path:
        digest = hashlib.sha1(url.encode("utf-8")).hexdigest()[:24]
        host = urllib.parse.urlparse(url).netloc.replace(":", "_") or "unknown"
        return self.cache_dir / f"{host}-{digest}.{kind}"

    def _read_cache(self, url: str, kind: str, *, ignore_ttl: bool = False) -> Any | None:
        path = self._cache_path(url, kind)
        if not path.exists():
            return None
        if not ignore_ttl and self.ttl > 0 and time.time() - path.stat().st_mtime > self.ttl:
            return None
        try:
            text = path.read_text(encoding="utf-8")
            return json.loads(text) if kind == "json" else text
        except (OSError, json.JSONDecodeError):
            return None

    def _write_cache(self, url: str, kind: str, payload: Any) -> None:
        path = self._cache_path(url, kind)
        tmp = path.with_suffix(path.suffix + ".tmp")
        try:
            tmp.write_text(
                json.dumps(payload, ensure_ascii=False) if kind == "json" else str(payload),
                encoding="utf-8",
            )
            tmp.replace(path)
        except OSError:
            pass

    # ---------------------------------------------------------------- fetch
    def _throttle(self) -> None:
        with self._lock:
            wait = self.interval - (time.monotonic() - self._last)
            if wait > 0:
                time.sleep(wait)
            self._last = time.monotonic()

    def _request(self, url: str) -> str:
        headers = {
            "User-Agent": self.user_agent,
            "Accept": "*/*",
        }
        if self.polite_email:
            headers["From"] = self.polite_email

        if self._offline_streak >= 3:
            # 连续三次连不上：直接失败，别让整轮跑拖着空转
            raise HttpError(url, 0, "网络不可用（连续多次连接失败，本轮放弃）")

        delay = 2.0
        last_exc: Exception | None = None
        for attempt in range(1, self.max_retries + 1):
            self._throttle()
            req = urllib.request.Request(url, headers=headers)
            try:
                with urllib.request.urlopen(req, timeout=self.timeout) as resp:
                    data = resp.read().decode("utf-8", errors="replace")
                    self._offline_streak = 0
                    return data
            except urllib.error.HTTPError as exc:
                self._offline_streak = 0
                body = ""
                try:
                    body = exc.read().decode("utf-8", errors="replace")[:300]
                except Exception:
                    pass
                last_exc = HttpError(url, exc.code, body)
                # 4xx（429 除外）没有重试价值
                if 400 <= exc.code < 500 and exc.code != 429:
                    raise last_exc from None
                if exc.code == 429:
                    # 限流时别死等：最多试 3 次，然后交给上层用旧缓存兜底
                    if attempt >= 3:
                        raise last_exc from None
                    wait = min(8.0 * attempt, 30.0)
                else:
                    retry_after = exc.headers.get("Retry-After") if exc.headers else None
                    wait = float(retry_after) if retry_after and retry_after.isdigit() else delay
                    delay = min(delay * 2, 60.0)
            except Exception as exc:  # 超时 / TLS / 连接重置
                last_exc = exc
                # 连接类错误最多试 2 次，快速把"网络还没起来"暴露出来
                if attempt >= 2:
                    self._offline_streak += 1
                    self.stats["error"] += 1
                    raise HttpError(url, 0, f"{type(exc).__name__}: {exc}") from None
                wait = delay
                delay = min(delay * 2, 60.0)
            time.sleep(min(wait, 90.0) * (1 + random.random() * 0.25))
        self.stats["error"] += 1
        raise HttpError(url, getattr(last_exc, "status", 0), str(last_exc))

    def _stale_fallback(self, url: str, kind: str, exc: Exception) -> Any | None:
        """接口限流/故障时，用过期缓存顶上，保证当天照样有结果。"""
        if isinstance(exc, HttpError) and exc.status in (0, 429, 500, 502, 503, 504):
            cached = self._read_cache(url, kind, ignore_ttl=True)
            if cached is not None:
                self.stats["stale"] = self.stats.get("stale", 0) + 1
                return cached
        return None

    def get_text(self, url: str, *, refresh: bool = False) -> str:
        if not refresh:
            cached = self._read_cache(url, "txt")
            if cached is not None:
                self.stats["hit"] += 1
                return cached
        self.stats["miss"] += 1
        try:
            text = self._request(url)
        except HttpError as exc:
            stale = self._stale_fallback(url, "txt", exc)
            if stale is not None:
                return stale
            raise
        self._write_cache(url, "txt", text)
        return text

    def get_json(
        self,
        url: str,
        params: dict[str, Any] | None = None,
        *,
        refresh: bool = False,
        polite: bool = True,
    ) -> Any:
        params = dict(params or {})
        if polite and self.polite_email:
            params.setdefault("mailto", self.polite_email)
        full = url
        if params:
            full = url + ("&" if "?" in url else "?") + urllib.parse.urlencode(params, doseq=True)
        if not refresh:
            cached = self._read_cache(full, "json")
            if cached is not None:
                self.stats["hit"] += 1
                return cached
        self.stats["miss"] += 1
        try:
            text = self._request(full)
        except HttpError as exc:
            stale = self._stale_fallback(full, "json", exc)
            if stale is not None:
                return stale
            raise
        try:
            data = json.loads(text)
        except json.JSONDecodeError:
            # 响应被截断（代理/网络抖动）时重试一次
            text = self._request(full)
            try:
                data = json.loads(text)
            except json.JSONDecodeError as exc:
                raise HttpError(full, 0, f"JSON 解析失败（响应可能被截断）: {exc}") from exc
        self._write_cache(full, "json", data)
        return data
