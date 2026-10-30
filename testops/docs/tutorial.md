# sw-test-runner Tutorial

A practical onboarding guide for dev/QA bringing `sw-test-runner` to a
new product (e.g. RDMA validation). It covers the current feature set,
how to run scenarios locally and against m01/m02 over SSH, and how to
extend the runner with new actions or scenarios.

For the formal YAML spec, see [`scenario-spec.md`](scenario-spec.md).
For the open-core/private split and milestones, see
[`open-core-testops-roadmap.md`](open-core-testops-roadmap.md).

---

## 1. What it is

`sw-test-runner` is a YAML-driven test execution layer:

- write a scenario (YAML) describing phases + actions
- the runner sequences phases, dispatches actions to local or remote nodes,
  collects structured evidence, and writes an immutable bundle
- a bundle validator checks the bundle independently after the run

Two-layer design:

```text
scenario YAML                  ← what to prove (product team owns)
   |
   v
runner engine + action set     ← how to prove it (platform owns)
   |
   v
run bundle (status + result + artifacts + provenance)
   |
   v
validate-bundle                ← independent post-run trust check
```

The runner is **not** a generic CI tool — it's a scenario engine with
storage/networking domain actions baked in (iSCSI, NVMe, K8s, fault
injection, fio bench, exec, asserts, etc.). For RDMA you'd add an RDMA
action set the same way `actions/iscsi.go` and `actions/nvme.go` exist.

---

## 2. Concepts

| Concept | Definition | Where it lives in code |
|---|---|---|
| **Scenario** | A YAML file: name + topology + phases + actions | `scenarios/*.yaml` (yours), `examples/*` |
| **Phase** | An ordered group of actions; supports `parallel: true`, `repeat: N`, `always: true` | `engine.go runPhase` |
| **Action** | One executable step. Has typed params, returns map[string]string | `actions/*.go` |
| **Registry** | The set of actions known at runtime | `registry.go` |
| **Pack** | A bundle of product-specific actions a wrapper registers | `packs/*/register.go` |
| **Wrapper** | Thin product binary (`cmd/<product>/main.go`, ~25 lines) | `cmd/swblock/main.go` is the model |
| **Run Bundle** | Per-run dir under `--results-dir/<run_id>/` | `runbundle.go` |
| **Status** | Mutable `status.json` updated at phase boundaries | `runstatus.go` |
| **Suite** | A YAML chaining N scenarios into one run | `suite.go` |
| **Profile** | Named bundle-validator preset (e.g. `protocol-release-gate`) | `validate_bundle.go` |
| **Topology** | Map of named nodes (host + user + key + is_local) | `types.go Topology` |

The tier system (`TierCore`, `TierBlock`, `TierDevOps`, `TierChaos`,
`TierK8s`) lets `--tiers` gate which actions are even executable for a
run — useful when you want to refuse mutating actions in CI but allow
them in lab.

---

## 3. Build and install

The runner ships as a Go binary. Each product has a thin wrapper
under `cmd/`:

| Binary | Pack | Use case |
|---|---|---|
| `swblock` | V3 seaweed-block | iSCSI / NVMe / CSI / K8s actions |
| `weedblock` | V2 weed-block + KV | V2-shaped block actions |
| `weedv1` | V1 stub | placeholder |
| `sw-test-runner` | kitchen-sink | every pack registered |

For an RDMA project, add `cmd/rdma/main.go` that registers the actions
you need (see §8).

### Build

```bash
git clone https://github.com/seaweedfs/artifactory/testops
cd sw-test-runner
go build -o swblock ./cmd/swblock          # V3 product variant
# or
go build -o myproduct ./cmd/<your-pack>    # your wrapper
```

For Windows operators, point at the resulting `.exe`:

```powershell
go build -o C:\work\swblock.exe .\cmd\swblock
```

---

## 4. Your first scenario (local mode)

A minimal scenario that runs locally with no SSH or hardware:

```yaml
# hello.yaml
name: hello
timeout: 30s

topology:
  nodes:
    local:
      host: "127.0.0.1"
      is_local: true

phases:
  - name: hello
    actions:
      - action: exec
        node: local
        cmd: "echo 'hi from $(hostname)'"
        save_as: greeting
      - action: assert_contains
        actual: "{{ greeting }}"
        substring: "hi from"
```

Validate + run:

