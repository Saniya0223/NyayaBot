"""Dynamic case titles that follow what is actually known.

A case opens before anyone knows what it is about, so its first title is a
placeholder. As the conversation establishes the domain, the specific issue and
a distinguishing detail, the title should catch up - but it must not churn on
every turn, because a case the user is watching should not keep renaming itself
for no visible reason.

The rule here: rename only when something materially new is known - the domain
changed, the specific issue became clear, or the title is still a placeholder.
Cosmetic differences never trigger a rename.
"""

from __future__ import annotations

from typing import Any, Optional

from app.domains.registry import domain_registry


# Titles produced before the case is understood. Any of these may be replaced
# freely; anything else is treated as a title worth keeping unless the domain or
# issue genuinely changed.
PLACEHOLDER_TITLES = {
    "legal information request",
    "new case",
    "untitled case",
    "",
}


def _clean(value: Optional[str]) -> str:
    return (value or "").strip()


def _issue_display_name(domain: Any, issue_type: Optional[str]) -> Optional[str]:
    """Human label for a specific issue, or None when it is still the default."""
    issue_id = _clean(issue_type)
    if not issue_id or issue_id.upper() == "UNCLASSIFIED":
        return None
    if issue_id == domain.default_issue_type_id:
        # The default is a catch-all, so it adds nothing over the domain title.
        return None
    for issue in domain.issue_types:
        if issue.id == issue_id:
            return issue.display_name
    return None


def _qualifier(profile: Any) -> Optional[str]:
    """One distinguishing detail, so a case list stays readable.

    Deliberately narrow: an amount or the other party, never the user's own
    identity or address.
    """
    amount = getattr(profile, "disputed_amount", 0) or 0
    if amount and amount > 0:
        return f"Rs {int(amount):,}"
    other_party = _clean(getattr(profile, "opposite_party_name", None))
    if other_party:
        return other_party
    state = _clean(getattr(profile, "user_state", None))
    if state:
        return state
    return None


def compose_case_title(profile: Any) -> str:
    """Best title available from what the case currently knows."""
    domain = domain_registry.resolve(getattr(profile, "category", "GENERAL"))

    if domain.id == "GENERAL":
        # Nothing is established yet; the placeholder is the honest title.
        return domain.case_title

    issue_name = _issue_display_name(domain, getattr(profile, "issue_type", None))
    base = issue_name or domain.case_title

    qualifier = _qualifier(profile)
    return f"{base} - {qualifier}" if qualifier else base


def should_retitle(
    profile: Any,
    new_title: str,
    previous_category: Optional[str] = None,
    previous_issue_type: Optional[str] = None,
) -> bool:
    """True only when a rename is justified by materially new information."""
    current = _clean(getattr(profile, "title", None))
    new_title = _clean(new_title)

    if not new_title or new_title == current:
        return False

    # A placeholder should always give way to something real.
    if current.casefold() in PLACEHOLDER_TITLES:
        return True

    # The domain itself changed - the old title describes the wrong case.
    if previous_category is not None and previous_category != getattr(profile, "category", None):
        return True

    # The specific issue became known, or changed to a different specific issue.
    if previous_issue_type is not None and previous_issue_type != getattr(profile, "issue_type", None):
        domain = domain_registry.resolve(getattr(profile, "category", "GENERAL"))
        became_specific = _issue_display_name(domain, getattr(profile, "issue_type", None)) is not None
        if became_specific:
            return True

    # Everything else - a newly learned amount or party name on an already
    # well-named case - is not worth renaming for.
    return False


def apply_dynamic_title(
    profile: Any,
    previous_category: Optional[str] = None,
    previous_issue_type: Optional[str] = None,
) -> Optional[str]:
    """Retitle the case when justified. Returns the new title, else None."""
    candidate = compose_case_title(profile)
    if should_retitle(profile, candidate, previous_category, previous_issue_type):
        profile.title = candidate
        return candidate
    return None
