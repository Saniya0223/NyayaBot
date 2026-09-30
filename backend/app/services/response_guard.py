"""Deterministic output guard for chat replies: pure functions, no I/O, no provider.

Why this is Python and not a prompt. The chat system prompt *already* said, verbatim, at
the commit wave-1 ran against (``groq_provider.py:93-96``, ``gemini_provider.py:83-85``):

    Do not alter state, invent facts, cite laws not present in verified sources, promise
    outcomes, or fabricate deadlines.
    If verified sources are empty, clearly say the exact legal provision still needs
    verification instead of guessing.
    Never describe the Model Tenancy Act, 2021 as binding local law unless the supplied
    context confirms State adoption ...

C2 violated the first two and H4 the third, on a healthy non-degraded turn, while also
being told machine-readably that the domain had no corpus. The prompt-only fix has already
been tried in the exact words a report would recommend and it failed, so nothing here
depends on model compliance.

Standing rules, in priority order:

1. **Caveat on doubt, never delete.** A deleted-but-correct citation is a silent harm the
   user cannot detect, and CONSUMER/HOUSING_TENANT have real corpora whose citations are
   the product's main value. Match on section number alone; accept sub-clauses (``69(2)``
   against corpus ``69``); compare Act names loosely; require an Act-name match only to
   *reject*, never to accept.
2. **Every allowlist is read from code at runtime**, never hand-typed: the statute corpora,
   ``domain.rag.official_sources``, the curated document citation map, the helpline
   registry, the fact-key set. The one absolute is that no case-law corpus exists anywhere
   in the product, so *every* judgment citation is unverifiable by construction.
3. **Every edit is recorded in ``redactions``** so a wrong removal shows up in a test
   instead of vanishing silently.
4. **Never inject a helpline number.** The registry is a filter and a model-context input,
   never a footer: ordinary turns are asserted free of crisis numbers.
5. **Idempotent.** ``guard(guard(x)) == guard(x)``; the retry path re-guards on resume.
"""

from __future__ import annotations

import re
import unicodedata
from dataclasses import dataclass
from typing import Any, Iterable

from app.agents.rag_node import statutory_rag
from app.domains import domain_registry
from app.services.document_citations import DOCUMENT_CITATIONS
from app.services.helplines import ATTRIBUTABLE_AUTHORITIES, HELPLINES, lookup as helpline_lookup
from app.services.model_law_adoption import adoption_confirmed, is_model_law_document_type

# --------------------------------------------------------------------- unicode

# The recorded replies use U+2011 NON-BREAKING HYPHEN, U+2013/2014 dashes and U+2248.
# A hand-written "-" will not match them, so every dash class goes through this.
DASH = "\\-\u2010\u2011\u2012\u2013\u2014\u2015\u2212"
DASH_CLASS = f"[{DASH}]"
# U+202F NARROW NO-BREAK SPACE sits between digits and % in W1-06 t4. Python's \s does
# match it, so \s is used everywhere rather than a literal space or [ ].
# The Hindi/Hinglish approximators are included too, or an inline removal of "lagbhag 40 %"
# leaves a dangling "lagbhag" behind.
APPROX = ("(?:\u2248|~|approx\\.?|about|around|roughly|nearly|lagbhag|kareeb|qareeb"
          "|\u0932\u0917\u092d\u0917|\u0915\u0930\u0940\u092c)")
QUOTE_OPEN = "\"'\u201c\u2018\u201e\u00ab"
QUOTE_CLOSE = "\"'\u201d\u2019\u201c\u00bb"


# ---------------------------------------------------------------- result types

@dataclass(frozen=True)
class Redaction:
    """One machine-readable edit. Controlled vocabulary only - never reply text or PII."""

    rule: str
    detail: str = ""

    def as_log(self) -> str:
        return self.rule if not self.detail else f"{self.rule}:{self.detail}"


@dataclass(frozen=True)
class GuardResult:
    text: str
    redactions: tuple[Redaction, ...] = ()

    @property
    def changed(self) -> bool:
        return bool(self.redactions)

    def rules(self) -> tuple[str, ...]:
        return tuple(dict.fromkeys(item.rule for item in self.redactions))


# ------------------------------------------------------------------- allowlist

_SECTION_LEAD = r"(?:Sections?|Secs?\.|S\.|Articles?|Arts?\.|\u0927\u093e\u0930\u093e)"
_SECTION_NUM = r"\d{1,4}[A-Z]{0,2}(?:\(\s*\d{1,3}[A-Za-z]?\s*\))*"
_SECTION_JOIN = f"(?:\\s*(?:&|and|,|to|or|{DASH_CLASS})\\s*{_SECTION_NUM})*"

CITATION_RE = re.compile(
    rf"\b(?P<lead>{_SECTION_LEAD})\s*(?P<nums>{_SECTION_NUM}{_SECTION_JOIN})",
    re.IGNORECASE,
)
_BARE_NUM_RE = re.compile(_SECTION_NUM)

# Only a `section` string that actually names a section contributes section numbers.
# "Paragraphs 6-10" (RBI/2017-18/15) and "State/fact specific" contribute an Act name
# only - otherwise a reply citing "Section 6" in CYBER_FRAUD would be wrongly allowlisted.
_SECTION_STRING_RE = re.compile(rf"^\s*{_SECTION_LEAD}\b", re.IGNORECASE)


def _norm_section(value: str) -> str:
    return re.sub(r"\s+", "", (value or "")).casefold()


def _section_variants(value: str) -> set[str]:
    """``69(2)`` -> {``69(2)``, ``69``}; a cited sub-clause of an allowlisted section is verified."""
    token = _norm_section(value)
    out = {token}
    while token.endswith(")") and "(" in token:
        token = token[: token.rindex("(")]
        out.add(token)
    return {item for item in out if item}


def _sections_from_string(value: str) -> set[str]:
    """Exact tokens only - never the parents.

    Expanding both sides would allowlist a bare ``Section 2`` everywhere merely because the
    corpus holds ``Section 2(11)``, which would let a fabricated ``Section 7 of the Code on
    Wages`` through. Only the *cited* token expands, in ``is_verified_section``, so a cited
    sub-clause of an allowlisted section is verified and nothing wider.
    """
    if not value or not _SECTION_STRING_RE.match(value):
        return set()
    return {_norm_section(match.group(0)) for match in _BARE_NUM_RE.finditer(value)}


_CITED_JOIN_RE = re.compile(rf"\s*(?:&|and|,|to|or|{DASH_CLASS})\s*", re.IGNORECASE)


def _cited_parts(cited: str) -> list[str]:
    """Split a run such as "34 & 35" or "2(11), 2(47)" into its individual numbers."""
    return [part for part in _CITED_JOIN_RE.split(cited or "") if part.strip()]


def _norm_act(value: str) -> str:
    """Loose Act-name form: ``Act, 2019`` == ``Act 2019``. Used only to *reject*."""
    text = unicodedata.normalize("NFKC", value or "").casefold()
    text = re.sub(rf"[,.'\u2019\u2018\"\u201c\u201d{DASH}/]", " ", text)
    return re.sub(r"\s+", " ", text).strip()


# Anchor words that end (or begin) an Indian enactment's name.
_ACT_ANCHOR = r"(?:Act|Sanhita|Adhiniyam|Nyaya|Code|Rules|Regulations|Ordinance|Notification|Circular)"
ACT_NAME_RE = re.compile(
    rf"\b(?:[A-Z][\w&'\u2019{DASH}]*\s+(?:of\s+|on\s+|and\s+|for\s+|the\s+)?){{0,6}}{_ACT_ANCHOR}"
    rf"(?:\s+(?:on|of|for)\s+[A-Z][\w&'\u2019{DASH}]*(?:\s+[A-Z][\w&'\u2019{DASH}]*)?)?"
    rf"(?:\s*,?\s*(?:19|20)\d{{2}})?\b"
)


