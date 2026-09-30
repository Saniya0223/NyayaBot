"""Deterministic, multilingual safety triage for NyayaBot.

This module runs before model extraction, legal classification, retrieval, or
document routing. It separates a serious threat from evidence that danger is
happening *now*, records the relationship/context, and produces language- and
script-matched safety wording without relying on an LLM.
"""

from __future__ import annotations

import re
from dataclasses import dataclass, field
from typing import List, Optional, Sequence

from app.services.language_style import LanguageScript, detect_language_script


GREEN = "GREEN"
AMBER = "AMBER"
RED = "RED"

NONE = "NONE"
GENERIC_THREAT = "GENERIC_THREAT"
DOMESTIC_OR_PARTNER = "DOMESTIC_OR_PARTNER"
PHYSICAL_VIOLENCE = "PHYSICAL_VIOLENCE"
STALKING = "STALKING"
WEAPON = "WEAPON"
CHILD_SAFETY = "CHILD_SAFETY"
SELF_HARM = "SELF_HARM"

# Contexts that describe a danger coming from another person. DOMESTIC_OR_PARTNER
# and CHILD_SAFETY are deliberately absent: they say *who* is involved, not that
# anyone is being harmed.
EXTERNAL_HARM_CONTEXTS = (GENERIC_THREAT, PHYSICAL_VIOLENCE, STALKING, WEAPON)

CHECK_IMMEDIATE_DANGER = "CHECK_IMMEDIATE_DANGER"
URGENT_GUIDANCE = "URGENT_GUIDANCE"
SAFETY_INTAKE = "SAFETY_INTAKE"
CRISIS_SUPPORT = "CRISIS_SUPPORT"
NO_SAFETY_TRIAGE = "NONE"

# Free, 24x7 national lines. Kept here so every crisis reply, in every script,
# is generated from one source and can never be invented by a model.
CRISIS_HELPLINES = (
    ("Tele-MANAS", "14416"),
    ("KIRAN", "1800-599-0019"),
    ("AASRA", "9820466726"),
)
EMERGENCY_NUMBER = "112"


SignalPattern = tuple[str, str]

THREAT_PATTERNS: Sequence[SignalPattern] = (
    ("threat", r"\bthreat(?:s|ened|ening|en)?\b"),
    ("dhamki", r"\bdhamk[iy]\b|धमकी"),
    ("death_threat", r"\b(?:jaan|jan) se ma+r(?:ne)?\b|जान से मार"),
    ("future_harm", r"\b(?:maar|mar) (?:dunga|dungi|dega|degi|denge|dalega)\b|मार (?:दूंगा|दूँगा|देगा|देगी)"),
    ("kill_threat", r"\b(?:kill|murder) (?:me|us|him|her)\b|\bgoing to kill\b"),
    ("hurt_threat", r"\bhurt (?:me|us|karega|karegi)\b"),
    ("intimidation", r"\bintimidat(?:e|ed|es|ing|ion)\b|डरा(?:ना|ता|ती)|डराता"),
)

VIOLENCE_PATTERNS: Sequence[SignalPattern] = (
    ("physical_violence", r"\b(?:beat(?:ing|en)?|hit(?:ting)?|attack(?:ing|ed)?|assault(?:ed|ing)?) (?:me|us|him|her)\b"),
    ("roman_hindi_violence", r"\b(?:maar|mar|peet) (?:raha|rahi|rahe) (?:hai|hain)\b"),
    ("hindi_violence", r"(?:मार|पीट) (?:रहा|रही|रहे) (?:है|हैं)|मारपीट|हिंसा"),
    ("abuse", r"\b(?:domestic violence|physical abuse|sexual assault|rape|molest(?:ed|ing)?)\b|घरेलू हिंसा|यौन हमला"),
)

STALKING_PATTERNS: Sequence[SignalPattern] = (
    ("stalking", r"\bstalk(?:s|ed|ing|er)?\b|\bfollows? me\b"),
    ("peecha", r"\bpeecha kar (?:raha|rahi|rahe) (?:hai|hain)\b|पीछा कर (?:रहा|रही|रहे)"),
    ("repeated_harassment", r"\b(?:harass(?:ed|es|ing|ment)?|repeatedly threatens?)\b|बार[- ]?बार धमकी"),
)

WEAPON_PATTERNS: Sequence[SignalPattern] = (
    ("weapon", r"\b(?:weapon|knife|gun|pistol|rifle|acid|chaku|chaaku|bandook)\b|हथियार|चाकू|बंदूक|तेज़ाब"),
)

PARTNER_PATTERNS: Sequence[SignalPattern] = (
    ("intimate_partner", r"\b(?:husband|wife|spouse|partner|boyfriend|girlfriend|pati|patni)\b|पति|पत्नी|जीवनसाथी|प्रेमी|प्रेमिका"),
)

CHILD_PATTERNS: Sequence[SignalPattern] = (
    ("dependants", r"\b(?:child|children|kid|kids|minor|bachcha|bachche|bachchon|dependant|dependent)s?\b|बच्चा|बच्चे|बच्चों|नाबालिग"),
)

