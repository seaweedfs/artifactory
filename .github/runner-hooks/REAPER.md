# Post-job reaper for the self-hosted runner

`job-completed-reap.sh` + `job-started-record-session.sh`. **Not installed by
this pull request** — the scripts live here so they are reviewable and version
controlled; installing them is a host action, recorded in the internal runbook.

## The problem it solves

A GitHub-hosted runner discards its VM after every job, so a process that
outlives its job dies with the machine. A persistent runner does not. The
process keeps running, keeps its listening sockets, and the **next** job fails
to bind them — a failure that lands on a job which did nothing wrong.

This is observed, not theoretical. On tp01:

```
❌ ERROR: Some ports are still in use after aggressive cleanup!
```

`s3tests` failed this way while the suite itself was healthy, taking two jobs
down and cancelling three more.

The suites already try to clean up after themselves. That is not enough: a job
that is cancelled mid-run — which happens constantly, because
`cancel-in-progress` cancels superseded pushes — never reaches its own cleanup
step. Something outside the job has to do it.

## How it identifies what to kill

**By session id. Never by name.**

`job-started-record-session.sh` records the session id the job runs in. Every
process the job spawns inherits it, and — unlike a parent pid — the session
survives the parent exiting. That matters because the processes we are hunting
are exactly the ones whose parent is gone: an orphan reparents to init but keeps
its session, so the session id is the one identifier that still ties it back to
the job that made it.

Matching on a name pattern (`pkill -f weed`) would be wrong twice: it misses
anything renamed, and on a shared host it kills processes belonging to other
people. tp01 runs other things.

Three safety rules are in the script:

1. **Its own ancestor chain is never touched.** The hook runs *inside* the
   session it is cleaning, so its shell and the runner's worker share that
   session. Without this the first thing it reaps is itself, and the second is
   the runner. (Found in a dry run, before it ever went near a real job.)
2. **Only processes owned by the runner account.** Verified per pid.
3. **TERM, a grace period, then KILL** — so a process that can shut down
   cleanly does.

## The port sweep

A backstop for a process that escaped its session with `setsid()`. It resolves
each watched port to the **pid** holding it, checks that pid belongs to the
runner account, and only then kills it. A port held by somebody else's service
is reported and left alone.

Ports watched by default: `9333 19333 8888 18888 8333 8084 18084 9533 8004
26777 16777` — override with `RUNNER_REAP_PORTS`.

If a port is still bound at the end, the hook says so loudly. That line is the
early warning for the next job's failure.

## It never fails a job

The completed hook always exits 0. A job that already passed must not be marked
failed by its own cleanup. Every problem is reported in the log instead.

## Install (root-owned)

A job runs as the runner account. If that account could edit these scripts, one
job could disable the cleanup that protects the next — so they are installed
root-owned and read-only to the runner, exactly like the fork guard.

```bash
sudo install -o root -g root -m 0755 job-completed-reap.sh \
  /usr/local/lib/github-runner/job-completed-reap.sh
sudo install -o root -g root -m 0755 job-started-record-session.sh \
  /usr/local/lib/github-runner/job-started-record-session.sh
sudo install -d -o root -g root -m 0755 /usr/local/lib/github-runner

# State directory the two hooks share, writable by the runner account.
sudo install -d -o <runner-user> -g <runner-user> -m 0755 /run/github-runner
# /run is a tmpfs: recreate on boot.
printf 'd /run/github-runner 0755 <runner-user> <runner-user> -\n' \
  | sudo tee /etc/tmpfiles.d/github-runner.conf
sudo systemd-tmpfiles --create /etc/tmpfiles.d/github-runner.conf
```

Then in the runner's root-owned `.env`:

```
ACTIONS_RUNNER_HOOK_JOB_COMPLETED=/usr/local/lib/github-runner/job-completed-reap.sh
```

`ACTIONS_RUNNER_HOOK_JOB_STARTED` is already taken by the fork guard, which must
keep failing closed. Point it at a small root-owned wrapper that runs the guard
first and this recorder second, so a refusal still refuses:

```bash
#!/bin/sh
/usr/local/lib/github-runner/job-started-guard.sh || exit $?
/usr/local/lib/github-runner/job-started-record-session.sh
exit 0
```

Restart the runner service for `.env` changes to take effect.

## Testing without a runner

```bash
RUNNER_REAP_STATE=/tmp/reap ./job-started-record-session.sh
RUNNER_REAP_STATE=/tmp/reap RUNNER_REAP_DRYRUN=1 ./job-completed-reap.sh
```

Dry run lists what it *would* kill and touches nothing. Verified against a
deliberately leaked listener on a watched port: the reaper terminated it and the
port came back free, while a job running concurrently under a different account
was untouched.

## What this does not fix

Two instances running port-binding suites **at the same time** still collide —
this only cleans up *between* jobs on one runner. That needs per-instance port
offsets (`MASTER_PORT` / `FILER_PORT` / `S3_PORT`), which is a separate change
and a prerequisite for a second instance.
