# RDMA release packaging validation (2026-10-26)

Source: `seaweedfs/seaweed-mono` main
`2485b898923d40300bfe42c4b9f84cdba03a5436` (clean `git archive`).
Workflow base: artifactory `544475d`.
Validation host: M02, Ubuntu 24.04.3, Linux 6.17, Rust/Cargo 1.93.0.
Build root: `/data/nvme/build/codex03/release-rdma-2485b89892`.

The two exact workflow cargo commands in the [runtime/build README](rdma-release-linux-amd64.md)
completed successfully, with locked dependencies and explicit x86_64 target.
The lab's existing `claude-s3lab/build.sh` lines 8 and 11 independently confirm
these feature sets: `rdma,rdma-dc,kvcache` and object-service `mlx5-dc`.
The workflow retains the default `5bytes` feature for the new volume variant.

| Binary | Bytes | SHA-256 | Help exit |
| --- | ---: | --- | ---: |
| `weed-volume` | 50,943,016 | `de1614b0dd7dce99c6e1d22dccd1a6f0a5a704eb0774c2a6dec176e05255f606` | 0 |
| `sw-rdma-object-service` | 3,795,880 | `c3352c869af47e53d0530c5fe44da0ad6a62cee2867a317ad9bc25165db64cf1` | 1 |

Both `ldd` checks exited 0 with no missing library. Both binaries link dynamically
to `libibverbs.so.1`, `librdmacm.so.1` and `libmlx5.so.1`, plus libc, libgcc_s,
libm and libnl. The volume additionally links OpenSSL 3 (libssl/libcrypto).
On this Ubuntu host, mlx5 is supplied by `ibverbs-providers`, rdmacm by
`librdmacm1t64`, and ibverbs by `libibverbs1`, all rdma-core 50.0-2ubuntu0.2.

`weed-volume --help` prints the normal CLI and KV slab options, exit 0.
`sw-rdma-object-service --help` prints its usage to stderr, exit 1: this is the
existing manual Config parser's help convention, not a loader failure.

The workflow's packaging commands were also executed successfully on M02.
Both archives contain exactly their binary, `source-commit.txt`, and `README.md`
(plus the tar directory entry); MD5 and SHA-256 sidecars were generated.
The archives copied to the evidence share were independently SHA-256 checked:

- `weed-volume-enterprise_rdma_large_disk_linux_amd64.tar.gz`: 18,071,733 bytes, SHA-256 `3bf0662ea23eb5a1629ed0e3438994d1d14d18b12d845a0d2527ee735f66ff6a`.
- `sw-rdma-object-service-enterprise_linux_amd64.tar.gz`: 1,622,492 bytes, SHA-256 `3df2b5f44670957e2c81c52f59aba6d9ae676f13945b960a431f2005a9892c09`.

Local `actionlint`, YAML parse, `git diff --check`, and the repository's lab-literal
self-test pass. The entire pre-existing release workflow is an unchanged byte
prefix, and its existing parsed jobs are unchanged (explicit UTF-8 decode).

Raw logs, toolchain/package inventory, help, ldd, file output, binaries inside the
archives, checksums, and the exact build/package/smoke scripts are retained at
`V:/share/rdma/rdma-release-build-20261026-codex03/`.
The initial PowerShell text-pipe wrapper appended a stray CR after the successful
builds; the LF byte-stream rerun of both exact commands exited 0. No source fix
was needed. Final logs record that successful rerun.

This proves local build, packaging, linkage and CLI startup only. No devices were
opened, servers started, hardware transfer tested, workflow dispatched, or release
published. The Draft PR uses a `[skip ci]` commit so push/PR workflows do not run.

## Review amendment: protobuf and exact-checkout version admission

The new job now explicitly installs `protobuf-compiler` for dependency build
scripts. A separate check of the actual cloned source runs before the new job's
builds. Numeric tags must match that checkout's MAJOR/MINOR; patch tags use the
same MAJOR/MINOR rule as the existing action, and `latest` retains its skip.
No existing job or shared verification action was changed.

Controls: matching 4.51/v4.51.2 pass; after a simulated main bump to 4.52 the
4.51 tag refuses; the new matching 4.52 tag passes. Missing/malformed constants
refuse. The mismatch-check-removal mutant is RED; production controls are GREEN.
The real pinned source's constants also pass 4.51 and refuse 4.52.

A clean Ubuntu 24.04 container was built on M02 without RDMA devices passed in.
It first proved `protoc` absent on PATH (`INITIAL_PROTOC_ABSENT`), installed the
workflow's declared dependency list plus CA certificates, and observed
`libprotoc 3.21.12`. Both exact workflow cargo commands then passed from fresh
source and empty target directories: volume 2m 46s, object service 31.46s.
Rust 1.93.0 was mounted read-only; only the downloaded Cargo registry cache was
reused, not compiled artifacts. The driver exited 0 with
`CLEAN_CONTAINER_BUILD_PASS`. Image: `ubuntu@sha256:1e0a86e57d247923571b75e0aaf48a1449cf8c543d51fb3e07a4a7d7bfa79316`.

Both clean-container outputs also pass packaging, ldd with no missing libraries
(including ibverbs/rdmacm/mlx5), and the same help expectations (volume 0,
object-service 1). These are separate build outputs, not a bit-reproducibility
claim:

| Clean-container binary | Bytes | SHA-256 |
| --- | ---: | --- |
| `weed-volume` | 50,917,280 | `d1b8cb8f2229b7b903dee7fb2e70637cdbca5743eb88b1182ddf4b9790b11c88` |
| `sw-rdma-object-service` | 3,788,200 | `c62142aec9fab421c915ee998ceffd5cc2b98f9691de5687b15c7c988594b22a` |

The README now names `weed-enterprise-linux_amd64` and
`weed-enterprise-linux_amd64_large_disk`, requires compatible source/release,
and links the pinned `docs/s3-over-rdma.md` setup reference.

New raw logs, exact container/package/smoke scripts, red/green controls, archive
copies and checksums are in the original evidence directory's `amendment-1/`.
The original evidence and manifest remain intact. No workflow dispatch,
publication, merge or hardware transfer was performed.
