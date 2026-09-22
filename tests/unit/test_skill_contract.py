import re
from pathlib import Path

from mybudongsan.domain.scoring import EvaluationInput
from mybudongsan.research.contracts import (
    DeepAssessmentObservation,
    EvidenceObservation,
    ListingObservation,
    ResearchBundle,
)

ROOT = Path(__file__).resolve().parents[2]
SKILL = ROOT / "skills" / "mybudongsan-pm" / "SKILL.md"
REFERENCES = {
    "researcher": ROOT / "skills" / "mybudongsan-pm" / "references" / "researcher.md",
    "market": ROOT / "skills" / "mybudongsan-pm" / "references" / "specialist-market.md",
    "transit": ROOT / "skills" / "mybudongsan-pm" / "references" / "specialist-transit.md",
    "urban": ROOT
    / "skills"
    / "mybudongsan-pm"
    / "references"
    / "specialist-urban-planning.md",
    "report": ROOT / "skills" / "mybudongsan-pm" / "references" / "report-contract.md",
}


def _contents() -> str:
    paths = (SKILL, *REFERENCES.values())
    missing = [str(path.relative_to(ROOT)) for path in paths if not path.is_file()]
    assert not missing, f"missing MyBudongsan skill contracts: {missing}"
    return "\n".join(path.read_text(encoding="utf-8") for path in paths)


def _frontmatter(text: str) -> dict[str, str]:
    lines = text.splitlines()
    assert lines[0] == "---"
    assert "---" in lines[1:], "frontmatter must close"
    closing = lines.index("---", 1)
    fields: dict[str, str] = {}
    for line in lines[1:closing]:
        key, separator, value = line.partition(": ")
        assert separator and key and value, f"invalid YAML scalar: {line!r}"
        assert key not in fields, f"duplicate YAML field: {key}"
        fields[key] = value
    return fields


def _normalized(text: str) -> str:
    return " ".join(text.split())


def test_mybudongsan_pm_skill_contract() -> None:
    text = _contents()

    assert text.startswith("---\nname: mybudongsan-pm\ndescription: Use when")
    assert "PM alone communicates conclusions to the user" in text
    assert "one integrated researcher" in text
    assert "at most one conditional specialist" in text
    assert "read and use the installed `ego-browser` skill" in text
    assert "official sources first" in text
    assert "확정, 추진, 검토" in text
    assert "not an active listing until its individual listing page is checked" in text
    assert "valid `ResearchBundle` JSON" in text
    assert "never auto-relaxes mandatory criteria" in text
    assert "only `PlayMCP:MemoChat`" in text
    assert "only after confirmed delivery" in text
    assert "event_id + claim_token" in text
    assert "notify status" in text
    assert "never blind ack or retry" in _normalized(text).lower()
    assert "mybudongsan run resume" in text
    assert "SQLite is canonical" in text
    assert "maximum of five lifecycle event types" in text
    assert "Python never calls PlayMCP" in text
    assert "recommend, hold, exclude, or no_recommendation" in text


def test_pm_discovery_target_and_lifecycle_contract() -> None:
    pm = SKILL.read_text(encoding="utf-8")
    researcher = REFERENCES["researcher"].read_text(encoding="utf-8")

    for role in (pm, researcher):
        normalized = _normalized(role)
        assert "target 15–25 when evidence exists" in normalized
        assert "hard cap of 25" in normalized
        assert "report the actual count and shortage reason" in normalized
        assert "do not invent listings or relax criteria" in normalized
    assert "NEEDS_ACTION" in pm


def test_frontmatter_is_valid_minimal_yaml_and_trigger_only() -> None:
    fields = _frontmatter(SKILL.read_text(encoding="utf-8"))

    assert set(fields) == {"name", "description"}
    assert re.fullmatch(r"[a-z0-9]+(?:-[a-z0-9]+)*", fields["name"])
    assert fields["description"].startswith("Use when")
    assert "->" not in fields["description"]
    assert not re.search(r"\b(?:must|first|then|dispatch|acknowledge)\b", fields["description"])


