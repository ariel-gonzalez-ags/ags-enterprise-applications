#!/usr/bin/env python3
"""Azure executor + lifecycle test with the Azure SDK mocked out. No real
calls, no spend: fakes stand in for the management clients so the full
provision -> agent -> teardown flow and the tagging/cost model are verified
offline.

Usage: python3 scripts/test_azure_executor.py
"""
import asyncio
import os
import sys
import tempfile
from unittest import mock

sys.path.insert(0, "api")
_tmp = tempfile.mkdtemp(prefix="ags-az-")
os.environ["DB_PATH"] = os.path.join(_tmp, "t.db")

from app.config import load
from app.executors.base import RunPayload
from app.executors.azure_exec import lifecycle, tags as tagger


def _settings(**over):
    os.environ.update({
        "AZURE_SUBSCRIPTION_ID": "sub-1", "AZURE_TENANT_ID": "ten-1",
        "AZURE_CLIENT_ID": "cli-1", "AZURE_CLIENT_SECRET": "sec-1",
        "AZURE_LOCATION": "eastus",
    })
    os.environ.update(over)
    return load()


class _FakeRG:
    def __init__(self): self.created = {}; self.deleted = []
    def create_or_update(self, name, body): self.created[name] = body
    def begin_delete(self, name):
        self.deleted.append(name)
        return mock.Mock(result=lambda: None)


class _FakeMSI:
    def create_or_update(self, rg, name, body):
        return mock.Mock(id=f"/msi/{name}", client_id="cid-1",
                         principal_id="pid-1")


class _FakeAuth:
    def __init__(self): self.assignments = []
    @property
    def role_assignments(self): return self
    def create(self, scope, role_assignment_name, parameters):
        self.assignments.append({"scope": scope, "params": parameters})
    def list_for_scope(self, scope):
        # Mirror the real shape: objects with principal_id at this scope.
        return [mock.Mock(principal_id=a["params"]["principal_id"])
                for a in self.assignments if a["scope"] == scope]


class _FakeACIContainers:
    def __init__(self, log_text=""): self._log = log_text
    def list_logs(self, rg, group, container):
        return mock.Mock(content=self._log)


class _FakeACI:
    def __init__(self, log_text="", state="Succeeded", exit_code=0):
        self.groups = []
        self._containers = _FakeACIContainers(log_text)
        self._state = state       # container runtime state (Running/Terminated)
        self._exit = exit_code
    @property
    def container_groups(self): return self
    @property
    def containers(self): return self._containers
    def begin_create_or_update(self, rg, name, body):
        self.groups.append({"rg": rg, "name": name, "body": body})
        return mock.Mock(result=lambda: None)
    def get(self, rg, name):
        # Model the real shape: group provisioning is Succeeded immediately;
        # the container's instance current_state carries runtime + exit code.
        runtime = "Terminated" if self._state in ("Succeeded", "Failed") else self._state
        cur = mock.Mock(state=runtime, exit_code=self._exit)
        container = mock.Mock(instance_view=mock.Mock(current_state=cur))
        return mock.Mock(provisioning_state="Succeeded", containers=[container])


def _fake_az(log_text="", state="Succeeded", exit_code=0):
    return {
        "resource": mock.Mock(resource_groups=_FakeRG()),
        "msi": mock.Mock(user_assigned_identities=_FakeMSI()),
        "auth": _FakeAuth(),
        "aci": _FakeACI(log_text, state, exit_code),
    }


def test_tags():
    t = tagger.sandbox_tags(task_id="t1", owner_sub="u1", org_id="org1",
                            run_id="r1", ttl_minutes=60)
    for key in ("ags:managed-by", "ags:task-id", "ags:org-id", "ags:owner-sub",
                "ags:ttl-minutes"):
        assert key in t, f"missing tag {key}"
    assert t["ags:managed-by"] == "agisphire"
    assert tagger.sandbox_rg_name("t1") == "ags-sb-t1"
    print("ok    tagging model: chargeback tags + sandbox naming")


