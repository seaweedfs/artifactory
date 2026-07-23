# RDMA Lab CI

This is the M01/M02 hardware gate for RDMA work in `seaweed-mono`.

GitHub Actions already runs the software RDMA gate with `siw`. That catches
build/link and basic verbs regressions. This runner is different: it uses the
real M01/M02 RoCE lab and should be used before opening or updating RDMA PRs
that make performance or hardware-behavior claims.

## Current Shape

- M01: client / runner host.
- M02: Seaweed master, filer, and RDMA-enabled Rust volume host.
- Buildkite provides the web UI and GitHub trigger.
- `run-mono-rdma-lab.sh` can also be run manually on M01.

The first supported profile is `unified`. It runs the mono
`sw-rdma-loader/tests/lab/unified-rdma-gate` scripts and checks the current
object/loader path:

- one `enterprise/seaweed-volume` server;
- object and VFS access to the same namespace;
- RC push, RC pull, and optional DC push rows;
- SHA/byte correctness;
- benchmark lines for the active rows.

The second supported profile is `lmcache-nixl`. It runs the mono
`enterprise/python/seaweedkv-lmcache/tests/lab/lmcache-connector-gate`
scripts and checks the SeaweedKV/LMCache connector path:

- `libseaweedfs_sw_rdma_nixl.so` built with `--features real-rdma`;
- canonical RDMA-enabled `enterprise/seaweed-volume` on M02;
- LMCache source staged on M01;
- `nixl==1.3.0` installed in the M01 LMCache venv if missing;
- 1 MiB and 20 MiB connector smokes through `loader-rdma`;
- real NIXL CPU runtime transfer from retained Seaweed CPU descriptor to
  caller-owned CPU DRAM descriptor, with SHA match.

## Install on M01

1. Install a Buildkite agent on M01.
2. Register it with queue tag `m01-rdma`.
3. Give it SSH access to M02 as the lab user.
4. Put any private GitHub token or SSH key setup in the agent environment, not
   in this repo.
5. Create a Buildkite pipeline that points at this repo and uses
   `.buildkite/rdma-lab.yml`.

The same runner works without Buildkite:

```bash
bash rdma-lab-ci/run-mono-rdma-lab.sh \
  --repo https://github.com/seaweedfs/seaweed-mono.git \
  --ref main \
  --profile unified
```

For the LMCache/NIXL CPU runtime gate:

```bash
bash rdma-lab-ci/run-mono-rdma-lab.sh \
  --repo https://github.com/seaweedfs/seaweed-mono.git \
  --ref main \
  --profile lmcache-nixl \
  --lmcache-repo https://github.com/LMCache/LMCache.git \
  --lmcache-ref dev
```

For a PR branch:

```bash
bash rdma-lab-ci/run-mono-rdma-lab.sh \
  --ref rdma/transfer-context-v2 \
  --profile unified
```

## Local Artifact Web UI

For a simple web view on M01, run `serve-artifacts.py` behind Basic Auth:

```bash
RDMA_CI_USER=rdma \
RDMA_CI_PASSWORD='change-me' \
RDMA_CI_ROOT=/opt/rdma-lab-ci/artifacts \
RDMA_CI_PORT=8091 \
python3 rdma-lab-ci/serve-artifacts.py
```

Open:

```text
http://M01:8091/
```

Each run directory contains `index.html`, `run.log`, `provenance.txt`, and
`summary.env`.

## Pass Criteria

The run must print:

- `UNIFIED_SINGLE_VOLUME_SERVER_OK`
- `UNIFIED_OBJECT_TO_VFS_SHA_MATCH`
- `UNIFIED_VFS_TO_OBJECT_SHA_MATCH`
- `UNIFIED_S3_LOADER_MATRIX_DONE`
- `UNIFIED_VFS_READ_MATRIX_DONE`
- `UNIFIED_RDMA_GATE_PASS`

For `lmcache-nixl`, the run must print:

- `SEAWEEDKV_LMCACHE_CONNECTOR_GATE_PASS` for both configured sizes;
- `SEAWEEDKV_LMCACHE_WORKLOAD_PASS` with nonzero `batch_saves` and
  `batch_loads` when `LMCACHE_WORKLOAD_SECONDS` is enabled;
- `UNIFIED_COMMIT_WITNESS_OK`;
- `LMCACHE_NIXL_RUNTIME_GATE_PASS`.

When `LMCACHE_REQUIRE_NIXL_RUNTIME=1`, the run must also print:

- `SEAWEEDKV_LMCACHE_NIXL_CPU_CALLER`;
- `SEAWEEDKV_LMCACHE_NIXL_RUNTIME_CPU`;
- `LMCACHE_CONNECTOR_NIXL_RUNTIME_SIZE_PASS`.

For the current object hot path, the PR evidence should also record:

- RC push throughput for 20MiB/c32 and 128MiB/c32;
- RC pull throughput for 20MiB/c32 and 128MiB/c32;
- DC push throughput when the `mlx5-dc` feature is enabled;
- SHA match for every claimed row;
- explicit fallback/unsupported status for rows that are not implemented.

Do not claim a backend row from this CI unless the row is printed by the gate
and the SHA check passes.

## Outputs

Each run writes to `rdma-lab-runs/<timestamp>-<ref>-<profile>/`:

- `provenance.txt`
- `run.log`
- `summary.env`

For `lmcache-nixl`, `summary.env` additionally records:

- `RDMA_CI_LMCACHE_ROWS`;
- `RDMA_CI_NIXL_RUNTIME_ROWS`.

Buildkite uploads that directory as an artifact. For manual runs, serve the
directory with any static file server if a simple web view is needed.

## Notes

This runner is intentionally thin. It does not reimplement the RDMA tests.
The canonical test logic lives in `seaweed-mono` next to the code it validates.
