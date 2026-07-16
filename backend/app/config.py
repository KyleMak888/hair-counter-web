from __future__ import annotations

import os
from dataclasses import dataclass


def _env_int(name: str, default: int) -> int:
    value = os.getenv(name)
    if value is None:
        return default
    try:
        return int(value)
    except ValueError as exc:
        raise RuntimeError(f"{name} must be an integer") from exc


def _env_float(name: str, default: float) -> float:
    value = os.getenv(name)
    if value is None:
        return default
    try:
        return float(value)
    except ValueError as exc:
        raise RuntimeError(f"{name} must be a number") from exc


def _env_bool(name: str, default: bool) -> bool:
    value = os.getenv(name)
    if value is None:
        return default
    normalized = value.strip().lower()
    if normalized in {"1", "true", "yes", "on"}:
        return True
    if normalized in {"0", "false", "no", "off"}:
        return False
    raise RuntimeError(f"{name} must be a boolean")


@dataclass(frozen=True)
class Settings:
    app_name: str = os.getenv("APP_NAME", "Hair Counter API")
    app_version: str = os.getenv("APP_VERSION", "1.0.0")
    max_upload_bytes: int = _env_int("MAX_UPLOAD_BYTES", 20 * 1024 * 1024)
    max_image_pixels: int = _env_int("MAX_IMAGE_PIXELS", 20_000_000)
    max_image_side: int = _env_int("MAX_IMAGE_SIDE", 6000)
    max_processing_concurrency: int = _env_int("MAX_PROCESSING_CONCURRENCY", 2)
    request_timeout_seconds: float = _env_float("REQUEST_TIMEOUT_SECONDS", 45.0)
    max_batch_size: int = _env_int("MAX_BATCH_SIZE", 10)
    database_path: str = os.getenv("DATABASE_PATH", "/tmp/hair-counter.db")
    session_ttl_seconds: int = _env_int("SESSION_TTL_SECONDS", 7 * 24 * 60 * 60)
    secure_cookies: bool = _env_bool("SECURE_COOKIES", False)
    admin_username: str = os.getenv("ADMIN_USERNAME", "")
    admin_password: str = os.getenv("ADMIN_PASSWORD", "")


settings = Settings()
