"""Integration tests for WAU login-only session exclusion (US-020 · US-021 · BR-12).

These tests verify the WAU calculation rule implemented in the KQL saved searches:
  - line_command_shift_lead_wau / line_command_associate_wau (US-020)
  - quality_studio_paint_engineer_wau / quality_studio_quality_validator_wau (US-021)

The Python functions mirror the KQL logic exactly so that the tests act as a
runnable specification against which the KQL can be verified.  The rule under test:

  A user-day counts as active only when at least one AdoptionEvent for that
  userId on that calendar day has actionType IN the meaningful-action set.

  Sessions where the only recorded event is a LOGIN (no meaningful-action
  AdoptionEvent within the session window) are excluded from the weekly active
  user count (US-020 AC-19b649cd; US-021 AC2).

Acceptance criteria verified here:
  (a) A session with only a LOGIN event → WAU contribution of 0 for that user-day.
  (b) A session containing at least one meaningful-action AdoptionEvent →
      WAU contribution of 1 for that user-day.

Run:
    pytest platform/observability-stack/tests/test_wau_login_only_exclusion.py -v
"""

from __future__ import annotations

from collections import defaultdict
from datetime import datetime, timedelta, timezone
from typing import Any

import pytest

# ---------------------------------------------------------------------------
# WAU calculation — Python mirror of the KQL saved queries
# ---------------------------------------------------------------------------

LINE_COMMAND_MEANINGFUL_ACTIONS: frozenset[str] = frozenset(
    [
        "ALERT_ACKNOWLEDGED",
        "HANDOVER_SUBMITTED",
        "AR_SESSION_STARTED",
        "TORQUE_LOG_SUBMITTED",
    ]
)

# Kept as an alias so existing code referencing MEANINGFUL_ACTIONS still works.
MEANINGFUL_ACTIONS: frozenset[str] = LINE_COMMAND_MEANINGFUL_ACTIONS

QUALITY_STUDIO_MEANINGFUL_ACTIONS: frozenset[str] = frozenset(
    [
        "PROCESS_REPLAY_LAUNCH",
        "DEFECT_GENOME_QUERY",
        "ROOT_CAUSE_ASSIGNED",
        "CERTIFY",
        "CERTIFICATE_STUDIO_OPEN",
    ]
)


def _iso_to_week_key(iso_timestamp: str) -> tuple[int, int]:
    """Return (iso_year, iso_week) from an ISO 8601 UTC timestamp string."""
    dt = datetime.fromisoformat(iso_timestamp.rstrip("Z")).replace(
        tzinfo=timezone.utc
    )
    iso_cal = dt.isocalendar()
    return (iso_cal.year, iso_cal.week)


def _iso_to_day_key(iso_timestamp: str) -> str:
    """Return 'YYYY-MM-DD' from an ISO 8601 UTC timestamp string."""
    dt = datetime.fromisoformat(iso_timestamp.rstrip("Z")).replace(
        tzinfo=timezone.utc
    )
    return dt.strftime("%Y-%m-%d")


def compute_line_command_wau(
    events: list[dict[str, Any]],
    role_filter: str,
    window_start: str,
    window_end: str,
) -> dict[tuple[int, int], int]:
    """Compute WAU per ISO calendar week for a given role.

    Mirrors the KQL queries line_command_shift_lead_wau and
    line_command_associate_wau.

    Args:
        events: List of AdoptionEvent dicts with fields:
                component, actionType, userId, role, timestamp (ISO 8601 UTC).
        role_filter: 'shift_lead' or 'assembly_associate'.
        window_start: ISO 8601 date string (inclusive).
        window_end:   ISO 8601 date string (inclusive).

    Returns:
        Dict mapping (iso_year, iso_week) → distinct active user count.
        A user is active in a week only if they have ≥1 meaningful action on
        at least one day that week.  Login-only days do not count.
    """
    ws_dt = datetime.fromisoformat(window_start).replace(tzinfo=timezone.utc)
    we_dt = datetime.fromisoformat(window_end).replace(tzinfo=timezone.utc)

    # per (userId, day) → bool: had at least one meaningful action
    user_day_meaningful: dict[tuple[str, str], bool] = defaultdict(bool)

    for evt in events:
        if evt.get("component") != "line-command":
            continue
        if evt.get("role") != role_filter:
            continue
        ts = evt.get("timestamp", "")
        dt = datetime.fromisoformat(ts.rstrip("Z")).replace(tzinfo=timezone.utc)
        if dt < ws_dt or dt > we_dt:
            continue

        user_id = evt["userId"]
        day_key = _iso_to_day_key(ts)
        action = evt.get("actionType", "")
        if action in MEANINGFUL_ACTIONS:
            user_day_meaningful[(user_id, day_key)] = True
        else:
            # Mark the key as existing so login-only days are represented,
            # but only set to True if a meaningful action was seen.
            if (user_id, day_key) not in user_day_meaningful:
                user_day_meaningful[(user_id, day_key)] = False

    # Group active user-days by ISO week; count distinct userIds per week.
    week_users: dict[tuple[int, int], set[str]] = defaultdict(set)
    for (user_id, day_key), had_meaningful in user_day_meaningful.items():
        if had_meaningful:
            week_key = _iso_to_week_key(day_key + "T00:00:00Z")
            week_users[week_key].add(user_id)

    return {week: len(users) for week, users in week_users.items()}


