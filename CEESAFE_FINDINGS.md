# CeeSafe findings

Bugs and gaps in CeeSafe (and CeeSafe.Web) met while writing this client, for the CeeSafe repositories. Each has
what was tried, what the compiler said, and what this package does instead. Found with ceesafec 0.57.1 on macOS
arm64 unless an entry says otherwise.

| | What | Where | State |
|---|---|---|---|
| F1 | a method's result cannot borrow from `this` beside another scoped parameter | CeeSafe | fixed in ceesafec 0.61.0 (ADR 0160 §2) |
| F2 | diagnostics printed twice | — | withdrawn: not a bug |
| F3 | web_client times out when 64 connections are dialed at once | CeeSafe (runtime), see F7 | fixed in ceesafec 0.61.0 |
| F3b | a request waiting for a slot does not take a connection given back | CeeSafe.Web | fixed on branch `fix/web-client-findings` |
| F4 | web_client copies bodies a byte at a time | CeeSafe.Web | fixed on `fix/web-client-findings` |
| F5 | web_client allocates 32 to 35 times for each request | CeeSafe.Web | 10 now, on `fix/web-client-findings`; the rest needs another API |
| F6 | a loop's local cannot lend to a `drop` type made through `?` | CeeSafe | fixed in ceesafec 0.61.0 (ADR 0160 §3) |
| F7 | a wait blocks after a full kqueue change queue's early send readied tasks | CeeSafe (runtime) | fixed in ceesafec 0.61.0 |
| F8 | no DEFLATE decoder and no CRC-32 in lib/std | CeeSafe (library gap) | open; this package has its own |
| F9 | a `bool` field read inside a returned struct literal is taken as a loan (N0245) | CeeSafe | open |
| F10 | no practical way to keep a hook (callback) in a long-lived struct used from async code | CeeSafe (ergonomics, not a bug) | open; needs a design answer |
| F11 | gcc 13 warns `free` called on `ceesafe_vec_empty` in generated C (`-Wfree-nonheap-object`) | CeeSafe (build noise, no runtime fault) | open |
| F12 | `ceesafec fmt` never wraps an interface method's signature, and wraps its `@effects(...)` instead | CeeSafe (formatter) | open |
| F13 | web_client over HTTP/2 with 64 KiB bodies: 117 to 1,079 requests a second on Linux | CeeSafe.Web (performance) | open, not looked into |

The CeeSafe fixes were released in ceesafec 0.61.0 (2026-11-16, tag `v0.61.0`). The CeeSafe.Web ones are in the
worktree `~/dev/CeeSafe.Web-wt-client` (branch `fix/web-client-findings`, from `main` at `d8b0d38`), uncommitted.

## F1. A method cannot say its result borrows from `this` when it has another scoped parameter

**Fixed** in ceesafec 0.61.0 (ADR 0160 §2): in a method with several scoped parameters, a result with no
source written borrows from the receiver, so `fn a` below checks as written. This package uses it since: `client.get(url)`,
`client.send(request, body)` and `response.header(name)` are methods, and its manifest asks for ceesafec 0.61.0.

**Kind:** language gap. **Cost here, until 0.61.0:** the client's entry points were static functions, and a
response's field lookup was two calls or a static function.

A method whose result is a view or a struct with a region (`Slice<u8>`, `StringView`, `Lease<c>`) and which takes
`borrow this` plus any other scoped parameter (a `StringView` name, a `Slice<u8>`, a `borrow T`) is N0245: "this
function has several reference parameters: name the ones the result is borrowed from". For a `borrow T` result the
answer is `-> borrow(this) T`. For a view or a region-carrying struct there is no way to write it:

```ceesafe
struct Table {
    bytes: Vector<u8>;

    // N0245: several reference parameters
    fn a(borrow this, name: StringView) -> Slice<u8>? => Some(this.bytes[0..name.length]);

    // N0246: references to references are not supported
    fn b(borrow this, name: StringView) -> borrow(this) Slice<u8>? => Some(this.bytes[0..name.length]);

    // N0100: expected identifier, found 'borrow'
    fn c<region r>(borrow(r) this, name: StringView) -> Slice<r, u8>? => Some(this.bytes[0..name.length]);

    // N0105: expected type, found 'this'
    fn d(borrow this, name: StringView) -> Slice<this, u8>? => Some(this.bytes[0..name.length]);
}
```

