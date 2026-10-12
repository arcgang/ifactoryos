"""Semantic validation helpers for the optimizer request payload.

Option code validation compares VinInput.optionCodes against the known
constraint model. In production this set is loaded from the constraint
threshold table or a configuration file; here we keep the validation
logic separate from the router so it is trivially testable.
"""

from __future__ import annotations

from app.models import VinInput

# ---------------------------------------------------------------------------
# Known valid option codes for the MY25/MY26 constraint model.
# A configurable set; in production, populate from the DB or config at startup.
# An empty set means "all codes are valid" — used during testing.
# ---------------------------------------------------------------------------

_KNOWN_OPTION_CODES: frozenset[str] = frozenset()


def set_known_option_codes(codes: frozenset[str]) -> None:
    """Override the known option code set (called from config/startup)."""
    global _KNOWN_OPTION_CODES
    _KNOWN_OPTION_CODES = codes


def validate_option_codes(vins: list[VinInput]) -> frozenset[str]:
    """Return the set of unknown option codes present in any VinInput.

    Returns an empty set when _KNOWN_OPTION_CODES is not configured (permissive).
    """
    if not _KNOWN_OPTION_CODES:
        return frozenset()
    unknown: set[str] = set()
    for vin_input in vins:
        for code in vin_input.optionCodes:
            if code not in _KNOWN_OPTION_CODES:
                unknown.add(code)
    return frozenset(unknown)
