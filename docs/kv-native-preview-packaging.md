# KV native LMCache connector PREVIEW bundle

`seaweedkv-lmcache-native-preview_cp312_linux_amd64.tar.gz` contains two wheels:
`seaweedkv-lmcache` (Python shim) and `seaweedkv-native` (C++ native-plugin
extension plus its bundled `libseaweedfs_sw_rdma_nixl.so`). The Rust provider
uses `mlx5-dc`, which includes real RDMA RC/DC; EFA is not enabled.

This is a CPython 3.12, Linux x86_64, Ubuntu 24.04 build, not a portable
manylinux wheel. It needs compatible glibc/libstdc++ plus rdma-core runtime
libraries and mlx5 providers (Ubuntu: libibverbs1, librdmacm1t64,
ibverbs-providers). RDMA use also requires appropriate NICs, drivers/device
access, network configuration and sufficient memlock. Build/import checks do
not qualify the LMCache runtime, throughput, or hardware transfers.

The immutable LMCache header commit, CPython ABI, Rust toolchain and Python
build-tool versions are in `preview-pins.json`; `BUILD-MANIFEST.json` records
the exact mono source commit, header and wheel hashes, installed versions,
Python patch version, provider hash and extension hash. Only the downloaded
registry/package caches may be reused for a build; artifacts always come from
the selected source. Existing release assets are unchanged.

Use the accompanying preview install guide and the LMCache source commit in
`preview-pins.json` when preparing the runtime environment. Install these two
wheels with that pinned environment, not an unconstrained upgrade of LMCache. The native wheel's existing metadata allows LMCache broadly;
that is not a compatibility promise beyond the manifest's header pin. Use the
matching server/source release and preserve the runtime environment pins. The
bundle alone is not a full vLLM/LMCache installation.

To locate the shared provider for the shim after wheel installation:

```python
from importlib.resources import files
library = files("seaweedkv_native") / "libseaweedfs_sw_rdma_nixl.so"
```

The release job installs both wheels into a fresh, source-free venv using
`--no-deps`, imports both packages, verifies the bundled provider bytes and its
RC/DC feature query, and checks dynamic linkage. It never creates a connector
or opens an RDMA device. This deliberately separates wheel importability from
runtime LMCache integration testing.
