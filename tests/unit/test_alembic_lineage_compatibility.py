"""Synthetic / local compatibility tests for Alembic migration graph lineage.

Validates that:
1. 'v1_5_baseline' is the canonical root revision (down_revision = None).
2. 'v1_7_phase2_1' links to 'v1_5_baseline' as its down_revision.
3. An existing historical revision ('v1_7_phase2_1') resolves an upgrade path to HEAD
   that strictly excludes 'v1_5_baseline' (no retrospective execution).
4. A fresh empty database (current_rev=None) resolves an upgrade path to HEAD starting
   with 'v1_5_baseline'.
5. Upgrading from prior production-compatible head ('obligations_v1_7_auto_payments') to HEAD
   ('credit_v2_refund_type') executes Credit V2 migrations linearly, and downgrading resolves back.
"""

from alembic.config import Config
from alembic.script import ScriptDirectory


def get_script_directory() -> ScriptDirectory:
    config = Config("alembic.ini")
    return ScriptDirectory.from_config(config)


def test_alembic_lineage_tree_root_and_head():
    """Verify that Alembic lineage forms a single linear DAG with v1_5_baseline as base."""
    script_dir = get_script_directory()

    # Head must be credit_v2_refund_type
    heads = script_dir.get_heads()
    assert len(heads) == 1
    assert heads[0] == "credit_v2_refund_type"

    # Bases must be exactly v1_5_baseline
    bases = script_dir.get_bases()
    assert len(bases) == 1
    assert bases[0] == "v1_5_baseline"

    baseline_rev = script_dir.get_revision("v1_5_baseline")
    assert baseline_rev is not None
    assert baseline_rev.down_revision is None

    v1_7_rev = script_dir.get_revision("v1_7_phase2_1")
    assert v1_7_rev is not None
    assert v1_7_rev.down_revision == "v1_5_baseline"


def test_historical_revision_upgrades_to_head_without_executing_baseline():
    """Verify that a database versioned at v1_7_phase2_1 upgrades to HEAD

    without executing the newly introduced v1_5_baseline retrospectively.
    """
    script_dir = get_script_directory()

    # Alembic's exact upgrade resolution mechanism
    steps = script_dir._upgrade_revs(destination="head", current_rev="v1_7_phase2_1")
    upgrade_revisions = [step.revision.revision for step in steps]

    # Baseline and current revision must NOT be in the upgrade path
    assert "v1_5_baseline" not in upgrade_revisions
    assert "v1_7_phase2_1" not in upgrade_revisions

    expected_sequence = [
        "v1_7_phase_fx_rate_snapshot",
        "categories_v1_phase1",
        "categories_v1_ph2_reclass",
        "transfers_v1_ph2_multicurrency",
        "goals_v1_ph1_reserved",
        "goals_v1_ph2_releases",
        "goals_v1_9_auto_contributions",
        "obligations_v1_7_auto_payments",
        "credit_v2_canonical_schema",
        "credit_v2_canonical_check",
        "credit_v2_canonical_closure",
        "credit_v2_refund_type",
    ]
    assert upgrade_revisions == expected_sequence


def test_fresh_empty_database_includes_v1_5_baseline_in_upgrade_path():
    """Verify that a fresh empty database (current_rev=None) includes v1_5_baseline

    as the initial migration step when upgrading to HEAD.
    """
    script_dir = get_script_directory()

    steps = script_dir._upgrade_revs(destination="head", current_rev=None)
    all_revisions = [step.revision.revision for step in steps]

    # Full chain starting from v1_5_baseline up to head
    assert all_revisions[0] == "v1_5_baseline"
    assert all_revisions[1] == "v1_7_phase2_1"
    assert all_revisions[-1] == "credit_v2_refund_type"
    assert len(all_revisions) == 14


def test_prior_head_to_new_head_upgrade_and_downgrade_resolution():
    """Verify that upgrading from prior head (obligations_v1_7_auto_payments) to HEAD

    resolves strictly to Credit V2 migrations, and downgrading resolves back.
    """
    script_dir = get_script_directory()

    # Upgrade step resolution from prior head
    steps = script_dir._upgrade_revs(
        destination="head", current_rev="obligations_v1_7_auto_payments"
    )
    upgrade_revisions = [step.revision.revision for step in steps]
    assert upgrade_revisions == [
        "credit_v2_canonical_schema",
        "credit_v2_canonical_check",
        "credit_v2_canonical_closure",
        "credit_v2_refund_type",
    ]

    # Downgrade step resolution back to prior head
    down_steps = script_dir._downgrade_revs(
        destination="obligations_v1_7_auto_payments", current_rev="credit_v2_refund_type"
    )
    downgrade_revisions = [step.revision.revision for step in down_steps]
    assert downgrade_revisions == [
        "credit_v2_refund_type",
        "credit_v2_canonical_closure",
        "credit_v2_canonical_check",
        "credit_v2_canonical_schema",
    ]
