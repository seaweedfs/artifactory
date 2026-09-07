# CI runners

This repository runs its Actions workloads on two kinds of runner: GitHub-hosted
images, and one persistent self-hosted machine carrying the label `tp01`. This
document is the rule for which work goes where, how to override it for a single
run, what happens automatically when the self-hosted runner is unavailable, and
what a workflow author may and may not do on a persistent host.

## Why there is a self-hosted runner at all

The organisation's hosted concurrency cap is the binding constraint, not CPU. A
publish that does about 19 minutes of real work has taken 84 minutes of wall
clock, the remainder spent queued behind other jobs in the same org. The
self-hosted runner adds capacity outside that cap and — because it is
persistent — keeps a warm Docker layer cache and warm toolchain caches between
runs. A "nothing changed" publish that costs tens of minutes hosted completes in
one to two minutes there.

Those two properties, spare concurrency and warm caches, are the whole reason
the runner exists. Anything that destroys the caches (see
[Cleanup rules](#cleanup-rules)) gives back most of the benefit.

## The split

| | GitHub-hosted | Self-hosted (`tp01`) |
|---|---|---|
| Trigger types | every trigger, including `pull_request` | `repository_dispatch`, `workflow_dispatch`, `schedule`, `push` on this repo's own branches |
| Privileges | full `sudo`, image discarded after the job | **no `sudo`**; the runner account is unprivileged and stays that way |
| Filesystem | fresh each job | persistent; treat everything outside the workspace and `$RUNNER_TEMP` as shared |
| Docker | per-job daemon | one shared system daemon, reached through the `docker` group |
| Concurrency | counts against the org cap | a single runner instance, so its jobs serialise |

### `pull_request` workflows never run on self-hosted

This is a hard rule, not a default.

A pull request runs the workflow file **as it exists on the PR branch**. On a
public repository anyone may open a pull request. If a `pull_request` workflow
could select a self-hosted runner, an author could change `runs-on` in their own
branch and execute arbitrary code on a persistent machine that holds the caches,
the Docker daemon, and the state of every other job. Fork isolation on a
throwaway hosted runner is what makes accepting stranger code safe, and a
persistent host does not have it.

So: `pull_request` and `pull_request_target` workflows are hosted-only, with no
`runner` input and no `runs-on` expression. Do not add one.

The work that *is* eligible for `tp01` is exactly the work a fork cannot
trigger: `repository_dispatch` suites fired by the source repository, manual
dispatches, scheduled runs, and pushes to branches of this repository.

### Release and publish workflows

Release and container-publish workflows stay hosted unless they are migrated
deliberately and individually. Multi-architecture container builds that emulate
`arm64`/`armv7` through QEMU are recommended to stay hosted: they are dominated
by emulation rather than by queue time, so they gain little, and they hold the
shared daemon for a long time.

## How a migrated workflow is shaped

Every workflow that may run on `tp01` follows the same four-part pattern.

**1. A `runner` input, so any run can be forced either way.**

```yaml
on:
  workflow_dispatch:
    inputs:
      runner:
        description: "Which runner to use"
        type: choice
        options: [tp01, hosted]
        default: tp01
```

**2. `runs-on` as an expression, defaulting to the workflow's own default.**

```yaml
runs-on: ${{ (inputs.runner || 'tp01') == 'tp01'
             && fromJSON('["self-hosted","Linux","X64","tp01"]')
             || 'ubuntu-24.04' }}
```

A workflow that is *prepared* but not yet flipped carries `'hosted'` as the
literal default in both places, so its automatic triggers keep using the hosted
image while `runner=tp01` still works for a single manual run.

**3. Privileged steps gated on the runner environment.** Anything that installs
packages, writes outside the workspace, or restarts a daemon is either rewritten
to need no privilege or fenced off:

```yaml
- name: Install build dependencies
  if: runner.environment == 'github-hosted'
  run: sudo apt-get install -y ...
```

On `tp01` the equivalent package is pre-installed once by an administrator and
recorded in the internal runbook; the workflow *asserts* the prerequisite rather
than installing it. Binaries a job needs on `PATH` go into `$RUNNER_TEMP/bin`
appended to `$GITHUB_PATH`, never into a root-owned directory — that also stops
one run's binary leaking into the next.

**4. A run name that carries the inputs.** The rescue path below can only
re-dispatch a run if it can read back what the run was for, and a queued run
exposes nothing but its name:

```
… · mono:<sha> · branch:<ref> · runner:<tp01|hosted>
```

Keep those fields in that format when adding a workflow.

## Manual fallback: `runner=hosted`

Any migrated workflow can be sent to the hosted image for one run by dispatching
it with `runner=hosted`. `runs-on` then resolves to exactly the image the job
used before migration, and every hosted-only step re-enables itself. Use it
when:

- the self-hosted runner is offline or busy and the run is urgent;
- a failure looks environment-specific and you want to compare;
- the job needs something on the host that `tp01` does not have.

The reverse, `runner=tp01`, opts a still-hosted workflow into the self-hosted
runner for one run without changing what any automatic trigger does. That is the
supported way to exercise a migration before flipping its default.

## The watchdog

`runs-on` has no "or else". A job pinned to `[self-hosted, tp01]` queues for up
to 24 hours if that runner is offline and never falls back on its own. The
scheduled workflow `.github/workflows/runner-watchdog.yml` is that fallback.

- **Cadence.** Every 10 minutes, plus `workflow_dispatch` for an immediate pass.
- **What it judges.** The symptom, not the runner. Listing runners requires repo
  admin; reading the queue does not. It looks at runs in the `queued` state for
  the workflows it is configured to watch, and reads the labels of their queued
  jobs.
- **What it acts on.** A run queued longer than `max_queue_minutes` (default 10)
  whose queued jobs carry the `self-hosted` label. Runs queued on hosted labels
  are left alone — that is ordinary queue pressure, not an outage.
- **What it does.** Cancels the run and re-dispatches the same workflow on the
  same branch with `runner=hosted`, passing through only those inputs the target
  workflow actually declares (a dispatch naming an undeclared input is rejected
  outright). The values are read back from the run name.
- **Why it cannot loop.** The re-dispatched run carries `runner=hosted`, so its
  jobs queue on hosted labels and the next pass ignores them.
- **Where it runs.** On a hosted runner, on purpose. A watchdog living on `tp01`
  would die with `tp01`.

The practical consequence: an offline self-hosted runner costs a delay of up to
about ten minutes, not a broken pipeline.

**When adding a workflow to `tp01`, add its filename to the watchdog's
`WORKFLOWS` list.** A migrated workflow the watchdog does not watch has no
automatic fallback.

## Cleanup rules

The instinct to end a job with `docker system prune -af --volumes` is correct on
a hosted runner and wrong here: it destroys the warm layer cache that is the
reason for the self-hosted runner, and it will happily delete images belonging
to whatever else is running on the host.

The rule:

- **Hosted:** clean up as aggressively as you like. Gate it on
  `runner.environment == 'github-hosted'` so it does not follow the job onto
  `tp01`.
- **Self-hosted:** use the composite action
  `.github/actions/runner-housekeeping`, invoked once after checkout. It is a
  no-op on hosted. On self-hosted it prunes **only** when free space on `/`
  drops below a floor (default 150 GB), and then only `docker builder prune` and
  `docker image prune` with `--filter until=336h` (14 days). Never volumes,
  never `-a` without an age filter.

It is a safety valve at a cliff, not a scheduled reaper. Trimming the cache on a
cadence is a host-side concern and belongs in a timer on the machine, not in
every workflow.

Jobs must also clean up after *themselves*, which matters on a persistent host
in a way it does not on a hosted one:

- Stop containers and remove compose stacks in an `if: always()` step.
- Do not write outside the workspace and `$RUNNER_TEMP`.
- Do not assume a fixed host port is free. It usually is, because one runner
  instance serialises its jobs, but a leaked process from a killed job surfaces
  as a bind failure.
- Give every job a `timeout-minutes`. A hung job on a single-instance runner
  blocks everything queued behind it.

## Security policy for the self-hosted runner

These are the rules the runner's owner has set. A workflow change that breaks
one of them will not be accepted.

1. **The runner account never gets `sudo`.** Not a targeted sudoers rule "just
   for this one step", not a wrapper script. A persistent runner serving a
   public repository must not be able to become root. Workflows that need root
   at run time — loading kernel modules, manipulating host network interfaces —
   stay on hosted runners.
2. **Nothing is installed on the host from a workflow.** Host prerequisites are
   applied once, out of band, by an administrator, and recorded in the internal
   runbook. Workflows assert their prerequisites and fail loudly when one is
   missing; they do not try to fix the host.
3. **No `pull_request`-triggered workflow may select a self-hosted runner** (see
   above).
4. **No `--privileged` containers, no host networking, no `cap-add`,** and no
   job-level `container:` mounting host paths outside the workspace.
5. **Secrets are per-run, not per-host.** Do not write a credential to a file
   outside `$RUNNER_TEMP`, and do not treat `docker login` as persistent host
   state established from inside a job.
6. **Registration is administrator-held.** Registering or removing a runner
   needs repository admin. If the runner disappears, migrated workflows queue and
   the watchdog rescues them onto hosted. That is the designed failure mode, and
   it is not fixed from inside a workflow.

## Adding a workflow to the self-hosted runner

1. Confirm the trigger is eligible — no `pull_request`, and no fork can fire it.
2. Audit every `sudo` in it. Each one is either (a) hosted-only cleanup: gate it;
   (b) install-time: gate it and add the package to the host pre-install list, or
   rewrite it to need no root; or (c) root at run time, in which case the
   workflow stays hosted.
3. Add the `runner` input, the `runs-on` expression, and the run-name fields.
4. Add `.github/actions/runner-housekeeping` after checkout in every job that
   checks out.
5. Add the workflow filename to the watchdog's `WORKFLOWS` list.
6. Land it defaulting to `hosted`, exercise it once with `runner=tp01`, then flip
   the default in a follow-up.

Rolling a migration back is always just reverting the change: no host state and
no repository setting is involved.
