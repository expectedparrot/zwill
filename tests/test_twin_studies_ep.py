from pathlib import Path

from zwill import twin_studies


def test_read_twin_raw_results_loads_ep_with_edsl_accessor(monkeypatch) -> None:
    expected = {"edsl_class_name": "Results", "data": [{"answer": "ok"}]}
    monkeypatch.setattr(twin_studies, "read_edsl_results", lambda path: expected)
    monkeypatch.setattr(
        twin_studies,
        "read_json_or_gzip",
        lambda path: (_ for _ in ()).throw(AssertionError(".ep package decoded as JSON")),
    )

    assert twin_studies.read_twin_raw_results(Path("results.ep")) == expected


def test_read_twin_raw_results_loads_json_report_artifact(monkeypatch) -> None:
    expected = {"format": "json"}
    monkeypatch.setattr(twin_studies, "read_json_or_gzip", lambda path: expected)

    assert twin_studies.read_twin_raw_results(Path("results.json")) == expected
