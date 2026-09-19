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
from app import db, embers


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
    def get(self, name):
        # Model Azure: a GET on a deleted RG raises (404). The teardown proof
        # relies on this to confirm the sandbox is really gone.
        if name in self.deleted:
            raise Exception("ResourceGroupNotFound")
        return mock.Mock(name=name)


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
    assert proof["verified_gone"] is True  # post-delete GET confirmed the RG is gone
    assert proof["org_id"] == "org-9" and proof["owner_sub"] == "owner-9"
    assert proof["resource_group"] == sb.rg_name
    assert proof["duration_seconds"] >= 0
    print("ok    teardown: RG deleted + verified-gone proof recorded (#13)")


async def test_teardown_proof_stored_as_artifact():
    # End to end: a verified real run persists teardown.json proof (the RG was
    # confirmed deleted) as an artifact on the task. (#13)
    from app.executors.azure_exec.executor import AzureExecutor
    import app.runner as runner
    s = _settings(EXECUTOR_BACKEND="azure", GEMINI_API_KEY="k")
    db.init(os.environ["DB_PATH"]); await db.create_schema()
    az = _fake_az(log_text=_TRANSCRIPT, state="Succeeded")
    from app.models import Task
    async with db.session() as sess:
        t = Task(id="td-proof", owner_sub="owner-proof", title="x",
                 state="running", formats=["markdown"])
        sess.add(t); await sess.commit()
    from app import executors as ex_mod
    orig = ex_mod.get_executor
    ex_mod.get_executor = lambda settings: AzureExecutor(settings, az_clients=az)
    try:
        await runner._run_real("td-proof", s)
    finally:
        ex_mod.get_executor = orig
    async with db.session() as sess:
        from app.models import Artifact
        from sqlalchemy import select
        arts = (await sess.execute(select(Artifact).where(Artifact.task_id == "td-proof"))).scalars().all()
        names = {a.filename for a in arts}
        proof = next((a for a in arts if a.filename == "teardown.json"), None)
        assert "teardown.json" in names, f"no teardown proof artifact in {names}"
        assert '"verified_gone": true' in proof.content
        t2 = await sess.get(Task, "td-proof")
        assert t2.state == "verified", t2.state
    print("ok    teardown proof stored as teardown.json artifact on a verified run (#13)")


async def test_abort_records_real_meters_and_proof():
    # Regression for the abort race: when a run is stopped mid-flight, the runner
    # must read the SETTLED result (real sandbox_seconds/tokens + teardown proof),
    # not the init stub. The old break-early read result() before the executor's
    # generator finished, recording 0 Embers and no teardown.json.
    import app.runner as runner
    from app.executors.base import RunResult
    from app.models import Artifact, Task
    from sqlalchemy import select
    s = _settings(EXECUTOR_BACKEND="azure", GEMINI_API_KEY="k")
    db.init(os.environ["DB_PATH"]); await db.create_schema()

    class _FakeEx:
        def __init__(self): self.live_log = []; self._res = None
        async def run(self, payload):
            for i in range(3):
                self.live_log.append(f"line {i}"); yield f"line {i}"
                await asyncio.sleep(0.03)
            # settle the real result AFTER teardown, like the real executor does
            self._res = RunResult(ok=False, exit_code=1, log="x", note="aborted by user",
                                  sandbox_seconds=47, llm_tokens=1200,
                                  teardown_proof={"resource_group": "rg-x",
                                                  "verified_gone": True,
                                                  "duration_seconds": 47})
        def result(self):
            return self._res or RunResult(ok=False, exit_code=-1, log="",
                                          note="not run", sandbox_seconds=0, llm_tokens=0)
        def abort(self): pass

    async with db.session() as sess:
        sess.add(Task(id="abort-meters", owner_sub="u", title="x",
                      state="running", formats=["markdown"]))
        await sess.commit()
    from app import executors as ex_mod
    orig = ex_mod.get_executor
    ex_mod.get_executor = lambda settings: _FakeEx()
    try:
        drive = asyncio.ensure_future(runner._run_real("abort-meters", s))
        await asyncio.sleep(0.05)
        runner.killswitch.request_abort("abort-meters")
        await asyncio.wait_for(drive, timeout=10)
    finally:
        ex_mod.get_executor = orig
    async with db.session() as sess:
        t = await sess.get(Task, "abort-meters")
        assert t.sandbox_seconds == 47 and t.llm_tokens == 1200, \
            f"aborted run must bill real meters, got {t.sandbox_seconds}s/{t.llm_tokens}tok"
        assert t.embers_spent > 0, "aborted run must burn some Embers"
        names = {a.filename for a in (await sess.execute(
            select(Artifact).where(Artifact.task_id == "abort-meters"))).scalars().all()}
        assert "teardown.json" in names, f"aborted run must store teardown proof, got {names}"
    print("ok    abort records real meters + teardown proof, not the init stub")



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
        # Mirror the real SDK message shape: model_dump(mode="json") returns the
        # dict the agent appends (preserving fields like thought_signature).
        if tool_name:
            payload = {"role": "assistant", "content": None, "tool_calls": [
                {"id": "c1", "type": "function", "function":
                 {"name": tool_name, "arguments": __import__("json").dumps(args or {})}}]}
        else:
            payload = {"role": "assistant", "content": text or "working",
                       "tool_calls": None}
        m = mock.Mock()
        m.model_dump = lambda mode="json": dict(payload)
        r = mock.Mock(choices=[mock.Mock(message=m)])
        # Mirror the real SDK: usage is None unless the caller requests it, so
        # the agent's token accumulation skips these debug-path responses.
        r.usage = None
        return r

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

    _settings()
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


