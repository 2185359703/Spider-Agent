# Collector delivery gate

A result is a collector only when all applicable checks pass:

1. the real list endpoint and detail behavior are confirmed;
2. moving state and decode/sign/bootstrap steps are named;
3. each decoder/helper passes a fixed captured vector;
4. the live business request succeeds repeatedly in a bounded run;
5. pagination and termination work beyond the first page when data spans pages;
6. required session and proxy behavior are explicit;
7. the final runtime has no browser/page/profile dependency;
8. unexpected response shapes fail instead of returning a successful empty list;
9. required fields survive local decode and normalization;
10. all saved evidence and fixtures are redacted.

If these checks fail, return the lower capability that is actually proven plus the precise blocker. Do not package evidence or a fixture-only parser as a collector.

For recruitment onboarding, this gate is followed by the `collector-onboarding` contract: nullable business IDs, internship filtering, local Git candidate, human run and human review.
