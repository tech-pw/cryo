"""Tests for P2 auto-provision + teardown (docs/s3-request-cost-tracking.md §4.3/§4.3.1, §9.4)."""

from __future__ import annotations

import json
from uuid import uuid4

from botocore.exceptions import ClientError

from cryo.db.models import S3BucketLogConfig, Source
from cryo.integrations import s3_bucket_logging, s3_bucket_provisioning
from cryo.security.crypto import encrypt_secret


def _seed_source(session) -> Source:
    source = Source(
        id=uuid4(), name=f"source-{uuid4()}", type="rest", config={"catalog_name": "lake"}
    )
    session.add(source)
    session.flush()
    return source


def _config_with_creds(session, source, *, bucket="lake-bucket") -> S3BucketLogConfig:
    config = S3BucketLogConfig(
        source_id=source.id,
        bucket=bucket,
        region="us-east-1",
        access_key_id="AKIAEXAMPLE",
        secret_ref=encrypt_secret("shh"),
    )
    session.add(config)
    session.flush()
    return config


class _FakeProvisionClient:
    def __init__(self, *, existing_policy=None, create_bucket_error=None):
        self.existing_policy = existing_policy
        self.create_bucket_error = create_bucket_error
        self.created_buckets: list[str] = []
        self.put_policies: dict[str, dict] = {}
        self.logging_calls: list[dict] = []
        self.deleted_policies: list[str] = []

    def create_bucket(self, **kwargs):
        if self.create_bucket_error:
            raise self.create_bucket_error
        self.created_buckets.append(kwargs["Bucket"])

    def get_bucket_policy(self, Bucket):  # noqa: N803
        policy = self.put_policies.get(Bucket, self.existing_policy)
        if policy is None:
            raise ClientError(
                {"Error": {"Code": "NoSuchBucketPolicy", "Message": "none"}}, "GetBucketPolicy"
            )
        return {"Policy": json.dumps(policy)}

    def put_bucket_policy(self, *, Bucket, Policy):  # noqa: N803
        self.put_policies[Bucket] = json.loads(Policy)

    def delete_bucket_policy(self, Bucket):  # noqa: N803
        self.deleted_policies.append(Bucket)
        self.put_policies.pop(Bucket, None)

    def put_bucket_logging(self, **kwargs):
        self.logging_calls.append(kwargs)

    def get_caller_identity(self):
        return {"Account": "123456789012"}


class _FakeBotoSession:
    def __init__(self, client):
        self._client = client

    def client(self, name, **kwargs):
        return self._client


def test_propose_target_bucket_name_derives_from_source_bucket() -> None:
    assert (
        s3_bucket_provisioning.propose_target_bucket_name("my-lake") == "my-lake-cryo-access-logs"
    )


def test_provision_logging_creates_bucket_merges_policy_and_enables_logging(
    session, monkeypatch
) -> None:
    source = _seed_source(session)
    config = _config_with_creds(session, source)
    fake = _FakeProvisionClient()
    monkeypatch.setattr(s3_bucket_logging, "_session", lambda creds: _FakeBotoSession(fake))

    result = s3_bucket_provisioning.provision_logging(session, config)

    assert result.ok is True
    assert result.log_target_bucket == "lake-bucket-cryo-access-logs"
    assert fake.created_buckets == ["lake-bucket-cryo-access-logs"]
    assert len(fake.logging_calls) == 1
    logging_status = fake.logging_calls[0]["BucketLoggingStatus"]["LoggingEnabled"]
    assert logging_status["TargetBucket"] == "lake-bucket-cryo-access-logs"
    assert logging_status["TargetObjectKeyFormat"] == {
        "PartitionedPrefix": {"PartitionDateSource": "EventTime"}
    }
    assert config.logging_status == "enabled"
    assert config.log_target_bucket == "lake-bucket-cryo-access-logs"
    assert config.enabled is True
    assert config.cryo_added_policy_sids == ["CryoS3AccessLogDeliverylake-bucket"]

    policy = fake.put_policies["lake-bucket-cryo-access-logs"]
    statement = policy["Statement"][0]
    assert statement["Principal"] == {"Service": "logging.s3.amazonaws.com"}
    assert statement["Condition"]["StringEquals"]["aws:SourceAccount"] == "123456789012"


def test_provision_logging_merges_into_existing_policy_without_dropping_statements(
    session, monkeypatch
) -> None:
    source = _seed_source(session)
    config = _config_with_creds(session, source)
    unrelated_statement = {
        "Sid": "SomeUnrelatedStatement",
        "Effect": "Allow",
        "Principal": "*",
        "Action": "s3:GetObject",
        "Resource": "arn:aws:s3:::lake-bucket-cryo-access-logs/other/*",
    }
    fake = _FakeProvisionClient(
        existing_policy={"Version": "2012-10-17", "Statement": [unrelated_statement]}
    )
    monkeypatch.setattr(s3_bucket_logging, "_session", lambda creds: _FakeBotoSession(fake))

    s3_bucket_provisioning.provision_logging(session, config)

    policy = fake.put_policies["lake-bucket-cryo-access-logs"]
    sids = {s["Sid"] for s in policy["Statement"]}
    assert "SomeUnrelatedStatement" in sids
    assert "CryoS3AccessLogDeliverylake-bucket" in sids
    assert len(policy["Statement"]) == 2


