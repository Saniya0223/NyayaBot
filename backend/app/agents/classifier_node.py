import re
from typing import Dict, Any, Tuple

class IssueClassifier:
    """
    Classifies the legal domain and evaluates severity for lawyer triage / escalation guardrails.
    """

    CRITICAL_CRIMINAL_KEYWORDS = [
        "murder", "assault", "rape", "domestic violence", "kidnap", "arrest", "fir",
        "bail", "custody", "dowry", "physical violence", "police harassment", "extortion",
        "human trafficking", "narcotics", "suicide"
    ]

    CONSUMER_KEYWORDS = [
        "product", "defect", "amazon", "flipkart", "warranty", "refund", "seller",
        "ecommerce", "refrigerator", "laptop", "mobile", "flight cancellation", "deficiency",
        "service", "hotel booking", "appliance", "car service", "insurance claim"
    ]

    TENANCY_KEYWORDS = [
        "landlord", "tenant", "rent", "security deposit", "lease", "evict", "eviction",
        "flat", "apartment", "house owner", "brokerage", "maintenance charges", "tenancy agreement"
    ]

    RTI_KEYWORDS = [
        "rti", "information", "pio", "cpio", "first appeal", "public authority", "tender document",
        "government record", "inspection", "marksheet verification", "pension delay record"
    ]

    @classmethod
    def classify_and_evaluate(cls, narrative: str) -> Dict[str, Any]:
        text = narrative.lower()

        # 1. Check for serious criminal or high-risk matters requiring urgent human lawyer escalation
        for word in cls.CRITICAL_CRIMINAL_KEYWORDS:
            if re.search(r'\b' + re.escape(word) + r'\b', text):
                return {
                    "category": "CRIMINAL_SERIOUS",
                    "severity_level": "ESCALATED_LAWYER",
                    "can_self_serve": False,
                    "escalation_reason": f"Matter involves critical criminal / personal safety allegations ({word}). Under the Advocates Act, 1961, self-service automated tools cannot substitute for professional criminal defense counsel. Immediate consultation with an advocate or police assistance is required.",
                    "recommended_action": "Seek immediate legal counsel from a practicing advocate or District Legal Services Authority (DLSA / NALSA Free Legal Aid at 15100)."
                }

        # 2. Check Tenancy
        tenancy_score = sum(1 for kw in cls.TENANCY_KEYWORDS if kw in text)
        consumer_score = sum(1 for kw in cls.CONSUMER_KEYWORDS if kw in text)
        rti_score = sum(1 for kw in cls.RTI_KEYWORDS if kw in text)

        scores = {
            "CONSUMER": consumer_score,
            "TENANCY": tenancy_score,
            "RTI": rti_score
        }

        best_category = max(scores, key=scores.get)
        if scores[best_category] == 0:
            best_category = "CONSUMER"  # Default general civil consumer dispute

        return {
            "category": best_category,
            "severity_level": "STANDARD",
            "can_self_serve": True,
            "escalation_reason": None,
            "recommended_action": "Self-service resolution via legal notice and statutory tribunal filing."
        }
