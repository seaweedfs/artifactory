# Milestone M1 — NVMe Action Set + Multipath Smoke Port

Companion to [qa-platform-roadmap.md](qa-platform-roadmap.md). M1 is
the first concrete milestone in Tier 1: extend the NVMe action set
to be multipath-aware, **establish the TestOps three-layer pattern**
for V3 with the multipath smoke as member 1 of the V3 release gate,
and prove the fixture-test contract.

Target tag: **v0.2.0** (platform). Scope: single engineer, ~3-4 weeks.

## TestOps framing

M1 isn't "port one bash script to YAML." It is "make the V3
release-gate flow real." That work physically splits across three
layers:

| Layer | What lives there | Repo |
|---|---|---|
| Platform | engine, action library, suites/baselines/provenance/redaction/mutating, infra adapters | `seaweedfs/artifactory/testops` |
| Product pack | V3-specific actions and assertions (`v3_blockvolume_start`, `v3_wait_authority`, `v3_kill_replica`, `v3_assert_role`) | `seaweed_block/testops/v3pack/` |
| Product TestOps metadata | scenarios, suites/gates, baselines, provenance contract, mutating flags | `seaweed_block/testops/{scenarios,gates,baselines}/` |

The bash smokes today fold all three layers into one script. The
TestOps shape factors them apart: scenarios/baselines move at
product velocity; the action library moves at platform velocity;
the gate composition moves with release cadence.

## Why this milestone first

- Captive customer: V3 ships a passing multipath smoke today
  (commit a5ef1a5 on `frontend/nvme-ana-parity-plan`) and a passing
  mounted-failover smoke (e1e0e0c). Both have known-green
  reference outputs to anchor baselines against.
- Latent bug in current `actions/nvme.go` `findNVMeDevice` (line
  190): `return "/dev/" + p.Name + "n1"` is the same per-controller
  + n1 guess that the bash script just had to fix. Multipath
  testrunner scenarios would hit it on day one.
- Five P4 product fixes (CMIC / NMIC / cntlid / smart log) each
  correspond to a single Identify field. With assertion actions,
  the next regression of any of them surfaces in seconds with
  field-level diff, not days of layer-peeling.

## Concrete deliverables

The work is sequenced as **platform → v3pack → v3 metadata**, with
each step independently shippable. Within the platform side, the
sequence is "fix the latent multipath bug → add the parsing/probe
actions → add the TestOps wrapper primitives → ship the Gate kind."

### Platform side (this repo, target tag v0.2.0)

**PR-1 — NVMe multipath fix + parser refactor + fixture contract** (🟢 DONE this session)
- Pure `parseListSubsys(string) (*ListSubsysView, error)` extracted
- Sysfs-aware `nsDevicesViaSysfs` resolves merged ns devices
- `findNVMeDevice` prefers sysfs, falls back to per-path derivation
- 4 captured fixtures + goldens under `actions/testdata/nvme_list_subsys/`
- `TestParseListSubsys_Fixtures` walks the dir; M1 contract enforced

**PR-2 — `nvme_id_ctrl` and `nvme_id_ns`** (🟢 ready)
- Parsed Identify Controller / Identify Namespace structs
- Fixtures from m02 captures (CMIC=0x0a multipath, CMIC=0x00 single)
- Each P4 product fix becomes one declarative field assertion

**PR-3 — `nvme_read_ana_log`** (🟢 ready)
- Parsed ANA log binary (group_count / state / nsid)
- Fixture from today's PASS run + synthesized inaccessible state

**PR-4 — `nvme_assert_subsystem`** (🟢 ready)
- Composite declarative assertion: paths / namespace_devices / ana_state / cmic / nguid_set_size
- Replaces 30+ lines of bash gate logic with one YAML block
- Field-level diff on failure

**PR-5 — TestOps wrapper actions** (🟢 ready)
- `testops_pin_build`: build → record git SHA + bin sha256 + image digest into `provenance.json`
- `testops_pre_clean`: domain-profile pre-flight (kill stale, disconnect fabric, wipe state)
- `testops_post_capture`: failure-time domain bundle (the `on_failure: { capture: [...] }` shape from the roadmap)
- These collapse the boilerplate today's bash smokes hand-roll

**PR-6 — `Gate` kind** (🟡 design pending)
- Thin extension of existing `Suite`: declares membership, pass criteria, baseline anchoring, mutating policy
- One YAML resolves to "release-gate / soak-gate / smoke-gate" semantics
- Final platform-side piece needed before V3 can compose its first gate

### V3 pack side (`seaweed_block/testops/v3pack/`)

**PR-7 — V3 product actions** (🟢 ready)
- `v3_blockmaster_start { topology, listen, expected_slots }`
- `v3_blockvolume_start { replica, server, ports, durable_root }`
- `v3_wait_authority { replica, role, epoch_min }`
- `v3_kill_replica { replica }` (Mutating: true)
- These wrap the binary invocations the bash smokes do today.