```bash
swblock validate hello.yaml
# VALID: hello (1 phases, 0 targets)

swblock run hello.yaml
# run bundle: results/20260510-180000-abcd
# [phase] hello
#   [action] exec @local
#   [var] greeting = hi from myhost
#   [action] assert_contains
# === hello === PASS (12ms)
```

The run bundle written under `results/<run_id>/` has:

```
results/20260510-180000-abcd/
├── manifest.json     ← immutable identity (run_id, scenario sha256, runner version, git sha)
├── status.json       ← mutable run-control state (queued/running/pass/fail/cancelled)
├── result.json       ← structured terminal result + per-phase + per-action
├── result.xml        ← JUnit XML for CI
├── result.html       ← human-readable
├── provenance.json   ← image/binary digests + git dirty flag
├── scenario.yaml     ← frozen copy of the scenario as run
└── artifacts/        ← collected via collect_path or on_failure
```

A sibling `latest` pointer at the parent results-dir contains the
most-recent run_id.

---

## 5. SSH execution against m01 / m02

For lab work, declare the remote node and the rest is identical.
SSH runner uses key-based auth.

```yaml
# rdma-loopback-smoke.yaml
name: rdma-loopback-smoke
timeout: 5m

env:
  ssh_key: "C:/work/dev_server/testdev_key"
  product_root: "/tmp/rdma-runtime"

topology:
  nodes:
    m01:
      host: 192.168.1.181
      user: testdev
      key: "{{ ssh_key }}"
    m02:
      host: 192.168.1.184
      user: testdev
      key: "{{ ssh_key }}"

phases:
  - name: pre_clean
    actions:
      - action: exec
        node: m01
        cmd: "pkill -KILL -f rdma-bench || true"
      - action: exec
        node: m02
        cmd: "pkill -KILL -f rdma-target || true"

  - name: bringup
    actions:
      - action: exec
        node: m02
        cmd: "cd {{ product_root }} && ./bin/rdma-target --listen 0.0.0.0:50051 &"
        env.RDMA_LOG_LEVEL: "info"
      - action: exec
        node: m01
        cmd: "cd {{ product_root }} && ./bin/rdma-bench --target 192.168.1.184:50051 --duration 30s"
        timeout: 1m
        save_as: bench_output

  - name: assert
    actions:
      - action: assert_contains
        actual: "{{ bench_output }}"
        substring: "PASS"

  - name: teardown
    always: true
    actions:
      - action: exec
        node: m02
        cmd: "pkill -TERM -f rdma-target || true"
      - action: collect_path
        node: m02
        path: "/tmp/rdma-runtime/logs"
        name: "rdma-server-logs"
        ignore_error: true
```

Run from a Windows controller (or from m02 directly):

```powershell
swblock run rdma-loopback-smoke.yaml -results-dir V:/share/lab-runs/rdma
```

Important details:

- **`is_local: true`** on a node makes `exec @<that-node>` run via
  `os/exec` with no SSH. Default is SSH.
- **`env.KEY: VALUE`** params on `exec` actions are translated to
  `KEY=VALUE export ... ; <cmd>` so they survive `&&` chains. Use this
  instead of inlining env in cmd strings.
- **`always: true`** phases run even if a normal phase failed. Use for
  cleanup + artifact collection.
- **`collect_path`** tars a remote dir and pulls it into the local run
  bundle's `artifacts/` subdir. Always use `ignore_error: true` on
  cleanup-side `collect_path` so a missing file doesn't mask the
  upstream failure.
- **`{{ var }}` templating** works on env values, action params, cmd
  strings, and `save_as` references.

---

## 6. Run-control CLI

For long runs, status is checkable from a separate shell:

```bash
swblock status                                # latest run, default results dir
swblock status -results-dir V:/share/runs    # latest under a specific root
swblock status -results-dir V:/share/runs <run_id>
swblock status -json [...]                    # machine-readable
```

Sample output:

```
run_id:        20260510-180000-abcd
scenario:      rdma-loopback-smoke
state:         running
phases:        2/4
current_phase: bringup
started_at:    2026-05-10T18:00:00Z
artifact_dir:  V:\share\runs\20260510-180000-abcd
phase_results:
  [pass]     pre_clean
  [running]  bringup
```

Other run-control commands:

```bash
swblock list-runs -results-dir V:/share/runs   # one line per run, * marks latest
swblock cancel    -results-dir V:/share/runs   # writes control/cancel; engine stops at next phase boundary
```