The spec (type-system.md §25.6) shows how to name another parameter's region
(`fn first<region a>(borrow this, other: borrow(a) Vector<u8>) -> Slice<a, u8>`), but nothing names the receiver's.
What does work is a static function, where the receiver is an ordinary parameter:

```ceesafe
fn find<region r>(table: borrow(r) Table, name: StringView) -> Slice<r, u8>? => Some(table.bytes[0..name.length]);
```

This is the most common shape of a lookup (`map.get(key)`, `headers.value(name)`, `client.get(url)`), so it is
probably why web_client's `ClientResponse.header(name)` returns a copied `Vector<u8>` instead of a view.

**Wanted:** either `borrow(r) this` in a method's receiver, or a result form that names the receiver
(`Slice<this, u8>`, or `borrow(this)` accepted in front of a scoped type as "the result's regions are `this`'s").

**Here, until 0.61.0:** `Client.get(client, url)`, `Client.send(client, request, body)` and
`Response.header(response, name)` were static functions; `response.find(name)` and `response.value(i)` were the
method form.

## F3. web_client: a request sometimes times out when 64 connections are dialed at once

**Cause found, and it is not web_client's:** CeeSafe's runtime, F7 below. With the runtime fixed, web_client at 64
connections passed 30 runs of 30 (11 of 30 timed out without it). The second thing this entry suspected, from reading
the code, was real too, and is fixed in CeeSafe.Web (F3b): a request that waited for a slot did not take a connection
given back to the pool; with `maxPerOrigin` 1 the second of two requests waited until `totalMs`. What follows is the
entry as first written.

**Kind:** CeeSafe.Web bug, or a runtime one under it; cause not found. **Seen:** 2026-11-16, macOS arm64,
ceesafec 0.57.1, CeeSafe.Web main (b3fbc75).

`bench/` runs web_client and this client against the same loopback server. With 64 tasks each sending requests
through one `web_client.Client` (`maxPerOrigin` and `maxIdlePerOrigin` 64, no TLS, HTTP/1.1), 2 runs of 6 ended
with `ClientError` "timed out" after exactly 10.00 s of wall time and 0.00 s of CPU: the process waited idle for one
of web_client's 10 s timeouts (`connectMs`). The other 4 runs finished in 0.3 to 0.5 s. At 16, 32 and 48 tasks no run
failed. This client, dialing the same 64 connections at once against the same server, passed 12 runs of 12.

```sh
cd bench && ceesafec build
./out/bench server 18290 &
for i in 1 2 3 4 5 6; do ./out/bench web http://127.0.0.1:18290/1k 20000 64; done
```

