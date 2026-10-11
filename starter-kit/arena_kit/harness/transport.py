"""Async, journaled arena transport and one budgeted model gateway per run."""

import asyncio
import json
import os
import time
from urllib.parse import quote

import httpx

from ..client import ArenaError
from .resources import Pacer
from .state import encode, redact


class UncertainRequest(RuntimeError):
    """Keep the original operation pending; never invent a replacement move ID."""


def component(value):
    return quote(str(value), safe="")


class Transport:
    def __init__(self, cfg, journal, *, url=None, key=None, transport=None, pacer=None):
        self.cfg, self.journal = cfg, journal
        self.http = httpx.AsyncClient(
            base_url=(url or cfg["url"]) + "/v1/",
            headers={"X-API-Key": key or os.environ.get("ARENA_API_KEY", "")},
            timeout=180,
            follow_redirects=False,
            transport=transport,
        )
        self.lock, self.next = asyncio.Lock(), 0.0
        self.pacer = pacer or Pacer()

    async def close(self):
        await self.http.aclose()

    async def pace(self):
        await self.pacer.wait()

    def allowed(self, method, path):
        # Allowlist, not a list of known forbidden aliases.
        read = path in ("me", "data", "cases", "sessions") or path.startswith("sessions/")
        write = path == "sessions" or (path.startswith("sessions/") and path.endswith("/messages"))
        if path.startswith("/") or ".." in path or "?" in path or not (read if method == "GET" else write):
            raise ValueError(f"Endpoint is outside harness protocol: {method} {path}")
        if "/backoffice" in path or "://" in path:
            raise ValueError("Backoffice and external endpoints are not used by the runner")
        if method == "POST" and self.cfg["profile"] != "sim" and not self.cfg["allow_arena"]:
            raise ValueError("Arena mutation is not authorized")

    async def request(self, method, path, *, body=None, params=None, operation_id=None, raw=False, deadline=None):
        """`deadline` (monotonic) for a message: past RETRY_MARGIN before it, a transient failure is not
        retried - the arena may record our timeout and the supplier's follow-up first, and a late copy of the
        message then reads as an answer to that follow-up (practice: a retried message after a 503 arrived
        as a push after the supplier's final offer, which withdrew)."""
        self.allowed(method, path)
        op = None
        if method == "POST":
            if redact(body) != body:
                raise ValueError("Request body contains a configured secret")
            if len(encode(body).encode()) > 64 * 1024:
                raise ValueError("Request exceeds 64 KiB")
            op = self.journal.operation(path, body, operation_id)
            if op["status"] == "done":
                return json.loads(op["response"])
            if op["status"] == "rejected":
                error = json.loads(op["response"])
                raise ArenaError(error["status"], error["detail"])
        headers = {"X-Move-Id": op["id"]} if op else {}

        def reject_late_retry():
            if op and deadline is not None and time.monotonic() >= deadline - RETRY_MARGIN:
                self.journal.finish(op["id"], {"status": 409, "detail": "not retried: too late"}, "rejected")
                raise ArenaError(409, "Retry too close to reply deadline; refresh server state")

        for attempt in range(4):
            await self.pace()
            if attempt:
                reject_late_retry()
            start = time.monotonic()
            try:
                response = await self.http.request(
                    method, path, json=body, params=params, headers=headers, timeout=180 if method == "POST" else 10
                )
            except httpx.TransportError:
                self.journal.event(
                    "api",
                    method=method,
                    path=path,
                    error="transport",
                    elapsed=time.monotonic() - start,
                    retry=attempt > 0,
                )
                reject_late_retry()
                if attempt == 3:
                    raise UncertainRequest(f"Unresolved {method} {path}; journal retained") from None
                await asyncio.sleep(0.5 * 2**attempt)
                continue
            self.journal.event(
                "api",
                method=method,
                path=path,
                status=response.status_code,
                elapsed=time.monotonic() - start,
                retry=attempt > 0,
            )
            retry_after = response.headers.get("Retry-After", "")
            retry = response.status_code in (500, 502, 503, 504) or (
                response.status_code == 429 and retry_after.isdigit() and 0 < int(retry_after) <= 5
            )
            if retry and op and deadline is not None and time.monotonic() > deadline - RETRY_MARGIN:
                self.journal.finish(op["id"], {"status": 409, "detail": "not retried: too late"}, "rejected")
                raise ArenaError(409, f"Transient HTTP {response.status_code} too close to the reply deadline")
            if retry:
                if attempt < 3:
                    await asyncio.sleep(float(retry_after) if retry_after.isdigit() else 0.5 * 2**attempt)
                    continue
                raise UncertainRequest(f"Transient HTTP {response.status_code}; original operation retained")
            if response.is_error:
                try:
                    detail = response.json().get("detail", "Arena error")
                except (ValueError, AttributeError):
                    detail = "Non-JSON Arena error"
                if op:
                    self.journal.finish(op["id"], {"status": response.status_code, "detail": detail}, "rejected")
                raise ArenaError(response.status_code, detail)
            if response.is_redirect:
                raise UncertainRequest("Redirect refused; API credentials were not forwarded")
            if raw:
                return response.content
            try:
                data = response.json()
            except ValueError:
                raise UncertainRequest("Non-JSON success response; operation retained") from None
            if op:
                self.journal.finish(op["id"], data)
            return data