# Suicidal ideation / self-harm. Every form here is first-person and reflexive by
# construction, so "mar jaunga" (I will die) can never be read as "maar dunga"
# (I will kill him), which stays a threat against another person above.
SELF_HARM_PATTERNS: Sequence[SignalPattern] = (
    ("suicide_word", r"\bsuicid(?:e|al)\b|\bkhud[- ]?kush[iy]\b|\ba?atma[- ]?hatya\b|आत्महत्या|खुदकुशी|ख़ुदकुशी"),
    ("kill_myself", r"\b(?:kill|killing) myself\b|\bend(?:ing)? my (?:own )?life\b|\btake my own life\b|\bcommit suicide\b"),
    ("want_to_die", r"\b(?:want|wanted|wanna|wish|wished) to die\b|\bbetter off (?:dead|without me)\b|\bi should (?:just )?die\b"),
    ("no_reason_to_live", r"\b(?:don'?t|dont|do not) (?:want to|wanna) (?:live|go on)\b|\bno (?:reason|point|use)(?: in)? (?:to )?liv(?:e|ing)\b|\bnothing (?:left )?to live for\b|\bcan'?t (?:go on|live) any ?more\b|(?:और )?नहीं जी सकता|जी नहीं सकता"),
    ("self_injury", r"\b(?:harm|hurt|cut|cutting|harming|hurting) myself\b"),
    ("jeene_ka_fayda", r"\bje+ne (?:ka|ki) (?:koi )?(?:fayda|faida|matlab|maksad) nahi\b|जीने (?:का|की) (?:कोई )?(?:फायदा|फ़ायदा|मतलब|मकसद) नहीं"),
    ("jeene_ka_mann", r"\bje+ne ka (?:mann?|dil) nahi\b|\bje+na nahi chaht[aiu]\w*\b|जीने का (?:मन|दिल) नहीं|जीना नहीं चाहत"),
    ("mar_jaun", r"\bmar\s?jaa?un(?:ga|gi)?\b|\bmar\s?jaa?na chaht[aiu]\w*\b|मर जाऊ[ँं]|मर जाना चाहत"),
    ("apne_aap_ko_khatam", r"\bapne aap ko (?:khatam|khatm|khatham|samapt)\b|\bapni (?:jaan|zindagi|jindagi) (?:de\s?d(?:oon|un|u)(?:ga|gi)?|khatam|khatm)\b|अपने आप को (?:खत्म|ख़त्म)|अपनी (?:जान|ज़िंदगी|जिंदगी) (?:खत्म|ख़त्म|दे दू[ँं])"),
    ("zindagi_khatam", r"\b(?:zindagi|jindagi|zindgi) (?:khatam|khatm) (?:kar |kr )?(?:du|dun|doon|dungi|dunga)\b|(?:ज़िंदगी|जिंदगी) (?:खत्म|ख़त्म) कर (?:दू[ँं]|दूंगा)"),
    ("zinda_nahi_rehna", r"\bzinda nahi (?:rehna|rahna) chaht[aiu]\w*\b|(?:ज़िंदा|जिंदा) नहीं रहना चाहत"),
    ("phansi", r"\bphansi laga\w*\b|फांसी लगा|फाँसी लगा"),
)

# Ambiguous on their own ("let me finish the work", "I want to kill him").
# They count as self-harm only alongside a despair cue or a high-confidence hit.
SELF_HARM_AMBIGUOUS_PATTERNS: Sequence[SignalPattern] = (
    ("sab_khatam_kar_dun", r"\b(?:sab|sab kuch)? ?(?:khatam|khatm|khatham) (?:kar |kr )?(?:du|dun|doon|dunga|dungi)\b|(?:सब|सब कुछ)? ?(?:खत्म|ख़त्म) कर (?:दू[ँं]|दूंगा|दूंगी)"),
    ("end_it_all", r"\bend it all\b|\bgive up on everything\b"),
    ("marna_chahta", r"\bmarna chaht[aiu]\w*\b|मरना चाहत"),
)

# Hopelessness/despair wording that corroborates an ambiguous phrase above.
DESPAIR_PATTERNS: Sequence[SignalPattern] = (
    ("despair_nothing_left", r"\bkuch (?:nahi|nahin) bacha\b|\bnothing (?:is )?left\b|\bsab kuch (?:chala gaya|gaya|doob gaya|luta)\b|कुछ नहीं बचा|सब कुछ (?:चला गया|डूब गया)"),
    ("despair_ruined", r"\b(?:barbaad|barbad|tabah|tabaah)\b|\b(?:ruined|destroyed|wiped out)\b|बर्बाद|तबाह"),
    ("despair_hopeless", r"\bhopeless\b|\bno way out\b|\bcan'?t take (?:it|this) any ?more\b|\bhimmat (?:nahi|nahin)\b|\bkoi (?:fayda|faida|raasta|rasta) (?:nahi|nahin)\b|हिम्मत नहीं|कोई (?:फायदा|रास्ता) नहीं"),
    ("despair_worthless", r"\bmera (?:koi )?(?:wajood|matlab) (?:nahi|nahin)\b|\bworthless\b|\bburden (?:on|to) (?:my|everyone)\b"),
)

# "bhookhe mar jayenge" is an idiom about hardship, not an ideation disclosure.
SELF_HARM_IDIOM_EXCLUSION = re.compile(r"\bbhook(?:h|he|ha)?\b|भूख", re.IGNORECASE)

# How far back a self-harm match looks for the subject that governs it. Wide
# enough for "my tenant threatened to commit suicide", narrow enough that an
# unrelated earlier sentence cannot claim the clause.
SELF_HARM_SUBJECT_WINDOW = 6

# A third-person subject plus a reporting/threat verb, or a third-person
# reflexive inflection. Reporting somebody *else's* threat of suicide is a legal
# question, not the user's own disclosure.
THIRD_PARTY_ATTRIBUTION_PATTERN = re.compile(
    r"(?:\b(?:he|she|they|his|her|their)\b|\bmy \w+|\bthe \w+)\s+"
    r"(?:(?:is|was|are|were|has been|keeps|kept)\s+)?"
    r"(?:threaten(?:ed|s|ing)?|said|says|say|told|tells|claims|claimed|warned|warns|wants)\b"
    r"|(?:\busne\b|\bunhone\b|\buska\b|\buski\b|\bwoh?\b|\b\w+ ne)\s+(?:\w+\s+){0,3}?"
    r"(?:kaha|kahta|bola|boli|bol raha|bol rahi|keh raha|keh rahi|dhamki|dhamkaya)\b"
    r"|(?:उसने|उन्होंने|वह|\S+ ने)\s+(?:\S+\s+){0,3}?(?:कहा|बोला|धमकी)"
    r"|\b(?:kar lega|kar legi|kar lenge|le lega|le legi|himself|herself|themselves)\b",
    re.IGNORECASE,
)

