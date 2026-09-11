# sw-test-runner QA Platform Roadmap

Companion to [scenario-spec-v1-roadmap.md](scenario-spec-v1-roadmap.md).
That doc scopes the YAML language; this one scopes the **platform**:
action library completeness, infra adapters, artifact discipline, and
the dogfooding path that takes us from "scattered bash smokes per
product" to "scenarios are the source of truth for QA across V1/V2/V3."

Status legend: 🟢 ready to plan • 🟡 design pending • 🔴 v3-only,
do **not** start.

## Motivation

Concrete trigger from 2026-05-07: the V3 NVMe multipath smoke
(`run-nvme-multipath-smoke.sh`) burned 9 product-fix retry rounds
across two days. Five of those rounds were genuine wire-format
target bugs (CMIC, NMIC, cntlid, smart log). The remaining four
were two bash-only test-script bugs:

1. `parse_nvme_subsys` piped JSON into `python3 -` whose script source
   came from a `<<'PY'` heredoc; the heredoc consumed stdin and
   `sys.stdin.read()` always returned `""`. Wait loop saw
   `count=empty` 900 iterations in a row; kernel had two live paths
   the entire time.
2. `parse_nvme_subsys devices` synthesised `/dev/nvmeXn1` from each
   controller name. Under native multipath the kernel merges into
   one ns device under the subsystem; the synthetic `/dev/nvme2n1`
   never existed.

Both bugs were impossible to hit in the testrunner action layer
(Go code, structured exec, fixture-tested parser) — yet the very
same `/dev/<name>n1` shape currently lives in `actions/nvme.go`
at `findNVMeDevice`. The platform has not yet absorbed enough of
the QA surface to prevent this class of bug from biting again.

This roadmap is what that absorption looks like.

## Tier 1 — Action library completeness (next 2-4 weeks)

Every item is directly traceable to a specific failure mode we
already lived through.

### 🟢 NVMe action set extension

Today: 4 actions (connect / disconnect / get_device / cleanup),
single-path only, latent multipath bug in get_device.

Target:

| Action | Tier | Returns |
|---|---|---|
| `nvme_connect` | TierBlock | (existing — verify multipath-safe) |
| `nvme_disconnect` | TierBlock | (existing) |
| `nvme_get_device` | TierBlock | merged ns device path (FIX: walk `/sys/class/nvme-subsystem/<sub>/`) |
| `nvme_id_ctrl` | TierBlock | parsed Identify Controller struct (CMIC / NN / ANATT / …) |
| `nvme_id_ns` | TierBlock | parsed Identify Namespace struct (NMIC / NGUID / EUI64 / ANAGRPID / NSZE) |
| `nvme_read_ana_log` | TierBlock | parsed ANA log (group_count / state / nsid…) |
| `nvme_list_subsys` | TierBlock | structured `[]Subsystem{NQN, Paths[], NSDevices[]}` (post-merge) |
| `nvme_assert_subsystem` | TierBlock | declarative match: `paths`, `namespace_devices`, `ana_state`, `cmic`, `nguid` |

The five product fixes 19c9b93..ac36cb7 each correspond to **one
field** in Identify Controller / Identify Namespace. With
`nvme_assert_id_ctrl { CMIC: 0x0a }` the regression collapses from
"connect rejected with cryptic kernel message" to "assertion fails
on field X = Y, expected Z."

### 🟢 iSCSI action set audit

Symmetric coverage gap: P5 (ALUA) and P6 (CSI/k8s) tests run
mature iSCSI scenarios, but assertions about ALUA group state,
target portal state, and login redirect behavior are still split
between bash one-liners and ad-hoc `exec | grep`. Mirror what we
build for NVMe:

- `iscsi_assert_alua_state { tpg, expected_state }`
- `iscsi_id_lu` (parsed VPD pages)
- `iscsi_assert_session { initiator, target, portal_count, alua_state }`

Entry condition: pick the next iSCSI smoke scheduled to run on
m02; port that one and inventory its assertions; promote the 3-4
that recur into actions.

