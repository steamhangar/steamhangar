package client

import (
	"context"
	"encoding/json"
	"errors"
	"io"
	"math/rand"
	"net"
	"net/http"
	"net/http/httptest"
	"strconv"
	"strings"
	"sync/atomic"
	"testing"
	"time"

	"github.com/Riviera822/steamhangar/agent/report"
)

// testBackoff keeps every retry test fast: base/max are tiny, so even a
// full retry budget sleeps for well under a second in the worst case (real
// sleeps, tight caps - see WP 2.2 brief).
func testBackoff() Option { return WithBackoff(1*time.Millisecond, 5*time.Millisecond) }

func testPayload() report.Payload {
	return report.Payload{ClientID: "test-pc", AppIDs: []int{440, 730}}
}

func TestReportInstalled_Success(t *testing.T) {
	var gotAPIKey, gotMethod, gotPath string
	var gotBody []byte
	srv := httptest.NewServer(http.HandlerFunc(func(w http.ResponseWriter, r *http.Request) {
		gotAPIKey = r.Header.Get("X-Api-Key")
		gotMethod = r.Method
		gotPath = r.URL.Path
		gotBody, _ = io.ReadAll(r.Body)
		w.Header().Set("Content-Type", "application/json")
		w.WriteHeader(http.StatusOK)
		_, _ = w.Write([]byte(`{"client_id":"test-pc","received":2,"added":[440,730],"removed":[],"first_report":true}`))
	}))
	defer srv.Close()

	c := New(srv.URL, "secret-key", testBackoff())
	result, err := c.ReportInstalled(context.Background(), testPayload())
	if err != nil {
		t.Fatalf("unexpected error: %v", err)
	}

	if gotAPIKey != "secret-key" {
		t.Errorf("X-Api-Key header = %q, want %q", gotAPIKey, "secret-key")
	}
	if gotMethod != http.MethodPost {
		t.Errorf("method = %q, want POST", gotMethod)
	}
	if gotPath != "/v1/agent/installed" {
		t.Errorf("path = %q, want /v1/agent/installed", gotPath)
	}
	var sentPayload report.Payload
	if err := json.Unmarshal(gotBody, &sentPayload); err != nil {
		t.Fatalf("request body was not valid JSON: %v (body=%s)", err, gotBody)
	}
	if sentPayload.ClientID != "test-pc" || len(sentPayload.AppIDs) != 2 {
		t.Errorf("request body = %+v, want client_id=test-pc appids=[440 730]", sentPayload)
	}

	if result.ClientID != "test-pc" || result.Received != 2 || !result.FirstReport {
		t.Errorf("result = %+v, unexpected", result)
	}
	if len(result.Added) != 2 || len(result.Removed) != 0 {
		t.Errorf("result.Added/Removed = %v/%v, unexpected", result.Added, result.Removed)
	}
}

func TestReportInstalled_401IsNotRetried(t *testing.T) {
	var requestCount int32
	srv := httptest.NewServer(http.HandlerFunc(func(w http.ResponseWriter, r *http.Request) {
		atomic.AddInt32(&requestCount, 1)
		w.WriteHeader(http.StatusUnauthorized)
		_, _ = w.Write([]byte(`{"detail":"Missing or invalid X-Api-Key header"}`))
	}))
	defer srv.Close()

	c := New(srv.URL, "wrong-key", testBackoff())
	_, err := c.ReportInstalled(context.Background(), testPayload())
	if err == nil {
		t.Fatal("expected an error for a 401 response")
	}
	var apiErr *APIError
	if !errors.As(err, &apiErr) {
		t.Fatalf("error = %v (%T), want *APIError", err, err)
	}
	if apiErr.StatusCode != http.StatusUnauthorized {
		t.Errorf("StatusCode = %d, want 401", apiErr.StatusCode)
	}
	if got := atomic.LoadInt32(&requestCount); got != 1 {
		t.Errorf("server received %d request(s), want exactly 1 (no retry on 401)", got)
	}
}