FIRST_PERSON_MARKER_PATTERN = re.compile(
    r"\bi\b|\bi'm\b|\bim\b|\bmain\b|\bmai\b|\bmaine\b|\bmujhe\b|\bmera\b|\bmeri\b|\bkhud\b"
    r"|\bapne aap\b|\bmyself\b|मैं|मैंने|मुझे",
    re.IGNORECASE,
)

# Generic threat labels carry no object. Only these may be dropped when the
# thing being threatened is property, money or a legal step - never a person.
GENERIC_THREAT_LABELS = frozenset({"threat", "dhamki", "intimidation"})

NON_PERSONAL_THREAT_PATTERN = re.compile(
    r"\b(?:evict(?:ion|ing|ed)?|vacate|vacating|khali kar|room khali|throw (?:us|me|our|my) (?:stuff|things|luggage|belongings) out"
    r"|luggage|belongings|samaan|saaman|furniture|suitcase"
    r"|sue|suing|legal action|legal notice|court case|case (?:kar|karne|karunga|karenge)|civil suit|defamation"
    r"|lock (?:the|our|my) (?:room|door|gate|flat)|tala laga|ताला"
    r"|cut (?:the |off )?(?:water|electricity|power|wifi|internet)|bijli(?: aur | )?(?:pani)? kaat|pani kaat|बिजली|पानी काट"
    r"|terminate|termination|fire me|sack me|blacklist|suspend me|salary (?:rok|nahi denge)|deposit (?:rok|nahi denge)"
    r"|withhold (?:the )?(?:deposit|salary|payment)|forfeit"
    r"|report (?:me|us) to (?:the )?(?:police|company|hr)|police complaint against (?:me|us)"
    r"|increase (?:the )?rent|rent bada)\b"
    r"|खाली कर|सामान|कानूनी कार्रवाई|मुकदमा",
    re.IGNORECASE,
)

# ---------------------------------------------------------------------------
# Generic-threat complement analysis.
#
# The set of things a person can be *threatened with* is open, so enumerating
# objects (NON_PERSONAL_THREAT_PATTERN, above) can never be finished. The set of
# ways to harm a *person* is small and closed. So a generic threat is judged on
# its complement instead: keep it when the threat's direct object is the person,
# when it states no complement at all, or when the complement names a person and
# a person-harm verb. Otherwise it is a commercial/legal threat, not a safety case.
# ---------------------------------------------------------------------------

_PERSON = (
    r"(?:\bme\b|\bus\b|\bmyself\b"
    r"|\bmy (?:wife|husband|family|children|kids|daughter|son|mother|father|parents|sister|brother)\b"
    r"|\bour (?:family|children|kids)\b"
    # A threat against someone's body is a threat against them.
    r"|\bmy (?:legs?|arms?|hands?|fingers?|face|head|teeth|bones?|body|neck)\b"
    # So is a threat to turn up where they live or work. An unwanted approach is
    # menacing without naming any harm ("come to my house tonight"); the
    # lawful-process test below still drops "evict me from my house".
    # "room"/"shop" are deliberately absent: "threatened to lock my room" is an
    # illegal-lockout tenancy dispute, not a threat to the person.
    r"|\b(?:my|our) (?:house|home|place|workplace)\b"
    r"|\b(?:mere|hamare) (?:ghar|makan|office)\b|मेरे घर|हमारे घर"
    r"|\bmujhe\b|\bmujhko\b|\bhamein\b|\bhumein\b|\bhame\b"
    r"|\bmere (?:pariwar|bachchon|bachche|biwi|patni|pati)\b"
    r"|मुझे|हमें|मेरे परिवार)"
)

# Closed set: how one person harms another.
_PERSON_HARM_VERB = re.compile(
    r"\b(?:kill|murder|beat|hit|attack|assault|harm|hurt|injure|stab|shoot|burn|rape|molest|abduct|kidnap|acid)\w*\b"
    r"|\b(?:maar|maarne|maarunga|peet|peetne)\b|\bjaan se\b|\bjaan le\b|\bkhatam kar\b|\butha le\b|\bzinda nahi\b"
    r"|\bchhod(?:unga|ega|enge|egi)? nahi\b|\bnahi chhod(?:unga|ega|enge|egi)?\b|\bchod(?:unga|ega|enge)? nahi\b"
    r"|\btezaab\b|\bteja?ab\b"
    r"|मार|पीट|जान से|नहीं छोड़|छोड़ूंगा नहीं|तेज़ाब|तेजाब",
    re.IGNORECASE,
)

_PERSON_IN_TEXT = re.compile(_PERSON, re.IGNORECASE)

# The genuinely closed class. "Ways to harm a person" looked closed but is not -
# it is every transitive violence verb plus idiom (strangle, poison, break my
# legs, dekh lunga), and enumerating it made the guard miss real threats. What a
# person can lawfully threaten to *do to you through a process* is short and
# stable, so the test is inverted: a complement naming a person is a safety case
# unless the threatened act is one of these.
_LAWFUL_PROCESS_VERB = re.compile(
    r"\b(?:sue|suing|prosecute|litigat\w*|arbitrat\w*"
    r"|report(?:ing)?|complain(?:ing|t)?|file|filing|lodge|lodging"
    r"|evict\w*|terminate|terminating|termination|dismiss\w*|fire|sack|suspend"
    r"|blacklist\w*|withhold\w*|forfeit\w*|deduct\w*|recover|repossess|cancel\w*"
    r"|charge (?:me|us) (?:extra|more)|take (?:me|us) to court|legal action|court case)\b"
    r"|\b(?:naukri se nikal|nikal (?:dunga|denge|dega)|case kar(?:unga|enge|ega)?|court le ja\w*)\b",
    re.IGNORECASE,
)

# Rule 1: the threat's *direct object* is the person ("threatened me",
# "mujhe dhamki di", "मुझे धमकी दी"). Deliberately adjacent - at most one
# intervening word - so "threatening to sue me" cannot satisfy it.
_THREAT_OBJECT_IS_PERSON = re.compile(
    r"\b(?:threat(?:en|ens|ened|ening)|intimidat(?:e|ed|es|ing))\s+" + _PERSON
    + r"|" + _PERSON + r"\s+(?:\w+\s+){0,1}?(?:dhamk[iy]|threat(?:en|ens|ened|ening)?|धमकी)",
    re.IGNORECASE,
)

