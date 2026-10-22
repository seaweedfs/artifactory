# TestOps Handbook

> Drop-and-test manual for the SeaweedFS storage products (Seaweed Block, S3, VFS).
> Read this top-to-bottom once; after that the [Cheat Sheet](#12-cheat-sheet) is
> usually all you need. Companion doc: the architecture/standard is in
> [`cross-product-testops-standard.md`](cross-product-testops-standard.md).

## 0. The 5-minute mental model

```
 you ──CLI── swblock run scenario.yaml ──┐
                                          ├─► engine sequences PHASES of ACTIONS
 you ──Web── swblock console (:9090) ─────┘     across NODES (ssh/local), then
                                                writes a RESULT BUNDLE
                                                (result.json/.xml/.html + artifacts)
```

- **Runner** = one YAML-driven scenario engine. It does **not** know storage
  semantics in its core; product knowledge lives in **packs**.
- **Pack** = a product's plugin (a set of named actions). `block`/`v3block` exist
  today; `s3` and `vfs` are being added the same way.
- **Scenario** = a YAML file: a sequence of `phases`, each a list of `actions`
  (e.g. `exec`, `dd_write`, `kubectl_apply`), run against declared `nodes`.
- **Action** = one reusable step. List them all with `swblock list`.
- Everything a run produces lands under `results/<run-id>/`. Nothing is hidden.

You do **not** edit the runner's core to add a test. You write a **scenario**
(YAML), and if you need a new product step, you add an **action in a pack**. This
is how the whole team works in parallel without stepping on each other
(see [§10](#10-rules-of-the-road-dont-modify-each-others-tools)).

## 1. What you can test and where it runs

| Product | What it is | Runtime | Lab target |
|---|---|---|---|
| **Seaweed Block** | K8s block storage (CSI, iSCSI/NVMe, blockmaster, operator-status, lifecycle-owner) | k3s + Helm | m01/m02 k3s lab |
| **S3** | SeaweedFS S3 gateway (master+volume+filer+s3) | local/docker processes | m01/m02 (ssh + docker) |
| **VFS** | Rust FUSE mount + kernel module (`sw-fuse`/`sw-kd`/`seaweedvfs.ko`) | processes + mounts | swdev Debian box (kernel needs swdev) |

## 2. Lab environment & access

> If your lab differs, this is the only section you replace. Everything else is
> product/tool-generic.

| Node | IP | Role |
|---|---|---|
| m01 | 192.168.1.181 | k3s worker, benchmark client, RDMA 10.0.0.1 |
| m02 | 192.168.1.184 | k3s control-plane + build host, RDMA 10.0.0.3 |
| tp01 | 192.168.1.188 | k3s worker (currently NotReady — restore before RF=3 work) |
| swdev | (Debian box) | VFS kernel-module build/mount host |

- **SSH user**: `testdev`. **SSH key**: `C:\work\dev_server\testdev_key`
  (`/c/work/dev_server/testdev_key` from Git Bash).
  ```bash
  ssh -i /c/work/dev_server/testdev_key testdev@192.168.1.184   # m02
  ```
- **KUBECONFIG** (block / k3s): `/etc/rancher/k3s/k3s.yaml` on m02. Export it for
  any `kubectl`/`helm` command:
  ```bash
  export KUBECONFIG=/etc/rancher/k3s/k3s.yaml
  ```
- **Product source on the lab**: synced to `/tmp/seaweed_block` on m02 (block) and
  the mono repo for S3/VFS. Scenarios take it as `product_root`.
- **Shared evidence/scratch (SMB)**: `V:/share` (Windows) = `/mnt/smb/work/share`
  (Linux). Block scenarios stage artifacts under
  `/mnt/smb/work/share/g15d-k8s/<run-id>-*`.

> **Disk hygiene (m02):** keep `/` below ~80%. Above k3s's 85% image-GC threshold,
> k3s prunes local `:local` images mid-install and installs hang. `sudo docker
> image prune -f` (dangling only) is safe and usually enough.

## 3. Get the tool

The runner source is `artifactory/testops` (module
`github.com/seaweedfs/artifactory/testops`). One binary is built **per product** — each
links the product-agnostic core plus that product's pack(s):

| Binary | Packs linked | Use for |
|---|---|---|
| `swblock` | core + `v3block` | Seaweed Block (V3) |
| `weedblock` | core + `block` + `kv` | V2 weed-block |
| `sw-test-runner` | core + **all** packs | kitchen-sink / mixed |
| `sweeds3` *(new)* | core + `s3` | S3 gateway |
| `sweedvfs` *(new)* | core + `vfs` | VFS / FUSE |

Build (run from the runner repo; Windows `.exe` or Linux):

```bash
cd /c/work/seaweedfs/learn/artifactory/testops
go build -o /c/work/swblock.exe ./cmd/swblock          # Windows
# or on the lab:
go build -o /usr/local/bin/swblock ./cmd/swblock        # Linux
```

The runner runs **from your workstation** and reaches the lab over SSH (declared
per-node in the scenario), so you usually build `swblock.exe` on Windows and let
it drive m02. The go-test-only gates (no live cluster) can also run on the lab
directly where Go ≥ 1.24 is installed (m02 has Go 1.25).

Sanity-check the tool:

```bash
swblock list                 # every registered action, grouped by tier
swblock validate scenario.yaml   # parse + lint, no lab needed
```

## 4. Run your first test (CLI)

```bash
swblock run testops/scenarios/<scenario>.yaml \
  -env product_root=/tmp/seaweed_block \
  -env sw_block_image=sw-block:local \
  -env sw_block_csi_image=sw-block-csi:local
```

- A scenario is **self-contained**: it does its own pre-clean → build/seed →
  test → cleanup, so you can run it from a clean lab and it leaves the lab clean.
- `-env KEY=VALUE` overrides a scenario `env:` value (image tag, product_root,
  ssh key, …).

> **Two Windows gotchas that will bite you (they bit QA):**
> 1. **`-env` must come BEFORE the scenario path.** Go's stdlib flag parser stops
>    at the first non-flag arg, so `swblock run <scenario> -env X=Y` silently
>    ignores the override. Correct order: `swblock run -env X=Y <scenario>`.
> 2. **MSYS mangles `/tmp/...` values.** From Git Bash, `-env product_root=/tmp/x`
>    becomes `C:/work/tmp/gotmp/x`. Either don't override `product_root` (the
>    scenario default is already `/tmp/...`) or prefix:
>    `MSYS_NO_PATHCONV=1 MSYS2_ARG_CONV_EXCL='*' swblock run ...`.

### Tag your run so the dashboard can group it (metadata)

When several people/agents share the dashboard, pass identity so your runs are
easy to find and filter (the dashboard shows test_id, run_by, team, and filters
by project/team):

```bash
swblock run -env product_root=/tmp/seaweed_block \
  -meta project=block-qa -meta run_by="$(whoami)" -meta team=block \
  -results-dir /mnt/smb/work/share/testops/results/block-qa \
  testops/scenarios/<scenario>.yaml
```

- `-meta KEY=VALUE` is repeatable; it lands in `manifest.json` and shows on the
  dashboard. Keys it renders specially: **project** (overrides the results-dir
  name), **run_by** (or `runner`), **team**, **test_id**. Any other key is kept
  in the manifest for your own tooling.
- Test-intrinsic identity (`test_id`, `team`, `owner`) can instead live in the
  scenario's `metadata:` block so it travels with the test — see
  [Scenario Syntax](wiki/syntax.md).
- No flag handy? Drop a `meta.json` (`{"run_by":"…","team":"…"}`) into the run's
  bundle dir to annotate it after the fact.

## 5. Watch status & read the report

### CLI

```bash
swblock status --latest        # state of the most recent run (queued/running/pass/fail)
swblock list-runs              # all runs under results/
swblock cancel <run-id>        # stop a running scenario
```

A finished run is a **bundle** under `results/<timestamp>-<id>/`:

```
results/20260616-184100-4f8f/
├── manifest.json   run metadata (scenario, args, commit)
├── status.json     phase-by-phase state (what `status` reads)
├── result.json     structured per-phase / per-action results + saved vars
├── result.xml      JUnit XML (CI ingestion)
├── result.html     human-readable report — open in a browser
├── scenario.yaml   echo of the exact input
└── artifacts/      per-daemon logs, fio JSON, metrics, failure snapshots
```

### Web UI (status + report in the browser)

```bash
swblock console --port 9090 --scenarios-dir testops/scenarios
# open http://127.0.0.1:9090
```

The console serves:
- a scenario list + one-click **submit** (`POST /api/run`),
- **live status** (`/api/status`),
- **results + HTML reports** (`/api/result/`, `/api/report/`),
- the action catalog by tier (`/api/tiers`), and agents in distributed mode.

This is the "submit by CLI **or** UI, watch status, view report" surface — it
already exists; you just start it.

### Global read-only dashboard (who ran what, across projects)

`console` only shows runs it launched itself. To see **everyone's** runs (block-qa,
vfs-rdma-qa, s3-qa…) and their reports in one place, use the read-only dashboard
over the shared results root:

```bash
testops-dashboard -root /mnt/smb/work/share/testops/results -port 9099
# browser → http://<lab-host>:9099   project · scenario · status · commit + result.html links
```

It walks `…/results/<project>/<run>/` bundles, never runs or writes anything, and
auto-refreshes — safe to leave up for the team. Each agent just runs with its own
subdir: `swblock run -results-dir …/results/<project> <scenario>`. See the
standard §7c–7d for the shared-lab layout + the don't-collide rules.

### Offline gate check (no lab)

```bash
swblock validate-bundle --profile beta-hardening --expect-commit <sha> results/<run>
```

Named **profiles** encode a product's release-gate contract (which scenarios must
be present + pass at which commit). This is how a release is signed off
reproducibly.

