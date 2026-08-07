"""The runtime identity contract, written against the failure it prevents.

On 2026-08-07 the Claude Code hook wrote and read under `local` / `<cwd>` while
the CLI had been writing under `locaith` / `locaith-intelligence-os` since the
day before. Three unrelated queries returned the same four useless rows. The
first diagnosis was a broken ranker or a full-text index that could not handle
Vietnamese; both were wrong. Retrieval was fine. Isolation was fine. The two
halves of one system simply disagreed about which partition they were in, and a
correct partition boundary is indistinguishable from a broken index when you are
looking at it through a single query.

So these tests pin two things that sound opposed and are not:

* every entry point resolves the *same* scope from the same inputs, and
* a genuinely different tenant or workspace still sees nothing.

The second half matters as much as the first. A test suite that only proved
"the hook can now see the CLI's memories" would pass just as happily if
isolation had been deleted.
"""

from __future__ import annotations

import json
import sqlite3

import pytest

from bio_agent_os.cognitive.facade import MemoryOS
from bio_agent_os.cognitive.models import AccessContext, MemoryType
from bio_agent_os.cognitive.scope import (
    ENV_STRATEGY,
    ENV_TENANT,
    ENV_WORKSPACE,
    FALLBACK_TENANT_ID,
    FALLBACK_WORKSPACE_ID,
    LEGACY_ENV_TENANT,
    LEGACY_ENV_WORKSPACE,
    MemoryScope,
    ScopeSource,
    WorkspaceStrategy,
    resolve_scope,
)
from bio_agent_os.cognitive.scope_doctor import diagnose


# ==========================================================================
# precedence — section 5 of the Run 8 gate
# ==========================================================================

def test_precedence_runs_explicit_then_env_then_legacy_then_profile_then_fallback(tmp_path):
    profile = tmp_path / "profile.json"
    profile.write_text(json.dumps({"tenant_id": "from-profile",
                                   "workspace_id": "ws-profile"}), encoding="utf-8")
    env = {ENV_TENANT: "from-env", ENV_WORKSPACE: "ws-env",
           LEGACY_ENV_TENANT: "from-legacy", LEGACY_ENV_WORKSPACE: "ws-legacy"}

    explicit = resolve_scope(tenant_id="from-arg", workspace_id="ws-arg",
                             env=env, profile_path=profile)
    assert (explicit.tenant_id, explicit.workspace_id) == ("from-arg", "ws-arg")
    assert explicit.tenant_source == ScopeSource.EXPLICIT.value

    from_env = resolve_scope(env=env, profile_path=profile)
    assert (from_env.tenant_id, from_env.workspace_id) == ("from-env", "ws-env")
    assert from_env.tenant_source == ScopeSource.ENV.value

    legacy_only = {LEGACY_ENV_TENANT: "from-legacy", LEGACY_ENV_WORKSPACE: "ws-legacy"}
    from_legacy = resolve_scope(env=legacy_only, profile_path=profile)
    assert (from_legacy.tenant_id, from_legacy.workspace_id) == ("from-legacy", "ws-legacy")
    assert from_legacy.tenant_source == ScopeSource.LEGACY_ENV.value

    from_profile = resolve_scope(env={}, profile_path=profile)
    assert (from_profile.tenant_id, from_profile.workspace_id) == ("from-profile", "ws-profile")
    assert from_profile.tenant_source == ScopeSource.PROFILE.value

    bare = resolve_scope(env={}, profile_path=tmp_path / "does-not-exist.json")
    assert (bare.tenant_id, bare.workspace_id) == (FALLBACK_TENANT_ID, FALLBACK_WORKSPACE_ID)
    assert bare.tenant_source == ScopeSource.FALLBACK.value


def test_the_working_directory_is_never_a_silent_workspace(tmp_path):
    """The specific default that caused the incident.

    An unconfigured process must land on a documented name, not on wherever it
    happened to be started. Path scoping fragments memory once per directory,
    and doing that without being asked is how thirty memories went missing.
    """
    scope = resolve_scope(env={}, project_path=tmp_path,
                          profile_path=tmp_path / "none.json")
    assert scope.workspace_id == FALLBACK_WORKSPACE_ID
    assert str(tmp_path) not in scope.workspace_id
    assert not scope.workspace_looks_like_a_path


