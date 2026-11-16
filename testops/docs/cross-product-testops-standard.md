# Cross-Product TestOps Standard

> The architecture + standardization spec behind the
> [TestOps Handbook](testops-handbook.md). Audience: anyone adding a product,
> writing a pack, or reviewing how SeaweedFS (Block / S3 / VFS) is tested as one
> standardized platform. Goal: **one tool, one process, one evidence format**
> across products, so the team builds scenarios in parallel without forking
> tooling.

## 1. Why standardize

Today block validation is mature (≈40 scenarios + a runner with a web console),
but the recent live gates (admission, finalizer lifecycle) were run as one-off
manual sessions + per-phase scripts — **not reproducible regressions**. S3 and VFS
have their own ad-hoc Go-test / shell harnesses. Standardization means:

1. **One runner** (`sw-test-runner`) drives all three products via the **pack**
   plugin model — no per-product fork of the engine.
2. **One process** — every product scenario is `build → seed → test → cleanup`,
   self-contained, zero-residue.
3. **One evidence format** — every run is a bundle (`result.json/.xml/.html` +
   `status.json` + `artifacts/`), readable by CLI, the web console, and offline
   gate profiles.
4. **One access model** — the same lab topology + SSH/KUBECONFIG conventions.

The result: hand someone the [handbook](testops-handbook.md), they test; nobody
edits the shared core to make their product pass.

## 2. Architecture

```
        ┌─────────────────────────── product-agnostic CORE ───────────────────────────┐
        │  parser  engine  reporter  baseline  coordinator/agent  runstatus  console   │
        │  registry + ActionContext + tiers          infra.Node (SSH | local exec)     │
        └──────────────────────────────────────────────────────────────────────────────┘
                 ▲ RegisterCore(r)                              ▲ shell-out / HTTP / gRPC only
   actions/ ─────┘  exec, dd_*, fio, iscsi_*, kubectl_*, fault, bench, collect_path, assert_*
                 ▲ RegisterPack(r)
   packs/  ──────┴── v3block · block · kv · [s3] · [vfs]   (one dir per product)
                 ▲ link
   cmd/    ──────┴── swblock · weedblock · sweeds3 · sweedvfs · sw-test-runner (all packs)
```

- **Core** never imports a product. It sequences phases/actions, talks to nodes
  through `infra.Node`, and emits the bundle. It is unit-tested on its own.
- **Action** = `func(ctx, *ActionContext, Action) (map[string]string, error)`,
  registered under a **tier** (`core/block/devops/chaos/k8s/s3/vfs`).
- **Pack** = a product's `RegisterPack(r *Registry)` adding that product's
  actions. Packs **shell-out + HTTP/gRPC only** — never import daemon internals.
  This is the rule that lets one runner drive Go (block/S3) and Rust (VFS).
- **cmd/`<product>`** = a 15-line `main` that does `actions.RegisterCore(r)` +
  `yourpack.RegisterPack(r)` and calls `cli.Main`. Per-product binaries link only
  what they need; `sw-test-runner` links everything.

## 3. The scenario contract (build → seed → test → cleanup)

Every product scenario MUST be expressible as these four beats, encoded as
`phases`:

```yaml
phases:
  - name: pre_clean        # idempotent: kill stragglers, uninstall, delete leftovers
  - name: build            # produce the binary/image under test on the node
  - name: seed             # install/start + create first object/volume/mount
  - name: test             # exercise + assert (the actual checks)
  - name: cleanup          # always: true  → tear down + assert zero residue
    always: true
```

Required invariants (enforced by review + the cleanup assertion, not by the core):

1. **Self-contained.** A scenario starts from and returns to a clean lab.
2. **`cleanup` is `always: true`** and ends with a residue assertion
   (`assert_no_processes`, `assert_no_active_iscsi_sessions`, a `verify-helm-cleanup`
   / `posix`/`s3 ls` zero-residue check).
3. **Pin the artifact under test.** Build from a known commit/image; record it in
   the bundle manifest. Don't test "whatever is already installed".
4. **Negative-first.** Blocked/missing/stale/corrupt states must never surface a
   false healthy/`Ready=True`.
