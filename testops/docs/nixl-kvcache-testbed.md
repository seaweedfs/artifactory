# NIXL and KVCache Testbed Plan

This page defines how TestOps will host the next RDMA work:

- NIXL/object compatibility and benchmark gates.
- RDMA transport hardening with repeatable M01/M02 evidence.
- A CPU in-memory KVCache MVP before any GPU-only claim.

It is a testbed plan, not a product claim. Product code still lives in
`seaweed-mono`; TestOps owns scenario metadata, lab orchestration, evidence, and
acceptance profiles.

## Current Baseline

The `rdma` suite already gates the mono object/VFS RDMA path:

- one RDMA-enabled Rust volume server;
- object loader rows for `http`, `rc pull`, `rc push`, and `dc push`;
- VFS read/write correctness rows;
- perf floors for object hot paths;
- shared evidence through `qa-assert.sh`.

This remains the release gate. New NIXL/KVCache work starts as additional
scenarios under the same lab lease, then graduates to its own suite only if it
needs different workers or hardware.

## Roadmap

### R1: NIXL As a RDMA-Suite Scenario

Goal: run the existing NIXL/object compatibility code through TestOps without
changing the RDMA release gate.

Scenario shape:

```text
checkout seaweed-mono ref
build NIXL/object provider or stub path
start M02 volume/filer
run CPU NIXL/object GET and PUT compatibility checks
emit:
  __product=rdma
  __gate_pass
  __tested_ref
  __tested_sha
  __lab_run_id
  __nixl_obj_cpu_get_sha_match
  __nixl_obj_cpu_put_sha_match
  __nixl_obj_backend=seaweed
```

Acceptance:

- CPU GET and PUT both SHA-match.
- No GPU/cuObject claim.
- Any unsupported row emits `UNSUPPORTED`, not a silent fallback.
- The existing object RDMA release rows are not weakened.

### R2: RDMA Transport Hardening

Goal: keep the object path at the pre-migration performance level while adding
NIXL-facing tests.

Required rows:

| Row | Floor |
|---|---:|
| `rc push` | `>= 5120 MiB/s` |
| `rc pull` | `> 0` and recorded |
| `dc push` | `>= 5120 MiB/s` when DC is enabled |
| `dc pull` | explicit `UNSUPPORTED` until implemented |

The gate must prove the numbers come from real object data:

- 128 MiB object payload;
- c32 workload;
- bytes and SHA match;
- source is volume data, not a synthetic memory-only loop;
- rows are emitted by the product gate, not hand-entered.

### R3: In-Memory KVCache MVP

Goal: build the smallest useful KVCache service before discussing GPU/NIXL
integration.

MVP contract:

```text
put(key, bytes) -> ack
get(key) -> bytes
exists(key) -> bool
delete(key) -> ack
```

Constraints:

- CPU memory only.
- Fixed-size registered slabs or a bounded buffer pool.
- No prefix matching in V1 unless the basic key path is already green.
- RDMA transport optional at first; local loopback correctness comes first.
- Every test records byte count and SHA.

Why this order: hot KVCache is not just a file/object read. It needs key
ownership, memory pressure rules, eviction semantics, and a stable API. The
object/NIXL path can use existing Seaweed storage; KVCache needs a small service
surface of its own.

### R4: KVCache RDMA Path

Goal: move the MVP from local memory calls to RDMA-backed transfer.

Rows:

| Operation | Backend | Requirement |
|---|---|---|
| put | local | SHA match |
| get | local | SHA match |
| put | rc | SHA match, no silent fallback |
| get | rc | SHA match, no silent fallback |

DC and GPU rows stay out until RC CPU is stable.

### R5: NIXL / LMCache Decision Point

After R1-R4, decide which integration is worth product work:

- NIXL object backend path: good for object-store ecosystem compatibility.
- LMCache connector path: better for real KVCache workloads if the service API
  is strong.
- GPU/cuObject path: only after CPU correctness and hardware requirements are
  explicit.

## TestOps Ownership

TestOps owns:

- scenario YAML;
- controller/worker registration;
- result bundle layout;
- profile floors;
- dashboard visibility;
- QA assertion scripts.

Mono owns:

- RDMA transport code;
- object/VFS/KVCache implementations;
- stable witness lines printed by product gates;
- product docs for runtime flags and operation.

## Immediate Work Items

1. Add a NIXL CPU provider scenario that reuses the RDMA lab lease.
2. Add `docs/qa-profiles/nixl.expect` once the first scenario emits stable keys.
3. Add a minimal KVCache in-memory scenario with no RDMA dependency.
4. Only then add RDMA rows to the KVCache scenario.
