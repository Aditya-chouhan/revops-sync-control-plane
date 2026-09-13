from __future__ import annotations

import json
import sys
from pathlib import Path

import httpx
import pytest

from revops_sync.demo import _check, main, render_walkthrough, run_demo


def test_demo_runs_real_services_with_only_mocked_http(monkeypatch: pytest.MonkeyPatch) -> None:
    def prohibit_network(*args: object, **kwargs: object) -> None:
        pytest.fail("demo tried to use a real network transport")

    monkeypatch.setattr(httpx.HTTPTransport, "handle_request", prohibit_network)
    monkeypatch.setenv("DATABASE_URL", "postgresql://must-not-be-used.invalid/database")
    monkeypatch.setenv("SALESFORCE_INSTANCE_URL", "https://must-not-be-used.invalid")
    monkeypatch.setenv("SALESFORCE_ACCESS_TOKEN", "sentinel-real-token-do-not-use")
    receipt = run_demo()
    assert receipt["all_checks_passed"] and len(receipt["scenarios"]) == 5
    assert receipt["real_external_writes"] == 0
    assert [c["method"] for c in receipt["simulated_http_calls"]] == ["POST", "GET", "PATCH"]
    assert receipt["scenarios"][3]["observed"]["additional_simulated_http"] == 0
    assert receipt["scenarios"][4]["observed"]["original_intent_preserved"] is True
    assert "sentinel-real-token" not in json.dumps(receipt)


def test_demo_is_repeatable_in_fresh_databases() -> None:
    assert run_demo() == run_demo()


def test_walkthrough_discloses_simulation_and_contains_inputs() -> None:
    rendered = render_walkthrough(run_demo())
    assert "Synthetic accounts and simulated CRM responses" in rendered
    assert "## Sample inputs" in rendered and "stale-domain.example" in rendered
    assert "operator release is simulated" in rendered
    assert "No pipeline, revenue" in rendered


@pytest.mark.parametrize("json_mode", [False, True])
def test_cli_emits_verified_markdown_or_json(
    monkeypatch: pytest.MonkeyPatch, capsys: pytest.CaptureFixture[str], json_mode: bool
) -> None:
    monkeypatch.setattr(sys, "argv", ["demo"] + (["--json"] if json_mode else []))
    main()
    output = capsys.readouterr().out
    if json_mode:
        assert json.loads(output)["all_checks_passed"] is True
    else:
        assert output.startswith("# Five-minute CRM control-plane demo")


def test_demo_check_raises_instead_of_printing_a_false_success() -> None:
    with pytest.raises(RuntimeError, match="Demo verification failed"):
        _check(False, "injected failure")


def test_captured_artifacts_match_fresh_verified_execution() -> None:
    root = Path(__file__).parents[1]
    receipt = run_demo()
    assert json.loads((root / "evidence/hiring_demo_2026-09-13.json").read_text()) == receipt
    assert (root / "docs/HIRING_DEMO_OUTPUT.md").read_text() == render_walkthrough(receipt)
