"""Auto-reorg (P8): debounce policy, reorg log endpoint, run recording."""

from datetime import UTC, datetime, timedelta

from app.workers.reorg import record_reorg_run, should_run

_NOW = datetime(2026, 8, 18, 12, 0, tzinfo=UTC)


def test_should_run_batch_always():
    assert should_run("batch", 0, _NOW, 3) is True


def test_should_run_debounced():
    assert should_run("debounced", 0, None, 3, now=_NOW) is True  # never ran → run
    assert should_run("debounced", 2, _NOW, 3, now=_NOW) is False  # below min docs
    assert should_run("debounced", 3, _NOW, 3, now=_NOW) is True  # min docs reached
    assert (
        should_run("debounced", 0, _NOW - timedelta(hours=25), 3, now=_NOW) is True
    )  # 24h elapsed


def test_should_run_nightly():
    assert should_run("nightly", 5, _NOW, 3, now=_NOW) is False  # fresh run → skip
    assert should_run("nightly", 5, _NOW - timedelta(hours=25), 3, now=_NOW) is True  # day passed


async def test_reorg_log_endpoint_records_runs(require_db, client):
    await record_reorg_run("auto", 3, 2, 4, 1, {"detected": {"engine": "lpa"}})
    r = await client.get("/api/v1/library/reorganizations")
    assert r.status_code == 200
    runs = r.json()["runs"]
    assert runs, "reorg log must contain the recorded run"
    latest = runs[0]
    assert latest["triggered_by"] == "auto"
    assert latest["docs_since_last"] == 3
    assert latest["communities_before"] == 2
    assert latest["communities_after"] == 4
    assert latest["summaries_made"] == 1
    assert latest["detail"]["detected"]["engine"] == "lpa"
