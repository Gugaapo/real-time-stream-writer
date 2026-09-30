"""The public book stops once estimated Flash spend for the UTC month reaches the cap."""
from __future__ import annotations

from datetime import datetime, timezone

from app.budget import Budget, cost_micros, is_peak, reserve_micros
from app.store import Store
from tests.test_sessions import _world


def test_peak_window_is_weekday_beijing_office_hours():
    assert is_peak(datetime(2026, 9, 30, 2, 0, tzinfo=timezone.utc))
    assert is_peak(datetime(2026, 9, 30, 7, 30, tzinfo=timezone.utc))
    assert not is_peak(datetime(2026, 9, 30, 5, 0, tzinfo=timezone.utc))
    assert not is_peak(datetime(2026, 9, 30, 10, 0, tzinfo=timezone.utc))
    assert not is_peak(datetime(2026, 10, 3, 2, 0, tzinfo=timezone.utc))


def test_off_peak_cache_miss_matches_published_flash_rate():
    when = datetime(2026, 9, 30, 15, 0, tzinfo=timezone.utc)
    usage = {
        "prompt_cache_hit_tokens": 0,
        "prompt_cache_miss_tokens": 1_000_000,
        "completion_tokens": 0,
    }
    assert cost_micros(usage, when) == 150_000


def test_commit_replaces_the_hold_with_the_reported_usage(tmp_path):
    when = datetime(2026, 9, 30, 15, 0, tzinfo=timezone.utc)
    store = Store(str(tmp_path / "spend.sqlite"))
    budget = Budget(store, cap_usd=10, now=lambda: when)
    month = budget.try_reserve()
    assert store.spend_micros(month) == reserve_micros()
    budget.commit(
        month,
        {
            "prompt_cache_hit_tokens": 0,
            "prompt_cache_miss_tokens": 1000,
            "completion_tokens": 100,
        },
    )
    # 1000 * $0.15 + 100 * $0.60, per 1M tokens.
    assert store.spend_micros(month) == 210


def test_a_new_month_starts_at_zero(tmp_path):
    current = {"when": datetime(2026, 9, 30, 15, 0, tzinfo=timezone.utc)}
    store = Store(str(tmp_path / "spend.sqlite"))
    budget = Budget(store, cap_usd=10, now=lambda: current["when"])
    assert budget.try_reserve() == "2026-09"
    current["when"] = datetime(2026, 10, 1, 15, 0, tzinfo=timezone.utc)
    assert budget.spent_micros() == 0
    assert budget.can_afford()


def test_cap_blocks_the_model_call(tmp_path):
    app, _capture, describer, _clock = _world(tmp_path, cap_usd=0.0001)
    from fastapi.testclient import TestClient

    with TestClient(app) as client:
        session_id = client.post("/api/v1/sessions", json={"style": "novel"}).json()["id"]
        runtime = app.state.runtime
        assert runtime.acquire(session_id) is True
        runtime.tick()
        assert describer.calls == []
        assert runtime.runs[session_id].status == "orcamento"
        assert runtime.budget.spent_micros() == 0