@dataclass(frozen=True)
class CitationAllowlist:
    """Everything the product already vouches for, for one case's domain."""

    domain_id: str
    corpus_available: bool
    verified_sections: frozenset[str]
    act_names: tuple[str, ...]
    model_law_sections: frozenset[str]
    official_authorities: tuple[str, ...]
    allowed_urls: frozenset[str]
    fact_keys: frozenset[str]

    def is_verified_section(self, cited: str) -> bool:
        """A multi-number citation ("Sections 34 & 35") is verified only if every number
        in it is. The corpus stores that pair as one string, so both halves are present.
        """
        parts = _cited_parts(cited)
        return bool(parts) and all(
            _section_variants(part) & self.verified_sections for part in parts
        )

    def is_model_law_section(self, cited: str) -> bool:
        return any(
            _section_variants(part) & self.model_law_sections
            for part in _cited_parts(cited)
        )


def _corpus_items(corpus_ids: Iterable[str]) -> list[dict[str, Any]]:
    items: list[dict[str, Any]] = []
    for corpus_id in corpus_ids:
        items.extend(statutory_rag.corpus.get(corpus_id, []) or [])
    return items


# URLs this product itself publishes. Allowlisted by exact URL (normalised), not by host:
# a fabricated path on a real host is still a dead end for the user, which is why
# rbi.org.in/Scripts/Complaints.aspx is not here while the RBI notification PDF is.
# Sources: portal_selector.py:31,49,68,85,100,117,134 and dossier_generator.py:37,160,179.
_PRODUCT_URLS: tuple[str, ...] = (
    "https://consumerhelpline.gov.in/",
    "https://cybercrime.gov.in/",
    "https://www.cybercrime.gov.in/",
    "https://rtionline.gov.in/",
    "https://services.ecourts.gov.in/ecourtindia_v6/",
    "https://samadhan.labour.gov.in/",
    "https://pgportal.gov.in/",
    "https://edaakhil.nic.in",
)


def _norm_url(value: str) -> str:
    text = (value or "").strip().casefold()
    text = re.sub(r"^https?://", "", text)
    text = re.sub(r"^www\.", "", text)
    return text.rstrip("/")


def _fact_keys(domain_id: str) -> frozenset[str]:
    """Closed set, read live: the domain's fact keys plus every profile field name."""
    from app.schemas.chat import StructuredCaseProfile

    keys = {fact.key for fact in domain_registry.resolve(domain_id).facts}
    keys |= set(StructuredCaseProfile.model_fields)
    return frozenset(key for key in keys if "_" in key)


def build_allowlist(profile: Any) -> CitationAllowlist:
    """Union of the turn's retrieved provisions, the domain corpus, the curated document
    citation map, and ``official_sources`` Act names.

    Deliberately wider than "this turn's retrieved provisions": the retrieval limit is 4,
    so a CONSUMER turn that happened to retrieve two provisions would otherwise have the
    guard delete a correct citation of a third. The whole corpus is curated repository
    content the product vouches for, which keeps the set closed and checkable.
    """
    domain_id = domain_registry.normalize_id(getattr(profile, "category", None))
    domain = domain_registry.resolve(domain_id)
    corpus_ids = tuple(domain.rag.corpus_ids)

    sections: set[str] = set()
    acts: list[str] = []
    model_law: set[str] = set()

    # (a) this turn's retrieved provisions, as carried on the profile
    for source in getattr(profile, "legal_sources", None) or []:
        if not isinstance(source, dict):
            continue
        section = str(source.get("section") or "")
        act = str(source.get("act") or source.get("title") or "")
        if act:
            acts.append(act)
        found = _sections_from_string(section)
        sections |= found
        if is_model_law_document_type(str(source.get("document_type") or "")):
            model_law |= found

    # (b) the domain's own statute corpus, in full
    for item in _corpus_items(corpus_ids):
        found = _sections_from_string(str(item.get("section") or ""))
        sections |= found
        if item.get("act"):
            acts.append(str(item["act"]))
        if is_model_law_document_type(str(item.get("document_type") or "")):
            model_law |= found

    # (c) the curated citation map. The domain's own bound documents first, then the whole
    # map: the product prints these provisions inside documents it generates, so deleting
    # one from the chat message that explains that same document is a contradiction we
    # must not ship. RTI_SEC6 is bound to no domain, which is why the global tier exists.
    for citations in DOCUMENT_CITATIONS.values():
        for citation in citations:
            sections |= _sections_from_string(str(citation.get("section") or ""))
            if citation.get("act"):
                acts.append(str(citation["act"]))

    # (d) the RTI corpus: no domain binds it, but the product generates an RTI application
    # from it, so its provisions are vouched for.
    for item in _corpus_items(["RTI"]):
        sections |= _sections_from_string(str(item.get("section") or ""))
        if item.get("act"):
            acts.append(str(item["act"]))

    authorities: list[str] = []
    urls: set[str] = {_norm_url(url) for url in _PRODUCT_URLS}
    for source in domain.rag.official_sources:
        acts.append(source.title)
        if source.authority:
            authorities.append(source.authority)
        if source.url:
            urls.add(_norm_url(source.url))
    # Every domain's own official source URL: the product publishes all of them, and a
    # reply may legitimately point a user at another domain's portal.
    for other in domain_registry.all():
        for source in other.rag.official_sources:
            if source.url:
                urls.add(_norm_url(source.url))

    return CitationAllowlist(
        domain_id=domain_id,
        corpus_available=bool(corpus_ids),
        verified_sections=frozenset(sections),
        act_names=tuple(dict.fromkeys(_norm_act(act) for act in acts if act)),
        model_law_sections=frozenset(model_law),
        official_authorities=tuple(dict.fromkeys(authorities)),
        allowed_urls=frozenset(urls),
        fact_keys=_fact_keys(domain_id),
    )


# ------------------------------------------------------------ script-matched copy

def _style(profile: Any) -> str:
    script = str(getattr(profile, "script_style", "") or "roman").casefold()
    language = str(getattr(profile, "language_style", "") or "english").casefold()
    if script == "devanagari" or language == "hindi":
        return "devanagari"
    if language == "hinglish":
        return "hinglish"
    return "english"


def _copy(profile: Any, english: str, hinglish: str, devanagari: str) -> str:
    style = _style(profile)
    return {"english": english, "hinglish": hinglish, "devanagari": devanagari}[style]


# A short stable marker per appended paragraph, used for idempotence. Each marker must
# stay a substring of every script variant of its paragraph.
_MARKERS = {
    "case_law": ("verified case-law database", "verified case-law database",
                 "\u0938\u0924\u094d\u092f\u093e\u092a\u093f\u0924 case-law database"),
    "statute": ("Note on legal sources", "Legal sources ke baare mein",
                "\u0915\u093e\u0928\u0942\u0928\u0940 \u0938\u094d\u0930\u094b\u0924\u094b\u0902 \u0915\u0947 \u092c\u093e\u0930\u0947 \u092e\u0947\u0902"),
    "outcome": ("cannot be reduced to a number", "number mein nahi bataya ja sakta",
                "\u0938\u0902\u0916\u094d\u092f\u093e \u092e\u0947\u0902 \u0928\u0939\u0940\u0902 \u092c\u0924\u093e\u092f\u093e \u091c\u093e \u0938\u0915\u0924\u093e"),
    "helpline": ("your own card, passbook", "apne card, passbook",
                 "\u0905\u092a\u0928\u0947 card, passbook"),
    "adoption": ("model law that each State must enact", "model law hai jise har State ko",
                 "model \u0915\u093e\u0928\u0942\u0928 \u0939\u0948 \u091c\u093f\u0938\u0947 \u0939\u0930 State \u0915\u094b"),
}


def _marker(name: str, profile: Any) -> str:
    english, hinglish, devanagari = _MARKERS[name]
    return _copy(profile, english, hinglish, devanagari)


