"""Foundation track sanity checks: agents, skills, memory, AGENTS.md (filesystem only)."""

from __future__ import annotations

from pathlib import Path


def _repo_root() -> Path:
    current = Path(__file__).resolve()
    for candidate in (current.parent, *current.parents):
        if (candidate / "pyproject.toml").is_file():
            return candidate
    raise AssertionError("repo root not found")


def _frontmatter(text: str) -> dict[str, str]:
    if not text.startswith("---"):
        return {}
    end = text.find("---", 3)
    if end == -1:
        return {}
    block = text[3:end]
    data: dict[str, str] = {}
    for line in block.splitlines():
        if ":" in line:
            key, _, value = line.partition(":")
            data[key.strip()] = value.strip()
    return data


def test_agent_files_exist_with_description() -> None:
    """At least 10 agent files exist, each with a description frontmatter."""
    agents_dir = _repo_root() / ".opencode" / "agents"
    assert agents_dir.is_dir(), "missing .opencode/agents"
    files = sorted(agents_dir.glob("*.md"))
    assert len(files) >= 10, f"expected >=10 agents, found {len(files)}"
    for path in files:
        meta = _frontmatter(path.read_text(encoding="utf-8"))
        assert meta.get("description"), f"{path.name} missing description frontmatter"


def test_skill_files_exist_with_name_and_description() -> None:
    """At least 12 skills exist, each SKILL.md with name/description frontmatter."""
    skills_dir = _repo_root() / ".opencode" / "skills"
    assert skills_dir.is_dir(), "missing .opencode/skills"
    skill_files = sorted(skills_dir.glob("*/SKILL.md"))
    assert len(skill_files) >= 12, f"expected >=12 skills, found {len(skill_files)}"
    for path in skill_files:
        assert path.parent.name, "skill dir missing"
        meta = _frontmatter(path.read_text(encoding="utf-8"))
        assert meta.get("name"), f"{path} missing name frontmatter"
        assert meta.get("description"), f"{path} missing description frontmatter"


def test_memory_files_exist() -> None:
    """All four memory files exist."""
    memory_dir = _repo_root() / "memory"
    for name in ("CURRENT_STATE.md", "NEXT_STEPS.md", "SESSION_LOG.md", "PROJECT_MEMORY.md"):
        assert (memory_dir / name).is_file(), f"missing memory/{name}"


def test_agents_md_mentions_phase1() -> None:
    """AGENTS.md exists and scopes the work to Phase 1."""
    text = (_repo_root() / "AGENTS.md").read_text(encoding="utf-8")
    assert "Phase 1" in text