func TestReportInstalled_422IsNotRetried(t *testing.T) {
	var requestCount int32
	srv := httptest.NewServer(http.HandlerFunc(func(w http.ResponseWriter, r *http.Request) {
		atomic.AddInt32(&requestCount, 1)
		w.WriteHeader(http.StatusUnprocessableEntity)
		_, _ = w.Write([]byte(`{"detail":"bad client_id"}`))
	}))
	defer srv.Close()

	c := New(srv.URL, "any-key", testBackoff())
	_, err := c.ReportInstalled(context.Background(), testPayload())
	if err == nil {
		t.Fatal("expected an error for a 422 response")
	}
	if got := atomic.LoadInt32(&requestCount); got != 1 {
		t.Errorf("server received %d request(s), want exactly 1 (no retry on 422)", got)
	}
}

func TestReportInstalled_500IsRetriedThenSucceeds(t *testing.T) {
	var requestCount int32
	srv := httptest.NewServer(http.HandlerFunc(func(w http.ResponseWriter, r *http.Request) {
		n := atomic.AddInt32(&requestCount, 1)
		if n < 3 {
			w.WriteHeader(http.StatusInternalServerError)
			_, _ = w.Write([]byte(`{"detail":"temporary"}`))
			return
		}
		w.WriteHeader(http.StatusOK)
		_, _ = w.Write([]byte(`{"client_id":"test-pc","received":2,"added":[],"removed":[],"first_report":false}`))
	}))
	defer srv.Close()

	c := New(srv.URL, "key", testBackoff(), WithMaxRetries(5))
	result, err := c.ReportInstalled(context.Background(), testPayload())
	if err != nil {
		t.Fatalf("unexpected error after eventual success: %v", err)
	}
	if result.ClientID != "test-pc" {
		t.Errorf("result = %+v, unexpected", result)
	}
	if got := atomic.LoadInt32(&requestCount); got != 3 {
		t.Errorf("server received %d request(s), want exactly 3 (2 failures + 1 success)", got)
	}
}

func TestReportInstalled_RetryCapIsRespected(t *testing.T) {
	var requestCount int32
	srv := httptest.NewServer(http.HandlerFunc(func(w http.ResponseWriter, r *http.Request) {
		atomic.AddInt32(&requestCount, 1)
		w.WriteHeader(http.StatusInternalServerError)
		_, _ = w.Write([]byte(`{"detail":"permanent-ish"}`))
	}))
	defer srv.Close()

	c := New(srv.URL, "key", testBackoff(), WithMaxRetries(3))
	_, err := c.ReportInstalled(context.Background(), testPayload())
	if err == nil {
		t.Fatal("expected an error - server always returns 500")
	}
	want := int32(4) // 1 initial + 3 retries
	if got := atomic.LoadInt32(&requestCount); got != want {
		t.Errorf("server received %d request(s), want exactly %d (retry cap respected)", got, want)
	}
}

// --- S4/S5 (WP 2.2 review, orchestrator decision): 429 is retryable with
// the normal capped backoff (the one 4xx that heals), and any Retry-After
// header is deliberately ignored rather than honored.

func TestReportInstalled_429IsRetriedThenSucceeds(t *testing.T) {
	var requestCount int32
	srv := httptest.NewServer(http.HandlerFunc(func(w http.ResponseWriter, r *http.Request) {
		n := atomic.AddInt32(&requestCount, 1)
		if n < 3 {
			w.WriteHeader(http.StatusTooManyRequests)
			_, _ = w.Write([]byte(`{"detail":"slow down"}`))
			return
		}
		w.WriteHeader(http.StatusOK)
		_, _ = w.Write([]byte(`{"client_id":"test-pc","received":0,"added":[],"removed":[],"first_report":false}`))
	}))
	defer srv.Close()

	c := New(srv.URL, "key", testBackoff(), WithMaxRetries(5))
	result, err := c.ReportInstalled(context.Background(), testPayload())
	if err != nil {
		t.Fatalf("unexpected error after eventual success: %v", err)
	}
	if result.ClientID != "test-pc" {
		t.Errorf("result = %+v, unexpected", result)
	}
	if got := atomic.LoadInt32(&requestCount); got != 3 {
		t.Errorf("server received %d request(s), want exactly 3 (2x 429 + 1 success)", got)
	}
}

