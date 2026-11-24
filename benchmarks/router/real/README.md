# Real Agent Sessions (Pending)

No company-authorized real Agent sessions were supplied for the 2026-11-23
retest. Do not place synthetic sessions here or label them as real.

When authorized transcripts become available:

1. Store the original transcript under the internal access-controlled location.
2. Scrub secrets, internal addresses and identities with stable placeholders.
3. Document the transformation rules and validate the scrubbed case.
4. Record the source, allowed internal-use scope, session count, call count,
   prefix length distribution and SHA-256 in `../manifest.json`.
5. Put at least 20% of the real sessions in the calibration partition; freeze
   the remaining set before tuning.
6. Keep real and synthetic cases in separate files; do not merge their sources
   in reported metrics.

Required format per real case: stable case ID, full messages/tools, expected
answer or tool result, scoring method, TTFT/completion budget, session ID, step
ID, parent step, actual assistant/tool result, inter-step gap, and a note that
this is a historical replay (not a live closed-loop run). Closed-loop tasks
additionally need their executable test and fixture.
