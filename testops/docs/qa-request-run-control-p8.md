# QA Request: TestOps Run-Control + P8 Repeatability Validation

**Owner:** dev (sw-test-runner platform)
**For:** QA (m01/m02 lab)
**Runner commit (platform):** `seaweedfs/artifactory/testops` HEAD ≥ `b2f56c8`
**Product commit (V3):** `seaweed_block` HEAD as relevant to current P8 scenario
**Validation target:** the existing P8 compat-soak chain, on a shared drive, twice back-to-back, with full run-control coverage.

This request closes the loop on the run-control feature (status.json,
latest pointer, status/list-runs/cancel CLI). Local schema tests + a
Windows-driven m02 P5 run already validate the basics; QA is asked to
prove the same shape holds for a long P8 chain on real hardware,
including repeatability and cleanup discipline.

---

## What dev shipped

### Run directory schema (under any `results-dir` root)

```
<results-dir>/
├── latest                       ← plain-text file: run_id of most-recent run
└── <run_id>/
    ├── manifest.json            ← immutable identity (existing)
    ├── status.json              ← NEW: mutable run-control state
    ├── provenance.json          ← image/binary digests + git SHA (existing)
    ├── scenario.yaml            ← frozen scenario (existing)
    ├── result.json/.xml/.html   ← terminal results (existing)
    ├── artifacts/               ← collected via collect_path / on_failure
    └── control/                 ← NEW: cancel signal lives here
        └── cancel               ← presence requests cancel between phases
```

`status.json` schema (version 1):

| Field | Type | Notes |
|---|---|---|
| `schema_version` | int | always 1 |
| `run_id` | string | matches dir name |
| `scenario` | string | scenario.name |
| `state` | string | `queued / running / pass / fail / cancelled / error` |
| `phases_total` | int | counted with Repeat expansion |
| `phases_done` | int | bumped at each phase pass/fail |
| `current_phase` / `current_action` | string | empty when not running |
| `phases[]` | array | per-phase rows: `name / state / started_at / ended_at / error` |
| `started_at` / `ended_at` / `updated_at` | RFC3339 UTC | |
| `error_summary` | string | one-line terminal-fail summary |
| `artifact_dir` | string | absolute path |

Updated atomically (write-temp + rename); poll-safe.

### CLI surface (any `cli.Run`-based binary, e.g. `swblock`)

```
swblock status [-results-dir <root>] [run_id]   # latest if no id
swblock status -json [...]                      # raw status.json
swblock list-runs [-results-dir <root>]         # one line per run, * marks latest
swblock cancel [-results-dir <root>] [run_id]   # writes control/cancel
```

Exit codes for `status`:
- `0` → terminal pass
- `1` → running / fail / error / cancelled (still-running OR not-pass)
- `2` → missing / parse error

### Engine wiring

- `Engine.PhaseHook(when, name, state, errMsg)` — fired at phase
  boundaries; `cli.go`'s `runCmd` wires it to `StatusWriter`.
- `Engine.CancelCheck()` — polled before each normal phase;
  `cli.go` wires it to `StatusWriter.CancelRequested()`.
- Always-phases run regardless of cancel/fail; cleanup is preserved.

---

## Validation target: P8 compat-soak chain

QA should run the existing P8 runner-native scenario:

```bash
swblock run \
  -env product_root=/tmp/seaweed-block-nvme-p4l \
  -env ssh_key=C:/work/dev_server/testdev_key \
  -results-dir //smbshare/path/g15d-k8s/testops-runs/runs \
  testops/scenarios/iscsi-p8-compat-soak-chain.yaml
```

Pin `-results-dir` to the **shared drive** (visible from both Windows
controller AND m02), so QA can see the same run directory whether
they SSH into m02 or sit at the Windows workstation.

**On Linux** (running directly on m02):

```bash
swblock run \
  -env product_root=/tmp/seaweed-block-nvme-p4l \
  -env ssh_key=/home/testdev/.ssh/testdev_key \
  -results-dir /mnt/smb/work/share/g15d-k8s/testops-runs/runs \
  testops/scenarios/iscsi-p8-compat-soak-chain.yaml
```

**Run twice back-to-back** without manually pre-cleaning between runs.
The scenario's own `pre_clean` phase must handle stale state.

---

## Acceptance criteria

### 1. Repeatability (the gate)

- [ ] Run 1 reaches a terminal state (pass or fail).
- [ ] Run 2 reaches a terminal state (pass or fail) **without manual cleanup between runs**.
- [ ] If both PASS: ✅
- [ ] If either FAILs: the failure is explained by a real product/workload error visible in `phases[].error` and downstream artifacts; **not** by stale-session/process residue from a prior run. A stale state escape that bypasses pre_clean and surfaces inside a workload phase is a regression to file against pre_run_cleanup hygiene.

### 2. Required P8 phases all reach terminal state

