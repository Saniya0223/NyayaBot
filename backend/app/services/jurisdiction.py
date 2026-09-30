"""Deterministic Indian jurisdiction reference data (city / State).

Why a lookup table is the right mechanism *here*, when it was the wrong one for
threat vocabulary in cycle 2: this is reference data with a safe default, not a
semantic classifier. A missing entry fails **closed and silent** - ``user_state``
simply stays ``None``, exactly as it does today, and nothing downstream asserts a
wrong forum. A missing threat verb failed **open and dangerous**. Indian place
names are still an open class, so this table is deliberately allowed to be
incomplete and must never guess a State for a city it does not know.

Storage is canonical Latin so document templates, RAG filters and the portal
selector keep seeing the strings they already handle, even when the user wrote
Devanagari.
"""

from __future__ import annotations

import re
from typing import Optional

# --------------------------------------------------------------- States / UTs

# canonical State name -> recognised surface forms (Latin variants + Devanagari)
STATE_VARIANTS: dict[str, tuple[str, ...]] = {
    "Andhra Pradesh": ("Andhra Pradesh", "आंध्र प्रदेश", "आन्ध्र प्रदेश"),
    "Arunachal Pradesh": ("Arunachal Pradesh", "अरुणाचल प्रदेश"),
    "Assam": ("Assam", "असम"),
    "Bihar": ("Bihar", "बिहार"),
    "Chhattisgarh": ("Chhattisgarh", "Chattisgarh", "छत्तीसगढ़"),
    "Goa": ("Goa", "गोवा"),
    "Gujarat": ("Gujarat", "गुजरात"),
    "Haryana": ("Haryana", "हरियाणा"),
    "Himachal Pradesh": ("Himachal Pradesh", "हिमाचल प्रदेश"),
    "Jharkhand": ("Jharkhand", "झारखंड", "झारखण्ड"),
    "Karnataka": ("Karnataka", "कर्नाटक"),
    "Kerala": ("Kerala", "केरल"),
    "Madhya Pradesh": ("Madhya Pradesh", "मध्य प्रदेश"),
    "Maharashtra": ("Maharashtra", "महाराष्ट्र"),
    "Manipur": ("Manipur", "मणिपुर"),
    "Meghalaya": ("Meghalaya", "मेघालय"),
    "Mizoram": ("Mizoram", "मिजोरम"),
    "Nagaland": ("Nagaland", "नागालैंड"),
    "Odisha": ("Odisha", "Orissa", "ओडिशा", "उड़ीसा"),
    "Punjab": ("Punjab", "पंजाब"),
    "Rajasthan": ("Rajasthan", "राजस्थान"),
    "Sikkim": ("Sikkim", "सिक्किम"),
    "Tamil Nadu": ("Tamil Nadu", "Tamilnadu", "तमिलनाडु", "तमिल नाडु"),
    "Telangana": ("Telangana", "तेलंगाना"),
    "Tripura": ("Tripura", "त्रिपुरा"),
    "Uttar Pradesh": ("Uttar Pradesh", "उत्तर प्रदेश"),
    "Uttarakhand": ("Uttarakhand", "Uttaranchal", "उत्तराखंड"),
    "West Bengal": ("West Bengal", "पश्चिम बंगाल"),
    "Andaman and Nicobar Islands": ("Andaman and Nicobar Islands", "Andaman & Nicobar", "अंडमान और निकोबार"),
    "Chandigarh": ("Chandigarh", "चंडीगढ़"),
    "Dadra and Nagar Haveli and Daman and Diu": ("Dadra and Nagar Haveli", "Daman and Diu", "दादरा और नगर हवेली"),
    "Delhi": ("Delhi", "NCT of Delhi", "New Delhi", "दिल्ली", "नई दिल्ली"),
    "Jammu and Kashmir": ("Jammu and Kashmir", "Jammu & Kashmir", "जम्मू और कश्मीर", "जम्मू कश्मीर"),
    "Ladakh": ("Ladakh", "लद्दाख"),
    "Lakshadweep": ("Lakshadweep", "लक्षद्वीप"),
    "Puducherry": ("Puducherry", "Pondicherry", "पुदुचेरी", "पांडिचेरी"),
}

