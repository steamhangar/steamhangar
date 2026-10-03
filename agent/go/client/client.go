// Package client is a small HTTP client for vault-agent's one write path:
// POST /v1/agent/installed (WP 2.2). It is deliberately the ONLY place in
// vault-agent that speaks HTTP, so retry/timeout/TLS policy lives in one
// spot.
//
// Retry policy (plan §7: "tolerate VPN/network outages"):
//   - Connection errors (refused, reset, DNS failure, timeout) and 5xx
//     responses ARE retried, with capped exponential backoff + jitter.
//   - 429 (Too Many Requests) IS ALSO retried, with the SAME backoff as a
//     5xx - it is the one 4xx that heals by waiting and resending (plan
//     §9 recommends operators put a rate limiter in front of vault-api
//     via their reverse proxy, so a real deployment can plausibly return
//     this). The response's Retry-After header, if present, is
//     deliberately IGNORED rather than parsed and honored precisely -
//     plan §9's simplicity principle: the capped exponential backoff
//     already waits between attempts, and this is a small periodic
//     status report, not a bulk/high-volume client a precise Retry-After
//     wait would meaningfully protect a server from.
//   - Every OTHER 4xx response is NEVER retried: a 401 (bad api key) or
//     422 (rejected body) will not heal by resending the same request,
//     and hammering the server on a genuine auth/validation failure would
//     be actively harmful.
//   - A successful (2xx) response whose body couldn't be fully read, or
//     whose body fails to parse as JSON, is also NOT retried: the server
//     has already accepted and stored the snapshot at that point (POST
//     /v1/agent/installed's effect is idempotent-ish per client_id, but
//     retrying purely to re-read/re-parse a response we already know
//     indicates success would only produce a duplicate, pointless
//     snapshot row) — the caller is told the report likely succeeded but
//     its result could not be read.
//
// Worst-case retry wall time with the defaults below (5 retries = 6 total
// attempts, each up to the 15s per-attempt timeout, plus the 5 backoff
// sleeps between them: 500ms+1s+2s+4s+8s = 15.5s at their upper bound):
// 6*15s + 15.5s ≈ 105.5s. cmd/vault-agent budgets 2 minutes per report
// specifically to comfortably clear this.
//
// Servers that predate the presence fields (WP AGENT-FEAT-1):
// every vault-api up to and including v0.1.0-rc8 validates the report body
// with extra="forbid", so the optional agent_version /
// report_interval_seconds fields make it reject the WHOLE report with a
// 422 (measured against the rc8 tree: {"detail":[{"type":
// "extra_forbidden","loc":["body","agent_version"],...}]}). A fleet
// upgrades agents and server in no fixed order, so ReportInstalled sends
// the new fields first and, when the 422 consists of NOTHING BUT
// extra_forbidden errors for those optional fields, resends the same
// report once without them (Result.PresenceDropped tells the caller). Any
// other 422 - including one that ALSO names a real problem such as a bad
// client_id - is returned unchanged, so the fallback can never hide a
// genuine rejection. The cost on an old server is one extra request per
// report, nothing is remembered between reports, and the first report
// after the server upgrade carries the fields again. Rejected
// alternatives: asking the server first (an extra round trip on every
// report, and rc8 has nothing to ask), and HTTP headers (old servers
// ignore them, but the report contract would live in two places).
//
// TLS uses Go's default system root CA pool (no custom TLSClientConfig is
// set). Proxying respects the standard http_proxy/https_proxy/no_proxy
// environment variables via http.ProxyFromEnvironment, same as any other
// well-behaved Go HTTP client — set them in the environment vault-agent
// runs under if the network requires a proxy to reach the server.
package client

import (
	"bytes"
	"context"
	"encoding/json"
	"errors"
	"fmt"
	"io"
	"math/rand"
	"net/http"
	"net/url"
	"strconv"
	"strings"
	"time"
	"unicode"

	"github.com/Riviera822/steamhangar/agent/report"
)

// Defaults for New. All are overridable via Option.
const (
	DefaultTimeout    = 15 * time.Second
	DefaultMaxRetries = 5
	DefaultBaseDelay  = 500 * time.Millisecond
	DefaultMaxDelay   = 30 * time.Second

	// maxResponseBytes bounds how much of a response body we ever read into
	// memory. vault-api's response is a few small JSON fields; anything
	// wildly larger than this is not a response this client needs to trust.
	maxResponseBytes = 1 << 20 // 1 MiB
)