def _case_law_note(profile: Any) -> str:
    return _copy(
        profile,
        "On judgments: I do not have a verified case-law database, so I cannot cite court "
        "decisions. Do not put any case name in a legal notice or complaint unless an "
        "advocate has verified it against the official report.",
        "Judgments ke baare mein: mere paas verified case-law database nahi hai, isliye main "
        "court decisions cite nahi kar sakta. Kisi bhi case ka naam legal notice ya complaint "
        "mein tab tak na daalein jab tak koi advocate use official report se verify na kar de.",
        "\u0928\u093f\u0930\u094d\u0923\u092f\u094b\u0902 \u0915\u0947 \u092c\u093e\u0930\u0947 \u092e\u0947\u0902: \u092e\u0947\u0930\u0947 \u092a\u093e\u0938 \u0938\u0924\u094d\u092f\u093e\u092a\u093f\u0924 case-law database \u0928\u0939\u0940\u0902 \u0939\u0948, \u0907\u0938\u0932\u093f\u090f \u092e\u0948\u0902 \u0905\u0926\u093e\u0932\u0924\u0940 \u092b\u0948\u0938\u0932\u0947 \u0909\u0926\u094d\u0927\u0930\u093f\u0924 \u0928\u0939\u0940\u0902 \u0915\u0930 \u0938\u0915\u0924\u093e\u0964 \u0915\u093f\u0938\u0940 \u092d\u0940 \u092e\u093e\u092e\u0932\u0947 \u0915\u093e \u0928\u093e\u092e \u0915\u093e\u0928\u0942\u0928\u0940 \u0928\u094b\u091f\u093f\u0938 \u092f\u093e \u0936\u093f\u0915\u093e\u092f\u0924 \u092e\u0947\u0902 \u0924\u092c \u0924\u0915 \u0928 \u0932\u093f\u0916\u0947\u0902 \u091c\u092c \u0924\u0915 \u0915\u094b\u0908 \u0905\u0926\u094d\u0935\u0915\u094d\u0924\u093e \u0909\u0938\u0947 \u0938\u0930\u0915\u093e\u0930\u0940 \u0930\u093f\u092a\u094b\u0930\u094d\u091f \u0938\u0947 \u0938\u0924\u094d\u092f\u093e\u092a\u093f\u0924 \u0928 \u0915\u0930 \u0926\u0947\u0964",
    )


def _statute_note(profile: Any, allow: CitationAllowlist) -> str:
    authority = allow.official_authorities[0] if allow.official_authorities else ""
    # The authority is named, never the URL: EMPLOYMENT's official source URL carries six
    # underscores and test_api.py:170 asserts no underscore reaches reply_text. The URL
    # already reaches the user through the workspace "Laws & rights" panel.
    if authority:
        english_tail = (
            f"Please check the official text published by the {authority} "
            "(the link is in your case workspace) before relying on a section number."
        )
        hinglish_tail = (
            f"Kisi bhi section number par bharosa karne se pehle {authority} dwara "
            "publish kiya gaya official text check kar lein (link aapke case workspace mein hai)."
        )
        devanagari_tail = (
            f"\u0915\u093f\u0938\u0940 \u092d\u0940 \u0938\u0947\u0915\u094d\u0936\u0928 \u0928\u0902\u092c\u0930 \u092a\u0930 \u092d\u0930\u094b\u0938\u093e \u0915\u0930\u0928\u0947 \u0938\u0947 \u092a\u0939\u0932\u0947 {authority} "
            "\u0926\u094d\u0935\u093e\u0930\u093e \u092a\u094d\u0930\u0915\u093e\u0936\u093f\u0924 \u0938\u0930\u0915\u093e\u0930\u0940 \u092a\u093e\u0920 \u0926\u0947\u0916 \u0932\u0947\u0902 (\u0932\u093f\u0902\u0915 \u0906\u092a\u0915\u0947 case workspace \u092e\u0947\u0902 \u0939\u0948)\u0964"
        )
    else:
        english_tail = "Please have the exact provision verified before relying on a section number."
        hinglish_tail = "Kisi section number par bharosa karne se pehle exact provision verify kara lein."
        devanagari_tail = "\u0915\u093f\u0938\u0940 \u0938\u0947\u0915\u094d\u0936\u0928 \u0928\u0902\u092c\u0930 \u092a\u0930 \u092d\u0930\u094b\u0938\u093e \u0915\u0930\u0928\u0947 \u0938\u0947 \u092a\u0939\u0932\u0947 \u0938\u0939\u0940 \u092a\u094d\u0930\u093e\u0935\u0927\u093e\u0928 \u0938\u0924\u094d\u092f\u093e\u092a\u093f\u0924 \u0915\u0930\u093e \u0932\u0947\u0902\u0964"
    return _copy(
        profile,
        "Note on legal sources: I do not have verified statutory text for this kind of case, "
        "so I have removed the section numbers I could not confirm. " + english_tail,
        "Legal sources ke baare mein: is tarah ke case ke liye mere paas verified statutory "
        "text nahi hai, isliye jo section numbers main confirm nahi kar saka unhe hata diya hai. "
        + hinglish_tail,
        "\u0915\u093e\u0928\u0942\u0928\u0940 \u0938\u094d\u0930\u094b\u0924\u094b\u0902 \u0915\u0947 \u092c\u093e\u0930\u0947 \u092e\u0947\u0902: \u0907\u0938 \u0924\u0930\u0939 \u0915\u0947 \u092e\u093e\u092e\u0932\u0947 \u0915\u0947 \u0932\u093f\u090f \u092e\u0947\u0930\u0947 \u092a\u093e\u0938 \u0938\u0924\u094d\u092f\u093e\u092a\u093f\u0924 \u0915\u093e\u0928\u0942\u0928\u0940 \u092a\u093e\u0920 \u0928\u0939\u0940\u0902 \u0939\u0948, \u0907\u0938\u0932\u093f\u090f \u091c\u093f\u0928 \u0938\u0947\u0915\u094d\u0936\u0928 \u0928\u0902\u092c\u0930\u094b\u0902 \u0915\u0940 \u092a\u0941\u0937\u094d\u091f\u093f \u0928\u0939\u0940\u0902 \u0939\u094b \u0938\u0915\u0940 \u0909\u0928\u094d\u0939\u0947\u0902 \u0939\u091f\u093e \u0926\u093f\u092f\u093e \u0939\u0948\u0964 "
        + devanagari_tail,
    )