STATES: tuple[str, ...] = tuple(STATE_VARIANTS)

# ---------------------------------------------------------------------- Cities

# canonical city -> (State, recognised surface forms). Roughly the State
# capitals, the million-plus cities, the NCR belt and the towns that recur in
# tenancy/consumer/cyber matters. Alternate romanisations and Devanagari forms
# are listed because users write both.
_CITY_TABLE: tuple[tuple[str, str, tuple[str, ...]], ...] = (
    ("New Delhi", "Delhi", ("New Delhi", "नई दिल्ली")),
    ("Delhi", "Delhi", ("Delhi", "दिल्ली")),
    ("Noida", "Uttar Pradesh", ("Noida", "Greater Noida", "नोएडा", "ग्रेटर नोएडा")),
    ("Ghaziabad", "Uttar Pradesh", ("Ghaziabad", "गाज़ियाबाद", "गाजियाबाद")),
    ("Gurugram", "Haryana", ("Gurugram", "Gurgaon", "गुरुग्राम", "गुड़गांव", "गुड़गाँव")),
    ("Faridabad", "Haryana", ("Faridabad", "फरीदाबाद")),
    ("Sonipat", "Haryana", ("Sonipat", "Sonepat", "सोनीपत")),
    ("Panipat", "Haryana", ("Panipat", "पानीपत")),
    ("Hisar", "Haryana", ("Hisar", "हिसार")),
    ("Karnal", "Haryana", ("Karnal", "करनाल")),
    ("Ambala", "Haryana", ("Ambala", "अंबाला")),
    ("Rohtak", "Haryana", ("Rohtak", "रोहतक")),
    ("Chandigarh", "Chandigarh", ("Chandigarh", "चंडीगढ़")),
    ("Mohali", "Punjab", ("Mohali", "मोहाली")),
    ("Ludhiana", "Punjab", ("Ludhiana", "लुधियाना")),
    ("Amritsar", "Punjab", ("Amritsar", "अमृतसर")),
    ("Jalandhar", "Punjab", ("Jalandhar", "जालंधर")),
    ("Patiala", "Punjab", ("Patiala", "पटियाला")),
    ("Bathinda", "Punjab", ("Bathinda", "बठिंडा")),
    ("Shimla", "Himachal Pradesh", ("Shimla", "शिमला")),
    ("Dharamshala", "Himachal Pradesh", ("Dharamshala", "धर्मशाला")),
    ("Dehradun", "Uttarakhand", ("Dehradun", "देहरादून")),
    ("Haridwar", "Uttarakhand", ("Haridwar", "हरिद्वार")),
    ("Roorkee", "Uttarakhand", ("Roorkee", "रुड़की")),
    ("Haldwani", "Uttarakhand", ("Haldwani", "हल्द्वानी")),
    ("Srinagar", "Jammu and Kashmir", ("Srinagar", "श्रीनगर")),
    ("Jammu", "Jammu and Kashmir", ("Jammu", "जम्मू")),
    ("Leh", "Ladakh", ("Leh", "लेह")),
    ("Jaipur", "Rajasthan", ("Jaipur", "जयपुर")),
    ("Jodhpur", "Rajasthan", ("Jodhpur", "जोधपुर")),
    ("Udaipur", "Rajasthan", ("Udaipur", "उदयपुर")),
    ("Kota", "Rajasthan", ("Kota", "कोटा")),
    ("Ajmer", "Rajasthan", ("Ajmer", "अजमेर")),
    ("Bikaner", "Rajasthan", ("Bikaner", "बीकानेर")),
    ("Alwar", "Rajasthan", ("Alwar", "अलवर")),
    ("Bhilwara", "Rajasthan", ("Bhilwara", "भीलवाड़ा")),
    ("Lucknow", "Uttar Pradesh", ("Lucknow", "लखनऊ")),
    ("Kanpur", "Uttar Pradesh", ("Kanpur", "कानपुर")),
    ("Varanasi", "Uttar Pradesh", ("Varanasi", "Banaras", "Benares", "वाराणसी", "बनारस")),
    ("Prayagraj", "Uttar Pradesh", ("Prayagraj", "Allahabad", "प्रयागराज", "इलाहाबाद")),
    ("Agra", "Uttar Pradesh", ("Agra", "आगरा")),
    ("Meerut", "Uttar Pradesh", ("Meerut", "मेरठ")),
    ("Bareilly", "Uttar Pradesh", ("Bareilly", "बरेली")),
    ("Aligarh", "Uttar Pradesh", ("Aligarh", "अलीगढ़")),
    ("Moradabad", "Uttar Pradesh", ("Moradabad", "मुरादाबाद")),
    ("Gorakhpur", "Uttar Pradesh", ("Gorakhpur", "गोरखपुर")),
    ("Jhansi", "Uttar Pradesh", ("Jhansi", "झांसी")),
    ("Mathura", "Uttar Pradesh", ("Mathura", "मथुरा")),
    ("Ayodhya", "Uttar Pradesh", ("Ayodhya", "अयोध्या")),
    ("Patna", "Bihar", ("Patna", "पटना")),
    # Gaya (Bihar) and Sagar (MP) are deliberately absent: "gaya" is an
    # extremely common Hinglish verb ("paise kat gaya") and "Sagar" is a common
    # personal name. A false positive here would assert a wrong forum, which is
    # worse than the silent miss this table is designed to degrade to.
    ("Muzaffarpur", "Bihar", ("Muzaffarpur", "मुजफ्फरपुर")),
    ("Bhagalpur", "Bihar", ("Bhagalpur", "भागलपुर")),
    ("Darbhanga", "Bihar", ("Darbhanga", "दरभंगा")),
    ("Ranchi", "Jharkhand", ("Ranchi", "रांची")),
    ("Jamshedpur", "Jharkhand", ("Jamshedpur", "जमशेदपुर")),
    ("Dhanbad", "Jharkhand", ("Dhanbad", "धनबाद")),
    ("Bokaro", "Jharkhand", ("Bokaro", "बोकारो")),
    ("Kolkata", "West Bengal", ("Kolkata", "Calcutta", "कोलकाता", "कलकत्ता")),
    ("Howrah", "West Bengal", ("Howrah", "हावड़ा")),
    ("Siliguri", "West Bengal", ("Siliguri", "सिलीगुड़ी")),
    ("Durgapur", "West Bengal", ("Durgapur", "दुर्गापुर")),
    ("Asansol", "West Bengal", ("Asansol", "आसनसोल")),
    ("Bhubaneswar", "Odisha", ("Bhubaneswar", "भुवनेश्वर")),
    ("Cuttack", "Odisha", ("Cuttack", "कटक")),
    ("Rourkela", "Odisha", ("Rourkela", "राउरकेला")),
    ("Guwahati", "Assam", ("Guwahati", "गुवाहाटी")),
    ("Dibrugarh", "Assam", ("Dibrugarh", "डिब्रूगढ़")),
    ("Silchar", "Assam", ("Silchar", "सिलचर")),
    ("Shillong", "Meghalaya", ("Shillong", "शिलांग")),
    ("Imphal", "Manipur", ("Imphal", "इंफाल")),
    ("Aizawl", "Mizoram", ("Aizawl", "आइजोल")),
    ("Kohima", "Nagaland", ("Kohima", "कोहिमा")),
    ("Dimapur", "Nagaland", ("Dimapur", "दीमापुर")),
    ("Agartala", "Tripura", ("Agartala", "अगरतला")),
    ("Itanagar", "Arunachal Pradesh", ("Itanagar", "ईटानगर")),
    ("Gangtok", "Sikkim", ("Gangtok", "गंगटोक")),
    ("Mumbai", "Maharashtra", ("Mumbai", "Bombay", "मुंबई", "मुम्बई", "बंबई")),
    ("Navi Mumbai", "Maharashtra", ("Navi Mumbai", "नवी मुंबई")),
    ("Thane", "Maharashtra", ("Thane", "ठाणे")),
    ("Pune", "Maharashtra", ("Pune", "Poona", "पुणे", "पूना")),
    ("Nagpur", "Maharashtra", ("Nagpur", "नागपुर")),
    ("Nashik", "Maharashtra", ("Nashik", "Nasik", "नाशिक")),
    ("Aurangabad", "Maharashtra", ("Aurangabad", "Chhatrapati Sambhajinagar", "औरंगाबाद")),
    ("Kolhapur", "Maharashtra", ("Kolhapur", "कोल्हापुर")),
    ("Solapur", "Maharashtra", ("Solapur", "सोलापुर")),
    ("Amravati", "Maharashtra", ("Amravati", "अमरावती")),
    ("Ahmedabad", "Gujarat", ("Ahmedabad", "Amdavad", "अहमदाबाद")),
    ("Surat", "Gujarat", ("Surat", "सूरत")),
    ("Vadodara", "Gujarat", ("Vadodara", "Baroda", "वडोदरा", "बड़ौदा")),
    ("Rajkot", "Gujarat", ("Rajkot", "राजकोट")),
    ("Bhavnagar", "Gujarat", ("Bhavnagar", "भावनगर")),
    ("Jamnagar", "Gujarat", ("Jamnagar", "जामनगर")),
    ("Gandhinagar", "Gujarat", ("Gandhinagar", "गांधीनगर")),
    ("Bhopal", "Madhya Pradesh", ("Bhopal", "भोपाल")),
    ("Indore", "Madhya Pradesh", ("Indore", "इंदौर")),
    ("Jabalpur", "Madhya Pradesh", ("Jabalpur", "जबलपुर")),
    ("Gwalior", "Madhya Pradesh", ("Gwalior", "ग्वालियर")),
    ("Ujjain", "Madhya Pradesh", ("Ujjain", "उज्जैन")),
    ("Raipur", "Chhattisgarh", ("Raipur", "रायपुर")),
    ("Bhilai", "Chhattisgarh", ("Bhilai", "भिलाई")),
    ("Bilaspur", "Chhattisgarh", ("Bilaspur", "बिलासपुर")),
    ("Bengaluru", "Karnataka", ("Bengaluru", "Bangalore", "बेंगलुरु", "बैंगलोर", "बंगलौर")),
    ("Mysuru", "Karnataka", ("Mysuru", "Mysore", "मैसूर")),
    ("Mangaluru", "Karnataka", ("Mangaluru", "Mangalore", "मंगलुरु")),
    ("Hubballi", "Karnataka", ("Hubballi", "Hubli", "हुबली")),
    ("Belagavi", "Karnataka", ("Belagavi", "Belgaum", "बेलगाम")),
    ("Hyderabad", "Telangana", ("Hyderabad", "हैदराबाद")),
    ("Warangal", "Telangana", ("Warangal", "वारंगल")),
    ("Secunderabad", "Telangana", ("Secunderabad", "सिकंदराबाद")),
    ("Visakhapatnam", "Andhra Pradesh", ("Visakhapatnam", "Vizag", "विशाखापत्तनम")),
    ("Vijayawada", "Andhra Pradesh", ("Vijayawada", "विजयवाड़ा")),
    ("Guntur", "Andhra Pradesh", ("Guntur", "गुंटूर")),
    ("Tirupati", "Andhra Pradesh", ("Tirupati", "तिरुपति")),
    ("Amaravati", "Andhra Pradesh", ("Amaravati",)),
    ("Chennai", "Tamil Nadu", ("Chennai", "Madras", "चेन्नई", "मद्रास")),
    ("Coimbatore", "Tamil Nadu", ("Coimbatore", "कोयंबटूर")),
    ("Madurai", "Tamil Nadu", ("Madurai", "मदुरै")),
    ("Tiruchirappalli", "Tamil Nadu", ("Tiruchirappalli", "Trichy", "तिरुचिरापल्ली")),
    ("Salem", "Tamil Nadu", ("Salem", "सेलम")),
    ("Tirunelveli", "Tamil Nadu", ("Tirunelveli",)),
    ("Kochi", "Kerala", ("Kochi", "Cochin", "Ernakulam", "कोच्चि", "कोचीन")),
    ("Thiruvananthapuram", "Kerala", ("Thiruvananthapuram", "Trivandrum", "तिरुवनंतपुरम")),
    ("Kozhikode", "Kerala", ("Kozhikode", "Calicut", "कोझिकोड")),
    ("Thrissur", "Kerala", ("Thrissur", "त्रिशूर")),
    ("Kollam", "Kerala", ("Kollam", "कोल्लम")),
    ("Panaji", "Goa", ("Panaji", "Panjim", "पणजी")),
    ("Margao", "Goa", ("Margao", "मडगांव")),
    ("Puducherry", "Puducherry", ("Puducherry", "Pondicherry", "पुदुचेरी")),
    ("Port Blair", "Andaman and Nicobar Islands", ("Port Blair", "पोर्ट ब्लेयर")),
)

