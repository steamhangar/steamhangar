"""Unit tests for core/tools/throughput-report.py (WP THROUGHPUT-DEBUG-1).

Run: python3 -m unittest core/tests/test_throughput_report.py  (stdlib only)
"""
import contextlib
import importlib.util
import io
import pathlib
import unittest

HERE = pathlib.Path(__file__).resolve().parent
spec = importlib.util.spec_from_file_location("tpr", HERE.parent / "tools" / "throughput-report.py")
tpr = importlib.util.module_from_spec(spec)
spec.loader.exec_module(tpr)
FIXTURE = HERE / "fixtures" / "throughput-sample.log"


def load():
    reqs, starts, bad = tpr.parse(FIXTURE.read_text().splitlines())
    by = {}
    for r in reqs:
        by.setdefault(r.client, []).append(r)
    return by, starts, bad


class ReportTests(unittest.TestCase):
    def test_parse_counts_and_skips_junk(self):
        by, starts, bad = load()
        self.assertEqual(bad, 1)
        self.assertEqual(len(starts), 1)
        self.assertEqual(set(by), {"192.0.2.10", "192.0.2.20", "192.0.2.30", "192.0.2.40", "192.0.2.50"})

    def test_percentile(self):
        self.assertEqual(tpr.pct([1, 2, 3, 4, 5], 50), 3)
        self.assertEqual(tpr.pct([], 95), 0.0)
        self.assertAlmostEqual(tpr.pct([0, 10], 95), 9.5)

    def test_few_connections_slow_streams_is_not_called_client_limited(self):
        by, _, _ = load()
        a = tpr.analyse(by["192.0.2.10"], 1000, 256)
        self.assertEqual(a["conc_max"], 2)
        self.assertAlmostEqual(a["bucket_avg"], 200, delta=25)
        self.assertAlmostEqual(a["per_req_p50"], 100, delta=12)
        self.assertEqual(a["hit_n"], 100)
        text = " ".join(a["verdicts"])
        self.assertIn("few connections", text)
        self.assertIn("slow single streams", text)
        self.assertNotIn("not limited by the cache path", text)

    def test_four_slow_connections(self):
        by, _, _ = load()
        a = tpr.analyse(by["192.0.2.40"], 1000, 256)
        self.assertEqual(a["conc_max"], 4)
        text = " ".join(a["verdicts"])
        self.assertIn("slow single streams", text)
        self.assertNotIn("not limited by the cache path", text)

    def test_few_fast_streams_is_client_limited(self):
        by, _, _ = load()
        a = tpr.analyse(by["192.0.2.30"], 1000, 256)
        self.assertEqual(a["conc_max"], 1)
        self.assertGreaterEqual(a["per_req_p50"], 500)
        self.assertTrue(any("not limited by the cache path in this log" in v and "cannot be told apart" in v
                            for v in a["verdicts"]), a["verdicts"])
        self.assertFalse(any("client-limited" in v for v in a["verdicts"]), a["verdicts"])

    def test_small_sample_gives_no_verdict(self):
        by, _, _ = load()
        a = tpr.analyse(by["192.0.2.50"], 1000, 256)
        self.assertEqual(len(a["verdicts"]), 1)
        self.assertIn("sample too small", a["verdicts"][0])

    def test_server_limited(self):
        by, _, _ = load()
        a = tpr.analyse(by["192.0.2.20"], 1000, 256)
        self.assertGreaterEqual(a["conc_max"], 16)
        self.assertAlmostEqual(a["bucket_avg"], 300, delta=50)
        self.assertAlmostEqual(a["rtt_ms"], 0.25, places=2)
        self.assertEqual(a["cwnd"], 40)
        self.assertLess(a["hit_n"], 100)  # the planted MISS
        self.assertTrue(any("server or disk limited" in v for v in a["verdicts"]), a["verdicts"])

    def test_small_requests_excluded_from_per_request_stats(self):
        by, _, _ = load()
        a = tpr.analyse(by["192.0.2.20"], 1000, 256)
        # 16 connections x ~3 requests each plus one MISS; the 512-byte one is below the floor
        self.assertEqual(a["big_n"], a["requests"] - 1)

    def test_fast_link_has_no_bottleneck_verdict(self):
        by, _, _ = load()
        a = tpr.analyse(by["192.0.2.20"], 300, 256)
        self.assertTrue(any("no server-side bottleneck" in v for v in a["verdicts"]), a["verdicts"])

    def test_main_prints_both_clients(self):
        buf = io.StringIO()
        with contextlib.redirect_stdout(buf):
            rc = tpr.main([str(FIXTURE)])
        out = buf.getvalue()
        self.assertEqual(rc, 0)
        self.assertIn("== client 192.0.2.10 ==", out)
        self.assertIn("== client 192.0.2.20 ==", out)
        self.assertIn("verdict:", out)
        self.assertIn("1 malformed", out)
        self.assertIn("busy-time", out)
        self.assertIn("restart at 1", out)

    def test_main_client_filter_and_empty(self):
        buf = io.StringIO()
        with contextlib.redirect_stdout(buf):
            self.assertEqual(tpr.main(["--client", "192.0.2.10", str(FIXTURE)]), 0)
            self.assertEqual(tpr.main(["--client", "203.0.113.1", str(FIXTURE)]), 1)
        self.assertNotIn("192.0.2.20 ==", buf.getvalue())

    def test_dash_value_and_retry_list(self):
        self.assertIsNone(tpr._num("-"))
        self.assertAlmostEqual(tpr._num("0.1, 0.2"), 0.3)


