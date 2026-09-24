"""Validated registry and query API for all legal-domain definitions."""

from __future__ import annotations

from collections.abc import Iterable
from typing import Any, Optional

from app.domains.compatibility import CATEGORY_ALIASES, read_profile_fact
from app.domains.contracts import DomainDefinition, FactDefinition, FactPurpose, FactState, fact_state


class DomainRegistryError(ValueError):
    pass


class DomainRegistry:
    def __init__(self, definitions: Iterable[DomainDefinition], aliases: Optional[dict[str, str]] = None):
        values = tuple(definitions)
        identifiers = [item.id for item in values]
        if len(identifiers) != len(set(identifiers)):
            raise DomainRegistryError("duplicate domain IDs")
        self._definitions = {item.id: item for item in values}
        self._aliases: dict[str, str] = {}

        alias_values = dict(aliases or {})
        for domain in values:
            for alias in domain.aliases:
                alias_values.setdefault(alias, domain.id)
        for raw_alias, raw_target in alias_values.items():
            alias = raw_alias.strip().upper()
            target = raw_target.strip().upper()
            if not alias or alias == target:
                continue
            if target not in self._definitions:
                raise DomainRegistryError(f"alias {alias} targets unknown domain {target}")
            existing = self._aliases.get(alias)
            if existing and existing != target:
                raise DomainRegistryError(f"alias {alias} maps to multiple domains")
            if alias in self._definitions and alias != target:
                raise DomainRegistryError(f"alias {alias} collides with a domain ID")
            self._aliases[alias] = target

    def all(self) -> tuple[DomainDefinition, ...]:
        return tuple(self._definitions.values())

    def get(self, domain_id: str | None) -> Optional[DomainDefinition]:
        if not domain_id:
            return None
        key = domain_id.strip().upper()
        return self._definitions.get(self._aliases.get(key, key))

    def require(self, domain_id: str | None) -> DomainDefinition:
        domain = self.get(domain_id)
        if domain is None:
            raise DomainRegistryError(f"unknown domain ID: {domain_id}")
        return domain

    def resolve(self, domain_id: str | None) -> DomainDefinition:
        return self.get(domain_id) or self.require("GENERAL")

    def normalize_id(self, domain_id: str | None) -> str:
        return self.resolve(domain_id).id

    def fallback_classify(self, text: str) -> str:
        value = (text or "").casefold()
        matches = [
            domain for domain in self._definitions.values()
            if domain.id != "GENERAL" and any(term.casefold() in value for term in domain.classification_terms)
        ]
        if not matches:
            return "GENERAL"
        return min(matches, key=lambda item: item.fallback_priority).id

    @staticmethod
    def _fact_is_known(profile: Any, definition: FactDefinition) -> bool:
        present, value = read_profile_fact(profile, definition.key, definition.aliases)
        return fact_state(value, present=present, definition=definition) != FactState.UNKNOWN

    def unresolved_facts(
        self,
        profile: Any,
        *,
        required_only: bool = True,
        include_document: bool = False,
    ) -> tuple[FactDefinition, ...]:
        domain = self.resolve(getattr(profile, "category", None))
        unresolved: list[FactDefinition] = []
        for definition in domain.ordered_facts(getattr(profile, "issue_type", None), include_document=include_document):
            if required_only and not definition.required_for_understanding:
                continue
            if definition.ask_when_fact:
                _, condition = read_profile_fact(profile, definition.ask_when_fact)
                if condition is not definition.ask_when_value:
                    continue
            if not self._fact_is_known(profile, definition):
                unresolved.append(definition)
        return tuple(unresolved)

    def issue_understood(self, profile: Any) -> bool:
        domain = self.resolve(getattr(profile, "category", None))
        if domain.id == "GENERAL":
            return False
        context_keys = domain.minimum_context_any_of or tuple(
            fact.key for fact in domain.ordered_facts(getattr(profile, "issue_type", None))
            if fact.purpose == FactPurpose.CORE_CONTEXT
        )
        definitions = {fact.key: fact for fact in domain.facts}
        return any(self._fact_is_known(profile, definitions[key]) for key in context_keys if key in definitions)

    def guidance_possible(self, profile: Any) -> bool:
        if not self.issue_understood(profile) or getattr(profile, "risk_level", "GREEN") == "RED":
            return False
        safety = getattr(profile, "safety_status", None) or {}
        return not (
            safety.get("immediate_danger") is True
            or (safety.get("is_safety_case") and not safety.get("triage_complete"))
        )

    def compact_context(self, profile: Any, *, max_candidates: int = 4) -> dict[str, Any]:
        domain = self.resolve(getattr(profile, "category", None))
        definitions = {fact.key: fact for fact in domain.facts}
        candidates = []
        unavailable = set((getattr(profile, "key_facts", {}) or {}).get("unavailable_fact_keys") or ())
        for fact in self.unresolved_facts(profile, required_only=False):
            if fact.key in unavailable:
                continue
            if fact.purpose in {FactPurpose.ADMINISTRATIVE_IDENTIFIER, FactPurpose.DOCUMENT_ONLY}:
                continue
            if any(self._fact_is_known(profile, definitions[key]) for key in fact.conversation_alternatives):
                continue
            candidates.append(fact)
            if len(candidates) == max_candidates:
                break
        return {
            "domain_id": domain.id,
            "display_name": domain.display_name,
            "issue_type_id": domain.normalize_issue_type(getattr(profile, "issue_type", None)),
            "workflow_binding": domain.workflow_binding,
            "issue_understood": self.issue_understood(profile),
            "guidance_possible": self.guidance_possible(profile),
            "next_fact_candidates": [
                {
                    "key": fact.key,
                    "meaning": fact.meaning,
                    "purpose": fact.purpose.value,
                    "priority_reason": fact.priority.name,
                    "stage": fact.stage.name,
                }
                for fact in candidates
            ],
            "jurisdiction": {
                "requirement": domain.jurisdiction.requirement.value,
                "fact_key": domain.jurisdiction.fact_key,
            },
            "rag": {
                "corpus_ids": list(domain.rag.corpus_ids),
                "requires_state": domain.rag.requires_state,
            },
        }

    def extraction_catalog(self) -> list[dict[str, Any]]:
        return [
            {
                "domain_id": domain.id,
                "issue_type_ids": [issue.id for issue in domain.issue_types],
            }
            for domain in self._definitions.values()
        ]

    def corpus_mapping(self) -> dict[str, str]:
        mapping: dict[str, str] = {}
        for domain in self._definitions.values():
            if domain.rag.corpus_ids:
                mapping[domain.id] = domain.rag.corpus_ids[0]
        for alias, target in self._aliases.items():
            if target in mapping:
                mapping[alias] = mapping[target]
        return mapping

    def validate_integrations(
        self,
        *,
        workflows: dict[str, Any],
        document_ids: set[str],
    ) -> None:
        errors: list[str] = []
        for domain in self._definitions.values():
            if domain.id == "GENERAL":
                continue
            workflow = workflows.get(domain.workflow_binding)
            if workflow is None:
                errors.append(f"{domain.id}: unknown workflow {domain.workflow_binding}")
                continue
            stages = {item.get("id") for item in workflow.get("stages", [])}
            workflow_evidence = {item.get("id") for item in workflow.get("evidence_items", [])}
            for action in domain.actions:
                if action.target_workflow_stage and action.target_workflow_stage not in stages:
                    errors.append(f"{domain.id}: action {action.id} references unknown stage {action.target_workflow_stage}")
            missing_evidence = {item.id for item in domain.evidence} - workflow_evidence
            if missing_evidence:
                errors.append(f"{domain.id}: evidence absent from workflow: {sorted(missing_evidence)}")
            for binding in domain.documents:
                if binding.document_type not in document_ids:
                    errors.append(f"{domain.id}: unknown document {binding.document_type}")
                unknown_stages = set(binding.workflow_stages) - stages
                if unknown_stages:
                    errors.append(f"{domain.id}: document references unknown stages {sorted(unknown_stages)}")
        if errors:
            raise DomainRegistryError("; ".join(errors))

    def validate_corpora(self, corpus_ids: set[str]) -> None:
        unknown = {
            corpus for domain in self._definitions.values()
            for corpus in domain.rag.corpus_ids if corpus not in corpus_ids
        }
        if unknown:
            raise DomainRegistryError(f"unknown RAG corpus IDs: {sorted(unknown)}")


from app.domains.consumer import CONSUMER_DOMAIN
from app.domains.cyber_fraud import CYBER_FRAUD_DOMAIN
from app.domains.employment import EMPLOYMENT_DOMAIN
from app.domains.legacy import GENERAL_DOMAIN, POLICE_COMPLAINT_DOMAIN
from app.domains.tenancy import TENANCY_DOMAIN


domain_registry = DomainRegistry(
    (
        CONSUMER_DOMAIN,
        TENANCY_DOMAIN,
        EMPLOYMENT_DOMAIN,
        CYBER_FRAUD_DOMAIN,
        POLICE_COMPLAINT_DOMAIN,
        GENERAL_DOMAIN,
    ),
    aliases=CATEGORY_ALIASES,
)