def _outcome_note(profile: Any) -> str:
    return _copy(
        profile,
        "On the chance of winning: the strength of a case cannot be reduced to a number, and "
        "I do not calculate one. What actually matters is the evidence you hold, whether the "
        "claim is within the limitation period, the forum's jurisdiction, and what the other "
        "side does when it receives a formal notice.",
        "Jeetne ke chance ke baare mein: kisi case ki strength number mein nahi bataya ja sakta, "
        "aur main koi number calculate nahi karta. Jo waqai matter karta hai: aapke paas kaun se "
        "proof hain, claim limitation period ke andar hai ya nahi, forum ki jurisdiction, aur "
        "formal notice milne par doosra paksh kya karta hai.",
        "\u091c\u0940\u0924\u0928\u0947 \u0915\u0940 \u0938\u0902\u092d\u093e\u0935\u0928\u093e \u0915\u0947 \u092c\u093e\u0930\u0947 \u092e\u0947\u0902: \u0915\u093f\u0938\u0940 \u092e\u093e\u092e\u0932\u0947 \u0915\u0940 \u092e\u091c\u092c\u0942\u0924\u0940 \u0938\u0902\u0916\u094d\u092f\u093e \u092e\u0947\u0902 \u0928\u0939\u0940\u0902 \u092c\u0924\u093e\u092f\u093e \u091c\u093e \u0938\u0915\u0924\u093e, \u0914\u0930 \u092e\u0948\u0902 \u0915\u094b\u0908 \u0938\u0902\u0916\u094d\u092f\u093e \u0928\u0939\u0940\u0902 \u0928\u093f\u0915\u093e\u0932\u0924\u093e\u0964 \u0905\u0938\u0932 \u092e\u0947\u0902 \u092e\u093e\u092f\u0928\u0947 \u0930\u0916\u0924\u093e \u0939\u0948: \u0906\u092a\u0915\u0947 \u092a\u093e\u0938 \u0915\u094c\u0928 \u0938\u0947 \u0938\u092c\u0942\u0924 \u0939\u0948\u0902, \u0926\u093e\u0935\u093e \u0938\u092e\u092f-\u0938\u0940\u092e\u093e \u0915\u0947 \u0905\u0902\u0926\u0930 \u0939\u0948 \u092f\u093e \u0928\u0939\u0940\u0902, \u092e\u0902\u091a \u0915\u093e \u0915\u094d\u0937\u0947\u0924\u094d\u0930\u093e\u0927\u093f\u0915\u093e\u0930, \u0914\u0930 \u0914\u092a\u091a\u093e\u0930\u093f\u0915 \u0928\u094b\u091f\u093f\u0938 \u092e\u093f\u0932\u0928\u0947 \u092a\u0930 \u0926\u0942\u0938\u0930\u093e \u092a\u0915\u094d\u0937 \u0915\u094d\u092f\u093e \u0915\u0930\u0924\u093e \u0939\u0948\u0964",
    )


def _helpline_note(profile: Any) -> str:
    return _copy(
        profile,
        "I removed a phone number I could not verify. Please take any bank or company helpline "
        "from your own card, passbook, account statement or the organisation's official website.",
        "Ek phone number jo main verify nahi kar saka, use hata diya hai. Kisi bhi bank ya company "
        "ki helpline apne card, passbook, account statement ya us sanstha ki official website se hi lein.",
        "\u091c\u093f\u0938 \u092b\u094b\u0928 \u0928\u0902\u092c\u0930 \u0915\u0940 \u092a\u0941\u0937\u094d\u091f\u093f \u092e\u0948\u0902 \u0928\u0939\u0940\u0902 \u0915\u0930 \u0938\u0915\u093e, \u0909\u0938\u0947 \u0939\u091f\u093e \u0926\u093f\u092f\u093e \u0939\u0948\u0964 \u0915\u093f\u0938\u0940 \u092c\u0948\u0902\u0915 \u092f\u093e \u0915\u0902\u092a\u0928\u0940 \u0915\u0940 helpline \u0905\u092a\u0928\u0947 card, passbook, account statement \u092f\u093e \u0909\u0938 \u0938\u0902\u0938\u094d\u0925\u093e \u0915\u0940 \u0938\u0930\u0915\u093e\u0930\u0940 \u0935\u0947\u092c\u0938\u093e\u0907\u091f \u0938\u0947 \u0939\u0940 \u0932\u0947\u0902\u0964",
    )


def _adoption_note(profile: Any) -> str:
    state = str(getattr(profile, "user_state", "") or "").strip()
    where_en = f" in {state}" if state else " in your State"
    where_hi = f" {state} mein" if state else " aapke State mein"
    where_dv = f" {state} \u092e\u0947\u0902" if state else " \u0906\u092a\u0915\u0947 State \u092e\u0947\u0902"
    return _copy(
        profile,
        "About the Model Tenancy Act, 2021: it is a central model law that each State must enact "
        f"for itself. I have no confirmation that it is in force{where_en}, so treat the section "
        "above as a starting point and check the equivalent provision of the tenancy or "
        "rent-control law that actually applies there before relying on it.",
        "Model Tenancy Act, 2021 ke baare mein: yeh ek central model law hai jise har State ko "
        f"khud enact karna padta hai. Mere paas iski koi pushti nahi hai ki yeh{where_hi} laagu hai, "
        "isliye upar diye gaye section ko sirf starting point maanein aur wahan actually laagu hone "
        "wale tenancy ya rent-control law ka equivalent provision check kar lein.",
        "Model Tenancy Act, 2021 \u0915\u0947 \u092c\u093e\u0930\u0947 \u092e\u0947\u0902: \u092f\u0939 \u0915\u0947\u0902\u0926\u094d\u0930\u0940\u092f model \u0915\u093e\u0928\u0942\u0928 \u0939\u0948 \u091c\u093f\u0938\u0947 \u0939\u0930 State \u0915\u094b \u0916\u0941\u0926 \u0932\u093e\u0917\u0942 \u0915\u0930\u0928\u093e \u092a\u0921\u093c\u0924\u093e \u0939\u0948\u0964 "
        f"\u092e\u0947\u0930\u0947 \u092a\u093e\u0938 \u0907\u0938\u0915\u0940 \u092a\u0941\u0937\u094d\u091f\u093f \u0928\u0939\u0940\u0902 \u0939\u0948 \u0915\u093f \u092f\u0939{where_dv} \u0932\u093e\u0917\u0942 \u0939\u0948, \u0907\u0938\u0932\u093f\u090f \u090a\u092a\u0930 \u0926\u093f\u092f\u093e \u0917\u092f\u093e \u0938\u0947\u0915\u094d\u0936\u0928 \u0915\u0947\u0935\u0932 \u0936\u0941\u0930\u0941\u0906\u0924\u0940 \u092c\u093f\u0902\u0926\u0941 \u092e\u093e\u0928\u0947\u0902 \u0914\u0930 \u0935\u0939\u093e\u0902 \u0935\u093e\u0938\u094d\u0924\u0935 \u092e\u0947\u0902 \u0932\u093e\u0917\u0942 tenancy \u092f\u093e rent-control \u0915\u093e\u0928\u0942\u0928 \u0915\u093e \u0938\u092e\u093e\u0928 \u092a\u094d\u0930\u093e\u0935\u0927\u093e\u0928 \u0926\u0947\u0916 \u0932\u0947\u0902\u0964",
    )


def _cite_rewrite(profile: Any) -> str:
    return _copy(
        profile,
        "Treat that provision as a starting point only, and confirm the equivalent provision of "
        "the State law that actually applies before citing it in a notice.",
        "Us provision ko sirf ek starting point maanein, aur notice mein uska hawala dene se pehle "
        "wahan actually laagu hone wale State law ka equivalent provision confirm kar lein.",
        "\u0909\u0938 \u092a\u094d\u0930\u093e\u0935\u0927\u093e\u0928 \u0915\u094b \u0915\u0947\u0935\u0932 \u0936\u0941\u0930\u0941\u0906\u0924\u0940 \u092c\u093f\u0902\u0926\u0941 \u092e\u093e\u0928\u0947\u0902, \u0914\u0930 \u0928\u094b\u091f\u093f\u0938 \u092e\u0947\u0902 \u0909\u0938\u0915\u093e \u0939\u0935\u093e\u0932\u093e \u0926\u0947\u0928\u0947 \u0938\u0947 \u092a\u0939\u0932\u0947 \u0935\u0939\u093e\u0902 \u0935\u093e\u0938\u094d\u0924\u0935 \u092e\u0947\u0902 \u0932\u093e\u0917\u0942 State \u0915\u093e\u0928\u0942\u0928 \u0915\u093e \u0938\u092e\u093e\u0928 \u092a\u094d\u0930\u093e\u0935\u0927\u093e\u0928 confirm \u0915\u0930 \u0932\u0947\u0902\u0964",
    )


def _authority_contact_rewrite(profile: Any) -> str:
    return _copy(
        profile,
        "(you will need to confirm the applicable rent authority or civil court yourself)",
        "(aapko applicable rent authority ya civil court khud confirm karna hoga)",
        "(\u0932\u093e\u0917\u0942 rent authority \u092f\u093e \u0938\u093f\u0935\u093f\u0932 \u0915\u094b\u0930\u094d\u091f \u0906\u092a\u0915\u094b \u0916\u0941\u0926 confirm \u0915\u0930\u0928\u093e \u0939\u094b\u0917\u093e)",
    )


