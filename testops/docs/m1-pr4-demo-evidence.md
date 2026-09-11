# M1 PR-4 Demo Evidence — `nvme_assert_subsystem`

Evidence captured 2026-05-07 on m02 (`192.168.1.184`,
k3s 1.34, kernel 6.17.0-19, nvme-cli 2.8). All five test runs
hit the same V3 `seaweed-block` binaries built from
`frontend/nvme-ana-parity-plan` HEAD at the time of the run.

The point of this doc is to put the original bash-smoke results
next to the new testrunner demo runs, so the assertion-action
contract can be evaluated against the bash baseline directly.

---

## Test runs at a glance

| # | Test | Tool | Result | Wall time | Origin |
|---|---|---|---|---|---|
| 1 | NVMe multipath discovery | bash `run-nvme-multipath-smoke.sh` | PASS | ~30s end-to-end | a5ef1a5 |
| 2 | NVMe mounted failover (r1→r2 SIGKILL) | bash `run-nvme-mounted-failover-smoke.sh` | PASS | ~70s end-to-end | e1e0e0c |
| 3 | NVMe-P5 CSI dynamic PVC (NVMe protocol) | bash `run-k8s-alpha-nvme.sh` | **FAIL** at gated check | — | 622fae7 |
| 4 | `nvme_assert_subsystem` against live multipath | testrunner `weedblock run` | PASS | **135 ms** | 050e103 |
| 5 | `nvme_assert_subsystem` regression-shape (4 wrong fields) | testrunner `weedblock run` | **FAIL** with field-level diff | **145 ms** | 050e103 |

Tests 1-3 are the bash baselines. Tests 4-5 are the new
testrunner demo proving M1 PR-4 reproduces the bash gate
shape declaratively.

---

## Test 1 — NVMe multipath discovery (bash)

Run ID: `20260507T161800Z-test`
Artifact: `/tmp/sw-block-nvme-mpath/runs/20260507T161800Z-test/`

Tail of `run.log`:

```
[nvme-mpath] capture identity and ANA for /dev/nvme1n1
[nvme-mpath] disconnect both NVMe paths
[nvme-mpath] PASS: two NVMe/TCP paths expose one ANA-aware namespace
[nvme-mpath] artifacts=/tmp/sw-block-nvme-mpath/runs/20260507T161800Z-test
[nvme-mpath] cleanup
```

ANA log, identity, list-subsys all captured under the artifact
dir. The bash gate is `parse_nvme_subsys path_count >= 2 &&
parse_nvme_subsys devices count == 1` plus a binary `summarize_ana_log`
gate. ~60 LOC of grep + python over `nvme list-subsys -o json`
plus the binary ANA log parser inline.

## Test 2 — NVMe mounted failover (bash)

Run ID: `20260507T170000Z-nvme-p4-mounted-failover`
Artifact: `/tmp/sw-block-nvme-failover/runs/20260507T170000Z-nvme-p4-mounted-failover/`

Tail of `run.log`:

```
[nvme-failover] mkfs/mount NVMe multipath namespace
[nvme-failover] write pre-failover payload
[nvme-failover] kill active r1 pid=1080080
[nvme-failover] wait r2 failover
[nvme-failover] verify mounted workload after failover
[nvme-failover] unmount
[nvme-failover] disconnect NVMe subsystem
[nvme-failover] PASS: mounted NVMe multipath workload read/wrote through r1->r2 failover
[nvme-failover] artifacts=/tmp/sw-block-nvme-failover/runs/20260507T170000Z-nvme-p4-mounted-failover
```

Demonstrates pre-failover SHA-256 survives r1 SIGKILL +
r2 promotion. r2 reaches `Epoch=2, AuthorityRole=primary,
FrontendPrimaryReady=true`.

## Test 3 — NVMe-P5 CSI dynamic PVC (bash) — FAIL

Run ID: `20260507T183000Z-nvme-p5-csi-622fae7`
Artifact: `/mnt/smb/work/share/g15d-k8s/20260507T183000Z-nvme-p5-csi-622fae7/`

Console tail:

```
[alpha-nvme] frontend_protocol=nvme
[alpha-nvme] apply CSI dynamic-provisioning manifests
[alpha-nvme] apply dynamic StorageClass/PVC/pod
[alpha-nvme] wait for launcher-generated blockvolume manifest
generated blockvolume manifest missing NVMe arg --nvme-listen=
```

The new gate added in 622fae7 fired correctly. The
`lifecycle-volumes.json` artifact pinpoints the bug:

```json
{
  "spec": {
    "volume_id": "pvc-39354c27-e3d7-4205-a6b6-86f7eefe400b",
    "size_bytes": 1048576,
    "replication_factor": 1,
    "pvc_name": "sw-block-dynamic-v1",
    "pvc_namespace": "default",
    "pvc_uid": "39354c27-e3d7-4205-a6b6-86f7eefe400b",
    "pv_name": "pvc-39354c27-e3d7-4205-a6b6-86f7eefe400b"
  }
}
```

No `protocol` field in the lifecycle-stored spec. Bug is
upstream of `lifecycle.Create` (CSI-side parameter extraction
or RPC packaging). This is a PRODUCT bug; QA reported back to
dev for fix.

This test run is the dev-side argument for promoting the
lifecycle-volumes shape into a typed action: a future
`v3_assert_lifecycle_volume { protocol: nvme }` would have
caught this in seconds with `expected protocol=nvme,
got=<missing>` — same field-level diff shape demonstrated by
PR-4 below.

## Test 4 — testrunner GREEN

Scenario: `examples/v3-testops/scenarios/nvme-assert-demo.yaml`
Live target: V3 multipath stack with NQN
`nqn.2024-01.com.seaweedfs:vol.demo-v1`, two TCP paths.