## 6. The standard test process: build → seed → test → cleanup

Every product scenario follows the same four-beat shape. This is the contract
that makes block/S3/VFS feel the same to run:

| Beat | What it does | Block example | S3 example | VFS example |
|---|---|---|---|---|
| **build** | get the binaries/images under test onto the node | `build-alpha-images.sh` → `sw-block:local` | `make install` → `weed` | `cargo build` → `sw-fuse`/`sw-kd` (on swdev) |
| **seed** | install + create the first object/state | helm install + first PVC | start master/volume/filer/s3 + `mb` bucket + `put` | mount FUSE + create files |
| **test** | exercise + assert | writer/reader checksum, CRD/RBAC, failover | `get`+checksum, list, multipart, IAM | posix_itest / pjdfstest, read-back |
| **cleanup** | tear everything down, prove zero residue | helm uninstall + `verify-helm-cleanup.sh` | kill weed + rm data dir | `fusermount3 -u` / `umount` + rm |

A scenario encodes these as `phases`. The **cleanup phase is marked
`always: true`** so it runs even on failure, and ends with a residue assertion.
"Zero-residue close" is a required pass criterion for every product.

## 7. Where things live (directory standard)

```
artifactory/testops/
├── engine.go parser.go reporter.go coordinator.go ...   core framework (don't edit to add a test)
├── actions/        product-agnostic actions (exec, dd_*, iscsi_*, kubectl_*, fault, bench)
├── infra/          node abstraction (SSH or local exec), daemon lifecycle helpers
├── packs/          PER-PRODUCT action sets  ── add new products here
│   ├── v3block/    Seaweed Block (register.go + spec/spawn/status)
│   ├── block/ kv/  V2
│   ├── s3/         (new) S3 gateway pack
│   └── vfs/        (new) VFS / FUSE pack
├── cmd/            PER-PRODUCT binaries (swblock, weedblock, sweeds3, sweedvfs, sw-test-runner)
├── scenarios/      bundled scenario YAMLs
├── docs/           this handbook + the standard + spec
└── results/        run bundles (git-ignored)
```

