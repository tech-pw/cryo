"""Tests for the native engine Python wrapper.

Worker maintenance runs the ``cryo-engine-go`` subprocess. That path is exercised by
``tests/test_native_runner.py``, ``tests/test_native_audit_fields.py``, and
``tests/integration/native/test_native_*_e2e.py``. These tests only cover construction.
"""

from __future__ import annotations

from unittest.mock import patch

from cryo.engines.native.engine import NativeEngine, is_native_available


class TestNativeEngineAvailability:
    def test_is_native_available_returns_bool(self):
        result = is_native_available()
        assert isinstance(result, bool)

    def test_engine_initializes_without_native(self):
        """Engine should initialize even when the Go binary isn't built."""
        engine = NativeEngine(config={})
        assert engine is not None

    def test_engine_initializes_when_binary_is_missing(self):
        with patch("cryo.engines.native.engine.is_native_available", return_value=False):
            assert NativeEngine(config={}) is not None

    def test_probe_capacity_works_without_native(self):
        """probe_capacity() should always return a valid CapacitySnapshot."""
        engine = NativeEngine(config={})
        capacity = engine.probe_capacity()
        assert capacity.capacity >= 1
        assert capacity.used >= 0
