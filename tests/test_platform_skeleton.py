# tests/test_platform_skeleton.py
import pathlib

import yaml

from ledger_sense.notice import NON_AFFILIATION_NOTICE

ROOT = pathlib.Path(__file__).resolve().parent.parent


def test_the_notice_has_exactly_one_definition() -> None:
    """F-55: two documents each hardcoded their own wording; both asserted it."""
    hits = []
    for path in ROOT.rglob("*.py"):
        # notice.py: the one legitimate definition. This test file: its own
        # source line below necessarily contains the search string itself.
        if ".venv" in path.parts or path.name in ("notice.py", "test_platform_skeleton.py"):
            continue
        if "not developed, endorsed, approved" in path.read_text(encoding="utf-8"):
            hits.append(str(path))
    assert hits == [], f"notice text duplicated outside notice.py: {hits}"


def test_compose_defines_both_client_and_demo_profiles() -> None:
    compose = yaml.safe_load((ROOT / "docker-compose.yml").read_text(encoding="utf-8"))
    profiles = {p for svc in compose["services"].values() for p in svc.get("profiles", [])}
    assert {"client", "demo"} <= profiles


def test_the_client_profile_uses_no_third_party_processor() -> None:
    """Spec §2.1 Q6: CLIENT = compose-only Postgres, no third-party processor ever."""
    compose = yaml.safe_load((ROOT / "docker-compose.yml").read_text(encoding="utf-8"))
    for name, svc in compose["services"].items():
        if "client" in svc.get("profiles", []):
            image = svc.get("image", "")
            assert not any(h in image for h in ("supabase", "planetscale", "neon")), name


def test_env_example_contains_no_real_secret() -> None:
    for line in (ROOT / ".env.example").read_text(encoding="utf-8").splitlines():
        if "=" not in line or line.startswith("#"):
            continue
        _, _, value = line.partition("=")
        assert value.strip() in {"", "changeme"} or value.strip().startswith("<"), line


def test_gitleaks_is_wired_into_pre_commit_and_ci() -> None:
    """Hard constraint 00 §3.4. Named in the Global Constraints, implemented nowhere."""
    assert "gitleaks" in (ROOT / ".pre-commit-config.yaml").read_text(encoding="utf-8")
    assert "gitleaks" in (ROOT / ".github/workflows/ci.yml").read_text(encoding="utf-8")


def test_dependency_audit_and_sbom_jobs_exist() -> None:
    """00 §8. pip-audit, npm audit, cyclonedx — none appeared in any task."""
    ci = (ROOT / ".github/workflows/ci.yml").read_text(encoding="utf-8")
    for tool in ("pip-audit", "npm audit", "cyclonedx"):
        assert tool in ci, f"{tool} missing from CI"


def test_gitleaks_scans_the_recorded_cassettes() -> None:
    """F-28: VCR does not scrub response bodies, and that is where the token lands."""
    assert "tests/writeback/cassettes" in (
        ROOT / ".github/workflows/ci.yml").read_text(encoding="utf-8")


def test_the_container_runs_as_a_non_root_user() -> None:
    dockerfile = (ROOT / "Dockerfile").read_text(encoding="utf-8")
    assert "USER " in dockerfile
    assert "USER root" not in "\n".join(dockerfile.splitlines()[-5:])


def test_no_module_opens_a_database_connection_directly() -> None:
    """Rule 3. One connection factory; F-19 is what happens without it."""
    offenders = []
    for path in (ROOT / "ledger_sense").rglob("*.py"):
        if path.parts[-2:] == ("store", "sqlite.py") or path.name == "session.py":
            continue
        text = path.read_text(encoding="utf-8")
        if "sqlite3.connect" in text or "psycopg.connect" in text:
            offenders.append(str(path))
    assert offenders == [], f"direct connection outside the factory: {offenders}"


def test_the_licence_is_apache_2_0() -> None:
    assert "Apache License" in (ROOT / "LICENSE").read_text(encoding="utf-8")


def test_the_readme_carries_the_notice_from_the_single_source() -> None:
    assert NON_AFFILIATION_NOTICE in (ROOT / "README.md").read_text(encoding="utf-8")


def test_ledger_sense_runs_as_a_single_process() -> None:
    """Phase 6 Ruling 3: writeback/qbo.py's in-process ConcurrentWriteInProgressError
    guard (an in-memory, per-process set) only closes the concurrent-write
    race within one process. Pinning WORKERS as a source-level constant means
    raising it later is a deliberate, reviewed change — not silent drift from
    an unenforced comment."""
    from ledger_sense.__main__ import WORKERS
    assert WORKERS == 1
