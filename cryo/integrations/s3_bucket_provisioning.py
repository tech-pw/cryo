"""One-click auto-provision + teardown of S3 server access logging (§4.3/§4.3.1, §9.4).

This module writes to the user's AWS account -- it is the one part of the S3 request-cost
tracking feature that mutates infrastructure rather than just reading it. Everything here is
only ever invoked from an explicit, confirm-then-act user action (never from a scheduled job),
matching how the rest of Cryo treats irreversible/external-effect actions.
"""

from __future__ import annotations

import json
import logging
import secrets
from dataclasses import dataclass, field
from typing import Any

from botocore.exceptions import ClientError
from sqlalchemy.orm import Session

from cryo.db.models import S3BucketLogConfig
from cryo.integrations import s3_bucket_logging

logger = logging.getLogger(__name__)

_POLICY_SID_PREFIX = "CryoS3AccessLogDelivery"


@dataclass(frozen=True)
class ProvisionResult:
    ok: bool
    log_target_bucket: str | None = None
    log_target_prefix: str | None = None
    error: str | None = None


@dataclass(frozen=True)
class TeardownResult:
    ok: bool
    policy_reverted: bool = False
    warnings: list[str] = field(default_factory=list)
    error: str | None = None


def propose_target_bucket_name(source_bucket: str) -> str:
    """§4.3 step 1 -- name derived only from the user's own bucket, never a fixed/shared name."""
    return f"{source_bucket}-cryo-access-logs"


def _create_target_bucket(client: Any, *, bucket: str, region: str) -> str:
    """Create the target bucket, retrying once with a random suffix on a name collision.

    S3 bucket names are unique globally across every AWS account in the partition, so a name
    derived from the user's own already-unique bucket name is extremely unlikely to collide --
    but not impossible (someone else may already hold that exact string).
    """
    kwargs: dict = {"Bucket": bucket}
    if region != "us-east-1":
        kwargs["CreateBucketConfiguration"] = {"LocationConstraint": region}
    try:
        client.create_bucket(**kwargs)
        return bucket
    except ClientError as exc:
        code = exc.response.get("Error", {}).get("Code", "")
        if code not in ("BucketAlreadyExists", "BucketAlreadyOwnedByYou"):
            raise
        if code == "BucketAlreadyOwnedByYou":
            # Already ours from a previous attempt -- reuse it.
            return bucket
        suffix = secrets.token_hex(4)
        retry_bucket = f"{bucket}-{suffix}"
        kwargs["Bucket"] = retry_bucket
        client.create_bucket(**kwargs)
        return retry_bucket


def _merge_policy_statement(
    client: Any, *, target_bucket: str, source_bucket: str, prefix: str, account_id: str
) -> str:
    """§4.3.1 -- read the target bucket's existing policy and merge in a delivery-grant
    statement, never overwrite. Uses a bucket-policy grant (not the legacy ACL grantee option,
    which silently does nothing under the now-default Bucket-owner-enforced Object Ownership).
    """
    try:
        existing_raw = client.get_bucket_policy(Bucket=target_bucket)["Policy"]
        policy = json.loads(existing_raw)
    except ClientError as exc:
        if exc.response.get("Error", {}).get("Code") != "NoSuchBucketPolicy":
            raise
        policy = {"Version": "2012-10-17", "Statement": []}

    sid = f"{_POLICY_SID_PREFIX}{source_bucket.replace('.', '-')}"
    statements = [s for s in policy.get("Statement", []) if s.get("Sid") != sid]
    statements.append(
        {
            "Sid": sid,
            "Effect": "Allow",
            "Principal": {"Service": "logging.s3.amazonaws.com"},
            "Action": "s3:PutObject",
            "Resource": f"arn:aws:s3:::{target_bucket}/{prefix}*",
            "Condition": {
                "StringEquals": {"aws:SourceAccount": account_id},
                "ArnLike": {"aws:SourceArn": f"arn:aws:s3:::{source_bucket}"},
            },
        }
    )
    policy["Statement"] = statements
    client.put_bucket_policy(Bucket=target_bucket, Policy=json.dumps(policy))
    return sid


