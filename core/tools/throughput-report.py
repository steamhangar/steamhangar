#!/usr/bin/env python3
"""Analyse the vault-core throughput debug log (WP THROUGHPUT-DEBUG-1).

Input: the tab-separated lines vault-core writes to
/vault/logs/throughput-debug/throughput.log (and its rotated
throughput.<epoch>.log files) while VAULT_DEBUG_THROUGHPUT is on. Lines
starting with '#' (the startup line with the rendered nginx settings) are
echoed once. Python 3 standard library only.

Usage:
    python3 throughput-report.py [--link-mbit 1000] [--client ADDR]
                                 [--min-request-kb 256] [--probe-log FILE]
                                 FILE [FILE ...]

Fields (tp1): msec client depot method status range bytes_sent request_time
upstream_response_time cache connection connection_requests request_length
tcp_rtt_us tcp_rttvar_us tcp_snd_cwnd tcp_rcv_space

Heartbeat lines (tag hb1, one per GET /lancache-heartbeat, same file): msec
client status request_time connection connection_requests tcp_rtt_us. They get
their own "Heartbeat" section (count, p50/p95/max request_time, number of 499,
and the worst 10 s buckets next to the slowest small depot requests of the
same bucket); logs without hb lines print "no heartbeat lines". These are
nginx-side times only: a heartbeat that queues while the single worker is
blocked waits BEFORE nginx reads it, so its request_time stays ~0 ms (cold-game
test 2026-10-18, ADR-0027). The section says so; --probe-log adds an outside
probe's timings, one line per probe: "<epoch seconds or ISO-8601 time>
<time_total in seconds> [<http code>]", e.g. from
    while sleep 5; do echo "$(date +%s.%N) $(curl -s -o /dev/null \
        -w '%{time_total} %{http_code}' http://<vault-core>/lancache-heartbeat)"; done

Large requests: --min-request-kb counts KiB (1024 bytes). Every 200/206
request at or above the floor counts as "above the size floor"; per-request
MB/s needs a request_time > 0, so requests logged with request_time 0.000
(nginx's cached clock did not advance while it served them, e.g. a worker
blocked in sendfile on cold reads) are counted and reported, never silently
dropped.

Per client it prints total MB, minutes covered, aggregate Mbit/s per 10 s
bucket (average and p95 over the full buckets, edge buckets dropped), per-request throughput
(bytes / request_time, requests >= --min-request-kb), connection counts,
TCP medians, HIT/MISS share and a short list of plain-English verdicts.
The verdicts are heuristics that point at the first thing to look at, not
proof; the numbers above them are the evidence.
"""
from __future__ import annotations

import argparse
import statistics
import sys
import time
from collections import defaultdict
from datetime import datetime, timezone
from typing import Iterable, Optional

FIELDS = ("msec client depot method status range bytes_sent request_time "
          "upstream_response_time cache connection connection_requests "
          "request_length rtt rttvar cwnd rcv_space").split()
BUCKET_S = 10
MIN_BIG_REQUESTS = 30   # large requests before a verdict is given
MIN_FULL_BUCKETS = 3    # complete 10 s buckets before a verdict is given
MSS = 1448  # bytes per segment on Ethernet, for the cwnd ceiling estimate


def _num(text: str) -> Optional[float]:
    """A number, or None for '-' / junk. Comma lists (upstream retries) sum."""
    text = text.strip()
    if not text or text == "-":
        return None
    try:
        return float(text)
    except ValueError:
        pass
    try:
        return sum(float(p) for p in text.replace(" ", "").split(",") if p not in ("", "-"))
    except ValueError:
        return None