Command:

```bash
sudo /tmp/sw-test-runner/bin/weedblock run nvme-assert-demo.yaml
```

Output:

```
2026/05/07 18:05:45 run bundle: results/20260507-180545-205a
2026/05/07 18:05:45 [phase] assert_multipath
2026/05/07 18:05:45   [action] nvme_assert_subsystem @local
2026/05/07 18:05:45   nvme_assert_subsystem OK (target=v3-vol nqn=nqn.2024-01.com.seaweedfs:vol.demo-v1)

=== nvme-assert-demo === PASS (135ms)

  assert_multipath     PASS  (135ms)
      nvme_assert_subsystem     135ms

  1 actions: 1 passed, 0 failed

2026/05/07 18:05:45 run bundle finalized: results/20260507-180545-205a
```

7 fields asserted against live state in 135 ms total:

```yaml
  - action: nvme_assert_subsystem
    target: v3-vol
    paths: "2"
    namespace_devices: "1"
    ana_state: "optimized"
    cmic: "0x0a"
    nmic: "0x01"
    anagrpid: "1"
    cntlid_distinct: "true"
```

This is the same wire shape the bash test 1 gates inline
across 60 LOC of grep + python + binary parsing. Action
implementation is shared, fixture-tested, single-edit.

## Test 5 — testrunner REGRESSION FAIL

Scenario: `examples/v3-testops/scenarios/nvme-assert-demo-regression.yaml`
Same live target as test 4. Four fields flipped wrong.

Output:

```
2026/05/07 18:06:04 run bundle: results/20260507-180604-d869
2026/05/07 18:06:04 [phase] assert_multipath_with_wrong_expectations
2026/05/07 18:06:04   [action] nvme_assert_subsystem @local
2026/05/07 18:06:04   nvme_assert_subsystem FAIL: 5 field(s) mismatched
2026/05/07 18:06:04     - cmic[path=0]: expected=0x00 got=0x0a
2026/05/07 18:06:04     - cmic[path=1]: expected=0x00 got=0x0a
2026/05/07 18:06:04     - nmic: expected=0x00 got=0x01
2026/05/07 18:06:04     - anagrpid: expected=99 got=1
2026/05/07 18:06:04     - ana_state: expected=inaccessible got=optimized (raw=0x01)

=== nvme-assert-demo-regression === FAIL (145ms)
EXIT=1
```

Each failure line corresponds to one P4 product fix. If the
matching commit got reverted on `frontend/nvme-ana-parity-plan`,
this exact output would surface in 145 ms:

| Failure line | P4 product fix it would catch |
|---|---|
| `cmic[*]: expected=0x00 got=0x0a` | `c0e4a6a` (CMIC bit 1 — multi-controller subsystem support) |
| `nmic: expected=0x00 got=0x01` | `035702f` (NMIC.SHARED — kernel rejects "duplicate unshared namespace") |
| `anagrpid` mismatch | ANA group identity wire shape |
| `ana_state` mismatch | per-path ANA state report |

(`60d3533` per-replica cntlid is also covered via
`cntlid_distinct: "true"` — exercised in unit tests.)

---

## Side-by-side: what the same regression would look like

If `c0e4a6a` (CMIC bit 1) regressed today:

**Bash baseline (test 1)**:
1. `run-nvme-multipath-smoke.sh` reaches `[nvme-mpath] connect second NVMe path`.
2. Kernel rejects: `Subsystem does not support multiple controllers`.
3. `set -e` silently exits.
4. Operator stares at empty `run.log`; opens dmesg manually; looks at `nvme-list-subsys.connect-failed.json` (if instrumented); inspects Identify Controller bytes.
5. ~30-60 minutes diagnosis OR (worse) silent self-PASS if instrumentation is incomplete.

**testrunner equivalent (test 5 shape)**:
1. `weedblock run nvme-assert-demo.yaml` reaches the assertion phase.
2. `nvme_assert_subsystem` runs `id-ctrl` per path, evaluates spec.
3. Output: `cmic[path=0]: expected=0x0a got=0x08` (or similar).
4. EXIT=1, run bundle saved with full evidence.
5. Wall time: 145 ms. Operator knows the field, the path, and the delta before they finish the next sentence.

---

## What this run does NOT prove

- Not a replacement for the K8s smoke (test 3 needs k3s + CSI lifecycle; M1 covers the wire-shape gate, not the K8s-CSI integration).
- Not perf evidence. The 135ms / 145ms numbers are for the assertion alone, against an already-attached stack; full bring-up + connect is dominated by the V3 daemon startup time, same as bash.
- Not multi-NSID or multi-ANA-group evidence (single-group fixture set today; parser handles `count > 1` and tests will be added when V3 grows that surface).

## Reproducing this evidence

From a clone of `seaweedfs/artifactory/testops` at commit `050e103` or later:

```bash
go build -o bin/weedblock ./cmd/weedblock
# Bring up V3 multipath stack with NQN
#   nqn.2024-01.com.seaweedfs:vol.demo-v1
# (the bash bringup script used here is in /tmp/bringup-v3-mpath.sh on m02
#  but any stack with that NQN suffix works)
sudo ./bin/weedblock run examples/v3-testops/scenarios/nvme-assert-demo.yaml
sudo ./bin/weedblock run examples/v3-testops/scenarios/nvme-assert-demo-regression.yaml
```

Unit-level reproduction (no live state needed):

```bash
go test ./actions/ -run TestAssertSubsystem -v
```

7 unit tests cover happy path, full regression delta, omitted
fields, NQN absent, cntlid distinct synthetic regression, and
spec parser for hex/decimal forms.