def test_context_helpers():
    from app.executors.azure_exec import agent
    # clip: short text passes through, long text is head+tail with a pointer
    assert agent._clip("short") == "short"
    long_text = "x" * 5000
    clipped = agent._clip(long_text)
    assert len(clipped) < len(long_text) and "truncated" in clipped
    assert clipped.startswith("x" * 100) and clipped.endswith("x" * 100)
    # prune: all but the last KEEP_RECENT_TOOLS tool results become stubs,
    # tool-call pairing intact, system/user untouched
    msgs = [{"role": "system", "content": "s"}, {"role": "user", "content": "u"}]
    for i in range(6):
        msgs.append({"role": "assistant", "content": None,
                     "tool_calls": [{"id": f"c{i}"}]})
        msgs.append({"role": "tool", "tool_call_id": f"c{i}", "content": f"out{i}"})
    agent._prune_tools(msgs)
    tools = [m for m in msgs if m.get("role") == "tool"]
    kept = [m for m in tools if not str(m["content"]).startswith("[cleared")]
    assert len(kept) == agent._KEEP_RECENT_TOOLS
    assert kept[-1]["content"] == "out5"  # most recent verbatim
    assert all(str(t["content"]).startswith("[cleared") for t in tools[:-agent._KEEP_RECENT_TOOLS])
    # est_tokens scales with content
    assert agent._est_tokens(msgs) > 0
    print("ok    context: clip truncates with pointer, prune keeps recent, est works")


async def test_compact_shrinks_history():
    from app.executors.azure_exec import agent
    _settings(); os.environ["GEMINI_API_KEY"] = "k"
    load()
    # a long history that should compact down to system+user+brief+tail
    msgs = [{"role": "system", "content": "sys"}, {"role": "user", "content": "task"}]
    for i in range(12):
        msgs.append({"role": "assistant", "content": f"step {i} " + "y" * 400})
        msgs.append({"role": "tool", "tool_call_id": f"c{i}", "content": "out"})
    before = len(msgs)
    with mock.patch.object(agent, "AsyncOpenAI") as cli:
        cli.return_value.chat.completions.create = mock.AsyncMock(
            return_value=mock.Mock(choices=[mock.Mock(
                message=mock.Mock(content="brief: made storage; owe main.tf"))]))
        out = await agent._compact(cli.return_value, "gemini-2.5-flash", msgs)
    assert len(out) < before
    assert out[0]["role"] == "system" and "[compacted history]" in out[2]["content"]
    assert out[-1] is msgs[-1]  # verbatim tail preserved
    print("ok    context: compaction summarizes head, keeps verbatim tail")


