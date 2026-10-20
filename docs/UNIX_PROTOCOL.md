# Unix socket protocol, version 1

The host creates one `UnixBrokerServer` for one `Arc<Broker>`. Every connection to that listener uses the same execution identity, resource configuration, grants, budget, and request-ID ledger. A workload cannot choose the execution by submitting an ID. The protocol exposes effect requests only; grant issuance, revocation, and execution termination remain trusted Rust methods.

## Framing

Each frame consists of a four-byte unsigned big-endian payload length followed by exactly that many bytes of UTF-8 JSON. The length excludes the four-byte prefix. No newline or EOF delimiter is used. A connection may send multiple frames; the server processes them sequentially and sends one response per complete request. The Python client opens a new connection per call.

| Limit | Value |
|---|---|
| Request payload | 1–16,384 bytes |
| Response payload | At most 1,399,128 bytes |
| Decoded HTTP response body | At most 1,048,576 bytes |
| Active connection workers | At most four per listener |
| Reading a frame | Two seconds total across prefix and payload |
| Writing a response | Two seconds total across prefix and payload |
| Python call deadline | Ten seconds total across connect, send, and receive |

The server checks a request's declared size before allocating or reading its payload. Empty, oversized, truncated, and timed-out frames are rejected and closed without admission. Valid-sized malformed JSON is denied by the broker. Excess connections receive a best-effort `capacity_exceeded` denial and are closed; they never create another worker or consume request budget. Connection failures can occur before a denial is received.

Read/write deadlines are enforced across partial I/O, rather than restarting the entire timeout after each byte. Slow or idle connections therefore cannot hold workers indefinitely. The HTTP adapter's separate five-second total I/O deadline still applies after admission. Storage delays can exceed a client's deadline; losing a client connection never refunds an admission or triggers a target retry.

## Request

```json
{
  "request_id": "summary-read",
  "capability": "opaque-execution-bound-handle",
  "method": "GET",
  "path": "/invoices"
}
```

All four fields are required strings. Limits are 128 UTF-8 bytes for `request_id`, 256 for `capability`, 16 for `method`, and 2,048 for `path`; empty values are invalid. Unknown or duplicate fields are invalid. Identity fields, target URLs, headers, bodies, and grant controls cannot be added to a request. Successfully parsing a message grants no authority.

The current grant permits exactly `GET /invoices`. Query suffixes, encoded path variants, other routes, and other methods are denied. Request IDs are scoped to the execution and retained across reconnects and replacement grants. An already admitted ID cannot be reused to acquire a fresh attempt under a different grant.

## Responses

A successful HTTP exchange carries its body as Base64, preserving binary data:

```json
{"type":"response","status":200,"body_base64":"W10="}
```

The Python client converts this to `{"type": "response", "status": 200, "body": b"[]"}`. It validates both the wire-frame size and the decoded body size. HTTP error statuses are also responses and still consume an allowance.

An authorization denial:

```json
{"type":"denied","reason":"revoked"}
```

Reasons are the stable `DenialReason` names in snake case. They include `invalid_request`, `invalid_execution`, `invalid_capability`, `missing_grant`, `wrong_resource`, `out_of_scope`, `expired`, `revoked`, `budget_exhausted`, `state_unavailable`, `request_conflict`, and `capacity_exceeded`.

A duplicate returns recorded status, without another HTTP call or response-body replay:

```json
{"type":"replay","outcome":{"state":"completed","status":200}}
```

Other replay states are `{"state":"in_progress"}` and `{"state":"unknown","reason":"timeout"}`. An unknown reason may be `null` when durable recording failed. Current authority is checked before returning replay metadata: revocation, expiry, and a wrong capability cannot be bypassed by resubmitting an old ID.

A newly attempted operation whose outcome cannot be established:

```json
{"type":"unknown_outcome","reason":"timeout"}
```

This result does not prove that the target performed no effect. Neither the server nor the Python client retries it automatically. A partial, missing, or malformed response likewise leaves the caller uncertain. A journal failure can close the endpoint before an error response is delivered. Python raises `ProtocolError` for invalid/incomplete responses and `TransportError` for connection failures; neither exception causes an automatic retry.

## Endpoint lifecycle and current limits

`UnixBrokerServer::start` creates a fresh random private directory under the host's temporary directory. The directory has mode `0700`; its socket has mode `0600`. `start_in` permits a trusted caller to choose an existing parent directory. Long Unix socket paths fail at startup. Existing socket paths are never reused or blindly deleted.

`shutdown` commits broker termination, stops acceptance, closes existing connections, joins workers, and removes the endpoint. Directly stopping the broker is also detected by the listener. Operations admitted before termination may still finish, retain their allowance, and record their outcome. Unexpectedly replaced paths or extra directory contents are preserved and cleanup reports an error. A crash may leave a stale directory; a new server always creates a different path and session instead of resuming it.

The host injects `INVARIANT_SOCKET` and `INVARIANT_CAPABILITY` into the example Python program, along with `PYTHONPATH` for the standard-library SDK. These are an endpoint and a scoped capability, not target credentials. The example clears unrelated inherited environment variables and disables Python site initialization.

The Docker supervisor supplies `INVARIANT_CAPABILITY_FILE` instead of a static
handle. The SDK validates and reads that bounded file for each explicit call so
live operator issuance can replace a capability without restarting Python.
No extra fields or control operations are added to this effect protocol.
See [operator controls](OPERATOR_CONTROLS.md) for the separate protected interface.

This transport alone does **not** isolate host processes running as the same user. The [Docker supervisor](DOCKER_EXECUTION.md) now provides private mounts, process supervision, resource limits and network restrictions. It exposes only the execution's exact socket inode, assigned to the workload UID with mode `0600`; the host parent remains private. The earlier host-side Python demo is still a transport demonstration and does not provide a sandbox.