### 🟢 K8s assertion gaps

`actions/k8s.go` covers `kubectl_apply` / `kubectl_delete` /
`kubectl_wait` etc. but pod-level forensic actions are thin:

- `k8s_assert_pod_no_events { since, exclude_reasons }`
- `k8s_get_pod_logs_since { pod, ns, since, container }` (already
  partial; canonicalize)
- `k8s_assert_pvc_status { pvc, ns, expected_phase, expected_owner_ref }`

Entry condition: pvc-metadata branch ([memory] BUG fixed in
ff93033) revealed a class of "owner ref must be present and shaped
exactly so" assertions that today are bash-grep-on-yaml. Promote
those to a typed action.

### 🟢 Fixture-test contract for all parsers

Every action that parses product / kernel output must ship a
`testdata/<action>/` dir with at least:

- `<scenario>.input.json` (or .txt / .bin) — captured real output
- `<scenario>.golden.json` — expected parsed struct, JSON-marshalled
- `_test.go` round-tripping each fixture

The bash stdin/heredoc bug is impossible in Go, but the
*assertion drift* (per-controller-name + n1 → wrong ns device)
is the same shape and absolutely possible in Go. Fixture tests
are the moat. Acceptance: every parsing action lands with at
least 1 happy-path fixture and 1 edge-case fixture (multipath /
empty subsys / older nvme-cli version).

### 🟢 Failure-time artifact bundle profiles

Today the runner can collect artifacts on failure. Codify
domain-aware *profiles* invoked declaratively:

```yaml
on_failure:
  capture: [nvme, dmesg, k8s_pod_state]
```

Each profile is a registered Go function that snapshots the
right state for that domain (NVMe = `nvme list-subsys -o json` +
`/sys/class/nvme-subsystem/*` + dmesg-since-start; K8s = `kubectl
describe pod` + events + previous-container logs; iSCSI =
`iscsiadm -m session -P 3` + targetcli ls).

The bash smoke we just shipped does this manually in the
`cleanup` trap. Make it declarative + reusable.

## Tier 2 — Infra hardening + cross-product reuse (next quarter)

### 🟡 Hardware-loop driver standardization

Today m01 / M02 access is reinvented per harness:
- `iterate-m01-nvme.sh` syncs over SMB stage, builds remotely,
  runs over SSH
- `run-iscsi-os-smoke.sh` SSHes directly with hard-coded paths
- `run-nvme-multipath-smoke.sh` was just patched to clone via
  HTTPS from origin

Promote one canonical `infra/remote.go` driver:

```go
type RemoteHost struct {
    Name      string  // "m01" / "m02"
    SSHKey    string
    SyncMode  Sync    // SMB | Clone | RsyncOverSSH
    BuildRoot string
}
```

Scenarios reference `node: m02` and the driver handles
provisioning, build, run, artifact collection, teardown. Bash
harnesses become thin entry points (or go away).

### 🟡 Regression baseline + drift gate

Memory: baseline regression already exists in the framework but
no scenario uses it. Pick the NVMe multipath smoke (now green) as
the first baseline-anchored scenario:

- Capture this run's `provenance.json` + structured assertion
  results as the baseline
- CI gate: PR fails if a re-run drifts from baseline without
  explicit `--update-baseline`
- Forces "intentional change" discipline on test outputs

### 🟡 Mutating audit close-out

Carry-over from v1-roadmap. 119+ existing actions, today only the
new build batch is marked Mutating. Categorize the rest in one
small PR:

- `kubectl_delete*` against shared namespaces → mutating
- `corrupt_wal` / `fill_disk` / chaos action family → mutating
- `docker_push` (when added) → mutating

Net effect: CI defaults to refusing destructive actions; lab
scenarios pass `--allow-mutating` explicitly.

### 🟡 Cross-product wrapper finalization

Memory: V1 stub / V2 weedblock / V3 swblock / kitchen-sink wrappers
exist as packs. Solidify the contract:

- Each pack registers `<product>_*` actions
- Each pack ships an `examples/<product>/` scenario set
- Each pack ships a CI smoke that runs at least one scenario per
  release tag

Today V3 is well-covered, V2 has 24/24 PASS, V1 is a stub. Drive
V1 to "1 working scenario in CI" so the multi-product story is
real.

### 🟡 V3 testops/ relocation

Carry-over. Waits on platform tag (≥ v0.1.0) and the V3 repo
agreeing to pin via go.mod. Once relocated, the seaweed_block
repo's `testops/scenarios/` becomes part of the platform's
canonical example set.

## Tier 3 — Strategic platform features (half-year+)

### 🔴 Wire-format schema actions

The most architecturally interesting one. Define NVMe Identify
Controller / Identify Namespace / Get Log pages as Go structs
derived from the spec, with field-level validation:

```go
nvme.assert_id_ctrl_fields {
    CMIC: 0x0a,           // multi-controller + ANA
    NN:   1,
    ANATT: 10,
    OACS: { ManagementSupport: true },
}
```

Each field assertion would have collapsed one of the five product
fixes from the P4 cycle. Same approach for iSCSI PDUs (already
partly done in `weed/storage/blockvol/iscsi/`) and CSI gRPC.

Why deferred: needs a stable spec source-of-truth and
codegen path; not a 1-week effort.

### 🔴 TTFF telemetry as platform KPI

Per scenario, log wall-clock from "scenario start" to "first
failing assertion." The 2026-05-07 P4 cycle would show
TTFF=approximately 5 days. Platform's improvement target:
TTFF < 1h for any fixture-covered regression.

Why deferred: needs persistent storage / dashboard / alerting;
out of single-binary scope.

### 🔴 Cross-product compat soak as a first-class scenario kind

`Suite` already exists as a v0 ordering primitive. A "compat
soak" promotes that to a long-running matrix that runs V1/V2/V3
in parallel against shared assertions, anchors regression
baselines per-product, and gates single-binary release.

Why deferred: SW QA Platform Direction memo is the input;
needs design pass on what "shared assertion" means across
products with different wire formats.

### 🔴 Plugin / subprocess action protocol (CSI-style)

Already in scenario-spec-v2 deferred bucket. Stays here too —
needed when external orgs want to ship custom actions without
forking. Years off.

## What we will NOT chase

- Generic "test framework" abstractions (BDD / Cucumber-style /
  fluent assertion DSL). Our edge is **product-specific actions
  with fixture tests**, not pretty test prose.
- Cloud-test-cloud orchestration. Lab hardware (m01/m02) and
  local-K8s (k3s/kind) are the targets. Cloud CI runs scenarios
  against the same actions, but the actions don't grow cloud-
  specific code paths.
- Performance benchmarking framework competition. Existing
  `bench.go` / metrics scraping is the contract; we don't
  rebuild what `fio` / `dd` / `bcc-tools` already do.
- Replacing `go test`. Unit tests stay in Go. Scenarios are for
  **integration / hardware / cross-product** validation.

## Compatibility commitment

- Every Tier 1 action lands as additive — no existing action
  signature changes
- Existing bash smokes keep working until their replacement
  scenario is green AND in CI
- Each milestone ships behind its own minor tag (v0.2.x for M1,
  v0.3.x for M2…) so V3 testops can pin to a known-good version
- "Deprecation" of a bash harness means: the YAML scenario is
  green in CI for one release cycle, then the bash harness is
  archived (not deleted) under `scripts/legacy/`

## How milestones are scoped

Each milestone is a single PR set targeting one product surface,
sized to fit one engineer-month. The output:

1. Action implementations + fixture-tested parsers
2. One ported bash smoke as the proof scenario
3. Updated `scenario-spec.md` examples
4. CI smoke against the ported scenario

See [milestone-m1-nvme-multipath.md](milestone-m1-nvme-multipath.md)
for the M1 plan.
