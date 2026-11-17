#!/usr/bin/env python3
"""The request-rate benchmark: httpclient against CeeSafe.Web's web_client, over loopback.

usage: python3 bench/run.py [--requests 100000] [--runs 3] [--routes 1k,64k,chunked] [--conc 1,16,64]
                            [--modes cosmo,stream,web] [--port 18290] [--json out.json]

It builds bench/ (`ceesafec build`), starts `bench server` as its own process, and for each route, concurrency and
mode runs the client `--runs` times. Reported for each: the best run's requests a second, the client process's CPU
time for a request (user + system, from the children's rusage), and its allocations for a request (two runs with
CEESAFE_ALLOC_REPORT at different request counts; the difference divided by the difference in requests). A run
that exits with an error is counted in the last column and left out of the best.

The modes: `cosmo` is this client with buffered responses, `stream` this client reading the body through a 16 KiB
buffer, `web` is web_client (`get`, `readToEnd`, `finish`). The server is the same for all: a keep-alive loop that
answers each GET from memory, so the numbers compare clients, not servers.

With --h2 the server is CeeSafe.Web's benchmarks/http (HTTP/1.1 and h2c on one port; build it there first with
`ceesafec build`, or name its binary with --web-server), the routes are its `small` (13 bytes), `4k` and `64k`, and
the modes are `cosmo` and `web` over HTTP/1.1 and `cosmo2` and `web2` over HTTP/2 by prior knowledge, where every
request of a run shares one connection.
"""
import argparse, json, os, pathlib, re, resource, socket, subprocess, sys, time

HERE = pathlib.Path(__file__).resolve().parent
BENCH = HERE / "out" / "bench"
SIZES = {"1k": 20, "64k": 4, "chunked": 20, "empty": 20}  # requests scale down for the large body
WEB_SERVER = HERE.parent.parent.parent / "CeeSafe.Web" / "benchmarks" / "http" / "out" / "bench_http"


def wait_for(port):
    for _ in range(100):
        try:
            socket.create_connection(("127.0.0.1", port), timeout=0.2).close()
            return
        except OSError:
            time.sleep(0.05)
    sys.exit("the server did not start")


def once(mode, url, requests, conc, env=None):
    before = resource.getrusage(resource.RUSAGE_CHILDREN)
    p = subprocess.run([str(BENCH), mode, url, str(requests), str(conc)], capture_output=True, text=True,
                       env={**os.environ, **(env or {})})
    after = resource.getrusage(resource.RUSAGE_CHILDREN)
    if p.returncode != 0:
        return None  # reported as a failed run
    line = dict(f.split("=") for f in p.stdout.split()[1:] if "=" in f)
    cpu = (after.ru_utime - before.ru_utime) + (after.ru_stime - before.ru_stime)
    user = after.ru_utime - before.ru_utime
    allocs = re.search(r"attempts=(\d+)", p.stdout + p.stderr)
    return {"rps": int(line["rps"]), "ms": int(line["ms"]), "requests": int(line["requests"]), "cpu": cpu,
            "user": user, "allocs": int(allocs.group(1)) if allocs else None}


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--requests", type=int, default=100000)
    ap.add_argument("--runs", type=int, default=3)
    ap.add_argument("--routes")
    ap.add_argument("--conc", default="1,16,64")
    ap.add_argument("--modes")
    ap.add_argument("--h2", action="store_true", help="HTTP/2 against CeeSafe.Web's benchmark server")
    ap.add_argument("--web-server", default=str(WEB_SERVER))
    ap.add_argument("--port", type=int, default=18290)
    ap.add_argument("--json")
    args = ap.parse_args()
    args.routes = args.routes or ("small,4k,64k" if args.h2 else "1k,64k,chunked")
    args.modes = args.modes or ("cosmo,cosmo2,web,web2" if args.h2 else "cosmo,stream,web")
    subprocess.run(["ceesafec", "build"], cwd=HERE, check=True)
    if args.h2:
        server = subprocess.Popen([args.web_server], env={**os.environ, "CW_PORT": str(args.port), "CW_WORKERS": "256"},
                                  stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL)
    else:
        server = subprocess.Popen([str(BENCH), "server", str(args.port)])
    rows = []
    try:
        wait_for(args.port)
        print("| route | conc | mode | req/s | CPU µs/req | user µs/req | allocs/req | failed runs |")
        print("|---|---:|---|---:|---:|---:|---:|---:|")
        for route in args.routes.split(","):
            url = f"http://127.0.0.1:{args.port}/{route}"
            for conc in [int(c) for c in args.conc.split(",")]:
                n = max(conc, args.requests * SIZES.get(route, 20) // 20)
                for mode in args.modes.split(","):
                    tried = [once(mode, url, n, conc) for _ in range(args.runs)]
                    runs = [r for r in tried if r]
                    failures = len(tried) - len(runs)
                    low = high = None
                    for _ in range(4):  # a failed run is tried again; its failure is counted
                        low = low or once(mode, url, 2000, conc, {"CEESAFE_ALLOC_REPORT": "1"})
                        high = high or once(mode, url, 12000, conc, {"CEESAFE_ALLOC_REPORT": "1"})
                    if not runs or not low or not high:
                        print(f"| {route} | {conc} | {mode} | failed | | | | {failures} |", flush=True)
                        continue
                    best = max(runs, key=lambda r: r["rps"])
                    per = (high["allocs"] - low["allocs"]) / (high["requests"] - low["requests"])
                    row = {"route": route, "conc": conc, "mode": mode, "rps": best["rps"],
                           "cpu_us": best["cpu"] / best["requests"] * 1e6,
                           "user_us": best["user"] / best["requests"] * 1e6, "allocs": per,
                           "all_rps": [r["rps"] for r in runs], "failed_runs": failures}
                    rows.append(row)
                    print(f"| {route} | {conc} | {mode} | {row['rps']} | {row['cpu_us']:.1f} | {row['user_us']:.1f} |"
                          f" {per:.1f} | {failures} |", flush=True)
    finally:
        server.terminate()
        server.wait()
    if args.json:
        pathlib.Path(args.json).write_text(json.dumps(rows, indent=1))


if __name__ == "__main__":
    main()
