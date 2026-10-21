"""Authoritative structural definitions for NyayaBot legal domains."""

from app.domains.contracts import (
    DomainDefinition, FactDefinition, FactProvenance, FactState,
    QuestionPriority, VerificationState,
)
from app.domains.registry import DomainRegistry, DomainRegistryError, domain_registry

__all__ = [
    "DomainDefinition", "DomainRegistry", "DomainRegistryError",
    "FactDefinition", "FactProvenance", "FactState", "QuestionPriority",
    "VerificationState", "domain_registry",
]