func TestReportInstalled_RetryAfterHeaderIsIgnored(t *testing.T) {
	var requestCount int32
	srv := httptest.NewServer(http.HandlerFunc(func(w http.ResponseWriter, r *http.Request) {
		n := atomic.AddInt32(&requestCount, 1)
		if n == 1 {
			// A huge Retry-After that this client must NOT honor - if it
			// did, this test would need to wait ~an hour instead of
			// completing almost instantly on the small testBackoff().
			w.Header().Set("Retry-After", "3600")
			w.WriteHeader(http.StatusTooManyRequests)
			_, _ = w.Write([]byte(`{"detail":"slow down"}`))
			return
		}
		w.WriteHeader(http.StatusOK)
		_, _ = w.Write([]byte(`{"client_id":"test-pc","received":0,"added":[],"removed":[],"first_report":false}`))
	}))
	defer srv.Close()

	start := time.Now()
	c := New(srv.URL, "key", testBackoff(), WithMaxRetries(3))
	_, err := c.ReportInstalled(context.Background(), testPayload())
	elapsed := time.Since(start)
	if err != nil {
		t.Fatalf("unexpected error: %v", err)
	}
	if elapsed > 1*time.Second {
		t.Errorf("elapsed = %v, want well under 1s - the 3600s Retry-After header must be ignored", elapsed)
	}
	if got := atomic.LoadInt32(&requestCount); got != 2 {
		t.Errorf("server received %d request(s), want exactly 2", got)
	}
}

// --- alignment (WP 2.2 review nitpick): a 2xx response whose BODY fails
// to be fully read gets the same non-retry treatment as a 2xx response
// whose body fails to PARSE as JSON (TestReportInstalled_
// MalformedResponseJSONIsNotRetried below) - both mean the server already
// accepted the report. A non-2xx read error, by contrast, retries exactly
// when the status code itself would have.

func TestReportInstalled_2xxBodyReadErrorIsNotRetried(t *testing.T) {
	var requestCount int32
	srv := httptest.NewServer(http.HandlerFunc(func(w http.ResponseWriter, r *http.Request) {
		atomic.AddInt32(&requestCount, 1)
		hj, ok := w.(http.Hijacker)
		if !ok {
			t.Fatal("ResponseWriter does not support hijacking")
		}
		conn, bufrw, err := hj.Hijack()
		if err != nil {
			t.Fatalf("hijack failed: %v", err)
		}
		defer conn.Close()
		// Advertise more bytes than are actually sent, then close - the
		// client's body read sees an unexpected EOF partway through.
		bufrw.WriteString("HTTP/1.1 200 OK\r\nContent-Length: 100\r\n\r\nshort")
		bufrw.Flush()
	}))
	defer srv.Close()

	c := New(srv.URL, "key", testBackoff())
	_, err := c.ReportInstalled(context.Background(), testPayload())
	if err == nil {
		t.Fatal("expected an error for a truncated 200 response body")
	}
	if !strings.Contains(err.Error(), "ACCEPTED") {
		t.Errorf("error = %v, want it to say the report was likely still accepted", err)
	}
	if got := atomic.LoadInt32(&requestCount); got != 1 {
		t.Errorf("server received %d request(s), want exactly 1 (2xx body-read error is not retried)", got)
	}
}

func TestReportInstalled_5xxBodyReadErrorIsRetriedThenSucceeds(t *testing.T) {
	var requestCount int32
	srv := httptest.NewServer(http.HandlerFunc(func(w http.ResponseWriter, r *http.Request) {
		n := atomic.AddInt32(&requestCount, 1)
		if n == 1 {
			hj, ok := w.(http.Hijacker)
			if !ok {
				t.Fatal("ResponseWriter does not support hijacking")
			}
			conn, bufrw, err := hj.Hijack()
			if err != nil {
				t.Fatalf("hijack failed: %v", err)
			}
			defer conn.Close()
			bufrw.WriteString("HTTP/1.1 500 Internal Server Error\r\nContent-Length: 100\r\n\r\nshort")
			bufrw.Flush()
			return
		}
		w.WriteHeader(http.StatusOK)
		_, _ = w.Write([]byte(`{"client_id":"test-pc","received":0,"added":[],"removed":[],"first_report":false}`))
	}))
	defer srv.Close()

	c := New(srv.URL, "key", testBackoff(), WithMaxRetries(2))
	result, err := c.ReportInstalled(context.Background(), testPayload())
	if err != nil {
		t.Fatalf("unexpected error after eventual success: %v", err)
	}
	if result.ClientID != "test-pc" {
		t.Errorf("result = %+v, unexpected", result)
	}
	if got := atomic.LoadInt32(&requestCount); got != 2 {
		t.Errorf("server received %d request(s), want exactly 2 (1 failed read + 1 success)", got)
	}
}

