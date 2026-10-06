# Packs & Binaries

The runner is built as **one core + per-product packs**, shipped as **per-product
binaries**. You pick the binary that carries the packs you need; the all-in-one
`sw-test-runner` carries the common set.

## Binaries (`cmd/`)

| Binary | Registers | Use for |
|---|---|---|
| `sw-test-runner` | core + `block` + `kv` + `v3block` | All-in-one for block + kv + V3 block work. |
| `swblock` | core + `v3block` + iSCSI / K8s / NVMe actions | **V3 seaweed-block** (the primary block runner). |
| `weedblock` | core + `block` + `kv` | V2 seaweed-block + KV (legacy V2 line). |
| `weedv1` | core + `v1weed` | V1 weed (stub / legacy). |
| `sweeds3` | core + `s3` | **S3 gateway** testing. |
| `sweedrdma` | core + `rdma` | M01/M02 RDMA lab gates. |
| `testops-dashboard` | — (read-only viewer) | Global runs + docs view ([dashboard](dashboard.md)). |
| `testops-controller` | — (queue submit/status) | Safe RDMA queue submitter for the M01 controller-lite worker. |

> `sw-test-runner` is the common all-in-one — it does **not** include the `s3` or
> `v1weed` packs. Use `sweeds3` for S3, `sweedrdma` for RDMA, and `weedv1` for V1.

## Packs (`packs/`)

| Pack | Product |
|---|---|
| `block` | V2 seaweed-block actions |
| `v3block` | V3 seaweed-block (spawn, spec, status, replication) |
| `kv` | V2 KV object store |
| `s3` | S3 gateway (curl-against-REST, no aws-cli dep) |
| `v1weed` | V1 weed (stub) |

## Tiers

Actions are tagged with a **tier** so `list` and suites can scope them:

`core` · `block` · `devops` · `chaos` · `k8s` (via `actions.TierK8s`) · plus
product-pack tiers as plain strings (`s3`, `vfs`).

```bash
<binary> list            # all registered actions, grouped by tier
```

## Building

```bash
make build               # build every cmd/ binary into ./bin
make build-swblock       # one binary
go build ./cmd/sweeds3   # or plain go build
```

## Adding a product pack

The `s3` pack is the smallest end-to-end reference (`packs/s3/{register,actions}.go`
+ `cmd/sweeds3/main.go` + `scenarios/s3-smoke-chain.yaml`). The shape:

```go
// packs/<product>/register.go
const Tier = "<product>"
func RegisterPack(r *tr.Registry) {
    r.RegisterFunc("<product>_start_stack", Tier, startStack)
    // ... more actions
}

// cmd/<product>/main.go
func main() {
    register := func(r *tr.Registry) { actions.RegisterCore(r); pack.RegisterPack(r) }
    os.Exit(cli.Run(register, os.Args[1:]))
}
```

See the top-level `README.md` ("Adding a product pack") and
[Code Map](code-map.md) for the handler signature and helpers.
