# Agent TestOps Runbook

This is the short operating guide for development agents running block, S3,
VFS, or RDMA tests.

Use this when you need to prove a change. Do not invent a private test flow when
a TestOps scenario already exists.

## One-Minute Paths

RDMA agent:

```bash
ssh testdev@192.168.1.181
cd /opt/rdma-lab-ci/sw-test-runner
TESTOPS_MONO_REF=<branch-or-sha> ./scripts/testops-ci-submit.sh
```

If the dashboard is running with controller enabled, API submit is equivalent:

```bash
curl -X POST http://192.168.1.181:9099/api/rdma/submit \
  -H 'Content-Type: application/json' \
  -d '{"mono_ref":"<branch-or-sha>","run_by":"<agent-name>"}'
```

The controller API is suite-shaped: `/api/<suite>/submit`. RDMA is enabled now;
block is registered but stays disabled until the block worker adapter is added.

Then watch:

```text
http://192.168.1.181:9099/?project=rdma-ci
```

Block agent:

```bash
swblock run <scenario.yaml> \
  -results-dir /mnt/smb/work/share/testops/results/block-qa \
  -meta project=block-qa \
  -meta team=block \
  -meta run_by=<agent-name> \
  -meta test_id=<stable-test-id> \
  -meta branch=<branch-or-sha> \
  -meta commit=<sha>
```

S3/VFS agent:

```bash
sweeds3 run <scenario.yaml> \
  -results-dir /mnt/smb/work/share/testops/results/s3-qa \
  -meta project=s3-qa \
  -meta team=s3 \
  -meta run_by=<agent-name> \
  -meta test_id=<stable-test-id> \
  -meta branch=<branch-or-sha> \
  -meta commit=<sha>
```

Report the dashboard run, bundle path, commit, pass/fail, and the first failing
assertion if the run fails.

## Golden Rules

- Put every serious run on the shared dashboard.
- Use the shared results root, not a private temp directory.
- Include metadata: `project`, `team`, `run_by`, `test_id`, `branch`, `commit`.
- For the RDMA hardware lab, use the queue. Do not start parallel M01/M02 RDMA
  gates by hand.
- Report the result bundle path and dashboard link, not just terminal output.
- If a run fails, keep the bundle and explain the first real failing assertion.
- Do not run destructive lab commands outside a scenario unless the user
  explicitly asked for manual intervention.

## Dashboard

Open:

```text
http://192.168.1.181:9099/
```

The result view is read-only. On M01, the same dashboard may also show a small
RDMA queue submit/status panel. Result bundles live under:

```text
/mnt/smb/work/share/testops/results
```

Useful filters:

```text
http://192.168.1.181:9099/?project=rdma-ci
http://192.168.1.181:9099/?project=block-qa
http://192.168.1.181:9099/?team=rdma
```

## RDMA Hardware Gate

Use this for normal RDMA PR/lab validation:

```bash
ssh testdev@192.168.1.181
cd /opt/rdma-lab-ci/sw-test-runner
TESTOPS_MONO_REF=<branch-or-sha> ./scripts/testops-ci-submit.sh
```

The worker on M01 will pick it up, take the RDMA lab lock, run the scenario, and
publish the result.

Watch:

```text
http://192.168.1.181:9099/?project=rdma-ci
```

Status:

```bash
curl http://192.168.1.181:9099/api/controller/status?suite=rdma
ls -lt /mnt/smb/work/share/testops/state/rdma-ci/status
test -f /mnt/smb/work/share/testops/state/rdma-ci/status/last-run.json && \
  cat /mnt/smb/work/share/testops/state/rdma-ci/status/last-run.json
```

Logs:

```bash
ls -lt /mnt/smb/work/share/testops/logs/rdma-ci
tail -100 /mnt/smb/work/share/testops/logs/rdma-ci/<request>.log
```

Queue/state:

```bash
find /mnt/smb/work/share/testops/queue/rdma-ci -maxdepth 1 -type f
find /mnt/smb/work/share/testops/state/rdma-ci/running -maxdepth 1 -type f
find /mnt/smb/work/share/testops/state/rdma-ci/done -maxdepth 1 -type f | tail
find /mnt/smb/work/share/testops/state/rdma-ci/failed -maxdepth 1 -type f | tail
```

