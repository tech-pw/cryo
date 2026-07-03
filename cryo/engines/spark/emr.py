from __future__ import annotations

import time
from collections.abc import Mapping
from dataclasses import dataclass
from enum import StrEnum
from typing import TYPE_CHECKING, Any

if TYPE_CHECKING:
    from mypy_boto3_emr_serverless import EMRServerlessClient as Boto3EMRClient


class JobRunState(StrEnum):
    PENDING = "PENDING"
    SCHEDULED = "SCHEDULED"
    RUNNING = "RUNNING"
    SUCCESS = "SUCCESS"
    FAILED = "FAILED"
    CANCELLING = "CANCELLING"
    CANCELLED = "CANCELLED"


TERMINAL_STATES = frozenset({JobRunState.SUCCESS, JobRunState.FAILED, JobRunState.CANCELLED})
SUCCESS_STATES = frozenset({JobRunState.SUCCESS})


@dataclass(frozen=True)
class EMRServerlessConfig:
    application_id: str
    execution_role_arn: str
    region: str = "us-east-1"
    s3_logs_bucket: str | None = None
    s3_logs_prefix: str = "cryo-spark-logs"
    spark_submit_params: Mapping[str, str] | None = None
    entry_point: str = "s3://cryo-artifacts/cryo_spark_runner.py"
    poll_interval_seconds: int = 10
    timeout_seconds: int = 3600

    @classmethod
    def from_config(cls, config: Mapping[str, Any]) -> EMRServerlessConfig:
        emr_config = config.get("emr", {})
        return cls(
            application_id=str(emr_config.get("application_id") or config.get("application_id")),
            execution_role_arn=str(
                emr_config.get("execution_role_arn") or config.get("execution_role_arn")
            ),
            region=str(emr_config.get("region") or config.get("region", "us-east-1")),
            s3_logs_bucket=emr_config.get("s3_logs_bucket") or config.get("s3_logs_bucket"),
            s3_logs_prefix=str(
                emr_config.get("s3_logs_prefix") or config.get("s3_logs_prefix", "cryo-spark-logs")
            ),
            spark_submit_params=emr_config.get("spark_submit_params")
            or config.get("spark_submit_params"),
            entry_point=str(
                emr_config.get("entry_point")
                or config.get("entry_point", "s3://cryo-artifacts/cryo_spark_runner.py")
            ),
            poll_interval_seconds=int(
                emr_config.get("poll_interval_seconds") or config.get("poll_interval_seconds", 10)
            ),
            timeout_seconds=int(
                emr_config.get("timeout_seconds") or config.get("timeout_seconds", 3600)
            ),
        )


@dataclass(frozen=True)
class JobRunResult:
    job_run_id: str
    state: JobRunState
    state_details: str | None = None
    total_execution_duration_seconds: int | None = None


class EMRServerlessClient:
    """Client for AWS EMR Serverless job submission and monitoring."""

    def __init__(self, config: EMRServerlessConfig) -> None:
        self._config = config
        self._client: Boto3EMRClient | None = None

    @property
    def client(self) -> Any:
        if self._client is None:
            import boto3
            from botocore.config import Config as BotoConfig

            self._client = boto3.client(
                "emr-serverless",
                region_name=self._config.region,
                config=BotoConfig(retries={"max_attempts": 3, "mode": "adaptive"}),
            )
        return self._client

    def submit_spark_sql(
        self,
        sql: str,
        *,
        job_name: str = "cryo-maintenance",
        catalog: str = "glue_catalog",
    ) -> str:
        """Submit a Spark SQL statement as an EMR Serverless job.

        Args:
            sql: The SQL statement to execute
            job_name: Name for the EMR job run
            catalog: Iceberg catalog name for Spark

        Returns:
            The job run ID
        """
        spark_submit: dict[str, Any] = {
            "entryPoint": self._config.entry_point,
            "entryPointArguments": [
                "--sql",
                sql,
                "--catalog",
                catalog,
            ],
            "sparkSubmitParameters": self._build_spark_params(),
        }

        monitoring: dict[str, Any] = {}
        if self._config.s3_logs_bucket:
            monitoring["s3MonitoringConfiguration"] = {
                "logUri": f"s3://{self._config.s3_logs_bucket}/{self._config.s3_logs_prefix}/",
            }

        response = self.client.start_job_run(
            applicationId=self._config.application_id,
            executionRoleArn=self._config.execution_role_arn,
            name=job_name,
            jobDriver={"sparkSubmit": spark_submit},
            configurationOverrides={"monitoringConfiguration": monitoring} if monitoring else {},
        )
        return str(response["jobRunId"])

    def get_job_run(self, job_run_id: str) -> JobRunResult:
        """Get the current state of a job run."""
        response = self.client.get_job_run(
            applicationId=self._config.application_id,
            jobRunId=job_run_id,
        )
        job_run = response["jobRun"]
        return JobRunResult(
            job_run_id=job_run_id,
            state=JobRunState(job_run["state"]),
            state_details=job_run.get("stateDetails"),
            total_execution_duration_seconds=job_run.get("totalExecutionDurationSeconds"),
        )

    def wait_for_completion(self, job_run_id: str) -> JobRunResult:
        """Poll until the job reaches a terminal state or times out."""
        deadline = time.monotonic() + self._config.timeout_seconds
        while time.monotonic() < deadline:
            result = self.get_job_run(job_run_id)
            if result.state in TERMINAL_STATES:
                return result
            time.sleep(self._config.poll_interval_seconds)
        result = self.get_job_run(job_run_id)
        if result.state not in TERMINAL_STATES:
            raise TimeoutError(
                f"Job {job_run_id} did not complete within {self._config.timeout_seconds}s"
            )
        return result

    def cancel_job_run(self, job_run_id: str) -> None:
        """Request cancellation of a running job."""
        self.client.cancel_job_run(
            applicationId=self._config.application_id,
            jobRunId=job_run_id,
        )

    def _build_spark_params(self) -> str:
        base_params = {
            "--conf": "spark.sql.catalog.glue_catalog=org.apache.iceberg.spark.SparkCatalog",
            "--conf spark.sql.catalog.glue_catalog.catalog-impl": (
                "org.apache.iceberg.aws.glue.GlueCatalog"
            ),
            "--conf spark.sql.catalog.glue_catalog.warehouse": "s3://your-warehouse/",
            "--conf spark.sql.extensions": (
                "org.apache.iceberg.spark.extensions.IcebergSparkSessionExtensions"
            ),
        }
        if self._config.spark_submit_params:
            base_params.update(self._config.spark_submit_params)
        return " ".join(
            f"{key}={value}" if "=" not in key else f"{key} {value}"
            for key, value in base_params.items()
        )
