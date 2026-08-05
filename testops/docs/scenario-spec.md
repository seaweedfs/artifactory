# sw-test-runner Scenario Spec — v0 (current)

Describes the YAML format the runner accepts **today**. Every field
listed here is implemented in `types.go` / `parser.go` / `engine.go`
and exercised by scenarios under `scenarios/`.

For proposed extensions that are **not** yet implemented, see
[`scenario-spec-v1-roadmap.md`](scenario-spec-v1-roadmap.md). v1 is
strictly additive: every v0 scenario keeps working unchanged.

## Scope

A scenario is a YAML file describing a sequence of **phases**. Each
phase contains an ordered list of **actions** drawn from a shared
**registry** populated by the framework's `actions.RegisterCore` plus
zero or more **product packs** (`packs/v3block`, `packs/v1weed`, ...).

The runner is invoked by a thin product wrapper (~25 lines, see
`cmd/swblock/main.go` for an example) which composes the registry and
calls `cli.Run(register, args)`.

## Top-level Scenario shape

```yaml
name: <scenario-name>          # required, kebab-case
timeout: 5m                    # optional, default unbounded; Go duration

env:                           # flat string→string map; expanded as {{.env.K}}
  repo_dir: "/path/to/repo"

cluster:                       # optional, declares cluster requirements
  require:
    servers: 1
    block_capable: 1
  fallback: managed            # managed (default) | fail | skip
  cleanup: auto                # auto (default) | keep | destroy
  managed:
    master_port: 9333
    volumes:
      - { port: 8080, block_listen: ":3350", extra_args: "" }
    node: target_node
    ip:   192.168.1.184

topology:
  agents:                      # optional, coordinator-mode agent map
    a01: 192.168.1.181:9990
  nodes:
    target_node:
      host: 192.168.1.184
      user: testdev
      key:  /c/work/dev_server/testdev_key
      alt_ips: [10.0.0.3]      # optional, e.g. RDMA NIC
      is_local: false
      agent: a01               # optional, for coordinator mode

targets:                       # optional, named iSCSI/NVMe targets
  primary:
    node: target_node
    vol_size: 100M
    wal_size: 16M
    iscsi_port: 3260
    admin_port: 8080
    replica_data_port: 0
    replica_ctrl_port: 0
    rebuild_port: 0
    iqn_suffix: epoch-primary
    tpg_id: 1
    nvme_port: 0
    nqn_suffix: ""
    max_concurrent_writes: 0
    nvme_io_queues: 0

phases:
  - name: setup
    actions: [...]

artifacts:
  on_failure: ["**/*.log", "**/result.json"]
  dir: ""                      # optional override; defaults to run-bundle dir
```

`Scenario.Targets[name].IQN()` and `.NQN()` are framework-computed
identifiers; tests reference targets by **name** (`target: primary`),
never by raw IQN.

## Phase

```yaml
- name: <phase-name>
  always: false                # if true, runs even on prior failure (use for teardown)
  parallel: false              # if true, actions in this phase run concurrently
  repeat: 1                    # >1 ⇒ run actions N times for benchmark aggregation
  aggregate: median            # median (default for repeat>1) | mean | none
  trim_pct: 20                 # outlier trim percentage per end (default 20)
  actions: [...]               # ordered list of action calls
  include: ../shared/foo.yaml  # alternative: pull phases from another file
  include_params:              # string→string overrides for the included file
    foo: bar
```

Notes on existing semantics:

- **`repeat`**: aggregates a benchmark phase (median/mean across N
  runs). It is **not** "run this scenario body N times with
  independent state" — every iteration shares scenario state, and only
  metric-emitting actions are aggregated.
- **`include`** is the v0 template mechanism: the phase is replaced by
  the phases defined in the included file; `include_params` are
  flat string substitutions, no type/required/default schema.
- **`always: true`** is the v0 cleanup mechanism: a teardown phase
  marked `always: true` runs even when an earlier phase failed.

## Action

```yaml
- action: <registered-name>    # required; must exist in the registry
  target: primary              # optional, target name from scenario.targets
  replica: r1                  # optional, for multi-replica scenarios
  node: target_node            # optional, topology node for remote exec
  save_as: my_var              # optional, captures action's primary output to a flat var
  ignore_error: false          # if true, action failure does not fail the phase
  retry: 0                     # number of retries on failure
  timeout: 30s                 # per-action timeout, Go duration string
  # any remaining keys are passed inline as Action.Params (string→string)
  master: "192.168.1.184:9333"
  device: "/dev/sda"
```

