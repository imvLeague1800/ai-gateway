# Gateway operations

The four services are LiteLLM, scheduler, PostgreSQL and Redis.
Both applications mount the whole `config` directory read-only at `/app/config`,
so editors that replace files on save do not leave stale individual file mounts.
See [setup](../README.md) and [routing/queue tests](../deploy/ubuntu/TESTING.md).

- Start: `docker compose up -d`
- State: `docker compose exec scheduler python -m app.admin status`
- Reload backend YAML: `docker compose exec scheduler python -m app.admin reload`
- Routing/wait records: `docker compose logs -f scheduler`
- Stop preserving data: `docker compose stop`

Do not use `down -v` unless intentionally erasing keys/history. Run a single
scheduler replica/worker; its admission state is in memory. Backend reloads
preserve active accounting and reject removal of active endpoints. To retire
an endpoint, disable it, reload, wait for inflight to reach zero, then remove it.
Health checks use /health; token counting requires the vLLM /tokenize endpoint.
