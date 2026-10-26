# Enterprise RDMA binaries (Linux x86_64)

These are additional opt-in assets. Existing normal and large-disk asset names
and their feature sets are unchanged. No ARM64 RDMA asset is supplied.

| Asset | Binary | Build features |
| --- | --- | --- |
| `weed-volume-enterprise_rdma_large_disk_linux_amd64.tar.gz` | `weed-volume` | default `5bytes` plus `rdma,rdma-dc,kvcache` |
| `sw-rdma-object-service-enterprise_linux_amd64.tar.gz` | `sw-rdma-object-service` | `mlx5-dc` (includes `real-rdma`) |

Each archive includes this README and its full mono `source-commit.txt`.
MD5 sidecars follow the existing release convention; SHA-256 sidecars are also
published for these assets. The object service is a separate executable, not
embedded in the volume server. S3 RDMA also requires a matching RDMA-aware Go
S3 gateway, its explicit opt-in configuration, and the client buffer/token
contract. Installing either archive alone does not enable S3 RDMA or KV RDMA.
The `kvcache` feature includes the slab engine; it does not enable it at runtime.

## Build

The workflow uses an Ubuntu 24.04 x86_64 runner and stable Rust, and builds both
binaries from one enterprise checkout. The default 5-byte volume offsets are
retained. From that checkout:

```sh
cd enterprise/seaweed-volume
cargo build --locked --release --target x86_64-unknown-linux-gnu --features rdma,rdma-dc,kvcache --bin weed-volume --target-dir target/rdma
cd ../rust
cargo build --locked --release --target x86_64-unknown-linux-gnu -p seaweedfs-sw-rdma-object --features mlx5-dc --bin sw-rdma-object-service --target-dir target/rdma
```

Build dependencies on Ubuntu 24.04:

```sh
sudo apt-get install build-essential pkg-config clang libclang-dev libibverbs-dev librdmacm-dev libssl-dev
```

`libibverbs-dev` includes the mlx5 headers/linker/pkg-config files on this
Ubuntu release. EFA/libfabric is not enabled by this variant.

## Customer runtime prerequisites

- Linux x86_64 with a compatible glibc. These builds target Ubuntu 24.04;
  verify `ldd` on the deployment host before starting either service.
- rdma-core userspace and provider packages. On Ubuntu 24.04 install
  `rdma-core libibverbs1 librdmacm1t64 ibverbs-providers`;
  retain the normal system runtime libraries reported by `ldd` (including
  OpenSSL if linked). Development headers and Rust are not needed at runtime.
- A functioning RDMA-capable NIC, driver/firmware and configured RDMA network.
  DC needs a supported NVIDIA/Mellanox mlx5 adapter; compiling the DC feature
  does not make an unsupported NIC DC-capable. Configure device, port and GID
  selection to match the deployment's RoCE/InfiniBand network.
- Kernel RDMA drivers (`ib_uverbs`, `rdma_cm`/`rdma_ucm`, and the NIC driver),
  accessible `/dev/infiniband/uverbs*` and `/dev/infiniband/rdma_cm` for the
  service account. Containers must expose those devices and the provider
  libraries; privilege alone does not configure the fabric.
- Sufficient locked-memory allowance for all registered buffers and slabs.
  Set the service account's memlock limits (or systemd `LimitMEMLOCK=`) to the
  planned registration budget, commonly `infinity`, and check the effective
  limit in the actual service/container. Normal memory sizing still applies.
- Reachable configured control/data endpoints and the normal master/filer
  services. For S3 direct push, configure the object service's volume DC push
  map and gateway connection to its resident Unix socket; keep the socket
  private to the gateway account. Match client token registration and lifetime
  to the selected transport. Authentication settings must agree at both ends.

`--help` and dynamic-link checks prove packaging/CLI viability, not hardware
transfer correctness, production readiness, or customer-network performance.