# Complement extraction - three structural forms, no object list.
_THREAT_COMPLEMENT_PATTERNS = (
    # English: "threatened to <VP>" / "threatening that <clause>" / "... with <NP>"
    re.compile(r"\bthreat(?:en|ens|ened|ening)\s+(?:to|that|with)\s+(?P<c>.+)", re.IGNORECASE),
    # Hinglish / Hindi post-posed: "dhamki di ki <clause>"
    re.compile(r"(?:dhamk[iy]|धमकी)\s*(?:\w+\s+){0,3}?(?:ki|कि)\s+(?P<c>.+)", re.IGNORECASE),
    # Hinglish / Hindi pre-posed: "<VP>ne ki dhamki". Requiring the complement to
    # end in an infinitive (...ne) stops the very common word "ki" from
    # swallowing unrelated text.
    re.compile(r"(?P<c>.+?\b\w+ne)\s+(?:ki|की)\s+(?:dhamk[iy]|धमकी)", re.IGNORECASE),
)

CURRENT_DANGER_PATTERNS: Sequence[SignalPattern] = (
    ("right_now", r"\b(?:right now|currently|at this moment)\b|\babhi\b|अभी"),
    ("outside_home", r"\boutside (?:my|our|the) (?:house|home|door|flat)\b|\b(?:ghar ke )?bahar khad[aei]? (?:hai|hain)\b|घर के बाहर खड़ा"),
    ("person_nearby", r"\b(?:is|standing|waiting) (?:near|beside) me\b|\b(?:mere|meri|hamare) paas (?:hai|hain)\b|मेरे पास (?:है|हैं)"),
    ("cannot_leave", r"\b(?:won'?t|will not|cannot|can'?t) let me leave\b|जाने नहीं दे"),
    ("breaking_in", r"\bbreaking (?:in|into)\b|दरवाज़ा तोड़"),
    ("not_safe", r"\b(?:i am|i'm|we are|we're) not safe\b|\b(?:main|hum) safe nahi (?:hun|hain)\b|मैं सुरक्षित नहीं"),
    ("in_danger", r"\b(?:i am|i'm|we are|we're) in (?:immediate )?danger\b|\b(?:main|hum) khatre mein (?:hun|hain)\b|खतरे में"),
)

SAFE_NOW_PATTERNS: Sequence[SignalPattern] = (
    ("safe_now", r"\b(?:i am|i'm|we are|we're) safe(?: now| right now)?\b|\b(?:main|hum) (?:ab |abhi |filhal )?safe (?:hun|hain)\b|मैं (?:अभी )?सुरक्षित (?:हूँ|हूं)|हम (?:अभी )?सुरक्षित हैं"),
    ("no_immediate_danger", r"\b(?:no|not in) immediate danger\b|\babhi (?:koi )?khatra nahi\b|अभी कोई खतरा नहीं"),
    ("person_left", r"\b(?:he|she|they|the person) (?:has |have )?left\b|\bwoh chala gaya\b|वह चला गया"),
)

EXPLICIT_DANGER_PATTERNS: Sequence[SignalPattern] = (
    ("explicit_danger", r"\b(?:i am|i'm|we are|we're) in danger\b|\b(?:main|hum) khatre mein (?:hun|hain)\b|मैं खतरे में"),
    ("explicit_not_safe", r"\bnot safe\b|\bsafe nahi\b|सुरक्षित नहीं"),
)

EVIDENCE_PATTERN = re.compile(
    r"\b(?:whatsapp|message|messages|chat|recording|audio|video|cctv|photo|photos|screenshot|screenshots|witness|witnesses|saboot)\b|सबूत|रिकॉर्डिंग|गवाह",
    re.IGNORECASE,
)
POLICE_PATTERN = re.compile(r"\b(?:police|fir|thana|sho|112|181)\b|पुलिस|थाना", re.IGNORECASE)
REPEATED_PATTERN = re.compile(r"\b(?:again|before|earlier|repeated|repeatedly|many times|often|pehle bhi|baar baar|har baar)\b|पहले भी|बार बार", re.IGNORECASE)
ONE_TIME_PATTERN = re.compile(r"\b(?:first time|never before|pehli baar)\b|पहली बार", re.IGNORECASE)
NO_EVIDENCE_PATTERN = re.compile(r"\b(?:no|don'?t have|do not have) (?:any )?(?:evidence|proof|messages|recording)\b|\b(?:koi )?saboot nahi\b|कोई सबूत नहीं", re.IGNORECASE)
NO_POLICE_PATTERN = re.compile(r"\b(?:not|haven'?t|have not|didn'?t|did not) (?:called|contacted|told|reported to) (?:the )?police\b|\bpolice (?:ko )?(?:nahi|nahin)\b|पुलिस को नहीं", re.IGNORECASE)
NO_VIOLENCE_PATTERN = re.compile(r"\b(?:no|not any|never) (?:physical violence|weapon|knife|gun)\b|\b(?:maarpeet|hathiyar|chaku|bandook) nahi\b|कोई मारपीट नहीं|कोई हथियार नहीं", re.IGNORECASE)
DATE_PATTERN = re.compile(
    r"\b(?:today|yesterday|tonight|last night|aaj|kal|raat|\d{1,2}[-/.]\d{1,2}[-/.]\d{2,4})\b|आज|कल|आज रात",
    re.IGNORECASE,
)

HARM_SIGNAL_LABELS = frozenset(
    {
        "threat", "dhamki", "death_threat", "future_harm", "kill_threat",
        "hurt_threat", "intimidation", "physical_violence",
        "roman_hindi_violence", "hindi_violence", "abuse", "stalking",
        "peecha", "repeated_harassment", "weapon",
    }
)

# Deliberately kept out of HARM_SIGNAL_LABELS: a disclosure of self-harm is not
# a "threat detail" to be written into an evidence record.
SELF_HARM_SIGNAL_LABELS = frozenset(
    {label for label, _ in (*SELF_HARM_PATTERNS, *SELF_HARM_AMBIGUOUS_PATTERNS)}
)

