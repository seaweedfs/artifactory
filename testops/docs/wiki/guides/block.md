# Block (iSCSI / NVMe)

Testing **seaweed-block** — the iSCSI/NVMe block target, HA, lease/epoch fencing,
and recovery.

**Binary:** `swblock` (core + `v3block` + iSCSI/K8s/NVMe actions), or the
all-in-one `sw-test-runner`.

## Quick start

```bash
make build-swblock           # or: go build ./cmd/swblock

# smoke
swblock run scenarios/public/smoke-iscsi.yaml -env <env.yaml> \
  -results-dir /mnt/smb/work/share/testops/results/block-qa

# end-to-end
swblock run scenarios/public/e2e-block.yaml -env <env.yaml> \
  -results-dir /mnt/smb/work/share/testops/results/block-qa
```

## What you can exercise

| Area | Public scenarios |
|---|---|
| Smoke | `smoke-block-api`, `smoke-iscsi` |
| End-to-end | `e2e-block`, `e2e-block-auto`, `e2e-combined-auto` |
| HA / failover | `ha-failover`, `ha-full-lifecycle`, `ha-io-continuity`, `ha-rebuild`, `cp11b3-*` |
| Fault injection | `fault-disk-full`, `fault-netem`, `fault-partition` |
| Consistency / lease | `consistency-epoch`, `consistency-lease`, `lease-expiry-write-gate`, `lease-renewal-under-io` |
| Recovery | `crash-recovery`, `diag-restart-recovery` |

## Action tiers in play

`core` (exec/assert/ssh), `block` (iSCSI login, io: dd/mkfs/mount/fsck, nvme
identify/ANA/connect, metrics scrape), `chaos` (netem, partition, kill, fill
disk), `k8s` (kubectl apply/wait/logs) for the CSI path.

See the [Handbook](../../testops-handbook.md) for env access and the
[Scenario Catalog](../scenario-catalog.md) for the full list.
