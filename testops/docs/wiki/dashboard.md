# Live Dashboard

A global run view across projects, served by the `testops-dashboard` binary.
Result reports are read-only. On M01, the same service can also enable the
suite queue submit/status panel.

**URL:** `http://192.168.1.181:9099/` (M01)

| Page | Shows |
|---|---|
| `/` | Every run across projects — project, scenario, **status**, started, commit, host, report. Auto-refresh 15s. |
| `/report?run=…` | That run's `result.html`. |
| `/docs` | The curated doc follow-set (Handbook → Standard → Scenario Spec → Tutorial → product guides), rendered. |
| `/api/runs` | JSON of all runs. |
| `/api/controller/status?suite=<suite>` | Queue/running/done/failed state for one suite, when controller mode is enabled. |
| `/api/<suite>/submit` | Queue one suite gate request, when submit is enabled for that suite. |
| `/healthz` | Liveness. |

## How status works

The dashboard reads each bundle's **`status.json`** for the authoritative
`state`:

- **running** → a pulsing blue pill + `current_phase done/total` progress
- **pass** / **fail** → green / red
- (`manifest.json` carries no status field, so the live state comes from
  `status.json`; legacy bundles fall back to `manifest.status`.)

The index re-scans the results root on every request, so a new run appears
**without restarting** the dashboard.

## RDMA queue submit

When M01 starts the dashboard with `-controller`, the home page includes a small
TestOps Controller panel:

- submit a ref for enabled suites such as RDMA;
- see queued/running/done/failed counts per suite;
- open the normal result report after the worker finishes.

The panel does not execute shell commands. It writes one `.env` request file
under `/mnt/smb/work/share/testops/queue/<project>`; the suite worker owns
execution and the lab lock. Block is registered in the panel but submit remains
disabled until a block worker adapter is installed.

## Make your run show up

Point the runner at the shared results root, under your project's subdir:

```bash
<binary> run <scenario> -results-dir /mnt/smb/work/share/testops/results/<project>
```

`<project>` (e.g. `block-qa`, `s3-qa`, `rdma-qa`) becomes the **Project** column.
The shared root is on SMB (`//192.168.1.34/Work`), so bundles do **not** consume
m01/m02 local disk.

## Run metadata (test_id · project · run_by · team)

When several agents/teams share the dashboard, tag runs so they're easy to tell
apart and filter. Metadata lands in `manifest.json` and the dashboard surfaces it
(test_id under the scenario, run_by under the host, team as a chip; the project
link and team chip filter the view, or use `?project=…` / `?team=…`).

Three ways to set it, highest precedence last:

1. **Scenario `metadata:` block** — test-intrinsic identity that travels with the
   scenario:
   ```yaml
   name: vfs-rc-readonly-smoke
   metadata:
     test_id: vfs-rc-smoke
     team: rdma
     owner: vfs-rdma-qa
   ```
2. **`run -meta key=value`** (repeatable) — run-context that varies per execution:
   ```bash
   swblock run <scenario> -meta project=rdma-qa -meta run_by=vfs-rdma-agent \
     -results-dir /mnt/smb/work/share/testops/results/rdma-qa
   ```
3. **`meta.json` sidecar** — drop
   `{"test_id":…,"run_by":…,"team":…,"project":…}` into the bundle dir to annotate
   a run after the fact (no runner change needed).

Keys the dashboard renders specially: **test_id**, **project** (overrides the dir
name), **run_by** (or `runner`), **team**. Any other keys stay in `manifest.json`
for your own tooling.

## Deployment

`testops-dashboard` runs as a **systemd service on M01** (auto-starts on boot):

```bash
# unit: /etc/systemd/system/testops-dashboard.service  (User=testdev, Restart=always)
# binary: /usr/local/bin/testops-dashboard
# serves: /mnt/smb/work/share/testops/{results/<project>/<run>/, docs/}
sudo systemctl status testops-dashboard
```

Redeploy a new build with `install -m 755` (not plain `cp`, which drops the
exec bit → `203/EXEC` crash-loop):

```bash
sudo install -m 755 testops-dashboard /usr/local/bin/testops-dashboard
sudo systemctl restart testops-dashboard
```

## Disk hygiene

A weekly **`testops-janitor`** systemd timer on M01 + M02 prunes docker dangling
images and deletes stale `/tmp/sra-*` / `/tmp/mono-*` residue older than 7 days
(`journalctl -u testops-janitor`). If scenarios honor `cleanup`, manual cleanup
is rarely needed.