async def test_embers():
    _settings()
    db.init(os.environ["DB_PATH"])
    await db.create_schema()
    s = load()
    # rating: blended seconds + tokens, rounds up, floor of 0
    assert embers.cost_embers(s, 0, 0) == 0
    # 4 min * 3/min = 12, plus 4000/1000 * 2 = 8 -> 20
    assert embers.cost_embers(s, 240, 4000) == 20
    # trial grant: first sight creates the account with the allowance
    bal = await embers.balance("user-a", s)
    assert bal == s.ember_trial_allowance
    # affordability gate: a small estimate passes on a fresh trial balance
    ok, bal, est = await embers.can_afford("user-a", s)
    assert ok is True and est == embers.estimate_run_cost(s)
    # burn: decrements balance, records meter quantities in the ledger
    new_bal = await embers.burn("user-a", "task-1", 30, 240, 4000)
    assert new_bal == s.ember_trial_allowance - 30
    # a user with no balance cannot afford a run
    await embers.burn("user-a", "task-2", new_bal, 10, 10)  # drain to 0
    ok, bal, _ = await embers.can_afford("user-a", s)
    assert ok is False and bal == 0
    # usage tokens parsed from a transcript
    from app.executors.azure_exec import transcript
    assert transcript.usage_tokens("line\nUSAGE_TOKENS: 4321\n") == 4321
    assert transcript.usage_tokens("no usage here") == 0
    # The agent prints a RUNNING total each step so a hard abort still leaves a
    # count; the parser takes the LAST one (the most complete total).
    assert transcript.usage_tokens(
        "USAGE_TOKENS: 100\nUSAGE_TOKENS: 250\nUSAGE_TOKENS: 4321\n") == 4321
    print("ok    embers: rate, trial grant, afford gate, burn, usage parse")


async def test_rate_limits():
    # TODO #6: per-user caps. Concurrent: N running tasks hit the cap. Daily:
    # sandbox_seconds summed over the UTC day vs the hours cap.
    from app import ratelimit
    from app.models import Task
    s = _settings(RATELIMIT_MAX_CONCURRENT="2", RATELIMIT_MAX_SANDBOX_HOURS_DAY="1")
    db.init(os.environ["DB_PATH"]); await db.create_schema()
    # under both caps -> allowed
    ok, _ = await ratelimit.check_rate_limits("rl-user", s)
    assert ok is True
    # add 2 running tasks -> concurrent cap blocks the next
    async with db.session() as sess:
        for i in range(2):
            sess.add(Task(id=f"rl-c{i}", owner_sub="rl-user", title="x", state="running"))
        await sess.commit()
    ok, reason = await ratelimit.check_rate_limits("rl-user", s)
    assert ok is False and "at once" in reason
    # daily-hours: a finished task today with 3600s (= the 1h cap) blocks
    async with db.session() as sess:
        t = await sess.get(Task, "rl-c0")
        t.state = "verified"; t.sandbox_seconds = 3600  # exactly the 1h cap
        await sess.commit()
    # concurrent is now under (only 1 running) but daily is at the cap
    ok, reason = await ratelimit.check_rate_limits("rl-user", s)
    assert ok is False and "daily" in reason.lower(), reason
    # limits off (0) -> always allowed even at the cap
    s_off = _settings(RATELIMIT_MAX_CONCURRENT="0", RATELIMIT_MAX_SANDBOX_HOURS_DAY="0")
    ok, _ = await ratelimit.check_rate_limits("rl-user", s_off)
    assert ok is True
    print("ok    rate limits: concurrent cap + daily sandbox-hours cap + off-when-zero (#6)")


