from eval.bench import SYSTEMS, engram_config
from eval.ablate_features import run_ablation


def test_engram_config_applies_algorithm_ablation_flags():
    cfg = engram_config(ablations=("chain", "raw", "graph"))

    assert cfg.chain_evidence is False
    assert cfg.provenance_evidence is False
    assert cfg.graph_proximity is False
    assert cfg.evidence_planner is True


def test_bench_exposes_named_lean_ablation_systems():
    names = {
        "engram_lean_no_chain",
        "engram_lean_no_raw",
        "engram_lean_no_graph",
        "engram_lean_core",
    }

    assert names <= set(SYSTEMS)
    assert SYSTEMS["engram_lean_no_chain"].ablations == ("chain",)
    assert SYSTEMS["engram_lean_no_raw"].ablations == ("raw",)
    assert SYSTEMS["engram_lean_no_graph"].ablations == ("graph",)
    assert SYSTEMS["engram_lean_core"].ablations == ("chain", "raw", "graph")


def test_offline_feature_ablation_proves_each_enabled_feature_adds_evidence():
    rows, summary = run_ablation()

    assert summary["n"] == 3
    assert summary["improved"] == 3
    assert {row.feature for row in rows} == {
        "chain_evidence",
        "provenance_evidence",
        "graph_proximity",
    }
    assert all(row.enabled_hit and not row.disabled_hit and row.improved for row in rows)
