"""GitOps policy loader for maintenance-as-code.

Allows teams to define maintenance policies in YAML files stored in Git,
with version control, PR-based approvals, and automatic sync to Cryo.
"""

from __future__ import annotations

import logging
from dataclasses import dataclass
from pathlib import Path
from typing import Any

import yaml
from sqlalchemy import select
from sqlalchemy.orm import Session

from cryo.db.models import Preset, PresetRule, Source, Table
from cryo.domain.enums import Mode, OperationType

logger = logging.getLogger(__name__)


@dataclass
class GitOpsPolicyConfig:
    """GitOps policy configuration from YAML."""

    table_pattern: str  # catalog.schema.table or pattern with wildcards
    maintenance: dict[str, Any]
    alerts: dict[str, Any] | None = None
    metadata: dict[str, Any] | None = None


def parse_policy_yaml(yaml_content: str) -> GitOpsPolicyConfig:
    """
    Parse a policy YAML file.
    
    Args:
        yaml_content: YAML content as string
    
    Returns:
        Parsed policy configuration
    
    Example YAML:
        ```yaml
        table: catalog.finance.transactions
        maintenance:
          optimize:
            enabled: true
            cooldown_days: 3
            strategy: zorder
            zorder_columns: [customer_id, transaction_date]
          expire_snapshots:
            enabled: true
            retention_days: 30
          compute_statistics:
            enabled: true
            columns: [customer_id, transaction_date, amount_usd]
        alerts:
          slack_webhook: https://hooks.slack.com/...
          pagerduty_key: xxx
        ```
    """
    data = yaml.safe_load(yaml_content)
    
    return GitOpsPolicyConfig(
        table_pattern=data.get("table", ""),
        maintenance=data.get("maintenance", {}),
        alerts=data.get("alerts"),
        metadata=data.get("metadata"),
    )


def load_policies_from_directory(
    policy_dir: Path,
) -> list[GitOpsPolicyConfig]:
    """
    Load all policy YAML files from a directory.
    
    Args:
        policy_dir: Path to directory containing policy YAML files
    
    Returns:
        List of parsed policy configurations
    """
    policies = []
    
    for yaml_file in policy_dir.rglob("*.yaml"):
        try:
            with open(yaml_file) as f:
                policy = parse_policy_yaml(f.read())
                policies.append(policy)
                logger.info(f"Loaded policy from {yaml_file}: {policy.table_pattern}")
        except Exception as e:
            logger.error(f"Failed to load policy from {yaml_file}: {e}")
            continue
    
    return policies


def sync_policy_to_preset(
    session: Session,
    policy: GitOpsPolicyConfig,
    preset_name: str | None = None,
) -> Preset:
    """
    Sync a GitOps policy to a Cryo preset.
    
    Args:
        session: Database session
        policy: Policy configuration
        preset_name: Optional preset name (defaults to table pattern)
    
    Returns:
        Created or updated preset
    """
    # Find or create preset
    if not preset_name:
        preset_name = f"gitops-{policy.table_pattern.replace('.', '-')}"
    
    stmt = select(Preset).where(Preset.name == preset_name)
    preset = session.execute(stmt).scalar_one_or_none()
    
    if not preset:
        # Create new preset
        # For now, we need a destination_id - in production this would be configured
        # via the GitOps policy or fetched from a default destination
        from cryo.db.models import Destination
        default_dest = session.execute(select(Destination).limit(1)).scalar_one_or_none()
        if not default_dest:
            raise ValueError("No destination configured - cannot create preset")
        
        preset = Preset(
            name=preset_name,
            destination_id=default_dest.id,
            mode=Mode.STANDARD,
            params={},
        )
        session.add(preset)
    
    # Update preset parameters from policy
    maintenance = policy.maintenance
    params = {}
    
    if "optimize" in maintenance:
        opt = maintenance["optimize"]
        if opt.get("enabled"):
            params["cooldown_days"] = opt.get("cooldown_days", 3)
            if opt.get("strategy") == "zorder":
                params["strategy"] = "zorder"
                params["zorder_columns"] = opt.get("zorder_columns", [])
    
    if "expire_snapshots" in maintenance:
        exp = maintenance["expire_snapshots"]
        if exp.get("enabled"):
            params["retention_days"] = exp.get("retention_days", 7)
    
    if "compute_statistics" in maintenance:
        stats = maintenance["compute_statistics"]
        if stats.get("enabled"):
            params["compute_statistics"] = True
            params["stats_columns"] = stats.get("columns", [])
    
    preset.params = params
    
    session.commit()
    
    return preset