SIMPLE_YES_ANSWERS = frozenset(
    {"yes", "yes i am", "yeah", "yep", "haan", "han", "ha", "ji", "हाँ", "हां", "जी"}
)
SIMPLE_NO_ANSWERS = frozenset(
    {"no", "no i am not", "nope", "nahi", "nahin", "nhi", "नहीं", "नही"}
)


SAFETY_INTAKE_FACTS = [
    "immediate_danger",
    "threat_details",
    "incident_date",
    "repeated_incidents",
    "physical_violence_or_weapon",
    "evidence_available",
    "police_contacted",
]

# A person disclosing self-harm is not interrogated. Three of the seven safety
# facts above (threat_details, physical_violence_or_weapon, police_contacted)
# describe an attacker, and a crisis-only case has none - so demanding them
# froze the underlying legal matter for the life of the case.
CRISIS_INTAKE_FACTS: list[str] = []


def _normalize(text: str) -> str:
    return re.sub(r"\s+", " ", (text or "").casefold()).strip()


def simple_yes_no(text: str) -> Optional[bool]:
    """Interpret an unambiguous short answer in supported scripts."""
    value = re.sub(r"[^a-z\u0900-\u097f ]", "", _normalize(text))
    if value in SIMPLE_YES_ANSWERS:
        return True
    if value in SIMPLE_NO_ANSWERS:
        return False
    return None


def _hits(patterns: Sequence[SignalPattern], text: str) -> list[str]:
    return [label for label, pattern in patterns if re.search(pattern, text, re.IGNORECASE)]


def _unique(values: Sequence[str]) -> list[str]:
    return list(dict.fromkeys(value for value in values if value))


def _threat_complement(text: str) -> Optional[str]:
    """The clause a generic threat states, or None when it states none."""
    for pattern in _THREAT_COMPLEMENT_PATTERNS:
        match = pattern.search(text)
        if match:
            return match.group("c")
    return None


def _drop_non_personal_generic_threats(labels: list[str], text: str) -> list[str]:
    """Keep a generic threat only when its complement is harm to a person.

    "The manager keeps threatening to put our luggage outside" is an eviction
    dispute, not a safety case. The discriminator is not "is a person named"
    (that would drop "mere pati ne dhamki di", which names nobody) but "does the
    threat state an object at all, and if so is that object a person being
    harmed" - a closed test against an open one.

        1. the threat's direct object is the person  ("threatened me")      KEEP
        2. no complement clause found                ("dhamki di")          KEEP*
        3. complement contains a PERSON-HARM verb                           KEEP
        4. complement names a PERSON, and the act is not lawful process     KEEP
        5. otherwise                                                        DROP

    (*) NON_PERSONAL_THREAT_PATTERN survives only as the secondary filter for
    case 2, where the wording plainly names property but states no complement
    ("the society is threatening legal action against us").

    The whole guard applies only when every threat label in the turn is a
    generic one, so a death, kill, future-harm or hurt threat can never be
    discarded by it.
    """
    if not labels or any(label not in GENERIC_THREAT_LABELS for label in labels):
        return labels
    if _THREAT_OBJECT_IS_PERSON.search(text):
        return labels
    complement = _threat_complement(text)
    if complement is None:
        return [] if NON_PERSONAL_THREAT_PATTERN.search(text) else labels
    # A person-harm verb settles it on its own, named victim or not:
    # "tezaab phek dega", "burn my house down".
    if _PERSON_HARM_VERB.search(complement):
        return labels
    # Otherwise a complement naming a person is kept unless what is threatened
    # is a lawful or commercial process ("report me to the bank", "take me to
    # court"). Requiring a harm verb here instead made the guard drop every
    # violence threat whose verb was not on the list - strangle, poison, break
    # my legs - which is the failure this ordering exists to prevent.
    if _PERSON_IN_TEXT.search(complement):
        return [] if _LAWFUL_PROCESS_VERB.search(_complement_head(complement)) else labels
    return []


_SUBORDINATOR = re.compile(
    r"\s+(?:if|when|unless|until|because|since|so that|otherwise|or else)\s+"
    r"|\s+(?:agar|jab|warna|nahi to|kyunki)\s+"
    r"|\s*(?:अगर|जब|वरना|क्योंकि)\s*",
    re.IGNORECASE,
)


def _complement_head(complement: str) -> str:
    """The threatened act itself, without the condition attached to it.

    In "strangle me if I complain to the police" the subordinate clause
    describes what the *user* might do. Testing the whole complement for lawful
    process therefore reads the victim's conduct as if it were the threat, and
    discards a strangulation threat as a legal dispute.
    """
    return _SUBORDINATOR.split(complement, maxsplit=1)[0]


def _window_start(text: str, index: int, tokens: int) -> int:
    """Start offset of the `tokens`-th whitespace token before `index`."""
    matches = list(re.finditer(r"\S+", text[:index]))
    if len(matches) <= tokens:
        return 0
    return matches[-tokens].start()


def _third_party_attributed(text: str, start: int) -> bool:
    """True when the clause governing a self-harm match belongs to someone else.

    "My tenant threatened to commit suicide if I evict him" is the landlord's
    legal problem, not the landlord's crisis. The test is deliberately
    *positional*: only a first-person marker between the attribution frame and
    the match rescues the hit. A whole-text scan would be fooled by the "I" in
    "if I evict him", which arrives after the match and says nothing about who
    would be harmed.

    The default is KEEP. A sentence with no subject at all ("socha tha suicide
    kar lun") is still the user speaking about themselves.
    """
    window = text[_window_start(text, start, SELF_HARM_SUBJECT_WINDOW):start]
    frame = None
    for match in THIRD_PARTY_ATTRIBUTION_PATTERN.finditer(window):
        frame = match
    if frame is None:
        return False
    return not FIRST_PERSON_MARKER_PATTERN.search(window[frame.end():])