_TRANSCRIPT = """agent: planning
tool: run_shell {"command": "az storage account create ..."}
  -> (exit 0) created
tool: verify_outcome {"command": "az resource show --ids <id> --query properties.provisioningState"}
VERIFY-RESULT: exit=0
"Succeeded"
VERIFY-END
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


async def test_done_without_verify_evidence_is_not_verified():
    # The core of the evidence contract: declare_done alone is just the agent's
    # word. A run that writes its files and claims done but never ran a passing
    # verify_outcome check must NOT be stamped verified, even with exit 0.
    from app.executors.azure_exec.executor import AzureExecutor
    s = _settings(EXECUTOR_BACKEND="azure", GEMINI_API_KEY="k")
    base = ("DONE\nSUMMARY: built it\n"
            "===AGS-FILE-BEGIN:runbook.md\nstuff\n===AGS-FILE-END:runbook.md\n")
    # (a) no verify_outcome at all -> not verified
    az = _fake_az(log_text=base, state="Succeeded", exit_code=0)
    ex = AzureExecutor(s, az_clients=az)
    payload = RunPayload(run_id="tv1", image="", commands=[], env={
        "AGS_TASK": "tv1", "AGS_REQUIREMENT": "x", "AGS_OUTPUTS": "runbook.md"},
        timeout_seconds=60)
    async for _ in ex.run(payload):
        pass
    assert ex.result().ok is False, "done with no verify evidence must not verify"
    # (b) a verify_outcome that FAILED (exit != 0) -> not verified
    failing = ("tool: verify_outcome {\"command\": \"check\"}\n"
               "VERIFY-RESULT: exit=1\nnot actually compliant\nVERIFY-END\n" + base)
    az2 = _fake_az(log_text=failing, state="Succeeded", exit_code=0)
    ex2 = AzureExecutor(s, az_clients=az2)
    payload2 = RunPayload(run_id="tv2", image="", commands=[], env={
        "AGS_TASK": "tv2", "AGS_REQUIREMENT": "x", "AGS_OUTPUTS": "runbook.md"},
        timeout_seconds=60)
    async for _ in ex2.run(payload2):
        pass
    assert ex2.result().ok is False, "done with a failing verify must not verify"
    print("ok    executor: declare_done without a PASSING verify_outcome is rejected (#12)")


async def test_verify_evidence_parser():
    # transcript.verify_evidence: captures (exit_code, command, output) per
    # block; empty when no verify_outcome ran. verify_log_text renders ONLY the
    # evidence (command + pass/fail + output), not the whole transcript.
    from app.executors.azure_exec import transcript
    assert transcript.verify_evidence("no markers here") == []
    ev = transcript.verify_evidence(
        "VERIFY-RESULT: exit=0\nVERIFY-CMD: check the thing\ntrue\nVERIFY-END\n")
    assert ev == [(0, "check the thing", "true")], ev
    ev2 = transcript.verify_evidence(
        "VERIFY-RESULT: exit=1\nbad\nVERIFY-END\nVERIFY-RESULT: exit=0\nok\nVERIFY-END\n")
    assert ev2 == [(1, "", "bad"), (0, "", "ok")], ev2
    # verify_log_text: the proof, front and center, not a transcript copy
    vlog = transcript.verify_log_text(
        "tool: run_shell lots of noise\nagent: working\n"
        "VERIFY-RESULT: exit=0\nVERIFY-CMD: az check\nall good\nVERIFY-END\n")
    assert "az check" in vlog and "PASS" in vlog and "all good" in vlog, vlog
    assert "lots of noise" not in vlog, "verify.log must NOT contain run noise"
    assert "no verification check" in transcript.verify_log_text("nothing")
    print("ok    transcript: verify_evidence (exit+cmd+output) + verify_log_text is evidence-only")




async def test_executor_abort_tears_down():
    # Kill switch (TODO #7): a run whose container never finishes is aborted
    # mid-poll; abort() must force-teardown the RG and the run must report not-ok.
    from app.executors.azure_exec.executor import AzureExecutor
    s = _settings(EXECUTOR_BACKEND="azure", GEMINI_API_KEY="k")
    az = _fake_az(log_text="working...", state="Running")  # never terminates
    ex = AzureExecutor(s, az_clients=az)
    payload = RunPayload(run_id="t-kill", image="", commands=[], env={
        "AGS_TASK": "t-kill", "AGS_REQUIREMENT": "x"}, timeout_seconds=120)

    async def drive():
        async for _ in ex.run(payload):
            pass

    run = asyncio.ensure_future(drive())
    await asyncio.sleep(0.2)   # let it provision + enter the poll loop
    ex.abort()                 # the abort endpoint's call, from another coroutine
    await asyncio.wait_for(run, timeout=10)
    res = ex.result()
    assert res.ok is False, "aborted run must not verify"
    assert res.note == "aborted by user", res.note
    # abort() tears down immediately AND the finally re-tears-down (idempotent):
    assert az["resource"].resource_groups.deleted, "abort must tear the RG down"
    print("ok    executor: abort() force-tears-down the sandbox mid-run")


async def main():
    test_tags()
    test_provision_scopes_identity_to_rg()
    test_launch_attaches_identity()
    test_teardown_returns_cost_event()
    test_azure_configured_gate()
    test_executor_selected_for_azure()
    await test_agent_loop_declares_done()
    test_context_helpers()
    await test_compact_shrinks_history()
    await test_embers()
    await test_rate_limits()
    await test_executor_end_to_end_in_container()
    await test_executor_failed_run_returns_not_ok()
    await test_missing_deliverable_is_not_verified()
    await test_done_without_verify_evidence_is_not_verified()
    await test_verify_evidence_parser()
    await test_executor_abort_tears_down()
    await test_teardown_proof_stored_as_artifact()
    await test_abort_records_real_meters_and_proof()
    print("\nAzure executor: all checks passed (mocked, no spend)")


asyncio.run(main())