# ---------------------------------------------------------------------------
# Fixtures — synthetic AdoptionEvent records
#
# All timestamps are expressed relative to _GATE1_WINDOW_START so that the
# fixture dates remain clearly tied to the Gate 1 measurement window defined
# in the KQL saved queries (datetime(2025-11-01) … datetime(2025-11-28)).
# If the gate window changes, update _GATE1_WINDOW_START and the day offsets
# below; do not scatter absolute timestamps across the test body.
# ---------------------------------------------------------------------------

_GATE1_WINDOW_START = datetime(2025, 11, 1, 0, 0, 0, tzinfo=timezone.utc)
_GATE1_WINDOW_END   = datetime(2025, 11, 28, 23, 59, 59, tzinfo=timezone.utc)

def _ts(offset_days: int, hour: int = 9, minute: int = 0) -> str:
    """Return an ISO 8601 UTC timestamp anchored to the Gate 1 window start."""
    dt = _GATE1_WINDOW_START + timedelta(days=offset_days, hours=hour, minutes=minute)
    return dt.strftime("%Y-%m-%dT%H:%M:%SZ")

_DAY_1 = _ts(2, hour=9)   # 2025-11-03 09:00Z — Monday W45-2025
_DAY_2 = _ts(3, hour=10, minute=30)  # 2025-11-04 10:30Z — Tuesday W45-2025
_DAY_3 = _ts(9, hour=8, minute=15)   # 2025-11-10 08:15Z — Monday W46-2025
_WINDOW_START = _GATE1_WINDOW_START.strftime("%Y-%m-%dT%H:%M:%SZ")
_WINDOW_END   = _GATE1_WINDOW_END.strftime("%Y-%m-%dT%H:%M:%SZ")


def _make_event(
    user_id: str,
    action: str,
    timestamp: str,
    role: str = "shift_lead",
    component: str = "line-command",
) -> dict[str, Any]:
    return {
        "userId": user_id,
        "role": role,
        "actionType": action,
        "timestamp": timestamp,
        "component": component,
        "stationId": "MARRIAGE-01",
    }


# ---------------------------------------------------------------------------
# Acceptance criterion (a):
# A session with only a LOGIN event produces WAU contribution of 0.
# ---------------------------------------------------------------------------


class TestLoginOnlyExclusion:
    def test_login_only_session_contributes_zero_wau(self) -> None:
        """A user with only a LOGIN event on a day is NOT counted as active."""
        events = [
            _make_event("user-001", "LOGIN", _DAY_1),
        ]
        wau = compute_line_command_wau(
            events, "shift_lead", _WINDOW_START, _WINDOW_END
        )
        assert sum(wau.values()) == 0, (
            "Expected WAU=0 for a login-only session; "
            f"got {wau}"
        )

    def test_multiple_logins_same_day_still_zero_wau(self) -> None:
        """Multiple LOGINs in a single day still produce no active user count."""
        events = [
            _make_event("user-001", "LOGIN", _DAY_1),
            _make_event("user-001", "LOGIN", _DAY_1),
            _make_event("user-002", "LOGIN", _DAY_1),
        ]
        wau = compute_line_command_wau(
            events, "shift_lead", _WINDOW_START, _WINDOW_END
        )
        assert sum(wau.values()) == 0

    def test_login_only_different_users_different_days_still_zero(self) -> None:
        """Login-only sessions across multiple users and days all produce WAU=0."""
        events = [
            _make_event("user-001", "LOGIN", _DAY_1),
            _make_event("user-002", "LOGIN", _DAY_2),
            _make_event("user-003", "LOGIN", _DAY_3),
        ]
        wau = compute_line_command_wau(
            events, "shift_lead", _WINDOW_START, _WINDOW_END
        )
        assert sum(wau.values()) == 0