def _self_harm_pattern_hits(patterns: Sequence[SignalPattern], text: str) -> list[str]:
    """Labels whose match is not attributed to a third party."""
    labels: list[str] = []
    for label, pattern in patterns:
        for match in re.finditer(pattern, text, re.IGNORECASE):
            if _third_party_attributed(text, match.start()):
                continue
            labels.append(label)
            break
    return labels


def _self_harm_hits(text: str) -> list[str]:
    """Self-harm labels for this turn, with the precision guards applied."""
    strong = [
        label for label in _self_harm_pattern_hits(SELF_HARM_PATTERNS, text)
        if not (label == "mar_jaun" and SELF_HARM_IDIOM_EXCLUSION.search(text))
    ]
    ambiguous = _self_harm_pattern_hits(SELF_HARM_AMBIGUOUS_PATTERNS, text)
    if not ambiguous:
        return strong
    # "sab khatam kar dun" also means "let me finish it off". It only counts
    # when the turn also carries despair, or an unambiguous disclosure.
    if strong or _hits(DESPAIR_PATTERNS, text):
        return _unique([*strong, *ambiguous])
    return strong


def crisis_support_copy(style: LanguageScript) -> tuple[Optional[str], Optional[str]]:
    """Copy for a self-harm disclosure, in the user's own script.

    Returns (question, support_text). It acknowledges the person, gives the free
    24x7 national lines, and offers to pick the legal matter up again later. It
    carries no evidence question, no attacker-proximity question, no promise that
    everything will be fine, and exactly one gentle question.
    """
    lines = "\n".join(f"- {name}: {number}" for name, number in CRISIS_HELPLINES)

    if style.language == "hindi" and style.script == "devanagari":
        support = (
            "आप जो महसूस कर रहे हैं वह बहुत भारी है, और इसे अकेले सहना ज़रूरी नहीं है। "
            "जो नुकसान हुआ है उसके लिए रास्ते निकाले जा सकते हैं; आपकी जान उनसे कहीं ज़्यादा ज़रूरी है।\n\n"
            "कृपया अभी किसी से बात कीजिए। ये मदद मुफ़्त है और चौबीसों घंटे उपलब्ध है:\n"
            f"{lines}\n"
            f"- तुरंत ख़तरा हो तो: {EMERGENCY_NUMBER}\n\n"
            "जब आप तैयार हों, हम आपके मामले पर आगे बात करेंगे।"
        )
        return "क्या इस वक़्त आपके पास कोई अपना व्यक्ति है जिससे आप बात कर सकें?", support

    if style.language == "hinglish" and style.script == "roman":
        support = (
            "Aap jo mehsoos kar rahe hain wo bahut bhaari hai, aur ise akele sehna zaroori nahi hai. "
            "Jo nuksaan hua hai uske liye raaste nikale ja sakte hain; aapki jaan unse kahin zyada zaroori hai.\n\n"
            "Please abhi kisi se baat kijiye. Yeh madad free hai aur 24x7 available hai:\n"
            f"{lines}\n"
            f"- Turant khatra ho to: {EMERGENCY_NUMBER}\n\n"
            "Jab aap taiyar hon, hum aapke maamle par aage baat karenge."
        )
        return "Kya is waqt aapke paas koi apna vyakti hai jisse aap baat kar sakein?", support

    support = (
        "What you are carrying right now sounds very heavy, and you do not have to carry it alone. "
        "There are ways to pursue what you have lost; your life matters far more than any of it.\n\n"
        "Please talk to someone now. This help is free and available 24x7:\n"
        f"{lines}\n"
        f"- If you are in immediate danger: {EMERGENCY_NUMBER}\n\n"
        "When you feel ready, we can pick your matter up again."
    )
    return "Is there someone you trust who can be with you right now?", support


def _localized_safety_copy(
    style: LanguageScript,
    immediate_danger: Optional[bool],
    dependants_present: bool,
    stage: str = NO_SAFETY_TRIAGE,
) -> tuple[Optional[str], Optional[str]]:
    if stage == CRISIS_SUPPORT:
        return crisis_support_copy(style)

    if style.language == "hindi" and style.script == "devanagari":
        if immediate_danger is True:
            question = "क्या आप बच्चों सहित किसी सुरक्षित जगह या भरोसेमंद व्यक्ति के पास जा सकते हैं?" if dependants_present else "क्या आप अभी किसी सुरक्षित जगह या भरोसेमंद व्यक्ति के पास जा सकते हैं?"
            guidance = "पहले सुरक्षित जगह पर जाएँ और 112 पर कॉल करें। यदि सुरक्षित हो तो 181 महिला हेल्पलाइन या बच्चों के लिए 1098 पर भी कॉल कर सकते हैं।"
        elif immediate_danger is False:
            question = None
            guidance = "ठीक है—यह जानना महत्वपूर्ण है कि आप अभी सुरक्षित हैं।"
        else:
            question = "क्या आप अभी तुरंत खतरे में हैं, या वह व्यक्ति अभी आपके पास है?"
            guidance = "अगर आपको अभी तुरंत खतरा है, तो पहले किसी सुरक्षित जगह पर जाएँ और 112 पर कॉल करें।"
        return question, guidance

    if style.language == "hinglish" and style.script == "roman":
        if immediate_danger is True:
            question = "Kya aap bachchon ke saath kisi safe jagah ya bharosemand vyakti ke paas ja sakte hain?" if dependants_present else "Kya aap abhi kisi safe jagah ya bharosemand vyakti ke paas ja sakte hain?"
            guidance = "Pehle kisi safe jagah par jaiye aur 112 par call kijiye. Agar safe ho, to 181 women's helpline ya bachchon ke liye 1098 par bhi call kar sakte hain."
        elif immediate_danger is False:
            question = None
            guidance = "Theek hai—yeh jaanna zaroori hai ki aap abhi safe hain."
        else:
            question = "Kya aap abhi turant khatre mein hain, ya woh vyakti abhi aapke paas hai?"
            guidance = "Agar aapko abhi turant khatra hai, to pehle kisi safe jagah par jaiye aur 112 par call kijiye."
        return question, guidance

    if immediate_danger is True:
        question = "Can you move to a safe place or reach a trusted person right now, with the children if they are with you?" if dependants_present else "Can you move to a safe place or reach a trusted person right now?"
        guidance = "Move to safety first and call 112. If safe to do so, you can also call the women's helpline at 181 or Childline at 1098."
    elif immediate_danger is False:
        question = None
        guidance = "Thank you for confirming that you are safe right now."
    else:
        question = "Are you in immediate danger right now, or is that person currently near you?"
        guidance = "If the danger is immediate, move to a safe place and call 112 before we continue."
    return question, guidance


