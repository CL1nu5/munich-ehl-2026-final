"""Event-driven case scheduler; durable operations survive engine restarts."""

import asyncio
import copy
import hashlib
import json
import os
import signal
import sys
import time
import uuid

from ..client import ArenaError
from ..docs import read_document
from ..play import latest, live_threads, moves_left, standing
from .agent_process import AgentProcess
from .resources import Resources
from .state import Journal, digest, redact
from .transport import Models, Transport, UncertainRequest, component
from .validation import close_action, validate

TRANSIENT_EXIT = 75


DOCUMENT_ATTEMPTS = 3  # loads of one attachment (each within the 12 s document budget of its pass)


class Engine:
    def __init__(self, cfg, journal, *, api=None, models=None, resources=None):
        self.cfg, self.journal = cfg, journal
        self.resources = resources or Resources([journal])
        self.api = api or Transport(
            cfg,
            journal,
            url=os.environ.get("HARNESS_SIM_URL") if cfg["profile"] == "sim" else None,
            key="local-harness" if cfg["profile"] == "sim" else None,
            pacer=self.resources.pacer,
        )
        self.models = models or Models(cfg, journal, shared=self.resources.models)
        self.reflections = set()
        self.selected_cases = None
        self.document_text = {}  # immutable attachments, scoped by session/id/name; bounded below
        self.stop = asyncio.Event()
        self.quota_exhausted = False
        self.practice_left = None
        self.caps = {}
        self.slots = asyncio.Semaphore(cfg["parallel"])

    async def preflight(self, check_sessions=True):
        me = await self.api.request("GET", "me")
        if me.get("status", "active") != "active":
            raise ValueError("Team key is not active")
        fingerprint = hashlib.sha256(os.environ.get("ARENA_API_KEY", "local-harness").encode()).hexdigest()
        if self.cfg["profile"] != "sim":
            previous = self.journal.get("key_fingerprint")
            if previous and previous != fingerprint:
                raise ValueError("Resume requires the original team key")
            self.journal.put("key_fingerprint", fingerprint)
        self.practice_left = me.get("practice", {}).get("left")
        team = self.journal.get("team")
        if team and team != me.get("team_name"):
            raise ValueError("Team identity changed")
        self.journal.put("team", me.get("team_name"))
        # Reconcile original operations before inspecting sessions, including lost open responses.
        for op in self.journal.pending():
            try:
                result = await self.api.request("POST", op["path"], body=json.loads(op["body"]), operation_id=op["id"])
                if op["path"] == "sessions":
                    self.journal.case(json.loads(op["body"])["case_id"], session_id=result["session_id"])
            except ArenaError as e:
                if e.status not in (409, 422):
                    raise
        # Crash may occur after journaling the response but before recording its session ID.
        for op in self.journal.db.execute("SELECT * FROM operations WHERE path='sessions' AND status='done'"):
            response = json.loads(op["response"])
            self.journal.case(json.loads(op["body"])["case_id"], session_id=response["session_id"])
        if self.cfg["profile"] == "final":
            releases = await self.api.request("GET", "data")
            if releases.get("release-sunday", {}).get("public"):
                from pathlib import Path

                if not (Path(self.cfg["data"]) / "release-sunday" / "facts.json").is_file():
                    raise ValueError("Public Sunday release missing: download it before arming final")
        if check_sessions:
            await self.check_sessions()
        if self.cfg["profile"] == "practice" and me.get("practice", {}).get("left", 0) < 1:
            self.quota_exhausted = True
            if check_sessions and not any(
                c.get("session_id") and not c.get("result") for c in self.journal.cases().values()
            ):
                raise ValueError("Practice quota exhausted")

    async def check_sessions(self):
        owned = self.resources.owned_sessions()
        for set_ in ("practice", "sandbox", "final") if self.cfg["profile"] != "sim" else ("practice",):
            sessions = await self.api.request("GET", "sessions", params={"set": set_})
            for session in sessions:
                if session["status"] == "open" and session["session_id"] not in owned:
                    raise ValueError("Unowned open Arena session; resume its original run before starting another")

    async def documents(self, sid, message):
        document_deadline = time.monotonic() + 12
        for att in message.get("attachments") or []:
            if not att.get("attachment_id") or "text" in att or att.get("attempts", 0) >= DOCUMENT_ATTEMPTS:
                continue
            cache_key = (sid, att["attachment_id"], att.get("name"))
            if cache_key in self.document_text:
                att["text"] = self.document_text[cache_key]
                att.pop("error", None)
                continue
            att["attempts"] = att.get("attempts", 0) + 1
            name = hashlib.sha256((sid + ":" + att["attachment_id"]).encode()).hexdigest()
            file = self.journal.folder / "attachments" / name
            try:
                if time.monotonic() >= document_deadline:
                    raise TimeoutError("Document budget exhausted")
                if file.exists():
                    data = file.read_bytes()
                else:
                    data = await asyncio.wait_for(
                        self.api.request(
                            "GET", f"sessions/{component(sid)}/attachments/{component(att['attachment_id'])}", raw=True
                        ),
                        min(10, document_deadline - time.monotonic()),
                    )
                    file.parent.mkdir(exist_ok=True)
                    file.write_bytes(data)
                if len(data) > 10 * 1024 * 1024:
                    raise ValueError("Attachment exceeds 10 MiB")
                parser = None
                try:
                    parser = await asyncio.create_subprocess_exec(
                        sys.executable,
                        "-m",
                        "arena_kit.harness.document_worker",
                        att["name"],
                        str(file),
                        stdout=asyncio.subprocess.PIPE,
                        stderr=asyncio.subprocess.DEVNULL,
                    )
                    output, _ = await asyncio.wait_for(
                        parser.communicate(), max(0.01, min(5, document_deadline - time.monotonic()))
                    )
                    if parser.returncode or len(output) > 1024 * 1024:
                        raise ValueError("Document parse failed or text too large")
                    att["text"] = output.decode("utf-8")
                except (ValueError, TimeoutError, OSError) as e:
                    # Late in a long run the parser process failed for every further quote (practice: an RFQ's
                    # final offers then counted as undocumented, were pushed instead of accepted, and two
                    # bidders walked). The arena's own small files are parsed in-process instead.
                    if len(data) > 1024 * 1024:
                        raise
                    self.journal.event(
                        "document_fallback", session=sid, attachment=att["attachment_id"], reason=repr(e)[:200]
                    )
                    att["text"] = await asyncio.wait_for(asyncio.to_thread(read_document, att["name"], data), 5)
                finally:
                    if parser is not None and parser.returncode is None:
                        parser.kill()
                        await parser.wait()
                att.pop("error", None)
                if len(att["text"].encode("utf-8")) <= 1024 * 1024:
                    if len(self.document_text) >= 128:
                        self.document_text.pop(next(iter(self.document_text)))
                    self.document_text[cache_key] = att["text"]
            except (ArenaError, ValueError, TimeoutError, OSError) as e:
                att["error"] = "Document unavailable or unreadable"
                self.journal.event("document_error", session=sid, attachment=att["attachment_id"], reason=repr(e)[:200])
            except UncertainRequest:
                raise
            except Exception as e:
                att["error"] = "Document parsing failed"
                self.journal.event("document_error", session=sid, attachment=att["attachment_id"], reason=repr(e)[:200])
        return message

    async def snapshot(self, sid):
        snapshot = await self.api.request("GET", f"sessions/{component(sid)}")
        captured_at = time.monotonic()
        threads, deadlines = {}, {}
        if snapshot["status"] == "ended":
            return snapshot, threads, deadlines
        for supplier, state in snapshot["threads"].items():
            thread = await self.api.request("GET", f"sessions/{component(sid)}/threads/{component(supplier)}")
            self.journal.event(
                "thread_state", session=sid, supplier=supplier, timeouts=len(thread.get("timeouts") or [])
            )
            # Timeout state is authoritative even when the server adds no email.
            # Never revive a closed supplier or accept the ID from an older message.
            events = copy.deepcopy(thread["messages"])
            offer_id = thread.get("offer_id", standing(events)[0])
            status = thread.get("status", latest(events).get("status"))
            if thread.get("closed") or offer_id != standing(events)[0] or status != latest(events).get("status"):
                timeout = next((t for t in reversed(thread.get("timeouts") or []) if t.get("offer_id") == offer_id), {})
                terms = timeout.get("terms", {}) if offer_id != standing(events)[0] else standing(events)[1]
                events.append(
                    {
                        "from": "arena",
                        "status": "walked_away" if thread.get("closed") else status,
                        "body": "Reconciled current server thread state.",
                        "attachments": [],
                        "term_sheets": [{"term_sheet_id": offer_id, "terms": terms}]
                        if offer_id and not thread.get("closed")
                        else [],
                    }
                )
            threads[supplier] = events
            deadlines[supplier] = captured_at + (
                state.get("seconds_left") if state.get("seconds_left") is not None else 60
            )
        await asyncio.gather(*(self.documents(sid, m) for events in threads.values() for m in events))
        return snapshot, threads, deadlines

    async def case(self, case):
        async with self.slots:
            async with self.resources.session(self.journal, case["case_id"]):
                return await self.play_case(case)

    async def play_case(self, case):
        cid = case["case_id"]
        stored = self.journal.cases().get(cid, {})
        if stored.get("result"):
            return
        if self.stop.is_set() and not stored.get("session_id"):
            return
        agent = AgentProcess(self.cfg, self.models, self.journal)
        pending = {}
        try:
            state = stored.get("agent_state", {})
            if not stored.get("prepared"):
                try:
                    state = await agent.call(
                        "prepare",
                        self.cfg["decision_seconds"],
                        context={"case": case, "parameters": self.cfg["parameters"]},
                    )
                    self.journal.case(cid, prepared=True, agent_state=state)
                except Exception as e:
                    self.journal.event("fallback", case=cid, phase="prepare", reason=type(e).__name__)
                    state = {}
                    # A broken plugin must be detected before it consumes a live session.
                    self.journal.case(cid, error="Agent preparation failed")
                    return
            sid = stored.get("session_id")
            if not sid:
                if self.quota_exhausted or self.stop.is_set():
                    return
                if case.get("wave") and time.monotonic() >= self.caps.get(case["wave"], 0) - 90:
                    self.journal.case(cid, error="Insufficient wave time to start safely")
                    return
                # X-Move-Id is replayed team-wide: a fixed ID would return another run's session.
                op_id = stored.get("open_operation") or f"open-{cid}-{uuid.uuid4().hex}"
                self.journal.case(cid, open_operation=op_id)
                try:
                    opened = await self.api.request("POST", "sessions", body={"case_id": cid}, operation_id=op_id)
                    sid = opened["session_id"]
                except ArenaError as e:
                    if e.status == 409 and self.cfg["profile"] == "final":
                        sessions = await self.api.request("GET", "sessions", params={"set": "final"})
                        match = [s for s in sessions if s["case_id"] == cid]
                        if len(match) != 1:
                            raise
                        sid = match[0]["session_id"]
                    else:
                        raise
                self.journal.case(cid, session_id=sid)
            snapshot, threads, deadlines = await self.snapshot(sid)
            if snapshot["status"] == "ended":
                self.record_result(cid, snapshot)
                return

            async def send(action):
                supplier, message = action["supplier_id"], action["message"]
                path = f"sessions/{component(sid)}/threads/{component(supplier)}/messages"
                deadline = deadlines.get(supplier)
                reply = await self.api.request("POST", path, body={"message": message}, deadline=deadline)
                if reply.get("ended") or "result" in reply:
                    return reply
                deadlines[supplier] = time.monotonic() + 60
                await self.documents(sid, reply)
                threads[supplier] += [{**message, "from": "buyer"}, reply]
                self.journal.event("reply", case=cid, supplier=supplier, claims=reply.get("claim_results", []))
                return reply

            force_close = False
            resync = False  # a thread went late or ended under us: refresh all threads, keep negotiating
            close_attempts = 0
            rejected = set()  # term sheet IDs the arena refused to accept
            while live_threads(threads):
                for supplier, task in list(pending.items()):
                    if task.done():
                        del pending[supplier]
                        try:
                            reply = task.result()
                            if "result" in reply or reply.get("ended"):
                                self.journal.case(cid, result=reply.get("result", reply), error=None)
                                return
                        except ArenaError as e:
                            if e.status not in (409, 422):
                                raise
                            self.journal.event("reconcile", case=cid, status=e.status)
                            if e.status == 422:
                                force_close = True
                            else:
                                # 409: this thread was late or has ended. With several suppliers the others are
                                # still open: closing the whole case here accepted a moving RFQ bid early.
                                resync = True
                cap = self.caps.get(case.get("wave"), float("inf"))
                if not force_close and (self.stop.is_set() or time.monotonic() >= cap - 90):
                    self.journal.event(
                        "fallback", case=cid, phase="close", reason="Shutdown" if self.stop.is_set() else "WaveDeadline"
                    )
                force_close = force_close or self.stop.is_set() or time.monotonic() >= cap - 90
                ready = [s for s in live_threads(threads) if s not in pending]
                late = [s for s in ready if deadlines.get(s, float("inf")) <= time.monotonic()]
                if not force_close and (resync or late):
                    # A late thread has a follow-up from the supplier we have not seen; deciding on the stale
                    # thread would fail the decision deadline and close the case. Refresh once in-flight
                    # requests have settled.
                    if pending:
                        await asyncio.wait(pending.values(), timeout=0.1, return_when=asyncio.FIRST_COMPLETED)
                        continue
                    self.journal.event("resync", case=cid, late=late, after_conflict=resync)
                    resync = False
                    snapshot, threads, deadlines = await self.snapshot(sid)
                    if snapshot["status"] == "ended":
                        self.record_result(cid, snapshot)
                        return
                    if [s for s in live_threads(threads) if deadlines.get(s, float("inf")) <= time.monotonic()]:
                        force_close = True  # still late after a refresh: close as before rather than loop
                    continue
                if force_close:
                    if pending:
                        await asyncio.wait(pending.values(), timeout=0.1, return_when=asyncio.FIRST_COMPLETED)
                        continue
                    snapshot, threads, deadlines = await self.snapshot(sid)
                    if snapshot["status"] == "ended":
                        self.record_result(cid, snapshot)
                        return
                    actions = [close_action(case, threads, rejected)]
                elif not ready:
                    await asyncio.wait(pending.values(), timeout=0.1, return_when=asyncio.FIRST_COMPLETED)
                    continue
                else:
                    budget = min(
                        self.cfg["decision_seconds"],
                        min(deadlines[s] for s in ready) - time.monotonic() - 5,
                        cap - time.monotonic() - 90,
                    )
                    observation = {
                        "case": case,
                        "threads": copy.deepcopy(threads),
                        "ready_suppliers": ready,
                        "seconds_left": {s: max(0, deadlines[s] - time.monotonic()) for s in ready},
                        "wave_seconds_left": None if cap == float("inf") else max(0, cap - time.monotonic()),
                    }
                    started = time.monotonic()
                    try:
                        if budget <= 0:
                            raise TimeoutError("Decision deadline")
                        decision = await agent.call("decide", budget, observation=observation, state=state)
                        actions = validate(case, threads, decision["actions"])
                        if any(a["supplier_id"] not in ready for a in actions):
                            raise ValueError("Action for busy supplier")
                        state = decision["state"]
                        self.journal.case(cid, agent_state=state)
                    except Exception as e:
                        self.journal.event("fallback", case=cid, phase="decide", reason=type(e).__name__)
                        force_close = True
                        continue
                    finally:
                        self.journal.event("decision", case=cid, elapsed=time.monotonic() - started)
                if actions[0]["message"].get("action"):
                    if pending:
                        force_close = True
                        continue
                    # Refresh before accepting, then validate the original offer or safe fallback.
                    snapshot, fresh, deadlines = await self.snapshot(sid)
                    if snapshot["status"] == "ended":
                        self.record_result(cid, snapshot)
                        return
                    threads = fresh
                    try:
                        validate(case, threads, actions)
                        if (actions[0]["message"]["action"].get("term_sheet_id") or "") in rejected:
                            raise ValueError("Rejected before")
                    except ValueError:
                        self.journal.event("fallback", case=cid, phase="close", reason="StaleOrUnsafeOffer")
                        actions = [close_action(case, threads, rejected)]
                    close_attempts += 1
                    if close_attempts > 3:
                        self.journal.case(cid, error="Closure repeatedly rejected; session retained")
                        return
                    try:
                        a = actions[0]
                        result = await self.api.request(
                            "POST",
                            f"sessions/{component(sid)}/threads/{component(a['supplier_id'])}/messages",
                            body={"message": a["message"]},
                        )
                        if "result" in result:
                            result = result["result"]
                        if result.get("ended") or "score" in result:
                            self.journal.case(cid, result=result, error=None)
                            self.record_result(cid, await self.api.request("GET", f"sessions/{component(sid)}"))
                            return
                    except ArenaError as e:
                        if e.status not in (409, 422):
                            raise
                        offer = a["message"]["action"].get("term_sheet_id")
                        self.journal.event(
                            "close_rejected", case=cid, status=e.status, offer=offer, detail=str(e.detail)[:300]
                        )
                        if offer and e.status == 422:  # invalid, never accepted: 409 (late, ended) may resolve
                            rejected.add(offer)
                    snapshot, threads, deadlines = await self.snapshot(sid)
                    if snapshot["status"] == "ended":
                        self.record_result(cid, snapshot)
                        return
                    force_close = True
                    continue
                for action in actions:
                    supplier = action["supplier_id"]
                    if moves_left(case, threads[supplier]) <= 0:
                        force_close = True
                        break
                    pending[supplier] = asyncio.create_task(send(action))
                # A plugin can omit a ready supplier; its remaining time continues to count.
            self.record_result(cid, await self.api.request("GET", f"sessions/{component(sid)}"))
        except ArenaError as e:
            self.journal.case(cid, error=f"Arena HTTP {e.status}")
            if e.status in (401, 403):
                raise
            if e.status == 429:
                self.quota_exhausted = True
        finally:
            for task in pending.values():
                if not task.done():
                    task.cancel()
            await asyncio.gather(*pending.values(), return_exceptions=True)
            await agent.close()
            completed = self.journal.cases().get(cid, {})
            if completed.get("result") and not completed.get("reflected"):
                task = asyncio.create_task(self.reflect_result(cid))
                self.reflections.add(task)
                task.add_done_callback(self.reflections.discard)

    async def reflect_result(self, cid):
        record = self.journal.cases()[cid]
        agent = AgentProcess(self.cfg, self.models, self.journal)
        try:
            state = copy.deepcopy(record.get("agent_state") or {})
            # Analysis may use idle capacity, but live decisions get the model slots first.
            if self.resources.leases or self.resources.models.locked() or self.models.sem.locked():
                state.setdefault("p", {})["llm"] = False
            result = await agent.call(
                "on_result",
                self.cfg["model"].get("timeout", 20) + 5,
                context={"case": record["case"], "result": record["result"]},
                state=state,
            )
            self.journal.case(cid, agent_state=result, reflected=True)
        except Exception as e:
            self.journal.event("reflection_error", case=cid, reason=type(e).__name__)
            self.journal.case(cid, reflected=True)
        finally:
            await agent.close()

    def record_result(self, cid, snapshot):
        self.journal.case(
            cid,
            result=snapshot.get("result"),
            end_reason=snapshot.get("end_reason"),
            rounds=snapshot.get("rounds"),
            error=None if snapshot.get("result") else "Result unavailable",
        )

    def remember(self, case):
        cid = case["case_id"]
        stable = {k: v for k, v in case.items() if k not in ("open", "seconds_left")}
        old = self.journal.cases().get(cid)
        if old and old.get("case_hash") != digest(stable):
            raise ValueError("Case definition changed during run")
        self.journal.case(cid, case=case, case_hash=digest(stable))

    async def select_cases(self):
        if self.journal.cases():
            return [x["case"] for x in self.journal.cases().values()]
        set_ = "practice" if self.cfg["profile"] == "sim" else self.cfg["profile"]
        available = await self.api.request("GET", "cases", params={"set": set_})
        cases = [c for c in available if c.get("hosted", True)]
        requested = set(self.cfg["cases"])
        if requested:
            cases = [c for c in cases if c["case_id"] in requested]
            if {c["case_id"] for c in cases} != requested:
                raise ValueError("Requested cases are unknown or not hosted")
        limit = self.cfg["max_cases"]
        if requested and len(cases) > limit:
            raise ValueError("Case selection exceeds max_cases; raise it explicitly")
        cases = sorted(cases, key=lambda c: c["case_id"])[:limit]
        if not cases:
            raise ValueError("No cases selected")
        for case in cases:
            self.remember(case)
        return cases

    async def run(self, preflight=True):
        tasks = {}
        try:
            if preflight:
                await self.preflight()
            if self.cfg["profile"] != "final":
                cases = self.selected_cases if self.selected_cases is not None else await self.select_cases()
                # Resume lease owners before new cases can occupy all per-run worker slots.
                cases = sorted(
                    cases, key=lambda c: (self.journal.folder.name, c["case_id"]) not in self.resources.leases
                )
                if self.cfg["profile"] == "practice" and not self.quota_exhausted:
                    unopened = sum(not self.journal.cases()[c["case_id"]].get("session_id") for c in cases)
                    if self.practice_left is None or unopened > self.practice_left:
                        raise ValueError("Selected cases exceed remaining practice quota")
                tasks = {c["case_id"]: asyncio.create_task(self.case(c)) for c in cases}
                await asyncio.gather(*tasks.values())
            else:
                closed = set(self.journal.get("closed_waves", []))
                first_poll = True
                while first_poll or len(closed) < 5 or any(not t.done() for t in tasks.values()):
                    first_poll = False
                    for task in tasks.values():
                        if task.done() and task.exception():
                            raise task.exception()
                    if self.stop.is_set():
                        await asyncio.gather(*tasks.values())
                        break
                    try:
                        cases = await self.api.request("GET", "cases", params={"set": "final"})
                    except ArenaError as e:
                        if e.status != 403:
                            raise
                        me = await self.api.request("GET", "me")
                        if me.get("status") != "active":
                            raise ValueError("Final key is not active") from e
                        cases = []
                    for case in cases:
                        self.remember(case)
                        wave = case["wave"]
                        self.caps[wave] = time.monotonic() + max(0, case.get("seconds_left") or 0)
                        if not case["open"]:
                            closed.add(wave)
                            stored = self.journal.cases()[case["case_id"]]
                            if stored.get("session_id") and not stored.get("result") and case["case_id"] not in tasks:
                                snapshot = await self.api.request("GET", f"sessions/{component(stored['session_id'])}")
                                self.record_result(case["case_id"], snapshot)
                        elif case["case_id"] not in tasks:
                            tasks[case["case_id"]] = asyncio.create_task(self.case(case))
                    self.journal.put("closed_waves", sorted(closed))
                    await asyncio.sleep(self.cfg["poll_seconds"])
            complete = all(c.get("result") is not None for c in self.journal.cases().values())
            self.journal.put("status", "completed" if complete and not self.stop.is_set() else "incomplete")
            return 0 if complete and not self.stop.is_set() else 2
        finally:
            for task in tasks.values():
                if not task.done():
                    task.cancel()
            await asyncio.gather(*tasks.values(), return_exceptions=True)
            await asyncio.gather(*list(self.reflections), return_exceptions=True)
            await self.api.close()
            await self.models.close()


def main():
    journal = Journal(sys.argv[1])
    cfg = journal.get("config")

    async def execute():
        engine = Engine(cfg, journal)
        for sig in (signal.SIGINT, signal.SIGTERM):
            asyncio.get_running_loop().add_signal_handler(sig, engine.stop.set)
        return await engine.run()

    try:
        code = asyncio.run(execute())
    except UncertainRequest:
        journal.event("engine_error", reason="Uncertain API request; restart and reconcile")
        code = TRANSIENT_EXIT
    except Exception as e:
        reason = redact(str(e) if isinstance(e, (ArenaError, ValueError)) else type(e).__name__)
        journal.event("engine_error", reason=reason)
        journal.put("status", "failed")
        print(f"Harness stopped: {reason}. See report and journal.", file=sys.stderr)
        code = 2
    finally:
        journal.close()
    raise SystemExit(code)


if __name__ == "__main__":
    main()