// --- S2 (WP 2.2 review): the backoff sleep itself must be interruptible
// by ctx cancellation mid-sleep, not just checked before it starts.

// maxInt63n is a randSource fake that always returns the largest value
// backoffDelay's rng.Int63n(n) call could legally return (n-1) - i.e. it
// always answers "the top of the range", making backoffDelay
// deterministically return its full upper bound (== maxDelay, once
// baseDelay is also set to maxDelay so the cap is reached on attempt 1).
//
// This does NOT wrap a rand.Source: a fixed/non-random Source under a
// real *rand.Rand would make Int63n's internal modulo-bias rejection-
// sampling loop spin FOREVER (confirmed empirically while writing this
// test - it hung the whole suite) trying to draw an in-range value from a
// Source that always returns the same out-of-range constant. Implementing
// randSource's Int63n directly sidesteps that loop entirely.
type maxInt63n struct{}

func (maxInt63n) Int63n(n int64) int64 {
	if n <= 0 {
		return 0
	}
	return n - 1
}

func TestReportInstalled_CancelDuringBackoffSleepReturnsQuickly(t *testing.T) {
	srv := httptest.NewServer(http.HandlerFunc(func(w http.ResponseWriter, r *http.Request) {
		w.WriteHeader(http.StatusInternalServerError) // always retryable
		_, _ = w.Write([]byte(`{"detail":"down"}`))
	}))
	defer srv.Close()

	c := New(srv.URL, "key",
		WithBackoff(2*time.Second, 2*time.Second), // deterministically-forced delay, see maxInt63n
		WithMaxRetries(5),
		withRand(maxInt63n{}),
	)

	ctx, cancel := context.WithCancel(context.Background())
	go func() {
		time.Sleep(20 * time.Millisecond)
		cancel()
	}()

	start := time.Now()
	_, err := c.ReportInstalled(ctx, testPayload())
	elapsed := time.Since(start)

	if err == nil {
		t.Fatal("expected an error (context canceled)")
	}
	if !errors.Is(err, context.Canceled) {
		t.Errorf("error = %v, want it to wrap context.Canceled", err)
	}
	if elapsed >= 100*time.Millisecond {
		t.Fatalf("elapsed = %v, want < 100ms - the 2s backoff sleep must be interrupted "+
			"by ctx cancellation, not slept out in full", elapsed)
	}
}

func TestReportInstalled_MalformedResponseJSONIsNotRetried(t *testing.T) {
	var requestCount int32
	srv := httptest.NewServer(http.HandlerFunc(func(w http.ResponseWriter, r *http.Request) {
		atomic.AddInt32(&requestCount, 1)
		w.WriteHeader(http.StatusOK)
		_, _ = w.Write([]byte(`not json at all`))
	}))
	defer srv.Close()

	c := New(srv.URL, "key", testBackoff())
	_, err := c.ReportInstalled(context.Background(), testPayload())
	if err == nil {
		t.Fatal("expected an error for a malformed 200 response body")
	}
	if !strings.Contains(err.Error(), "not valid JSON") {
		t.Errorf("error = %v, want it to mention invalid JSON", err)
	}
	if got := atomic.LoadInt32(&requestCount); got != 1 {
		t.Errorf("server received %d request(s), want exactly 1 (a 2xx is never retried)", got)
	}
}

func TestReportInstalled_TimeoutIsRetried(t *testing.T) {
	var requestCount int32
	srv := httptest.NewServer(http.HandlerFunc(func(w http.ResponseWriter, r *http.Request) {
		atomic.AddInt32(&requestCount, 1)
		time.Sleep(50 * time.Millisecond) // longer than the client's timeout below
		w.WriteHeader(http.StatusOK)
		_, _ = w.Write([]byte(`{"client_id":"test-pc","received":0,"added":[],"removed":[],"first_report":false}`))
	}))
	defer srv.Close()

	c := New(srv.URL, "key", testBackoff(), WithMaxRetries(2), WithTimeout(5*time.Millisecond))
	_, err := c.ReportInstalled(context.Background(), testPayload())
	if err == nil {
		t.Fatal("expected a timeout error")
	}
	want := int32(3) // 1 initial + 2 retries, every attempt times out
	if got := atomic.LoadInt32(&requestCount); got != want {
		t.Errorf("server received %d request(s), want exactly %d", got, want)
	}
}