CITY_STATE: dict[str, str] = {city: state for city, state, _ in _CITY_TABLE}

# surface form (casefolded) -> canonical city
_CITY_LOOKUP: dict[str, str] = {}
for _city, _state, _forms in _CITY_TABLE:
    for _form in _forms:
        _CITY_LOOKUP.setdefault(_form.casefold(), _city)

# surface form (casefolded) -> canonical State
_STATE_LOOKUP: dict[str, str] = {}
for _state_name, _forms in STATE_VARIANTS.items():
    for _form in _forms:
        _STATE_LOOKUP.setdefault(_form.casefold(), _state_name)

# Every place word, used by the party extractor as a rejection list: a city or a
# State is never the opposite party.
PLACE_WORDS: frozenset[str] = frozenset(_CITY_LOOKUP) | frozenset(_STATE_LOOKUP)


def _compile(forms: dict[str, str]) -> re.Pattern[str]:
    # `\b` does not behave usefully around Devanagari, so use explicit
    # word-character lookarounds (Python's `\w` is Unicode-aware and covers
    # Devanagari letters and the matras attached to them).
    alternation = "|".join(
        re.escape(form) for form in sorted(forms, key=len, reverse=True)
    )
    return re.compile(rf"(?<!\w)(?:{alternation})(?!\w)", re.IGNORECASE)


