"""Async, journaled arena transport and one budgeted model gateway per run."""

import asyncio
import json
import os
import re
import time
from urllib.parse import quote

import httpx

from ..client import ArenaError
from .state import encode, redact


class UncertainRequest(RuntimeError):
    """Keep the original operation pending; never invent a replacement move ID."""


def component(value):
    return quote(str(value), safe="")


class Transport:
    def __init__(self, cfg, journal, *, url=None, key=None, transport=None):
        self.cfg, self.journal = cfg, journal
        self.http = httpx.AsyncClient(
            base_url=(url or cfg["url"]) + "/v1/",
            headers={"X-API-Key": key or os.environ.get("ARENA_API_KEY", "")},
            timeout=180,
            follow_redirects=False,
            transport=transport,
        )
        self.lock, self.next = asyncio.Lock(), 0.0

    async def close(self):
        await self.http.aclose()

    async def pace(self):
        async with self.lock:
            delay = self.next - time.monotonic()
            if delay > 0:
                await asyncio.sleep(delay)
            self.next = time.monotonic() + 1 / 9

    def allowed(self, method, path):
        # Allowlist, not a list of known forbidden aliases.
        if method not in ("GET", "POST"):
            raise ValueError("Only GET and POST are part of the harness protocol")
        read = path in ("me", "data", "cases", "sessions") or bool(
            re.fullmatch(r"sessions/[^/]+(?:/(?:threads|attachments)/[^/]+)?", path)
        )
        write = path == "sessions" or bool(re.fullmatch(r"sessions/[^/]+/threads/[^/]+/messages", path))
        if path.startswith("/") or ".." in path or "?" in path or not (read if method == "GET" else write):
            raise ValueError(f"Endpoint is outside harness protocol: {method} {path}")
        if "/backoffice" in path or "://" in path:
            raise ValueError("Backoffice and external endpoints are not used by the runner")
        if method == "POST" and self.cfg["profile"] != "sim" and not self.cfg["allow_arena"]:
            raise ValueError("Arena mutation is not authorized")

    async def request(self, method, path, *, body=None, params=None, operation_id=None, raw=False):
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
        for attempt in range(4):
            await self.pace()
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


class Models:
    def __init__(self, cfg, journal, *, transport=None):
        self.cfg, self.journal = cfg, journal
        self.sem = asyncio.Semaphore(cfg["model"].get("parallel", 2))
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
        return body

    async def complete(self, messages):
        model = self.cfg["model"]
        if not model:
            raise ValueError("No model configured")
        # Repeat the authorization check at the only model network boundary.
        from urllib.parse import urlparse

        if urlparse(model["base_url"]).hostname not in ("127.0.0.1", "localhost", "::1"):
            if not self.cfg["allow_model"]:
                raise ValueError("Remote model not authorized")
        if len(encode(messages).encode()) > model.get("max_input_bytes", 128 * 1024):
            raise ValueError("Model input exceeds configured byte budget")
        async with self.sem:
            call_id = self.journal.reserve_model(model["max_calls"])
            start = time.monotonic()
            try:
                payload = {"model": model["name"], "messages": messages}
                if "reasoning_effort" in model:
                    payload.update(
                        max_completion_tokens=model["max_output_tokens"], reasoning_effort=model["reasoning_effort"]
                    )
                else:
                    payload["max_tokens"] = model["max_output_tokens"]
                if model.get("json_mode"):
                    payload["response_format"] = {"type": "json_object"}
                r = await self.http.post(
                    model["base_url"].rstrip("/") + "/chat/completions",
                    headers={"Authorization": "Bearer " + os.environ.get("LLM_API_KEY", "")},
                    json=payload,
                )
                r.raise_for_status()
                data = r.json()
                self.journal.model_done(call_id, data.get("usage", {}))
                return data["choices"][0]["message"]["content"]
            except BaseException:
                self.journal.model_done(call_id, {}, "unknown")
                raise  # no retries: an ambiguous timeout may already have been billed
            finally:
                self.journal.event("model", call_id=call_id, elapsed=time.monotonic() - start)