def apply_policy_to_tables(
    session: Session,
    policy: GitOpsPolicyConfig,
    preset: Preset,
) -> int:
    """
    Apply a policy to matching tables.
    
    Args:
        session: Database session
        policy: Policy configuration
        preset: Preset to assign
    
    Returns:
        Number of tables updated
    """
    # Simple pattern matching (in production, support wildcards)
    table_pattern = policy.table_pattern
    
    if "*" in table_pattern:
        # Wildcard pattern
        parts = table_pattern.split(".")
        catalog_pattern = parts[0] if len(parts) > 0 else "%"
        schema_pattern = parts[1] if len(parts) > 1 else "%"
        table_pattern = parts[2] if len(parts) > 2 else "%"
        
        stmt = select(Table).where(
            Table.catalog.like(catalog_pattern.replace("*", "%")),
            Table.schema_name.like(schema_pattern.replace("*", "%")),
            Table.table_name.like(table_pattern.replace("*", "%")),
        )
    else:
        # Exact match
        parts = table_pattern.split(".")
        if len(parts) != 3:
            logger.warning(f"Invalid table pattern: {table_pattern}")
            return 0
        
        catalog, schema, table = parts
        stmt = select(Table).where(
            Table.catalog == catalog,
            Table.schema_name == schema,
            Table.table_name == table,
        )
    
    tables = session.execute(stmt).scalars().all()
    
    # Create or update preset rule for each table
    from cryo.db.models import PresetAssignment
    
    for table in tables:
        # Check if assignment already exists
        existing_stmt = select(PresetAssignment).where(
            PresetAssignment.table_id == table.id,
            PresetAssignment.preset_id == preset.id,
        )
        existing = session.execute(existing_stmt).scalar_one_or_none()
        
        if not existing:
            assignment = PresetAssignment(
                table_id=table.id,
                preset_id=preset.id,
                provenance="gitops",
            )
            session.add(assignment)
    
    session.commit()
    
    return len(tables)


def watch_git_repository(
    repo_path: Path,
    policy_dir_rel: str,
    session: Session,
    poll_interval_seconds: int = 60,
) -> None:
    """
    Watch a Git repository for policy changes and sync to Cryo.
    
    This is a simple polling implementation. In production, use webhooks.
    
    Args:
        repo_path: Path to Git repository
        policy_dir_rel: Relative path to policy directory within repo
        session: Database session
        poll_interval_seconds: Polling interval
    """
    import time
    import subprocess
    
    last_commit = None
    policy_dir = repo_path / policy_dir_rel
    
    while True:
        try:
            # Get current commit hash
            result = subprocess.run(
                ["git", "rev-parse", "HEAD"],
                cwd=repo_path,
                capture_output=True,
                text=True,
                check=True,
            )
            current_commit = result.stdout.strip()
            
            if current_commit != last_commit:
                logger.info(f"Git repository updated: {current_commit}")
                
                # Load and sync policies
                policies = load_policies_from_directory(policy_dir)
                logger.info(f"Loaded {len(policies)} policies from Git")
                
                for policy in policies:
                    try:
                        preset = sync_policy_to_preset(session, policy)
                        tables_updated = apply_policy_to_tables(session, policy, preset)
                        logger.info(f"Applied policy {policy.table_pattern} to {tables_updated} tables")
                    except Exception as e:
                        logger.error(f"Failed to sync policy {policy.table_pattern}: {e}")
                
                last_commit = current_commit
        
        except Exception as e:
            logger.error(f"Error watching Git repository: {e}")
        
        time.sleep(poll_interval_seconds)
