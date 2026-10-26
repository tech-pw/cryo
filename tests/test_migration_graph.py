from __future__ import annotations

from pathlib import Path

from alembic.config import Config
from alembic.script import ScriptDirectory

ROOT = Path(__file__).resolve().parents[1]


def _scripts() -> ScriptDirectory:
    config = Config(str(ROOT / "alembic.ini"))
    config.set_main_option("script_location", str(ROOT / "migrations"))
    return ScriptDirectory.from_config(config)


def test_migrations_have_a_single_head() -> None:
    # Two PRs that branch from the same parent each pass CI alone and fork the graph once both
    # merge; `alembic upgrade head` then refuses to run.
    assert len(_scripts().get_heads()) == 1


def test_revision_ids_are_unique() -> None:
    revisions = [script.revision for script in _scripts().walk_revisions()]
    assert len(revisions) == len(set(revisions))
