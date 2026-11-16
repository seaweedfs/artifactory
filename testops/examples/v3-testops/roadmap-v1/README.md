# roadmap-v1

These scenarios depend on items proposed in
[`docs/scenario-spec-v1-roadmap.md`](../../../docs/scenario-spec-v1-roadmap.md)
that are not yet implemented in the runner. Do **not** try to run
them with a v0 build — they will fail at validate-time on unknown
actions or parser features.

Kept here for design conversation. Will move to `scenarios/` as the
needed primitives land:

| File | Depends on |
|---|---|
| iscsi-os-repeat.yaml | `loop` body meta-action (v2 deferred) |
| k8s-attach-detach-loop.yaml | `loop` body, `kind: Template` (v2 deferred) |
| templates/*.yaml | `kind: Template` + parameter schema (v2 deferred) |

If the team decides to ship v1.x without `loop`, these stay in
roadmap-v1/ and the corresponding bash harnesses
(`run-iscsi-os-smoke.sh`, `run-k8s-attach-detach-loop.sh`) keep
ownership of the multi-iteration logic.
