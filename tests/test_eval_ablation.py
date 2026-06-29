from eval.bench import SYSTEMS, engram_config
from eval.ablate_features import run_ablation


def test_engram_config_applies_algorithm_ablation_flags():
    cfg = engram_config(ablations=("chain", "raw", "graph"))

    assert cfg.chain_evidence is False
    assert cfg.provenance_evidence is False
    assert cfg.graph_proximity is False
    assert cfg.graph_relation_awareness is True
    assert cfg.graph_negative_constraints is False
    assert cfg.evidence_budgeting is True
    assert cfg.evidence_planner is True

    budget_cfg = engram_config(ablations=("evidence_budget",))
    assert budget_cfg.evidence_budgeting is False

    temporal_cfg = engram_config(ablations=("temporal_history",))
    assert temporal_cfg.temporal_history_queries is False

    relation_cfg = engram_config(ablations=("graph_relation",))
    assert relation_cfg.graph_proximity is True
    assert relation_cfg.graph_relation_awareness is False

    reinforcement_cfg = engram_config(ablations=("graph_reinforcement",))
    assert reinforcement_cfg.graph_proximity is True
    assert reinforcement_cfg.graph_path_reinforcement is False

    self_anchor_cfg = engram_config(ablations=("graph_self_anchor",))
    assert self_anchor_cfg.graph_proximity is True
    assert self_anchor_cfg.graph_self_anchor is False

    alias_cfg = engram_config(ablations=("graph_entity_alias",))
    assert alias_cfg.graph_proximity is True
    assert alias_cfg.graph_entity_alias_anchor is False

    negative_cfg = engram_config(ablations=("graph_negative",))
    assert negative_cfg.graph_proximity is True
    assert negative_cfg.graph_negative_constraints is False

    location_cfg = engram_config(ablations=("planner_location",))
    assert location_cfg.planner_location_chains is False

    project_cfg = engram_config(ablations=("planner_project",))
    assert project_cfg.planner_project_chains is False


def test_bench_exposes_named_lean_ablation_systems():
    names = {
        "engram_lean_no_chain",
        "engram_lean_no_raw",
        "engram_lean_no_evidence_budget",
        "engram_lean_no_temporal_history",
        "engram_lean_no_graph",
        "engram_lean_no_graph_relation",
        "engram_lean_no_graph_reinforcement",
        "engram_lean_no_graph_self_anchor",
        "engram_lean_no_graph_entity_alias",
        "engram_lean_no_graph_negative",
        "engram_lean_no_planner_location",
        "engram_lean_no_planner_project",
        "engram_lean_core",
    }

    assert names <= set(SYSTEMS)
    assert SYSTEMS["engram_lean_no_chain"].ablations == ("chain",)
    assert SYSTEMS["engram_lean_no_raw"].ablations == ("raw",)
    assert SYSTEMS["engram_lean_no_evidence_budget"].ablations == ("evidence_budget",)
    assert SYSTEMS["engram_lean_no_temporal_history"].ablations == ("temporal_history",)
    assert SYSTEMS["engram_lean_no_graph"].ablations == ("graph",)
    assert SYSTEMS["engram_lean_no_graph_relation"].ablations == ("graph_relation",)
    assert SYSTEMS["engram_lean_no_graph_reinforcement"].ablations == ("graph_reinforcement",)
    assert SYSTEMS["engram_lean_no_graph_self_anchor"].ablations == ("graph_self_anchor",)
    assert SYSTEMS["engram_lean_no_graph_entity_alias"].ablations == ("graph_entity_alias",)
    assert SYSTEMS["engram_lean_no_graph_negative"].ablations == ("graph_negative",)
    assert SYSTEMS["engram_lean_no_planner_location"].ablations == ("planner_location",)
    assert SYSTEMS["engram_lean_no_planner_project"].ablations == ("planner_project",)
    assert SYSTEMS["engram_lean_core"].ablations == ("chain", "raw", "graph")


def test_offline_feature_ablation_proves_each_enabled_feature_adds_evidence():
    rows, summary = run_ablation()

    assert summary["n"] == 12
    assert summary["improved"] == 12
    assert {row.feature for row in rows} == {
        "chain_evidence",
        "temporal_history_queries",
        "provenance_evidence",
        "evidence_budgeting",
        "graph_proximity",
        "graph_relation_awareness",
        "graph_path_reinforcement",
        "graph_self_anchor",
        "graph_entity_alias_anchor",
        "graph_negative_constraints",
        "planner_location_chains",
        "planner_project_chains",
    }
    assert all(row.enabled_hit and not row.disabled_hit and row.improved for row in rows)
