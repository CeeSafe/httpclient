# cosmohttp

An HTTP/1.1 and HTTP/2 client for [CeeSafe](https://ceesafe.github.io/CeeSafe/), written for speed: a request is one
write, the response is parsed in buffers that are reused, and a request on a warm connection allocates nothing.
It is a new design after the .NET library [CosmoHttpClient](../../CosmoHttpClient), not a translation of it.

**Status:** HTTP/1.1 and HTTP/2 over TCP and TLS, connection pooling, buffered and streamed responses, redirects.
Not yet: HTTP/3, WebSockets, cookies, a response cache, decompression, request bodies that are streamed (see
[What is not here yet](#what-is-not-here-yet)).

```ceesafe
import web_core;
import web_tls;
import cosmohttp;

async fn fetch(http: borrow Client) -> Result<void, HttpError> {
    let response = await http.get("https://example.com/")?;
    let status = response.status();                           // 200
    let body = response.body();                               // a view of the connection's buffer
    let kind = response.header("content-type");     // Slice<u8>?, also a view
    return Ok();
}   // the response is dropped here, and its connection goes back to the pool
```

A request with a method, fields and a body is prepared once and sent any number of times:

```ceesafe
var request = Request.create(Method.Post, "https://api.example.com/v1/things")?;
request.header("Content-Type", "application/json")?;
request.header("Authorization", "Bearer …")?;
let response = await http.send(request, "{\"name\":\"widget\"}".bytes())?;
```

`Client.open` returns as soon as the head has arrived; `response.read(buffer)` then gives the body a piece at a
time, a chunk's data going straight from the socket into `buffer`.

For JSON, [CeeSafeLibs](../../CeeSafeLibs)' `json` reads the body where it is:
`Json.decode<Thing>(response.body())`.

## How it differs from web_client

CeeSafe.Web's `web_client` is the general client: `Send + Share`, HTTP/2, WebSockets, Happy Eyeballs, a retry
budget, trailers. This one does less and does it with less work for each request.

| | web_client | cosmohttp |
|---|---|---|
| Threads | one client shared by all (`AsyncMutex`) | one client for each thread (`RefCell`, no lock) |
| Buffers | allocated for each request | owned by the connection, reused |
| Response fields and body | copied out | views of the connection's buffer |
| Request | written as it is sent | fields encoded once; head and a small body in one write, a large body beside it in one vectored write |
| Dispatch | `Box<dyn Transport>`, `dyn BodySource` | an enum over plain and TLS |
| HTTP/2 | a connection task run beside the requests (`Client.run`), a channel hop each way | no task: the request that waits does the connection's reading and writing |
| Protocols | HTTP/1.1, HTTP/2, WebSockets | HTTP/1.1, HTTP/2 |

Use `web_client` when one client must be shared between threads or for WebSockets; use this one where requests
are many and each thread can have its own client.

## HTTP/2

An `https://` origin is spoken to over HTTP/2 when the `TlsConfig` offers ALPN `h2` and the server picks it; an
`http://` origin when `ClientOptions.http2PriorKnowledge` is set (h2c, for services behind a proxy and for gRPC-style
internal traffic). Nothing else changes: the same `client.get`, `send` and `open`, the same `Response`
(`response.isHttp2()` says which it was).

```ceesafe
var config = TlsConfig.client()?;
config.trustSystem()?;
let protocols = [Alpn.Http2, Alpn.Http11];
config.setAlpn(protocols[..])?;
let http = Client.withTls(ClientOptions.defaults(), config);
```

How it is built (`src/h2.cee`, `src/hpack.cee`):

- **No connection task.** A request that waits for its response is the connection's *leader*: it writes what any
  request has queued and reads frames for all of them, and the others sleep until their stream has what they
  want. One request at a time costs no task switch; many at once share each write and each read.
- **Requests are encoded once.** A `Request`'s fields are HPACK literals that use no dynamic table, written when the
  field is added, so the same bytes are right on every connection and sending is a copy. Credentials are marked
  never-indexed.
- **Responses** go through a complete HPACK decoder (static and dynamic tables, size updates, Huffman four bits at a
  time through a generated table). A large DATA frame is read from the socket straight into its stream's body.
- **Flow control.** 2 MiB for a stream and 16 MiB for the connection, refilled at half; a streamed response's
  window is refilled as it is read, so a slow reader holds back its own stream only. Request bodies wait for the
  server's windows.
- **Limits and failures.** `http2Streams` requests on a connection (100, or the server's limit if lower), up to
  `http2Connections` connections; a further connection is opened when one is full or the server said GOAWAY. A
  stream the server refused (REFUSED_STREAM, or a GOAWAY that leaves it out) is sent again whatever its method;
  otherwise the HTTP/1.1 rule holds. Dropping a response before its end resets the stream.

Checked against RFC 7541's examples (Appendix C), 11 tests over scripted connections with frames cut at every
size, and by hand against example.com, google.com, nghttp2.org, cloudflare.com and github.com over TLS.

## Measurements

`python3 bench/run.py` runs both clients against the same keep-alive loopback server, each in its own process.
Apple M1, macOS, ceesafec 0.57.1, 2026-11-16; the best of 5 runs of 100,000 requests (20,000 for `64k`).
`cosmo` is `Client.send`, `stream` is `Client.open` read through a 16 KiB buffer, `web` is web_client's `get`,
`readToEnd` and `finish`. CPU is the client process's user and system time. The machine was shared with other
work, so compare ratios, and measure again before deciding anything on a small difference.

| Body | Concurrent | Client | Requests/s | CPU µs/request | of which user | Allocations/request |
|---|---:|---|---:|---:|---:|---:|
| 1 KiB | 1 | cosmo | 42,607 | 8.8 | 1.6 | 0 |
| | | web | 34,770 | 14.6 | 7.3 | 32 |
| 1 KiB | 16 | cosmo | 179,211 | 5.6 | 0.8 | 0 |
| | | web | 83,472 | 11.5 | 6.2 | 32 |
| 1 KiB | 64 | cosmo | 166,336 | 6.0 | 0.9 | 0 |
| | | web | 44,628 | 20.2 | 9.0 | 32 |
| 64 KiB | 1 | cosmo | 25,412 | 13.8 | 2.0 | 0 |
| | | web | 11,428 | 60.9 | 46.2 | 33 |
| 64 KiB | 16 | cosmo | 63,492 | 14.5 | 1.6 | 0 |
| | | web | 14,738 | 63.6 | 49.1 | 33 |
| 1 KiB chunked | 1 | cosmo | 42,354 | 9.0 | 1.9 | 0 |
| | | web | 30,184 | 16.3 | 7.9 | 35 |
| 1 KiB chunked | 16 | cosmo | 112,612 | 7.6 | 1.3 | 0 |
| | | web | 78,616 | 12.1 | 6.6 | 35 |

The whole table, with the streamed mode and the 64-connection rows, is `bench/results/2026-11-16-m1.json`. At 64
concurrent connections some of web_client's runs ended in a 10 s timeout (2 or 3 of 5); those runs are left out of
its best, and the failure is F3 in [CEESAFE_FINDINGS.md](CEESAFE_FINDINGS.md).

With one request at a time the round trip through the kernel dominates (about 22 µs of the 23), so the rate
differs by a fifth while the client's own user time differs by a factor of four or more.

### HTTP/2

`python3 bench/run.py --h2` runs the same comparison against CeeSafe.Web's own benchmark server (web_server, which
speaks HTTP/1.1 and h2c on one port): `cosmo` and `web` over HTTP/1.1, `cosmo2` and `web2` over HTTP/2 by prior
knowledge, every request of a run on one connection. Same machine and day, the best of 5 runs of 100,000 requests
(20,000 for 64 KiB). This server does more work for each request than the loop above, and it, not the client, sets
most of these rates; the client's own cost is the CPU column.

| Body | Concurrent | Client | Requests/s | CPU µs/request | of which user | Allocations/request |
|---|---:|---|---:|---:|---:|---:|
| 13 B | 1 | cosmo2 | 23,036 | 10.0 | 2.3 | 0 |
| | | web2 | 18,037 | 17.0 | 7.6 | 37 |
| 13 B | 16 | cosmo2 | 137,174 | 1.9 | 0.8 | 0 |
| | | web2 | 56,882 | 6.5 | 5.4 | 37 |
| 13 B | 64 | cosmo2 | 124,029 | 1.5 | 0.9 | 0 |
| | | web2 | 104,678 | 5.2 | 4.7 | 37 |
| 4 KiB | 16 | cosmo2 | 99,009 | 3.9 | 1.4 | 0 |
| | | web2 | 52,192 | 11.7 | 10.1 | 33 |
| 4 KiB | 64 | cosmo2 | 96,587 | 2.8 | 1.3 | 0 |
| | | web2 | 109,135 | 8.5 | 7.5 | 33 |
| 64 KiB | 1 | cosmo2 | 13,888 | 23.7 | 5.5 | 0 |
| | | web2 | 2,119 | 210.6 | 164.7 | 37 |
| 64 KiB | 16 | cosmo2 | 36,900 | 11.2 | 3.7 | 0 |
| | | web2 | 7,443 | 117.3 | 101.7 | 37 |

What the table does and does not show:

- Over HTTP/2 this client spends a third to a tenth of web_client's CPU for a request, and allocates nothing.
- Many requests at once cost less each than one at a time (1.5 to 1.9 µs against 10): they share writes and reads.
- At 64 concurrent 4 KiB requests web_client's rate was the higher one (109,135 against 96,587) while using three
  times the CPU. Why is not established; the two rates are within what this shared machine varies between runs.
- One request at a time is slower over HTTP/2 than over HTTP/1.1 against this server (23,036 against 33,222 for
  13 B); the client's CPU differs by 1 µs, so most of that is the server's.
- For a 64 KiB body this client's user time is 5.5 µs over HTTP/2 against 2.2 over HTTP/1.1: the server sends
  16 KiB frames, and most of each passes through the read buffer before it reaches the body.

The whole table (HTTP/1.1 rows too) is `bench/results/2026-11-16-m1-h2.json`.

The .NET CosmoHttpClient's own figure for a pooled 1 KiB GET on an M1 is 42.9 µs and 946 B allocated, against
Kestrel in the same process; this client's 23.5 µs is against a simpler server in another process, so the two are
not the same measurement.

## What it does

- **Requests.** `GET`, `HEAD`, `POST`, `PUT`, `DELETE`, `PATCH`, `OPTIONS`, `TRACE`. `Host` and `Content-Length` are
  the client's; a field name must be a token and a value may hold no CR, LF or NUL, so a request cannot be split.
  A URL with a space, a control character or user information is refused.
- **Responses** (RFC 9112). `content-length`, chunked (extensions and trailers skipped), and bodies that end with
  the connection; 1xx responses are skipped; HEAD, 204 and 304 have no body. Conflicting `content-length`s, a folded
  field line, whitespace before a colon and a bare CR are faults. `chunked` counts only as the last coding.
- **Reuse.** A connection goes back to the pool when the response was read to its end and the server keeps it:
  not after `Connection: close`, HTTP/1.0 without `keep-alive`, a body that ended at the close, a response with both
  `transfer-encoding` and `content-length`, or bytes after the response's end.
- **Stale connections.** If a pooled connection turns out closed before any byte of the response, the request goes
  again on a new one, but only when that is safe: the request never went out whole, or its method is idempotent. A
  POST that was sent is not sent twice.
- **Limits.** The head's size and field count and a buffered body's size are bounded (`ClientOptions`); a
  connection's buffer that grew past `maxKeptBuffer` is not kept.
- **Timeouts.** To connect, to handshake, and for the response (`responseMs`, off by default).
- **Per-origin limit.** `maxPerOrigin` bounds requests in flight; the next waits, and takes the connection the
  moment a response is dropped.
- **Redirects** (`maxRedirects`, off by default). 303 becomes a GET, and so do 301 and 302 after a POST; 307 and 308
  repeat the request. Another origin gets no `Authorization`, `Cookie` or `Proxy-Authorization`; https to http is
  not followed.
- **TLS** through CeeSafe.Web's `web_tls` (OpenSSL 3): `Client.withTls(options, config)`; the ALPN it offers
  chooses the version.

## What is not here yet

Of CosmoHttpClient's surface: HTTP/3 and the `Alt-Svc` upgrade to it; WebSockets; the handler pipeline's cookies, response cache, decompression, status-code retries with backoff and
telemetry; streamed request bodies and `Expect: 100-continue`; proxies; TLS session resumption; a DNS cache. A name
with several addresses is tried one address at a time, not raced. In HTTP/2: no PING keep-alive, no request
trailers, and when every connection is full a request fails with `LimitExceeded` instead of waiting (bound requests
in flight with `maxPerOrigin`).

## Working on it

```sh
ceesafec check && ceesafec fmt --check
ceesafec test                 # 46 tests over scripted connections; also --interp, --sweep alloc, --sweep cancel
python3 bench/run.py          # the HTTP/1.1 table; --h2 for the HTTP/2 one
python3 tools/gen_huffman.py  # src/huffman.bin, checked against RFC 7541's examples
(cd examples/get && ceesafec build && ./out/get https://example.com/ 3)
```

It depends on CeeSafe.Web by path (`../../CeeSafe.Web/packages`): `web_core`, `web_transport` and `web_tls`.
Building needs ceesafec 0.61.0 or later (its methods' results borrow from the receiver, CeeSafe ADR 0160) and
OpenSSL 3 (Homebrew's `openssl@3` on macOS). CeeSafe bugs and gaps met on the way are in
[CEESAFE_FINDINGS.md](CEESAFE_FINDINGS.md).
