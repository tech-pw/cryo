"""Regression test for review finding F01, 2026-09-20: cryo.cli.run_scheduler (the actual
production entrypoint -- deploy/helm/cryo/templates/scheduler-deployment.yaml runs
"cryo scheduler", never "cryo serve") used to start cryo.scheduler.benefit_score_updater.
benefit_score_updater_loop_sync directly, a raw loop with no knowledge of
benefit_score_sweep_via_k8s_jobs at all. The flag check lived entirely inside
BenefitScoreUpdaterRunner._tick, which only run_serve's other_runners list ever instantiated --
so flipping the flag in production changed nothing on the one entrypoint that actually runs.

Testing run_scheduler directly isn't practical (_run_with_signal blocks on a real daemon loop
and installs signal handlers) -- instead this test drives the actual benefit-score thread target
run_scheduler starts, confirming it is BenefitScoreUpdaterRunner.run (whose _tick checks the
flag), not the old raw loop function.
"""

from __future__ import annotations

import ast
import inspect

import cryo.cli as cli_module


def test_run_scheduler_starts_the_flag_aware_runner_not_the_raw_loop():
    source = inspect.getsource(cli_module.run_scheduler)
    tree = ast.parse(source)

    # Find every "name = BenefitScoreUpdaterRunner(...)" assignment's target variable name.
    runner_var_names: set[str] = set()
    for node in ast.walk(tree):
        if (
            isinstance(node, ast.Assign)
            and isinstance(node.value, ast.Call)
            and getattr(node.value.func, "id", None) == "BenefitScoreUpdaterRunner"
        ):
            for target in node.targets:
                if isinstance(target, ast.Name):
                    runner_var_names.add(target.id)

    assert runner_var_names, "run_scheduler must construct a BenefitScoreUpdaterRunner()"

    # Confirm some Thread(...) call's target= is "<that variable>.run".
    thread_targets_run_a_runner = False
    for node in ast.walk(tree):
        if isinstance(node, ast.Call) and getattr(node.func, "id", None) == "Thread":
            for kw in node.keywords:
                if (
                    kw.arg == "target"
                    and isinstance(kw.value, ast.Attribute)
                    and kw.value.attr == "run"
                    and isinstance(kw.value.value, ast.Name)
                    and kw.value.value.id in runner_var_names
                ):
                    thread_targets_run_a_runner = True

    assert thread_targets_run_a_runner, (
        "run_scheduler must start a Thread targeting BenefitScoreUpdaterRunner().run (the "
        "flag-aware coordinator's DaemonRunner loop), not a standalone loop function"
    )
    # The old raw loop function must no longer be CALLED anywhere in the function body (a
    # mention in an explanatory comment, like this fix's own changelog note, doesn't count) --
    # confirms this isn't just "also start the new one alongside the old one".
    called_names = {
        node.func.id
        for node in ast.walk(tree)
        if isinstance(node, ast.Call) and isinstance(node.func, ast.Name)
    }
    assert "benefit_score_updater_loop_sync" not in called_names


def test_benefit_score_updater_loop_sync_no_longer_exists():
    """The old flag-unaware loop function was dead code once run_scheduler stopped calling it
    (no other caller existed anywhere in the codebase) -- confirms it was actually removed
    rather than left as an unused, drift-prone duplicate implementation."""
    import cryo.scheduler.benefit_score_updater as updater_module

    assert not hasattr(updater_module, "benefit_score_updater_loop_sync")
    assert not hasattr(updater_module, "benefit_score_updater_loop")
    assert not hasattr(updater_module, "start_benefit_score_updater")


def test_benefit_score_updater_runner_tick_respects_the_flag(monkeypatch):
    """Direct unit check that BenefitScoreUpdaterRunner._tick (the method run_scheduler now
    actually drives) branches on the flag -- the specific behavior F01 says was being bypassed."""
    calls: list[str] = []

    class _FakeSettings:
        benefit_score_sweep_via_k8s_jobs = True
        benefit_score_update_interval_hours = 6

    monkeypatch.setattr(cli_module, "get_settings", lambda: _FakeSettings())
    runner = cli_module.BenefitScoreUpdaterRunner()
    monkeypatch.setattr(runner, "_tick_via_k8s_job", lambda: calls.append("k8s_job"))

    runner._tick()

    assert calls == ["k8s_job"], "flag=True must route through the K8s-Job coordinator path"