def test_provision_logging_retries_with_suffix_on_bucket_already_exists(
    session, monkeypatch
) -> None:
    source = _seed_source(session)
    config = _config_with_creds(session, source)
    error = ClientError(
        {"Error": {"Code": "BucketAlreadyExists", "Message": "taken"}}, "CreateBucket"
    )
    fake = _FakeProvisionClient(create_bucket_error=error)
    monkeypatch.setattr(s3_bucket_logging, "_session", lambda creds: _FakeBotoSession(fake))

    # First call raises for every bucket name (simulating a globally taken name); the retry
    # path should append a suffix and try exactly once more, not loop.
    calls = {"count": 0}

    def flaky_create_bucket(**kwargs):
        calls["count"] += 1
        if calls["count"] == 1:
            raise error
        fake.created_buckets.append(kwargs["Bucket"])

    fake.create_bucket = flaky_create_bucket  # type: ignore[method-assign]

    result = s3_bucket_provisioning.provision_logging(session, config)

    assert result.ok is True
    assert len(fake.created_buckets) == 1
    assert fake.created_buckets[0].startswith("lake-bucket-cryo-access-logs-")
    assert fake.created_buckets[0] != "lake-bucket-cryo-access-logs"


def test_provision_logging_without_credentials_fails_cleanly(session) -> None:
    source = _seed_source(session)
    config = S3BucketLogConfig(source_id=source.id, bucket="lake-bucket")
    session.add(config)
    session.flush()

    result = s3_bucket_provisioning.provision_logging(session, config)

    assert result.ok is False
    assert "credentials" in (result.error or "")


def test_provision_logging_surfaces_access_denied(session, monkeypatch) -> None:
    source = _seed_source(session)
    config = _config_with_creds(session, source)
    error = ClientError({"Error": {"Code": "AccessDenied", "Message": "nope"}}, "PutBucketLogging")
    fake = _FakeProvisionClient()
    fake.put_bucket_logging = lambda **kwargs: (_ for _ in ()).throw(error)  # type: ignore[method-assign]
    monkeypatch.setattr(s3_bucket_logging, "_session", lambda creds: _FakeBotoSession(fake))

    result = s3_bucket_provisioning.provision_logging(session, config)

    assert result.ok is False
    assert "AccessDenied" in (result.error or "")
    # Config must not be mutated on failure.
    assert config.logging_status == "unknown"


# ---------------------------------------------------------------------------
# §9.4 teardown
# ---------------------------------------------------------------------------


def test_teardown_reverts_only_cryo_added_statement(session, monkeypatch) -> None:
    source = _seed_source(session)
    config = _config_with_creds(session, source)
    config.log_target_bucket = "log-bucket"
    config.cryo_added_policy_sids = ["CryoS3AccessLogDeliverylake-bucket"]
    unrelated_statement = {
        "Sid": "SomeUnrelatedStatement",
        "Effect": "Allow",
        "Principal": "*",
        "Action": "s3:GetObject",
        "Resource": "arn:aws:s3:::log-bucket/other/*",
    }
    cryo_statement = {
        "Sid": "CryoS3AccessLogDeliverylake-bucket",
        "Effect": "Allow",
        "Principal": {"Service": "logging.s3.amazonaws.com"},
        "Action": "s3:PutObject",
        "Resource": "arn:aws:s3:::log-bucket/lake-bucket/*",
    }
    fake = _FakeProvisionClient(
        existing_policy={
            "Version": "2012-10-17",
            "Statement": [unrelated_statement, cryo_statement],
        }
    )
    monkeypatch.setattr(s3_bucket_logging, "_session", lambda creds: _FakeBotoSession(fake))

    result = s3_bucket_provisioning.teardown_bucket(session, config, revert_policy=True)

    assert result.ok is True
    assert result.policy_reverted is True
    remaining = fake.put_policies["log-bucket"]["Statement"]
    assert len(remaining) == 1
    assert remaining[0]["Sid"] == "SomeUnrelatedStatement"


def test_teardown_deletes_policy_entirely_when_no_statements_remain(session, monkeypatch) -> None:
    source = _seed_source(session)
    config = _config_with_creds(session, source)
    config.log_target_bucket = "log-bucket"
    config.cryo_added_policy_sids = ["CryoS3AccessLogDeliverylake-bucket"]
    cryo_statement = {
        "Sid": "CryoS3AccessLogDeliverylake-bucket",
        "Effect": "Allow",
        "Principal": {"Service": "logging.s3.amazonaws.com"},
        "Action": "s3:PutObject",
        "Resource": "arn:aws:s3:::log-bucket/lake-bucket/*",
    }
    fake = _FakeProvisionClient(
        existing_policy={"Version": "2012-10-17", "Statement": [cryo_statement]}
    )
    monkeypatch.setattr(s3_bucket_logging, "_session", lambda creds: _FakeBotoSession(fake))

    result = s3_bucket_provisioning.teardown_bucket(session, config, revert_policy=True)

    assert result.ok is True
    assert result.policy_reverted is True
    assert fake.deleted_policies == ["log-bucket"]


def test_teardown_without_revert_policy_skips_policy_changes(session, monkeypatch) -> None:
    source = _seed_source(session)
    config = _config_with_creds(session, source)
    config.log_target_bucket = "log-bucket"
    config.cryo_added_policy_sids = ["CryoS3AccessLogDeliverylake-bucket"]
    fake = _FakeProvisionClient()
    monkeypatch.setattr(s3_bucket_logging, "_session", lambda creds: _FakeBotoSession(fake))

    result = s3_bucket_provisioning.teardown_bucket(session, config, revert_policy=False)

    assert result.ok is True
    assert result.policy_reverted is False
    assert fake.put_policies == {}
    assert fake.deleted_policies == []


def test_teardown_without_any_cryo_added_sids_is_a_noop_revert(session) -> None:
    source = _seed_source(session)
    config = _config_with_creds(session, source)
    config.log_target_bucket = "log-bucket"
    config.cryo_added_policy_sids = None

    result = s3_bucket_provisioning.teardown_bucket(session, config, revert_policy=True)

    assert result.ok is True
    assert result.policy_reverted is False