// Result is the parsed outcome of a successful report (mirrors
// vault_api.routers.agent.InstalledReportResponse).
type Result struct {
	ClientID    string
	Received    int
	Added       []int
	Removed     []int
	FirstReport bool
	// PresenceDropped is true when the server rejected the optional
	// presence fields as unknown and the report was resent without them
	// (see the package doc: the server predates WP AGENT-FEAT-1).
	PresenceDropped bool
}

// APIError is returned for a non-2xx HTTP response. StatusCode is always
// set; Body holds a truncated excerpt of the response body (for
// diagnostics — vault-api's 401/422 bodies are small JSON error objects).
// This type is distinct from a transport-level error so callers
// (cmd/vault-agent) can use errors.As to tell "the server rejected this"
// apart from "the network failed", and pick the retry/exit-code behavior
// each deserves without string-matching.
type APIError struct {
	StatusCode int
	Body       string
	// Location is the resolved Location header of a 3xx response, empty
	// otherwise (and empty on a 3xx that carried no usable Location). The
	// client never follows it (see New's CheckRedirect); it is surfaced
	// here so the operator can see WHERE the server wanted to send the
	// report and fix --server-url to point there directly.
	Location string

	// presenceUnknown is set on a 422 whose body names nothing but the
	// optional presence fields as unknown (see unknownPresenceFieldsOnly):
	// the one rejection ReportInstalled answers by resending without them.
	presenceUnknown bool
}

func (e *APIError) Error() string {
	if e.Location != "" {
		return fmt.Sprintf(
			"server returned HTTP %d redirected to %s; vault-agent never follows redirects "+
				"(the API key would be forwarded to another host) - set --server-url to that address: %s",
			e.StatusCode, e.Location, e.Body,
		)
	}
	return fmt.Sprintf("server returned HTTP %d: %s", e.StatusCode, e.Body)
}

// responseBody mirrors InstalledReportResponse's JSON shape exactly.
type responseBody struct {
	ClientID    string `json:"client_id"`
	Received    int    `json:"received"`
	Added       []int  `json:"added"`
	Removed     []int  `json:"removed"`
	FirstReport bool   `json:"first_report"`
}

// randSource is the minimal randomness surface backoffDelay needs.
// *rand.Rand satisfies it (it has an Int63n(int64) int64 method). The
// interface (rather than the concrete type) exists so a test can
// substitute a fake that returns a fixed value directly - a fixed/
// non-random rand.Source underneath a real *rand.Rand would make
// Int63n's internal modulo-bias rejection-sampling loop spin forever
// (confirmed empirically: it does), since that loop keeps drawing from
// the Source until it gets a value in range, which a constant Source
// output can never satisfy if the constant happens to be rejected.
// Satisfying Int63n directly sidesteps that loop entirely.
type randSource interface {
	Int63n(n int64) int64
}

// Client posts installed-app reports to vault-api.
type Client struct {
	baseURL string
	apiKey  string
	// userinfo holds credentials stripped from a server URL like
	// http://user:pass@host (WP AGENT-FIX-1 S1). They are kept out of
	// baseURL so a *url.Error (which masks only the password) can never
	// put the username into a log line; attempt() sends them as Basic
	// auth instead. nil when the URL carried no userinfo.
	userinfo   *url.Userinfo
	httpClient *http.Client
	maxRetries int
	baseDelay  time.Duration
	maxDelay   time.Duration
	rng        randSource
}

// Option configures a Client returned by New.
type Option func(*Client)

// WithHTTPClient overrides the default *http.Client entirely (tests use
// this to inject a custom Transport — see client_test.go).
func WithHTTPClient(hc *http.Client) Option {
	return func(c *Client) { c.httpClient = hc }
}

// WithTimeout sets the per-attempt request timeout (default
// DefaultTimeout). Each retry attempt gets a fresh timeout budget — this is
// a per-HTTP-round-trip timeout, not a total-across-all-retries budget
// (callers wanting an overall deadline should pass a context with a
// deadline/cancel to ReportInstalled instead).
func WithTimeout(d time.Duration) Option {
	return func(c *Client) { c.httpClient.Timeout = d }
}

// WithMaxRetries sets how many RETRIES are attempted after the initial
// try (default DefaultMaxRetries) — total attempts = 1 + maxRetries.
func WithMaxRetries(n int) Option {
	return func(c *Client) { c.maxRetries = n }
}

// WithBackoff sets the base and max delay for the capped-exponential +
// jitter backoff (defaults DefaultBaseDelay / DefaultMaxDelay). Tests use
// small values here to keep the suite fast without needing a fake clock.
func WithBackoff(base, max time.Duration) Option {
	return func(c *Client) {
		c.baseDelay = base
		c.maxDelay = max
	}
}

