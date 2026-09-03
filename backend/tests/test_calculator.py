import pytest
from datetime import datetime, timedelta
from app.agents.calculator_node import LegalCalculator

def test_consumer_pecuniary_jurisdiction_district():
    res = LegalCalculator.calculate_consumer_jurisdiction(45000.0, "Pune")
    assert res["tier"] == "DISTRICT"
    assert "District Consumer Disputes Redressal Commission" in res["appropriate_forum"]
    assert "Pune" in res["appropriate_forum"]

def test_consumer_pecuniary_jurisdiction_state():
    res = LegalCalculator.calculate_consumer_jurisdiction(7500000.0, "Mumbai")
    assert res["tier"] == "STATE"
    assert "State Consumer Disputes Redressal Commission" in res["appropriate_forum"]

def test_consumer_pecuniary_jurisdiction_national():
    res = LegalCalculator.calculate_consumer_jurisdiction(25000000.0, "Delhi")
    assert res["tier"] == "NATIONAL"
    assert "National Consumer Disputes Redressal Commission" in res["appropriate_forum"]

def test_limitation_calculation_consumer():
    # 2 years limitation for Consumer Protection Act
    today = datetime.now().date()
    past_date_str = (today - timedelta(days=100)).strftime("%d-%m-%Y")
    deadline, days_left, status = LegalCalculator.calculate_limitation_period("CONSUMER", past_date_str)
    assert days_left is not None
    assert days_left > 600
    assert status == "SAFE"

def test_limitation_calculation_expired():
    # 3 years ago for Consumer should be expired (> 730 days)
    today = datetime.now().date()
    expired_date_str = (today - timedelta(days=800)).strftime("%d-%m-%Y")
    deadline, days_left, status = LegalCalculator.calculate_limitation_period("CONSUMER", expired_date_str)
    assert days_left is not None
    assert days_left < 0
    assert status == "EXPIRED"
