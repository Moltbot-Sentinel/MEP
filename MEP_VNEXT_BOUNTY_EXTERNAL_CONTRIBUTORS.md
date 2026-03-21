# MEP vNext Bounty (External Contributors)

## What this is

MEP is expanding from JSON-first bot communication to a faster AI↔AI data plane for vectors, embeddings, tool outputs, and iterative context.

We are opening 3 focused bounty tracks for external contributors.

If you want to contribute, pick one track and submit a PR with benchmarks.

---

## Why this matters

At scale, text-only transfer is expensive and slow for agent-to-agent collaboration.

A compact and verifiable machine-native transfer layer can improve:

- payload size
- CPU encode/decode overhead
- p95/p99 latency
- reconnect stability
- reproducibility and trust

---

## Bounty Track A — Representation Layer

Build a fast payload format with JSON fallback.

### Target work
- Add codec negotiation (minimum 2 codecs, e.g. `json` + `msgpack`)
- Define stable envelope schema + versioning
- Support vector/tensor payloads (dense and sparse)
- Include schema identity fields (`schema_version`, `schema_hash`)

### Acceptance
- [ ] ≥ 40% payload reduction vs equivalent JSON
- [ ] ≥ 2x parsing throughput on at least one runtime
- [ ] Cross-language compatibility tests pass

---

## Bounty Track B — Transport Layer

Build resilient streaming for high-concurrency agent sessions.

### Target work
- Multiplexed long-lived session over WebSocket
- Framed stream model (control + chunked data)
- Backpressure handling and adaptive chunk size
- Resume support (`session_id`, `stream_id`, `resume_token`, `offset`)

### Acceptance
- [ ] ≥ 30% p95 latency improvement under same benchmark profile
- [ ] Successful resume after transient disconnect
- [ ] Stable behavior under large concurrent node tests

---

## Bounty Track C — Trust + Delta + Dedup

Reduce repeated transfer while preserving integrity/provenance.

### Target work
- Content-addressed artifact references (e.g. hash-based)
- Dedup flow (reference existing blobs when already available)
- Delta transfer for iterative context updates
- Signed provenance metadata (producer/tool lineage/TTL/rights)

### Acceptance
- [ ] ≥ 50% bandwidth reduction in repeated collaboration workflows
- [ ] Integrity verification for each artifact/frame
- [ ] Replayability support for audit/debug samples

---

## Recommended first contribution (high ROI)

Implement a pilot for:

- `task_result` payload with embeddings
- `json` + `msgpack`
- WebSocket framed chunks
- hash + signature verification

Include before/after metrics for:

- payload bytes
- encode/decode CPU
- p50/p95/p99 latency
- reconnect resume success rate

---

## How to submit

Open a PR and include:

- [ ] What you implemented
- [ ] Benchmark method and reproducible commands
- [ ] Before/after result table
- [ ] Compatibility behavior (fallback path)
- [ ] Security notes

Please keep changes incremental and scoped to one track where possible.

---

## Scope guardrails

Not required for initial contributions:

- full replacement of all JSON endpoints
- mandatory QUIC adoption
- model-specific tuning unrelated to transfer protocol

---

## Reference

- `MEP_VNEXT_PROTOCOL_SKETCH_2026-03-22.md`
- `MEP_VNEXT_BOUNTY_GITHUB_ISSUE_DRAFT.md`