RETRY_MARGIN = 30  # seconds a retried message needs before its reply deadline


class Models:
    def __init__(self, cfg, journal, *, transport=None, shared=None):
        self.cfg, self.journal = cfg, journal
        self.sem = asyncio.Semaphore(cfg["model"].get("parallel", 2))
        self.shared = shared or asyncio.Semaphore(10)
        self.http = httpx.AsyncClient(
            timeout=cfg["model"].get("timeout", 20), follow_redirects=False, transport=transport
        )

    async def close(self):
        await self.http.aclose()

    def payload(self, messages):
        model = self.cfg["model"]
        body = {"model": model["name"], "messages": messages}
        # Reasoning models reject max_tokens; plain endpoints keep the original field.
        if "reasoning_effort" in model:
            body |= {"reasoning_effort": model["reasoning_effort"], "max_completion_tokens": model["max_output_tokens"]}
        else:
            body["max_tokens"] = model["max_output_tokens"]
        if "service_tier" in model:
            body["service_tier"] = model["service_tier"]
        return body

    async def complete(self, messages, *, background=False):
        model = self.cfg["model"]
        if not model:
            raise ValueError("No model configured")
        # Repeat the authorization check at the only model network boundary.
        from urllib.parse import urlparse

        if urlparse(model["base_url"]).hostname not in ("127.0.0.1", "localhost", "::1"):
            if not self.cfg["allow_model"]:
                raise ValueError("Remote model not authorized")
        if len(encode(messages).encode()) > 128 * 1024:
            raise ValueError("Model input exceeds 128 KiB")
        # Reflections never queue ahead of decisions. Acquiring available semaphores does not yield.
        if background and (self.sem.locked() or self.shared.locked()):
            raise RuntimeError("Background analysis skipped while model capacity is busy")
        async with self.sem, self.shared:
            call_id = self.journal.reserve_model(model["max_calls"])
            start = time.monotonic()
            served_tier = None
            try:
                r = await self.http.post(
                    model["base_url"].rstrip("/") + "/chat/completions",
                    headers={"Authorization": "Bearer " + os.environ.get("LLM_API_KEY", "")},
                    json=self.payload(messages),
                )
                r.raise_for_status()
                data = r.json()
                served_tier = data.get("service_tier")
                self.journal.model_done(call_id, data.get("usage", {}))
                return data["choices"][0]["message"]["content"]
            except BaseException:
                self.journal.model_done(call_id, {}, "unknown")
                raise  # no retries: an ambiguous timeout may already have been billed
            finally:
                self.journal.event(
                    "model",
                    call_id=call_id,
                    elapsed=time.monotonic() - start,
                    service_tier_requested=model.get("service_tier", "auto"),
                    service_tier=served_tier,
                )
