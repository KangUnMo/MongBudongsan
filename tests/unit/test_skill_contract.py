from pathlib import Path

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


def test_mybudongsan_pm_skill_contract() -> None:
    text = _contents()

    assert text.startswith("---\nname: mybudongsan-pm\ndescription: Use when")
    assert "PM alone communicates conclusions to the user" in text
    assert "one integrated researcher" in text
    assert "at most one conditional specialist" in text
    assert "read and use the installed `ego-browser` skill" in text
    assert "discovered: 25" in text
    assert "verified: 7" in text
    assert "deep assessments: 3" in text
    assert "official sources first" in text
    assert "확정, 추진, 검토" in text
    assert "not an active listing until its individual listing page is checked" in text
    assert "valid `ResearchBundle` JSON" in text
    assert "never auto-relaxes mandatory criteria" in text
    assert "only `PlayMCP:MemoChat`" in text
    assert "only after confirmed delivery" in text
    assert "event_id + claim_token" in text
    assert "notify status" in text
    assert "never blind ack or retry" in text
    assert "mybudongsan run resume" in text
    assert "SQLite is canonical" in text
    assert "maximum of five lifecycle event types" in text
    assert "Python never calls PlayMCP" in text
    assert "recommend, hold, exclude, or no_recommendation" in text


def test_every_browser_role_requires_ego_browser_and_a_bounded_output() -> None:
    researcher = REFERENCES["researcher"].read_text(encoding="utf-8")
    specialists = (
        REFERENCES["market"],
        REFERENCES["transit"],
        REFERENCES["urban"],
    )

    assert "Output only valid `ResearchBundle` JSON" in researcher
    assert "individual listing\npage is checked" in researcher
    for role in (researcher, *(path.read_text(encoding="utf-8") for path in specialists)):
        assert "read and use the installed `ego-browser` skill" in role
        assert "login, CAPTCHA" in role and "permission" in role
    for specialist in (path.read_text(encoding="utf-8") for path in specialists):
        assert "Answer only that question" in specialist
        assert "do not redo the full" in specialist