class Req:
    __slots__ = ("end", "start", "client", "status", "bytes", "rt", "up", "cache",
                 "conn", "conn_reqs", "rtt", "cwnd", "rcv")

    def __init__(self, f: list[str]) -> None:
        self.end = float(f[1])
        self.client = f[2]
        self.status = f[5]
        self.bytes = int(float(f[7]))
        self.rt = _num(f[8]) or 0.0
        self.up = _num(f[9])
        self.cache = f[10]
        self.conn = f[11]
        self.conn_reqs = int(float(f[12]))
        self.rtt = _num(f[14])
        self.cwnd = _num(f[16])
        self.rcv = _num(f[17])
        self.start = self.end - self.rt


class Heartbeat:
    __slots__ = ("end", "client", "status", "rt")

    def __init__(self, f: list[str]) -> None:
        self.end = float(f[1])
        self.client = f[2]
        self.status = f[3]
        self.rt = _num(f[4]) or 0.0


def parse_all(lines: Iterable[str]) -> tuple[list[Req], list[Heartbeat], list[str], int]:
    """(requests, heartbeats, startup lines, number of malformed lines)."""
    reqs: list[Req] = []
    hbs: list[Heartbeat] = []
    starts: list[str] = []
    bad = 0
    for raw in lines:
        line = raw.rstrip("\r\n")
        if not line:
            continue
        if line.startswith("#"):
            if line.startswith("# start"):
                starts.append(line[2:])
            continue
        f = line.split("\t")
        try:
            if f[0] == "tp1" and len(f) == 18:
                reqs.append(Req(f))
            elif f[0] == "hb1" and len(f) == 8:
                hbs.append(Heartbeat(f))
            else:
                bad += 1
        except ValueError:
            bad += 1
    return reqs, hbs, starts, bad


def parse(lines: Iterable[str]) -> tuple[list[Req], list[str], int]:
    """(requests, startup lines, number of malformed lines); heartbeat lines are valid and skipped here."""
    reqs, _hbs, starts, bad = parse_all(lines)
    return reqs, starts, bad


def pct(values: list[float], p: float) -> float:
    """Linear-interpolated percentile, p in 0..100."""
    if not values:
        return 0.0
    s = sorted(values)
    k = (len(s) - 1) * p / 100.0
    lo = int(k)
    hi = min(lo + 1, len(s) - 1)
    return s[lo] + (s[hi] - s[lo]) * (k - lo)