def _provision_placeholder(profile: Any) -> str:
    return _copy(profile, "the applicable provision", "sambandhit provision",
                 "\u0938\u0902\u092c\u0902\u0927\u093f\u0924 \u092a\u094d\u0930\u093e\u0935\u0927\u093e\u0928")


# ------------------------------------------------------------- text structure

_BLOCK_SPLIT_RE = re.compile(r"(\n[ \t]*\n[\s]*)")
# Not after a single-letter initial: "*M.S.R. Enterprises*" must stay one sentence, or a
# later reference to a removed judgment survives split across three fragments.
_SENTENCE_SPLIT_RE = re.compile(r"(?<=[.!?\u0964])(?<![A-Z]\.)(?=\s)")
_HEADING_RE = re.compile(
    r"^[ \t]*(?:#{1,6}\s+\S|[-*+]?\s*\*\*[^*\n]+\*\*[ \t:]*|[-*+]?\s*__[^_\n]+__[ \t:]*)$"
)
_LIST_ONLY_RE = re.compile(r"^[\s>*_#\-\u2010-\u2015\u2212+.]*\d*[.)]?[\s>*_]*$")


def _blocks(text: str) -> list[str]:
    """Blank-line-separated blocks, separators kept, so a rejoin is byte-exact."""
    return _BLOCK_SPLIT_RE.split(text or "")


def _is_separator(part: str) -> bool:
    return bool(part) and _BLOCK_SPLIT_RE.fullmatch(part) is not None


def _is_heading_block(block: str) -> bool:
    lines = [line for line in (block or "").splitlines() if line.strip()]
    return len(lines) == 1 and bool(_HEADING_RE.match(lines[0]))


def _is_blank_after_edit(line: str) -> bool:
    return bool(_LIST_ONLY_RE.match(line))


def _sentences(line: str) -> list[str]:
    parts = _SENTENCE_SPLIT_RE.split(line)
    return parts if parts else [line]


def _alnum_upper(value: str) -> str:
    return re.sub(r"[^0-9A-Za-z\u0900-\u097F]", "", value or "").upper()


# ----------------------------------------------------------------- rule: D1

_PAREN_KEY_RE = re.compile(r"\s*[(\[]\s*(?P<key>[a-z][a-z0-9]*(?:_[a-z0-9]+)+)\s*[)\]]")


def _strip_fact_key_parentheticals(text: str, allow: CitationAllowlist,
                                   redactions: list[Redaction]) -> str:
    """Belt-and-braces for D1: a parenthetical whose entire content is a known fact key.

    The one enumerated list in this module that is legitimate, because fact keys are a
    closed set read live from the domain registry and the profile schema. The real fix is
    ``LLMResponseContext.model_payload``, which stops the key reaching the model at all.
    """
    def replace(match: re.Match[str]) -> str:
        if match.group("key") in allow.fact_keys:
            redactions.append(Redaction("internal_fact_key", match.group("key")))
            return ""
        return match.group(0)

    return _PAREN_KEY_RE.sub(replace, text)


# ------------------------------------------------------------ rule: case law

_CASE_PARTY = rf"[A-Z][\w&'\u2019./{DASH}]*"
CASE_NAME_RE = re.compile(
    rf"(?P<a>(?:{_CASE_PARTY}\s+){{0,6}}{_CASE_PARTY})\s+(?:v\.|vs\.|versus)\s+"
    rf"(?P<b>{_CASE_PARTY}(?:\s+(?:of\s+|the\s+)?[A-Za-z][\w&'\u2019./{DASH}]*){{0,6}})"
)
REPORTER_RE = re.compile(
    r"\(\s*(?:19|20)\d{2}\s*\)\s*\d{1,3}\s*(?:SCC|SCR|AIR|SCALE|SCJ|SCW|Bom|Del|Mad|Cal|Kar|All|Guj|Raj|Ker|Ori|Pat|P&H)\b"
    r"|\b(?:AIR|SCC|SCR|SCALE)\s+(?:19|20)\d{2}\s+(?:SC|SCC)?\s*\d{1,4}\b",
    re.IGNORECASE,
)
_HONORIFICS = ("m/s", "messrs", "mr", "mrs", "ms", "smt", "shri", "sri", "dr", "the", "in re")


def _case_law_hits(text: str) -> list[re.Match[str]]:
    return list(CASE_NAME_RE.finditer(text)) + list(REPORTER_RE.finditer(text))


def _party_needles(text: str) -> set[str]:
    """Needles for the *same* case referred to again elsewhere, derived from the reply itself.

    ``M/s. M. S. R. Enterprises`` yields ``MSRENTERPRISES`` once the honorific is dropped,
    which is what also catches the later ``*M.S.R. Enterprises*`` (no spaces). Single words
    need 12+ characters, so generic nouns like ``Enterprises`` never become a needle.
    """
    needles: set[str] = set()
    for match in CASE_NAME_RE.finditer(text):
        party = match.group("a")
        words = party.split()
        while words and words[0].rstrip(".").casefold() in _HONORIFICS:
            words = words[1:]
        joined = _alnum_upper(" ".join(words))
        if len(joined) >= 8:
            needles.add(joined)
        for word in words:
            token = _alnum_upper(word)
            if len(token) >= 12:
                needles.add(token)
    return needles


def _apply_case_law(text: str, profile: Any, redactions: list[Redaction]) -> tuple[str, bool]:
    """Remove the block a judgment citation anchors, plus any later reference to that case.

    No case-law corpus exists anywhere in the product (``app/data/`` holds exactly three
    statute files), so this rule needs no allowlist and is unconditional in every domain.
    """
    if not _case_law_hits(text):
        return text, False

    needles = _party_needles(text)
    parts = _blocks(text)
    removed_indices: set[int] = set()
    hit = False

    for index, part in enumerate(parts):
        if _is_separator(part) or not part.strip():
            continue
        if _case_law_hits(part):
            removed_indices.add(index)
            redactions.append(Redaction("case_law", "citation_block"))
            hit = True
            continue
        if not needles:
            continue
        kept_lines = []
        changed = False
        for line in part.splitlines(keepends=True):
            ending = ""
            body = line
            while body and body[-1] in "\r\n":
                ending = body[-1] + ending
                body = body[:-1]
            kept = [
                sentence for sentence in _sentences(body)
                if not any(needle in _alnum_upper(sentence) for needle in needles)
            ]
            if len(kept) != len(_sentences(body)):
                changed = True
                hit = True
                redactions.append(Redaction("case_law", "reference_sentence"))
            rebuilt = "".join(kept)
            if changed:
                # The removed sentence took the line's indent with it.
                indent = re.match(r"[ \t]*", body).group(0)
                rebuilt = indent + rebuilt.lstrip(" \t")
            if changed and _is_blank_after_edit(rebuilt):
                continue
            kept_lines.append(rebuilt + ending)
        if changed:
            rebuilt_block = "".join(kept_lines)
            parts[index] = rebuilt_block
            if not rebuilt_block.strip():
                removed_indices.add(index)

    # A heading whose content block was removed is an orphan: W1-03 t3's
    # "**Supreme Court case you can quote**" with nothing left under it.
    for index, part in enumerate(parts):
        if index in removed_indices or _is_separator(part) or not _is_heading_block(part):
            continue
        following = next(
            (j for j in range(index + 1, len(parts))
             if not _is_separator(parts[j]) and parts[j].strip()),
            None,
        )
        if following is not None and following in removed_indices:
            removed_indices.add(index)
            redactions.append(Redaction("case_law", "orphan_heading"))

    return _rejoin(parts, removed_indices, text), hit


