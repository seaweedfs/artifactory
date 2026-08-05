# TestOps

> **Status:** incubator, now hosted in `seaweedfs/artifactory/testops`.
> It is stable enough to drive RDMA, S3, VFS, and block validation gates,
> while the suite catalog and controller workflow continue to evolve.

A YAML-driven scenario runner for storage-product validation: deploy
binaries to remote nodes, sequence multi-host actions, run benchmarks,
inject faults, collect artifacts, emit JUnit XML.

## What it is for

- end-to-end smoke tests for block-storage products (V2 weed-block, V3 seaweed-block)
- S3 gateway and RDMA lab gates for object/VFS data paths
- iSCSI / NVMe-oF target integration scenarios
- multi-node failover and rebuild scenarios
- chaos / fault injection (`netem`, `iptables`, disk-fill, kill-loop)
- fio / dd performance comparisons across binaries
- regression baselines + Prometheus metric scrapes

It is **not** a generic CI runner; it is a scenario engine that knows about
storage daemons and the protocols they expose.

## Architecture in one paragraph

A **registry** of named **actions** (`exec`, `iscsi_login_direct`, `dd_write`,
`v3_start_blockmaster`, ...) is composed by **packs** (`packs/block` for V2,
`packs/v3block` for V3, ...) on top of a product-agnostic **framework**
(parser, engine, reporter, coordinator) that talks to remote nodes through
an abstract `infra.Node` SSH/local exec interface. Scenarios are YAML files
that sequence actions across phases. Each run produces an artifact bundle
(`result.json`, `result.xml`, per-daemon logs, metrics, optional HTML).

## Quick install

```bash
go install github.com/seaweedfs/artifactory/testops/cmd/sw-test-runner@latest

sw-test-runner list                        # show registered actions by tier
sw-test-runner validate scenario.yaml      # parse + lint
sw-test-runner run scenario.yaml           # execute
sw-test-runner validate-bundle results/RUN # offline result/status check
```

## Docs and wiki

Full documentation is a MkDocs wiki under `docs/` (mirrors the seaweed-block
wiki). Serve it locally:

```bash
pip install -r requirements-docs.txt
mkdocs serve            # http://127.0.0.1:8000   (or: make wiki)
```

- **Start here:** `docs/testops-handbook.md`, `docs/cross-product-testops-standard.md`, `docs/scenario-spec.md`, `docs/tutorial.md`
- **For development agents:** `docs/agent-testops-runbook.md` — the short
  guide for Block, S3, VFS, and RDMA agents running shared lab tests and
  reporting evidence.
- **Reference:** `docs/wiki/` — code map, packs & binaries, scenario catalog, product testing guides, the live dashboard
- **Live run dashboard/controller:** http://192.168.1.181:9099/ (global run view; M01 can enable RDMA queue submit)

## Agent quick start

For RDMA PR/lab validation, submit to the M01 queue so the lab lock, shared
results, and dashboard metadata are handled consistently:

```bash
ssh testdev@192.168.1.181
cd /opt/rdma-lab-ci/sw-test-runner
TESTOPS_MONO_REF=<branch-or-sha> ./scripts/testops-ci-submit.sh
```

For Block/S3/VFS scenarios, run the product binary with shared results and
metadata:

```bash
swblock run <scenario.yaml> -results-dir /mnt/smb/work/share/testops/results/block-qa \
  -meta project=block-qa -meta team=block -meta run_by=<agent> \
  -meta test_id=<test-id> -meta branch=<branch-or-sha> -meta commit=<sha>
```

Watch runs at http://192.168.1.181:9099/. Report the `project/run_id`, bundle
path, branch/commit, pass/fail, and key metrics.

If the dashboard is started with controller flags, the same page can submit the
RDMA queue:

```bash
curl -X POST http://m01:9099/api/rdma/submit \
  -H 'Content-Type: application/json' \
  -d '{"mono_ref":"main","run_by":"dev-agent"}'
```

## Build

```bash
make build               # all cmd/ binaries into ./bin
make test                # unit tests
make wiki                # serve the wiki locally
```

## Repository layout