5. **No authority shortcuts** (see [handbook §10](testops-handbook.md#10-rules-of-the-road-dont-modify-each-others-tools)).

## 4. Environment / access standard

The runner runs on a workstation and reaches the lab through **per-node topology**
declared in the scenario — never hard-coded in actions:

```yaml
topology:
  nodes:
    m02: { host: 192.168.1.184, user: testdev, key: "{{ ssh_key }}" }
    m01: { host: 192.168.1.181, user: testdev, key: "{{ ssh_key }}" }
    swdev: { host: <swdev-ip>, user: testdev, key: "{{ ssh_key }}" }
    local: { host: 127.0.0.1, is_local: true }
```

Standard env keys (override with `-env KEY=VALUE`, **before** the scenario path):

| Key | Meaning |
|---|---|
| `product_root` | product source dir on the node (`/tmp/seaweed_block`, mono-repo path) |
| `ssh_key` | path to the SSH key on the **controller** (`C:\work\dev_server\testdev_key`) |
| `<product>_image` / `_csi_image` | image refs under test |
| injected: `run_id`, `bundle_dir`, `artifacts_dir` | per-run, for routing artifacts |

Lab facts (the swappable part): m01 `192.168.1.181`, m02 `192.168.1.184`
(control-plane + build host, `KUBECONFIG=/etc/rancher/k3s/k3s.yaml`, **k3s
v1.34.4 VAP-enabled**), tp01 `192.168.1.188` (NotReady), swdev (VFS kernel),
shared SMB `V:/share`=`/mnt/smb/work/share`. Secrets are **paths, not values** —
the key lives on the controller, scenarios reference it by `{{ ssh_key }}`.

## 5. Per-product mapping

| | **Seaweed Block** | **S3** | **VFS** |
|---|---|---|---|
| pack | `packs/v3block` | `packs/s3` *(new)* | `packs/vfs` *(new)* |
| binary | `cmd/swblock` | `cmd/sweeds3` | `cmd/sweedvfs` |
| source | `seaweed_block` → `/tmp/seaweed_block` | mono `enterprise/` | mono `seaweed-vfs/` |
| **build** | `build-alpha-images.sh` → `sw-block:local`, `sw-block-csi:local` | `cd enterprise && make install` → `weed` | `cargo build --workspace` → `sw-fuse`/`sw-kd`; `make` → `seaweedvfs.ko` |
| env target | m01/m02 k3s + Helm | m02 (ssh + processes/docker) | **M01** kernel (`seaweedvfs.ko` + `sw-rdma-kd`) + **M02** master/volume(RDMA READ)/filer, RoCE 10.0.0.x (proven); FUSE smoke also m02/CI |
| **seed** | `helm install` + first PVC writer | start master/volume/filer/s3 + `mb` bucket + `put` object | mount FUSE/`mount -t seaweedvfs` + write files |
| **test** | writer/reader checksum; CRD/RBAC/VAP; failover/rebuild; delete-lifecycle | `get`+checksum; `ls`; multipart; IAM/versioning; ceph s3-tests; warp | `posix_itest.sh`; pjdfstest; read-back checksum; bench |
| **cleanup** | `helm uninstall` + `verify-helm-cleanup.sh` (zero residue) | `pkill weed` + `rm -rf <data>` + `s3 ls` empty | `fusermount3 -u`/`umount` + `pkill sw-kd`/`rmmod` + rm |
| existing harness to wrap | scenarios + per-phase scripts | `enterprise/test/s3/*` Go suites, `start-seaweedfs-components.sh`, warp | `seaweed-vfs/tests/posix_itest.sh`, `bench.sh`, `framework_test.go` |

**Reuse, don't replace.** Packs should wrap the product's existing harness via
`exec` rather than re-implement it: the S3 pack starts the stack with the mono
repo's `start-seaweedfs-components.sh` and asserts on `aws s3`/the Go suites; the
VFS pack drives `posix_itest.sh` on the mounted path. The standard adds
orchestration, evidence, and a web console on top — it does not rewrite the
product tests.

## 5b. Where files live (placement rule)

One runner, one engine; product content versions with the product. The rule:

| Artifact | Home | Why |
|---|---|---|
| **engine / core / `infra`** | runner repo | shared; never forked per product |
| **product pack** (`packs/<x>`, the Go "对接" code) | runner repo (internal) | the plug-point; `swblock list` + console see it; one place to maintain |
| **per-product binary** (`cmd/<x>`) | runner repo | links only that product's pack |
| **full product scenario suite + per-phase scripts** | **product repo** (`<product>/testops/scenarios/`) | co-versioned with the chart/CRD/kernel code it tests — change the code and its scenario in the same PR |
| **smoke / reference scenario** | runner repo `scenarios/` | living example ("an S3/VFS scenario looks like this") |
| **one built binary, no drifting copies** | build `cmd/<x>` from the canonical runner repo | a workspace (scenarios + `results/`) may live anywhere, but the `swblock.exe`/`sweedvfs` in it must be built from the one runner source — not a separately-edited fork |

Seaweed Block already follows this: its 59 scenarios live in
`seaweed_block/testops/scenarios/`, the `v3block` pack lives in the runner. New
products do the same. (Scenarios *may* also sit in the runner if a product has no
repo yet — fine for an internal runner — but the moment a product owns code it
co-evolves with, the suite should move to the product repo.)

## 6. Onboarding a new product (the recipe)

1. `packs/<product>/register.go`:
   ```go
   package myproduct
   import tr "github.com/seaweedfs/artifactory/testops"
   func RegisterPack(r *tr.Registry) {
       r.RegisterFunc("myproduct_start_stack", tr.TierDevOps, startStack)
       r.RegisterFunc("myproduct_seed_object", tr.TierDevOps, seedObject)
       // ... shell-out / HTTP only
   }
   ```
2. `cmd/<binary>/main.go`: `actions.RegisterCore(r)` + `myproduct.RegisterPack(r)`
   + `cli.Main(...)` (copy `cmd/swblock`).
3. `go test ./... -count=1` green; `swblock list` shows the new tier.
4. Write one **smoke scenario** (`build → seed → test → cleanup`, zero-residue),
   validate, run, eyeball `result.html` + the console.
5. Add a `validate-bundle` **profile** for that product's release gate when the
   suite stabilizes.

Most steps are thin `exec` wrappers around the product's existing build/test
scripts — onboarding is hours, not weeks, because the engine, evidence, console,
distributed mode, and gate machinery are already shared.

## 7. Tools & evidence standard

- **One binary per product** (§2). Mixed work uses `sw-test-runner`.
- **Submit**: `swblock run <scenario>` (CLI) **or** `swblock console` web UI
  (`POST /api/run`). Suites: `swblock suite`.
- **Watch**: `swblock status --latest` / `list-runs` / `cancel`; the console
  `/api/status`; live phase state in `status.json`.
- **Report**: per-run `result.html` (browser) + `result.xml` (JUnit/CI) +
  `result.json` (machine) + `artifacts/` (logs, fio, metrics, failure snapshots).
- **Gate**: `validate-bundle --profile <name> --expect-commit <sha>` checks a
  bundle offline against a product-owned suite contract — the reproducible
  sign-off that replaces "trust me, I ran it manually".
- **Distributed**: `coordinator` + `agent` fan a suite across hosts; the console
  shows agents.

## 7c. Multi-project shared lab (parallel, non-conflicting)

Block QA and VFS-RDMA QA (and S3, KV…) all run on the **same** M01/M02 lab, often
driven by **different project agents at the same time**. They must not 打架. The
model:

### Shared layout (on the SMB share, visible to every node + the dashboard)

```
/mnt/smb/work/share/testops/
├── results/                 one root, per-project subdir → the global dashboard reads this
│   ├── block-qa/<run-id>/    (run bundles: manifest.json, status.json, result.html, artifacts/)
│   ├── vfs-rdma-qa/<run-id>/
│   └── s3-qa/<run-id>/
└── leases/                  file locks for singleton resources (see below)
```

Each agent runs with its own results subdir:

```bash
swblock run  -results-dir /mnt/smb/work/share/testops/results/block-qa     <scenario>
sweedvfs run -results-dir /mnt/smb/work/share/testops/results/vfs-rdma-qa  <scenario>
```

Scenarios/workspaces stay **parallel and private** to each project (own folder,
own repo); only the `results/` root is shared, and only for read.

### Isolation rules (so concurrent runs don't collide)

1. **Namespace everything by `<project>-<run_id>`.** Run dirs, k8s namespace /
   helm release, iSCSI IQN / NVMe NQN prefix, object collections, log files —
   all carry the project + run id. The runner injects `{{ run_id }}`; use it in
   every path/name. Two runs then never share a dir/name.
2. **Per-project port ranges.** Give each project a base (e.g. block via k3s
   fixed; VFS 9100–9199 — its gate already uses 9753/8103/9103/7530; S3 8300–8399).
   Never bind a default port (9333/8888/8333) in a shared-lab scenario.
3. **Scoped process cleanup — the #1 打架 trap.** Never `pkill -f weed` /
   `pkill seaweedfs-volume-server` by bare name — that kills another project's
   live daemon. Scope teardown to *this run only*: track PIDs in the run dir
   (`echo $! > {{ run_dir }}/x.pid`) and `kill $(cat …pid)`, or match a
   run-unique tag, not the binary name. (The current VFS gate uses bare-name
   `pkill` — fine while it runs alone, must be scoped before it runs concurrently
   with another weed-using project.)
4. **Lease the singletons.** Some resources are global and can't be shared
   concurrently:
   - the **k3s cluster** (one helm release/namespace at a time per node),
   - the **M01 kernel module** (`seaweedvfs.ko` — single insmod/mount),
   - a fixed **RDMA port**.
   Guard each with a lease file under `…/testops/leases/<resource>` (atomic
   create / `flock`, holder = `<project>-<run_id>`, with a TTL). Runs needing a
   singleton block until they hold its lease; everything else (process+port+dir
   scoped) runs fully parallel. (Future: the runner's `coordinator`/`agent` can
   schedule this centrally; a lease dir is enough to start.)
5. **One read-only global view:** `testops-dashboard -root …/testops/results`
   (see §7d). Any number of agents keep writing bundles; the dashboard only
   reads them, so observability never contends with execution.

## 7d. Global observability (read-only dashboard)

`cmd/testops-dashboard` is a **read-only** global view over the shared results
root — it walks `…/results/<project>/<run>/manifest.json`, lists every run
(project · scenario · status · started · commit · host), and serves each run's
`result.html`. It never executes or writes, so it's safe to leave running on a
lab host for the whole team:

```bash
testops-dashboard -root /mnt/smb/work/share/testops/results -port 9099
# browser → http://<lab-host>:9099   (auto-refreshes; "what ran + reports")
```

Note the distinction from `swblock console`: `console` is an *interactive runner*
(pick a scenario, run it in-process, one at a time) and only shows runs it
executed itself. `testops-dashboard` is the *cross-project historical view* over
on-disk bundles produced by any agent's `swblock/sweedvfs run`. Use the dashboard
for "who ran what"; use `console` (or the CLI) to launch.

## 8. Roadmap

- **Now (this change):** standard + handbook committed; `packs/s3` scaffold +
  `s3-smoke-chain.yaml` proving the model end-to-end (build→seed→test→cleanup,
  zero-residue) on m02.
- **Next:** port the block live gates (admission/finalizer/delete-lifecycle) from
  one-off manual sessions into scenarios so they become one-click regressions;
  add `validate-bundle` profiles per product.
- **VFS already has a real gate (~85%):**
  `sw-rdma-mono-kernel-vfs-rdma-read` runs today on the same runner — M01 kernel
  VFS mount + M02 mono Rust volume native RDMA READ, asserting the read SHA and
  that the daemon log shows `rdma read OK`. It is all `exec`/`assert_*` (no pack
  yet) and lives in a scratch workspace. Standardization to-do: (1) factor the
  repeated `exec → assert_contains <SENTINEL>` blocks into a `packs/vfs` action
  set (`vfs_stage_source`, `vfs_build_*`, `vfs_bringup_master|volume|filer`,
  `vfs_upload_object`, `vfs_kernel_mount`, `vfs_read_verify_sha`,
  `vfs_assert_rdma_used`, `vfs_teardown`) so the gate body shrinks and the next
  VFS scenario reuses them; (2) promote the scenario into the VFS product repo
  (mono `seaweed-vfs/testops/scenarios/`) per the §5b placement rule; (3) build
  that workspace's runner binary from the canonical runner source so copies don't
  drift.
- **Then:** S3 ceph-s3-tests + warp scenarios; VFS FUSE smoke + pjdfstest.
- **Platform polish:** a shared `console` instance on a lab host over the common
  `results/` root = the team's live status/report dashboard; per-product gate
  profiles wired into CI via `result.xml`.

This standard is intentionally additive: it formalizes the pack model the runner
already ships, and wraps each product's existing tests rather than rewriting them.
