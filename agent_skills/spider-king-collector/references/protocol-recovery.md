# Protocol recovery

## Prove the real request

Capture and correlate exact URL, method, query, body, required non-secret headers, response content type, raw response shape and initiator. Distinguish bootstrap assets, data APIs, details, verifier/warm-up traffic and telemetry. A visible page URL or guessed `#list` suffix is never evidence of an API.

## Moving state

Classify changing fields independently: timestamp, nonce, signed body/query, rotating cookie, cursor, response wrapper, decode IV/key, operation name, session tuple and page-specific route. Server-issued values and locally computed values must remain distinct.

## Response decode

When `200` returns unreadable data:

1. save the exact redacted raw envelope;
2. locate the first consumer in captured scripts or an existing proven protocol client;
3. reproduce each layer in order: decompression, Base64/alphabet conversion, cipher/remap, protobuf/msgpack/JSON parse;
4. verify the local decoder against the captured payload and a negative vector;
5. apply it to a fresh response.

Do not decode a browser-mutated value when the wire payload is available. Do not label compression/remapping as encryption without evidence. The final decoder cannot require DOM or page context.

## Pagination

Pagination is protocol state, not UI decoration. Record page/offset/cursor values, total, page size and termination. Follow the live next route when pagination pivots between route families. For HTML pagers, compare raw source with DOM-decoded attributes because legacy markup may mutate `&` or inline templates.

Prove page one before later pages. Require empty/repeated/total/max bounds and fail loudly on an unexpected response shape.

## Sessions and transport

Keep acquisition and replay in one session for bootstrap-heavy endpoints. Verify whether environment proxy, TLS/client profile, referer or cookie provenance is actually required. Test proxy and direct paths separately before setting `trust_env`; do not confuse an egress failure with a parser failure.
