# Self-hosted runner job guard

`job-started-guard.sh` is a runner-side pre-job hook for the `tp01` self-hosted
runner. The runner executes it before every job it accepts, and a non-zero exit
fails the job before any workflow step runs.

It answers one question the CEO asked: can chrislusf's and pingqiu's own pull
requests run on tp01 while everyone else's stay hosted? With this hook plus the
repository setting below, yes.

## What it refuses

| Situation | Outcome |
|---|---|
| `pull_request` / `pull_request_target` from a fork | refused |
| `pull_request` whose head or base repository cannot be determined | refused |
| `pull_request` targeting a different repository | refused |
| `pull_request` whose author is not on the allowlist | refused |
| `pull_request` re-run by someone not on the allowlist | refused |
| `jq` missing, payload unreadable, payload not JSON, event name unset | refused |
| `push`, `schedule`, `repository_dispatch`, `workflow_dispatch` | allowed |

The non-pull-request events are allowed because they can only run code that has
already reached a branch of this repository, and that trust boundary is
repository write access, which the hook does not need to re-check.

Login comparison is case-insensitive, since GitHub logins are.

## This is defence in depth, not the primary control

The primary control is the repository setting **Settings → Actions → General →
Fork pull request workflows → Require approval for all outside collaborators**.
That stops a fork's workflow from ever being queued, which is strictly better
than stopping it at the runner. This hook catches the cases that setting does
not: it being relaxed later, or a workflow being pointed at the self-hosted
label by mistake.

Understand the limit that follows from where the hook runs. It protects against
unauthorised code *arriving*; it cannot protect against code that is already
executing. Once any job runs as the runner account, that code can modify
anything the runner account can write — including the hook and the `.env` that
points at it, which would disable the guard for the next job. That is why the
install below makes all three root-owned and leaves the runner account with read
access only.

## Install (run as root on tp01)

Nothing here is applied by CI. Do it once, by hand.

```bash
# 1. The hook itself: root-owned, not writable by the runner account.
install -d -m 0755 -o root -g root /opt/github-runner-hooks
install -m 0755 -o root -g root job-started-guard.sh \
        /opt/github-runner-hooks/job-started-guard.sh

# 2. The allowlist, one login per line. "#" comments and blank lines are ignored.
#    Omit this file entirely to use the built-in default (chrislusf, pingqiu).
install -d -m 0755 -o root -g root /etc/github-runner
printf 'chrislusf\npingqiu\n' > /etc/github-runner/allowlist
chown root:root /etc/github-runner/allowlist && chmod 0644 /etc/github-runner/allowlist

# 3. Point the runner at it. The runner reads .env at service start.
cd /home/ghrunner/actions-runner
grep -q ACTIONS_RUNNER_HOOK_JOB_STARTED .env 2>/dev/null || \
  echo 'ACTIONS_RUNNER_HOOK_JOB_STARTED=/opt/github-runner-hooks/job-started-guard.sh' >> .env

# 4. Make .env root-owned too, so a job cannot edit the hook out of it.
#    The runner only reads this file.
chown root:root .env && chmod 0644 .env

# 5. Restart the runner service so it picks the variable up.
systemctl restart actions.runner.seaweedfs-artifactory.tp01
systemctl is-active actions.runner.seaweedfs-artifactory.tp01
```

Repeat steps 3 to 5 for every additional runner instance (`tp01-2`, `tp01-3`, …);
steps 1 and 2 are shared by all of them.

## Verify

The hook is a plain script and takes its whole input from the environment, so it
can be exercised without queueing a job:

```bash
cat > /tmp/ev.json <<'JSON'
{"pull_request":{"number":1,"user":{"login":"mallory"},
 "head":{"repo":{"full_name":"mallory/artifactory","fork":true}},
 "base":{"repo":{"full_name":"seaweedfs/artifactory"}}}}
JSON

GITHUB_EVENT_NAME=pull_request GITHUB_EVENT_PATH=/tmp/ev.json \
GITHUB_REPOSITORY=seaweedfs/artifactory GITHUB_ACTOR=mallory \
  /opt/github-runner-hooks/job-started-guard.sh; echo "exit=$?"
# expect exit=1 and a DENY line naming the fork
```

Change `full_name` to `seaweedfs/artifactory`, `fork` to `false`, and the logins
to `chrislusf` to see the allow path (`exit=0`).

Decisions are also written to the journal:

```bash
journalctl -t gh-runner-guard --since -1h
```

## Roll back

```bash
sed -i '/ACTIONS_RUNNER_HOOK_JOB_STARTED/d' /home/ghrunner/actions-runner/.env
systemctl restart actions.runner.seaweedfs-artifactory.tp01
```

The runner then accepts jobs exactly as it did before. Leaving the script and
allowlist on disk is harmless once the variable is gone.

## Current relevance

As of this change, no `pull_request`-triggered workflow in this repository
targets a self-hosted label — the tp01 migration deliberately covered only the
`repository_dispatch` and scheduled suites. So the hook denies nothing today. It
is what has to exist *before* anyone points a pull-request workflow at tp01.