def test_provision_scopes_identity_to_rg():
    s = _settings()
    az = _fake_az()
    sb = lifecycle.Sandbox("task-abc", "owner-1", "org-1", 60)
    lifecycle.provision(az, s, sb)
    # RG created with tags
    rg = az["resource"].resource_groups.created[sb.rg_name]
    assert rg["tags"]["ags:task-id"] == "task-abc"
    # role assignment scoped to the RG, not the subscription
    asn = az["auth"].assignments[0]
    assert asn["scope"].endswith(f"resourceGroups/{sb.rg_name}"), asn["scope"]
    assert "roleDefinitions" in asn["params"]["role_definition_id"]
    print("ok    provision: tagged RG + identity Contributor scoped to its RG")


def test_launch_attaches_identity():
    s = _settings()
    az = _fake_az()
    sb = lifecycle.provision(az, s, lifecycle.Sandbox("t2", "o", "org", 30))
    lifecycle.launch_agent(az, s, sb, image="img:1", env={"K": "v"},
                           command=["sh", "-c", "run"])
    grp = az["aci"].groups[0]
    assert grp["body"]["identity"]["type"] == "UserAssigned"
    assert sb.identity_id in grp["body"]["identity"]["user_assigned_identities"]
    assert grp["body"]["restart_policy"] == "Never"  # one-shot sandbox
    print("ok    launch: ACI runs with the per-RG managed identity, one-shot")


def test_teardown_returns_cost_event():
    s = _settings()
    az = _fake_az()
    sb = lifecycle.provision(az, s, lifecycle.Sandbox("t3", "owner-9", "org-9", 45))
    proof = lifecycle.teardown(az, s, sb)
    assert sb.rg_name in az["resource"].resource_groups.deleted
    assert proof["teardown_complete"] is True
    assert proof["org_id"] == "org-9" and proof["owner_sub"] == "owner-9"
    assert proof["resource_group"] == sb.rg_name
    assert proof["duration_seconds"] >= 0
    print("ok    teardown: RG deleted + cost/teardown proof row produced")


def test_azure_configured_gate():
    s = _settings()
    assert s.azure_configured is True
    s2 = _settings(AZURE_CLIENT_SECRET="")
    assert s2.azure_configured is False
    print("ok    azure_configured: true only when all four values present")


def test_executor_selected_for_azure():
    s = _settings(EXECUTOR_BACKEND="azure")
    from app.executors import get_executor
    from app.executors.azure_exec import AzureExecutor
    assert isinstance(get_executor(s), AzureExecutor)
    print("ok    get_executor: azure backend -> AzureExecutor")


async def test_agent_loop_declares_done():
    # Fake the Gemini client: first a tool call, then declare_done.
    from app.executors.azure_exec import agent
    calls = {"n": 0}

    def _msg(tool_name=None, args=None, text=None):
        m = mock.Mock()
        if tool_name:
            tc = mock.Mock(); tc.function.name = tool_name
            tc.function.arguments = __import__("json").dumps(args or {})
            tc.id = "c1"; m.tool_calls = [tc]; m.content = None
        else:
            m.tool_calls = None; m.content = text or "working"
        return mock.Mock(choices=[mock.Mock(message=m)])

    async def _create(**kw):
        calls["n"] += 1
        if calls["n"] == 1:
            return _msg("run_shell", {"command": "az group show"})
        return _msg("declare_done", {"summary": "storage account up",
                                     "artifacts": ["out.txt"]})

    ran = []

    async def run_tool(name, args):
        ran.append(name)
        return "ok"

    s = _settings()
    os.environ["GEMINI_API_KEY"] = "k"
    s = load()
    with mock.patch.object(agent, "AsyncOpenAI") as cli:
        cli.return_value.chat.completions.create = mock.AsyncMock(side_effect=_create)
        out = await agent.run_agent(s, "make a storage account",
                                    {"resource_group": "ags-sb-x"},
                                    run_tool, "gemini-2.5-flash")
    assert out["done"] is True and out["summary"] == "storage account up"
    assert "run_shell" in ran
    print("ok    agent loop: acts with tools, then declares done with summary")


_TRANSCRIPT = """agent: planning
tool: run_shell {"command": "az storage account create ..."}
  -> (exit 0) created
DONE
SUMMARY: storage account + key vault created and tagged
===AGS-FILE-BEGIN:runbook.md
# runbook
what ran
===AGS-FILE-END:runbook.md
"""


