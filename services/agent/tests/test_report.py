from app.report import parse_report

GOOD = (
    '{"summary": "payments crash-loops", "likely_cause": "MAX_AMOUNT_CENTS=abc", '
    '"evidence": ["ValueError in logs"], '
    '"suggested_action": {"kind": "rollout_undo", "target": "payments", "detail": "undo"}, '
    '"confidence": "high"}'
)


def test_plain_json():
    report = parse_report(GOOD)
    assert report.suggested_action.kind == "rollout_undo"
    assert report.confidence == "high"


def test_json_inside_text_or_fences():
    assert parse_report("Here is the report:\n```json\n" + GOOD + "\n```").summary == (
        "payments crash-loops"
    )
    assert parse_report("Report: " + GOOD + " Thanks").likely_cause == "MAX_AMOUNT_CENTS=abc"


def test_unknown_values_are_made_safe():
    report = parse_report(
        '{"summary": "x", "evidence": "one fact", "confidence": "very",'
        ' "suggested_action": {"kind": "delete_namespace", "target": null}}'
    )
    assert report.evidence == ["one fact"]
    assert report.confidence == "low"
    assert report.suggested_action.kind == "manual"  # never an action we don't allow
    assert report.suggested_action.target == ""


def test_not_a_report():
    assert parse_report("") is None
    assert parse_report("I think payments is broken.") is None
    assert parse_report('{"likely_cause": "no summary"}') is None
