# Control-Plane Product Contract

How a product — **block, S3, VFS, RDMA** — plugs into the one TestOps / CI/CD /
ProdOps control plane so it is run, tracked, accepted, and compared **the same
way** as every other product.

This is the normative *plug-in contract*. It sits under the two existing docs and
makes their durable boundary concrete:

- [TestOps Control Plane Roadmap](testops-control-plane-roadmap.md) — the platform
  shape and the 6-phase roadmap (WHAT / WHEN). It states the durable boundary is
  the **bundle contract**; this doc *specifies* that contract and the interface
  around it.
- [Storage TestOps Platform](storage-testops-platform.md) — surfaces, SOP, and the
  scenario/action/result interfaces (the per-run mechanics).

> **Principle:** products differ in scenario, worker, and rows. They must NOT
> differ in the **envelope** — the submit shape, the bundle keys, and the
> acceptance assertion. RDMA is the reference implementation; block/S3/VFS adopt
> the same envelope rather than inventing their own.

---

## 1. The bundle/provenance envelope (the durable boundary)

Every serious gate, for every product, emits one common envelope into the run
bundle. Product-specific rows are layered **on top**, never instead.

**Bundle files (already standard):** `manifest.json`, `status.json`,
`result.json`, `result.xml`, `result.html`, `scenario.yaml`, `artifacts/`.

**Common envelope keys** in `result.json` `vars` (proposed; un-prefixed so one
assertion reads them across products):

