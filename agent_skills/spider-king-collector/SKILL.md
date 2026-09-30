---
name: spider-king-collector
description: Recover a real web data protocol from captured browser evidence and deliver a repeatable browser-free Python collector.
metadata:
  version: "1.0.0-adapter"
  upstream: "spider-king"
---

# Spider King Collector Adapter

This is the OpenHands-compatible collector subset of the local `spider-king` skill. Use it for recruitment pages whose useful data passes through dynamic APIs, encrypted/encoded responses, bootstrap state, signatures, cookies, GraphQL, protobuf, WebSocket or route-changing pagination.

The control plane already owns browser evidence collection. Do not look for unavailable Codex MCP tools. Start from the supplied evidence directory, then use bounded public HTTP probes to confirm the protocol.

## Auto judge

Classify the task before implementation:

- `artifact-only`: saved requests/responses are enough for fixed-vector decode or parser work; live acceptance remains unproven.
- `live-target`: the current endpoint must be confirmed with fresh public requests.
- `continuation`: the same target and environment already have a proven request; inspect only the surface invalidated by the failure.

Tag the smallest dominant gate: `decode-gated`, `signer-gated`, `session-gated`, `transport-gated`, or plain HTTP. State the desired result as evidence, local proof, compact replay, or collector. Recruitment onboarding normally requires a collector.

## Protocol recovery loop

1. Fingerprint the entry, bootstrap, list, detail, pagination and telemetry routes separately.
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
