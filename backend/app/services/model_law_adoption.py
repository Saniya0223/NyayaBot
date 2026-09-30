"""Whether a State has enacted the Model Tenancy Act, 2021 - and the honest default.

Finding H4: W1-02 t1 told a Jaipur tenant to cite "Model Tenancy Act ke Section 11" in a
notice, and t2 asserted flatly that s.30 governs where the dispute is adjudicated.
Section 11 and Section 30 are *not* invented - both are in
``app/data/model_tenancy_provisions.json``. The defect is that the MTA 2021 is a central
**model** law that each State must enact for itself, Rajasthan has not enacted it, and the
one honest signal the pipeline already carries (``rag_node.py:121``,
``document_type: "Model law - State adoption must be verified"``) never reached the reply.

**The register is deliberately empty.** Every State resolves to ``UNVERIFIED``, and
``UNVERIFIED`` behaves exactly like ``NOT_ADOPTED`` for output purposes: the caveat is
appended. That is the point. The register's job is to let a future curator *add* a sourced
entry, not to let a fix loop guess. Fabricating an adoption status while fixing a
fabrication finding would be strictly worse than the bug.

To add an entry you need, in the comment beside it: the State's own Act or notification
name, its number/year, and the gazette or official-portal reference. No secondary source,
no news report, no model output.
"""

from __future__ import annotations

ADOPTED = "ADOPTED"
UNVERIFIED = "UNVERIFIED"

# canonical State/UT name (as jurisdiction.STATE_VARIANTS spells it) -> ADOPTED
# Intentionally empty. See the module docstring before adding anything.
MODEL_TENANCY_ADOPTION: dict[str, str] = {}


def adoption_status(state: str | None) -> str:
    """ADOPTED only for a State with a sourced register entry; UNVERIFIED otherwise.

    An unknown State, a missing State and a State known not to have enacted the Act all
    return UNVERIFIED, and every caller must treat UNVERIFIED as "do not present the
    provision as binding local law".
    """
    if not state:
        return UNVERIFIED
    return MODEL_TENANCY_ADOPTION.get(state.strip(), UNVERIFIED)


def adoption_confirmed(state: str | None) -> bool:
    return adoption_status(state) == ADOPTED


# Corpus metadata marker that says a provision is model law pending State adoption.
# Set by rag_node.py:121; CaseWorkspacePanel.tsx:120 matches it with
# .toLowerCase().includes('model law'), so the wording must not change.
MODEL_LAW_DOCUMENT_TYPE_MARKERS: tuple[str, ...] = ("model law", "adoption must be verified")


def is_model_law_document_type(document_type: str | None) -> bool:
    value = (document_type or "").casefold()
    return any(marker in value for marker in MODEL_LAW_DOCUMENT_TYPE_MARKERS)