def provision_logging(
    session: Session, config: S3BucketLogConfig, *, target_bucket: str | None = None
) -> ProvisionResult:
    """§4.3 -- turn on server access logging for ``config.bucket``, creating/using
    ``target_bucket`` (or a name derived from the source bucket) as the log destination.

    Requires the optional write-permission tier (§11): ``s3:PutBucketLogging`` on the source
    bucket, ``s3:GetBucketPolicy``/``s3:PutBucketPolicy`` on the target, and ``s3:CreateBucket``
    only if a new target bucket is being created. Caller is responsible for committing.
    """
    creds = s3_bucket_logging.credentials_for_config(config)
    boto_session = s3_bucket_logging.boto_session_for_config(config)
    if creds is None or boto_session is None:
        return ProvisionResult(ok=False, error="No AWS credentials configured for this bucket")
    client = boto_session.client("s3")

    region = config.region or creds.region or "us-east-1"
    resolved_target = target_bucket or propose_target_bucket_name(config.bucket)
    prefix = f"{config.bucket}/"

    try:
        actual_target = _create_target_bucket(client, bucket=resolved_target, region=region)

        account_id = boto_session.client("sts").get_caller_identity()["Account"]

        sid = _merge_policy_statement(
            client,
            target_bucket=actual_target,
            source_bucket=config.bucket,
            prefix=prefix,
            account_id=account_id,
        )

        client.put_bucket_logging(
            Bucket=config.bucket,
            BucketLoggingStatus={
                "LoggingEnabled": {
                    "TargetBucket": actual_target,
                    "TargetPrefix": prefix,
                    "TargetObjectKeyFormat": {
                        "PartitionedPrefix": {"PartitionDateSource": "EventTime"}
                    },
                }
            },
        )
    except ClientError as exc:
        code = exc.response.get("Error", {}).get("Code", "Unknown")
        message = exc.response.get("Error", {}).get("Message", str(exc))
        logger.warning(
            "S3 logging auto-provision failed for %s: %s: %s", config.bucket, code, message
        )
        return ProvisionResult(ok=False, error=f"{code}: {message}")
    except Exception as exc:  # pragma: no cover - defensive
        logger.exception("S3 logging auto-provision failed for %s", config.bucket)
        return ProvisionResult(ok=False, error=str(exc))

    # logging_status is set to "enabled" because the *configuration* call succeeded -- this is
    # NOT the same as "verified delivering", which only flips once an ingestion run actually
    # finds a log file under the target prefix (§4.3.1's "verify, don't assume"). The frontend
    # distinguishes the two via last_ingested_at (see S3LogConfigPanel's awaitingFirstDelivery).
    config.logging_status = "enabled"
    config.log_target_bucket = actual_target
    config.log_target_prefix = prefix
    config.manual_override = False
    config.enabled = True
    config.cryo_added_policy_sids = [sid]

    return ProvisionResult(ok=True, log_target_bucket=actual_target, log_target_prefix=prefix)


def teardown_bucket(
    session: Session, config: S3BucketLogConfig, *, revert_policy: bool
) -> TeardownResult:
    """§9.4 -- remove Cryo's tracking of this bucket. Only ever reverts a bucket-policy
    statement Cryo itself added (``cryo_added_policy_sids``) -- never touches statements it
    didn't add. Caller is responsible for committing/deleting the config row afterwards."""
    warnings: list[str] = []
    reverted = False

    if revert_policy and config.cryo_added_policy_sids and config.log_target_bucket:
        client = s3_bucket_logging.s3_client_for_config(config)
        if client is None:
            warnings.append("No credentials available to revert the target bucket policy")
        else:
            try:
                existing_raw = client.get_bucket_policy(Bucket=config.log_target_bucket)["Policy"]
                policy = json.loads(existing_raw)
                remaining = [
                    s
                    for s in policy.get("Statement", [])
                    if s.get("Sid") not in config.cryo_added_policy_sids
                ]
                if remaining:
                    policy["Statement"] = remaining
                    client.put_bucket_policy(
                        Bucket=config.log_target_bucket, Policy=json.dumps(policy)
                    )
                else:
                    client.delete_bucket_policy(Bucket=config.log_target_bucket)
                reverted = True
            except ClientError as exc:
                code = exc.response.get("Error", {}).get("Code", "Unknown")
                if code == "NoSuchBucketPolicy":
                    reverted = True
                else:
                    warnings.append(f"Could not revert target bucket policy: {code}")
            except Exception as exc:  # pragma: no cover - defensive
                warnings.append(f"Could not revert target bucket policy: {exc}")

    try:
        _drop_log_table(config)
    except Exception as exc:  # pragma: no cover - defensive: teardown of the DB row must
        # still succeed even if the Trino table drop fails (e.g. Trino unreachable) -- an
        # orphaned external table definition is a harmless cleanup gap, not a correctness bug.
        warnings.append(f"Could not drop the Trino log table: {exc}")

    return TeardownResult(ok=True, policy_reverted=reverted, warnings=warnings)


def _drop_log_table(config: S3BucketLogConfig) -> None:
    from cryo.config import get_settings
    from cryo.engines.trino.client import connect
    from cryo.scanner.s3_request_cost_ingest import _coordinator, _fq_table, _log_table_name

    settings = get_settings()
    catalog = settings.s3_log_trino_catalog
    schema = settings.s3_log_trino_schema
    coordinator = _coordinator(settings, catalog=catalog, schema=schema)
    if coordinator is None:
        return
    conn = connect(coordinator)
    cursor = conn.cursor()
    try:
        table = _log_table_name(config.bucket)
        cursor.execute(f"DROP TABLE IF EXISTS {_fq_table(catalog, schema, table)}")
    finally:
        cursor.close()
        conn.close()
