import os
import json
import re
from dataclasses import dataclass, field
from typing import List, Dict, Any, Optional, Tuple
from app.config import settings
from app.schemas.case import StatutoryCitation


# Case categories are product-level; corpus keys are retrieval-level. Mapping
# lives here so callers never have to know how the corpus is filed.
CATEGORY_TO_CORPUS = {
    "HOUSING_TENANT": "TENANCY",
    "TENANCY": "TENANCY",
    "CONSUMER": "CONSUMER",
    "RTI": "RTI",
}

# Words that carry no retrieval signal. Without this filter a single "the" or
# "of" in a provision matches every query and collapses ranking to file order.
STOPWORDS = frozenset({
    "a", "an", "and", "the", "of", "or", "to", "in", "on", "for", "is", "are",
    "was", "were", "be", "been", "by", "with", "at", "from", "as", "that",
    "this", "it", "its", "has", "have", "had", "not", "no", "yes", "my", "me",
    "i", "you", "your", "he", "she", "they", "we", "his", "her", "their",
    "any", "all", "can", "will", "would", "should", "may", "if", "so", "than",
    "then", "there", "when", "which", "who", "what", "how", "about", "after",
    "before", "into", "over", "under", "up", "out", "do", "did", "does", "done",
})

_TOKEN_RE = re.compile(r"[a-z0-9]+")


def _tokenize(text: str) -> set:
    """Lowercase word tokens with stopwords and 1-character noise removed."""
    if not text:
        return set()
    return {
        token for token in _TOKEN_RE.findall(text.lower())
        if len(token) > 2 and token not in STOPWORDS
    }


@dataclass(frozen=True)
class RagQueryContext:
    """Structured, PII-free retrieval input.

    Built by the conversation layer from the persisted case profile, so that
    retrieval reflects the whole case rather than only the newest utterance.
    Every field here is either a controlled vocabulary value (category, stage,
    evidence id) or a jurisdiction name - never a person, address or account.
    """

    category: str
    issue_type: Optional[str] = None
    state: Optional[str] = None
    city: Optional[str] = None
    workflow_stage: Optional[str] = None
    facts: Tuple[str, ...] = field(default_factory=tuple)
    latest_message: str = ""
    low_context: bool = False

    def to_query_text(self) -> str:
        """Deterministic, compact, human-readable query string.

        Stable across turns for the same case state, which is what keeps
        retrieval from swinging as the user sends short replies.
        """
        parts: List[str] = []
        if self.category:
            parts.append(self.category.replace("_", " ").title())
        if self.issue_type:
            parts.append(self.issue_type.replace("_", " "))
        if self.state:
            parts.append(self.state)
        # City is included only when it is the sole jurisdiction signal; with a
        # State present it adds no legal reach and only narrows the query.
        if self.city and not self.state:
            parts.append(self.city)
        if self.workflow_stage:
            parts.append("stage " + self.workflow_stage.replace("_", " ").lower())
        if self.facts:
            parts.append(", ".join(fact.replace("_", " ") for fact in self.facts))
        # A terse turn ("Yes, both") must never become the retrieval query.
        if self.latest_message and not self.low_context:
            parts.append(self.latest_message.strip())
        return ". ".join(part for part in parts if part)