# ---------------------------------------------------------------------------
# Acceptance criterion (b):
# A session with ≥1 meaningful action produces WAU contribution of 1.
# ---------------------------------------------------------------------------


class TestMeaningfulActionInclusion:
    @pytest.mark.parametrize(
        "action",
        [
            "ALERT_ACKNOWLEDGED",
            "HANDOVER_SUBMITTED",
            "AR_SESSION_STARTED",
            "TORQUE_LOG_SUBMITTED",
        ],
    )
    def test_each_meaningful_action_produces_wau_1(self, action: str) -> None:
        """Each individual meaningful action type counts the user as active for that week."""
        events = [
            _make_event("user-001", action, _DAY_1),
        ]
        wau = compute_line_command_wau(
            events, "shift_lead", _WINDOW_START, _WINDOW_END
        )
        week_key = _iso_to_week_key(_DAY_1)
        assert wau.get(week_key, 0) == 1, (
            f"Expected WAU=1 for action={action!r}; got {wau}"
        )

    def test_login_plus_meaningful_action_counts_as_active(self) -> None:
        """A session that starts with a LOGIN then performs a meaningful action counts as active."""
        events = [
            _make_event("user-001", "LOGIN", _DAY_1),
            _make_event("user-001", "ALERT_ACKNOWLEDGED", _DAY_1),
        ]
        wau = compute_line_command_wau(
            events, "shift_lead", _WINDOW_START, _WINDOW_END
        )
        week_key = _iso_to_week_key(_DAY_1)
        assert wau.get(week_key, 0) == 1

    def test_two_users_both_active_counts_as_wau_2(self) -> None:
        """Two distinct users each with a meaningful action in the same week → WAU=2."""
        events = [
            _make_event("user-001", "HANDOVER_SUBMITTED", _DAY_1),
            _make_event("user-002", "TORQUE_LOG_SUBMITTED", _DAY_2),
        ]
        wau = compute_line_command_wau(
            events, "shift_lead", _WINDOW_START, _WINDOW_END
        )
        week_key = _iso_to_week_key(_DAY_1)
        assert wau.get(week_key, 0) == 2

    def test_same_user_multiple_meaningful_actions_same_week_counts_once(self) -> None:
        """A single user with multiple meaningful actions in a week is counted once for that week."""
        events = [
            _make_event("user-001", "ALERT_ACKNOWLEDGED", _DAY_1),
            _make_event("user-001", "HANDOVER_SUBMITTED", _DAY_2),
        ]
        wau = compute_line_command_wau(
            events, "shift_lead", _WINDOW_START, _WINDOW_END
        )
        week_key = _iso_to_week_key(_DAY_1)
        assert wau.get(week_key, 0) == 1


# ---------------------------------------------------------------------------
# Role filtering
# ---------------------------------------------------------------------------


