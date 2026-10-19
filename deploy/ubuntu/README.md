# Ubuntu ARM64 gateway with admission control

Four services: LiteLLM (with both logos), admission controller, PostgreSQL and Redis.
Requests follow OpenCode -> LiteLLM -> admission controller -> model endpoint.
The gateway machine needs no GPU. This archive starts a fresh database; existing
Windows keys/history and passwords are not included.

## 1. Check Docker

```bash
uname -m
docker version
docker compose version
```

`uname -m` must report `aarch64`. Install Docker Engine and the Compose plugin
if missing: https://docs.docker.com/engine/install/ubuntu/ . Use `sudo docker`
if required by your server's account permissions.

## 2. Transfer and extract

Copy `imvision-gateway-admission-ubuntu-arm64.tar.gz` to Ubuntu, then:

```bash
tar -xzf imvision-gateway-admission-ubuntu-arm64.tar.gz
cd imvision-gateway
sha256sum -c SHA256SUMS
docker load -i images.tar
```

All four ARM64 images are included; loading needs no image registry access.
Both logos are inside the gateway image and follow its hostname automatically.

## 3. Configure

```bash
cp .env.example .env
chmod 600 .env
nano .env
```

Fill the FIVE blank secret fields. Generate a different value for each using
`openssl rand -hex 24`. Prefix the LiteLLM master key with `sk-`.
Set `GATEWAY_BIND_ADDRESS` to the Ubuntu server's private/LAN IP for access
from your Windows computer. Leave 127.0.0.1 for access only on Ubuntu.
Only port 4000 is published. Restrict access to your company network; use HTTPS
before wider deployment.

Edit `config/backends.yaml` to match the services actually running on your Sparks.
The supplied defaults are qwen3.8-27b at 10.169.25.15:8000 and 10.169.25.16:8000.
Those services were stopped during packaging; start them or change the entries
before expecting model replies. The controller needs `/health`, `/tokenize`,
and `/v1/chat/completions`. URLs in this file are service roots WITHOUT `/v1`.
If backend authentication is enabled, set `api_key_env: SPARK_15_API_KEY`
(or SPARK_16_API_KEY) and fill its value in `.env`.

## 4. Start

```bash
docker compose config --quiet
docker compose up -d
docker compose ps
docker compose exec scheduler python -m app.admin status
```

Allow time for initial database setup. All four containers should become
healthy. Scheduler container health checks the process; check `healthy: true`
for each enabled model endpoint in the status output before testing requests.
If a Spark is stopped, its endpoint is unhealthy even though the controller
itself is healthy. Requests may wait until their queue deadline.

## 5. Create your key and use OpenCode

- UI: `http://SERVER_IP:4000/ui`
- Login: `admin`, using your chosen LITELLM_UI_PASSWORD.
- Create a virtual key allowing `qwen3.8-27b`.
- OpenCode base URL: `http://SERVER_IP:4000/v1`
- OpenCode model: `qwen3.8-27b`
- OpenCode key: your virtual key, not the internal scheduler secret.

Follow **[TESTING.md](TESTING.md)** for routing, waiting, and Python tests.

## Changing capacity or models

`config/backends.yaml` controls endpoints, models and per-endpoint capacity.
A TP group is ONE endpoint, regardless of GPU count. Multiple independent
replicas of the same model belong in one pool. All pool members must share
the model, tokenizer and chat template. Give a different model its own pool.
The default 32768 context / 4096 output limits are configurable gateway policy,
not hardware measurements. Set limits within the actual serving capabilities.

After editing backend YAML:

The entire `config` directory is mounted read-only into both application
containers at `/app/config`. This keeps saved file replacements visible inside
the containers. LiteLLM explicitly reads `/app/config/litellm.yaml`; its logos
remain embedded separately. Reload is still needed to apply scheduler changes.

```bash
docker compose exec scheduler python -m app.admin reload
```

To retire an endpoint: disable it, reload, wait for inflight=0, then remove it.
Adding a new public alias also requires an entry in `config/litellm.yaml`
pointing at `http://scheduler:8080/v1`, using SCHEDULER_INTERNAL_API_KEY and
`allowed_openai_params: [user]`. Restart LiteLLM after that edit.
Adding a direct Spark URL in LiteLLM's UI BYPASSES admission. The two layers
are not automatically synchronized. Keep `overwrite_user_with_key_hash: true`
so per-key limits cannot be bypassed by a caller-supplied user field.

`.env` controls per-key active and waiting limits, queue size and deadlines.
Run `docker compose up -d` after environment changes. Keep queue timeout <
scheduler request timeout < LiteLLM timeout. Backend YAML can change without
rebuilding either image. Changes to controller source require a new image.

## Persistence and limitations

`docker compose stop` preserves keys/history. Do not use `down -v` unless
intentionally deleting data. Existing installations should back up PostgreSQL
and keep their original Compose project name and secrets when upgrading;
this archive does not migrate data automatically.

Run exactly ONE scheduler container with ONE worker. Its fairness, queues and
active counts are in process memory; Redis does not coordinate scheduler
replicas. Restarting it interrupts queued/running requests. Limits apply to
requests passing through this scheduler, not direct calls to the Sparks.
Per-principal means per virtual key, not an aggregate across a person's keys.
Only chat completions are supported by the custom controller at present.
No Grafana/Prometheus containers are required; scheduler metrics are available
internally at /metrics if you add monitoring later.

The local test verifies admission routing/queueing with simulated backends,
not real GPU throughput. Real Spark inference must be checked after you start
your model servers. Native Ubuntu ARM startup still needs checking there.
Windows ARM emulation previously triggered Redis's ARM64-COW-BUG guard; do not
disable that safeguard. If seen on Ubuntu, check/update the host kernel.