Product repos keep their own `testops/scenarios/*.yaml` (e.g. `seaweed_block`
ships ~40 scenarios in `testops/scenarios/`). The runner is the **engine**; the
product repo owns its scenarios + per-phase scripts.

## 8. Write a new scenario

Minimal:

```yaml
name: my-smoke
timeout: 5m
env:
  product_root: "/tmp/seaweed_block"
  ssh_key: "C:\\work\\dev_server\\testdev_key"
topology:
  nodes:
    m02:
      host: 192.168.1.184
      user: testdev
      key: "{{ ssh_key }}"
phases:
  - name: build
    actions:
      - action: exec
        node: m02
        cmd: "cd {{ product_root }} && go build ./cmd/sw-block"
  - name: test
    actions:
      - action: exec
        node: m02
        cmd: "echo run-the-thing"
      - action: assert_greater          # assertions are actions too
        actual: "1"
        threshold: "0"
  - name: cleanup
    always: true                         # runs even if 'test' failed
    actions:
      - action: assert_no_processes
        node: m02
        pattern: "[/]blockmaster --"
```

Workflow: `swblock validate my-smoke.yaml` → `swblock run -env ... my-smoke.yaml`
→ read `results/<run>/result.html`. Discover available steps with `swblock list`
(grouped by tier: core / block / devops / chaos / k8s / s3 / vfs).

Variables: `{{ var }}` substitutes from `env:` (+ injected `run_id`, `bundle_dir`,
`artifacts_dir`). `save_as:` on an action stores its output for a later
`assert_*`.

## 9. Per-product quick recipes

**Seaweed Block (k3s lab):**
```bash
# build+import images on m02, then:
swblock run -env sw_block_image=sw-block:local -env sw_block_csi_image=sw-block-csi:local \
  testops/scenarios/helm-first-volume-via-sw-block-cli-chain.yaml
```
Live admission/finalizer gates need a VAP-capable cluster — m02 k3s is **v1.34.4,
VAP-enabled**. Don't use rancher-desktop (no VAP).