class TestRoleFiltering:
    def test_shift_lead_events_not_counted_for_assembly_associate(self) -> None:
        """shift_lead events must not appear in assembly_associate WAU."""
        events = [
            _make_event("user-001", "ALERT_ACKNOWLEDGED", _DAY_1, role="shift_lead"),
        ]
        wau = compute_line_command_wau(
            events, "assembly_associate", _WINDOW_START, _WINDOW_END
        )
        assert sum(wau.values()) == 0

    def test_assembly_associate_events_not_counted_for_shift_lead(self) -> None:
        """assembly_associate events must not appear in shift_lead WAU."""
        events = [
            _make_event(
                "user-001",
                "TORQUE_LOG_SUBMITTED",
                _DAY_1,
                role="assembly_associate",
            ),
        ]
        wau = compute_line_command_wau(
            events, "shift_lead", _WINDOW_START, _WINDOW_END
        )
        assert sum(wau.values()) == 0

    def test_mixed_roles_counted_independently(self) -> None:
        """Shift lead and associate events produce separate WAU counts."""
        events = [
            _make_event("sl-001", "HANDOVER_SUBMITTED", _DAY_1, role="shift_lead"),
            _make_event("aa-001", "TORQUE_LOG_SUBMITTED", _DAY_1, role="assembly_associate"),
            _make_event("aa-002", "AR_SESSION_STARTED", _DAY_2, role="assembly_associate"),
        ]
        week_key = _iso_to_week_key(_DAY_1)
        sl_wau = compute_line_command_wau(
            events, "shift_lead", _WINDOW_START, _WINDOW_END
        )
        aa_wau = compute_line_command_wau(
            events, "assembly_associate", _WINDOW_START, _WINDOW_END
        )
        assert sl_wau.get(week_key, 0) == 1
        assert aa_wau.get(week_key, 0) == 2


# ---------------------------------------------------------------------------
# Window boundary filtering
# ---------------------------------------------------------------------------


class TestWindowBoundary:
    def test_events_outside_window_excluded(self) -> None:
        """Events before window_start or after window_end are excluded."""
        before_window = (_GATE1_WINDOW_START - timedelta(seconds=1)).strftime("%Y-%m-%dT%H:%M:%SZ")
        after_window  = (_GATE1_WINDOW_END   + timedelta(seconds=2)).strftime("%Y-%m-%dT%H:%M:%SZ")
        events = [
            _make_event("user-001", "ALERT_ACKNOWLEDGED", before_window),
            _make_event("user-002", "HANDOVER_SUBMITTED", after_window),
        ]
        wau = compute_line_command_wau(
            events, "shift_lead", _WINDOW_START, _WINDOW_END
        )
        assert sum(wau.values()) == 0

    def test_events_at_window_boundary_included(self) -> None:
        """Events exactly at window_start and window_end are included."""
        at_start = _GATE1_WINDOW_START.strftime("%Y-%m-%dT%H:%M:%SZ")
        at_end   = _GATE1_WINDOW_END.strftime("%Y-%m-%dT%H:%M:%SZ")
        events = [
            _make_event("user-001", "ALERT_ACKNOWLEDGED", at_start),
            _make_event("user-002", "HANDOVER_SUBMITTED", at_end),
        ]
        wau = compute_line_command_wau(
            events, "shift_lead", _WINDOW_START, _WINDOW_END
        )
        assert sum(wau.values()) == 2


# ---------------------------------------------------------------------------
# Component filtering
# ---------------------------------------------------------------------------


class TestComponentFiltering:
    def test_planning_cockpit_events_ignored(self) -> None:
        """Events from component='planning-cockpit' must not appear in line-command WAU."""
        events = [
            _make_event(
                "user-001",
                "ALERT_ACKNOWLEDGED",
                _DAY_1,
                component="planning-cockpit",
            ),
        ]
        wau = compute_line_command_wau(
            events, "shift_lead", _WINDOW_START, _WINDOW_END
        )
        assert sum(wau.values()) == 0


# ---------------------------------------------------------------------------
# Multi-week spanning
# ---------------------------------------------------------------------------


class TestMultiWeek:
    def test_active_in_two_separate_weeks_produces_two_week_entries(self) -> None:
        """A user with a meaningful action in each of two different weeks appears in both."""
        events = [
            _make_event("user-001", "ALERT_ACKNOWLEDGED", _DAY_1),  # W45
            _make_event("user-001", "HANDOVER_SUBMITTED", _DAY_3),  # W46
        ]
        wau = compute_line_command_wau(
            events, "shift_lead", _WINDOW_START, _WINDOW_END
        )
        assert len(wau) == 2
        for count in wau.values():
            assert count == 1

    def test_login_only_week_produces_no_entry_for_that_week(self) -> None:
        """A user logs in on W45 but performs a meaningful action only on W46.
        W45 must not appear in the WAU results."""
        events = [
            _make_event("user-001", "LOGIN", _DAY_1),       # W45 — login only
            _make_event("user-001", "HANDOVER_SUBMITTED", _DAY_3),  # W46 — meaningful
        ]
        wau = compute_line_command_wau(
            events, "shift_lead", _WINDOW_START, _WINDOW_END
        )
        week_45 = _iso_to_week_key(_DAY_1)
        week_46 = _iso_to_week_key(_DAY_3)
        assert wau.get(week_45, 0) == 0, (
            f"W45 should have WAU=0 (login-only); got {wau.get(week_45)}"
        )
        assert wau.get(week_46, 0) == 1, (
            f"W46 should have WAU=1; got {wau.get(week_46)}"
        )