def tp(end, client, status, nbytes, rt, conn="1"):
    return "\t".join(["tp1", end, client, "731", "GET", status, "-", str(nbytes), rt, "-", "HIT", conn, "1", "300", "300", "40", "50", "65535"])


def hb(end, rt, status="200"):
    return "\t".join(["hb1", end, "192.0.2.10", status, rt, "9", "1", "250"])


class HeartbeatTests(unittest.TestCase):
    LOG = [
        "# start x",
        tp("1760000003.0", "192.0.2.10", "206", 1048576, "0.5"),
        tp("1760000004.0", "192.0.2.10", "200", 512, "1.700", "2"),     # small, same bucket as the slow heartbeat
        hb("1760000004.5", "1.800"),
        hb("1760000005.0", "0.001"),
        hb("1760000015.0", "0.002"),
        hb("1760000025.0", "0.060", "499"),
        hb("1760000035.0", "0.003"),
    ]

    def test_parse_all_splits_tags_and_hb_is_not_malformed(self):
        reqs, hbs, starts, bad = tpr.parse_all(self.LOG + ["hb1\tshort", "tp1\tshort"])
        self.assertEqual((len(reqs), len(hbs), len(starts), bad), (2, 5, 1, 2))
        # the old three-tuple API still works and ignores hb lines without calling them junk
        reqs, starts, bad = tpr.parse(self.LOG)
        self.assertEqual((len(reqs), bad), (2, 0))

    def test_heartbeat_section_numbers(self):
        reqs, hbs, _, _ = tpr.parse_all(self.LOG)
        out = tpr.heartbeat_section(hbs, reqs, 256)
        self.assertIn("lines: 5", out)
        self.assertIn("max 1800.0 ms", out)
        self.assertIn("status 499 (client gave up): 1", out)
        first = out.splitlines()[4]   # largest bucket first
        self.assertIn("heartbeat max 1800.0 ms", first)
        self.assertIn("small requests max 1700.0 ms (n=1)", first)
        self.assertEqual(len([l for l in out.splitlines() if "Z  heartbeat max" in l]), 3)

    def test_no_heartbeat_lines(self):
        reqs, hbs, _, _ = tpr.parse_all([l for l in self.LOG if not l.startswith("hb1")])
        self.assertEqual(tpr.heartbeat_section(hbs, reqs, 256), "== Heartbeat ==\n  no heartbeat lines")

    def test_main_prints_heartbeat_section_with_and_without_hb_lines(self):
        import tempfile, os
        with tempfile.TemporaryDirectory() as d:
            for name, lines, want in (("with.log", self.LOG, "status 499"), ("without.log", [l for l in self.LOG if not l.startswith("hb1")], "no heartbeat lines")):
                path = os.path.join(d, name)
                pathlib.Path(path).write_text("\n".join(lines) + "\n")
                buf = io.StringIO()
                with contextlib.redirect_stdout(buf):
                    self.assertEqual(tpr.main([path]), 0)
                self.assertIn("== Heartbeat ==", buf.getvalue())
                self.assertIn(want, buf.getvalue())
                self.assertNotIn("malformed", buf.getvalue())
            # the shipped sample has no hb lines either
            buf = io.StringIO()
            with contextlib.redirect_stdout(buf):
                tpr.main([str(FIXTURE)])
            self.assertIn("no heartbeat lines", buf.getvalue())

    def test_heartbeat_only_log(self):
        import tempfile, os
        with tempfile.TemporaryDirectory() as d:
            path = os.path.join(d, "hb.log")
            pathlib.Path(path).write_text("\n".join(l for l in self.LOG if l.startswith("hb1")) + "\n")
            buf = io.StringIO()
            with contextlib.redirect_stdout(buf):
                self.assertEqual(tpr.main([path]), 0)
            self.assertIn("lines: 5", buf.getvalue())


if __name__ == "__main__":
    unittest.main()