// TestReportInstalled_ConnectionRefusedIsRetried proves a real connection
// error (nothing listening on the target port) is retried up to the cap.
// A custom DialContext counts dial attempts against the real OS network
// stack (not a fake transport) while still being fully deterministic: a
// closed listener's port refuses connections immediately (no SYN-timeout
// wait), so this needs no goroutine synchronization or port-timing luck.
func TestReportInstalled_ConnectionRefusedIsRetried(t *testing.T) {
	ln, err := net.Listen("tcp", "127.0.0.1:0")
	if err != nil {
		t.Fatalf("failed to reserve a port: %v", err)
	}
	addr := ln.Addr().String()
	ln.Close() // now nothing is listening on addr -> connections are refused

	var dialCount int32
	dialer := &net.Dialer{Timeout: 2 * time.Second}
	hc := &http.Client{
		Timeout: 2 * time.Second,
		Transport: &http.Transport{
			DialContext: func(ctx context.Context, network, address string) (net.Conn, error) {
				atomic.AddInt32(&dialCount, 1)
				return dialer.DialContext(ctx, network, address)
			},
		},
	}

	c := New("http://"+addr, "key", testBackoff(), WithMaxRetries(2), WithHTTPClient(hc))
	_, err = c.ReportInstalled(context.Background(), testPayload())
	if err == nil {
		t.Fatal("expected a connection error")
	}
	want := int32(3) // 1 initial + 2 retries
	if got := atomic.LoadInt32(&dialCount); got != want {
		t.Errorf("dial attempts = %d, want exactly %d (connection-refused retried up to the cap)", got, want)
	}
}

func TestBackoffDelay_BoundedByMaxDelay(t *testing.T) {
	rng := rand.New(rand.NewSource(1))
	base := 10 * time.Millisecond
	max := 100 * time.Millisecond
	for attempt := 1; attempt <= 40; attempt++ {
		d := backoffDelay(attempt, base, max, rng)
		if d < 0 || d > max {
			t.Fatalf("attempt %d: backoffDelay = %v, want in [0, %v]", attempt, d, max)
		}
	}
}

func TestBackoffDelay_GrowsWithAttemptBeforeHittingCap(t *testing.T) {
	// Not a statistical test - just checks the UPPER BOUND used for the
	// jitter grows with attempt number, by pinning the rng to always
	// return its max (Int63n(n) with n=1 degenerates, so instead check
	// across many samples that later attempts occasionally produce a
	// larger delay than early ones ever do while under the cap).
	rng := rand.New(rand.NewSource(42))
	base := 1 * time.Millisecond
	max := 1 * time.Second

	var maxAtAttempt1, maxAtAttempt10 time.Duration
	for i := 0; i < 200; i++ {
		if d := backoffDelay(1, base, max, rng); d > maxAtAttempt1 {
			maxAtAttempt1 = d
		}
		if d := backoffDelay(10, base, max, rng); d > maxAtAttempt10 {
			maxAtAttempt10 = d
		}
	}
	if maxAtAttempt10 <= maxAtAttempt1 {
		t.Errorf("expected attempt 10's observed max delay (%v) > attempt 1's (%v)", maxAtAttempt10, maxAtAttempt1)
	}
}

func TestBackoffDelay_HugeAttemptDoesNotOverflowOrPanic(t *testing.T) {
	rng := rand.New(rand.NewSource(7))
	base := 500 * time.Millisecond
	max := 30 * time.Second
	d := backoffDelay(1_000_000, base, max, rng)
	if d < 0 || d > max {
		t.Fatalf("backoffDelay with a huge attempt count = %v, want in [0, %v]", d, max)
	}
}