Variable expansion uses Go `text/template` with a single flat
namespace `{{.params.X}}`:

- `{{.params.foo}}` reads the saved variable named `foo`
- `{{.env.repo_dir}}` reads top-level env
- `save_as: foo` followed downstream by `{{.params.foo}}`

There is **no** `{{.phases.X.outputs.Y}}` form in v0. Each action
contributes one named value via `save_as`; consumers reference it by
that name.

## Tier system

Every action is registered against a tier:

| Tier | Purpose |
|---|---|
| `TierCore` | product-agnostic primitives (`exec`, `assert_*`, `grep_log`, `sleep`, `print`, `pre_run_cleanup`, `collect_path`, `collect_results`, `fio_parse`) |
| `TierBlock` | block-storage actions (`iscsi_*`, `mkfs`, `mount`, `dd_*`, `fio*`, `start_target`, `nvme_*`, `snapshot_*`, `pgbench_*`, V2 lifecycle) |
| `TierK8s` | `kubectl_*` family |
| `TierDevOps` | V1/V2 weed-cluster wiring (`start_weed_master`, `cluster_status`, etc.) |
| `TierChaos` | fault injection (`inject_netem`, `corrupt_wal`, `fill_disk`, ...) |

`sw-test-runner list` prints the registry by tier.

## Action inventory snapshot

(Run `sw-test-runner list` for the canonical, current set.)

**TierCore** — `assert_contains` `assert_equal` `assert_greater`
`assert_status` `bench_compare` `bench_stats` `benchmark_postcheck`
`benchmark_preflight` `benchmark_report` `collect_path` `collect_results` `exec`
`fio_parse` `grep_log` `pre_run_cleanup` `print` `sleep`
`validate_replication`

**TierBlock** — `iscsi_login` `iscsi_login_direct` `iscsi_logout`
`iscsi_discover` `iscsi_cleanup` `iscsi_rescan` `mkfs` `mount` `umount`
`dd_write` `dd_read_md5` `fio` `fio_json` `fio_verify` `fsck_ext4`
`fsck_xfs` `get_block_size` `iostat_capture` `vmstat_capture`
`pprof_capture` `start_target` `stop_target` `kill_target`
`stop_all_targets` `stop_bg` `kill_stale` `assign` `set_replica`
`status` `wait_role` `wait_lsn` `poll_shipper_state` `resize`
`build_deploy` `collect_artifacts` `scrape_metrics` `assert_metric_eq`
`assert_metric_gt` `assert_metric_lt` `perf_summary` `measure_rebuild`
`measure_recovery` `start_rebuild_client` `start_rebuild_server`
`validate_recovery_regression` `write_loop_bg` `nvme_connect`
`nvme_connect_direct` `nvme_disconnect` `nvme_disconnect_all`
`nvme_get_device` `nvme_cleanup` `snapshot_create` `snapshot_delete`
`snapshot_list` `snapshot_export_s3` `snapshot_import_s3`
`pgbench_init` `pgbench_run` `pgbench_cleanup` `sqlite_create_db`
`sqlite_insert_rows` `sqlite_count_rows` `sqlite_integrity_check`

**TierK8s** — `kubectl_apply` `kubectl_delete` `kubectl_delete_pod`
`kubectl_logs` `kubectl_exec` `kubectl_get_field` `kubectl_get_condition`
`kubectl_wait_condition` `kubectl_assert_exists`
`kubectl_assert_not_exists` `kubectl_rollout_status`
`kubectl_pod_ready_count` `kubectl_set_image` `kubectl_label`

**TierDevOps** — `start_weed_master` `start_weed_volume` `stop_weed`
`build_deploy_weed` `cluster_status` `wait_cluster_ready`
`wait_volume_healthy` `create_block_volume` `delete_block_volume`
`expand_block_volume` `lookup_block_volume` `discover_primary`
`block_status` `block_promote` `assert_block_field`
`wait_block_primary` `wait_block_servers` `collect_debug` `collect_glog`

**TierChaos** — `inject_netem` `inject_partition` `clear_fault`
`corrupt_wal` `fill_disk`

Pack-supplied actions follow product prefixes — `v3_*` (packs/v3block),
`v1_*` (packs/v1weed). New product actions **must** carry a product
prefix to avoid registry collisions.

## Run bundle

Each `sw-test-runner run` produces a directory under the configured
results root, named `YYYYMMDD-HHMMSS-<short-id>/` containing:

```
<run-dir>/
├── manifest.json        # RunID, scenario name+sha256, git_sha, host, command_line, status
├── scenario.yaml        # frozen copy of the input
├── result.json          # phase-by-phase status, durations, action results
├── result.xml           # JUnit XML
└── artifacts/           # files explicitly published by collect_* actions
```

`manifest.json` fields:

| field | always | description |
|---|---|---|
| `run_id` | yes | timestamp + 4-hex short hash |
| `started_at` / `finished_at` | yes | RFC3339 UTC |
| `scenario_name` | yes | `Scenario.Name` |
| `scenario_file` | yes | input path |
| `scenario_sha256` | yes | hash of frozen YAML bytes |
| `runner_version` | best-effort | framework build identity |
| `git_sha` | best-effort | git rev-parse HEAD of cwd |
| `host` | best-effort | os.Hostname |
| `status` | yes | `pass` / `fail` / `error` |
| `command_line` | yes | argv joined |

## Failure semantics

- An action returns an error → the phase fails.
- A failed phase aborts the scenario unless the next phase has
  `always: true`, which always runs.
- `ignore_error: true` on an action turns the action's error into a
  warning; the phase still proceeds.
- `retry: N` wraps the action with up to N additional attempts on
  error, no backoff configured (engine retries immediately).
- A timeout is treated as a failure.

## Parallel phase semantics

- `parallel: true` runs the phase's actions concurrently.
- `save_as` writes to a single shared variable namespace; **the last
  writer wins** and the order is non-deterministic. Don't rely on
  `save_as` from a parallel phase being read deterministically by a
  later phase. Use parallel for fan-out side effects (start three
  background loads), serial for sequence-dependent state.

## Remote vs local boundary

- An action runs on the controller's host by default.
- An action with `node: <name>` runs on that node via
  `infra.Node.Run` (SSH for remote, local exec for local).
- A handful of actions (`kubectl_*`, `iscsi_*`) accept `node:` to pick
  where the command actually executes; check the action's docstring
  for which side it runs on.
- The registry does **not** automatically dispatch product-pack
  actions to product binaries — pack handlers explicitly choose
  whether to shell out, hit an HTTP endpoint, or call a gRPC client.

## Validation

`sw-test-runner validate <scenario.yaml>` parses the file and checks:

- YAML decodes
- every `action:` resolves in the current registry (after pack
  registration)
- every `include:` resolves
- `cluster:` requirements parse

It does **not** check param keys against per-action expected schemas
(actions do their own param validation at run time).

## Example: existing v0 scenario

```yaml
name: consistency-epoch
timeout: 5m
env:
  repo_dir: "/path/to/repo"

topology:
  nodes:
    target_node: { host: 192.168.1.184, user: testdev, key: /key }

targets:
  primary:
    node: target_node
    vol_size: 100M
    iscsi_port: 3260
    admin_port: 8080
    iqn_suffix: epoch-primary

phases:
  - name: setup
    actions:
      - action: build_deploy
      - action: start_target
        target: primary
        create: "true"

  - name: epoch_monotonicity
    actions:
      - action: assign
        target: primary
        epoch: "1"
        role: primary
        lease_ttl: 30s
      - action: assert_status
        target: primary
        epoch: "1"
        role: primary

  - name: teardown
    always: true
    actions:
      - action: stop_all_targets
      - action: collect_artifacts
        on_failure: "true"
```

## Conventions

- **Product action prefix**: pack-registered actions use `<product>_*`
  (`v1_*`, `v2_*`, `v3_*`). Core actions are unprefixed.
- **Product packs are allowed**, but pack handlers must not import
  product internal packages. Talk to the product through shell, HTTP,
  or gRPC — same boundary the framework would use as a third party.
- **One scenario, one run-bundle.** Don't fan out into multiple
  scenarios via shell; use `include` if you need composition.
- **Cleanup goes in an `always: true` teardown phase**, not a separate
  top-level field.
- **Asserts are phases, not cleanup steps.** "No active iSCSI
  sessions" failing must fail the run; teardown must succeed
  best-effort even on a failed run.

## Compatibility commitment

v0 is the stable wire format. A v1-roadmap addition does not change
v0 semantics; a v0 scenario will continue to parse and run unchanged
through every v1.x runner version. Field additions are opt-in via new
top-level keys (e.g. `apiVersion`); behavior changes that would break
v0 scenarios are deferred to v2 and require a parallel-support cycle
before v0 retires.