**S3 (m02 processes):** see `packs/s3/` + `scenarios/s3-smoke-chain.yaml`
(build `weed` → start master/volume/filer/s3 → `mb`+`put` → `get`+checksum →
kill+rm). Reference: `cross-product-testops-standard.md` §S3.

**VFS — kernel pull-RDMA gate (M01 + M02), proven:** the
`sw-rdma-mono-kernel-vfs-rdma-read` scenario builds the mono Rust volume
(`--features rdma`) + `sw-rdma-kd` + `seaweedvfs.ko`, brings up Go master/volume
(RDMA READ)/filer on M02, mounts the real kernel VFS on M01, reads
`/kernel-vfs.bin` through the mount, asserts the SHA matches and the daemon log
shows `rdma read OK`, then unmounts/rmmods/kills (zero residue). Run command:

```bash
swblock.exe run \
  -results-dir <results-root> \
  scenarios/sw-rdma-mono-kernel-vfs-rdma-read.yaml
```

Needs the RoCE lab (M01 10.0.0.1 / M02 10.0.0.3) and `sudo -n` on M01 for
insmod/mount/rmmod. The mono source is staged from
`/mnt/smb/work/share/rdma-source/...`. FUSE smoke + pjdfstest are the lighter
variants. Reference: the standard §5 (VFS column) + §8.

## 10. Rules of the road (don't modify each other's tools)

1. **Add a scenario or a pack action — never hack the core to make your test
   pass.** Core (`engine.go`/`parser.go`/`actions/`) is shared; changing it for
   one product breaks others.
2. **Actions shell-out + HTTP/gRPC only.** No importing a product daemon's
   internal Go/Rust types. This is what keeps the runner pluggable across Go
   (block/S3) and Rust (VFS).
3. **No authority shortcuts.** Actions must not bypass the product control plane
   (`promote`/`force-failover` back-doors). Drive the product the way a user
   would.
4. **One binary per product** (`cmd/<product>`); link only that product's pack.
   Shared changes go in `actions/` + `infra/` with a test.
5. **`go test ./... -count=1`** must stay green for the runner itself before you
   push a pack/action.
6. **Scenarios are self-contained + zero-residue.** Pre-clean at the start, a
   `always: true` cleanup at the end, and a residue assertion.

## 11. Troubleshooting (gotchas QA actually hit)

| Symptom | Cause / fix |
|---|---|
| `-env` ignored, scenario used its default image | Put `-env` **before** the scenario path (Go stdlib flag). |
| `cd: C:/work/tmp/gotmp/...: No such file` | MSYS mangled a `/tmp` `-env` value → `MSYS_NO_PATHCONV=1 MSYS2_ARG_CONV_EXCL='*'` or don't override `product_root`. |
| Install hangs 10m, pods `ErrImageNeverPull` | m02 disk > 85% → k3s GC'd the `:local` image. `sudo docker image prune -f`; re-import. |
| blockmaster `CrashLoopBackOff: flag ... -launcher-durable-impl` | image older than the chart. Rebuild image from the same commit (and import to **all** nodes). |
| VAP gate "denied ... no such key: status" / deny on a legit patch | CEL not `has()`-guarding optional fields; also **wait for VAP propagation** (probe a known-bad patch until denied) before asserting. |
| `operator-status: KUBERNETES_SERVICE_HOST required` | non-dry-run needs in-cluster config — run it inside a pod (image + the SA), not from the host. |
| `can-i` shows all `no` for CSI | SA name ≠ ClusterRole name: CSI pod SA is `sw-block-seaweed-block-csi`, not `...-csi-controller`. |
| `ops explain --master-api` unknown flag | `explain` uses `--master` (not `--master-api`). |

## 12. Cheat sheet

```bash
# build the tool
go build -o /c/work/swblock.exe ./cmd/swblock

# discover
swblock list                                   # actions by tier
swblock validate scenario.yaml                 # lint, no lab

# run (NB: -env BEFORE the path; MSYS_NO_PATHCONV on Windows for /tmp values)
MSYS_NO_PATHCONV=1 MSYS2_ARG_CONV_EXCL='*' \
  swblock run -env sw_block_image=sw-block:local scenario.yaml

# watch + report
swblock status --latest
swblock list-runs
swblock console --port 9090 --scenarios-dir testops/scenarios   # web UI

# gate sign-off (offline)
swblock validate-bundle --profile beta-hardening --expect-commit <sha> results/<run>

# lab
ssh -i /c/work/dev_server/testdev_key testdev@192.168.1.184      # m02
export KUBECONFIG=/etc/rancher/k3s/k3s.yaml
```