def bucket_mbit(reqs: list[Req]) -> dict[int, float]:
    """Mbit/s per 10 s bucket; a request's bytes are spread over its run time."""
    acc: dict[int, float] = defaultdict(float)
    for r in reqs:
        if r.rt <= 0.001:
            acc[int(r.end // BUCKET_S)] += r.bytes
            continue
        b0, b1 = int(r.start // BUCKET_S), int(r.end // BUCKET_S)
        for b in range(b0, b1 + 1):
            lo, hi = max(r.start, b * BUCKET_S), min(r.end, (b + 1) * BUCKET_S)
            if hi > lo:
                acc[b] += r.bytes * (hi - lo) / r.rt
    return {b: v * 8 / BUCKET_S / 1e6 for b, v in acc.items()}


def concurrency(reqs: list[Req]) -> dict[int, int]:
    """Distinct $connection per second (a request counts for every second it ran)."""
    per_sec: dict[int, set[str]] = defaultdict(set)
    for r in reqs:
        for s in range(int(r.start), int(r.end) + 1):
            per_sec[s].add(r.conn)
    return {s: len(c) for s, c in per_sec.items()}


def analyse(reqs: list[Req], link_mbit: float, min_kb: int) -> dict:
    n = len(reqs)
    total = sum(r.bytes for r in reqs)
    t0, t1 = min(r.start for r in reqs), max(r.end for r in reqs)
    bm = bucket_mbit(reqs)
    # The first and last bucket are partial (the log starts and ends mid-bucket);
    # leave them out of the statistics when there are enough others.
    keys = sorted(bm)
    if len(keys) >= 4:
        keys = keys[1:-1]
    buckets = [bm[k] for k in keys]
    n_buckets = len(buckets)
    # Size floor first, timing second: the count of large requests must not
    # depend on whether nginx's clock advanced (TP-REPORT-2). Requests with
    # request_time 0.000 cannot give a rate and are reported separately.
    big = [r for r in reqs if r.bytes >= min_kb * 1024 and r.status in ("200", "206")]
    timed = [r for r in big if r.rt > 0]
    per_req = [r.bytes * 8 / r.rt / 1e6 for r in timed]  # Mbit/s
    conc = list(concurrency(reqs).values())
    conns = {r.conn for r in reqs}
    rtts = [r.rtt for r in reqs if r.rtt is not None]
    cwnds = [r.cwnd for r in reqs if r.cwnd is not None]
    rcvs = [r.rcv for r in reqs if r.rcv is not None]
    miss = [r for r in reqs if r.cache not in ("HIT",)]
    miss_bytes = sum(r.bytes for r in miss)
    up_time = sum(r.up or 0.0 for r in miss)
    miss_time = sum(r.rt for r in miss)
    a = {
        "requests": n,
        "mb": total / 1e6,
        "minutes": (t1 - t0) / 60.0,
        "full_buckets": n_buckets,
        "bucket_avg": statistics.fmean(buckets) if buckets else 0.0,
        "bucket_p95": pct(buckets, 95),
        "bucket_max": max(buckets) if buckets else 0.0,
        "big_n": len(big),
        "timed_n": len(timed),
        "min_kb": min_kb,
        "per_req_p10": pct(per_req, 10), "per_req_p50": pct(per_req, 50), "per_req_p90": pct(per_req, 90),
        "conc_max": max(conc) if conc else 0,
        "conc_median": statistics.median(conc) if conc else 0,
        "connections": len(conns),
        "reqs_per_conn": n / len(conns) if conns else 0.0,
        "reqs_per_conn_max": max(r.conn_reqs for r in reqs),
        "rtt_ms": statistics.median(rtts) / 1000.0 if rtts else None,
        "cwnd": statistics.median(cwnds) if cwnds else None,
        "rcv_space": statistics.median(rcvs) if rcvs else None,
        "hit_n": 100.0 * (n - len(miss)) / n,
        "hit_bytes": 100.0 * (total - miss_bytes) / total if total else 0.0,
        "miss_n": 100.0 * len(miss) / n,
        "up_share": 100.0 * up_time / miss_time if miss_time > 0 else 0.0,
        "link": link_mbit,
    }
    a["verdicts"] = verdicts(a)
    return a


def verdicts(a: dict) -> list[str]:
    out: list[str] = []
    link = a["link"]
    low_agg = a["bucket_p95"] < 0.8 * link
    rtt, cwnd = a["rtt_ms"], a["cwnd"]
    untimed = a["big_n"] - a["timed_n"]
    if a["big_n"] and untimed * 2 >= a["big_n"]:
        out.append(f"{untimed} of {a['big_n']} large requests ({100.0 * untimed / a['big_n']:.0f}%) logged request_time "
                   "0.000: nginx's clock did not advance while it served them (worker blocked, e.g. blocking sendfile "
                   "on cold reads); per-request MB/s covers only the rest and is biased; trust the aggregate figures")
    if a["timed_n"] < MIN_BIG_REQUESTS or a["full_buckets"] < MIN_FULL_BUCKETS:
        out.append(f"sample too small for a verdict ({a['timed_n']} timed large requests, {a['full_buckets']} full "
                   f"{BUCKET_S}s buckets; need {MIN_BIG_REQUESTS} and {MIN_FULL_BUCKETS}): record a longer run")
        return out
    if a["miss_n"] >= 10:
        out.append(f"{a['miss_n']:.0f}% of the requests were not HITs: this run is not a pure cache-read test")
        if a["up_share"] >= 50:
            out.append(f"request_time of the non-HIT requests is {a['up_share']:.0f}% upstream_response_time: "
                       "dominated by the upstream, the miss path (not the cache read)")
    if not low_agg:
        out.append(f"aggregate p95 {a['bucket_p95']:.0f} Mbit/s is within 80% of the {link:.0f} Mbit/s link: "
                   "no server-side bottleneck visible")
        return out
    p50 = a["per_req_p50"]
    if a["conc_max"] <= 5:
        if p50 >= 0.5 * link:
            out.append(f"only {a['conc_max']} parallel connection(s) at most, but each stream is fast "
                       f"(per-request p50 {p50:.0f} Mbit/s): not limited by the cache path in this log (client or "
                       "network side cannot be told apart here); the cap is the number of streams")
        else:
            out.append(f"few connections ({a['conc_max']} at most) AND slow single streams (per-request p50 "
                       f"{p50:.0f} Mbit/s, below half the {link:.0f} Mbit/s link): server, disk or the client's "
                       "receive window; the client opening more connections would not be the whole story")
    elif p50 * a["conc_median"] < 0.8 * link or p50 < 0.3 * link:
        if rtt is not None and rtt <= 5 and a["hit_n"] >= 90:
            out.append(f"per-request p50 {p50 / 8:.1f} MB/s over {a['conc_max']} connections with low rtt "
                       f"({rtt:.1f} ms) and all HIT: server or disk limited (check the start line: "
                       "worker_processes, sendfile, aio, directio, output_buffers; and cache disk read speed)")
        else:
            out.append(f"per-request p50 {p50 / 8:.1f} MB/s is low for {a['conc_max']} connections")
    if rtt is not None and rtt > 20:
        out.append(f"rtt median {rtt:.1f} ms is high for a LAN: look at the network path (Wi-Fi, switch, bufferbloat)")
    if rtt and cwnd:
        ceiling = cwnd * MSS * 8 / (rtt / 1000.0) / 1e6
        if ceiling < 1.2 * p50:
            out.append(f"cwnd {cwnd:.0f} segments at rtt {rtt:.1f} ms allows only ~{ceiling:.0f} Mbit/s per connection: "
                       "TCP window limited (loss, idle restarts or slow-start after pauses)")
    if a["connections"] > 20 and a["reqs_per_conn"] <= 2:
        out.append(f"only {a['reqs_per_conn']:.1f} requests per connection: connections are not reused "
                   "(handshake and slow-start overhead per request)")
    if not out:
        out.append("no single bottleneck stands out in this data")
    return out


def heartbeat_section(hbs: list[Heartbeat], reqs: list[Req], min_kb: int, top: int = 3) -> str:
    """The "Heartbeat" report section. Per 10 s bucket: the slowest heartbeat and
    the slowest small (< min_kb) depot request that ENDED in the same bucket."""
    if not hbs:
        return "== Heartbeat ==\n  no heartbeat lines"
    rts = [h.rt for h in hbs]
    n499 = sum(1 for h in hbs if h.status == "499")
    other = sum(1 for h in hbs if h.status not in ("200", "499"))
    small: dict[int, list[float]] = defaultdict(list)
    for r in reqs:
        if r.bytes < min_kb * 1024:
            small[int(r.end // BUCKET_S)].append(r.rt)
    worst: dict[int, float] = {}
    for h in hbs:
        b = int(h.end // BUCKET_S)
        worst[b] = max(worst.get(b, 0.0), h.rt)
    lines = [
        "== Heartbeat ==",
        f"  lines: {len(hbs)}   request_time p50 {pct(rts, 50) * 1000:.1f} ms, p95 {pct(rts, 95) * 1000:.1f} ms, "
        f"max {max(rts) * 1000:.1f} ms",
        HB_BLIND,
        f"  status 499 (client gave up): {n499}" + (f"   other non-200: {other}" if other else ""),
        f"  largest {min(top, len(worst))} {BUCKET_S}s bucket(s) by heartbeat max "
        f"(small = depot requests under {min_kb} KiB ending in the same bucket):",
    ]
    for b in sorted(worst, key=lambda k: -worst[k])[:top]:
        sm = small.get(b)
        sm_txt = (f"small requests max {max(sm) * 1000:.1f} ms (n={len(sm)})" if sm else "no small requests in this bucket")
        stamp = time.strftime("%Y-%m-%dT%H:%M:%SZ", time.gmtime(b * BUCKET_S))
        lines.append(f"    {stamp}  heartbeat max {worst[b] * 1000:.1f} ms; {sm_txt}")
    return "\n".join(lines)


HB_BLIND = ("  (nginx-side time only: queueing before nginx accepts/reads the request is invisible here, "
            "so a blocked worker still shows ~0 ms; measure from outside, --probe-log, see ADR-0027)")


def parse_probe(lines: Iterable[str]) -> tuple[list[tuple[float, float, str]], int]:
    """Outside-probe lines -> ([(epoch, seconds, http code or '')], malformed count).
    Format: "<epoch or ISO-8601> <time_total s> [<http code>]"; '#' lines skipped."""
    out: list[tuple[float, float, str]] = []
    bad = 0
    for raw in lines:
        f = raw.split()
        if not f or f[0].startswith("#"):
            continue
        try:
            try:
                ts = float(f[0])
            except ValueError:
                d = datetime.fromisoformat(f[0].replace("Z", "+00:00"))
                ts = (d if d.tzinfo else d.replace(tzinfo=timezone.utc)).timestamp()
            out.append((ts, float(f[1]), f[2] if len(f) > 2 else ""))
        except (ValueError, IndexError):
            bad += 1
    return out, bad


def probe_section(probes: list[tuple[float, float, str]], bad: int = 0, top: int = 3) -> str:
    """The "Heartbeat (outside probe)" section: what a client really waited."""
    lines = ["== Heartbeat (outside probe) =="]
    if not probes:
        lines.append("  no usable probe lines" + (f" ({bad} malformed)" if bad else ""))
        return "\n".join(lines)
    secs = [p[1] for p in probes]
    failed = sum(1 for p in probes if p[2] and p[2] != "200")
    lines += [
        f"  probes: {len(probes)}   time_total p50 {pct(secs, 50) * 1000:.1f} ms, p95 {pct(secs, 95) * 1000:.1f} ms, "
        f"max {max(secs) * 1000:.1f} ms",
        f"  >= 1 s: {sum(1 for s in secs if s >= 1.0)}   >= 100 ms: {sum(1 for s in secs if s >= 0.1)}"
        + (f"   non-200 / failed (000): {failed}" if failed else "")
        + (f"   ({bad} malformed line(s) skipped)" if bad else ""),
        f"  slowest {min(top, len(probes))}:",
    ]
    for ts, s, code in sorted(probes, key=lambda p: -p[1])[:top]:
        stamp = time.strftime("%Y-%m-%dT%H:%M:%SZ", time.gmtime(ts))
        lines.append(f"    {stamp}  {s * 1000:.1f} ms" + (f" (http {code})" if code else ""))
    return "\n".join(lines)


NOTES = (
    "notes:",
    "  - Aggregate Mbit/s is a busy-time figure: buckets with no traffic at all are absent, so idle gaps are not averaged in.",
    "  - $connection numbers restart at 1 after an nginx restart: when files of different runs are mixed, "
    "connections of different runs can look like one (fewer connections, more requests per connection).",
)


def render(client: str, a: dict) -> str:
    rtt = "n/a" if a["rtt_ms"] is None else f"{a['rtt_ms']:.2f} ms"
    cwnd = "n/a" if a["cwnd"] is None else f"{a['cwnd']:.0f} segments"
    rcv = "n/a" if a["rcv_space"] is None else f"{a['rcv_space'] / 1024:.0f} KiB"
    lines = [
        f"== client {client} ==",
        f"  requests: {a['requests']}   total: {a['mb']:.1f} MB   covered: {a['minutes']:.1f} min",
        f"  aggregate per {BUCKET_S}s bucket: avg {a['bucket_avg']:.0f} Mbit/s, p95 {a['bucket_p95']:.0f}, max {a['bucket_max']:.0f}",
        f"  per request ({a['big_n']} requests >= {a['min_kb']} KiB, {a['timed_n']} with request_time > 0"
        f"{', ' + str(a['big_n'] - a['timed_n']) + ' at 0.000 s excluded' if a['big_n'] > a['timed_n'] else ''}): p10 {a['per_req_p10'] / 8:.1f} / p50 {a['per_req_p50'] / 8:.1f} / "
        f"p90 {a['per_req_p90'] / 8:.1f} MB/s",
        f"  connections: {a['connections']} total, max {a['conc_max']} in the same second (median {a['conc_median']:.0f}); "
        f"{a['reqs_per_conn']:.1f} requests per connection (max {a['reqs_per_conn_max']})",
        f"  tcp medians: rtt {rtt}, cwnd {cwnd}, rcv_space {rcv}",
        f"  cache: {a['hit_n']:.0f}% HIT by requests, {a['hit_bytes']:.0f}% by bytes, {a['miss_n']:.0f}% other",
        "  verdict:",
    ]
    lines += [f"    - {v}" for v in a["verdicts"]]
    return "\n".join(lines)


def main(argv: Optional[list[str]] = None) -> int:
    ap = argparse.ArgumentParser(description=__doc__.split("\n\n")[0])
    ap.add_argument("files", nargs="+", help="throughput.log files (or - for stdin)")
    ap.add_argument("--link-mbit", type=float, default=1000.0, help="client link speed, default 1000")
    ap.add_argument("--client", help="only this client address")
    ap.add_argument("--min-request-kb", type=int, default=256, help="size floor in KiB for per-request throughput")
    ap.add_argument("--probe-log", help="outside heartbeat probe timings: '<epoch|ISO time> <seconds> [<http code>]' per line")
    args = ap.parse_args(argv)

    lines: list[str] = []
    for name in args.files:
        if name == "-":
            lines += sys.stdin.read().splitlines()
        else:
            with open(name, encoding="utf-8", errors="replace") as fh:
                lines += fh.read().splitlines()
    reqs, hbs, starts, bad = parse_all(lines)
    probe_txt = None
    if args.probe_log:
        with open(args.probe_log, encoding="utf-8", errors="replace") as fh:
            probe_txt = probe_section(*parse_probe(fh.read().splitlines()))
    if args.client:
        reqs = [r for r in reqs if r.client == args.client]
        hbs = [h for h in hbs if h.client == args.client]
    if not reqs and not hbs:
        print("no usable requests found" + (f" ({bad} malformed line(s))" if bad else ""))
        return 1
    if not reqs:
        for s in dict.fromkeys(starts):
            print(s)
        print("no depot requests in this log (heartbeat lines only)")
        print(heartbeat_section(hbs, reqs, args.min_request_kb))
        if probe_txt:
            print(probe_txt)
        return 0
    for s in dict.fromkeys(starts):  # unique, in order
        print(s)
    by_client: dict[str, list[Req]] = defaultdict(list)
    for r in reqs:
        by_client[r.client].append(r)
    for client in sorted(by_client, key=lambda c: -sum(r.bytes for r in by_client[c])):
        print(render(client, analyse(by_client[client], args.link_mbit, args.min_request_kb)))
    print(heartbeat_section(hbs, reqs, args.min_request_kb))
    if probe_txt:
        print(probe_txt)
    print("\n".join(NOTES))
    if bad:
        print(f"({bad} malformed line(s) skipped)")
    return 0


if __name__ == "__main__":
    sys.exit(main())
