# Developer Submit & Authoring Guide

How a developer runs a gate, and how to author a new one. Applies to all four
suites (`rdma`, `s3`, `block`, `vfs`) — they work the same way.

## 0. Two ways to run

| Path | How | Use when | Result |
|---|---|---|---|
| **CI** — submit to M01 | `POST http://192.168.1.181:9099/api/<suite>/submit` (or the 9099 panel) | a pushed, reviewable ref; team-visible; lab-locked; on the dashboard | ACCEPT / REJECT |
| **CLI** — run locally | `<runner> run <scenario> …` from any host that can SSH the lab | debug, dirty local build (`<suite>-dev`, never ACCEPT) | same bundle, your responsibility |

Both produce the **same bundle** and are validated by the **same
`scripts/qa-assert.sh`**. CI just adds the queue, the lab lease, standard metadata,
and the dashboard. M01 hosts the controller (9099) + one worker per suite; the
lab is M01 + M02.

## 1. Run an existing gate — just submit a `ref`

You do **not** write or pass a scenario. Each suite has **one canonical gate**;
you submit the thing under test:

```bash
curl -X POST http://192.168.1.181:9099/api/s3/submit \
  -H 'Content-Type: application/json' \
  -d '{"ref":"master","run_by":"you","test_id":"my-check"}'
```

What `ref` means per suite:

| Suite | `ref` is | The worker then |
|---|---|---|
| `rdma` | a **seaweed-mono** branch / SHA | checks out + builds it |
| `s3` | a **seaweedfs** (weed) branch / SHA | clones + builds weed |
| `vfs` | a **seaweedfs** (weed) branch / SHA | builds weed for the mount + S3 |
| `block` | a **published sw-block image tag** (e.g. `sha-28a99ce4f644`, `v0.5-…`) | installs that image (no source build) |

The ref becomes `__tested_ref` / `__tested_sha` in the bundle envelope and is
checked by `qa-assert --ref`, proving the bundle tested what you submitted.

**Watch + accept:**

```bash
http://192.168.1.181:9099/?project=<suite>-ci        # dashboard
curl -s "http://192.168.1.181:9099/api/controller/status?suite=<suite>"   # queue/running/done
# the worker runs qa-assert; a run that reaches "done" already passed it
```

## 2. Run locally (debug / dirty)

Dirty or uncommitted work must **not** become CI evidence. Run via the CLI to a
`*-dev` project:

```bash
sweeds3 run scenarios/s3-smoke-chain.yaml \
  -results-dir /mnt/smb/work/share/testops/results/s3-dev \
  -meta project=s3-dev -meta run_by=you -meta dirty=true
```

Same bundle contract; DEBUG ONLY, never ACCEPT. See the per-product runbooks.

## 3. Add a NEW gate — write a scenario (once)

Writing a scenario YAML is a one-time onboarding, **not** a per-run step. A gate is
a first-class platform citizen when it has the five contract items
([Product Contract §2](../control-plane-product-contract.md)):

1. a **runner + pack** (reuse `sweeds3`/`swblock`/`sweedrdma`, or a new pack);
2. a **scenario** (`scenarios/<name>-chain.yaml`) — build → seed → test → cleanup,
   self-contained, zero-residue, ending in `emit_provenance`;
3. a **qa-profile** (`docs/qa-profiles/<suite>.expect`) with the product floors;
4. a **result project** (`<suite>-ci` / `-dev`);
5. a **worker** (`scripts/run-<suite>-ci.sh` + a `testops-<suite>-ci-worker.service`)
   to enable 9099 submit.

Emit the envelope at the end of a passing test — one action:

```yaml
- action: emit_provenance
  product: <suite>
  tested_ref: "{{ ref }}"
  tested_sha: "{{ built_sha }}"          # stamped by the build, NOT runner-cwd git
  __<suite>_<row>: "{{ value }}"         # product perf / correctness rows
```

Then the shared `qa-assert.sh --profile docs/qa-profiles/<suite>.expect` accepts it.
`scenarios/vfs-cross-access-chain.yaml` + `docs/qa-profiles/vfs.expect` are the
smallest worked reference.

## 4. Define the topology (service ↔ client)

The scenario's `topology.nodes` maps a **name → machine**; an action runs on a node
via `node: <name>`. There is **no role field** — a node's role (service, client,
primary, replica) is just *which actions target it*.

```yaml
topology:
  nodes:
    m01: { host: 192.168.1.181, user: testdev, key: "{{ ssh_key }}" }   # client
    m02: { host: 192.168.1.184, user: testdev, key: "{{ ssh_key }}" }   # service

phases:
  - name: setup           # service side: bring up the server on m02
    actions:
      - action: s3_start_stack
        node: m02
        ip: 192.168.1.184                     # advertise a REACHABLE ip, not 127.0.0.1
  - name: mount           # client side: connect from m01
    actions:
      - action: exec
        node: m01
        cmd: "weed mount -filer=192.168.1.184:8888 -dir=/tmp/mnt ..."
  - name: test            # cross-node: write on service, read on client
    actions:
      - action: exec
        node: m02
        cmd: "... put object ...; sha256sum obj"
        save_as: put_sha
      - action: exec
        node: m01
        cmd: "... read via mount ...; [ \"$(sha256sum file)\" = \"{{ put_sha }}\" ]"
```

Rules that bite:

- **Advertise a reachable IP.** A service bound to `127.0.0.1` is invisible to the
  other node. Pass the node's LAN/RDMA IP (`-ip=192.168.1.184`).
- **Pass values across nodes** with `save_as` on one node + `{{ var }}` on another —
  the runner threads variables between nodes for you.
- **`{{ ssh_key }}`** comes from `env`. Under CI the worker overrides it to the M01
  linux key (`-env ssh_key=/home/testdev/.ssh/id_ed25519`); the scenario's default
  can be a Windows path for local CLI runs.
- **Stateful is fine** — express it as phases: `setup → mount → test → unmount`
  with the teardown phase `always: true`.

Full field reference: [Scenario Syntax](syntax.md). Worked cross-node example:
`scenarios/vfs-cross-access-chain.yaml` (service on m02, client on m01).

## 5. Cheat sheet

```bash
# run an existing gate (CI)
curl -X POST http://192.168.1.181:9099/api/<suite>/submit -d '{"ref":"<ref>","run_by":"you"}'
# watch
open http://192.168.1.181:9099/?project=<suite>-ci
# accept a bundle yourself
bash scripts/qa-assert.sh <bundle> --ref <ref> --profile docs/qa-profiles/<suite>.expect
# run locally (debug)
<runner> run <scenario> -results-dir results/<suite>-dev -meta project=<suite>-dev -meta dirty=true
# add a gate: scenario (+ emit_provenance) -> qa-profile -> run-<suite>-ci.sh -> worker service
```