async def test_executor_end_to_end_in_container():
    # The full Azure run with the SDK faked: provision -> launch agent ->
    # read transcript -> verify -> teardown -> cost row. No real Azure calls.
    from app.executors.azure_exec.executor import AzureExecutor
    s = _settings(EXECUTOR_BACKEND="azure", GEMINI_API_KEY="k")
    az = _fake_az(log_text=_TRANSCRIPT, state="Succeeded")
    ex = AzureExecutor(s, az_clients=az)
    payload = RunPayload(run_id="task-1", image="", commands=[], env={
        "AGS_TASK": "task-1", "AGS_OWNER": "owner-1", "AGS_ORG": "org-1",
        "AGS_REQUIREMENT": "make a storage account",
        "AGS_OUTPUTS": "runbook.md", "AGS_MODEL": "gemini-2.5-flash",
    }, timeout_seconds=60)
    lines = [line async for line in ex.run(payload)]
    res = ex.result()
    # provisioned + scoped
    assert az["resource"].resource_groups.created, "no RG created"
    assert az["auth"].assignments, "no role assignment"
    # agent container launched with the identity
    assert az["aci"].groups, "agent container not launched"
    assert az["aci"].groups[0]["body"]["identity"]["type"] == "UserAssigned"
    # torn down
    assert az["resource"].resource_groups.deleted, "RG not deleted"
    # verified + artifacts parsed from the transcript
    assert res.ok is True and res.idempotent is True, res.note
    assert "runbook.md" in res.files and "what ran" in res.files["runbook.md"]
    assert res.note == "storage account + key vault created and tagged"
    assert any("torn down" in l for l in lines), lines
    print("ok    executor: provision -> agent-in-container -> verify -> teardown + artifacts")


async def test_executor_failed_run_returns_not_ok():
    from app.executors.azure_exec.executor import AzureExecutor
    s = _settings(EXECUTOR_BACKEND="azure", GEMINI_API_KEY="k")
    az = _fake_az(log_text="INCOMPLETE\nSUMMARY: ran out of steps", state="Failed", exit_code=1)
    ex = AzureExecutor(s, az_clients=az)
    payload = RunPayload(run_id="t2", image="", commands=[], env={
        "AGS_TASK": "t2", "AGS_REQUIREMENT": "x"}, timeout_seconds=60)
    async for _ in ex.run(payload):
        pass
    res = ex.result()
    assert res.ok is False
    assert az["resource"].resource_groups.deleted, "teardown must still happen on failure"
    print("ok    executor: failed/incomplete run is not verified; teardown still runs")


async def test_missing_deliverable_is_not_verified():
    # Agent declared done but never wrote a required file: must NOT verify.
    from app.executors.azure_exec.executor import AzureExecutor
    transcript = ("DONE\nSUMMARY: built it\n"
                  "===AGS-FILE-BEGIN:runbook.md\nstuff\n===AGS-FILE-END:runbook.md\n"
                  "===AGS-FILE-MISSING:main.tf\n")
    s = _settings(EXECUTOR_BACKEND="azure", GEMINI_API_KEY="k")
    az = _fake_az(log_text=transcript, state="Succeeded", exit_code=0)
    ex = AzureExecutor(s, az_clients=az)
    payload = RunPayload(run_id="t3", image="", commands=[], env={
        "AGS_TASK": "t3", "AGS_REQUIREMENT": "x",
        "AGS_OUTPUTS": "main.tf,runbook.md"}, timeout_seconds=60)
    async for _ in ex.run(payload):
        pass
    res = ex.result()
    assert res.ok is False, "missing main.tf must fail verification"
    assert az["resource"].resource_groups.deleted, "teardown still runs"
    print("ok    executor: declared-done with a missing deliverable is rejected, not verified")


async def main():
    test_tags()
    test_provision_scopes_identity_to_rg()
    test_launch_attaches_identity()
    test_teardown_returns_cost_event()
    test_azure_configured_gate()
    test_executor_selected_for_azure()
    await test_agent_loop_declares_done()
    await test_executor_end_to_end_in_container()
    await test_executor_failed_run_returns_not_ok()
    print("\nAzure executor: all checks passed (mocked, no spend)")


asyncio.run(main())
