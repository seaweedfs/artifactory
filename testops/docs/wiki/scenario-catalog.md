# Scenario Catalog

Scenarios are YAML files describing `build → seed → test → cleanup`. They live
under `scenarios/`:

| Dir | What |
|---|---|
| `scenarios/public/` | The **documented, supported set** — start here. |
| `scenarios/internal/` | Advanced / in-progress scenarios (chaos, perf, recovery, NVMe). |
| `scenarios/templates/` | Reusable YAML snippets (e.g. block-crud, kv-write-verify). |
| `scenarios/` (root) | Older / migrating scenarios. |
| `scenarios/s3-smoke-chain.yaml` | The S3 reference smoke (see [S3 guide](guides/s3.md)). |

## Public set (`scenarios/public/`)

| Group | Scenarios |
|---|---|
| **Smoke** | `smoke-block-api`, `smoke-iscsi`, `smoke-kv` |
| **End-to-end** | `e2e-block`, `e2e-block-auto`, `e2e-kv`, `e2e-kv-auto`, `e2e-combined-auto` |
| **HA / failover** | `ha-failover`, `ha-full-lifecycle`, `ha-io-continuity`, `ha-rebuild`, `ha-restart-recovery`, `cp11b3-auto-failover`, `cp11b3-fast-reconnect`, `cp11b3-manual-promote` |
| **Fault injection** | `fault-disk-full`, `fault-netem`, `fault-partition` |
| **Consistency / lease** | `consistency-epoch`, `consistency-lease`, `lease-expiry-write-gate`, `lease-renewal-under-io` |
| **Recovery** | `crash-recovery`, `diag-restart-recovery` |

## List · validate · run

```bash
<binary> list                          # registered actions (by tier)
<binary> validate scenarios/public/e2e-block.yaml   # static check, no execution
<binary> run scenarios/public/e2e-block.yaml -env <env.yaml> \
    -results-dir /mnt/smb/work/share/testops/results/<project>
```

!!! note "flag order"
    `-env` is a stdlib flag and must come **before** the scenario path. On
    Windows/MSYS, prefix `MSYS_NO_PATHCONV=1` so `/tmp/...` env values aren't
    mangled. See the [Handbook](../testops-handbook.md) gotchas.

## Authoring

The scenario contract — self-contained, zero-residue, `cleanup` with
`always: true` — is in the [Standard](../cross-product-testops-standard.md);
the field-by-field schema is the [Scenario Spec](../scenario-spec.md). Every new
scenario should clean up after itself so the lab and the
[janitor](dashboard.md#disk-hygiene) stay quiet.