@dataclass(frozen=True)
class SafetyAssessment:
    """Structured outcome of deterministic safety triage for one turn."""

    is_safety_case: bool = False
    safety_level: str = GREEN
    safety_context: str = NONE
    contexts: List[str] = field(default_factory=list)
    immediate_danger: Optional[bool] = None
    dependants_present: bool = False
    language_style: str = "english"
    script_style: str = "roman"
    stage: str = NO_SAFETY_TRIAGE
    triage_question: Optional[str] = None
    guidance: Optional[str] = None
    matched_signals: List[str] = field(default_factory=list)
    # Signals found in *this* turn, as opposed to the sticky case-level state.
    self_harm: bool = False
    fresh_harm_signal: bool = False
    # This turn spoke about danger or safety ("abhi main safe hun"), so it is an
    # answer to the triage question rather than a change of subject.
    danger_signal: bool = False

    @property
    def severity(self) -> str:
        """Compatibility view for callers using the earlier severity names.

        The legacy value described how serious the wording was, not whether the
        danger was occurring now. New routing must use safety_level together
        with immediate_danger.
        """
        if self.safety_level == RED or any(
            signal in {"death_threat", "kill_threat", "future_harm"}
            for signal in self.matched_signals
        ):
            return "IMMEDIATE"
        return "ELEVATED" if self.safety_level == AMBER else "NONE"

    @property
    def has_safety_relevance(self) -> bool:
        return self.is_safety_case or self.dependants_present

    @property
    def blocks_document_routing(self) -> bool:
        return self.is_safety_case and self.safety_level in {AMBER, RED}

    def to_dict(self) -> dict:
        return {
            "is_safety_case": self.is_safety_case,
            "safety_level": self.safety_level,
            "severity": self.severity,
            "safety_context": self.safety_context,
            "contexts": list(self.contexts),
            "immediate_danger": self.immediate_danger,
            "dependants_present": self.dependants_present,
            "language": self.language_style,
            "script": self.script_style,
            "stage": self.stage,
            "triage_question": self.triage_question,
            "guidance": self.guidance,
            "matched_signals": list(self.matched_signals),
            "self_harm": self.self_harm,
            "fresh_harm_signal": self.fresh_harm_signal,
            "danger_signal": self.danger_signal,
        }


def assess_safety(
    message: str,
    prior_history_text: str = "",
    prior_safety: Optional[dict] = None,
    prior_language: Optional[str] = None,
    prior_script: Optional[str] = None,
    prior_question_group: Optional[str] = None,
) -> SafetyAssessment:
    """Classify safety before any model, workflow, retrieval, or document work."""
    value = _normalize(message)
    prior_value = _normalize(prior_history_text)
    prior_safety = prior_safety or {}
    style = detect_language_script(value, prior_language, prior_script)

    current_threats = _drop_non_personal_generic_threats(_hits(THREAT_PATTERNS, value), value)
    current_self_harm = _self_harm_hits(value)
    current_violence = _hits(VIOLENCE_PATTERNS, value)
    current_stalking = _hits(STALKING_PATTERNS, value)
    current_weapons = _hits(WEAPON_PATTERNS, value)
    current_partner = _hits(PARTNER_PATTERNS, value)
    current_children = _hits(CHILD_PATTERNS, value)

    prior_contexts = list(prior_safety.get("contexts") or [])
    if not prior_contexts and prior_value:
        if _self_harm_hits(prior_value):
            prior_contexts.append(SELF_HARM)
        if _drop_non_personal_generic_threats(_hits(THREAT_PATTERNS, prior_value), prior_value):
            prior_contexts.append(GENERIC_THREAT)
        if _hits(VIOLENCE_PATTERNS, prior_value):
            prior_contexts.append(PHYSICAL_VIOLENCE)
        if _hits(STALKING_PATTERNS, prior_value):
            prior_contexts.append(STALKING)
        if _hits(WEAPON_PATTERNS, prior_value):
            prior_contexts.append(WEAPON)
        if _hits(PARTNER_PATTERNS, prior_value):
            prior_contexts.append(DOMESTIC_OR_PARTNER)
        if _hits(CHILD_PATTERNS, prior_value):
            prior_contexts.append(CHILD_SAFETY)

    contexts: list[str] = []
    if current_self_harm:
        contexts.append(SELF_HARM)
    if current_partner:
        contexts.append(DOMESTIC_OR_PARTNER)
    if current_weapons:
        contexts.append(WEAPON)
    if current_violence:
        contexts.append(PHYSICAL_VIOLENCE)
    if current_stalking:
        contexts.append(STALKING)
    if current_threats:
        contexts.append(GENERIC_THREAT)
    if current_children:
        contexts.append(CHILD_SAFETY)
    contexts = _unique([*prior_contexts, *contexts])

    current_harm = bool(current_threats or current_violence or current_stalking or current_weapons)
    # DOMESTIC_OR_PARTNER is a context modifier, not a trigger - exactly like
    # CHILD_SAFETY. The bare word "pati"/"wife" says who is involved, not that
    # anyone is in danger, so on its own it must not create a safety case.
    is_safety_case = bool(prior_safety.get("is_safety_case")) or current_harm or bool(current_self_harm) or any(
        context in contexts
        for context in (*EXTERNAL_HARM_CONTEXTS, SELF_HARM)
    )
    dependants_present = bool(current_children) or bool(prior_safety.get("dependants_present")) or CHILD_SAFETY in contexts

    explicit_danger = _hits(EXPLICIT_DANGER_PATTERNS, value)
    explicit_safe = _hits(SAFE_NOW_PATTERNS, value)
    current_danger = _hits(CURRENT_DANGER_PATTERNS, value)
    ongoing_violence = bool(current_violence and re.search(r"\b(?:raha|rahi|rahe|beating|hitting|attacking)\b|(?:रहा|रही|रहे)", value))
    contextual_answer = simple_yes_no(value) if prior_question_group == "immediate_danger" else None

    if contextual_answer is True:
        explicit_danger.append("contextual_yes_to_immediate_danger")
        immediate_danger: Optional[bool] = True
    elif contextual_answer is False:
        explicit_safe.append("contextual_no_to_immediate_danger")
        immediate_danger = False
    elif explicit_danger:
        immediate_danger: Optional[bool] = True
    elif explicit_safe:
        immediate_danger = False
    elif is_safety_case and (ongoing_violence or current_danger or (current_weapons and current_harm)):
        immediate_danger = True
    elif current_harm and prior_safety.get("triage_complete"):
        # A newly disclosed threat after a completed check starts a fresh
        # immediate-danger check unless the current turn itself resolves it.
        immediate_danger = None
    elif "immediate_danger" in prior_safety:
        immediate_danger = prior_safety.get("immediate_danger")
    else:
        immediate_danger = None

    safety_level = RED if immediate_danger is True else AMBER if is_safety_case else GREEN
    # A disclosure of self-harm outranks the immediate-danger ladder: the
    # attacker-proximity question is the wrong question to ask a person in crisis.
    crisis_pending = SELF_HARM in contexts and not prior_safety.get("crisis_support_offered")
    if not is_safety_case:
        stage = NO_SAFETY_TRIAGE
    elif current_self_harm or crisis_pending:
        stage = CRISIS_SUPPORT
    elif immediate_danger is True:
        stage = URGENT_GUIDANCE
    elif immediate_danger is None:
        stage = CHECK_IMMEDIATE_DANGER
    else:
        stage = SAFETY_INTAKE

    priority = (SELF_HARM, DOMESTIC_OR_PARTNER, WEAPON, PHYSICAL_VIOLENCE, STALKING, GENERIC_THREAT, CHILD_SAFETY)
    safety_context = next((context for context in priority if context in contexts), NONE)
    question, guidance = (
        _localized_safety_copy(style, immediate_danger, dependants_present, stage)
        if is_safety_case
        else (None, None)
    )
    signals = _unique(
        [
            *current_self_harm,
            *current_threats,
            *current_violence,
            *current_stalking,
            *current_weapons,
            *current_partner,
            *current_children,
            *explicit_danger,
            *explicit_safe,
            *current_danger,
        ]
    )
    return SafetyAssessment(
        is_safety_case=is_safety_case,
        safety_level=safety_level,
        safety_context=safety_context,
        contexts=contexts,
        immediate_danger=immediate_danger,
        dependants_present=dependants_present,
        language_style=style.language,
        script_style=style.script,
        stage=stage,
        triage_question=question,
        guidance=guidance,
        matched_signals=signals[:12],
        self_harm=bool(current_self_harm),
        fresh_harm_signal=bool(current_harm or current_self_harm),
        danger_signal=bool(explicit_danger or explicit_safe or current_danger or ongoing_violence),
    )