For each run, `swblock status <run_id>` must show:
- [ ] `pre_clean` → pass
- [ ] `pin_build` (or scenario's pin phase name) → pass
- [ ] `os_fio_repeat` → pass
- [ ] `k8s_fio` → pass
- [ ] `k8s_attach_detach` → pass
- [ ] `collect_and_cleanup` (always) → pass

(If P8 scenario has slightly different phase names, use the names
from the scenario YAML; what matters is every phase the YAML
declares appears in `phases[]` with a terminal state.)

### 3. Run-control surface produces consistent answers

For each run:
- [ ] `status.json` exists in run dir and parses via `swblock status -json`.
- [ ] `phases_done == phases_total` at terminal state on pass paths.
- [ ] `started_at <= every phase started_at <= every phase ended_at <= ended_at`.
- [ ] `error_summary` matches the failed phase's error if state=fail; empty if state=pass.
- [ ] `latest` pointer equals run 2's run_id after run 2 completes.
- [ ] `swblock list-runs` shows both runs; only run 2 has `*` marker.

### 4. Provenance pinning intact

- [ ] `manifest.json` records product git SHA and runner version.
- [ ] `provenance.json` records image digests + binary sha256.
- [ ] `scenario.yaml` (frozen copy) byte-identical to the source YAML at run time.
- [ ] `alpha-images.env` (under pin_build artifact dir) records `GIT_DIRTY=false`.

### 5. Cleanup hygiene

After both runs complete (the second run's `collect_and_cleanup`):
- [ ] `iscsiadm -m session` on m02: no SeaweedFS sessions.
- [ ] `nvme list-subsys -o json | grep io.seaweedfs`: no V3 subsystems.
- [ ] `pgrep -af 'blockmaster|blockvolume|blockcsi'`: no V3 processes.
- [ ] `kubectl get pvc,pod,deploy -n default | grep sw-block`: no leftover P8 K8s resources.
- [ ] No leftover `/var/lib/sw-block/*` durable-root state from this run.

### 6. Failure-shape contract (only checked if a run fails)

- [ ] `status.json.state == "fail"`.
- [ ] `status.json.phases[N].state == "fail"` for the failing phase, with `error` populated.
- [ ] `collect_and_cleanup` phase still ran (always-phase) and contains the failure-time artifact bundle.
- [ ] `result.json` matches `status.json` on the failed phase identity.
- [ ] Failure is diagnosable from the on-disk artifacts alone, without re-running.

### 7. Cancel signal works (one-shot validation)

- [ ] Start a fresh P8 run (run 3, optional but encouraged).
- [ ] After 60s, run `swblock cancel <run_id>` (or drop a file at `<run-dir>/control/cancel`).
- [ ] The next phase boundary terminates the normal phase chain.
- [ ] `collect_and_cleanup` (always-phase) still runs.
- [ ] Final `state == "cancelled"`. Exit code from `swblock status` is `1`.

---

## Report-back format

Please return one block per run, plus a final summary:

```
## Run 1
runner commit:  <sha>
product commit: <sha>
run_id:         <id>
artifact dir:   <path>
swblock status output:
  ...
acceptance:
  - [ ] item-by-item per criterion above
cleanup audit (post-run-2 only): see Run 2

## Run 2
runner commit:  <sha>
product commit: <sha>
run_id:         <id>
artifact dir:   <path>
acceptance:
  - [ ] item-by-item
cleanup audit:
  iscsiadm -m session: <output>
  nvme list-subsys filtered: <output>
  pgrep ...: <output>
  kubectl get ...: <output>

## Summary
- gate verdict: PASS / FAIL
- if FAIL: which acceptance criterion + first failing phase + key artifact excerpts
- residue found: yes/no, what
- regressions/observations not covered by criteria
```

---

## Why this matters

Three of the four "P5 cycles burned" earlier this week were
infrastructure / harness issues, not product bugs:
1. Stale image not auto-imported to k3s.
2. `kubectl exec deploy -l` syntax bug in a script-side gate.
3. Lab-side stale iSCSI session leaked into a workload phase.

P8 is longer-running and exercises more cleanup surface area than P5.
If the run-control schema + repeatability gate hold there, the same
shape generalizes to every future scenario.

This is also the **first proof** that the controller can drive
m01/m02 via SSH while operators read run state from the same shared
drive — i.e. dev runs from Windows, QA reads progress on m02 over
NFS/SMB, both are looking at the same `status.json`.

---

## Out of scope for this validation

- Heartbeat / pidlog (control surface beyond status/list-runs/cancel).
  Tracked separately as M2 scope.
- Multi-node controller (HA / sharded scheduling).
  Out of scope for the schema work; the runner remains single-shot.
- Auto-rerun on transient infra errors. The control surface enables
  it but the policy stays explicit per-scenario for now.

If QA finds these missing while running P8 and they would clearly
unblock the validation, file a follow-up; do not block the gate on
them.
