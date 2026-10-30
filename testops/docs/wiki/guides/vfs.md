# VFS (POSIX access over the shared object/KV data)

VFS is **not a separate data store** — it is a **POSIX mount view of the same
object/KV data** that S3 serves. SeaweedFS keeps one filer namespace over volume
needles; S3 exposes it as an object API, and a **VFS mount** (FUSE / kernel)
exposes it as files. Write a file through the mount and it is an object readable
via S3; put an object via S3 and it is a file readable through the mount.

That is exactly why VFS matters and why it is worth a gate of its own:

> **Existing data must be consumable by S3 AND VFS at the same time.** The VFS
> signature gate is **cross-access**: write via one access method, read back via
> the other, byte/SHA-identical.

## Why it isn't "just onboarded" like S3

S3 is a stateless HTTP call; a VFS read/write needs a **stateful mount on M01**
(FUSE or kernel module) against the volume/filer on M02. So VFS onboarding needs
two things S3 doesn't:

1. an **M01 mount step** (bring up `seaweed-vfs` / `sw-rdma-vfs`, `fusermount3`,
   `/dev/fuse`), and
2. a **mount lease** so it doesn't collide with the kernel-module / RDMA-port
   singletons on M01.

Today VFS is validated **inside the RDMA unified gate** (its VFS read/write rows —
`__rdma_perf_vfs_read_rc4_mib_s`, `vfs_write_latest`, remount SHA), not as a
standalone suite.

## Topology

| Node | Role |
|---|---|
| **M01** `192.168.1.181` | VFS **mount** (FUSE/kernel), reader/writer, RDMA `10.0.0.1` |
| **M02** `192.168.1.184` | filer + volume (the shared object/KV data), RDMA `10.0.0.3` |

Source lives in the mono repo `C:\work\rdma\seaweed-mono-rdma-refresh`:
`seaweed-vfs` (client) + `enterprise/rust/sw-rdma-vfs` (RDMA VFS path) +
`enterprise/rust/volume-server`.

## The cross-access gate (the one to build)

```text
seed:  weed filer + volume on M02  (one dataset)
step1: PUT object via S3            -> object OBJ, sha=S
step2: mount filer on M01 (VFS)     -> read the same path -> sha == S      (S3 -> VFS)
step3: write a file via the mount   -> sha=W
step4: GET it via S3                -> sha == W                            (VFS -> S3)
assert: byte/SHA parity both directions, no silent fallback
emit:  emit_provenance product=vfs tested_ref/sha
         __vfs_s3_to_mount_sha_match=1  __vfs_mount_to_s3_sha_match=1
         __vfs_read_mib_s=...  __vfs_write_mib_s=...
```

This proves the unified namespace — one dataset, two consumers — which no
single-access gate can. Transport can then be matrixed (mount over http vs RC).

## Onboarding VFS (the 5 contract items)

Same contract as every product ([Control-Plane Product Contract](../../control-plane-product-contract.md)):

1. **Runner + pack** — a `packs/vfs` (or reuse `sweedrdma`) that mounts/unmounts +
   parses stable witness lines.
2. **Unified gate scenario** — the cross-access chain above; `emit_provenance` at
   the end.
3. **qa-profile** — `docs/qa-profiles/vfs.expect`: `__vfs_s3_to_mount_sha_match=1`,
   `__vfs_mount_to_s3_sha_match=1`, perf floors.
4. **Result project** — `vfs-qa` (dev `vfs-dev`); today `vfs-rdma-qa`.
5. **Worker adapter + mount lease** — a `vfs-ci-worker` that takes the **M01 mount
   lease** before mounting.

Until the worker exists, run it via the CLI like S3/block; the same
`qa-assert.sh --profile docs/qa-profiles/vfs.expect` accepts the bundle.

## Non-claims

- VFS perf is a correctness/path-health signal, not object-hot-path throughput
  (the VFS/kernel path caps it well below raw RoCE).
- Large-read kernel-VFS perf has a known-unstable path — see the RDMA
  `RDMA-SMOKE-TEST-RUNBOOK`; don't claim VFS perf from the cross-access gate.
