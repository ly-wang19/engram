from eval.bench import SYSTEMS, engram_config
from eval.ablate_features import run_ablation


def test_engram_config_applies_algorithm_ablation_flags():
    cfg = engram_config(ablations=("chain", "raw", "graph"))

    assert cfg.chain_evidence is False
    assert cfg.provenance_evidence is False
    assert cfg.graph_proximity is False
    assert cfg.graph_relation_awareness is True
    assert cfg.evidence_planner is True

    relation_cfg = engram_config(ablations=("graph_relation",))
    assert relation_cfg.graph_proximity is True
    assert relation_cfg.graph_relation_awareness is False

    reinforcement_cfg = engram_config(ablations=("graph_reinforcement",))
    assert reinforcement_cfg.graph_proximity is True
    assert reinforcement_cfg.graph_path_reinforcement is False

    self_anchor_cfg = engram_config(ablations=("graph_self_anchor",))
    assert self_anchor_cfg.graph_proximity is True
    assert self_anchor_cfg.graph_self_anchor is False


def test_bench_exposes_named_lean_ablation_systems():
    names = {
        "engram_lean_no_chain",
        "engram_lean_no_raw",
        "engram_lean_no_graph",
        "engram_lean_no_graph_relation",
        "engram_lean_no_graph_reinforcement",
        "engram_lean_no_graph_self_anchor",
        "engram_lean_core",
    }

    assert names <= set(SYSTEMS)
    assert SYSTEMS["engram_lean_no_chain"].ablations == ("chain",)
    assert SYSTEMS["engram_lean_no_raw"].ablations == ("raw",)
    assert SYSTEMS["engram_lean_no_graph"].ablations == ("graph",)
    assert SYSTEMS["engram_lean_no_graph_relation"].ablations == ("graph_relation",)
    assert SYSTEMS["engram_lean_no_graph_reinforcement"].ablations == ("graph_reinforcement",)
    assert SYSTEMS["engram_lean_no_graph_self_anchor"].ablations == ("graph_self_anchor",)
    assert SYSTEMS["engram_lean_core"].ablations == ("chain", "raw", "graph")


def test_offline_feature_ablation_proves_each_enabled_feature_adds_evidence():
    rows, summary = run_ablation()

    assert summary["n"] == 6
    assert summary["improved"] == 6
    assert {row.feature for row in rows} == {
        "chain_evidence",
        "provenance_evidence",
        "graph_proximity",
        "graph_relation_awareness",
        "graph_path_reinforcement",
        "graph_self_anchor",
    }
    assert all(row.enabled_hit and not row.disabled_hit and row.improved for row in rows)
