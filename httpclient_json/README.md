# httpclient_json

Typed JSON over [httpclient](..)'s `Client`: a request's body written from a value, a response's body read into
one. The values' types implement `ToJson` and `FromJson` from [CeeSafeLibs](https://github.com/CeeSafe/CeeSafeLibs)'
`json` package, which does the parsing and writing; this package is the few lines that join it to the client, kept
apart so that the client itself has no JSON in it.

```ceesafe
import httpclient;
import httpclient_json;
import json;

struct Server {
    host: String;
    port: u16;
}

impl ToJson for Server {
    fn toJson(borrow this, w: borrow mut JsonWriter) -> Result<void, JsonWriteError> {
        w.beginObject()?;
        w.field("host", this.host)?;
        w.field("port", this.port)?;
        return w.endObject();
    }
}

impl FromJson for Server {
    public fn fromJson(value: borrow JsonValue) -> Result<Server, DecodeError> {
        Decode.expectObject(value, "a server")?;
        return Ok(Server { host: Decode.field<String>(value, "host")?, port: Decode.field<u16>(value, "port")? });
    }
}

let client = Client.create(ClientOptions.defaults());
let one = await getJson<Server>(client, "http://example.com/servers/1")?;            // Result<Server, JsonFailure>
let made = await postJson<Server, Server>(client, "http://example.com/servers", one)?;
let tree = await getJson<JsonValue>(client, "http://example.com/anything")?;         // any JSON, as a tree
```

- `getJson<T>(client, url)`: a GET with `Accept: application/json`, the response read as a `T`.
- `postJson<B, T>(client, url, value)`: `value` sent as compact JSON with `Content-Type: application/json`, the
  response read as a `T`. `exchangeJson<B, T>(client, method, url, value)` is the same with another method (PUT,
  PATCH).
- `sendJson<T>(client, request, body)`: a request of your own (credentials, other fields), the response read as a
  `T`; `jsonRequest(method, url, withBody)` makes one with the JSON fields set.
- `fromResponse<T>(response)`: a `T` from a response you already have.

A call fails with a `JsonFailure`: `Http(HttpError)` when the request got no response, `Status(code)` when the
status was not 2xx (the body is not kept), `Encode(JsonWriteError)` when the value could not be written, and
`Decode(DecodeError)` when the body is not JSON or not the JSON the type needs; a `DecodeError` says where
(`$.port: expected u16`). Whatever the client does applies underneath: redirects, retries, the cache, cookies,
decompression, HTTP/2 and HTTP/3.

The response is buffered and parsed into a tree before the value is built from it, as the `json` package decodes;
nothing here streams.

## Working on it

```
ceesafec check && ceesafec fmt --check
ceesafec test          # 2 tests, over scripted connections (`Client.script`); also --interp and --sweep alloc
```