def _rejoin(parts: list[str], removed: set[int], original: str) -> str:
    """Rebuild. Byte-identical to ``original`` when no part was removed or rewritten."""
    if not removed and "".join(parts) == original:
        return original
    out: list[str] = []
    for index, part in enumerate(parts):
        if index in removed:
            continue
        if _is_separator(part) and (not out or not out[-1].strip()):
            continue
        out.append(part)
    return re.sub(r"\n{3,}", "\n\n", "".join(out)).strip("\n")


# ------------------------------------------------------------- rule: statute

_QUOTE_LINE_RE = re.compile(
    rf"^[\s>*_{DASH}]*[{QUOTE_OPEN}].*[{QUOTE_CLOSE}][\s*_.,;:]*$", re.DOTALL
)
_SEP_BEFORE_RE = re.compile(rf"(?:\s*(?:{DASH_CLASS}|,|:|\u2014)\s*)$")


def _apply_statute(text: str, profile: Any, allow: CitationAllowlist,
                   redactions: list[Redaction]) -> tuple[str, bool, bool]:
    """Strip unverifiable section numbers; caveat when the guard cannot decide.

    Returns ``(text, needs_statute_note, cited_model_law)``.
    """
    needs_note = False
    cited_model_law = False
    parts = _blocks(text)

    for index, part in enumerate(parts):
        if _is_separator(part) or not part.strip():
            continue
        stripped_here = False
        new_part = part
        offset_shift = 0
        for match in list(CITATION_RE.finditer(part)):
            nums = match.group("nums")
            if allow.is_verified_section(nums):
                if allow.is_model_law_section(nums):
                    cited_model_law = True
                continue
            verdict = _classify(nums, part, match.start(), allow)
            if verdict == "doubtful":
                needs_note = True
                redactions.append(Redaction("statute_unconfirmed_caveated", _norm_section(nums)))
                continue
            start = match.start() + offset_shift
            end = match.end() + offset_shift
            before = new_part[:start]
            separator = _SEP_BEFORE_RE.search(before)
            if separator:
                replacement = ""
                start -= len(separator.group(0))
            else:
                replacement = _provision_placeholder(profile)
            new_part = new_part[:start] + replacement + new_part[end:]
            offset_shift += start + len(replacement) - end
            redactions.append(Redaction("statute_unverified", _norm_section(nums)))
            needs_note = True
            stripped_here = True
        if stripped_here:
            # A fabricated *verbatim quote* the user might paste into a notice is worse than
            # a section number they might mis-cite, so an adjacent quotation goes too.
            kept_lines = []
            for line in new_part.splitlines(keepends=True):
                body = line.rstrip("\r\n")
                if body.strip() and _QUOTE_LINE_RE.match(body):
                    redactions.append(Redaction("statute_quoted_text"))
                    continue
                kept_lines.append(line)
            new_part = "".join(kept_lines)
        parts[index] = new_part

    removed = {i for i, part in enumerate(parts)
               if not _is_separator(part) and part != "" and not part.strip()}
    return _rejoin(parts, removed, text), needs_note, cited_model_law


def _classify(nums: str, block: str, position: int, allow: CitationAllowlist) -> str:
    """``unverified`` (strip) or ``doubtful`` (keep + caveat). Never called for a match."""
    if not allow.corpus_available:
        # No corpus exists for this domain, so no section number in it can be verified.
        return "unverified"
    window = block[max(0, position - 110): position + 90]
    named = [_norm_act(item.group(0)) for item in ACT_NAME_RE.finditer(window)]
    named = [name for name in named if len(name) > 6]
    if not named:
        return "doubtful"
    for name in named:
        if any(name in known or known in name for known in allow.act_names):
            return "doubtful"
    # The reply names an Act that is in no allowlist for this domain.
    return "unverified"


# ---------------------------------------------------- rule: outcome probability

_OUTCOME_WORD = (
    r"(?:chances?|odds|probability|likelihood|likely\s+to\s+(?:succeed|win)|success\s+rate"
    r"|favou?rable|\bwin\b|\bwins\b|winning|jeetne|jeet|safalta"
    r"|\u0938\u0902\u092d\u093e\u0935\u0928\u093e|\u091c\u0940\u0924\u0928\u0947)"
)
_OUTCOME_WORD_RE = re.compile(_OUTCOME_WORD, re.IGNORECASE)
_PERCENT_RE = re.compile(rf"(?:{APPROX}\s*)?\d{{1,3}}(?:\.\d+)?\s*%", re.IGNORECASE)
_PERCENT_PHRASE_RE = re.compile(
    rf"(?:{APPROX}\s*)?\d{{1,3}}(?:\.\d+)?\s*%"
    rf"(?:\s*(?:chances?|odds|probability|likelihood|success\s+rate))?"
    rf"(?:\s*[,;])?",
    re.IGNORECASE,
)
_OUTCOME_LINE_RE = re.compile(
    rf"^[\s>*_{DASH}+]*(?:\d+[.)]\s*)?(?P<label>[^:\n]{{0,90}}):?[\s*_]*"
    rf"(?:{APPROX}\s*)?\d{{1,3}}(?:\.\d+)?\s*%[\s*_.)\u0964]*$",
    re.IGNORECASE,
)
_WINDOW = 70


def _apply_outcome_probability(text: str, redactions: list[Redaction]) -> tuple[str, bool]:
    """Remove a numeric win probability. Nothing in the product computes one.

    The outcome-word list is the closed side of the rule; it is what keeps this from being
    a blanket percentage ban. ``18% per annum``, ``12% GST``, ``100% refund``,
    ``2% per month`` and ``50% deposit`` sit nowhere near an outcome word.
    """
    if not _PERCENT_RE.search(text) or not _OUTCOME_WORD_RE.search(text):
        return text, False

    hit = False
    out_lines: list[str] = []
    for line in text.splitlines(keepends=True):
        body = line.rstrip("\r\n")
        ending = line[len(body):]
        if not _PERCENT_RE.search(body):
            out_lines.append(line)
            continue
        line_match = _OUTCOME_LINE_RE.match(body)
        if line_match and _OUTCOME_WORD_RE.search(line_match.group("label") or ""):
            redactions.append(Redaction("outcome_probability", "whole_line"))
            hit = True
            continue

        def replace(match: re.Match[str]) -> str:
            nonlocal hit
            start, end = match.span()
            context = body[max(0, start - _WINDOW): min(len(body), end + _WINDOW)]
            if not _OUTCOME_WORD_RE.search(context):
                return match.group(0)
            redactions.append(Redaction("outcome_probability", "inline"))
            hit = True
            return ""

        rebuilt = _PERCENT_PHRASE_RE.sub(replace, body)
        if rebuilt != body:
            rebuilt = _tidy_edited_line(rebuilt)
        if rebuilt != body and _is_blank_after_edit(rebuilt):
            continue
        out_lines.append(rebuilt + ending)
    return "".join(out_lines), hit


# ------------------------------------------------- rule: helplines + URLs + L1