**PR-8 — V3 assertions** (🟢 ready)
- `v3_assert_role { replica, role, epoch_min, ready }`
- `v3_assert_promotion_epoch { replica, min }`
- Wraps the JSON shape today's bash smokes parse out of `/status?volume=v1`

### V3 metadata side (`seaweed_block/testops/`)

**PR-9 — Scenarios** (🟢 ready)
- `scenarios/nvme-multipath-smoke.yaml` — replaces `run-nvme-multipath-smoke.sh`
- `scenarios/nvme-mounted-failover.yaml` — replaces `run-nvme-mounted-failover-smoke.sh`

**PR-10 — Gate + baselines** (🟢 ready)
- `gates/release-gate.yaml` — names both scenarios, pass criteria, mutating-required for failover
- `baselines/nvme-multipath-smoke.json` — anchored from `20260507T161800Z-test`
- `baselines/nvme-mounted-failover.json` — anchored from `20260507T170000Z`

**PR-11 — Bash smoke retirement** (🟢 ready)
- Move `scripts/run-nvme-*-smoke.sh` to `scripts/legacy/`
- CI gate flips to running `testops/run-gate release-gate`

---

Below: action signatures and acceptance for the platform-side PRs.

### PR-1 — NVMe multipath fix + parser refactor + fixture contract (✅ DONE)

**Bug.** `findNVMeDevice` derived the namespace device by
appending `"n1"` to each controller path's `Name`. Under native
multipath both controllers share one merged namespace device that
hangs off the **subsystem** in sysfs, not off either controller.
This is the same bug shape we just fixed in `run-nvme-multipath-smoke.sh`.

**Fix shipped.**
- Pure parser `parseListSubsys(stdout) (*ListSubsysView, error)`
  extracted (was inlined in `findNVMeDevice` and only reachable
  through SSH; now fixture-testable).
- `ListSubsysView` / `Subsystem` / `Path` exported as the typed
  return shape the rest of M1 builds on.
- New `nsDevicesViaSysfs(ctx, node, nqn)` walks
  `/sys/class/nvme-subsystem/*/subsysnqn` and returns the merged
  ns device(s) the subsystem owns.
- `findNVMeDevice` now: (1) parse list-subsys, (2) confirm NQN
  present, (3) prefer sysfs walk for the device, (4) fall back to
  per-path derivation only when sysfs returns nothing (single-path
  case).
- Fallback path preserves single-path correctness, so existing
  callers keep working unchanged.

**Fixture set.**

| File | Source |
|---|---|
| `actions/testdata/nvme_list_subsys/multipath_two_tcp.json` | Captured live from m02 run `20260507T161800Z-test` |
| `actions/testdata/nvme_list_subsys/single_path.json` | Single TCP controller, NQN match |
| `actions/testdata/nvme_list_subsys/multipath_one_inaccessible.json` | One ANA path inaccessible (synthesized) |
| `actions/testdata/nvme_list_subsys/no_match.json` | NQN not in output (kernel-not-yet case) |

Each has a sibling `<name>.golden.json` containing the expected
`ListSubsysView` JSON-marshalled.

**Tests.**
- `TestParseListSubsys_Fixtures`: walks the fixture dir, parses
  every input, diffs against its golden. **Adding a new fixture
  is sufficient to extend coverage; no test code change needed.**
- `TestParseListSubsys_FindByNQN`: exercises the lookup helper
  the assert actions will reuse.
- `TestParseListSubsys_FlatShape`: older nvme-cli output with
  no host wrapper.
- `TestParseListSubsys_Empty`: kernel-not-yet → empty view, no error.
- Existing `TestFindNVMeDevice_*` cases preserved (now exercise
  the fallback path explicitly).

**Acceptance.**
- ✅ `go test ./...` clean
- ✅ Sysfs walk validated against m02's live multipath state
- ✅ Existing single-path tests still pass byte-for-byte
- ✅ Public action signature unchanged; PR is pure refactor + correctness

### PR-2 — `nvme_id_ctrl` and `nvme_id_ns`

**Action signatures.**