// --- B1 (WP AGENT-FIX-1): the client must NEVER follow a redirect. Before
// this fix New() built an http.Client with no CheckRedirect, so net/http's
// default policy applied: 301/302/303 turned the POST into a GET (body
// dropped) at the Location target, 307/308 replayed the POST there - and in
// every case the X-Api-Key header was copied onto the second hop (net/http
// only strips Authorization/Cookie/WWW-Authenticate on a host change, not
// custom headers). Measured with a two-server rig in review: hop 2 received
// the key, and the operator saw a bare "HTTP 405". The fix returns
// http.ErrUseLastResponse from CheckRedirect so the 3xx surfaces as-is, and
// the APIError names the Location so the operator can fix --server-url.
func TestReportInstalled_NeverFollowsRedirectOrForwardsKey(t *testing.T) {
	for _, code := range []int{301, 302, 303, 307, 308} {
		t.Run(http.StatusText(code), func(t *testing.T) {
			var hop2Requests int32
			hop2 := httptest.NewServer(http.HandlerFunc(func(w http.ResponseWriter, r *http.Request) {
				atomic.AddInt32(&hop2Requests, 1)
				w.WriteHeader(http.StatusOK)
				_, _ = w.Write([]byte(`{"client_id":"test-pc","received":0,"added":[],"removed":[],"first_report":false}`))
			}))
			defer hop2.Close()

			var hop1Requests int32
			var hop1Method, hop1Key string
			hop1 := httptest.NewServer(http.HandlerFunc(func(w http.ResponseWriter, r *http.Request) {
				atomic.AddInt32(&hop1Requests, 1)
				hop1Method = r.Method
				hop1Key = r.Header.Get("X-Api-Key")
				w.Header().Set("Location", hop2.URL+"/v1/agent/installed")
				w.WriteHeader(code)
			}))
			defer hop1.Close()

			c := New(hop1.URL, "secret-key", testBackoff())
			_, err := c.ReportInstalled(context.Background(), testPayload())
			if err == nil {
				t.Fatalf("expected an error for a %d response", code)
			}

			if got := atomic.LoadInt32(&hop2Requests); got != 0 {
				t.Fatalf("redirect target received %d request(s), want 0 - the client must not follow redirects", got)
			}
			if got := atomic.LoadInt32(&hop1Requests); got != 1 {
				t.Errorf("origin received %d request(s), want exactly 1 (3xx is not retried)", got)
			}
			if hop1Method != http.MethodPost || hop1Key != "secret-key" {
				t.Errorf("origin saw method=%q key=%q, want POST with the configured key", hop1Method, hop1Key)
			}

			var apiErr *APIError
			if !errors.As(err, &apiErr) {
				t.Fatalf("error = %v (%T), want *APIError", err, err)
			}
			if apiErr.StatusCode != code {
				t.Errorf("StatusCode = %d, want %d", apiErr.StatusCode, code)
			}
			wantLocation := hop2.URL + "/v1/agent/installed"
			if apiErr.Location != wantLocation {
				t.Errorf("Location = %q, want %q", apiErr.Location, wantLocation)
			}
			msg := err.Error()
			if !strings.Contains(msg, "HTTP "+strconv.Itoa(code)) || !strings.Contains(msg, wantLocation) || !strings.Contains(msg, "--server-url") {
				t.Errorf("error = %q, want it to name the status, the Location, and the --server-url hint", msg)
			}
		})
	}
}

// A 3xx WITHOUT a Location header (malformed reverse-proxy answer) must
// still be an APIError naming the status, just with no redirect hint.
func TestReportInstalled_RedirectWithoutLocationIsAPIError(t *testing.T) {
	srv := httptest.NewServer(http.HandlerFunc(func(w http.ResponseWriter, r *http.Request) {
		w.WriteHeader(http.StatusMultipleChoices)
		_, _ = w.Write([]byte(`pick one`))
	}))
	defer srv.Close()

	c := New(srv.URL, "key", testBackoff())
	_, err := c.ReportInstalled(context.Background(), testPayload())
	var apiErr *APIError
	if !errors.As(err, &apiErr) {
		t.Fatalf("error = %v (%T), want *APIError", err, err)
	}
	if apiErr.StatusCode != http.StatusMultipleChoices || apiErr.Location != "" {
		t.Errorf("APIError = %+v, want status 300 and an empty Location", apiErr)
	}
	if strings.Contains(err.Error(), "redirected to") {
		t.Errorf("error = %q, must not claim a redirect target it does not have", err.Error())
	}
}

