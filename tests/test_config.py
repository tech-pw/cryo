from __future__ import annotations

from cryo.config import Settings


def test_settings_defaults_are_development_safe() -> None:
    settings = Settings(_env_file=None)

    assert settings.environment == "dev"
    assert settings.database_url.startswith("postgresql+psycopg://")
    assert settings.redis_url.startswith("redis://")
    assert settings.cors_allow_origins == ["http://localhost:5173"]
    assert settings.scan_num_chunks == 4
    assert settings.default_per_replica_optimize_cap == 5
