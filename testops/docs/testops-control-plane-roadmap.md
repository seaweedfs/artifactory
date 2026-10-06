# TestOps Control Plane Roadmap

This project is growing from a scenario runner into a team control plane for
storage testing.

The short version:

```text
UI / API
  -> controller
  -> queue + lab leases
  -> SSH or agent executor
  -> scenario engine
  -> metrics + logs + result bundle
  -> dashboard + comparison + failure analysis
```

The same foundation should support two uses:

- **Developer TestOps:** repeatable PR gates, debug runs, perf comparison, and
  failure evidence for block, S3, VFS, and RDMA.
- **Production SiteOps:** controlled health checks, upgrade validation, incident
  reproduction, and cluster-level diagnostics without handing operators a pile
  of shell scripts.

## What Exists Today

| Piece | Status |
| --- | --- |
| Scenario runner | Exists. `sw-test-runner`, `swblock`, `sweeds3`, and `sweedrdma` run YAML scenarios. |
| SSH executor | Exists. It is the current default for M01/M02 and is good for bootstrap/debug. |
| Agent mode | Exists as a foundation. It has coordinator/agent commands, persistent registration, token auth, `/run`, `/exec`, and `/artifacts`. |
| Dashboard/controller | Exists at `http://192.168.1.181:9099/`. It scans shared result bundles; on M01 it can also expose the RDMA queue submit/status panel. |
| Shared results | Exists under `/mnt/smb/work/share/testops/results`. |
| M01 CI | Exists as a lab runner for RDMA gates. `testops-controller` can submit runs to the M01 queue; the worker executes them under the lab lock. |
| Binary store | Not standardized yet. Lab runs still mix build-in-place, copied binaries, and product-specific scripts. |
| Queue / lease | Exists for RDMA M01/M02 gates. Other products still need the same pattern. |
| Comparison database | Not standardized yet. The bundle has enough data, but trending and regression baselines are not first-class. |

## SSH and Agent Roles

SSH and agent mode are not competing test systems. They should be two execution
backends behind the same scenario/action contract.

```text
Scenario action
  -> NodeRunner
       -> SSH executor
       -> Agent executor
```

Use **SSH** for:

- first bring-up on a new machine;
- debugging broken agents;
- small labs such as M01/M02;
- one-off investigation where installing a daemon is not worth it.

Use **agent mode** for:

- data-center scale;
- long soak;
- frequent perf gates;
- runs that must survive controller reconnects;
- local metrics collection near hardware;
- binary caching on each node.

The long-term default should be agent mode. SSH remains the bootstrap and
fallback path.

## Artifact and Binary Store

Do not rely on ad-hoc copy commands for serious gates. Build once, publish with
a manifest, and make nodes fetch by checksum.

Local lab:

```text
/mnt/smb/work/share/testops/
  bin/
    seaweed-mono/<git_sha>/<target>/<binary>
    sw-test-runner/<git_sha>/<target>/<binary>
  results/
  docs/
```

Cloud or remote data center:

```text
s3://<testops-bucket>/
  bin/
  results/
  docs/
```

Each binary should have a small manifest:

```json
{
  "name": "volume-server",
  "repo": "seaweedfs/seaweed-mono",
  "git_sha": "abc123",
  "target": "linux-amd64",
  "profile": "release-rdma",
  "sha256": "...",
  "path": "smb://... or s3://..."
}
```

Every run should record the exact binary manifest it used. This is required for
reproducible performance numbers.

## Controller Responsibilities

The controller is the missing middle layer between "runner CLI" and "team
platform".

It should own:

- run submission from UI, API, GitHub, or a scheduled job;
- queueing and cancellation;
- lab/resource leases;
- binary selection and `ensure_binary`;
- choosing SSH or agent execution;
- run status;
- bundle validation;
- publishing result pointers to the dashboard;
- optional GitHub status/comment and email notification.

It should not execute arbitrary user shell from the UI. The UI submits a
scenario plus controlled parameters. Execution still goes through the scenario
engine and action registry.

## UI Scope

The first useful UI is small:

- choose a scenario;
- choose branch/commit/binary;
- set workload parameters;
- submit a run;
- watch queued/running/pass/fail;
- open `result.html`;
- compare this run against the last accepted baseline.

Later UI can add:

- test matrix builder;
- baseline approval;
- flaky-run history;
- lab inventory;
- node health;
- long-soak calendar;
- release readiness dashboard.

## AI Scope

AI can help, but it should not bypass policy.

Good AI jobs:

- summarize failed bundles;
- compare a run against prior baselines;
- point to likely root cause from logs and metrics;
- suggest a rerun profile;
- draft a new scenario;
- explain which node/resource is unhealthy.

Avoid:

- direct unrestricted root shell from chat;
- silent retries that hide failures;
- changing lab state outside the scenario/controller audit path.

The safe model:

```text
AI reads bundles/logs/metrics
  -> proposes diagnosis or next action
  -> controller executes only approved scenario actions
```

## Roadmap

### Phase 1 - Make Current Tracking Dependable

Goal: every serious run appears on the dashboard with enough metadata to be
reviewed by another person.