Direct debug only:

```bash
sweedrdma run scenarios/rdma-unified-lab-gate.yaml \
  -env mono_ref=<branch-or-sha> \
  -results-dir /mnt/smb/work/share/testops/results/rdma-dev \
  -meta project=rdma-dev \
  -meta team=rdma \
  -meta run_by=<agent-name> \
  -meta test_id=rdma-debug \
  -meta branch=<branch-or-sha> \
  -meta commit=<branch-or-sha>
```

Use direct mode only when you own the lab slot and are debugging the runner or
scenario. The team gate should go through `testops-ci-submit.sh`.

## Block Tests

Use `swblock`.

Default result project:

```text
/mnt/smb/work/share/testops/results/block-qa
```

Example:

```bash
swblock run <scenario.yaml> \
  -results-dir /mnt/smb/work/share/testops/results/block-qa \
  -meta project=block-qa \
  -meta team=block \
  -meta run_by=<agent-name> \
  -meta test_id=<stable-test-id> \
  -meta branch=<branch-or-sha> \
  -meta commit=<branch-or-sha>
```

If the scenario starts daemons, mounts devices, or changes the lab, it must have
a cleanup phase.

## S3 Tests

Use `sweeds3`.

Default result project:

```text
/mnt/smb/work/share/testops/results/s3-qa
```

Example:

```bash
sweeds3 run scenarios/s3-smoke-chain.yaml \
  -results-dir /mnt/smb/work/share/testops/results/s3-qa \
  -meta project=s3-qa \
  -meta team=s3 \
  -meta run_by=<agent-name> \
  -meta test_id=s3-smoke \
  -meta branch=<branch-or-sha> \
  -meta commit=<branch-or-sha>
```

## Evidence to Report

Every handoff should include:

- scenario name;
- run id;
- dashboard link or `project/run_id`;
- result bundle path;
- branch/commit tested;
- pass/fail;
- key metrics;
- failed phase/action, if any;
- root cause if known;
- what was not tested.

For accepted product gates, run the shared bundle assertion when the gate emits
the common envelope:

```bash
scripts/qa-assert.sh <bundle-dir> --ref <branch-or-sha> \
  --profile docs/qa-profiles/rdma.expect
```

Use `docs/qa-profiles/block.expect` for the block unified gate once block emits
`__product`, `__gate_pass`, `__tested_ref`, `__tested_sha`, and `__lab_run_id`.

Good example:

```text
RDMA gate PASS
run: rdma-ci/20260629-211837-07e9
branch: rdma/lab-gate-runner-tools
RC push: 11645.4 MiB/s
RC pull: 5492.2 MiB/s
DC push: 9872.0 MiB/s
bundle: /mnt/smb/work/share/testops/results/rdma-ci/20260629-211837-07e9
dashboard: http://192.168.1.181:9099/?project=rdma-ci
```

Bad example:

```text
looks good
```

## Failure Handling

When a run fails:

1. Open `result.html`.
2. Identify the first failed phase/action.
3. Check the action output and attached artifacts.
4. Check the worker log if the failure happened before the scenario completed.
5. Do not delete failed bundles.
6. Do not silently rerun until green without explaining the first failure.

For RDMA CI worker failures, check:

```bash
ls -lt /mnt/smb/work/share/testops/state/rdma-ci/status
test -f /mnt/smb/work/share/testops/state/rdma-ci/status/last-run.json && \
  cat /mnt/smb/work/share/testops/state/rdma-ci/status/last-run.json
tail -100 /mnt/smb/work/share/testops/logs/rdma-ci/<request>.log
systemctl status testops-rdma-ci-worker.service
```

## When to Use Email

Normal development does not need email. Use the dashboard.

Email or webhook notification is useful for:

- long nightly runs;
- unattended PR gates;
- soak tests;
- failure notifications when nobody is watching the dashboard.

Email is not the source of truth. The result bundle is.