// withRand overrides the source of randomness backoffDelay draws from.
// Unexported: only client_test.go (same package) uses this, to force a
// deterministic (large) backoff delay so a "cancel mid-sleep" test can
// prove the sleep is actually interrupted rather than merely happening to
// finish quickly by chance.
func withRand(r randSource) Option {
	return func(c *Client) { c.rng = r }
}

// New builds a Client for the given vault-api base URL (e.g.
// "http://127.0.0.1:8080", no trailing slash required) and API key.
func New(baseURL, apiKey string, opts ...Option) *Client {
	var userinfo *url.Userinfo
	if u, err := url.Parse(baseURL); err == nil && u.User != nil {
		userinfo = u.User
		u.User = nil
		baseURL = u.String()
	}
	c := &Client{
		baseURL:  baseURL,
		userinfo: userinfo,
		apiKey:   apiKey,
		httpClient: &http.Client{
			Timeout: DefaultTimeout,
			// Never follow a redirect (WP AGENT-FIX-1 B1): net/http's
			// default policy would carry X-Api-Key to whatever host the
			// Location names and rewrite the POST to a GET on 301/302/303.
			// Returning ErrUseLastResponse hands the 3xx itself back to
			// attempt(), which surfaces it (with the Location) as an
			// APIError instead. Pinned by
			// TestReportInstalled_NeverFollowsRedirectOrForwardsKey.
			CheckRedirect: func(*http.Request, []*http.Request) error {
				return http.ErrUseLastResponse
			},
			Transport: &http.Transport{
				// Preserved explicitly: building a custom *http.Transport
				// (rather than using http.DefaultTransport directly) means
				// this must be set by hand, or http_proxy/https_proxy/
				// no_proxy env vars would silently stop being honored.
				Proxy: http.ProxyFromEnvironment,
				// TLSClientConfig deliberately left nil: Go's default dials
				// TLS with the OS/system root CA pool. No custom CA
				// handling — vault-api's own TLS (if any, e.g. behind a
				// reverse proxy per plan §10) is expected to use a
				// certificate that chains to a public/system-trusted root.
			},
		},
		maxRetries: DefaultMaxRetries,
		baseDelay:  DefaultBaseDelay,
		maxDelay:   DefaultMaxDelay,
		rng:        rand.New(rand.NewSource(time.Now().UnixNano())),
	}
	for _, opt := range opts {
		opt(c)
	}
	return c
}

// ReportInstalled posts payload to POST /v1/agent/installed and returns the
// parsed result, retrying transient failures per the package doc's policy.
//
// ctx governs the ENTIRE call including all retries and backoff sleeps —
// canceling it (e.g. on SIGTERM in --loop mode) aborts promptly rather than
// finishing out the retry budget.
func (c *Client) ReportInstalled(ctx context.Context, payload report.Payload) (Result, error) {
	result, err := c.send(ctx, payload)
	var apiErr *APIError
	if err != nil && payload.HasPresence() && errors.As(err, &apiErr) && apiErr.presenceUnknown {
		// The server predates the presence fields (package doc): resend
		// the same report without them, once.
		result, err = c.send(ctx, payload.WithoutPresence())
		if err == nil {
			result.PresenceDropped = true
		}
	}
	return result, err
}

// send marshals payload and posts it with the retry policy from the
// package doc.
func (c *Client) send(ctx context.Context, payload report.Payload) (Result, error) {
	body, err := json.Marshal(payload)
	if err != nil {
		// Payload is our own struct with only string/int fields - this
		// path is not reachable in practice, but a client-side bug here
		// must not panic the caller.
		return Result{}, fmt.Errorf("client: encoding request body: %w", err)
	}

	url := c.baseURL + "/v1/agent/installed"
	var lastErr error

	for attempt := 0; attempt <= c.maxRetries; attempt++ {
		if attempt > 0 {
			delay := backoffDelay(attempt, c.baseDelay, c.maxDelay, c.rng)
			// The sleep itself must be cancellable, not just checked
			// before starting it (WP 2.2 review finding S2): a plain
			// c.sleep(delay) here would block for the FULL delay even if
			// ctx were canceled a moment later, so a SIGTERM during --loop
			// mode's backoff wait would sit through up to c.maxDelay
			// before this function ever noticed. Racing time.After(delay)
			// against ctx.Done() interrupts the wait immediately instead.
			timer := time.NewTimer(delay)
			select {
			case <-ctx.Done():
				timer.Stop()
				return Result{}, fmt.Errorf("client: %w", ctx.Err())
			case <-timer.C:
			}
		}

		result, retryable, err := c.attempt(ctx, url, body)
		if err == nil {
			return result, nil
		}
		if !retryable {
			return Result{}, err
		}
		lastErr = err

		if ctx.Err() != nil {
			return Result{}, fmt.Errorf("client: %w", ctx.Err())
		}
	}

	return Result{}, fmt.Errorf(
		"client: giving up after %d attempt(s): %w", c.maxRetries+1, lastErr,
	)
}