_TOLLFREE_RE = re.compile(rf"\b1800[{DASH}\s]?\d[\d{DASH}\s]{{4,14}}\d\b")
# A 3-5 digit run that is not part of a longer hyphen-joined number: 1800-11-001-112
# must be handled as one toll-free token, never as three separate short codes.
_SHORTCODE_RE = re.compile(
    rf"(?<![\d,.\u20b9{DASH}])\b\d{{3,5}}\b(?![\d,.]|{DASH_CLASS}\s*\d)"
)
_HELPLINE_WORD_RE = re.compile(
    r"helpline|help\s?line|toll[\s\u2010-\u2015-]?free|\bcall\b|\bdial\b|\bhelpdesk\b"
    r"|customer\s?care|\bnumber\b|\bnumbers\b|\bhotline\b"
    r"|\u0939\u0947\u0932\u094d\u092a\u0932\u093e\u0907\u0928|\u0915\u0949\u0932|\u0928\u0902\u092c\u0930",
    re.IGNORECASE,
)
# A bare 3-5 digit run is the same shape as an order reference, an invoice number or a
# complaint id, and the words "call" and "number" are everywhere in Indian legal prose.
# So the short-code rule needs a word that means *helpline specifically*, and it stands
# down entirely near an identifier word. M7's recorded evidence is toll-free-shaped in
# both cases, so nothing in the definition of done depends on the looser reading.
_HELPLINE_ONLY_WORD_RE = re.compile(
    r"helpline|help\s?line|toll[\s\u2010-\u2015-]?free|\bhelpdesk\b|customer\s?care"
    r"|\bhotline\b|short\s?code"
    r"|\u0939\u0947\u0932\u094d\u092a\u0932\u093e\u0907\u0928",
    re.IGNORECASE,
)
_IDENTIFIER_WORD_RE = re.compile(
    r"\border\b|\breference\b|\bref\b|\binvoice\b|\bbill\b|\bcomplaint\b|\bFIR\b"
    r"|\bticket\b|\backnowledg(?:e)?ment\b|\bPIN\b|\bOTP\b|\baccount\b|\bid\b|\bcase\b"
    r"|\bpin\s?code\b|\btracking\b|\btransaction\b|\bAWB\b|\bGST\b|\bPAN\b"
    r"|\u0911\u0930\u094dडर|\u0936\u093f\u0915\u093e\u092f\u0924",
    re.IGNORECASE,
)
_AMOUNT_RE = re.compile(r"(?:\u20b9|\bRs\.?|\bINR)\s*[\d,]+|\b\d{1,3}(?:,\d{2})*,\d{3}\b")
_URL_RE = re.compile(r"https?://[^\s<>)\]\"'`]+")
_ATTRIB_CONNECTOR = rf"(?:[\s{DASH}]*(?:designated|operated|run|ki|ka|ke|dwara|by|'s|\u2019s)?[\s{DASH}]*)"


def _protected_digits(profile: Any, user_message: str) -> set[str]:
    """Digit runs that belong to the user or the case and must never be touched."""
    protected: set[str] = set()
    values: list[str] = [user_message or ""]
    for attr in ("user_phone", "transaction_id", "order_reference_id", "payment_transaction_id",
                 "shipment_tracking_id", "case_id", "opposite_party_address", "property_address"):
        value = getattr(profile, attr, None)
        if value:
            values.append(str(value))
    amount = getattr(profile, "disputed_amount", None)
    if amount:
        values.append(str(int(amount)) if float(amount).is_integer() else str(amount))
    key_facts = getattr(profile, "key_facts", None) or {}
    if isinstance(key_facts, dict):
        values.extend(str(value) for value in key_facts.values() if isinstance(value, (str, int, float)))
    for source in getattr(profile, "provided_documents", None) or []:
        if isinstance(source, dict):
            values.extend(str(value) for value in source.values() if isinstance(value, str))
    for value in values:
        for run in re.findall(r"\d[\d\s,.\u2010-\u2015-]*\d|\d", value):
            digits = re.sub(r"\D", "", run)
            if len(digits) >= 3:
                protected.add(digits)
    return protected


def _apply_helplines(text: str, profile: Any, allow: CitationAllowlist,
                     user_message: str, redactions: list[Redaction]) -> tuple[str, bool]:
    protected = _protected_digits(profile, user_message)
    hit = False

    # L1 first: correct a wrong operator on a real number, before any removal runs, so
    # "RBI 1930 helpline (1800-11-001-112)" loses RBI and then loses the invented number.
    text = _fix_operator_attribution(text, redactions)

    def drop_tollfree(match: re.Match[str]) -> str:
        nonlocal hit
        token = match.group(0)
        digits = re.sub(r"\D", "", token)
        if helpline_lookup(token) or digits in protected:
            return token
        if _AMOUNT_RE.search(body[max(0, match.start() - 8): match.end() + 2]):
            return token
        if _IDENTIFIER_WORD_RE.search(body[max(0, match.start() - 25): match.end() + 10]):
            return token
        redactions.append(Redaction("helpline_unverified", "toll_free"))
        hit = True
        return ""

    lines: list[str] = []
    for line in text.splitlines(keepends=True):
        body = line.rstrip("\r\n")
        ending = line[len(body):]
        rebuilt = _TOLLFREE_RE.sub(drop_tollfree, body)

        if _HELPLINE_ONLY_WORD_RE.search(rebuilt):
            def drop_shortcode(match: re.Match[str]) -> str:
                nonlocal hit
                token = match.group(0)
                if helpline_lookup(token) or token in protected:
                    return token
                if re.fullmatch(r"(?:19|20)\d{2}", token):  # a year, not a helpline
                    return token
                start, end = match.span()
                context = rebuilt[max(0, start - 30): min(len(rebuilt), end + 30)]
                if not _HELPLINE_ONLY_WORD_RE.search(context):
                    return token
                if _IDENTIFIER_WORD_RE.search(rebuilt[max(0, start - 25): end + 10]):
                    return token
                if _AMOUNT_RE.search(rebuilt[max(0, start - 8): end + 2]):
                    return token
                redactions.append(Redaction("helpline_unverified", "short_code"))
                hit = True
                return ""
            rebuilt = _SHORTCODE_RE.sub(drop_shortcode, rebuilt)

        def drop_url(match: re.Match[str]) -> str:
            nonlocal hit
            url = match.group(0).rstrip(".,;:")
            tail = match.group(0)[len(url):]
            if _norm_url(url) in allow.allowed_urls:
                return match.group(0)
            redactions.append(Redaction("url_off_allowlist"))
            hit = True
            return tail
        rebuilt = _URL_RE.sub(drop_url, rebuilt)

        if rebuilt != body:
            rebuilt = _cleanup_empty_brackets(rebuilt)
        if rebuilt != body and _is_blank_after_edit(rebuilt):
            continue
        lines.append(rebuilt + ending)
    return "".join(lines), hit


def _tidy_edited_line(body: str) -> str:
    """Collapse the whitespace an in-line removal left behind, keeping the line's own
    trailing whitespace: two trailing spaces are a markdown hard line break."""
    core = body.rstrip(" \t")
    tail = body[len(core):]
    core = re.sub(r"[ \t]+([,.;:)])", r"\1", core)
    core = re.sub(r"(\S)[ \t]+(\*\*|__)(?=\W|$)", r"\1\2", core)
    core = re.sub(r"[ \t]{2,}", " ", core)
    return core + tail


def _cleanup_empty_brackets(line: str) -> str:
    """Tidy a line this guard actually edited.

    Trailing whitespace is preserved: two trailing spaces are a markdown hard line
    break, and collapsing them would reflow the reply around an edit.
    """
    body = line.rstrip(" \t")
    tail = line[len(body):]
    body = re.sub(r"\(\s*[:\u2014,;]?\s*\)", "", body)
    body = re.sub(r"\[\s*\]\s*\(\s*\)", "", body)
    return _tidy_edited_line(body + tail)


