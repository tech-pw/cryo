from __future__ import annotations

import uuid

from cryo.db.models import Destination
from cryo.db.repositories.job_store import _destination_config
from cryo.domain.enums import Mode
from cryo.governor.admission import AdmissionConfig, AdmissionJob, decide_job_admission
from cryo.governor.governor import _admission_config_from_destination
from cryo.worker.worker import DestinationConfig


def _destination(
    *,
    config: dict | None = None,
    offpeak_start: int | None = None,
    offpeak_end: int | None = None,
) -> Destination:
    return Destination(
        id=uuid.uuid4(),
        name="test-destination",
        engine="trino",
        config=config or {"catalog": "cdp"},
        offpeak_start=offpeak_start,
        offpeak_end=offpeak_end,
    )


def test_destination_config_uses_db_offpeak_columns_when_config_json_omits_them() -> None:
    destination = _destination(offpeak_start=15, offpeak_end=5)

    config = _destination_config(destination)

    assert config == DestinationConfig(
        id=str(destination.id),
        engine="trino",
        config={"catalog": "cdp", "offpeak_start_ist": 15, "offpeak_end_ist": 5},
    )


def test_destination_config_prefers_explicit_config_json_offpeak_values() -> None:
    destination = _destination(
        config={
            "catalog": "cdp",
            "offpeak_start_ist": 0,
            "offpeak_end_ist": 7,
        },
        offpeak_start=15,
        offpeak_end=5,
    )

    config = _destination_config(destination)

    assert config.config["offpeak_start_ist"] == 0
    assert config.config["offpeak_end_ist"] == 7


def test_destination_config_offpeak_columns_drive_governor_admission() -> None:
    destination = _destination(offpeak_start=15, offpeak_end=5)
    destination_config = _destination_config(destination)
    admission_config = _admission_config_from_destination(
        destination_config, AdmissionConfig()
    )

    decision = decide_job_admission(
        AdmissionJob(
            job_id="job-1",
            table_id="table-1",
            destination_id=destination_config.id,
            mode=Mode.DEEP,
        ),
        admission_config,
        now=__import__("datetime").datetime(
            2026, 7, 6, 18, 30, tzinfo=__import__("datetime").UTC
        ),
    )

    assert decision.admissible is True


def test_destination_without_offpeak_allows_deep_anytime() -> None:
    destination = _destination()
    destination_config = _destination_config(destination)
    admission_config = _admission_config_from_destination(
        destination_config, AdmissionConfig()
    )

    assert "offpeak_start_ist" not in destination_config.config
    assert "offpeak_end_ist" not in destination_config.config
    assert admission_config.offpeak_start_ist == 0
    assert admission_config.offpeak_end_ist == 0

    decision = decide_job_admission(
        AdmissionJob(
            job_id="job-1",
            table_id="table-1",
            destination_id=destination_config.id,
            mode=Mode.DEEP,
        ),
        admission_config,
        now=__import__("datetime").datetime(
            2026, 6, 30, 8, 0, tzinfo=__import__("datetime").UTC
        ),
    )

    assert decision.admissible is True
