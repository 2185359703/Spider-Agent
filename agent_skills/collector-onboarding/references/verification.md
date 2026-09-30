# Verification gates

All gates use the exact candidate worktree and generated files.

## Deterministic checks

Run at least:

```text
python -m compileall collectors/<platform_key>.py
pytest tests/test_<platform_key>.py
ruff check --isolated --select E,F,I,UP,B --target-version py312 --line-length 100 collectors/<platform_key>.py tests/test_<platform_key>.py
git diff --check
```

The platform test must exercise the captured method, endpoint and request body/query; response envelope and decode step; pagination termination; internship inclusion and full-time exclusion; unified required fields; and sensitive-field removal.

Fixtures passing is necessary but insufficient.

## Live gate

Run the collector against the supplied public entry in a bounded validation profile. When analysis observed internships, the live run must return at least one record. Inspect up to three records and require:

- `source_platform is None`;
- non-empty ID, title and URL;
- description or requirements present;
- no secrets in raw payload or trace.

If fixture tests pass but the live gate fails, do not commit a successful candidate. Report the actual endpoint/status/error, add it to the failure bundle, and repair the collector.

Zero live records may pass only when the report classification is a verified empty/no-internship state and entry/list evidence supports that classification.

## Git gate

- Every changed path must match `generation.allowed_files`.
- TOML must not contain `platform_id` before mapping approval.
- No `.env`, logs, output data, browser profiles, caches or credentials may be staged.
- Commit messages use `<type>(<scope>): <subject>`.
- The result is a local commit. `push_status` remains `DISABLED` unless a later explicit policy authorizes remote delivery.