# ===========================================================================
# Quality Studio WAU — US-021 · BR-12
# ===========================================================================

def compute_quality_studio_wau(
    events: list[dict[str, Any]],
    role_filter: str,
    window_start: str,
    window_end: str,
) -> dict[tuple[int, int], int]:
    """Compute WAU per ISO calendar week for a Quality Studio persona.

    Mirrors the KQL queries quality_studio_paint_engineer_wau and
    quality_studio_quality_validator_wau.

    Args:
        events: List of AdoptionEvent dicts with fields:
                component, actionType, userId, role, timestamp (ISO 8601 UTC).
        role_filter: 'PAINT_ENGINEER' or 'QUALITY_GATE_VALIDATOR'.
        window_start: ISO 8601 date string (inclusive).
        window_end:   ISO 8601 date string (inclusive).

    Returns:
        Dict mapping (iso_year, iso_week) → distinct active user count.
        A user is active in a week only if they have ≥1 meaningful action on
        at least one day that week.  Login-only days do not count.
    """
    ws_dt = datetime.fromisoformat(window_start).replace(tzinfo=timezone.utc)
    we_dt = datetime.fromisoformat(window_end).replace(tzinfo=timezone.utc)

    # UTC+1 offset for Europe/Berlin in November (standard time)
    berlin_offset = timedelta(hours=1)

    user_day_meaningful: dict[tuple[str, str], bool] = defaultdict(bool)

    for evt in events:
        if evt.get("component") != "quality-studio":
            continue
        if evt.get("role") != role_filter:
            continue
        ts = evt.get("timestamp", "")
        dt_utc = datetime.fromisoformat(ts.rstrip("Z")).replace(tzinfo=timezone.utc)
        if dt_utc < ws_dt or dt_utc > we_dt:
            continue

        user_id = evt["userId"]
        # Bucket by Berlin calendar day (UTC+1 in November)
        dt_berlin = dt_utc + berlin_offset
        day_key = dt_berlin.strftime("%Y-%m-%d")
        action = evt.get("actionType", "")
        if action in QUALITY_STUDIO_MEANINGFUL_ACTIONS:
            user_day_meaningful[(user_id, day_key)] = True
        else:
            if (user_id, day_key) not in user_day_meaningful:
                user_day_meaningful[(user_id, day_key)] = False

    week_users: dict[tuple[int, int], set[str]] = defaultdict(set)
    for (user_id, day_key), had_meaningful in user_day_meaningful.items():
        if had_meaningful:
            week_key = _iso_to_week_key(day_key + "T00:00:00Z")
            week_users[week_key].add(user_id)

    return {week: len(users) for week, users in week_users.items()}


def _make_qs_event(
    user_id: str,
    action: str,
    timestamp: str,
    role: str = "PAINT_ENGINEER",
    component: str = "quality-studio",
) -> dict[str, Any]:
    return {
        "userId": user_id,
        "role": role,
        "actionType": action,
        "timestamp": timestamp,
        "component": component,
        "vin": "WBY73CF080NCH14427",
    }


# ---------------------------------------------------------------------------
# Quality Studio: login-only session exclusion (US-021 AC2)
# ---------------------------------------------------------------------------


