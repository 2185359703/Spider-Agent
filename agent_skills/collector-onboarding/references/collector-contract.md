# Collector contract

## Platform configuration

- One company entry uses one platform key, collector module, TOML file, test file and fixture directory.
- New TOML must omit `platform_id` while the business mapping is unknown.
- `platform_id_env` stays present so an approved ID can be supplied later without regenerating the collector.
- `crawl_version` remains `v1.0.0` unless the task explicitly changes it.

## Unified record

Every successful live sample must contain:

- non-empty `source_id`;
- non-empty `title`;
- a real, reproducible `source_url` or `apply_url`;
- at least one non-empty body field: `description` or `requirements`;
- `source_platform = None` before downstream mapping;
- redacted `source_payload` and request trace.

Use existing `BaseCollector.build_record()` and common normalization helpers. Keep original response objects in the sanitized source payload so cleaning can be replayed. Do not create a platform-specific text normalization scheme.

`position` and downstream `apply_url` formatting remain downstream concerns. Do not change shared cleaning semantics from a company collector.

## Internship filtering

Use explicit recruitment type/category fields when available, then title and body evidence. Match Chinese and English internship terms without treating words such as `internal` as `intern`. A list-side filter is an optimization; recheck records after decoding so unsupported server filters cannot leak full-time roles.

`NO_INTERNSHIPS_OBSERVED` means no match in the verified sample/range. It never claims the company permanently has no internships.

## Pagination and details

- Bound every loop with evidence-backed termination plus `max_pages`.
- Detect empty pages and repeated page fingerprints where applicable.
- For offset APIs, increment by the observed limit and stop at total/empty/repeated data.
- Fetch detail data when list fields cannot satisfy the body contract.
- A decoded list item may serve as detail only when captured evidence proves it contains the complete body.

## Failure records

Network failures, access restrictions and removed jobs must remain distinguishable from an empty valid list. Do not convert a transport error into `NO_JOBS_OBSERVED` or return a successful empty result.
