import os
import json
from typing import List, Dict, Any
from app.config import settings
from app.schemas.case import StatutoryCitation

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
