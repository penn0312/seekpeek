"""Environment-driven configuration."""

from __future__ import annotations

import os
from dataclasses import dataclass

from seekpeek.errors import MISSING_KEY_HINT, ConfigError

DEFAULT_BASE_URL = "https://api.deepseek.com"
ENV_PREFIX = "SEEKPEEK_"

__all__ = ["DEFAULT_BASE_URL", "ENV_PREFIX", "MISSING_KEY_HINT", "ConfigError", "Settings"]


def _get_float(environ: dict[str, str], name: str, default: float) -> float:
    raw = (environ.get(name) or "").strip()
    if not raw:
        return default
    try:
        value = float(raw)
    except ValueError as exc:
        raise ConfigError(f"{name} must be a number, got {raw!r}.") from exc
    if value < 0:
        raise ConfigError(f"{name} must not be negative, got {raw!r}.")
    return value


def _get_int(environ: dict[str, str], name: str, default: int) -> int:
    value = _get_float(environ, name, float(default))
    if not value.is_integer():
        raise ConfigError(f"{name} must be a whole number, got {value!r}.")
    return int(value)


@dataclass(frozen=True, slots=True)
class Settings:
    """Resolved runtime settings."""

    api_key: str
    base_url: str = DEFAULT_BASE_URL
    timeout: float = 60.0
    max_retries: int = 2
    search_qps: float = 5.0
    fetch_qps: float = 5.0

    @classmethod
    def from_env(cls, env: dict[str, str] | None = None) -> Settings:
        environ = os.environ if env is None else env
        api_key = (environ.get("DEEPSEEK_API_KEY") or "").strip()
        base_url = (environ.get("DEEPSEEK_BASE_URL") or DEFAULT_BASE_URL).strip().rstrip("/")
        return cls(
            api_key=api_key,
            base_url=base_url,
            timeout=_get_float(environ, f"{ENV_PREFIX}TIMEOUT", 60.0),
            max_retries=_get_int(environ, f"{ENV_PREFIX}MAX_RETRIES", 2),
            search_qps=_get_float(environ, f"{ENV_PREFIX}SEARCH_QPS", 5.0),
            fetch_qps=_get_float(environ, f"{ENV_PREFIX}FETCH_QPS", 5.0),
        )

    def require_api_key(self) -> str:
        """Return the key, or explain how to provide one."""
        if not self.api_key:
            raise ConfigError(MISSING_KEY_HINT)
        return self.api_key

    def describe(self) -> list[tuple[str, str]]:
        """Settings as (label, value) pairs, for `seekpeek doctor`."""
        return [
            ("DEEPSEEK_API_KEY", f"{self.api_key[:6]}…{self.api_key[-4:]}" if self.api_key else "(missing)"),
            ("DEEPSEEK_BASE_URL", self.base_url),
            (f"{ENV_PREFIX}TIMEOUT", f"{self.timeout:g}s"),
            (f"{ENV_PREFIX}MAX_RETRIES", str(self.max_retries)),
            (f"{ENV_PREFIX}SEARCH_QPS", f"{self.search_qps:g}"),
            (f"{ENV_PREFIX}FETCH_QPS", f"{self.fetch_qps:g}"),
        ]