```go
// nvme_id_ctrl
// Params: target (required), node (optional)
// Returns: value = JSON of parsed Identify Controller struct
type IdCtrl struct {
    VID      uint16 `json:"vid"`
    SSVID    uint16 `json:"ssvid"`
    SN       string `json:"sn"`
    MN       string `json:"mn"`
    FR       string `json:"fr"`
    CMIC     uint8  `json:"cmic"`
    CNTLID   uint16 `json:"cntlid"`
    NN       uint32 `json:"nn"`
    ANATT    uint8  `json:"anatt"`
    ANACAP   uint8  `json:"anacap"`
    ANAGRPMAX uint32 `json:"anagrpmax"`
    NANAGRPID uint32 `json:"nanagrpid"`
    OACS     uint16 `json:"oacs"`
}

// nvme_id_ns
// Params: target (required), nsid (default 1), node (optional)
// Returns: value = JSON of parsed Identify Namespace struct
type IdNs struct {
    NSZE     uint64 `json:"nsze"`
    NCAP     uint64 `json:"ncap"`
    NUSE     uint64 `json:"nuse"`
    NSFEAT   uint8  `json:"nsfeat"`
    NLBAF    uint8  `json:"nlbaf"`
    FLBAS    uint8  `json:"flbas"`
    NMIC     uint8  `json:"nmic"`
    NGUID    string `json:"nguid"`     // hex
    EUI64    string `json:"eui64"`     // hex
    ANAGRPID uint32 `json:"anagrpid"`
}
```

Implementation: shell out to `nvme id-ctrl <dev> -o json` /
`nvme id-ns <dev> -o json` (nvme-cli ≥ 2.4 supports `-o json`),
parse with `encoding/json`, return as the action value via
`save_as`.

**Fixture set.** One per (CMIC=0x00, CMIC=0x0a) for id-ctrl;
one per (NMIC=0x00, NMIC=0x01) for id-ns. Plus one fixture
captured from m02 today's PASS run.

**Acceptance.** Parse round-trips for all fixtures. Field values
match captured nvme-cli text output (parsed via `nvme id-ctrl
<dev>` no `-o json` for cross-check).

### PR-3 — Add `nvme_read_ana_log` (🟢 ready)

**Action signature.**

```go
// nvme_read_ana_log
// Params: target (required), nsid (default 1), node (optional)
// Returns: value = JSON of parsed ANA log
type ANALog struct {
    ChangeCount     uint64    `json:"change_count"`
    GroupCount      uint16    `json:"group_count"`
    Groups          []ANAGroup `json:"groups"`
}
type ANAGroup struct {
    GroupID         uint32   `json:"group_id"`
    NSIDCount       uint32   `json:"nsid_count"`
    GroupChangeCount uint64  `json:"group_change_count"`
    State           uint8    `json:"state"`        // raw
    StateName       string   `json:"state_name"`   // "optimized" / "non_optimized" / "inaccessible" / …
    NSIDs           []uint32 `json:"nsids"`
}
```

Implementation: `nvme get-log <dev> -i 0x0c -l <auto-size>
-b > <tmp>` then parse the binary header + per-group records.
The bash smoke already has this parser inline (`summarize_ana_log`);
port it to Go and fixture-test it.

**Fixture set.** Captured `nvme-ana-log.dev1.bin` from PASS run +
synthesized inaccessible-state binary.

