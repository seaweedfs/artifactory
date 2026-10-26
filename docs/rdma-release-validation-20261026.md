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
