# cosmohttp3

HTTP/3 for [cosmohttp](..), as a package of its own so that the core client keeps no QUIC dependency.

QUIC is `web_quic` (CeeSafe.Web): ngtcp2 with OpenSSL 3.5 or later, through a C shim. Everything above it is
written here: HTTP/3's frames, QPACK, and the connection.

```ceesafe
import web_core;
import web_tls;
import web_quic;
import cosmohttp;
import cosmohttp3;

// once: QuicLibrary.initialise(); a TlsConfig.client() with trustSystem() and ALPN [Alpn.Http3]; QuicTls.of(config)
let conn = await H3Connection.connect(tls, "example.com", 443, H3Options.defaults())?;
let request = Request.create(Method.Get, "https://example.com/")?;   // cosmohttp's Request
let response = await conn.send(request, "".bytes())?;                 // or conn.open(...) and response.read(...)
response.status(); response.header("content-type"); response.body();
await conn.close();
```

`examples/get3` fetches a URL: `get3 https://cloudflare-quic.com/ 3`.

## How it works

- **No connection task.** As in cosmohttp's HTTP/2 session, the task that waits for a response does the connection's
  work: it hands queued requests to QUIC, sends and receives datagrams, runs QUIC's timers and gives what arrived to
  the streams; the other waiting tasks sleep until it wakes them, and one of them takes over when it is done. A
  connection with nothing in flight is not driven, so one left alone longer than the idle timeout (30 s) is gone.
- **QPACK without a dynamic table.** The client's table size is 0 (the default it announces), so a response's fields
  can refer only to the static table: decoding keeps no state, there are no encoder or decoder streams to run, and
  no stream waits for another. Requests are encoded with static references and literals; Huffman-coded strings are
  decoded, none are sent.
- **Flow control follows the reader.** A buffered response (`send`) gives the server room back as its bytes arrive,
  up to `maxBodyBytes`; a streamed one (`open`) only as `read` takes them.
- Trailers (`response.trailer(name)`), interim responses (skipped), unknown frames and stream types (skipped),
  GOAWAY (no new requests; those not yet sent fail as not acted on), a stream the server resets (the request fails,
  the connection goes on), and a response dropped early (its stream is cancelled).

## What is not here yet

One connection, made by hand: no pool, no `Alt-Svc` upgrade from cosmohttp's `Client`, no fallback to HTTP/2 when
UDP is blocked, no retry of a request the server refused. No 0-RTT or session resumption, no connection migration,
no server push (never allowed), no request bodies written piece by piece, no cookies, cache, redirects or
decompression (those live in cosmohttp's `Client`). A name's first address is used, not raced. The request body is
copied once into the stream's queue.

## Working on it

```sh
ceesafec check && ceesafec fmt --check
ceesafec test                   # the codecs, and the connection against a QUIC server in the same process
ceesafec test --sweep alloc     # every allocation failing in turn
ceesafec test --sweep cancel    # every await cancelled in turn
python3 tools/gen_qpack_table.py <QpackStaticTable.cs>   # regenerates src/qpack_table.cee
```

The connection's test needs UDP on the loopback and OpenSSL 3.5 or later, and the interpreter skips it (the QUIC
shim is C); the codec tests run there too.