class TestQualityStudioLoginOnlyExclusion:
    def test_login_only_session_contributes_zero_wau(self) -> None:
        """A quality-studio user with only LOGIN on a day is NOT counted as active."""
        events = [_make_qs_event("qs-001", "LOGIN", _DAY_1)]
        wau = compute_quality_studio_wau(
            events, "PAINT_ENGINEER", _WINDOW_START, _WINDOW_END
        )
        assert sum(wau.values()) == 0, (
            f"Expected WAU=0 for login-only session; got {wau}"
        )

    def test_multiple_logins_same_day_still_zero_wau(self) -> None:
        """Multiple LOGINs in a single quality-studio day produce WAU=0."""
        events = [
            _make_qs_event("qs-001", "LOGIN", _DAY_1),
            _make_qs_event("qs-001", "LOGIN", _DAY_1),
            _make_qs_event("qs-002", "LOGIN", _DAY_1),
        ]
        wau = compute_quality_studio_wau(
            events, "PAINT_ENGINEER", _WINDOW_START, _WINDOW_END
        )
        assert sum(wau.values()) == 0

    def test_login_only_different_users_different_days_still_zero(self) -> None:
        """Login-only sessions across multiple quality-studio users and days → WAU=0."""
        events = [
            _make_qs_event("qs-001", "LOGIN", _DAY_1),
            _make_qs_event("qs-002", "LOGIN", _DAY_2),
            _make_qs_event("qs-003", "LOGIN", _DAY_3),
        ]
        wau = compute_quality_studio_wau(
            events, "PAINT_ENGINEER", _WINDOW_START, _WINDOW_END
        )
        assert sum(wau.values()) == 0


# ---------------------------------------------------------------------------
# Quality Studio: meaningful action inclusion
# ---------------------------------------------------------------------------


class TestQualityStudioMeaningfulActionInclusion:
    @pytest.mark.parametrize(
        "action",
        [
            "PROCESS_REPLAY_LAUNCH",
            "DEFECT_GENOME_QUERY",
            "ROOT_CAUSE_ASSIGNED",
            "CERTIFY",
            "CERTIFICATE_STUDIO_OPEN",
        ],
    )
    def test_each_meaningful_action_produces_wau_1(self, action: str) -> None:
        """Each individual Quality Studio meaningful action counts the user as active."""
        events = [_make_qs_event("qs-001", action, _DAY_1)]
        wau = compute_quality_studio_wau(
            events, "PAINT_ENGINEER", _WINDOW_START, _WINDOW_END
        )
        week_key = _iso_to_week_key(_DAY_1)
        assert wau.get(week_key, 0) == 1, (
            f"Expected WAU=1 for action={action!r}; got {wau}"
        )

    def test_login_plus_meaningful_action_counts_as_active(self) -> None:
        """A session starting with LOGIN then a meaningful action counts as active."""
        events = [
            _make_qs_event("qs-001", "LOGIN", _DAY_1),
            _make_qs_event("qs-001", "CERTIFY", _DAY_1),
        ]
        wau = compute_quality_studio_wau(
            events, "PAINT_ENGINEER", _WINDOW_START, _WINDOW_END
        )
        week_key = _iso_to_week_key(_DAY_1)
        assert wau.get(week_key, 0) == 1

    def test_two_users_both_active_counts_as_wau_2(self) -> None:
        """Two distinct quality-studio users each with a meaningful action → WAU=2."""
        events = [
            _make_qs_event("qs-001", "PROCESS_REPLAY_LAUNCH", _DAY_1),
            _make_qs_event("qs-002", "DEFECT_GENOME_QUERY", _DAY_2),
        ]
        wau = compute_quality_studio_wau(
            events, "PAINT_ENGINEER", _WINDOW_START, _WINDOW_END
        )
        week_key = _iso_to_week_key(_DAY_1)
        assert wau.get(week_key, 0) == 2

    def test_same_user_multiple_meaningful_actions_same_week_counts_once(self) -> None:
        """A single quality-studio user with multiple actions in a week is counted once."""
        events = [
            _make_qs_event("qs-001", "PROCESS_REPLAY_LAUNCH", _DAY_1),
            _make_qs_event("qs-001", "CERTIFY", _DAY_2),
        ]
        wau = compute_quality_studio_wau(
            events, "PAINT_ENGINEER", _WINDOW_START, _WINDOW_END
        )
        week_key = _iso_to_week_key(_DAY_1)
        assert wau.get(week_key, 0) == 1


# ---------------------------------------------------------------------------
# Quality Studio: role filtering — PAINT_ENGINEER vs QUALITY_GATE_VALIDATOR
# ---------------------------------------------------------------------------


