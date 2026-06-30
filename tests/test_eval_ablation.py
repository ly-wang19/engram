from eval.bench import SYSTEMS, engram_config
from eval.ablate_features import run_ablation


def test_engram_config_applies_algorithm_ablation_flags():
    cfg = engram_config(ablations=("chain", "raw", "graph"))

    assert cfg.chain_evidence is False
    assert cfg.provenance_evidence is False
    assert cfg.provenance_chunk_promotion is False
    assert cfg.graph_proximity is False
    assert cfg.graph_relation_awareness is True
    assert cfg.graph_negative_constraints is False
    assert cfg.evidence_budgeting is True
    assert cfg.evidence_planner is True

    budget_cfg = engram_config(ablations=("evidence_budget",))
    assert budget_cfg.evidence_budgeting is False

    summary_cfg = engram_config(ablations=("summary_fallback",))
    assert summary_cfg.summary_fallback is False

    procedural_cfg = engram_config(ablations=("procedural_memory",))
    assert procedural_cfg.procedural_memory is False

    procedural_extraction_cfg = engram_config(ablations=("procedural_extraction",))
    assert procedural_extraction_cfg.procedural_extraction is False

    temporal_cfg = engram_config(ablations=("temporal_history",))
    assert temporal_cfg.temporal_history_queries is False

    provenance_chunk_cfg = engram_config(ablations=("provenance_chunks",))
    assert provenance_chunk_cfg.provenance_evidence is True
    assert provenance_chunk_cfg.provenance_chunk_promotion is False

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
        "engram_lean_no_provenance_chunks",
        "engram_lean_no_evidence_budget",
        "engram_lean_no_summary_fallback",
        "engram_lean_no_procedural_memory",
        "engram_lean_no_procedural_extraction",
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
    assert SYSTEMS["engram_lean_no_provenance_chunks"].ablations == ("provenance_chunks",)
    assert SYSTEMS["engram_lean_no_evidence_budget"].ablations == ("evidence_budget",)
    assert SYSTEMS["engram_lean_no_summary_fallback"].ablations == ("summary_fallback",)
    assert SYSTEMS["engram_lean_no_procedural_memory"].ablations == ("procedural_memory",)
    assert SYSTEMS["engram_lean_no_procedural_extraction"].ablations == ("procedural_extraction",)
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

    assert summary["n"] == 16
    assert summary["improved"] == 16
    assert {row.feature for row in rows} == {
        "chain_evidence",
        "temporal_history_queries",
        "summary_fallback",
        "procedural_memory",
        "procedural_extraction",
        "provenance_evidence",
        "provenance_chunk_promotion",
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
