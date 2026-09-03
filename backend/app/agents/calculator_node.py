from datetime import datetime, timedelta
from typing import Dict, Any, Tuple, Optional

class LegalCalculator:
    """
    Deterministic Legal Jurisdiction & Limitation Period Calculation Engine for India.
    Prevents LLM hallucinations on numbers, dates, and forum limits.
    """

    @classmethod
    def calculate_consumer_jurisdiction(cls, claim_amount: float, city: str = "Local District") -> Dict[str, Any]:
        """
        Calculates consumer forum jurisdiction under Section 34, 47, 58 of Consumer Protection Act 2019.
        """
        if claim_amount <= 5000000:  # <= 50 Lakhs
            forum = f"District Consumer Disputes Redressal Commission (DCDRC), {city}"
            tier = "DISTRICT"
            description = "Claims up to ₹50 Lakhs fall within District Commission jurisdiction (Sec 34 CPA 2019)."
            court_fee = "₹0 (for claims up to ₹5 Lakhs) / Nominal ₹200-₹500 (up to ₹50 Lakhs)"
        elif claim_amount <= 20000000:  # 50 Lakhs to 2 Crores
            forum = "State Consumer Disputes Redressal Commission (SCDRC)"
            tier = "STATE"
            description = "Claims exceeding ₹50 Lakhs but up to ₹2 Crores fall within State Commission jurisdiction (Sec 47 CPA 2019)."
            court_fee = "₹2,000 - ₹5,000"
        else:
            forum = "National Consumer Disputes Redressal Commission (NCDRC), New Delhi"
            tier = "NATIONAL"
            description = "Claims exceeding ₹2 Crores fall directly within National Commission jurisdiction (Sec 58 CPA 2019)."
            court_fee = "₹7,500"

        return {
            "appropriate_forum": forum,
            "tier": tier,
            "description": description,
            "court_fee_estimate": court_fee
        }

    @classmethod
    def calculate_tenancy_jurisdiction(cls, city: str = "Local Area") -> Dict[str, Any]:
        return {
            "appropriate_forum": f"Rent Authority & Rent Court / Civil Court of {city}",
            "tier": "DISTRICT_RENT_TRIBUNAL",
            "description": "Tenancy disputes regarding security deposit refund and eviction fall under the local Rent Authority / Civil Judge Senior Division.",
            "court_fee_estimate": "Ad-valorem court fees or nominal tribunal application fee"
        }

    @classmethod
    def calculate_rti_jurisdiction(cls, public_authority: str = "Concerned Public Authority") -> Dict[str, Any]:
        return {
            "appropriate_forum": f"Central / State Public Information Officer (PIO), {public_authority}",
            "tier": "PUBLIC_AUTHORITY_PIO",
            "description": "Original application is submitted to the CPIO/SPIO under Section 6(1) of RTI Act 2005.",
            "court_fee_estimate": "₹10 (Central Govt / Most States) via IPO/Court Fee Stamp/Online Payment"
        }

    @classmethod
    def calculate_limitation_period(
        cls, category: str, cause_of_action_str: Optional[str]
    ) -> Tuple[Optional[str], Optional[int], str]:
        """
        Calculates statutory limitation deadline and remaining days.
        Returns: (deadline_date_str, days_remaining, alert_status)
        """
        if not cause_of_action_str:
            return None, None, "UNKNOWN_DATE"

        parsed_date = cls._parse_flexible_date(cause_of_action_str)
        if not parsed_date:
            return None, None, "INVALID_DATE_FORMAT"

        today = datetime.now().date()

        if category.upper() == "CONSUMER":
            # 2 years under Section 69 of CPA 2019
            deadline = parsed_date + timedelta(days=730)
            limitation_rule = "2 Years from date of cause of action (Section 69, Consumer Protection Act 2019)"
        elif category.upper() == "TENANCY":
            # 3 years for civil recovery under Limitation Act / 30 days notice
            deadline = parsed_date + timedelta(days=1095)
            limitation_rule = "3 Years for civil money recovery of security deposit from date of vacation"
        elif category.upper() == "RTI":
            # RTI has no limitation for original request, but First Appeal is 30 days
            deadline = parsed_date + timedelta(days=30)
            limitation_rule = "30 Days response window for PIO (Section 7(1) RTI Act 2005)"
        else:
            deadline = parsed_date + timedelta(days=730)
            limitation_rule = "General 2-3 years statutory civil limitation"

        days_remaining = (deadline - today).days

        if days_remaining < 0:
            status = "EXPIRED"
        elif days_remaining <= 30:
            status = "CRITICAL_URGENT"
        elif days_remaining <= 90:
            status = "WARNING"
        else:
            status = "SAFE"

        return deadline.strftime("%d-%m-%Y"), days_remaining, status

    @classmethod
    def _parse_flexible_date(cls, date_str: str) -> Optional[datetime.date]:
        date_str = date_str.strip()
        formats = [
            "%Y-%m-%d", "%d-%m-%Y", "%d/%m/%Y", "%Y/%m/%d",
            "%d %b %Y", "%d %B %Y", "%b %d, %Y", "%B %d, %Y"
        ]
        for fmt in formats:
            try:
                return datetime.strptime(date_str, fmt).date()
            except ValueError:
                continue
        return None
