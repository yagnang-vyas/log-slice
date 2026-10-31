"""Generate synthetic log files for benchmarking.

Usage:
    python tests/benchmarks/generate.py --format jsonl --size 1GB --out /tmp/bench.jsonl
    python tests/benchmarks/generate.py --format nginx --size 200MB --out /tmp/bench.log

Output is deterministic for a given --seed.
"""

from __future__ import annotations

import argparse
import json
import random
import re
import sys
from datetime import datetime, timedelta
from pathlib import Path

ENDPOINTS = [
    "/api/users", "/api/users/{id}", "/api/orders", "/api/orders/{id}", "/api/search",
    "/api/cart", "/api/login", "/api/logout", "/api/products", "/api/products/{id}",
    "/healthz", "/static/app.js", "/static/app.css", "/api/payments", "/api/inventory",
]  # fmt: skip
LEVELS = ["info"] * 90 + ["warn"] * 6 + ["error"] * 4
MESSAGES = [
    "request completed", "cache miss for key {id}", "db query slow", "upstream timeout after {n}ms",
    "user {id} authenticated", "payment declined for order {id}", "connection reset by peer",
]  # fmt: skip
STATUSES = [200] * 85 + [201] * 3 + [301] * 2 + [404] * 5 + [500] * 3 + [503] * 2
AGENTS = ["curl/8.4.0", "Mozilla/5.0 (X11; Linux x86_64)", "python-requests/2.31", "okhttp/4.12"]
SIZE_RE = re.compile(r"^(\d+(?:\.\d+)?)\s*(KB|MB|GB)?$", re.I)


def parse_size(text: str) -> int:
    match = SIZE_RE.match(text.strip())
    if not match:
        raise argparse.ArgumentTypeError(f"invalid size: {text!r} (examples: 100MB, 1GB)")
    unit = {"KB": 10**3, "MB": 10**6, "GB": 10**9, None: 1}[
        match.group(2).upper() if match.group(2) else None
    ]
    return int(float(match.group(1)) * unit)


def jsonl_line(rng: random.Random, when: datetime) -> str:
    endpoint = rng.choice(ENDPOINTS).replace("{id}", str(rng.randint(1, 100000)))
    status = rng.choice(STATUSES)
    level = "error" if status >= 500 else rng.choice(LEVELS)
    message = rng.choice(MESSAGES).format(id=rng.randint(1, 10**6), n=rng.randint(100, 5000))
    record = {
        "timestamp": when.strftime("%Y-%m-%dT%H:%M:%S.") + f"{when.microsecond // 1000:03d}Z",
        "level": level,
        "service": rng.choice(["api", "web", "worker"]),
        "message": message,
        "http": {
            "method": rng.choice(["GET", "GET", "GET", "POST", "PUT", "DELETE"]),
            "path": endpoint,
            "status": status,
            "user_agent": rng.choice(AGENTS),
        },
        "duration_ms": round(rng.lognormvariate(3.5, 1.0), 2),
        "bytes": rng.randint(100, 50000),
        "trace_id": f"{rng.getrandbits(64):016x}",
        "client": {"ip": f"10.{rng.randint(0, 255)}.{rng.randint(0, 255)}.{rng.randint(1, 254)}"},
    }
    return json.dumps(record, separators=(",", ":"))


def nginx_line(rng: random.Random, when: datetime) -> str:
    endpoint = rng.choice(ENDPOINTS).replace("{id}", str(rng.randint(1, 100000)))
    method = rng.choice(["GET", "GET", "GET", "POST", "PUT", "DELETE"])
    ip = f"10.{rng.randint(0, 255)}.{rng.randint(0, 255)}.{rng.randint(1, 254)}"
    rt = round(rng.lognormvariate(-2.5, 1.0), 3)
    return (
        f'{ip} - - [{when.strftime("%d/%b/%Y:%H:%M:%S")} +0000] "{method} {endpoint} HTTP/1.1" '
        f'{rng.choice(STATUSES)} {rng.randint(100, 50000)} "-" "{rng.choice(AGENTS)}" {rt}'
    )


def generate(fmt: str, size: int, out: Path, seed: int = 42) -> tuple[int, int]:
    """Write about `size` bytes of log lines to `out`; returns (rows, bytes)."""
    rng = random.Random(seed)
    make = jsonl_line if fmt == "jsonl" else nginx_line
    when = datetime(2026, 1, 1)
    written = rows = 0
    with out.open("w", encoding="utf-8") as handle:
        while written < size:
            chunk = []
            for _ in range(10_000):
                when += timedelta(milliseconds=rng.randint(1, 20))
                chunk.append(make(rng, when))
            text = "\n".join(chunk) + "\n"
            handle.write(text)
            written += len(text.encode("utf-8"))
            rows += len(chunk)
    return rows, written


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__.split("\n")[0])
    parser.add_argument("--format", choices=["jsonl", "nginx"], default="jsonl")
    parser.add_argument("--size", type=parse_size, default="100MB")
    parser.add_argument("--out", type=Path, required=True)
    parser.add_argument("--seed", type=int, default=42)
    args = parser.parse_args()
    rows, written = generate(args.format, args.size, args.out, args.seed)
    print(f"wrote {rows:,} rows, {written / 1e6:,.1f} MB to {args.out}", file=sys.stderr)
    return 0


if __name__ == "__main__":
    sys.exit(main())
