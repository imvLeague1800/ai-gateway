# Test routing and admission, step by step

Run these commands in the directory containing compose.yaml. They work on
Windows Docker Desktop and Ubuntu unless marked otherwise.

## 1. Check your model services

Start the model servers first, then:

```text
docker compose ps
docker compose exec scheduler python -m app.admin status
```

Each intended backend must have `enabled: true` and `healthy: true`.
`capacity` is its configured simultaneous-request limit; `inflight` is current
work dispatched by this controller. `queues` shows requests still waiting.
GPU utilization itself must be checked on the serving machines.

## 2. Test one OpenCode request

Use your existing URL/key/model (Ubuntu users substitute their server IP).
In another terminal run:

```text
docker compose logs -f --since 1m scheduler
```

Send a message in OpenCode. Look for:

- `scheduler_request_admitted`: backend_id, request_id, principal_id and
  queue_wait_seconds. principal_id is the virtual-key hash, never the raw key.
- `scheduler_request_finished`: same request_id, outcome and total duration.

LiteLLM UI -> Logs/Usage shows key alias, tokens and request records. Its model
endpoint is now the scheduler; use scheduler logs for the actual Spark route.
The scheduler request_id and LiteLLM completion ID are separate identifiers;
correlate by key hash and time. Sequential requests can all select the same
backend because idle-load ties are deterministic. Use overlapping requests
to verify both endpoints are used.

## 3. Test two endpoints and waiting

Create TWO temporary virtual keys in LiteLLM, both allowing the model.
Default per-key admission permits one active request and two waiting requests.
With Python 3 installed:

Ubuntu (bundle directory):
```bash
python3 test_requests.py --url http://SERVER_IP:4000/v1 --keys 2 --requests 4 --concurrency 4
```

Windows (repository directory):
```powershell
python deploy/ubuntu/test_requests.py --keys 2 --requests 4 --concurrency 4
```

Paste the two different keys at the hidden prompts. The script sends four real
requests, two per key. With two healthy equal-capacity backends, overlapping
work should use both; the second request per key waits. Check scheduler logs
for both backend IDs and nonzero waiting times. While requests run, use the
status command again to see inflight and queues. Increase --max-tokens if
responses finish too quickly to observe. Default 128 can be consumed by a
reasoning model before it returns visible answer text; this still tests routing.

## 4. Test backend capacity limits

For an easy-to-observe test, temporarily set EACH backend's
`max_concurrent_requests: 1` and `max_long_requests: 1` in config/backends.yaml.
Reload with:

```text
docker compose exec scheduler python -m app.admin reload
```

Create three keys and run the script with `--keys 3 --requests 3 --concurrency 3`.
Two healthy endpoints can run two requests; the third waits for a slot.
Watch status while it runs. Restore your original backend limits and reload
when done. The provided normal defaults are 4 total / 1 long per endpoint;
these are starting policy values, not measured safe hardware capacities.

## 5. Test queue rejection and modest pressure

Use ONE key with `--keys 1 --requests 6 --concurrency 6`. With defaults, one
runs and two can wait; excess concurrent requests should receive HTTP 429
if they arrive before slots free up. That is admission working, not a crash.
LiteLLM key quotas can also produce 429; inspect its error and scheduler logs.

Increase requests/concurrency gradually and use prompts representative of your
work. The script reports each HTTP status, total seconds, token usage and the
LiteLLM completion ID, plus total wall time. Client elapsed time includes queue,
network and generation; scheduler queue_wait_seconds isolates admission waiting.
This small script is a smoke/load check, not a complete production benchmark.
For throughput across both endpoints with defaults, use multiple keys; one key
is intentionally limited to one active request.

The script has --model, --prompt and --max-tokens options. It needs no Python
packages. It does not store keys or prompts. Delete temporary virtual keys in
the UI after testing; logs/history remain available.
