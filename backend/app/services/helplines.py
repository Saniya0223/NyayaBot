"""The single registry of helpline numbers and their operating authority.

Why this file exists (finding M7 + L1). Before it, every number the product emitted
was correct, but they were scattered across ``safety_triage.py``, ``action_planner.py``,
``conversation_agent.py``, ``classifier_node.py``, ``workflows.json`` and both provider
prompts, and no single place stated "number -> (service, operating authority)". So a
reply had nothing to be checked against: W1-04 t2 invented ``1800-11-001-112`` and
attributed the real ``1930`` to RBI, and nothing in the pipeline could catch either.
A "is this number real?" check alone would have passed L1, because 1930 *is* real —
misattribution needs its own rule, which is why ``operator`` is a registry field.

Sourcing discipline. Every entry below is a number this repository already emits, with
the attribution this repository already gives it; the code reference is on each entry.
**Do not add a number you cannot source from the product or an official publication.**
A wrong entry here is finding M7 again, only harder to spot. In particular the crisis
lines are *imported* from ``safety_triage`` rather than restated, so the two can never
drift apart.

This registry is a filter and a model-context input. It is never a footer: the guard
must not append a helpline number to a reply, because ordinary turns are asserted to be
free of crisis numbers (``test_cycle1_verification.py:216``,
``test_safety_crisis_and_scope.py:197,207``).
"""

from __future__ import annotations

import re
from dataclasses import dataclass
from typing import Any, Optional

from app.services.safety_triage import CRISIS_HELPLINES, EMERGENCY_NUMBER


@dataclass(frozen=True)
class Helpline:
    number: str          # as published, hyphenation included
    service: str         # what the line is for
    operator: str        # the authority that actually runs it
    scope: str           # national | crisis | emergency
    operator_aliases: tuple[str, ...] = ()

    @property
    def digits(self) -> str:
        return re.sub(r"\D", "", self.number)


_CRISIS_OPERATORS: dict[str, tuple[str, tuple[str, ...]]] = {
    # safety_triage.py:44-49 supplies the numbers; these are their publishers.
    "Tele-MANAS": ("Ministry of Health and Family Welfare", ("MoHFW", "Tele-MANAS")),
    "KIRAN": ("Ministry of Social Justice and Empowerment", ("KIRAN",)),
    "AASRA": ("AASRA", ("AASRA",)),
}


def _crisis_entries() -> tuple[Helpline, ...]:
    """Imported, never restated - one source for every crisis number in the product."""
    entries = []
    for name, number in CRISIS_HELPLINES:
        operator, aliases = _CRISIS_OPERATORS.get(name, (name, (name,)))
        entries.append(Helpline(
            number=number, service=f"{name} mental-health support",
            operator=operator, scope="crisis", operator_aliases=aliases,
        ))
    return tuple(entries)


HELPLINES: tuple[Helpline, ...] = _crisis_entries() + (
    Helpline(
        # safety_triage.py:49 (EMERGENCY_NUMBER), safety_triage.py:542,553
        number=EMERGENCY_NUMBER, service="all-in-one emergency response",
        operator="Ministry of Home Affairs", scope="emergency",
        operator_aliases=("MHA", "Ministry of Home Affairs", "ERSS"),
    ),
    Helpline(
        # groq_provider.py:74, gemini_provider.py:66, action_planner.py:110,
        # conversation_agent.py:589, workflows.json:226 - always "Cyber Helpline",
        # never RBI. This entry is what makes L1 checkable.
        number="1930", service="cyber financial fraud reporting",
        operator="Indian Cyber Crime Coordination Centre (I4C), Ministry of Home Affairs",
        scope="national",
        operator_aliases=("I4C", "Indian Cyber Crime Coordination Centre", "MHA",
                          "Ministry of Home Affairs", "National Cyber Crime Reporting Portal"),
    ),
    Helpline(
        # groq_provider.py:74, gemini_provider.py:66, safety_triage.py:542,553
        number="181", service="women's helpline",
        operator="Ministry of Women and Child Development", scope="national",
        operator_aliases=("MWCD", "Ministry of Women and Child Development"),
    ),
    Helpline(
        # safety_triage.py:542,553
        number="1098", service="Childline for children in distress",
        operator="Ministry of Women and Child Development", scope="national",
        operator_aliases=("MWCD", "Ministry of Women and Child Development", "Childline"),
    ),
    Helpline(
        # groq_provider.py:74, gemini_provider.py:66
        number="1091", service="women's helpline (police)",
        operator="State police", scope="national",
        operator_aliases=("police", "State police"),
    ),
    Helpline(
        # classifier_node.py:43, conversation_agent.py:173
        number="15100", service="free legal aid",
        operator="National Legal Services Authority (NALSA)", scope="national",
        operator_aliases=("NALSA", "DLSA", "National Legal Services Authority",
                          "District Legal Services Authority"),
    ),
)

# digits -> entry, for O(1) membership from a reply token
BY_DIGITS: dict[str, Helpline] = {entry.digits: entry for entry in HELPLINES}


def lookup(token: str) -> Optional[Helpline]:
    """Resolve a reply token (any hyphenation, any dash character) to a registry entry."""
    return BY_DIGITS.get(re.sub(r"\D", "", token or ""))


def is_registered(token: str) -> bool:
    return lookup(token) is not None


def model_context() -> list[dict[str, Any]]:
    """Compact form for LLMResponseContext.helpline_registry."""
    return [
        {"number": entry.number, "service": entry.service, "operator": entry.operator}
        for entry in HELPLINES
    ]


# Authority names the guard is allowed to *recognise* when checking an attribution.
# Closed set: registry operators and their aliases, plus the authorities the domain
# registry itself names. Anything outside it is not treated as an attribution at all,
# so this is never a general authority checker.
ATTRIBUTABLE_AUTHORITIES: tuple[str, ...] = (
    "RBI", "Reserve Bank of India",
    "SEBI", "TRAI", "IRDAI", "NPCI", "UIDAI",
    "I4C", "Indian Cyber Crime Coordination Centre",
    "MHA", "Ministry of Home Affairs",
    "NALSA", "DLSA", "National Legal Services Authority",
    "MWCD", "Ministry of Women and Child Development",
    "MoHFW", "Ministry of Health and Family Welfare",
    "Ministry of Social Justice and Empowerment",
    "Ministry of Labour & Employment", "Ministry of Labour and Employment",
    "Ministry of Housing and Urban Affairs",
    "Department of Consumer Affairs",
    "TRAI",
)
