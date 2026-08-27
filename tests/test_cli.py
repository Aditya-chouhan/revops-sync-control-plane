from __future__ import annotations

import json
from pathlib import Path

import pytest

from revops_sync import cli


def test_cli_reconcile_runs_committed_fixture(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch, capsys: pytest.CaptureFixture[str]
) -> None:
    db_path = tmp_path / "cli_default.sqlite3"
    monkeypatch.setenv("DATABASE_URL", f"sqlite:///{db_path}")
    monkeypatch.setenv("AUTO_CREATE_SCHEMA", "true")
    monkeypatch.setattr("sys.argv", ["revops-sync", "reconcile"])

    cli.main()

    output = json.loads(capsys.readouterr().out)
    assert output["source_mode"] == "fixture"
    assert output["input_records"] == 6
    assert output["inserted_records"] == 6
    assert output["accounts_reconciled"] == 4
    assert output["external_writes"] == 0


def test_cli_reconcile_accepts_fixture_override(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch, capsys: pytest.CaptureFixture[str]
) -> None:
    fixture_path = tmp_path / "custom_fixture.json"
    fixture_path.write_text(
        json.dumps(
            {
                "data_classification": "synthetic",
                "claim_boundary": "invented CLI test data, not customer records",
                "records": [
                    {
                        "provider": "hubspot",
                        "external_id": "cli-hs-1",
                        "name": "CLI Fixture Co",
                        "domain": "cli-fixture.example",
                        "source_updated_at": "2026-08-27T00:00:00Z",
                    }
                ],
            }
        )
    )
    db_path = tmp_path / "cli_custom.sqlite3"
    monkeypatch.setenv("DATABASE_URL", f"sqlite:///{db_path}")
    monkeypatch.setenv("AUTO_CREATE_SCHEMA", "true")
    monkeypatch.setattr(
        "sys.argv", ["revops-sync", "reconcile", "--fixture", str(fixture_path)]
    )

    cli.main()

    output = json.loads(capsys.readouterr().out)
    assert output["input_records"] == 1
    assert output["accounts_reconciled"] == 1


def test_cli_serve_invokes_uvicorn_with_parsed_args(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    captured: dict[str, object] = {}

    def fake_run(app: str, host: str, port: int) -> None:
        captured["app"] = app
        captured["host"] = host
        captured["port"] = port

    monkeypatch.setattr(cli.uvicorn, "run", fake_run)
    monkeypatch.setattr(
        "sys.argv", ["revops-sync", "serve", "--host", "127.0.0.1", "--port", "9100"]
    )

    cli.main()

    assert captured == {"app": "revops_sync.main:app", "host": "127.0.0.1", "port": 9100}


def test_cli_serve_defaults_to_all_interfaces_and_port_8000(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    captured: dict[str, object] = {}
    monkeypatch.setattr(
        cli.uvicorn, "run", lambda app, host, port: captured.update(app=app, host=host, port=port)
    )
    monkeypatch.setattr("sys.argv", ["revops-sync", "serve"])

    cli.main()

    assert captured == {"app": "revops_sync.main:app", "host": "0.0.0.0", "port": 8000}