def extract_safety_facts(message: str, assessment: SafetyAssessment) -> dict:
    """Extract only high-confidence safety facts from the current turn."""
    value = _normalize(message)
    facts: dict = {}
    if assessment.immediate_danger is not None:
        facts["immediate_danger"] = assessment.immediate_danger
    if any(signal in HARM_SIGNAL_LABELS for signal in assessment.matched_signals):
        facts["threat_details"] = message.strip()
    date = DATE_PATTERN.search(value)
    if date:
        facts["incident_date"] = date.group(0)
    if REPEATED_PATTERN.search(value):
        facts["repeated_incidents"] = True
    elif ONE_TIME_PATTERN.search(value):
        facts["repeated_incidents"] = False
    if PHYSICAL_VIOLENCE in assessment.contexts or WEAPON in assessment.contexts:
        facts["physical_violence_or_weapon"] = True
    elif NO_VIOLENCE_PATTERN.search(value):
        facts["physical_violence_or_weapon"] = False
    if EVIDENCE_PATTERN.search(value):
        facts["evidence_available"] = True
    elif NO_EVIDENCE_PATTERN.search(value):
        facts["evidence_available"] = False
    if POLICE_PATTERN.search(value):
        facts["police_contacted"] = not bool(NO_POLICE_PATTERN.search(value))
    elif NO_POLICE_PATTERN.search(value):
        facts["police_contacted"] = False
    if assessment.dependants_present:
        facts["dependants_present"] = True
        if assessment.immediate_danger is True:
            facts["dependants_at_risk"] = True
    return facts


def is_crisis_only_case(key_facts: Optional[dict]) -> bool:
    """A self-harm disclosure with no other person threatening the user.

    A *mixed* case (self-harm plus a real attacker) is deliberately excluded:
    that case still needs the immediate-danger answer, and still runs the full
    safety intake.
    """
    contexts = set((key_facts or {}).get("safety_contexts") or [])
    if SELF_HARM not in contexts:
        return False
    return not any(context in contexts for context in EXTERNAL_HARM_CONTEXTS)


def safety_intake_facts_for(key_facts: Optional[dict]) -> list[str]:
    """The safety intake contract that applies to this case."""
    return list(CRISIS_INTAKE_FACTS if is_crisis_only_case(key_facts) else SAFETY_INTAKE_FACTS)


def safety_triage_resolved(key_facts: Optional[dict]) -> bool:
    """True once the safety question this case actually has has been settled.

    For an ordinary safety case that is the explicit immediate-danger answer.
    A crisis-only case never answers it - crisis support bypasses the danger
    ladder by design - so offering crisis support resolves it instead. Without
    this the ladder stayed pinned at UNDERSTANDING_CASE permanently.
    """
    key_facts = key_facts or {}
    if key_facts.get("immediate_danger") is not None:
        return True
    return bool(key_facts.get("crisis_support_offered")) and is_crisis_only_case(key_facts)