def test_path_scoping_is_available_but_only_when_asked_for(tmp_path):
    scope = resolve_scope(env={}, project_path=tmp_path,
                          workspace_strategy=WorkspaceStrategy.PROJECT_PATH,
                          profile_path=tmp_path / "none.json")
    assert scope.workspace_source == ScopeSource.PROJECT_PATH.value
    assert tmp_path.name in scope.workspace_id
    # Stable for the same directory, distinct for a different one.
    again = resolve_scope(env={}, project_path=tmp_path,
                          workspace_strategy=WorkspaceStrategy.PROJECT_PATH,
                          profile_path=tmp_path / "none.json")
    other = resolve_scope(env={}, project_path=tmp_path / "sub",
                          workspace_strategy=WorkspaceStrategy.PROJECT_PATH,
                          profile_path=tmp_path / "none.json")
    assert scope.workspace_id == again.workspace_id
    assert scope.workspace_id != other.workspace_id


def test_the_strategy_can_be_set_from_the_environment(tmp_path):
    scope = resolve_scope(env={ENV_STRATEGY: "project_path"}, project_path=tmp_path,
                          profile_path=tmp_path / "none.json")
    assert scope.strategy == WorkspaceStrategy.PROJECT_PATH.value
    assert scope.workspace_source == ScopeSource.PROJECT_PATH.value


def test_an_unparsable_profile_is_ignored_loudly_not_obeyed_quietly(tmp_path, caplog):
    bad = tmp_path / "profile.json"
    bad.write_text("{not json at all", encoding="utf-8")
    scope = resolve_scope(env={}, profile_path=bad)
    assert scope.tenant_id == FALLBACK_TENANT_ID
    assert any("profile" in r.message for r in caplog.records)


# ==========================================================================
# fingerprint — section 6
# ==========================================================================

def test_the_fingerprint_identifies_the_partition_and_nothing_else():
    a = resolve_scope(tenant_id="t", workspace_id="w", env={})
    b = resolve_scope(tenant_id="t", workspace_id="w", env={ENV_STRATEGY: "project_path"})
    c = resolve_scope(tenant_id="t", workspace_id="other", env={})
    assert a.fingerprint == b.fingerprint, (
        "the fingerprint must depend on the partition, not on how it was reached")
    assert a.fingerprint != c.fingerprint
    # It is a digest, so neither name leaks into it verbatim.
    assert "w" != a.fingerprint and len(a.fingerprint) == 12


# ==========================================================================
# end to end — section 9
# ==========================================================================

@pytest.fixture()
def db(tmp_path):
    return tmp_path / "memory.db"


def _remember(os_: MemoryOS, scope: MemoryScope, content: str) -> None:
    event = os_.observe(tenant_id=scope.tenant_id, actor="a", source="unit",
                        content=content, workspace_id=scope.workspace_id)
    os_.remember(event=event, memory_type=MemoryType.SEMANTIC, content=content)


def _recall(os_: MemoryOS, scope: MemoryScope, query: str) -> list[str]:
    results = os_.recall(query, context=AccessContext(
        tenant_id=scope.tenant_id, workspace_id=scope.workspace_id, agent_id="test"))
    return [str(r.memory.content) for r in results]


def test_what_one_entrypoint_writes_another_recalls(db):
    """The path that was broken: write through one door, read through another.

    Both doors resolve through `resolve_scope`, from the same environment, so
    they land in the same partition — which is the entire contract.
    """
    env = {ENV_TENANT: "locaith", ENV_WORKSPACE: "locaith-intelligence-os"}
    writer_scope = resolve_scope(env=env)               # stands in for the CLI
    reader_scope = resolve_scope(env=env, project_path=r"c:\somewhere\else")  # the hook

    assert writer_scope.fingerprint == reader_scope.fingerprint, (
        "two entry points reading the same environment disagreed about the partition")

    os_ = MemoryOS(db)
    _remember(os_, writer_scope, "tài khoản công ty là Techcombank 19040131667011")
    found = _recall(os_, reader_scope, "tài khoản công ty Techcombank")
    assert any("Techcombank" in f for f in found), (
        f"the second entry point could not see what the first wrote: {found}")


