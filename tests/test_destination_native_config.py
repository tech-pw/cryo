from __future__ import annotations

import pytest
from pydantic import ValidationError

from cryo.api.schemas import DestinationCreate, DestinationTestRequest, validate_native_engine_config
from cryo.domain.enums import EngineType


def test_validate_native_engine_config_accepts_go_and_rust() -> None:
    validate_native_engine_config(EngineType.NATIVE, {"engine_impl": "go"})
    validate_native_engine_config(EngineType.NATIVE, {"engine_impl": "rust"})


def test_validate_native_engine_config_rejects_unknown_impl() -> None:
    with pytest.raises(ValueError, match="engine_impl"):
        validate_native_engine_config(EngineType.NATIVE, {"engine_impl": "java"})


def test_destination_create_rejects_invalid_native_impl() -> None:
    with pytest.raises(ValidationError):
        DestinationCreate(
            name="native-bad",
            engine=EngineType.NATIVE,
            config={"engine_impl": "java"},
        )


def test_destination_test_request_rejects_invalid_native_impl() -> None:
    with pytest.raises(ValidationError):
        DestinationTestRequest(
            engine=EngineType.NATIVE,
            config={"engine_impl": "java"},
        )