**Acceptance.** State 0x01..0x04, 0x0F all parse to correct names.
Edge cases: 0-byte log (kernel didn't return any), undersized log
(< 40 bytes header) → return clear error.

### PR-4 — Add `nvme_assert_subsystem` (🟢 ready)

The declarative assertion action that turns 30+ lines of bash gate
logic into one YAML block.

**Action signature.**

```go
// nvme_assert_subsystem
// Params:
//   target: required
//   paths: int (expected path count)
//   namespace_devices: int (expected ns device count, 1 for native mpath)
//   ana_state: string (per-NS expected ANA state name)
//   cmic: hex string (e.g. "0x0a") — per-controller assertion
//   nguid_set_size: int (expect all paths to report same NGUID, count is 1)
// Returns: nil on success
```

Internally: composes `nvme_list_subsys` + `nvme_id_ctrl` (per
path) + `nvme_id_ns` + `nvme_read_ana_log`, evaluates the
declarative match, returns one structured failure per missing
or wrong field.

**Fixture set.** Reuse PR-1..PR-3 fixtures; add one
"all-correct" composite for the green path and one
"CMIC missing bit 1" for the regression-of-known-fix path.

**Acceptance.** Failed assertions print field-level diff
(`expected CMIC=0x0a, got 0x08`), not free-text.

### PR-5 — Port the multipath smoke as a scenario (🟢 ready)

Target file:
`examples/v3-testops/scenarios/nvme-multipath-smoke.yaml`

**Shape.**

```yaml
name: nvme-multipath-smoke
targets:
  v3-vol:
    nqn: nqn.2026-05.io.seaweedfs:mpath-v1
    nvme_port: 4421
phases:
  - name: build
    actions:
      - { action: go_build, params: { cwd: ., package: ./cmd/blockmaster, out: bin/blockmaster } }
      - { action: go_build, params: { cwd: ., package: ./cmd/blockvolume, out: bin/blockvolume } }
  - name: bring_up
    actions:
      - { action: v3_blockmaster_start, … }
      - { action: v3_blockvolume_start, params: { replica: r1, … } }
      - { action: v3_blockvolume_start, params: { replica: r2, … } }
      - { action: v3_wait_authority, params: { replica: r1, role: primary } }
      - { action: v3_wait_authority, params: { replica: r2, role: secondary } }
  - name: connect_paths
    actions:
      - { action: nvme_connect, target: v3-vol, params: { port: $PORT1 } }
      - { action: nvme_connect, target: v3-vol, params: { port: $PORT2 } }
  - name: assert_multipath
    actions:
      - action: nvme_assert_subsystem
        target: v3-vol
        params:
          paths: 2
          namespace_devices: 1
          ana_state: optimized
          cmic: "0x0a"
  - name: teardown
    always: true
    actions:
      - { action: nvme_disconnect, target: v3-vol }
on_failure:
  capture: [nvme, dmesg, blockvolume_logs]
```

**Acceptance.**
- Scenario runs green on m02 against the same artifacts the bash
  smoke produces (NGUID match, ANA group_id=1, two paths).
- Scenario runs against the **same product binary** the bash
  smoke runs against (no scenario-specific binary change).
- Total wall time ≤ bash smoke wall time + 10% overhead.
- `provenance.json` captures product git SHA + bin sha256 + image
  digests (per existing build action contract).

After PR-5 lands, the bash smoke moves to `scripts/legacy/` in V3
repo; CI green-light depends on the YAML scenario.

## Fixture-test contract (codified for M1)

Every PR in M1 must satisfy this contract or it does not merge:

1. **Every parser action ships with a `actions/testdata/<action_name>/` directory.**
2. The directory contains at least one `.input.<ext>` file (real
   captured product output) and one `.golden.json` (expected
   parsed struct, JSON-marshalled).
3. The action's `_test.go` contains a `TestParse_<Action>_Fixtures`
   that walks the directory, runs the parser on each input, and
   diffs against the golden.
4. New product output shapes (e.g. older nvme-cli version, edge
   case) get added as new fixtures, never as new code paths
   guarded by version checks. Code paths grow when a fixture
   forces them.
5. Generating fixtures is a documented one-liner per action:
   `actions/testdata/<action>/regenerate.sh` runs the live
   capture command on m02 and updates the fixture set. Diff
   review is part of the PR.

This is the single most important deliverable from M1. Without
it Tier 2 (cross-product reuse) has no foundation.

## Out of scope for M1

- iSCSI assertion gaps (Tier 1 separate milestone M2)
- K8s assertion gaps (Tier 1 separate milestone M3)
- Hardware-loop driver standardization (Tier 2)
- Wire-format schema codegen (Tier 3)
- Ports of `iterate-m01-nvme.sh` Matrices A-F (separate
  follow-up; M1 covers the multipath shape only)

## Risk register

| Risk | Mitigation |
|---|---|
| nvme-cli version drift on m02 vs CI runners | Pin nvme-cli ≥ 2.4 (json output) in CI; document in scenario `requires:` block |
| ANA log binary format changes across kernels | Fixture per kernel version; CI uses kernel pinned to m02's 6.17.x |
| `nvme_assert_subsystem` becomes a god-action | Hard cap at 6 fields; if a 7th is needed, split |
| Porting friction with V3 testops repo | M1 ships actions in sw-test-runner alone; V3 port follows when v0.2.0 tagged |

## Definition of done for M1

Platform side (this repo):
- [x] PR-1: NVMe multipath fix + parser refactor + fixture contract
- [ ] PR-2: nvme_id_ctrl + nvme_id_ns
- [ ] PR-3: nvme_read_ana_log
- [ ] PR-4: nvme_assert_subsystem
- [ ] PR-5: testops_pin_build / testops_pre_clean / testops_post_capture
- [ ] PR-6: Gate kind
- [ ] v0.2.0 tagged on github.com/seaweedfs/artifactory/testops

V3 pack side (`seaweed_block/testops/v3pack/`):
- [ ] PR-7: V3 product actions (start/wait/kill)
- [ ] PR-8: V3 assertions (role/epoch)

V3 metadata side (`seaweed_block/testops/`):
- [ ] PR-9: nvme-multipath-smoke.yaml + nvme-mounted-failover.yaml
- [ ] PR-10: release-gate.yaml + baselines anchored to today's PASS runs
- [ ] PR-11: bash smokes archived to scripts/legacy/

Cross-cutting:
- [ ] All NVMe parsers have fixture tests; all fixtures captured
      from real m02 output
- [ ] `testops/run-gate release-gate` produces green provenance.json
      when run from a clean V3 worktree
- [ ] `qa-platform-roadmap.md` Tier 1 NVMe row is checked off

After done, M2 (iSCSI ALUA assertions) starts. Same shape, same
contract, fewer surprises.
