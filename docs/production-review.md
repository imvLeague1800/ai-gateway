# Production review — September 7, 2026

Historical review: the current manual test deployment uses native LiteLLM routing directly to both Sparks. The custom scheduler and monitoring stack are not running. See the [current manual instructions](../README.md).

Current topology update: Spark 15 (`10.169.25.15:8000`) and Spark 16
(`10.169.25.16:8000`) are both enabled. The client-facing model is now
`qwen3.8-27b`; the earlier `code-32k` name was a gateway alias. Both backends
served simultaneous test requests successfully, and the renamed model passed
an OpenCode check. See the [current deployment record](local-test-deployment.md)
for the latest results; earlier findings below retain their original scope.

The project has a sound small-service structure, but it is not yet verified for
production. Configuration, backend state, admission, health, tokenization, and
metrics already have separate modules. The main gaps were error-path cleanup,
safe reconfiguration, and deployment validation. Those code paths now have
regression coverage; the remaining concerns below need an integration run and
operational decisions.

## Fixed during this review

| Finding | Result |
|---|---|
| Failed reads, cancelled sends, malformed streaming options, or failed cleanup could leak reserved capacity | Unified cleanup releases backend and per-key counters, including streaming disconnect and response-header failures |
| Backend HTTP operations could wait indefinitely | Finite, configurable connect/read/write/pool timeouts; invalid settings fail validation |
| Requests validated before reload could use a different model/pool afterward | Configuration revisions reject stale queued/prepared requests with retryable `503` |
| Changed backend URLs inherited old health, and old probes could overwrite new health | Reset health on replacement, reject active endpoint changes, ignore stale probes |
| Expired waiters could receive capacity; blocked users' age could promote another user's new long request | Check deadlines before reserving, and promote using the eligible request's own age |
| Chunked bodies were buffered before their size was checked | Enforce the size cap while receiving the body |
| Invalid numeric JSON and non-ASCII credentials could produce server errors | Reject invalid input before admission; malformed credentials return `401` |
| Arbitrary unknown model names created unbounded monitoring labels | Use one bounded `unknown` label |
| Model aliases containing slashes were incorrectly shortened | Exact alias lookup, then optional `openai/` provider-prefix lookup |
| Custom chat templates and special-token options were omitted from tokenization | Forward both options, confirmed against the deployed service schema |
| Default backend pointed at an optional container, without the actual Spark hosts | Verified Spark 15 configured; Spark 16 recorded without inventing an endpoint |
| Existing keys could not have their quotas changed with the supplied command | Added validated partial key updates; unspecified fields are preserved |
| GPU allocation was fixed to one local device | Configurable GPU count, tensor parallel size, and model mount directory |
| HTTP listener exposed every interface; database credentials could break connection URLs | Configurable loopback default and explicit encoded `DATABASE_URL` override |
| Latency histogram buckets ended at ten seconds | Extended queue and request duration buckets through 1,200 seconds |
| Disconnected queued/nonstream callers could leave work running | Watch disconnect after body upload, cancel tokenization/inference, and remove abandoned queue entries |
| Streaming could outlive every idle timeout by continuing to send bytes | A configurable total scheduler deadline now covers upload through the final response |
| Timeout fallback could produce malformed HTTP or streaming events | Track successful header transmission and complete event boundaries; close partial events as truncated |
| An upstream connection close could hang forever during cleanup | Bound close time before releasing concurrency counters |
| Retired backend monitoring labels remained active | Remove retired gauges and expose enabled state so disabled Spark 16 does not trigger backend alerts |
| Real LiteLLM filtered out the stamped identity for the custom alias | Explicitly allow the `user` parameter and validate that contract before deployment |

## Remaining findings, in priority order

1. **High — wider access needs network protections.**
   Local end-to-end tests now verify authentication, forged-identity rejection,
   per-key quotas and quota updates, and disconnect cleanup. Spark 15 accepts
   generation without an upstream key; direct calls bypass the gateway's
   limits. Restrict backend ingress before wider use and configure HTTPS for
   remote clients. The test gateway remains on loopback; a public/internal
   HTTPS reverse proxy is outside this repository.