def test_the_hook_default_no_longer_lands_in_its_own_corner(db, tmp_path):
    """Regression on the incident itself.

    With nothing configured, the hook used to resolve to `local` / `<cwd>`. Two
    processes started in two directories therefore had two memories. Now an
    unconfigured hook lands on the documented fallback, the same one everybody
    else lands on.
    """
    a = resolve_scope(env={}, project_path=tmp_path / "project-a",
                      profile_path=tmp_path / "none.json")
    b = resolve_scope(env={}, project_path=tmp_path / "project-b",
                      profile_path=tmp_path / "none.json")
    assert a.fingerprint == b.fingerprint, (
        "two directories still produce two different memories")


# -- and isolation still isolates ------------------------------------------

def test_a_different_tenant_sees_nothing(db):
    os_ = MemoryOS(db)
    mine = resolve_scope(tenant_id="locaith", workspace_id="ws", env={})
    theirs = resolve_scope(tenant_id="another-company", workspace_id="ws", env={})
    _remember(os_, mine, "doanh thu định kỳ 9.960.000đ mỗi tháng")

    assert _recall(os_, mine, "doanh thu định kỳ"), "the owner cannot see their own memory"
    assert _recall(os_, theirs, "doanh thu định kỳ") == [], (
        "another tenant could read it — this is the failure that matters most")


def test_a_different_workspace_sees_nothing(db):
    os_ = MemoryOS(db)
    mine = resolve_scope(tenant_id="locaith", workspace_id="customer-archilab", env={})
    other = resolve_scope(tenant_id="locaith", workspace_id="customer-reti", env={})
    _remember(os_, mine, "đơn giá ARCHILAB xây trọn gói 6,0-6,9 triệu mỗi m2")

    assert _recall(os_, mine, "đơn giá ARCHILAB")
    assert _recall(os_, other, "đơn giá ARCHILAB") == [], (
        "one customer's workspace leaked into another's")


# ==========================================================================
# the doctor — section 7
# ==========================================================================

def test_the_doctor_names_a_scope_mismatch_and_not_a_retrieval_failure(db):
    """The exact shape of the incident, reproduced and caught.

    Thirty memories in one partition, seven in the one the process resolved to.
    The report has to say SCOPE_CONFIGURATION_MISMATCH, because "recall returned
    nothing" sends the reader looking at the retriever, and the retriever is
    fine.
    """
    os_ = MemoryOS(db)
    real = resolve_scope(tenant_id="locaith", workspace_id="locaith-intelligence-os", env={})
    junk = resolve_scope(env={LEGACY_ENV_TENANT: "local",
                              LEGACY_ENV_WORKSPACE: r"c:\locaith\some project"})
    for i in range(30):
        _remember(os_, real, f"ký ức thật số {i}")
    for i in range(7):
        _remember(os_, junk, f"hook=SessionStart {i}")

    report = diagnose(sqlite3.connect(db), junk)
    codes = {f["code"] for f in report.findings}
    assert "SCOPE_CONFIGURATION_MISMATCH" in codes, codes
    assert "WORKSPACE_ID_IS_A_PATH" in codes, codes
    assert report.exit_code == 1
    assert report.resolved_memories == 7
    mismatch = next(f for f in report.findings if f["code"] == "SCOPE_CONFIGURATION_MISMATCH")
    assert mismatch["largest_other"]["memories"] == 30


def test_the_doctor_is_quiet_when_the_scope_is_the_only_one(db):
    os_ = MemoryOS(db)
    scope = resolve_scope(tenant_id="locaith", workspace_id="locaith-intelligence-os", env={})
    for i in range(12):
        _remember(os_, scope, f"ký ức {i}")
    report = diagnose(sqlite3.connect(db), scope)
    assert {f["code"] for f in report.findings} == {"SCOPE_CONSISTENT"}
    assert report.exit_code == 0
