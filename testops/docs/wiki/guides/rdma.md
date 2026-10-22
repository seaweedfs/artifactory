# RDMA / VFS / Object

This page is the TestOps entry point for the current SeaweedFS RDMA work in
`seaweed-mono`. It covers the lab gate, the runner command, and the performance
rows that are treated as release signals.

For the platform-wide SOP, see [Storage Platform SOP](../../storage-testops-platform.md).

## Current Shape

The active RDMA gate is driven by:

```text
sw-test-runner repo
  cmd/sweedrdma
  packs/rdma
  scenarios/rdma-unified-lab-gate.yaml

seaweed-mono repo
  enterprise/seaweed-volume          RDMA-enabled Rust volume server
  enterprise/rust/sw-rdma            low-level RDMA transport
  enterprise/rust/sw-rdma-loader     shared transfer code
  enterprise/rust/sw-rdma-object     object/S3-facing loader and benches
  seaweed-vfs                        VFS client path
```

The RDMA pack does not implement data movement. It calls the M01/M02 lab runner,
parses stable witness lines, and records the result as a normal TestOps bundle.

## Lab Topology

| Node | Role |
| --- | --- |
| **M01** `192.168.1.181` | client / VFS mount / object loader / runner |
| **M02** `192.168.1.184` | master + filer + RDMA-enabled Rust volume |
| **RoCE** | M01 `10.0.0.1`, M02 `10.0.0.3` |

The lab is the hardware gate. Software-RDMA/SIW CI remains in `artifactory`.

## Backends

| Backend | Status |
| --- | --- |
| `http` | baseline / fallback comparison |
| `rc push` | primary object hot path; must stay above 5 GiB/s |
| `rc pull` | supported object path; currently above 5 GiB/s in the lab |
| `dc push` | supported object path; must stay above 5 GiB/s |
| `dc pull` | intentionally unsupported today |
| `ucx` | not part of the current release gate |

Do not read `dc pull = unsupported` as a failure. It is a future matrix row.
The current production-candidate rows are RC push, RC pull, and DC push.

## Running the Gate

From this repo:

```bash
sweedrdma validate scenarios/rdma-unified-lab-gate.yaml

sweedrdma run scenarios/rdma-unified-lab-gate.yaml \
  -env mono_ref=main \
  -meta project=rdma \
  -meta run_by=$USER
```

For a feature branch:

```bash
sweedrdma run scenarios/rdma-unified-lab-gate.yaml \
  -env mono_ref=rdma/lab-gate-runner-tools \
  -meta project=rdma \
  -meta run_by=$USER
```

The scenario writes a standard TestOps bundle under `results/` unless
`-results-dir` is overridden.

## What the Gate Proves

The unified gate checks:

- one RDMA-enabled Rust volume server;
- object upload/read-back correctness;
- object to VFS read parity;
- VFS write then fresh remount read-back;
- object hot-path benchmark;
- proxy/coordinator style object GET matrix;
- S3 loader matrix with explicit source/destination fields;
- VFS read matrix for HTTP, RC1, RC4, RC8, RC16;
- commit witness on M02;
- no silent fallback in RDMA rows.

The loader matrix uses stable row language:

```text
op=read|write
backend=http|rc|dc
direction=pull|push
source=object-chunks|client-buffer
destination=file|object-chunk
status=PASS|UNSUPPORTED|FAIL
```

## Current Reference Result

Latest accepted TestOps run:

```text
run bundle: results/rdma-platform-smoke/20260629-152417-b7e9
mono sha:   1fb9be830695b8e97c3dc8380389695f0a6cb961
```

Observed rows:

| Row | Result |
| --- | ---: |
| object RC push | `11648.1 MiB/s` |
| object RC pull | `5504.9 MiB/s` |
| object DC push | `9756.1 MiB/s` |
| object bench | `11377.1 MiB/s` |
| VFS RC4 read | `453.90 MiB/s` |
| VFS write latest | `204.15 MiB/s` |
| loader rows | `9` |

Object numbers are the current high-performance signal. VFS numbers are
correctness and path-health signals; they are still limited by the VFS/kernel
path and should not be compared directly to object hot-path throughput.

## Non-Claims

- This page does not claim UCX is a current release path.
- This page does not claim VFS reaches raw RoCE bandwidth.
- This page does not claim DC pull is implemented.
- This page does not replace the mono PR gate; it documents how TestOps runs it.