_CITY_PATTERN = _compile(_CITY_LOOKUP)
_STATE_PATTERN = _compile(_STATE_LOOKUP)


def state_for_city(city: Optional[str]) -> Optional[str]:
    """Canonical State for a known city, or None. Never a guess."""
    if not city:
        return None
    return CITY_STATE.get(_CITY_LOOKUP.get(city.strip().casefold(), ""))


def canonical_city(city: Optional[str]) -> Optional[str]:
    if not city:
        return None
    return _CITY_LOOKUP.get(city.strip().casefold())


def canonical_state(state: Optional[str]) -> Optional[str]:
    if not state:
        return None
    return _STATE_LOOKUP.get(state.strip().casefold())


def resolve_jurisdiction(text: str) -> tuple[Optional[str], Optional[str]]:
    """Return the first (city, State) named in `text`, in canonical Latin form.

    An explicitly named State always wins over one inferred from a city. An
    unrecognised place yields `(None, None)` rather than a guessed State.
    """
    value = text or ""
    city_match = _CITY_PATTERN.search(value)
    city = _CITY_LOOKUP.get(city_match.group(0).casefold()) if city_match else None

    state = None
    city_span = city_match.span() if city_match else None
    for state_match in _STATE_PATTERN.finditer(value):
        # "Delhi" / "New Delhi" are simultaneously a city and a State. The same
        # span must not count twice, or a city mention would masquerade as an
        # explicitly stated State.
        if city_span and state_match.start() < city_span[1] and city_span[0] < state_match.end():
            continue
        candidate = _STATE_LOOKUP.get(state_match.group(0).casefold())
        if candidate:
            state = candidate
            break

    if state is None and city:
        state = CITY_STATE.get(city)
    return city, state