Not established: whether a connection attempt is lost (Happy Eyeballs' race, `dial.cee`), a wake-up is missed, or
the listener's backlog drops a SYN that only web_client's path fails to recover from. The two clients differ in
that web_client dials through `happyEyeballs` and a `Box<dyn DialJob>`, and this one awaits
`TcpTransport.connectTo` directly.

A second thing to check in the same code, from reading it only (not reproduced): a request that finds no idle
connection waits for a token on the origin's slot channel (`pool.cee`, `freshConnection`), and a connection given
back to the idle list sends no token. So with more concurrent requests than `maxPerOrigin`, a waiter may sleep
until a connection closes rather than until one is free.

## F4. web_client copies bodies a byte at a time (`copyBytes`)

**Fixed** on CeeSafe.Web's `fix/web-client-findings` (Web ADR 0009 amendment 2): whole-slice appends in `copyBytes`,
`Url` and `RequestWriter`, `readToEnd` into the vector's own room, and direct reads into a caller's buffer. For a
64 KiB body: 51 µs of user CPU a request before, 7.5 µs after.

**Kind:** CeeSafe.Web performance. **Measured:** 2026-11-16, `bench/run.py`, Apple M1, ceesafec 0.57.1.

`web_client/src/client.cee`'s `copyBytes` appends with one `Vector.push` for each byte, and `readToEnd`,
`ClientRequest.header`, `BytesBody.of` and the request writer all go through it. For a 64 KiB response body
web_client takes 46 µs of user CPU a request where this client takes 2 µs (the `64k` rows of
`bench/results/2026-11-16-m1.json`); `readToEnd` also reads through a fixed 8 KiB stack buffer, so every body byte
is copied twice. `to.extend(bytes)` is one capacity check and one `memcpy`.

**Wanted:** `copyBytes` as `to.extend(bytes)`, and `readToEnd` reading into the vector's own spare room
(`reserve`, then `read` into it) instead of through a stack buffer.

## F5. web_client allocates 32 to 35 times for each request

**Reduced to 10** on `fix/web-client-findings` (Web ADR 0009 amendment 2): F4's whole-slice appends alone brought it
to 17 (a vector grown a byte at a time reallocates as it goes); then the request's URL no longer copied for each
send, the request head reserved once, the connection held as an optional, and the read buffer kept with the pooled
connection. The 10 that are left (the URL parsed and copied for the response, the authority, the two heads) need a
response that borrows its connection's buffers, which is a different API from web_client's.

**Kind:** CeeSafe.Web performance. **Measured:** as F4 (`CEESAFE_ALLOC_REPORT`, the difference between runs of
2,000 and 12,000 requests): 32 allocations for a GET of 1 KiB on a pooled connection, 33 for 64 KiB, 35 chunked.
From reading `Client.attempt` and `send`: the head's `Vector`, a 16 KiB `OwnedBuffer` for each attempt, two copies of
the URL, a vector for every field name and value, a one-element vector to hold the connection, two `HeaderBlock`s
and the trailer vector, and a channel clone for each of three pool lookups. A connection that owned its buffers,
and a response that borrowed them, would need none of these (this client's count is 0).

## F6. In a loop, a local cannot lend to a value with a `drop` block that is made through `?`

**Fixed** in ceesafec 0.61.0 (ADR 0160 §3). **Kind:** borrow checker, a correct program rejected.

First reported here as "in an async fn": that was wrong. `async` has nothing to do with it; the first reduction
happened to have the other two ingredients only in its async form. The trigger is a loop, a borrower whose type
has a `drop` block, and the borrower coming out of a `Result` by `?` (or a `match` with a `return`):

```ceesafe
struct Owner {
    count: u32;
}

struct Lease<region c> {
    owner: borrow(c) Owner;

    drop {}
}

enum Bad {
    No,
}

fn lease<region c>(owner: borrow(c) Owner) -> Result<Lease<c>, Bad> => Ok(Lease { owner: owner });

fn run() -> Result<void, Bad> {
    for (k in 0..3) {
        let owner = Owner { count: 0 };   // N0240: cannot assign to 'owner' while 'owner' is borrowed
        let held = lease(owner)?;
    }                                     // N0243: 'owner' goes out of scope while still borrowed
    return Ok();
}

fn main() {
    let r = run();
}
```

Without the loop, without the `drop` block, or without the `?`, it is accepted.

**Cause** (`src/nir/borrow.cee`, liveness): destroying a value whose drop may read its loans is a use of them
(type-system.md §25.7 rule 6), and every `StorageDead` of such a local was taken as that use. On the path where `?`
returns, `held` was never assigned and the temporaries of `lease(owner)?` have been moved out of, so nothing is
destroyed there; taken as a use, that `StorageDead` kept the local live from the top of the loop's body, where it
still held the last turn's loan of `owner`.

**Fix:** a `StorageDead` of a local that the move analysis shows certainly not initialised there is no use. A local
that is, or may be, initialised is as before (two cases in the corpus's `adr0160-drop-reads-loans`).

**Here:** the loop's body is a function of its own (`streamedOnce` in `tests/h2.cee`).

## F7. A wait blocks after a full kqueue change queue's early send readied tasks

**Fixed** in ceesafec 0.61.0. **Kind:** CeeSafe runtime bug (macOS and the BSDs: kqueue), the cause of F3.

With exactly 64 descriptors armed at once (or a multiple), the wake pipe's own registration in `cee_wait_reactor` is
the 65th change, so `cee_kqueue_change` sends the first 64 early, without blocking. That call takes the events of
the connects that have already finished (one-shot: they do not come again) and readies their tasks, and its result
was dropped; `cee_wait_reactor` then blocked in `kevent` until the next deadline with those tasks ready. The earlier
fix of the same place (CeeSafe.Web's B16, `pipe_drained`) covered a drained wake, not readied tasks.

Reduced to lib/std alone (`async-early-send-ready.cee` in CeeSafe's corpus): tasks that each race a task set whose
child connects and reports on a channel against that channel's receive. It hangs about one run in seven at 64 and
128 tasks, and never at 8, 32, 60, 63, 65 or 70 (30 runs each). Plain `await connectTo(…)` in 64 tasks does not meet
it, because no wake pipe is opened; that is why this client never did.

**Fix:** the early send's result is kept (`early_ready`), and the wait that follows does not block when it readied a
task or drained a wake. 180 runs of the reduction at 64, 128 and 192 tasks: none hung.

## F8. lib/std has no DEFLATE decoder and no CRC-32

**Kind:** library gap, not a bug. **Found:** 2026-11-16, ceesafec 0.61.0, adding gzip and deflate to this client.

lib/std has `Crc32c` (Castagnoli) and `Crc64Nvme`, but not CRC-32 as gzip, zip and PNG use it (ISO 3309, polynomial
0xEDB88320), and no DEFLATE, gzip or zlib. CeeSafe.Web's `web_ws` has a public `Inflater` for permessage-deflate, a
bit at a time and without the gzip and zlib wrappers; using it here would also pull `web_http`, `web_http1` and the
WebSocket code into a client that needs none of them.

**Here:** `src/inflate.cee` has its own decoder (DEFLATE with a 10-bit lookup table, gzip, zlib, CRC-32 and
Adler-32), tested against bodies compressed by Python's zlib.

**Wanted, if lib/std is the place:** `Crc32` beside `Crc32c`, and an inflate that `web_ws` and an HTTP client could
both use. A compressor is not needed for this.

## F9. A Copy field read inside a returned struct literal is taken as a loan of its parameter

**Kind:** borrow checker, a correct program rejected. **Found:** 2026-11-17, ceesafec 0.61.0. **Reduced:** yes.

A function returns a struct with a region, built from one parameter's loan, and sets a `bool` field of it from a
field of another borrowed parameter. Written in the literal, the read is N0245, "the returned reference may be
derived from parameter 'entry', which the return type does not list". A `bool` holds no loan. The same value read
into a local first is accepted.

```ceesafe
struct Owner {
    count: u32;
}

struct Entry {
    flag: bool;
    size: usize;
}

struct Lease<region c> {
    owner: borrow(c) Owner;
    flag: bool;
}

// N0245: the field read inside the literal is taken as a loan of `entry`.
fn direct<region c>(owner: borrow(c) Owner, entry: borrow Entry) -> Lease<c> {
    return Lease { owner: owner, flag: entry.flag };
}

// Accepted: the same value through a local.
fn local<region c>(owner: borrow(c) Owner, entry: borrow Entry) -> Lease<c> {
    let flag = entry.flag;
    return Lease { owner: owner, flag: flag };
}
```

**Here:** `storedResponse` in `src/client.cee` reads `entry.decoded` into a local before the literal.

## F10. A hook kept in a long-lived struct and called from async code has no practical form

**Kind:** ergonomics gap, not a compiler bug: every rule involved is doing its job. **Found:** 2026-11-17,
ceesafec 0.61.0. **Not explored to the end:** an async closure type (`@effects(suspend)`) in a field was not tried.

The .NET client has telemetry hooks (`OnRequest`, `OnResponse`, `OnError`). The port wanted the same as fields of
`ClientOptions`. Each form met a wall:

- a plain function value, `(fn(Method, Slice<u8>) -> void)?`, checks as a field, but calling it from an `async fn`
  is N0295: a function type without an effect list may sleep (type-system.md §36.3), and that error then spreads to
  every async caller of the client;
- `@effects(alloc) fn(...)` can be called, but a function of that type may not do console or file I/O, and a plain
  function captures nothing, so it has nowhere to put what it sees;
- a capturing closure (`borrow @effects(alloc) fn(...)`) could update the caller's state, but a struct that borrows a
  closure has a region parameter, which would then be on `ClientOptions`, `Client`, and every type that names them.

**Here:** cosmohttp keeps counters (`Client.stats()`) and has no hooks; a caller times and logs around its own
calls. **What would help:** an owned closure type that can be stored without a region (a boxed closure), or a
documented pattern for "a callback registered once, called from async code".

## F11. gcc 13 warns that the generated C frees the empty-vector sentinel

**Kind:** build noise; no runtime fault found. **Found:** 2026-11-17, ceesafec 0.61.0 with gcc 13.3.0 on Ubuntu
24.04 x86-64 (clang on macOS says nothing). **Not reduced.**

Building cosmohttp's tests (and cosmohttp3's, which include cosmohttp) prints three warnings like:

```
ceesafe/rt.h:962:9: warning: 'free' called on unallocated object 'ceesafe_vec_empty' [-Wfree-nonheap-object]
In function 'cee_free_inline',
    inlined from 'ceeD_VUu8S10SystemHeap' at main.c:103233:23,
    inlined from 'ceeAR_ceeM_09cosmohttp9H2Session_leadWith' at main.c:75831:5,
```

The source is `H2Session.leadWith` in `src/h2.cee`: a local `var target = Vector<u8>.create();` that is swapped
with a stream's body on some paths (`swap(mut st.streams[d].body, mut target)`) and dropped at the end of each
loop turn. The generated drop frees only when the capacity is not 0 (`if (p->cap != 0u) ceesafe_free(p->ptr, ...)`,
`src/backend/c_helpers.cee`), and an empty vector's pointer is the sentinel with capacity 0, so the free is never
reached with the sentinel. After inlining, gcc sees the pointer's initial value and cannot see that the capacity
goes with it. The tests and both sweeps pass on that build.

**What would help:** a drop that tests the pointer against the sentinel (which gcc can follow), or the warning
switched off for generated code on gcc, so a user's build is quiet.

## F12. The formatter does not wrap an interface method's signature

**Kind:** formatter. **Found:** 2026-11-17, ceesafec 0.61.0. **Not reduced** (the file is small: `src/alt.cee`).

A method declared in an `interface` with many parameters is put on one line however long it is (here 230
columns), where the same signature on a function or in an `impl` is wrapped one parameter to a line. A trailing
comma written by hand stays, giving `answer: borrow mut Http3Answer,) -> ...`. With an attribute before it, the
attribute's argument list is what gets broken:

```ceesafe
public interface Http3Transport {
    async fn fetch(borrow this, method: Method, url: Slice<u8>, parts: UrlParts, port: u16, fields: Slice<u8>, body: Slice<u8>, responseMs: u64, maxBodyBytes: u64, answer: borrow mut Http3Answer,) -> Result<void, Http3Failure>;

    @effects(
        alloc,
    ) fn trailers(borrow this, stream: u64, head: borrow mut Vector<u8>, spans: borrow mut Vector<HeaderSpan>) -> Result<usize, AllocError>;
}
```

`ceesafec fmt --check` accepts its own output, so nothing fails; the lines are just not in the style the rest of
the file is in.

## F13. web_client's HTTP/2 is very slow with 64 KiB bodies on Linux

**Kind:** performance, CeeSafe.Web's web_client (or web_http2 under it). **Found:** 2026-11-17, in this package's
benchmark on Linux x86-64 (Xeon E3-1270 v6, Ubuntu 24.04, ceesafec 0.61.0), against CeeSafe.Web's own
`benchmarks/http` server, CeeSafe.Web `main` at `615a85d`. **Not looked into.**

`python3 bench/run.py --h2`, route `64k`, mode `web2` (web_client, HTTP/2 by prior knowledge, all requests on one
connection):

| streams at once | requests a second | client CPU for a request |
|---:|---:|---:|
| 1 | 117 | 249 µs |
| 16 | 329 | 562 µs |
| 64 | 1,079 | 427 µs |

The same client on the same machine does 20,708 a second for 4 KiB bodies and 22,805 for 64 KiB over HTTP/1.1,
and cosmohttp over HTTP/2 does 13,831 to 35,154 for the same 64 KiB route against the same server, so the server
is not what is slow. With one stream a request takes 8.5 ms of which 0.25 ms is CPU: it waits for something. With
16 and 64 streams the CPU time is what grows. On an M1 the same rows were 2,119, 7,443 and 8,670 a second: slow
there too, less so. No run failed.

## F2. Withdrawn: diagnostics were not printed twice

Reported as "`ceesafec check <file>` prints each diagnostic twice". It does not. The commands that showed it ran
`ceesafec check` and then `ceesafec run --interp` (or `ceesafec test`) on the same file in one line, and each printed
the error once. Checked again with 0.57.1 and 0.60.0: one diagnostic for each error.