// attempt performs exactly one HTTP round trip. The bool return reports
// whether err (if non-nil) is retryable.
func (c *Client) attempt(ctx context.Context, url string, body []byte) (Result, bool, error) {
	req, err := http.NewRequestWithContext(ctx, http.MethodPost, url, bytes.NewReader(body))
	if err != nil {
		return Result{}, false, fmt.Errorf("client: building request: %w", err)
	}
	req.Header.Set("Content-Type", "application/json")
	req.Header.Set("X-Api-Key", c.apiKey)
	if c.userinfo != nil {
		pass, _ := c.userinfo.Password()
		req.SetBasicAuth(c.userinfo.Username(), pass)
	}

	resp, err := c.httpClient.Do(req)
	if err != nil {
		// Connection refused/reset, DNS failure, TLS handshake failure,
		// per-attempt timeout (ctx or http.Client.Timeout) all surface
		// here as a *url.Error wrapping the underlying cause - all
		// retryable per the package doc.
		return Result{}, true, fmt.Errorf("client: request failed: %w", err)
	}
	defer resp.Body.Close()

	is2xx := resp.StatusCode >= 200 && resp.StatusCode < 300

	respBody, readErr := io.ReadAll(io.LimitReader(resp.Body, maxResponseBytes))
	if readErr != nil {
		if is2xx {
			// The server already accepted the report (2xx) before the
			// body read failed - retrying would only risk a duplicate
			// snapshot to re-read a response we already know indicates
			// success. Same call as the malformed-2xx-JSON case below
			// (WP 2.2 review: keep these two aligned).
			return Result{}, false, fmt.Errorf(
				"client: server returned HTTP %d but reading the response body failed "+
					"(the report was likely still ACCEPTED - only the response could not be read): %w",
				resp.StatusCode, readErr,
			)
		}
		// Non-2xx with an unreadable body: retry exactly if the status
		// itself would have been retryable.
		return Result{}, retryableStatus(resp.StatusCode), fmt.Errorf(
			"client: reading response body (status %d): %w", resp.StatusCode, readErr,
		)
	}

	switch {
	case retryableStatus(resp.StatusCode):
		// 5xx, or 429 (the one 4xx that heals - see the package doc;
		// Retry-After is deliberately not consulted, the normal capped
		// backoff applies here too).
		return Result{}, true, &APIError{StatusCode: resp.StatusCode, Body: excerpt(respBody)}
	case resp.StatusCode >= 400:
		// Every other 4xx: never retryable (401/422 will not heal by
		// resending the SAME body; ReportInstalled may resend a different
		// one, see presenceUnknown).
		return Result{}, false, &APIError{
			StatusCode:      resp.StatusCode,
			Body:            excerpt(respBody),
			presenceUnknown: resp.StatusCode == http.StatusUnprocessableEntity && unknownPresenceFieldsOnly(respBody),
		}
	case resp.StatusCode >= 300:
		// vault-api never issues a redirect for this endpoint, so a 3xx
		// means a reverse proxy in front of it (http->https upgrade, a
		// moved hostname, ...). It is NOT retryable, and it is NOT
		// followed: the http.Client built in New() returns
		// http.ErrUseLastResponse from CheckRedirect, so this branch sees
		// the 3xx itself. Without that, net/http's DEFAULT policy would
		// have followed it - rewriting the POST into a body-less GET on
		// 301/302/303, replaying the POST on 307/308, and in EVERY case
		// copying X-Api-Key onto the new host (net/http strips only
		// Authorization/Cookie/WWW-Authenticate on a host change, never
		// custom headers). Measured in the pre-freeze review with a two-
		// server rig (WP AGENT-FIX-1 B1). The Location is surfaced in the
		// error so the operator can point --server-url at it directly.
		return Result{}, false, &APIError{
			StatusCode: resp.StatusCode,
			Body:       excerpt(respBody),
			Location:   redirectTarget(resp),
		}
	}

	var parsed responseBody
	if err := json.Unmarshal(respBody, &parsed); err != nil {
		// 2xx but unparseable: the server has already accepted the report
		// (see package doc) - not retryable, same reasoning as the read-
		// error branch above.
		return Result{}, false, fmt.Errorf(
			"client: server returned HTTP %d but the response body is not valid JSON "+
				"(the report was likely still ACCEPTED - only the response could not be read): %w, body=%s",
			resp.StatusCode, err, excerpt(respBody),
		)
	}

	return Result{
		ClientID:    parsed.ClientID,
		Received:    parsed.Received,
		Added:       parsed.Added,
		Removed:     parsed.Removed,
		FirstReport: parsed.FirstReport,
	}, false, nil
}