2. **High — production capacity and compatibility need measured tests.**
   Four active requests, one long request, and 32K context are starting policies,
   not measured Spark capacity. The live service reports
   `0.26.1rc1.dev1046+gba07e4a48` and 262,144 context tokens. Pin tested image
   digests, including LiteLLM (minimum 1.95.0 for trusted key stamping). The
   local test deployment now pins its tested images and runs LiteLLM 1.100.0;
   base example defaults remain mutable for setup. Small OpenCode, tool-call,
   cancellation, and two-user tests passed. Sustained coding workloads, long
   prompts, queue saturation, and backend restarts still need representative
   capacity tests before raising limits.
   The deployed token-count schema lacks generation's `tool_choice` and
   `truncate_prompt_tokens` fields, so exact parity for all prompt options has
   not been established; the tested named-tool call did match prompt counts.
   All replicas in one pool must have identical model,
   tokenizer, and chat-template behavior.

3. **Medium — client and reverse-proxy deadlines remain deployment settings.**
   The scheduler now has a 1,260-second total deadline and LiteLLM a longer
   1,320-second native timeout. Validation checks alignment with the queue and
   any explicit LiteLLM YAML overrides. Configure client and reverse-proxy
   deadlines above the gateway budget with a margin for connection setup.

4. **Medium — tokenizer input errors become retryable server errors.**
   `scheduler/app/tokens.py` wraps upstream `400`/`422` responses as tokenizer
   unavailability; `main.py` returns `503`. Invalid messages or tool schemas can
   therefore trigger pointless retries. Separate invalid client input from
   unavailable/misconfigured tokenizers, without exposing internal errors.

5. **Medium — readiness and monitoring are incomplete for multiple models.**
   Readiness means any enabled backend is healthy, even if another model pool
   cannot serve. Removed model monitoring labels still persist until restart;
   backend gauge retirement is fixed. Per-model readiness remains useful.
   Five alert rules and their scenario tests are now provided, along with an
   operations runbook. PostgreSQL restored into a separate test database, and
   Redis snapshot integrity passed. Alert delivery, complete Redis restore, and
   application recovery against restored data remain operational follow-up.

## Organization and scaling

- Keep one scheduler process and one scheduler replica: admission state is in
  memory. More workers would independently admit requests and multiply limits.
  Horizontal gateway scaling needs shared, atomic admission state first.
- Current fairness is per key **per model alias**. LiteLLM supplies key-wide
  quotas. If strict fairness across different models is required, add a shared
  arbitration policy rather than assuming the present queues provide it.
- Preserve the existing module boundaries. A useful next refactor is moving
  upstream response handling and input validation out of the growing `main.py`;
  its module-level `app = create_app()` also loads configuration on import.
  Keep that refactor separate from changing scheduling behavior.
- Python dependencies are locked and a basic continuous integration workflow
  now runs tests and validates Compose. The supplied repository had no commits
  and all source files were untracked at review time; establish a reviewed
  baseline and release process before rollout.

## Verification performed

- All 237 local automated tests passed, including failure, cancellation, reload, quota,
  and simulated multi-backend scenarios. One upstream Starlette test-client
  deprecation warning remains.
- Both shipped backend configurations passed schema validation.
- Compose passed validation with the optional local-vLLM profile. All six
  services are deployed in the isolated local test stack. See the
  [deployment record](local-test-deployment.md) for connection settings and
  actual integration results.
- The dependency lock passed an offline consistency check with versions
  preserved.
- Initial read-only live checks confirmed DNS to `10.169.25.15`, health, model ID
  `qwen3.8-27b`, service version, API schema, and tokenization. These checks used
  the development workstation, not the deployment container. No generation was
  performed by those initial checks, no user keys were changed, and Spark 16
  was not probed.
- Follow-up live scheduler checks passed ordinary generation, streaming, and a
  forced tool call. Exact prompt counts matched reported usage (19, 19, and
  287 tokens); all reservations were released. These three calls generated
  30 output tokens in total and did not change the model deployment. They
  exercise the scheduler-to-model path, not the complete OpenCode/LiteLLM path.
- Official Prometheus 3.5.0 tooling validated the configuration, all five alert
  rules, and six rule scenarios. The documented image-digest override process
  was checked for Prometheus. See the operations runbook for scope and limits.

Official references: [LiteLLM trusted key identity](https://docs.litellm.ai/docs/proxy/virtual_keys#overwrite-outgoing-user-with-the-key-hash),
[LiteLLM partial key updates](https://docs.litellm.ai/docs/proxy/virtual_keys#custom-keyupdate),
[Docker GPU allocation](https://docs.docker.com/compose/how-tos/gpu-support/).
