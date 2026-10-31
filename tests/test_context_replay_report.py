from eval.context_replay_report import primary_gate


def diagnostics(**changes):
    return dict(dict(source_gain_qids=["gain1", "gain2", "gain3"],
                     source_loss_qids=[], literal_loss_qids=[]), **changes)


def test_net_source_gain_cannot_hide_a_paired_loss():
    gate = primary_gate({"invalid": 0}, diagnostics(source_loss_qids=["lost"] ))
    assert gate["decision"] == "reject_primary_regression"


def test_complete_sources_cannot_hide_a_literal_loss():
    gate = primary_gate({"invalid": 0}, diagnostics(literal_loss_qids=["lost"] ))
    assert gate["decision"] == "reject_primary_regression"


def test_missing_sample_cannot_turn_subset_gain_into_full_validation():
    gate = primary_gate({"invalid": 1}, diagnostics())
    assert gate["decision"] == "inconclusive_incomplete_cache"


def test_retaining_for_qa_never_establishes_qa_or_enables_default():
    gate = primary_gate({"invalid": 0}, diagnostics())
    assert gate == {"decision": "retain_for_qa", "budget_chars": 60000,
                    "qa_accuracy_established": False, "production_default_authorized": False}


def test_no_primary_gain_is_not_promoted():
    assert primary_gate({"invalid": 0}, diagnostics(source_gain_qids=[]))["decision"] == "no_observed_primary_gain"