// WP AGENT-FIX-1 S1: net/http masks only the password of a userinfo URL in
// *url.Error, so the username used to reach the log line. New strips the
// userinfo from the stored base URL; neither part may appear in the error.
func TestReportInstalled_NetworkErrorDoesNotLeakURLUserinfo(t *testing.T) {
	ln, err := net.Listen("tcp", "127.0.0.1:0")
	if err != nil {
		t.Fatalf("failed to reserve a port: %v", err)
	}
	addr := ln.Addr().String()
	ln.Close()

	c := New("http://USER-CANARY:PASS-CANARY@"+addr, "key", testBackoff(), WithMaxRetries(0))
	_, err = c.ReportInstalled(context.Background(), testPayload())
	if err == nil {
		t.Fatal("expected a connection error")
	}
	if msg := err.Error(); strings.Contains(msg, "USER-CANARY") || strings.Contains(msg, "PASS-CANARY") {
		t.Fatalf("server URL userinfo leaked into the error: %s", msg)
	}
}

// The stripped userinfo is still honored: it travels as Basic auth.
func TestReportInstalled_URLUserinfoIsSentAsBasicAuth(t *testing.T) {
	var gotUser, gotPass string
	var ok bool
	srv := httptest.NewServer(http.HandlerFunc(func(w http.ResponseWriter, r *http.Request) {
		gotUser, gotPass, ok = r.BasicAuth()
		w.Header().Set("Content-Type", "application/json")
		_, _ = w.Write([]byte(`{"client_id":"pc","received":0}`))
	}))
	defer srv.Close()

	base := strings.Replace(srv.URL, "http://", "http://alice:s3cret@", 1)
	c := New(base, "key", testBackoff(), WithMaxRetries(0))
	if _, err := c.ReportInstalled(context.Background(), testPayload()); err != nil {
		t.Fatalf("unexpected error: %v", err)
	}
	if !ok || gotUser != "alice" || gotPass != "s3cret" {
		t.Errorf("basic auth = (%q, %q, %v), want (alice, s3cret, true)", gotUser, gotPass, ok)
	}
}

// WP SEC-FIX-2 (N2): the response body is server-controlled and ends up in
// the error text (and so in the operator's terminal and logs). ESC and CR
// must arrive escaped, never raw: a raw ESC starts a terminal escape
// sequence, a raw CR lets the server overwrite the visible log line.
func TestReportInstalled_ErrorBodyControlCharsAreEscaped(t *testing.T) {
	for _, code := range []int{http.StatusUnprocessableEntity, http.StatusFound} {
		t.Run(strconv.Itoa(code), func(t *testing.T) {
			srv := httptest.NewServer(http.HandlerFunc(func(w http.ResponseWriter, r *http.Request) {
				if code == http.StatusFound {
					w.Header().Set("Location", "/elsewhere")
				}
				w.WriteHeader(code)
				_, _ = w.Write([]byte("bad\x1b[2J\rFAKE LOG LINE\nnext"))
			}))
			defer srv.Close()

			c := New(srv.URL, "key", testBackoff())
			_, err := c.ReportInstalled(context.Background(), testPayload())
			var apiErr *APIError
			if !errors.As(err, &apiErr) {
				t.Fatalf("error = %v (%T), want *APIError", err, err)
			}
			msg := err.Error()
			for _, raw := range []string{"\x1b", "\r", "\n"} {
				if strings.Contains(msg, raw) || strings.Contains(apiErr.Body, raw) {
					t.Errorf("error = %q contains a raw %q", msg, raw)
				}
			}
			if !strings.Contains(msg, `bad\x1b[2J\rFAKE LOG LINE\nnext`) {
				t.Errorf("error = %q, want the body with visible escapes", msg)
			}
		})
	}
}

func TestSanitizeForLog(t *testing.T) {
	cases := map[string]string{
		"plain ascii":            "plain ascii",
		"umlaut ä kept":          "umlaut ä kept",
		"esc\x1b[31m":            `esc\x1b[31m`,
		"cr\rlf\ntab\t":          `cr\rlf\ntab\t`,
		"del\x7f c1\u009b":       `del\x7f c1\u009b`,
		"cut rune \xc3":          "cut rune �",
		"https://h.example/\x1b": `https://h.example/\x1b`,
	}
	for in, want := range cases {
		if got := sanitizeForLog(in); got != want {
			t.Errorf("sanitizeForLog(%q) = %q, want %q", in, got, want)
		}
	}
}
