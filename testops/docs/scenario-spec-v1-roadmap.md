# sw-test-runner Scenario Spec — v1 Roadmap

Lists **proposed** additions on top of [v0](scenario-spec.md). Strictly
additive: every v0 scenario keeps parsing and running through every
v1 runner version without modification.

Status legend: 🟢 ready to implement • 🟡 design pending • 🔴 v2-only,
do **not** implement now.

## Acceptance bar for v1

A change is in v1 only if it is:

- additive (new key, new action, new artifact file) — never repurposing
  an existing v0 field;
- backward compatible — `validate` and `run` of a v0 scenario produce
  identical artifacts (modulo additions like `provenance.json`);
- usefully isolatable — can be merged in a single small PR without a
  parser rewrite.

Anything that would change `Scenario.Phases[].Actions[].SaveAs`
semantics, introduce typed phase outputs, or change include-template
parameter shape is out of scope here. See "v2 deferred" below.

## First batch (🟢 ready)

These are the only items I propose implementing for v1.0.

### 🟢 New core actions

| Action | Tier | Inputs | Output (saved via `save_as`) |
|---|---|---|---|
| `go_build` | TierCore | `cwd`, `package`, `out` | output path |
| `docker_build` | TierCore | `cwd`, `dockerfile`, `tag`, optional `build_args` | image name |
| `ctr_load` | TierCore | `runtime` (`k3s`/`docker`/`kind`), `images: [tag,...]` | imported image list (joined) |
| `image_digest` | TierCore | `image`, `pull: false` | image digest (sha256) |
| `assert_no_active_iscsi_sessions` | TierBlock | `node` optional | (none; fails on session present) |
| `provenance_write` | TierCore | (no inputs) | path to `provenance.json` |

Notes:

- `go_build` must invoke `go build` with `cwd` set; it is **not** a
  cross-compile primitive (use `exec` for that).
- `docker_build` shells out to `docker build` (or `nerdctl build` if
  `runtime: nerdctl`); BuildKit-specific flags go through `exec`.
- `ctr_load` for `runtime: k3s` does `docker save | sudo k3s ctr
  images import -`; for `kind` does `kind load docker-image`.
- `assert_no_active_iscsi_sessions` runs `iscsiadm -m session` and
  succeeds iff the command reports no sessions; this collapses the
  `exec iscsiadm | grep | assert_status` pattern that scenarios
  currently repeat.
- `provenance_write` is the action form; it can also fire
  automatically at the end of every run (see below).

### 🟢 Provenance file (additive run-bundle artifact)

In addition to the existing `manifest.json`, the runner writes
`provenance.json` once at run end:

```json
{
  "run_id": "20260505-080105-ab12",
  "scenario": { "name": "...", "sha256": "..." },
  "framework_version": "0.x.y",
  "git": { "repo": "seaweed-block", "sha": "9a1fe07...", "dirty": false },
  "host": { "name": "m02", "kernel": "6.17.0-19-generic", "os": "Ubuntu 24.04.3 LTS" },
  "images": [
    { "tag": "sw-block:local", "digest": "sha256:8865ebbb..." }
  ],
  "binaries": [
    { "path": "bin/blockvolume", "sha256": "abcd..." }
  ]
}
```

- `images` is populated from any `docker_build` / `image_digest`
  action in the run.
- `binaries` is populated from any `go_build` action in the run.
- `git.dirty` is `git diff --quiet` exit status.
- v0 scenarios that don't use the new build actions still get a
  minimal provenance.json (just `run_id`, `scenario`, `git`, `host`).

This is just an extra file; it does not replace `manifest.json` or
change `result.json`.

### 🟢 Redaction discipline

Both `manifest.json` and `provenance.json` redact any field whose key
matches `(?i)secret|password|token|chap|key`. Same rule applies to:

- `env:` values written into a future scenario dump (when added);
- `Action.Params` values surfaced in `result.json`;
- artifact-collection paths matched against the same key list.

The redacted form is `"***"` (constant string), not removal — so the
field's *presence* remains evidence in the bundle.

### 🟢 Mutating-action gate

A small set of registered actions must be marked `Mutating: true` in
the registry. The runner refuses to execute them unless invoked with
`--allow-mutating`. Initial mutating set:

- `docker_push` (when added — not in v1.0 first batch)
- any `kubectl_delete*` against shared production namespaces
  (gated by namespace allowlist, not action name)
- any chaos action that affects shared infrastructure

This is opt-in per action via a registry flag; existing actions stay
non-mutating by default. Local dev / lab runs pass `--allow-mutating`
once and forget; CI defaults to refusing.

### 🟢 Schema validation up front

`sw-test-runner validate` extends to fail-fast on:

- unknown action name (already done);
- known action with required params missing (new — actions register
  a `RequiredParams` list);
- `node:` referencing a node not in `topology.nodes`;
- `target:` referencing a target not in `scenario.targets`;
- `include:` parameter referenced via `{{.params.X}}` not in
  `include_params`.

No new wire-format keys; this is purely a stricter parser pass.

### 🟢 Action namespace policy (documentation only)

Document and lint the rule already in practice:

- pack-registered actions use `<product>_*` prefix;
- core actions never use a product prefix;
- `validate` warns (not fails) on a non-prefixed pack-registered
  action.

## Second batch (🟡 design pending)

Move to v1.1+ once the first batch lands and the team has used it for
two months on real scenarios.

### 🟡 `apiVersion` opt-in marker

Allow `apiVersion: testrunner.io/v1` at the top of a scenario as a
**marker**, not a behavior change. Used for:

- enforcing the stricter validation pass (v0 scenarios get the
  permissive pass; v1 scenarios must validate cleanly);
- gating future capability gates that are unsafe to apply to v0
  scenarios.

When introduced, must default to *enabling* validation for new
scenarios but never breaking v0 ones.

### 🟡 Per-action `RequiredParams` registration

Required for the schema-validation feature above. Each
`RegisterFunc` gains a `WithRequired([]string)` decorator. This is
internal API churn, not wire-format change.

### 🟡 `iscsi_session_count` / `kubectl_count` style helpers

If we find we keep writing `exec ... | wc -l | assert_equal`
patterns, promote to first-class actions. Don't add until we have
3+ scenarios doing the pattern.

### 🟡 Suite-level `vars` cascade

`Suite` (already in v0 for ordering scenarios) gains a `vars:` block
that downstream scenarios can read via `{{.suite.X}}`. Useful for
pinning a single image digest across a multi-scenario run.

## v2 deferred (🔴 do not start)

These would change the wire format or runner core. Park here so the
v0/v1 path stays clean.

- 🔴 `apiVersion: testrunner.io/v2` + `kind:` discriminator.
- 🔴 First-class `Template` kind with parameter schemas
  (type/required/default).
- 🔴 Typed phase outputs (`{{.phases.X.outputs.Y}}`) — replaces
  flat `save_as` namespace.
- 🔴 `loop` body meta-action with `iter.index` / `iter.n`.
- 🔴 Top-level `cleanup:` field (replacing `Phase.Always: true`).
- 🔴 Plugin/subprocess action protocol (CSI/CNI-style).
- 🔴 BuildKit / OCI advanced features (cache mounts, multi-arch
  fan-out).

Each v2 item has cross-cutting blast radius (parser rewrite,
template engine change, registry refactor). Bundle them when there's
a real cross-product driver — likely tied to a "publish standalone
v1.0 binary" milestone.

## Compatibility commitment

- v0 scenarios continue to parse and run unchanged across every
  v1.x release.
- v1 features are gated behind opt-in (a new action used; a new
  artifact written). A scenario can ignore them and behave exactly
  as in v0.
- The runner's CLI (`run`/`validate`/`list`/`suite`/`coordinator`/
  `agent`) keeps stable subcommand names and flags through v1.x.
- v2 will require parallel-support: at least one minor cycle where
  both v1 and v2 parse paths run side-by-side, with conversion
  guidance, before v0/v1 retires.

## What I will not commit to in v1

- typed outputs / `{{.phases.X.outputs.Y}}`;
- template parameter schemas;
- top-level `cleanup:` block;
- automatic build-on-source-change;
- distinct `kind:` namespace.

If a scenario actually needs one of these, it is a signal to widen
the v1 scope conversation — not to slip a partial implementation in.