`status` exit codes: `0` = pass, `1` = running/fail/cancelled/error,
`2` = missing/parse error.

Cancel preserves always-phases — your cleanup still runs.

---

## 7. Suite (multi-scenario chain)

Compose multiple scenarios into one orchestrated run:

```yaml
# rdma-release-gate.yaml
name: rdma-release-gate
mode: chain

scenarios:
  - id: rdma-loopback-smoke
    path: ../scenarios/rdma-loopback-smoke.yaml
  - id: rdma-cross-host
    path: ../scenarios/rdma-cross-host.yaml
  - id: rdma-fault-link-flap
    path: ../scenarios/rdma-fault-link-flap.yaml
```

Run:

```bash
swblock suite -results-dir V:/share/runs \
  --env ssh_key=C:/work/dev_server/testdev_key \
  --env product_root=/tmp/rdma-runtime \
  rdma-release-gate.yaml
```

Suite emits a top-level `result.json` + `status.json` + `suite.log`,
and stores each child under `<run_id>/<child-id>/runs/<child-run>/`.
The suite stops on first child fail; child `always` phases still run
inside their own scope.

Validate the suite bundle:

```bash
swblock validate-bundle \
  --require-pass --require-timing --require-child-bundles \
  --expect-scenario rdma-release-gate \
  --expect-commit a1b2c3d \
  --children rdma-loopback-smoke,rdma-cross-host,rdma-fault-link-flap \
  V:/share/runs/<suite-run-id>
```

Or use a named profile (after you register one in
`validate_bundle.go`):

```bash
swblock validate-bundle --profile rdma-release-gate <suite-run-id>
```

Profiles bundle the common set of `--require-*` + `--children` flags
so operators can't forget one.

---

## 8. Add a new action (extension)

Actions are Go functions of one signature:

```go
func myAction(ctx context.Context, actx *tr.ActionContext, act tr.Action) (map[string]string, error)
```

- `act.Params` is the inline-merged map of YAML keys other than the
  named fields (`action`, `node`, `target`, `replica`, `save_as`,
  `ignore_error`, `retry`, `timeout`).
- Return value is merged into `actx.Vars` under `act.SaveAs` if set;
  any string keys returned are also accessible as `{{ key }}` in
  downstream actions when `save_as` is present.
- Return non-nil error to fail the action; the engine reports the
  error in result.json + status.json.

### 8.1. Skeleton

Create `actions/rdma.go`:

```go
package actions

import (
	"context"
	"fmt"
	"strings"

	tr "github.com/seaweedfs/artifactory/testops"
)

// RegisterRDMAActions registers RDMA client/server actions.
func RegisterRDMAActions(r *tr.Registry) {
	r.RegisterFunc("rdma_loopback", tr.TierBlock, rdmaLoopback)
	r.RegisterFunc("rdma_assert_qp_state", tr.TierBlock, rdmaAssertQPState)
	r.RegisterFunc("rdma_get_perf_counters", tr.TierBlock, rdmaGetPerfCounters)
}

// rdmaLoopback executes an RDMA SEND loop locally for `count` iterations.
//
// Params:
//
//	dev:    required, RDMA device (e.g. "mlx5_0")
//	port:   required, port number
//	count:  optional, iteration count (default 1000)
//	node:   optional, defaults to local
//
// Returns: value = "OK" on success
func rdmaLoopback(ctx context.Context, actx *tr.ActionContext, act tr.Action) (map[string]string, error) {
	dev := act.Params["dev"]
	if dev == "" {
		return nil, fmt.Errorf("rdma_loopback: dev param required")
	}
	port := act.Params["port"]
	if port == "" {
		return nil, fmt.Errorf("rdma_loopback: port param required")
	}
	count := act.Params["count"]
	if count == "" {
		count = "1000"
	}

	node, err := GetNode(actx, act.Node)
	if err != nil {
		return nil, fmt.Errorf("rdma_loopback: %w", err)
	}

	cmd := fmt.Sprintf("ib_send_lat -d %s -p %s -n %s", dev, port, count)
	stdout, stderr, code, err := node.Run(ctx, cmd)
	if err != nil || code != 0 {
		return nil, fmt.Errorf("rdma_loopback: code=%d stderr=%s err=%v", code, stderr, err)
	}
	if !strings.Contains(stdout, "average") {
		return nil, fmt.Errorf("rdma_loopback: unexpected output: %s", stdout)
	}
	return map[string]string{"value": "OK"}, nil
}
```

