"""The karpenter.sh/capacity-type node selector must be optional.

It was emitted unconditionally, so on a cluster that does not run Karpenter and
restricts node selectors -- GKE Autopilot -- every dispatched Job is rejected at
admission and maintenance never runs through the k8s path at all:

    GKE Warden rejected the request because it violates one or more constraints.
    Violations details: {"[denied by autogke-node-affinity-selector-limitation]":
    ["Key 'karpenter.sh/capacity-type' is not allowed with node selector if not using
    workload separation; Autopilot only allows labels with keys:
    cloud.google.com/compute-class, ..."]}

Default stays True, so Karpenter clusters are unaffected.
"""

from __future__ import annotations

from cryo.engines.native.job_dispatch import _capacity_scheduling

KARPENTER_KEY = "karpenter.sh/capacity-type"


def test_selector_is_emitted_by_default() -> None:
    """Unchanged behaviour: the whole point is that existing clusters do not move."""
    for use_spot in (False, True):
        sel, _, _ = _capacity_scheduling(use_spot=use_spot)
        assert sel[KARPENTER_KEY] == ("spot" if use_spot else "on-demand")


def test_selector_is_omitted_when_disabled() -> None:
    for use_spot in (False, True):
        sel, _, _ = _capacity_scheduling(use_spot=use_spot, capacity_type_selector=False)
        assert KARPENTER_KEY not in sel


def test_spot_toleration_follows_the_selector() -> None:
    """A toleration for a taint no node carries is harmless but misleading, and the key
    it tolerates is the one the restricted cluster rejected."""
    _, tol_on, _ = _capacity_scheduling(use_spot=True, capacity_type_selector=True)
    assert [t["key"] for t in tol_on] == [KARPENTER_KEY]

    _, tol_off, _ = _capacity_scheduling(use_spot=True, capacity_type_selector=False)
    assert all(t["key"] != KARPENTER_KEY for t in tol_off)


def test_annotations_are_unaffected() -> None:
    """Annotations are descriptive and are not validated against a node-label
    allowlist, so they stay in both modes -- attribution should not depend on the
    cluster's scheduler."""
    for selector in (True, False):
        _, _, ann_od = _capacity_scheduling(use_spot=False, capacity_type_selector=selector)
        assert ann_od["cryo.io/capacity-type"] == "on-demand"
        assert ann_od["karpenter.sh/do-not-disrupt"] == "true"

        _, _, ann_spot = _capacity_scheduling(use_spot=True, capacity_type_selector=selector)
        assert ann_spot["cryo.io/capacity-type"] == "spot"


def test_dedicated_nodepool_is_independent() -> None:
    """Disabling the capacity-type selector must not silently disable the dedicated
    NodePool pairing, which is a separate opt-in.

    Note for restricted clusters: cryo.io/workload is also a non-allowlisted key, so
    dedicated_nodepool cannot be used on Autopilot either. It is off by default, so
    this only documents that the two switches are orthogonal.
    """
    sel, tol, _ = _capacity_scheduling(
        use_spot=False, dedicated_nodepool=True, capacity_type_selector=False
    )
    assert sel["cryo.io/workload"] == "native-engine"
    assert any(t["key"] == "cryo.io/workload" for t in tol)
    assert KARPENTER_KEY not in sel
