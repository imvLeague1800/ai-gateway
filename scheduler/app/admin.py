"""Inspect admission state or reload backend YAML inside the container."""
import argparse
import json
import os
import urllib.request


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("action", choices=["status", "reload"])
    args = parser.parse_args()
    path, method = ("backends", "GET") if args.action == "status" else ("reload", "POST")
    request = urllib.request.Request(
        "http://localhost:8080/admin/" + path,
        method=method,
        headers={"Authorization": "Bearer " + os.environ["SCHEDULER_INTERNAL_API_KEY"]},
    )
    with urllib.request.urlopen(request, timeout=30) as response:
        print(json.dumps(json.load(response), indent=2))


if __name__ == "__main__":
    main()
