---
name: spider-king-collector
description: Recover a real web data protocol from captured browser evidence and deliver a repeatable browser-free Python collector.
metadata:
  version: "1.1.0-cli-adapter"
  upstream: "spider-king"
---

# Spider King Collector Adapter

This is the OpenHands-compatible collector subset of the local `spider-king` skill. Use it for recruitment pages whose useful data passes through dynamic APIs, encrypted/encoded responses, bootstrap state, signatures, cookies, GraphQL, protobuf, WebSocket or route-changing pagination.

Start from the supplied evidence directory. `CollectorBrowserTool` exposes task-isolated Playwright CLI on the Agent Server. When the list/detail endpoint, response format, decoder, pagination or hard fields are uncertain, actively open the recruitment entry, take a snapshot, interact with internship filters/pagination, inspect numbered requests and real response bodies, and save the returned evidence references. The same tool is available during repairs. Do not look for unrelated Codex MCP tools or assume a terminal exists.

## Browser session ownership

- One onboarding batch reuses one Playwright CLI browser process. A company gets its own page while it is the active evidence owner; never interleave two companies' navigation or network inspection. Finish or retain the current company's session chain before another company takes ownership.
- `open` acquires a company page in the existing batch browser. `close` releases that company's page, not the shared browser process. The platform closes the batch browser after all companies finish the development/validation phase. Do not launch a fresh browser for every company.
- Paused or disconnected executions retain the current company page/session chain until resumed or explicitly cancelled. Do not discard unique bootstrap state as cache. Other companies must wait while that page is retained.
- First use saved evidence when sufficient. Otherwise capture a clean page/network baseline, prove the real business endpoint, inspect request/response and static scripts when needed, then verify one browser-free HTTP replay before scaling.
- CLI currently provides page and wire evidence. It does not provide the full chrome-devtools + js-reverse debugger handoff of the upstream skill. If runtime breakpoint/initiator evidence is required but unavailable, identify that capability gap instead of claiming it was traced.

## Auto judge

Classify the task before implementation:

- `artifact-only`: saved requests/responses are enough for fixed-vector decode or parser work; live acceptance remains unproven.
- `live-target`: the current endpoint must be confirmed with fresh public requests.
- `continuation`: the same target and environment already have a proven request; inspect only the surface invalidated by the failure.

Tag the smallest dominant gate: `decode-gated`, `signer-gated`, `session-gated`, `transport-gated`, or plain HTTP. State the desired result as evidence, local proof, compact replay, or collector. Recruitment onboarding normally requires a collector.

## Protocol recovery loop

1. Fingerprint the entry, bootstrap, list, detail, pagination and telemetry routes separately.
   A recruitment entry returning HTML, an XML/text endpoint, or an encoded response is not automatically a failure. Establish the expected wire format before choosing a parser. Never substitute the entry URL for an unconfirmed JSON API.
2. Prove one request that returns useful business data before implementing pagination or concurrency.
3. Freeze the raw response and locate its first real consumer before attempting decode.
4. Isolate every moving value: timestamp, nonce, cursor, cookie, wrapper field, decode key, session state or route pivot.
5. Find the canonical mutation point where the wire payload changes; do not copy a visible placeholder.
6. Rebuild from the smallest reliable runtime: pure Python first, then a tiny local JS/WASM/bootstrap helper only when fixed-vector evidence proves it is necessary.
7. Replay the business request, decode locally, verify the next page/detail route, and only then scale.

Read [references/protocol-recovery.md](references/protocol-recovery.md) for decode and pagination rules. Read [references/delivery-gate.md](references/delivery-gate.md) before calling the result a collector.

## Invariants

- Final collection is browser-free Python HTTP. Playwright, CDP, page-context fetch, browser profiles and manual cookie export are not runtime fallbacks.
- Preserve one session chain for bootstrap-heavy flows until cross-session reuse is proven.
- Do not hardcode rotating state until its writer, scope, expiry and refresh path are proven.
- Wire egress and saved raw response are authoritative when they differ from DOM values or intermediate callbacks.
- A `200`, non-empty token, helper load or passing fixture is not live acceptance by itself.
- Redact credentials, tokens, cookies and personal data from fixtures, logs, reports and Git.
