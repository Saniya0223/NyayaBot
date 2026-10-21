"""Auto-Fill portal selection: every case category the app can produce.

Pure lookup tests; no browser, network or LLM.
"""

import pytest

from app.domains import domain_registry
from app.services import portal_selector as ps
from app.services.portal_selector import select_portal

CONSUMER_HELPLINE = "https://consumerhelpline.gov.in/"
CYBERCRIME = "https://cybercrime.gov.in/"
SAMADHAN = "https://samadhan.labour.gov.in/"
CPGRAMS = "https://pgportal.gov.in/"

# The decision for every real case category. Adding a domain without adding a
# row here fails test_every_real_category_has_a_decision, so a new category can
# never silently fall through to the fallback again.
EXPECTED_URL = {
    "CONSUMER": CONSUMER_HELPLINE,
    "EMPLOYMENT": SAMADHAN,
    "CYBER_FRAUD": CYBERCRIME,
    "HOUSING_TENANT": CONSUMER_HELPLINE,
    "POLICE_COMPLAINT": CPGRAMS,  # deliberately unmapped; see portal_selector.py
    "GENERAL": CPGRAMS,
}


def real_categories():
    return [domain.id for domain in domain_registry.all()]


def test_every_real_category_has_a_decision():
    assert sorted(real_categories()) == sorted(EXPECTED_URL)


@pytest.mark.parametrize("category, url", sorted(EXPECTED_URL.items()))
def test_each_real_category_reaches_its_intended_portal(category, url):
    assert select_portal(category).url == url


def test_cyber_fraud_cases_no_longer_fall_back_to_cpgrams():
    portal = select_portal("CYBER_FRAUD")
    assert portal.label == "National Cyber Crime Reporting Portal"
    assert portal is not ps._FALLBACK


def test_tenant_cases_get_the_tenant_task_not_the_generic_one():
    portal = select_portal("HOUSING_TENANT")
    assert "Tenant Dispute" in portal.label
    assert "{property_address}" in portal.task_template


def test_police_complaint_is_not_sent_to_the_ecourts_case_status_lookup():
    portal = select_portal("POLICE_COMPLAINT")
    assert "ecourts" not in portal.url
    assert portal is ps._FALLBACK


@pytest.mark.parametrize("category", ["cyber_fraud", "Cyber-Fraud", "  CYBER FRAUD  ", "CYBER_FRAUD"])
def test_category_spelling_and_case_do_not_matter(category):
    assert select_portal(category).url == CYBERCRIME


@pytest.mark.parametrize("portal_key, url", [
    ("cybercrime", CYBERCRIME), ("rti", "https://rtionline.gov.in/"),
    ("tenant", CONSUMER_HELPLINE), ("consumer", CONSUMER_HELPLINE),
    ("employment", SAMADHAN),
])
def test_existing_portal_keys_still_work(portal_key, url):
    assert select_portal(portal_key).url == url


@pytest.mark.parametrize("category", [None, "", "   ", "SOMETHING_NEW", "unknown"])
def test_unknown_or_missing_category_still_falls_back(category):
    assert select_portal(category) is ps._FALLBACK


def test_every_mapped_alias_points_at_a_real_portal_entry():
    for category, key in ps._CATEGORY_TO_PORTAL_KEY.items():
        assert key in ps._PORTAL_MAP, f"{category} -> {key} has no portal entry"


def test_every_portal_task_still_forbids_final_submission():
    for key, portal in ps._PORTAL_MAP.items():
        assert "Do NOT" in portal.task_template, key
