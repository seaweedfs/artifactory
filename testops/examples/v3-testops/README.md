# V3 testops draft

Two layers, matching the spec layout:

```
scenarios/         — v0-current; uses only actions in today's registry.
                     QA can run these on iscsi/frontend-completeness
                     after sw-test-runner build, side-by-side with the
                     bash harnesses they translate.

roadmap-v1/        — assumes v1-roadmap primitives (go_build,
                     docker_build, ctr_load, assert_no_active_iscsi_sessions).
                     Kept for design conversation; do NOT run today.
```

## What translates 1:1 to v0 today

| Bash script | v0 scenario |
|---|---|
| scripts/run-iscsi-os-smoke.sh (fio mode, single iter)   | scenarios/iscsi-os-fio.yaml |
| scripts/run-k8s-alpha-fio.sh (modulo manifest rendering) | scenarios/k8s-alpha-fio.yaml |

The two v0 scenarios cover the **client-side / post-image-build**
half of each bash harness. The setup half (build images, render
templated manifests with sed) stays in the bash scripts until v1's
build primitives land. This is intentional: the goal of the v0 pass
is to prove the runner can drive the steady-state test loop, not to
absorb every line of bash on day one.

## What is parked in roadmap-v1/

| File | Reason |
|---|---|
| roadmap-v1/iscsi-os-repeat.yaml | needs `loop` body or repeated full-cycle phases — beyond v0's `Phase.Repeat` (which aggregates metrics, not full-cycle logic) |
| roadmap-v1/k8s-attach-detach-loop.yaml | same loop dependency |
| roadmap-v1/templates/                  | uses `kind: Template` + parameter schema (v2-only per roadmap) |

## Run

```bash
sw-test-runner validate examples/v3-testops/scenarios/iscsi-os-fio.yaml
sw-test-runner run      examples/v3-testops/scenarios/iscsi-os-fio.yaml
```

## Open work to make these end-to-end native

1. v1 build primitives (`go_build`, `docker_build`, `ctr_load`) so
   the scenario owns image build instead of pre-running
   `scripts/build-alpha-images.sh`.
2. v1 `assert_no_active_iscsi_sessions` so the iSCSI-clean
   assertion stops being an inline `exec`.
3. Manifest rendering (substitute NODE_NAME, image tags, owner-ref
   flags). Either a new `render_manifest` action OR a build step
   that emits ready-to-apply YAML into the run-bundle.
4. `loop` body, only when more than two scenarios actually want it.

Items 1–2 give the most coverage per LOC; item 3 has the biggest
single payoff because it removes the install-k8s-alpha.sh
sed/awk surgery; item 4 is parked.