Work:

- require `project`, `team`, `run_by`, `test_id`, `branch`, and `commit`
  metadata for team gates;
- standardize result roots:
  - `rdma-ci`
  - `rdma-dev`
  - `vfs-rdma-qa`
  - `s3-qa`
  - `block-qa`
- keep result-bundle browsing read-only;
- expose RDMA submit only through the controlled queue panel;
- make dashboard docs link to this control-plane roadmap;
- add bundle validation to CI-style runs.

Exit criteria:

- a new RDMA run appears on the dashboard without manual copying;
- another developer can open the run and see source commit, scenario, status,
  metrics, and artifacts.

### Phase 2 - M01 Controller Lite

Goal: M01 can receive a run request and execute it with a single lab lock.

Work:

- add a small `testops-ci` service on M01;
- implement one queue;
- implement one lab lease for M01/M02 RDMA hardware;
- add the dashboard controller panel as the safe web/API submitter for the queue;
- invoke `sweedrdma run ...` or product-specific runners;
- write bundles to the shared results root;
- send optional email/GitHub status on pass/fail.

Exit criteria:

- RDMA gate can be triggered without an interactive shell;
- two overlapping RDMA requests serialize instead of corrupting each other;
- failed runs still produce a dashboard bundle.

### Phase 3 - Binary Store and `ensure_binary`

Goal: nodes run known binaries, not whatever happens to be installed.

Work:

- publish runner/product binaries into SMB for local lab;
- support S3-compatible artifact storage for cloud/data-center runs;
- add `ensure_binary` action/helper;
- record binary manifests in the run bundle;
- cache binaries on nodes by sha256.

Exit criteria:

- a scenario can say "run mono SHA X, profile Y";
- M01/M02 fetch or reuse the right binary automatically;
- perf results include binary provenance.

### Phase 4 - Agent First for Scale

Goal: data-center runs use persistent agents, while SSH remains bootstrap.

Work:

- install agents on M01/M02 first;
- run the same RDMA scenario through SSH and agent mode;
- add local metrics collection through the agent;
- add artifact upload from agent to result bundle;
- add heartbeat and cancellation checks.

Exit criteria:

- one scenario can run through either SSH or agent backend;
- agent-collected metrics are visible in the bundle;
- agent restart or controller reconnect does not lose the run.

### Phase 5 - Comparison and Regression Gates

Goal: performance claims become controlled comparisons, not screenshots.

Work:

- define `metrics.jsonl` as the stable metrics stream;
- add baseline selection and update rules;
- add regression thresholds;
- add A/B matrix reports;
- include hardware/network provenance.

Exit criteria:

- a PR gate can fail on real perf regression;
- a reviewer can see "this run vs accepted baseline" with matching units and
  workload shape.

### Phase 6 - SiteOps Mode

Goal: use the same control plane for operational checks.

Work:

- add read-only cluster health scenarios;
- add upgrade validation scenarios;
- add incident reproduction bundles;
- add operator-safe actions with RBAC/audit;
- add scheduled health checks.

Exit criteria:

- operators can run approved diagnostics without custom shell access;
- each SiteOps action leaves an auditable bundle;
- production-facing scenarios avoid destructive actions unless explicitly
  authorized.

## Current Plan

Current focus: turn the existing M01/M02 RDMA lab and 9099 dashboard into the
first real team workflow.

1. **Document the control-plane shape.**
   - Status: this document.
   - Verify: MkDocs builds and the roadmap is linked from the wiki.

2. **Keep result tracking read-only.**
   - Do not add arbitrary command execution to `testops-dashboard`.
   - Dashboard reads bundles for reports.
   - RDMA submit writes only queue request files.
   - Verify: `/healthz`, `/api/runs`, and `/api/controller/status` stay stable.

3. **Standardize RDMA gate publication.**
   - Use `scripts/run-rdma-ci.sh` for team-visible M01 runs.
   - Results go to `/mnt/smb/work/share/testops/results/rdma-ci` or
     `/mnt/smb/work/share/testops/results/rdma-dev`.
   - The wrapper runs `sweedrdma`, tags the run with metadata, and validates
     the completed bundle.
   - Verify: dashboard shows the run with `test_id`, `branch`, `commit`, and
     perf rows.

4. **Add M01 controller-lite design.**
   - Status: implemented for RDMA.
   - Initial implementation: `scripts/testops-ci-submit.sh`,
     `scripts/testops-ci-worker.sh`, and the dashboard controller panel.
   - One queue, one RDMA lab lease, one runner command.
   - Verify: one manual trigger creates a dashboard-visible run.

5. **Add binary-store design and first implementation.**
   - Start with SMB.
   - Define manifest fields.
   - Add `ensure_binary` to the runner or as a small pack helper.
   - Verify: a scenario runs a binary by sha256.

6. **Run SSH vs agent pilot.**
   - M01/M02 only.
   - Same scenario, same binaries, same result contract.
   - Verify: results are equivalent and agent artifacts arrive in the bundle.

This plan deliberately keeps the first step small. The durable boundary is the
bundle contract. UI, controller, agent, and AI should all build around that
contract instead of replacing it.
