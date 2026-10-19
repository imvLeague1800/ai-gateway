"""Small concurrent gateway test. Prompts and API keys are never written to disk."""
import argparse
from concurrent.futures import ThreadPoolExecutor, as_completed
from getpass import getpass
import json
import time
import urllib.error
import urllib.request


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--url", default="http://localhost:4000/v1")
    parser.add_argument("--model", default="qwen3.8-27b")
    parser.add_argument("--keys", type=int, default=2)
    parser.add_argument("--requests", type=int, default=4)
    parser.add_argument("--concurrency", type=int, default=4)
    parser.add_argument("--max-tokens", type=int, default=128)
    parser.add_argument("--prompt", default="Explain how a rainbow forms in detail.")
    args = parser.parse_args()
    if min(args.keys, args.requests, args.concurrency, args.max_tokens) < 1:
        parser.error("Numeric options must be positive")
    keys = [getpass(f"Paste virtual key {i + 1} (hidden): ").strip() for i in range(args.keys)]
    if not all(keys):
        parser.error("Keys cannot be empty")

    def send(i):
        started = time.monotonic()
        body = {"model": args.model, "messages": [{"role": "user", "content": args.prompt}],
                "max_tokens": args.max_tokens}
        request = urllib.request.Request(args.url.rstrip("/") + "/chat/completions",
            data=json.dumps(body).encode(), headers={"Content-Type": "application/json",
            "Authorization": "Bearer " + keys[i % len(keys)]})
        result = {"request": i + 1, "key_number": i % len(keys) + 1}
        try:
            with urllib.request.urlopen(request, timeout=1350) as response:
                data = json.load(response)
                result.update(status=response.status, completion_id=data.get("id"), usage=data.get("usage"))
        except urllib.error.HTTPError as exc:
            result.update(status=exc.code, error=exc.read().decode(errors="replace")[:500])
        except Exception as exc:
            result.update(status="connection_error", error=str(exc))
        result["seconds"] = round(time.monotonic() - started, 3)
        return result

    started = time.monotonic()
    with ThreadPoolExecutor(max_workers=args.concurrency) as pool:
        results = [future.result() for future in as_completed([pool.submit(send, i) for i in range(args.requests)])]
    for result in results:
        print(json.dumps(result))
    print(json.dumps({"completed": len(results), "successful": sum(r["status"] == 200 for r in results),
                      "wall_seconds": round(time.monotonic() - started, 3)}))


if __name__ == "__main__":
    main()