```
.
├── README.md
├── LICENSE                       Apache-2.0
├── go.mod                        github.com/seaweedfs/artifactory/testops
├── engine.go, parser.go, ...     framework: scenario sequencer + YAML parser
├── registry.go, types.go         action registry + ActionContext
├── reporter.go, baseline.go      JUnit XML, regression baselines
├── coordinator.go, agent.go      multi-node distributed mode
├── actions/                      product-agnostic actions
│   ├── system.go                 exec, sleep, assert_*, ssh, scp
│   ├── fault.go                  netem, partition, kill_pid, fill_disk
│   ├── bench.go, benchmark.go    fio loops, perf parsing
│   ├── iscsi.go, io.go, k8s.go   iSCSI client, dd/mkfs/mount, kubectl
│   ├── recovery.go, results.go   verify, sha256, JSON output
│   └── ...
├── infra/                        agnostic node abstractions
│   ├── node.go                   SSH or local exec
│   ├── target.go, ha_target.go   daemon lifecycle helpers
│   └── iscsi_client.go, fault.go
├── packs/                        product-specific action sets
│   ├── block/                    V2 seaweedfs weed-block
│   ├── kv/                       V2 seaweedfs kv/object
│   ├── s3/                       SeaweedFS S3 gateway
│   ├── rdma/                     M01/M02 RDMA lab gates
│   └── v3block/                  V3 seaweed-block
├── scenarios/                    bundled scenario YAMLs (V2 baselines + V3)
├── cmd/sw-test-runner/           CLI entry
├── cmd/testops-controller/       safe RDMA queue submit/status web API
└── internal/blockapi/            internal HTTP client (V2 master REST)
```

## Writing a scenario

Minimum scenario:

```yaml
name: my-scenario
timeout: 2m
topology:
  nodes:
    target:
      host: 127.0.0.1
      is_local: true
phases:
  - name: hello
    actions:
      - action: exec
        node: target
        cmd: echo hello world
```

Validate, then run:

```bash
sw-test-runner validate my-scenario.yaml
sw-test-runner run my-scenario.yaml
```

Output:

```
results/
└── 20260504-100000-abcd/
    ├── manifest.json     run metadata
    ├── result.json       structured per-phase results
    ├── result.xml        JUnit XML
    ├── result.html       human-readable
    ├── scenario.yaml     echo of input
    └── artifacts/        per-daemon logs, fio JSON, etc.
```

Validate a completed bundle without touching the lab:

```bash
sw-test-runner validate-bundle \
  --profile protocol-release-gate \
  --expect-commit a0175f8 \
  results/protocol-release-gate-run
```

Named profiles encode product-owned suite contracts. For Seaweed Block beta
hardening:

```bash
swblock validate-bundle \
  --profile beta-hardening \
  --expect-commit de165f0 \
  results/beta-hardening-gate-run
```

## Adding a product pack

1. New directory `packs/yourproduct/`.
2. `RegisterPack(r *Registry)` calls `r.RegisterFunc("yourproduct_action", TierDevOps, handler)`.
3. Action handler signature:

   ```go
   func(ctx context.Context, actx *ActionContext, act Action) (map[string]string, error)
   ```

4. Wire it in `cmd/sw-test-runner/main.go::registerAll` (or build a tiny custom main that registers `actions.RegisterCore(r)` + your pack only).

See `packs/v3block/` for a worked example (~600 LOC, 7 actions: spawn three
V3 daemon types, apply cluster spec, wait for primary, parse status, assert
no-V2-authority-leak in logs).

For cross-product storage gates, start with
`docs/storage-testops-platform.md`. It maps the current block, S3, VFS, and
RDMA surfaces and points at the RDMA unified lab scenario.

## Tiers (action categories)

```
core       agnostic    exec, sleep, assert_*, print, grep_log
block      V2 storage  start_target, iscsi_*, dd_*, fio_*, assign, metrics
devops     daemons     start_weed_master, start_weed_volume, v3_start_*
chaos      faults      inject_netem, inject_partition, fill_disk, kill_loop
k8s        K8s         kubectl_apply, kubectl_wait_condition, kubectl_logs
```

A pack registers actions under whichever tier fits.

## Discipline

- Actions must not embed product authority semantics (no `promote`,
  `demote`, `force-failover` shortcuts that bypass the product control plane).
- Actions communicate via shell-out and HTTP/gRPC clients only — no direct
  imports of product daemon internal types. This is what keeps the runner
  pluggable across V2/V3 and cross-language products.
- Test the testrunner on its own (`go test ./... -count=1`); product
  validation is what scenarios run on real labs.

## Status

This is currently an incubator under a personal account. Module path may
relocate to a project org repo once the surface stabilizes. Pin to a commit
hash if you need stability across the move.

## License

Apache-2.0. See `LICENSE`.