| Key | Meaning |
|---|---|
| `__product` | `rdma` \| `block` \| `s3` \| `vfs` |
| `__gate_status` | `ok` on full pass (the product's `<gate>_status=ok`, normalized) |
| `__gate_pass` | `1` \| `0` |
| `__tested_ref` | the branch/SHA the submitter requested |
| `__tested_sha` | the resolved commit actually built/tested |
| `__lab_run_id` | the lab runner's run id (pointer to raw lab artifacts) |

**Product rows** keep their product prefix (per the platform doc):
`__rdma_perf_rc_push_mib_s`, `__block_fio_write_iops`, `__s3_put_p99_ms`,
`__vfs_read_rc4_mib_s`, … plus correctness witnesses (SHA/MD5 match, remount
read-back, no silent fallback).

Today RDMA already emits `__rdma_gate_pass / __rdma_mono_ref / __rdma_mono_sha /
__rdma_run_id` — i.e. the envelope with an `rdma` prefix. Onboarding = also emit
the **un-prefixed** common keys so the shared assertion works without per-product
code.

---

## 2. What a product provides (the plug-in checklist)

A product is a first-class control-plane citizen when it provides all five:

| # | Contract item | RDMA reference | Notes |
|---|---|---|---|
| 1 | **Runner + pack** | `sweedrdma` + `packs/rdma` | `swblock`/`sweeds3`/`sweedvfs` + their packs |
| 2 | **Unified gate scenario** | `scenarios/rdma-unified-lab-gate.yaml` | one canonical gate per product; emits the §1 envelope |
| 3 | **Worker adapter** | M01 worker runs `sweedrdma run …` under the lab lease | how the controller's worker checks out the ref, builds, runs, and writes the bundle |
| 4 | **Result root / project** | `results/rdma-ci` (+ `rdma-dev`) | `block-qa`, `s3-qa`, `vfs-rdma-qa` (+ `-dev`) |
| 5 | **Provenance EXPECT profile** | the `__rdma_perf_*` floors | the product's rows + acceptance floors the shared assert checks |

---

## 3. Submit → queue → worker (product-agnostic)

The controller stays safe and generic: the UI/API submits a **scenario + a few
controlled parameters**; the controller writes ONE request file to a per-project
queue; a per-product **worker** owns execution and the lab lease. No shell from
the UI.

Generalize the RDMA-named submit into a product-keyed one:

```text
# today (RDMA-specific)
POST /api/rdma/submit   { mono_ref, run_by }

# target (suite-agnostic handler, suite selected by path)
POST /api/<suite>/submit   { ref, scenario, run_by, team, test_id, dirty? }
   -> writes queue/<project>/<request>.env
   -> worker[product] consumes it, runs runner[product] + scenario, writes bundle
```

`block`/`S3`/`VFS` get the same 9099 submit + dashboard tracking RDMA has — they do
**not** each invent a queue. (Until a product has a worker, it runs via its CLI
per its runbook; that is the honest interim, not a second platform.)

### The suite contract (the wire spec a product implements)

The unification is **one controller + a suite registry**, not one controller per
product. `/api/<suite>/submit` is a single generic handler that looks `<suite>` up
in an allowlist; adding `block` is a registry entry, not a fork.

**Suite registry entry** (controller config — what makes `block` a suite):

```text
suite: block
  scenarios:   [ <allowlisted block gate scenarios> ]   # nothing else may be submitted
  ci_project:  block-ci          # results/<project> + queue/<project>
  dev_project: block-dev         # dirty=true is forced here, never *-ci
  lock:        block-lab         # leases/<lock> — block's lab/resource lease
  runner:      swblock           # worker invokes this
```

**Submit** — `POST /api/<suite>/submit` `{ref, scenario, run_by, team, test_id,
dirty?}` → validates (suite ∈ registry, scenario ∈ suite.scenarios, ref matches the
safe regex) → writes ONE request file to `queue/<project>/` → returns
`{request_id, queue_path, state}`. `GET /api/controller/status?suite=<suite>`
returns that suite's queue/running/done/failed.

**Shared filesystem layout** (every suite, only `<project>` differs):

```text
/mnt/smb/work/share/testops/
  queue/<project>/<request_id>.env          # submitted
  state/<project>/{running,done,failed}/<request_id>.env
  results/<project>/<run_id>/...            # the bundle
  leases/<lock>                             # the lab lease
```

**Request file** (`.env`, the worker's input):
`suite, ref, scenario, project, run_by, team, test_id, dirty, request_id, submitted_at`.

**Worker contract** (`<product>-ci-worker`, per product, same shape): watch
`queue/<project>` → acquire `leases/<lock>` → move request to `state/running` →
checkout `ref`, build, **resolve `__tested_sha`** → run
`runner <scenario> -results-dir results/<project> -meta project=<project> team=...
run_by=... test_id=... branch=<ref> commit=<sha>` → the gate emits the §1 envelope
→ move to `state/done|failed` → release the lease.

A product **copies this contract**, not RDMA's gate. It supplies its own scenario,
lock, build, and rows; the submit shape, queue/state layout, envelope, dirty rule,
and provenance assertion are identical to every other suite.

---

## 4. One acceptance assertion (not one per product)

Because every gate emits the §1 envelope, a single `qa-assert` validates any
product's bundle; the only per-product input is the EXPECT profile (rows/floors).

```text
qa-assert <bundle> --ref <requested> [--expect "k=v,k>=n,..."]
  requires: result.json/status.json/result.html/scenario.yaml/manifest.json
  checks:   result.status==PASS, status.state==pass,
            __gate_pass==1, __tested_ref==<requested>, __tested_sha present,
            __lab_run_id present,
            + each --expect row
  prints:   QA_BUNDLE_ASSERT_OK  (+ product, tested_sha, lab_run_id, key rows)
```

This collapses today's `RDMA_QA_BUNDLE_ASSERT_OK` and `BLOCK_QA_BUNDLE_ASSERT_OK`
into one `QA_BUNDLE_ASSERT_OK`. Ship it as `scripts/qa-assert.sh` so QA never
pastes inline python. Per-product EXPECT profiles live next to each product's
runbook.

> **Commit provenance rule:** `__tested_sha` must be recorded by the *worker/wrapper*
> at build time (resolved from `__tested_ref`), NOT inferred from `manifest.git_sha`
> — that field is the runner-cwd repo, not the product. (Verified on a block bundle:
> git_sha was the runner repo, not seaweed_block.)

---

## 5. Onboarding matrix (where each product stands)

| Item | RDMA | block | S3 | VFS |
|---|---|---|---|---|
| Runner + pack | ✅ sweedrdma | ✅ swblock | ✅ sweeds3 | ⛔ uses core `exec` (needs `sweedvfs`/pack) |
| Unified gate scenario | ✅ rdma-unified-lab-gate | ⚠️ many phase/helm gates, no single unified gate | ⚠️ s3-smoke-chain only | ⛔ scenarios scattered |
| Worker adapter | ✅ M01 worker + lease | ⛔ QA runs swblock by hand | ⛔ | ⛔ |
| Result root / project | ✅ rdma-ci/-dev | ✅ block-qa (+block-dev) | ✅ s3-qa | ✅ vfs-rdma-qa |
| Envelope keys (`__gate_pass`/`__tested_*`/`__lab_run_id`) | ✅ (rdma-prefixed) | ⛔ emits save_as counts | ⛔ | ⛔ |
| Provenance EXPECT profile | ✅ floors | ⚠️ per-gate save_as names | ⚠️ | ⛔ |

**Next onboarding step per product:** emit the §1 common envelope from its gate,
then give it a worker adapter so it can submit through 9099 like RDMA.

---

## 6. TestOps → CI/CD → ProdOps on one contract

The same envelope + submit + bundle serve all three; only the trigger and the
consumer change:

- **TestOps** — a human/agent submits a ref; reads pass/fail + evidence.
- **CI/CD** — GitHub/PR or a schedule submits the ref; the merge/release gate
  consumes `__gate_pass` + the EXPECT floors; the bundle is the PR evidence.
- **ProdOps / SiteOps** — the same controller runs read-only health / upgrade /
  incident-repro scenarios (roadmap Phase 6); operator-safe actions, RBAC + audit,
  each leaving an auditable bundle. seaweed_block's operator-status surface is the
  block ProdOps feed into this.

No product re-implements any of these layers — it implements §2 once.

---

## 7. How this slots into the roadmap

- Roadmap **Phase 1** (dependable tracking) → §1 envelope + §4 assert make
  tracking *verifiable*, not just present.
- Roadmap **Phase 2** (M01 controller-lite) → §3 generalizes its submit/worker
  from `rdma` to `product`.
- Roadmap **Phase 3** (binary store) → `__tested_sha` + a binary manifest are the
  same provenance need; `ensure_binary` records both.
- Roadmap **Phase 5** (comparison) → product rows + floors (§1/§5) are the inputs
  to baselines.
- Roadmap **Phase 6** (SiteOps) → §6.

Open decisions to ratify before block/S3/VFS onboard: the exact common key names
(§1), whether the worker or the scenario stamps `__tested_sha`, and the
`scripts/qa-assert.sh` interface (§4).