def _fix_operator_attribution(text: str, redactions: list[Redaction]) -> str:
    """Drop an authority that contradicts the registry operator of an adjacent number.

    Closed and checkable: the number must be a registry number, the authority must be in
    the recognised-authority set, and the pair must be adjacent to a helpline word. This is
    not a general authority checker - RBI stays namable for the unauthorised-transaction
    liability notification, which is real RBI material (finding H9 is a different set).
    """
    authorities = sorted(ATTRIBUTABLE_AUTHORITIES, key=len, reverse=True)
    alternation = "|".join(re.escape(name) for name in authorities)
    numbers = "|".join(re.escape(entry.number) for entry in HELPLINES)
    before_re = re.compile(rf"\b(?P<auth>{alternation})\b{_ATTRIB_CONNECTOR}(?P<num>{numbers})\b")
    after_re = re.compile(rf"(?P<num>{numbers})\b(?P<mid>[^()\n]{{0,25}}?)\s*\(\s*(?P<auth>{alternation})\s*\)")

    def wrong(auth: str, number: str) -> bool:
        entry = helpline_lookup(number)
        if entry is None:
            return False
        candidates = (entry.operator,) + entry.operator_aliases
        return not any(auth.casefold() in item.casefold() or item.casefold() in auth.casefold()
                       for item in candidates)

    def fix_before(match: re.Match[str]) -> str:
        if not wrong(match.group("auth"), match.group("num")):
            return match.group(0)
        window = text[max(0, match.start() - 30): match.end() + 40]
        if not _HELPLINE_WORD_RE.search(window):
            return match.group(0)
        redactions.append(Redaction("helpline_wrong_operator", match.group("num")))
        return match.group("num")

    def fix_after(match: re.Match[str]) -> str:
        if not wrong(match.group("auth"), match.group("num")):
            return match.group(0)
        window = text[max(0, match.start() - 30): match.end() + 40]
        if not _HELPLINE_WORD_RE.search(window):
            return match.group(0)
        redactions.append(Redaction("helpline_wrong_operator", match.group("num")))
        return match.group("num") + match.group("mid")

    text = before_re.sub(fix_before, text)
    return after_re.sub(fix_after, text)


# -------------------------------------------------------- rule: H4 model law

_MODEL_LAW_CITE_VERB_RE = re.compile(
    r"\bcit(?:e|ing)\b|\bquot(?:e|ing)\b|\brefer\s+to\b|hawala|\u0939\u0935\u093e\u0932\u093e|\u0909\u0932\u094d\u0932\u0947\u0916",
    re.IGNORECASE,
)
_CONSTRUCTED_AUTHORITY_RE = re.compile(
    r"\b[A-Z][\w'\u2019]*(?:\s+[A-Z][\w'\u2019]*){0,3}\s+(?:Rent\s+Authority|Rent\s+Court|Rent\s+Tribunal)\b"
)
# Only an *online location* claim is touched. "confirm the address" is correct advice and
# W1-02 t2's praised jurisdiction reasoning must come through byte-for-byte.
_WEB_CLAIM_RE = re.compile(r"\bwebsite\b|\bweb\s?site\b|\bportal\b|\bweb\s?page\b|\bonline\s+par\b",
                           re.IGNORECASE)
_PARENTHETICAL_RE = re.compile(r"\(([^()\n]{5,300})\)")


def _apply_model_law(text: str, profile: Any, allow: CitationAllowlist,
                     redactions: list[Redaction]) -> str:
    """Neutralise 'cite this in your notice' and constructed-authority website claims.

    The provisions themselves are never deleted - they are the product's only real tenancy
    content. The caveat adds; it never removes.
    """
    out_lines: list[str] = []
    for line in text.splitlines(keepends=True):
        body = line.rstrip("\r\n")
        ending = line[len(body):]
        rebuilt = body

        # (1) a constructed "<Place> Rent Authority" plus a claim about its website
        if _CONSTRUCTED_AUTHORITY_RE.search(rebuilt) and _WEB_CLAIM_RE.search(rebuilt):
            if not _URL_RE.search(rebuilt):
                replaced = False

                def swap(match: re.Match[str]) -> str:
                    nonlocal replaced
                    if replaced or not _WEB_CLAIM_RE.search(match.group(1)):
                        return match.group(0)
                    replaced = True
                    redactions.append(Redaction("constructed_authority_contact", "website_claim"))
                    return _authority_contact_rewrite(profile)

                rebuilt = _PARENTHETICAL_RE.sub(swap, rebuilt)
                if not replaced:
                    kept = [
                        sentence for sentence in _sentences(rebuilt)
                        if not (_WEB_CLAIM_RE.search(sentence)
                                and _CONSTRUCTED_AUTHORITY_RE.search(sentence))
                    ]
                    if len(kept) != len(_sentences(rebuilt)):
                        redactions.append(Redaction("constructed_authority_contact", "sentence"))
                        rebuilt = "".join(kept)

        # (2) "cite the model-law section in your notice" -> a starting point instead
        if _MODEL_LAW_CITE_VERB_RE.search(rebuilt):
            kept: list[str] = []
            changed = False
            for sentence in _sentences(rebuilt):
                cites_model_law = any(
                    allow.is_model_law_section(match.group("nums"))
                    for match in CITATION_RE.finditer(sentence)
                )
                if cites_model_law and _MODEL_LAW_CITE_VERB_RE.search(sentence):
                    indent = re.match(rf"^[\s>*_{DASH}+]*", sentence).group(0)
                    kept.append(indent + _cite_rewrite(profile))
                    redactions.append(Redaction("model_law_cite_instruction"))
                    changed = True
                else:
                    kept.append(sentence)
            if changed:
                rebuilt = "".join(kept)

        if rebuilt != body and _is_blank_after_edit(rebuilt):
            continue
        out_lines.append(rebuilt + ending)
    return "".join(out_lines)


# ------------------------------------------------------------------- appending

def _append_once(text: str, paragraph: str, marker: str) -> str:
    """Append as a separate trailing paragraph, never twice.

    A separate paragraph so no existing ``in reply_text`` assertion is split apart, and
    marker-guarded so ``guard(guard(x)) == guard(x)`` on the retry/resume path.
    """
    if not paragraph or marker in text:
        return text
    body = text.rstrip()
    return (body + "\n\n" + paragraph) if body else paragraph


# ---------------------------------------------------------------------- entry

def guard_reply(reply: str, profile: Any, user_message: str = "") -> GuardResult:
    """Guard one chat reply. Pure: no I/O, no provider, no mutation of ``profile``."""
    if not reply or not reply.strip() or profile is None:
        return GuardResult(text=reply or "", redactions=())

    allow = build_allowlist(profile)
    redactions: list[Redaction] = []
    text = reply

    text = _strip_fact_key_parentheticals(text, allow, redactions)
    text, case_law_hit = _apply_case_law(text, profile, redactions)
    text, statute_note, cited_model_law = _apply_statute(text, profile, allow, redactions)
    text, outcome_hit = _apply_outcome_probability(text, redactions)
    text, helpline_hit = _apply_helplines(text, profile, allow, user_message, redactions)

    # H4: a model-law provision was cited and the State's adoption is not sourced.
    model_law_caveat = (
        cited_model_law
        and bool(allow.model_law_sections)
        and not adoption_confirmed(getattr(profile, "user_state", None))
    )
    if model_law_caveat:
        text = _apply_model_law(text, profile, allow, redactions)

    if case_law_hit:
        text = _append_once(text, _case_law_note(profile), _marker("case_law", profile))
    if statute_note:
        text = _append_once(text, _statute_note(profile, allow), _marker("statute", profile))
    if outcome_hit:
        text = _append_once(text, _outcome_note(profile), _marker("outcome", profile))
    if helpline_hit:
        text = _append_once(text, _helpline_note(profile), _marker("helpline", profile))
    if model_law_caveat:
        before = text
        text = _append_once(text, _adoption_note(profile), _marker("adoption", profile))
        if text != before:
            redactions.append(Redaction("model_law_adoption_caveat",
                                        str(getattr(profile, "user_state", "") or "unknown_state")))

    if text == reply:
        return GuardResult(text=reply, redactions=())
    return GuardResult(text=text, redactions=tuple(redactions))


def guard_summary(summary: str, profile: Any) -> GuardResult:
    """Same guard for the on-demand case brief.

    ``generate_case_summary`` is the one model-text surface that does not pass through
    ``_tag_response``, and ``main.py`` persists its output into ``profile.ai_summary_cache``,
    so an unguarded fabrication there is served forever. Guarded here, inside the function
    that produces it, so the cache stores the guarded text.
    """
    return guard_reply(summary, profile)