### 8.2. Wire into a wrapper

Create `cmd/rdma/main.go`:

```go
package main

import (
	"os"

	tr "github.com/seaweedfs/artifactory/testops"
	"github.com/seaweedfs/artifactory/testops/actions"
	"github.com/seaweedfs/artifactory/testops/cli"
)

func main() {
	register := func(r *tr.Registry) {
		actions.RegisterCore(r)
		actions.RegisterRDMAActions(r)
	}
	os.Exit(cli.Run(register, os.Args[1:]))
}
```

Build:

```bash
go build -o rdma ./cmd/rdma
./rdma list | grep rdma_     # confirm registered
./rdma run scenarios/rdma-loopback-smoke.yaml
```

### 8.3. Pure parser + fixture-test contract

For any action that parses external tool output (id-ctrl, list-subsys,
sysfs walks, perf counters, etc.), follow the M1 contract:

1. **Extract a pure parser** (no I/O):

   ```go
   func parseIBStatus(stdout string) (*IBStatus, error) {
       // pure: stdout in, struct out
   }
   ```

2. **Drop captured real output as a fixture** under
   `actions/testdata/<action_name>/<scenario>.input.txt`.

3. **Drop the expected parsed struct** as
   `actions/testdata/<action_name>/<scenario>.golden.json`.

4. **Walk the dir in `_test.go`**:

   ```go
   func TestParseIBStatus_Fixtures(t *testing.T) {
       walkParserFixtures(t, "rdma_ib_status", func(input string) (interface{}, error) {
           return parseIBStatus(input)
       })
   }
   ```

The shared `walkParserFixtures` driver lives in
`actions/nvme_identify_test.go`. New actions inherit it for free.

This is the rule that closes the "stdin/heredoc bash bug" class
permanently — a pure Go parser with fixture coverage cannot silently
return empty just because of shell-quoting weirdness.

### 8.4. Register required params for stricter validation

```go
func RegisterRDMAActions(r *tr.Registry) {
    r.RegisterFunc("rdma_loopback", tr.TierBlock, rdmaLoopback)
    r.SetRequiredParams("rdma_loopback", []string{"dev", "port"})
    // ... others
}
```

`swblock validate <scenario.yaml>` will reject scenarios that omit
required params, before any action runs.

### 8.5. Mark mutating actions

Some actions destroy lab state (link flap, kill primary, force
unmount). Mark them mutating so the runner refuses to execute them
unless the operator passes `--allow-mutating`:

```go
r.RegisterFunc("rdma_link_flap", tr.TierChaos, rdmaLinkFlap)
r.SetMutating("rdma_link_flap", true)
```

CI defaults to refusing; lab passes `--allow-mutating` once.

---

## 9. Add a new product pack

Packs are reusable bundles of product-specific actions. Use a pack
when several wrappers might share the same action set, or when you
want to keep one product's actions in their own Go package.

```go
// packs/rdma/register.go
package rdma

import (
	tr "github.com/seaweedfs/artifactory/testops"
	"github.com/seaweedfs/artifactory/testops/actions"
)

// RegisterPack registers all RDMA-product-specific actions.
func RegisterPack(r *tr.Registry) {
	actions.RegisterRDMAActions(r)
	// ...add product-specific RDMA actions that don't belong in the
	// generic actions/ tree
}
```

Then in your wrapper:

```go
import (
	rdmapack "github.com/seaweedfs/artifactory/testops/packs/rdma"
)

register := func(r *tr.Registry) {
    actions.RegisterCore(r)
    rdmapack.RegisterPack(r)
}
```

Convention: pack-registered actions use a `<product>_` prefix
(`v3_blockmaster_start`, `rdma_qp_assert`), so they don't collide
with other products' actions if a kitchen-sink build links several
packs.

---

## 10. Add a new bundle-validator profile

When your scenarios stabilize, register a profile so operators can
validate bundles with a single flag:

```go
// validate_bundle.go (or a new file)
var rdmaReleaseGateProfile = ValidateProfile{
    Name:                "rdma-release-gate",
    ExpectScenario:      "rdma-release-gate",
    Children:            []string{
        "rdma-loopback-smoke",
        "rdma-cross-host",
        "rdma-fault-link-flap",
    },
    RequirePass:         true,
    RequireTiming:       true,
    RequireChildBundles: true,
}

func init() {
    registerProfile(rdmaReleaseGateProfile)
}
```

Then:

```bash
swblock validate-bundle --profile rdma-release-gate --expect-commit <sha> <bundle-dir>
```

---

## 11. What's NOT in the open runner today

These are documented in the open-core roadmap as **enterprise/private** scope:

- long-running remote agent (process that survives controller disconnect)
- shared queue / lease / heartbeat / pidlog
- fleet scheduling, lab inventory, multi-tenant
- cloud-burst provision/run/destroy actions
- RBAC, audit, dashboards, soak scheduling, perf trends
- full internal scenario corpus

For the open runner today, the operational pattern is "controller (your
laptop / Windows workstation) drives nodes over SSH; results land on a
shared drive; QA reads the bundle." That's what the seaweed_block project
uses today and is sufficient for small lab work.

---

## 12. Common pitfalls

| Pitfall | Symptom | Fix |
|---|---|---|
| Forgot `is_local: true` on local node | "read SSH key: open : no such file" | Add `is_local: true` to the node spec |
| Linux-style key path `/c/...` on Windows | "read SSH key: cannot find file" | Use `C:/work/dev_server/testdev_key` (forward slashes work on both OSes) |
| `env.X` in `cmd: "cd ... && bash ..."` | env not visible after `&&` | The runner now uses `export` form; if you see this on an old runner, upgrade |
| PowerShell 5.1 + `& go run` | `NativeCommandError` even on success | Use `pwsh` (PS 7), not `powershell.exe` |
| Stale image in k3s after rebuild | "binary doesn't accept --version" | After `docker build`: `docker save IMG | sudo k3s ctr images import -` |
| Lab residue from prior run | mid-workload "dangling iSCSI session" failure | First phase should be `pre_run_cleanup` action with broad iqn/nqn prefixes |
| Bundle reports VALID but child evidence missing | Forgot `--require-child-bundles` | Always pass `--require-child-bundles` when validating suite bundles |
| `wall_clock_s = 0` accepted | Forgot `--require-timing` | Profiles bundle this for you; for ad-hoc validate calls always pass it |

---

## 13. Where to look in the source

| You want to | Read |
|---|---|
| Understand a YAML field | `types.go` (Scenario, Phase, Action, Topology, ...) |
| Add a new action | `actions/system.go` (simplest example: `execAction`) |
| Add a parsing action with fixtures | `actions/nvme.go parseListSubsys` + `actions/testdata/nvme_list_subsys/` |
| Wire run-control to your run | `runstatus.go` + how `cli/cli.go runCmd` initializes it |
| Add a CLI subcommand | `cli/cli.go` (around the `switch args[0]` in `Run`) |
| Define a validator profile | `validate_bundle.go` (look for `protocol-release-gate`) |
| See a real product wrapper | `cmd/swblock/main.go` |
| See a real product pack | `packs/v3block/register.go` |

---

## 14. Suggested dev loop for an RDMA bring-up

1. **Add `actions/rdma.go`** with one or two minimal actions
   (`rdma_loopback`, `rdma_assert_qp_state`).
2. **Add `cmd/rdma/main.go`** wrapper, build it.
3. **Write `scenarios/rdma-loopback-smoke.yaml`** — single phase, runs
   locally first.
4. **`rdma validate ...` then `rdma run ...`** locally to confirm.
5. **Switch topology to m01/m02 SSH nodes**; re-run.
6. **Add a `pre_clean` phase** with `pre_run_cleanup` /
   `assert_no_processes` to make the scenario rerunnable from any
   lab state.
7. **Add fixture tests** for any action that parses tool output.
8. **Register required params + mutating flags** for safety.
9. **Compose 3-4 scenarios into a `suite.yaml`** with `mode: chain`.
10. **Add a `rdma-release-gate` validator profile**.
11. **Run the suite from a Windows or Linux controller against m02**;
    inspect the bundle.
12. **Hand QA**: one command to run the suite, one command to validate
    the bundle. Per QA-role discipline they should not need to touch
    the YAML or the code.

That's the full bootstrap. After step 12 you have a release gate the
QA team can run unattended and a bundle a CI pipeline can ingest.
