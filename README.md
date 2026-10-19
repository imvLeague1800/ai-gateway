# ai-gateway

This computer runs four Docker containers: **LiteLLM, the admission controller, PostgreSQL, and Redis**.
Requests follow OpenCode -> LiteLLM -> admission controller -> model endpoint.
See [testing instructions](deploy/ubuntu/TESTING.md) for routing and queue checks.

| Item | Value |
|---|---|
| LiteLLM web UI | http://localhost:4000/ui |
| OpenCode base URL | http://localhost:4000/v1 |
| Model name | `qwen3.8-27b` |
| Spark 15 | `http://10.169.25.15:8000/v1` |
| Spark 16 | `http://10.169.25.16:8000/v1` |
| Local credentials | `LOCAL_ACCESS.txt` (private, ignored by Git) |

## 1. Log in to LiteLLM

Open **http://localhost:4000/ui**. Use username **admin** and the UI password in
`LOCAL_ACCESS.txt`.

## 2. Create a user key

Open **Virtual Keys / Keys**, then **Create Key**. Give it a name, allow
`qwen3.8-27b`, choose the limits you want, and copy the generated key.
A working starter user key is also in `LOCAL_ACCESS.txt` if you want to test
immediately. Use a user key in OpenCode, not the administrator/master key.

## 3. Configure OpenCode manually

Use your normal OpenAI-compatible provider configuration and enter:

- **Base URL:** `http://localhost:4000/v1`
- **API key:** the user key from step 2 (or the supplied starter key)
- **Model:** `qwen3.8-27b`

Keep the base URL at `/v1`; do not append `/chat/completions`.
Select that provider/model in OpenCode and send a message, such as “Reply OK”.
No special OpenCode command, environment wrapper, or test script is required.
Your existing OpenCode settings have not been edited automatically.
[OpenCode provider documentation](https://opencode.ai/docs/providers/)

## 4. Change settings and inspect usage

Use LiteLLM's **Keys**, **Usage**, and **Logs** pages for keys and accounting.
LiteLLM now records the admission-controller endpoint. Actual Spark selection
and waiting time appear in `docker compose logs -f scheduler`.

- `config/backends.yaml`: model-to-pool mappings, serving URLs, context/output
  policy, endpoint capacity and weights. One entry per independent replica or TP group.
- `config/litellm.yaml`: public aliases routed to `http://scheduler:8080/v1`.
- `.env`: UI credentials, internal scheduler secret, per-key active/queue limits,
  queue deadlines and gateway port.

After backend YAML edits: `docker compose exec scheduler python -m app.admin reload`.
After LiteLLM YAML edits: `docker compose restart litellm`.
After environment edits: `docker compose up -d`.
Adding a model in LiteLLM alone does not configure admission. Add its route and
pool to the backend YAML too, and route the LiteLLM entry through the scheduler.
A LiteLLM entry pointing directly to a Spark bypasses admission.

## Docker controls

For the portable Ubuntu ARM64 image with logos embedded, see
[Ubuntu deployment instructions](deploy/ubuntu/README.md). The transfer archive
is `dist/imvision-gateway-admission-ubuntu-arm64.tar.gz`. It includes the gateway,
admission controller, PostgreSQL, and Redis images but no existing credentials or database data.
`Dockerfile.gateway` builds the branded image from the tested upstream release.

The logo uses `branding/logo-light.png` (black artwork) in light mode and
`branding/logo-dark.png` (white artwork) in dark mode. Both files are mounted
read-only into LiteLLM's existing UI asset directory and served locally.
`UI_LOGO_PATH` and `UI_LOGO_PATH_DARK` can override the image URLs in `.env`;
update them if using a different gateway hostname. `LITELLM_BRANDING_DIR`
overrides the asset directory if a future image changes its UI location.

From the project root (the folder containing `compose.yaml`):

```powershell
docker compose up -d
docker compose ps
docker compose logs -f litellm
docker compose stop
```

Stopping preserves keys and usage. Do not add `-v` to `docker compose down`
unless you intend to erase the database volumes. The local `.env` retains the
existing Compose project name to preserve those volumes automatically.

The gateway is reachable only from this computer. Existing keys and history
remain in the same database volumes. Run exactly one admission container with
one worker: its queue and concurrency counters live in memory. Redis does not
coordinate multiple admission replicas. A scheduler restart interrupts queued
and running requests. The controller currently supports chat completions;
other API types require separate implementation before routing through it.
