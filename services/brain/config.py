"""Validated settings for the conversational runtime; no model dependency."""
from dataclasses import dataclass
import math
import os


def number(name, default, low, high):
    try:
        value = float(os.getenv(name, str(default)))
        return max(low, min(high, value)) if math.isfinite(value) else default
    except (ValueError, TypeError):
        return default


def enabled(name, default=False):
    return os.getenv(name, str(default)).strip().lower() in {'true', '1', 'yes', 'on'}


@dataclass(frozen=True, slots=True)
class BrainConfig:
    timeout: float = 45
    max_active: int = 8
    guild_active: int = 3
    user_active: int = 2
    context_chars: int = 24000
    external_enabled: bool = True
    local_enabled: bool = False
    local_url: str = 'http://127.0.0.1:11434/v1'
    local_model: str = ''
    local_timeout: float = 15
    provider_timeout: float = 12
    provider_daily_limit: int = 1000
    retention_days: int = 90

    @classmethod
    def from_env(cls):
        return cls(
            timeout=number('MIKO_REQUEST_TIMEOUT', 45, 1, 120),
            max_active=int(number('MIKO_MAX_ACTIVE', 8, 1, 64)),
            guild_active=int(number('MIKO_GUILD_ACTIVE', 3, 1, 16)),
            user_active=int(number('MIKO_USER_ACTIVE', 2, 1, 4)),
            context_chars=int(number('MIKO_CONTEXT_CHARS', 24000, 16000, 64000)),
            external_enabled=enabled('MIKO_EXTERNAL_AI', True),
            local_enabled=enabled('MIKO_LOCAL_AI'),
            local_url=os.getenv('MIKO_LOCAL_URL', 'http://127.0.0.1:11434/v1').strip(),
            local_model=os.getenv('MIKO_LOCAL_MODEL', '').strip(),
            local_timeout=number('MIKO_LOCAL_TIMEOUT', 15, 1, 90),
            provider_timeout=number('MIKO_PROVIDER_TIMEOUT', 12, 1, 30),
            provider_daily_limit=int(number('MIKO_PROVIDER_DAILY_LIMIT', 1000, 0, 100000)),
            retention_days=int(number('MIKO_MEMORY_RETENTION_DAYS', 90, 1, 365)),
        )
