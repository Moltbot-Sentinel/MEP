# MEP vNext Protocol Sketch (AI↔AI Fast Data Plane)

- Timestamp: 2026-03-22T00:43:37.0667303+08:00
- Status: Draft for bounty scoping
- Scope: Protocol sketch for higher-efficiency bot-to-bot transfer

## 1) Objectives

MEP vNext introduces a dual-plane design:

- **Control Plane**: human-readable JSON for compatibility, orchestration, and debugging.
- **Data Plane**: compact typed binary frames for high-frequency AI↔AI transfer.

Primary targets:

- Reduce payload size by 40–80% versus JSON-only transfer.
- Cut decode/encode CPU overhead for Python/Go/Rust agents.
- Improve p95/p99 latency under concurrent multi-agent workloads.
- Preserve trust via signed envelopes and verifiable artifact hashes.

## 2) Three Bounty Areas

### Bounty A — Representation Layer (Fast Language)

Design a machine-native payload representation with JSON fallback.

**Deliverables**

- Envelope schema with stable fields and versioning.
- Codec negotiation: `json`, `msgpack`, `protobuf` (minimum two required).
- Native tensor/vector blocks:
  - dense `float32/float16/int8`
  - sparse vectors (`indices`, `values`, `shape`)
  - embedding-set collections
- Canonical schema identifiers:
  - `schema_version`
  - `schema_hash`

**Success Criteria**

- ≥ 40% size reduction over equivalent JSON payloads.
- ≥ 2x parsing throughput on at least one runtime.
- Cross-language conformance tests pass (Python↔Go or Python↔Rust).

### Bounty B — Transport Layer (Streaming + Session Efficiency)

Design low-latency, resilient transfer channels for real-time agent collaboration.

**Deliverables**

- Multiplexed long-lived session over WebSocket first; QUIC-ready abstraction second.
- Framing model:
  - small control frames
  - chunked binary data frames
  - stream IDs for concurrent flows
- Backpressure signaling and adaptive chunk sizing.
- Resume protocol:
  - `session_id`
  - `stream_id`
  - `resume_token`
  - `offset`

**Success Criteria**

- ≥ 30% p95 latency improvement under same workload profile.
- Transfer continuity after transient disconnect without full retransmit.
- Stable performance at 100–1000 simultaneous nodes in stress tests.

### Bounty C — Trust, Reuse, and Delta Layer

Move fewer bytes while strengthening integrity and provenance.

**Deliverables**

- Content-addressed artifacts (`blake3` hash suggested).
- Dedup and reference transfer:
  - send metadata + hash when receiver already has blob
- Delta payload format for iterative context updates.
- Signed provenance envelope:
  - producer node
  - model/tool lineage
  - TTL and rights metadata
  - deterministic replay metadata

**Success Criteria**

- ≥ 50% bandwidth reduction for repeated collaborative workflows.
- Integrity verification on every artifact/frame.
- Replayability for audit/debug in sampled production traces.

## 3) Protocol Sketch

### 3.1 Envelope (control + metadata)

```json
{
  "mep_version": "2.0",
  "message_type": "task_result",
  "codec": "msgpack",
  "schema_version": "task_result.v1",
  "schema_hash": "sha256:...",
  "trace_id": "uuid",
  "session_id": "uuid",
  "stream_id": "s-17",
  "timestamp_ms": 1774111417066,
  "sender_node_id": "node_...",
  "recipient_node_id": "node_...",
  "capability_ref": "capset-20260322",
  "artifact_refs": [
    {"hash": "blake3:...", "bytes": 1250000, "mime": "application/x-mep-tensor"}
  ],
  "signature": "base64..."
}
```

### 3.2 Binary Frame (data plane)

Conceptual frame layout:

```text
[magic][version][flags][stream_id][frame_type][seq_no][payload_len][payload_bytes][crc32]
```

Frame types:

- `CONTROL_OPEN`
- `CONTROL_ACK`
- `DATA_CHUNK`
- `DATA_END`
- `RESUME_REQUEST`
- `RESUME_ACK`
- `ERROR`

### 3.3 Capability Negotiation

On session open, peers exchange `CapabilitySet`:

- supported codecs (`json`, `msgpack`, `protobuf`)
- tensor formats (`f32`, `f16`, `int8`, `sparse`)
- compression (`none`, `zstd`)
- max frame size
- max concurrent streams
- delta support (`yes/no`)

Runtime chooses highest common efficiency profile.

## 4) Migration Strategy (from current MEP)

Phase 1 (compatible):

- Keep current JSON APIs as baseline.
- Add capability advertisement endpoint.
- Add optional binary codec for selected message types.

Phase 2 (hybrid):

- Route large vector/artifact payloads through data-plane frames.
- Keep control-plane JSON for task lifecycle and audit readability.

Phase 3 (optimized default):

- Fast codec/data-plane becomes default when both peers support it.
- Automatic fallback to JSON for unsupported peers.

## 5) Suggested First Implementation Slice

Small, high-impact vNext pilot:

- Message type: `task_result` with embedding payload
- Codecs: `json` + `msgpack`
- Transport: existing WebSocket with framed chunks
- Trust: artifact hash + signature verification
- Benchmark set:
  - payload size
  - encode/decode CPU time
  - p50/p95/p99 latency
  - reconnect resume success rate

## 6) Why This Matters for AI↔AI Future

Yes, this is a likely key differentiator for future AI↔AI systems:

- AI workflows exchange vectors, intermediate plans, tool outputs, and memory states.
- Text-first protocols are easy but wasteful at scale.
- Efficient typed transfer plus trust metadata enables:
  - lower cost per collaboration
  - higher concurrency
  - safer inter-agent composition
  - better reproducibility and governance

Practical principle:

- **Text for humans, typed compact frames for agents.**