def test_role_ownership_and_prohibitions_have_no_conflicting_permission() -> None:
    pm = SKILL.read_text(encoding="utf-8")
    researcher = REFERENCES["researcher"].read_text(encoding="utf-8")
    specialists = {
        name: REFERENCES[name].read_text(encoding="utf-8")
        for name in ("market", "transit", "urban")
    }

    assert "PM alone communicates conclusions to the user" in pm
    assert "at most one conditional specialist" in pm
    assert "dispatch none of them" in pm
    assert "Do not relabel them" in pm
    assert "only `PlayMCP:MemoChat`" in pm
    assert "never auto-relaxes mandatory criteria" in pm
    assert not re.search(
        r"\b(?:may|can|should) dispatch (?:two|three|multiple)", pm, re.IGNORECASE
    )
    assert not re.search(r"\b(?:may|can|should) auto-relax", pm, re.IGNORECASE)
    assert not re.search(
        r"\b(?:another|alternate) PlayMCP tool (?:is )?allowed", pm, re.IGNORECASE
    )
    assert set(re.findall(r"PlayMCP:[A-Za-z0-9_:-]+", pm)) == {"PlayMCP:MemoChat"}

    for name, role in {"researcher": researcher, **specialists}.items():
        assert "Do not communicate conclusions to the user" in role, name
        assert "call PlayMCP" in role and "Do not" in role, name
        assert "auto-relax criteria" in role, name
        assert not re.search(
            r"\b(?:may|can|should) communicate conclusions", role, re.IGNORECASE
        ), name
        assert not re.search(r"\b(?:may|can|should) auto-relax", role, re.IGNORECASE), name
        assert not re.search(
            r"\b(?:may|can|should) use (?:another|alternate) PlayMCP", role, re.IGNORECASE
        ), name


def test_researcher_has_unambiguous_success_and_blocker_envelopes() -> None:
    researcher = REFERENCES["researcher"].read_text(encoding="utf-8")

    assert "Success output — ResearchBundle JSON only" in researcher
    assert "after browser evidence is captured" in researcher
    assert "Blocked output — JSON control envelope only" in researcher
    for field in ("status", "stage", "reason", "required_user_action", "safe_context"):
        assert f'"{field}"' in researcher
    assert '"status": "blocked"' in researcher
    assert "must not return a ResearchBundle" in researcher
    assert "must not communicate with the user" in researcher


def test_researcher_documents_the_actual_research_bundle_schema() -> None:
    researcher = REFERENCES["researcher"].read_text(encoding="utf-8")

    for name in ResearchBundle.model_fields:
        assert f"`{name}`" in researcher
    for name, field in ListingObservation.model_fields.items():
        if field.is_required():
            assert f"`{name}`" in researcher
    for name, field in EvidenceObservation.model_fields.items():
        if field.is_required():
            assert f"`{name}`" in researcher
    for name, field in DeepAssessmentObservation.model_fields.items():
        if field.is_required():
            assert f"`{name}`" in researcher
    for name, field in EvaluationInput.model_fields.items():
        if field.is_required():
            assert f"`{name}`" in researcher


def test_timeout_recovery_uses_only_the_claim_token_cli_contract() -> None:
    pm = SKILL.read_text(encoding="utf-8")

    assert "`notify ack` has no `--status` option" in pm
    assert "leave the claimed event `dispatching`" in pm
    assert "never use an alternate provider or PlayMCP tool" in _normalized(pm)
    assert "confirmed non-delivery permits `notify fail`" in pm
    assert "new claim token" in pm
    assert "stop Kakao delivery and request setup" in _normalized(pm)
    assert "Do not use `mybudongsan notify ack EVENT_ID --claim-token CLAIM_TOKEN`" in pm
    assert "requires both `--provider-id` and `--claim-token`" in pm
    assert "After `notify fail`, stop that recovery turn; do not send" in pm


def test_every_browser_role_requires_ego_browser_and_a_bounded_output() -> None:
    researcher = REFERENCES["researcher"].read_text(encoding="utf-8")
    specialists = (
        REFERENCES["market"],
        REFERENCES["transit"],
        REFERENCES["urban"],
    )

    assert "Success output — ResearchBundle JSON only" in researcher
    assert "individual listing page is checked" in _normalized(researcher)
    for role in (researcher, *(path.read_text(encoding="utf-8") for path in specialists)):
        assert "read and use the installed `ego-browser` skill" in role
        assert "login, CAPTCHA" in role and "permission" in role
    for specialist in (path.read_text(encoding="utf-8") for path in specialists):
        assert "Answer only that question" in specialist
        assert "do not redo the full" in specialist