class TestQualityStudioRoleFiltering:
    def test_paint_engineer_events_not_counted_for_quality_validator(self) -> None:
        """PAINT_ENGINEER events must not appear in QUALITY_GATE_VALIDATOR WAU."""
        events = [
            _make_qs_event("qs-001", "CERTIFY", _DAY_1, role="PAINT_ENGINEER"),
        ]
        wau = compute_quality_studio_wau(
            events, "QUALITY_GATE_VALIDATOR", _WINDOW_START, _WINDOW_END
        )
        assert sum(wau.values()) == 0

    def test_quality_validator_events_not_counted_for_paint_engineer(self) -> None:
        """QUALITY_GATE_VALIDATOR events must not appear in PAINT_ENGINEER WAU."""
        events = [
            _make_qs_event(
                "qs-001", "CERTIFY", _DAY_1, role="QUALITY_GATE_VALIDATOR"
            ),
        ]
        wau = compute_quality_studio_wau(
            events, "PAINT_ENGINEER", _WINDOW_START, _WINDOW_END
        )
        assert sum(wau.values()) == 0

    def test_mixed_roles_counted_independently(self) -> None:
        """Paint engineer and quality validator events produce separate WAU counts."""
        events = [
            _make_qs_event("pe-001", "PROCESS_REPLAY_LAUNCH", _DAY_1, role="PAINT_ENGINEER"),
            _make_qs_event("qv-001", "CERTIFY", _DAY_1, role="QUALITY_GATE_VALIDATOR"),
            _make_qs_event("qv-002", "CERTIFICATE_STUDIO_OPEN", _DAY_2, role="QUALITY_GATE_VALIDATOR"),
        ]
        week_key = _iso_to_week_key(_DAY_1)
        pe_wau = compute_quality_studio_wau(
            events, "PAINT_ENGINEER", _WINDOW_START, _WINDOW_END
        )
        qv_wau = compute_quality_studio_wau(
            events, "QUALITY_GATE_VALIDATOR", _WINDOW_START, _WINDOW_END
        )
        assert pe_wau.get(week_key, 0) == 1
        assert qv_wau.get(week_key, 0) == 2


# ---------------------------------------------------------------------------
# Quality Studio: login-only week exclusion across multiple weeks
# ---------------------------------------------------------------------------


class TestQualityStudioMultiWeek:
    def test_login_only_week_not_in_wau(self) -> None:
        """A quality-studio user logs in on W45 (no action) and acts on W46 — W45=0."""
        events = [
            _make_qs_event("qs-001", "LOGIN", _DAY_1),            # W45 — login only
            _make_qs_event("qs-001", "CERTIFY", _DAY_3),           # W46 — meaningful
        ]
        wau = compute_quality_studio_wau(
            events, "PAINT_ENGINEER", _WINDOW_START, _WINDOW_END
        )
        week_45 = _iso_to_week_key(_DAY_1)
        week_46 = _iso_to_week_key(_DAY_3)
        assert wau.get(week_45, 0) == 0, (
            f"W45 should have WAU=0 (login-only); got {wau.get(week_45)}"
        )
        assert wau.get(week_46, 0) == 1, (
            f"W46 should have WAU=1; got {wau.get(week_46)}"
        )

    def test_active_in_two_separate_weeks_produces_two_entries(self) -> None:
        """A quality-studio user with a meaningful action in each of two weeks appears in both."""
        events = [
            _make_qs_event("qs-001", "PROCESS_REPLAY_LAUNCH", _DAY_1),  # W45
            _make_qs_event("qs-001", "DEFECT_GENOME_QUERY", _DAY_3),    # W46
        ]
        wau = compute_quality_studio_wau(
            events, "PAINT_ENGINEER", _WINDOW_START, _WINDOW_END
        )
        assert len(wau) == 2
        for count in wau.values():
            assert count == 1


# ---------------------------------------------------------------------------
# Quality Studio: component isolation — quality-studio events only
# ---------------------------------------------------------------------------


class TestQualityStudioComponentFiltering:
    def test_line_command_events_ignored_by_quality_studio_wau(self) -> None:
        """Events from component='line-command' must not appear in quality-studio WAU."""
        events = [
            _make_qs_event(
                "qs-001",
                "CERTIFY",
                _DAY_1,
                component="line-command",
            ),
        ]
        wau = compute_quality_studio_wau(
            events, "PAINT_ENGINEER", _WINDOW_START, _WINDOW_END
        )
        assert sum(wau.values()) == 0