class StatutoryRAG:
    """
    Curated RAG Retriever for verified Indian Statutory laws and sections.
    Ensures 0% citation hallucination by retrieving from ground-truth Indian Acts.
    """

    def __init__(self):
        self.corpus: Dict[str, List[Dict[str, Any]]] = {}
        self._load_corpus()

    def _load_corpus(self):
        corpus_files = {
            "CONSUMER": "consumer_protection_act_2019.json",
            "TENANCY": "model_tenancy_provisions.json",
            "RTI": "rti_act_2005.json"
        }
        for category, filename in corpus_files.items():
            path = os.path.join(settings.DATA_DIR, filename)
            if os.path.exists(path):
                with open(path, "r", encoding="utf-8") as f:
                    self.corpus[category] = json.load(f)
                source_metadata = {
                    "CONSUMER": {
                        "source_url": "https://www.indiacode.nic.in/handle/123456789/21423",
                        "source_authority": "India Code",
                        "effective_from": "2020-07-20",
                        "effective_to": None,
                        "document_type": "Central Act",
                    },
                    "TENANCY": {
                        "source_url": "https://mohua.gov.in/upload/uploadfiles/files/Model-Tenancy-Act-English-02_06_2021.pdf",
                        "source_authority": "Ministry of Housing and Urban Affairs",
                        "effective_from": "2021-06-02",
                        "effective_to": None,
                        "document_type": "Model law - State adoption must be verified",
                    },
                    "RTI": {
                        "source_url": "https://www.indiacode.nic.in/handle/123456789/2065",
                        "source_authority": "India Code",
                        "effective_from": "2005-10-12",
                        "effective_to": None,
                        "document_type": "Central Act",
                    },
                }.get(category, {})
                for item in self.corpus[category]:
                    item.update(source_metadata)
            else:
                self.corpus[category] = []

    def _citation(self, item: Dict[str, Any], relevance_reason: str) -> StatutoryCitation:
        return StatutoryCitation(
            section=item["section"],
            act=item["act"],
            title=item["title"],
            description=item["description"],
            relevance_reason=relevance_reason,
            source_url=item.get("source_url"),
            source_authority=item.get("source_authority"),
            effective_from=item.get("effective_from"),
            effective_to=item.get("effective_to"),
            document_type=item.get("document_type"),
        )

    def retrieve_for_context(
        self,
        context: RagQueryContext,
        limit: int = 4,
    ) -> List[StatutoryCitation]:
        """Rank provisions against structured case context, not a raw utterance.

        Terms are weighted by how much legal signal they carry: the issue type
        identifies the grievance, the category and jurisdiction bound the body of
        law, and the newest message only refines an already-established case. On
        a terse turn the message contributes nothing, so retrieval stays anchored
        to the case rather than collapsing to whatever the user just typed.
        """
        corpus_key = CATEGORY_TO_CORPUS.get((context.category or "").upper())
        items = self.corpus.get(corpus_key, []) if corpus_key else []
        if not items:
            # Same contract as the legacy path: never substitute an unrelated
            # statute for a category the curated corpus does not cover.
            return []

        weighted_terms: Dict[str, float] = {}

        def add(text: Optional[str], weight: float) -> None:
            for token in _tokenize(text or ""):
                weighted_terms[token] = max(weighted_terms.get(token, 0.0), weight)

        add(context.issue_type, 3.0)
        add(context.category, 2.0)
        for fact in context.facts:
            add(fact, 1.5)
        add(context.workflow_stage, 1.0)
        add(context.state, 1.0)
        if not context.low_context:
            add(context.latest_message, 2.0)

        scored: List[Tuple[float, int, StatutoryCitation]] = []
        for index, item in enumerate(items):
            haystack = " ".join([
                item.get("title", ""),
                item.get("description", ""),
                " ".join(item.get("applicable_situations", []) or []),
            ])
            tokens = _tokenize(haystack)
            score = sum(weight for term, weight in weighted_terms.items() if term in tokens)

            title_lower = item.get("title", "").lower()
            is_baseline = "jurisdiction" in title_lower or "limitation" in title_lower
            if is_baseline:
                # Forum and limitation provisions stay reachable for any case in
                # the category, but must not outrank a substantive match.
                score = max(score, 0.5)
                reason = "Procedural grounding for this dispute category; applicability depends on the facts."
            else:
                reason = (
                    f"Matched the case context: {context.issue_type or context.category}."
                    if score > 0 else
                    "Baseline source for this dispute category; applicability still depends on the facts."
                )

            if score > 0:
                scored.append((score, index, self._citation(item, reason)))

        if not scored:
            # Category is covered but nothing matched: fall back to file order so
            # the model still receives verified grounding rather than nothing.
            return [
                self._citation(item, "Baseline source for this dispute category; applicability still depends on the facts.")
                for item in items[:min(limit, 3)]
            ]

        # Descending score, then original corpus order for deterministic ties.
        scored.sort(key=lambda row: (-row[0], row[1]))
        return [citation for _, _, citation in scored[:limit]]

    def retrieve_applicable_sections(self, category: str, narrative: str, fact_keywords: List[str] = None) -> List[StatutoryCitation]:
        category_key = category.upper()
        if category_key not in self.corpus:
            # Never substitute an unrelated statute when the curated corpus does
            # not cover the case category. The response layer can still use the
            # category's verified official source metadata.
            return []

        items = self.corpus.get(category_key, [])
        results: List[StatutoryCitation] = []
        narrative_lower = narrative.lower()

        for item in items:
            # Match keywords or applicable situations
            relevance_score = 0
            for situation in item.get("applicable_situations", []):
                if any(word in narrative_lower for word in situation.lower().split()):
                    relevance_score += 1

            # Check title match
            if any(word in narrative_lower for word in item.get("title", "").lower().split()):
                relevance_score += 2

            # If relevant or essential base jurisdiction section, include
            if relevance_score > 0 or "jurisdiction" in item.get("title", "").lower() or "limitation" in item.get("title", "").lower():
                results.append(StatutoryCitation(
                    section=item["section"],
                    act=item["act"],
                    title=item["title"],
                    description=item["description"],
                    relevance_reason=f"Potentially relevant to the reported grievance: {item.get('applicable_situations', ['General statutory grounding'])[0]}",
                    source_url=item.get("source_url"),
                    source_authority=item.get("source_authority"),
                    effective_from=item.get("effective_from"),
                    effective_to=item.get("effective_to"),
                    document_type=item.get("document_type"),
                ))

        # If nothing matched specifically, return the core primary sections for the category
        if not results and items:
            for item in items[:3]:
                results.append(StatutoryCitation(
                    section=item["section"],
                    act=item["act"],
                    title=item["title"],
                    description=item["description"],
                    relevance_reason="Baseline source for this dispute category; applicability still depends on the facts.",
                    source_url=item.get("source_url"),
                    source_authority=item.get("source_authority"),
                    effective_from=item.get("effective_from"),
                    effective_to=item.get("effective_to"),
                    document_type=item.get("document_type"),
                ))

        return results

statutory_rag = StatutoryRAG()
