# Open-Core TestOps Roadmap

## Intent

`sw-test-runner` should become the open, reproducible test execution layer for
SeaweedFS and SeaweedFS-adjacent distributed storage testing.

The long-term product shape is open-core:

- Open source: local and SSH-based scenario execution, structured evidence, and
  basic SeaweedFS examples.
- Enterprise/private: distributed controller/agent operation, fleet scheduling,
  cloud burst, governance, dashboards, and the full internal scenario corpus.

This split gives the community a real tool while preserving the operational
test bed and release intelligence that hardens the SeaweedFS product family.

## Core Model

The platform should keep these concepts separate:

- Scenario YAML: the product contract describing what must be proven.
- Action registry: the executable capability set known by the runner.
- Runner/controller: schedules phases, records status, and validates bundles.
- Agent: owns execution near the resources when distributed mode is used.
- Bundle: immutable evidence for a run.

The expected operator flow is:

```text
write scenario -> run scenario/suite -> collect bundle -> validate bundle -> compare trend/regression
```

The expected product-team flow is:

```text
feature -> scenario contract -> field-level assertions -> release gate -> repeatability validation
```

## Open Source Scope

The open-source runner should be useful without the enterprise layer.

Open-source scope:

- Scenario YAML schema and parser.
- Local runner.
- SSH runner.
- Native suite chaining.
- Basic run-control files:
  - `status.json`
  - `result.json`
  - `manifest.json`
  - `provenance.json`
- Bundle validator.
- Basic cancellation between phases.
- Basic artifact collection.
- Basic action registry:
  - `exec`
  - `grep_log`
  - `assert_equal`
  - `assert_greater`
  - `assert_contains`
  - `collect_path`
  - process/session cleanup primitives
- Basic SeaweedFS smoke scenarios.
- Minimal examples for local, SSH, and single-node lab testing.
- Documentation for writing scenarios and interpreting bundles.

The open promise should be:

```text
You can define scenarios, run them locally or over SSH, collect structured
evidence, and validate the result.
```

## Enterprise / Private Scope

The enterprise layer should focus on scale, governance, and operational control.

Private or commercial scope:

- Long-running remote agent.
- Controller service.
- Agent fleet management.
- SW KV/Filer/FUSE-backed control plane.
- Multi-tenant scheduling.
- Lab topology inventory and reservation.
- Cloud burst provision/run/collect/destroy.
- Secret management.
- RBAC, audit, and team workflows.
- Long soak scheduling.
- Cost controls.
- Dashboards and release status views.
- Flaky-test analytics and history.
- Performance trend baselines.
- Full internal SeaweedFS scenario corpus.
- Full release-gate suites and qualification matrix.

The enterprise promise should be:

```text
You can run validated release gates continuously across labs and clouds, manage
resources and teams, trend results, and preserve evidence at scale.
```

## Scenario Corpus Boundary

Open a curated scenario set, not the entire internal test bed.

Open scenarios:

- Basic SeaweedFS smoke tests.
- Small protocol examples.
- Minimal CSI/Kubernetes examples.
- Simple local/SSH templates.
- Reproducibility examples for public bugs.

Private scenarios:

- Full release gates.
- iSCSI/NVMe compatibility matrix.
- CSI matrix.
- Chaos matrix.
- Long soak.
- Performance baselines.
- Cloud-scale scenarios.
- Hard-earned regression scenarios from internal incidents.

This keeps the public project credible while preserving the private testing
advantage.

## Controller / Agent Direction

The open SSH runner is enough for small labs and current developer workflows.
The agent model is needed when runs must survive controller disconnects or when
execution must stay local to hardware/cloud resources.

Future controller responsibilities:

- Submit run requests.
- Track latest and historical run status.
- Validate completed bundles.
- Cancel runs.
- Assign runs to agents.
- Maintain product/runner provenance.

Future agent responsibilities:

- Watch or receive assigned runs.
- Claim runs with leases.
- Stage pinned inputs locally.
- Verify input checksums.
- Execute scenario phases.
- Own cleanup.
- Maintain heartbeat and pidlog.
- Handle cancel at safe points.
- Upload curated evidence.

The agent should not use shared storage as a dumping ground. Local disk remains
the hot execution scratch space. Shared storage carries operational metadata,
pinned inputs, manifests, compact status, and curated artifacts.

## SW-Backed Control Plane

SeaweedFS can be used as the operational substrate for TestOps.

Recommended split:

```text
KV:
  run queue
  lease / claim
  heartbeat
  phase status
  cancel signal
  result pointer

Filer / Object:
  pinned binaries
  scenario snapshots
  curated artifacts
  result bundles

FUSE:
  convenience access for agents and operators
```

Avoid:

- Unbounded stdout/stderr streams in KV.
- High-churn temp files on FUSE.
- Large binaries directly in KV.
- Making shared storage the live workload scratch path unless the scenario is
  explicitly testing that behavior.

This dogfoods SeaweedFS while keeping the TestOps control plane reliable.

## Cloud Burst Direction

A cloud-backed run should be disposable.

Target flow:

```text
0-3 min     provision instances or Kubernetes
3-5 min     install agent and stage pinned inputs
5-17 min    run scenario or suite
17-19 min   collect and validate bundle
19-20 min   destroy cluster
```

The durable product is the bundle, not the cluster.

Cloud actions should eventually cover:

- Provision VMs or Kubernetes.
- Deploy agents.
- Register agents with controller.
- Run suites.
- Collect bundles.
- Validate bundles.
- Destroy infrastructure.

## Product Family Gates

The same platform should harden multiple SeaweedFS surfaces.

Protocol release gate:

- iSCSI ALUA failover.
- NVMe ANA multipath failover.
- CSI protocol selection.
- Compatibility soak.

Object/filer release gate:

- S3 write/read/list/delete.
- Multipart upload.
- Filer consistency.
- Volume compaction.
- Erasure coding.

Kubernetes release gate:

- Operator install.
- Upgrade/rollback.
- PVC lifecycle.
- Pod disruption.
- Node drain.

Cloud release gate:

- Deploy cluster.
- Run mixed workload.
- Collect metrics.
- Validate bundle.
- Destroy cluster.

## Milestones

### M1: Open Runner Foundation

- Keep scenario YAML stable.
- Keep local and SSH execution reliable.
- Maintain native suite chaining.
- Maintain run-control files.
- Maintain bundle validation profiles.
- Publish basic SeaweedFS examples.

### M2: Agent MVP

- Add single-node `testops-agent`.
- Support shared-drive request queue first.
- Add heartbeat and pidlog.
- Support phase-boundary cancel.
- Preserve current bundle schema.
- Validate with m01/m02 back-to-back repeatability runs.

### M3: SW Control Backend

- Add backend interface for run queue and metadata.
- Implement filesystem backend.
- Implement SW KV/Filer backend.
- Keep local filesystem as bootstrap/debug fallback.

### M4: Cloud Burst

- Add cloud provision/destroy actions.
- Run short-lived release gates.
- Store bundles durably.
- Validate cost and cleanup discipline.

### M5: Enterprise Control Plane

- Add controller service.
- Add scheduling and lab inventory.
- Add dashboard.
- Add RBAC/audit.
- Add trend and flaky-test analytics.

## Non-Goals For The Open Core

- Full multi-tenant SaaS controller.
- Fleet scheduling.
- Secret vault.
- Hosted dashboard.
- Complete SeaweedFS internal release matrix.
- All internal regression scenarios.
- Performance trend service.
- Cloud cost governance.

These belong in the enterprise/private layer.

## Guiding Principle

The open runner should make individual tests reproducible.

The enterprise platform should make release confidence operational at scale.