// presenceFieldNames are report.Payload's optional JSON keys (WP
// AGENT-FEAT-1), the only fields a 422 may name for the legacy resend.
var presenceFieldNames = map[string]bool{
	"agent_version":           true,
	"report_interval_seconds": true,
}

// unknownPresenceFieldsOnly reports whether a 422 body is FastAPI's
// validation error listing nothing but "extra_forbidden" at
// ["body", <presence field>]: what a pre-AGENT-FEAT-1 vault-api answers to
// a report carrying the presence fields. Anything else (another error type,
// another location, an empty or non-list detail, a non-JSON body) is false.
func unknownPresenceFieldsOnly(body []byte) bool {
	var parsed struct {
		Detail []struct {
			Type string `json:"type"`
			Loc  []any  `json:"loc"`
		} `json:"detail"`
	}
	if err := json.Unmarshal(body, &parsed); err != nil || len(parsed.Detail) == 0 {
		return false
	}
	for _, d := range parsed.Detail {
		if d.Type != "extra_forbidden" || len(d.Loc) != 2 || d.Loc[0] != "body" {
			return false
		}
		name, ok := d.Loc[1].(string)
		if !ok || !presenceFieldNames[name] {
			return false
		}
	}
	return true
}

// redirectTarget returns resp's Location header resolved against the
// request URL (so a relative Location is still meaningful to the
// operator), or "" when the response carries none / an unparseable one.
func redirectTarget(resp *http.Response) string {
	loc, err := resp.Location()
	if err != nil {
		return ""
	}
	return sanitizeForLog(loc.String())
}

// retryableStatus reports whether an HTTP status code should be retried:
// any 5xx, or 429 (the one 4xx that heals - see the package doc). Every
// other 4xx (401, 422, ...) is not retryable.
func retryableStatus(code int) bool {
	return code >= 500 || code == http.StatusTooManyRequests
}

// excerpt truncates a response body for inclusion in an error message -
// vault-api's error bodies are small JSON objects, but this guards against
// an unexpected huge body (e.g. from a misconfigured reverse proxy in
// front of vault-api) bloating a log line.
//
// The excerpt is server-controlled text that ends up in error messages and
// therefore in the operator's terminal and logs: control characters (ESC,
// CR, LF, ...) are escaped by sanitizeForLog so a hostile or broken server
// cannot inject terminal escape sequences or fake log lines.
func excerpt(body []byte) string {
	const maxLen = 500
	if len(body) <= maxLen {
		return sanitizeForLog(string(body))
	}
	return sanitizeForLog(string(body[:maxLen])) + "...[truncated]"
}

// sanitizeForLog makes server-controlled text safe for an error message or
// a log line: every control rune (C0, DEL, C1 - unicode.IsControl) is
// replaced by a visible Go-style escape (\x1b, \r, \u0085, ...), and
// invalid UTF-8 (e.g. a body cut mid-rune by excerpt's byte limit) becomes
// U+FFFD. Printable text, including non-ASCII, is kept as is.
func sanitizeForLog(s string) string {
	s = strings.ToValidUTF8(s, "\uFFFD")
	var b strings.Builder
	b.Grow(len(s))
	for _, r := range s {
		if !unicode.IsControl(r) {
			b.WriteRune(r)
			continue
		}
		q := strconv.QuoteRune(r) // e.g. '\x1b', '\r', '\u0085'
		b.WriteString(q[1 : len(q)-1])
	}
	return b.String()
}

// backoffDelay computes a "full jitter" capped-exponential backoff delay:
// uniformly random in [0, min(max, base*2^(attempt-1))]. attempt is
// 1-indexed (the delay BEFORE retry attempt N). Bounded shift avoids
// overflowing time.Duration for a pathologically large attempt count.
func backoffDelay(attempt int, base, max time.Duration, rng randSource) time.Duration {
	if attempt < 1 {
		attempt = 1
	}
	shift := attempt - 1
	const maxShift = 32 // base << 32 already dwarfs any sane max delay
	if shift > maxShift {
		shift = maxShift
	}
	upper := base << uint(shift)
	if upper <= 0 || upper > max {
		upper = max
	}
	if upper <= 0 {
		return 0
	}
	return time.Duration(rng.Int63n(int64(upper) + 1))
}
