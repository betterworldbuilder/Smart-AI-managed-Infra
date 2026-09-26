"""Natural language -> intent, and the "ask only what matters" rule."""

from __future__ import annotations

import pytest

from app.copilot.extraction import extract_intent, parse_answer
from app.copilot.wizard import Wizard
from app.governor import load_profiles
from app.models import (
    Environment,
    IsolationLevel,
    NetworkExposure,
    Sensitivity,
    WorkloadIntent,
)

PROFILES = load_profiles()
GPU_MODELS = ("A10", "L40S", "H100")


def extract(text: str):
    return extract_intent(text, PROFILES, GPU_MODELS)


def test_llama_request_is_understood():
    patch = extract(
        "I need a private AI server for 50 employees running Llama with "
        "confidential company data."
    )
    assert patch.workload_type == "llm-inference"
    assert patch.gpu_required is True
    assert patch.expected_users == 50
    assert patch.sensitive_data is Sensitivity.CONFIDENTIAL
    assert patch.name == "llama-internal"


def test_concurrency_is_not_confused_with_headcount():
    patch = extract(
        "Deploy Llama inference for 100 internal employees, about 25 concurrent users at peak."
    )
    assert patch.concurrent_users == 25
    assert patch.expected_users == 100


def test_legacy_vm_request_is_not_container_compatible():
    patch = extract(
        "I need an Ubuntu 22.04 virtual machine to run a legacy vendor application. "
        "It cannot run in a container."
    )
    assert patch.container_compatible is False
    assert patch.operating_system.startswith("ubuntu")


def test_isolation_and_ha_phrases():
    patch = extract("Deploy an isolated PyTorch development workstation for one ML engineer.")
    assert patch.workload_isolation is IsolationLevel.HIGH
    assert patch.gpu_required is True

    patch = extract("Create a highly available PostgreSQL database for production.")
    assert patch.high_availability is True
    assert patch.workload_type == "database"
    assert patch.environment is Environment.PROD


def test_storage_is_not_mistaken_for_memory():
    patch = extract("A database with 500 GB of data and 64 GB RAM")
    assert patch.storage_gb == 500
    assert patch.estimated_ram_gb == 64


def test_public_exposure_is_detected():
    patch = extract("A web api that must be internet facing for external customers")
    assert patch.internet_access is True
    assert patch.network_exposure is NetworkExposure.PUBLIC


def test_gpu_model_preference_is_discovered_not_hardcoded():
    patch = extract("Give me a workload on an H100 please")
    assert patch.preferred_gpu == "H100"
    # A model the catalog does not know must not be invented.
    patch = extract("Give me a workload on a B200 please")
    assert patch.preferred_gpu is None


@pytest.mark.parametrize(
    ("field", "raw", "expected"),
    [
        ("concurrent_users", "about 20 users", 20),
        ("storage_gb", "2 TB", 2048),
        ("high_availability", "yes", True),
        ("high_availability", "no", False),
        ("environment", "production", Environment.PROD),
        ("sensitive_data", "confidential", Sensitivity.CONFIDENTIAL),
        ("workload_isolation", "high", IsolationLevel.HIGH),
    ],
)
def test_answer_parsing(field, raw, expected):
    assert parse_answer(field, raw) == expected


def test_wizard_asks_only_what_changes_the_decision():
    wizard = Wizard(PROFILES)
    intent = WorkloadIntent(
        workload_type="llm-inference", gpu_required=True, expected_users=50
    )
    questions = wizard.next_questions(intent, provided={"workload_type", "expected_users"})
    fields = [question.field for question in questions]

    assert "concurrent_users" in fields
    assert len(questions) <= 2, "a copilot must not interrogate the operator"
    assert not wizard.ready(intent, provided=set())

    answered = intent.model_copy(update={"concurrent_users": 20})
    provided = {"workload_type", "expected_users", "concurrent_users", "environment", "sensitive_data"}
    assert wizard.ready(answered, provided=provided)


def test_wizard_skips_irrelevant_questions():
    wizard = Wizard(PROFILES)
    intent = WorkloadIntent(workload_type="web-api", gpu_required=False)
    fields = {spec.field for spec in wizard.pending(intent, provided=set())}
    # Storage sizing and GPU count are not relevant to a stateless web API.
    assert "storage_gb" not in fields
    assert "gpu_count" not in fields
    assert "workload_isolation" not in fields
