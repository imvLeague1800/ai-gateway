# Manual local testing

The local gateway has been simplified to standard LiteLLM connected directly
to both Sparks, backed by PostgreSQL and Redis.

Follow the [step-by-step instructions in the README](../README.md).

- Web UI: http://localhost:4000/ui
- OpenCode base URL: http://localhost:4000/v1
- Model: `qwen3.8-27b`
- UI login and starter user key: `LOCAL_ACCESS.txt` in the project root.

The previous launchers and load-test framework are removed. No custom script
is needed. Old localhost ports and `.verification` launcher instructions are
obsolete. Your personal key, usage database, and historical backups are retained.
