# KV native LMCache connector PREVIEW bundle

`seaweedkv-lmcache-native-preview_cp312_linux_amd64.tar.gz` contains two wheels:
`seaweedkv-lmcache` (Python shim) and `seaweedkv-native` (C++ native-plugin
extension plus its bundled `libseaweedfs_sw_rdma_nixl.so`). The Rust provider
builds with `mlx5-dc`, but only **RC** is supported for this preview. Compiled DC
is unsupported and not a documented runtime path. There is no NIXL/cuObject
connector path in this preview; EFA is not enabled.

Current draft build: CPython 3.12, Linux x86_64, Ubuntu 24.04.
**Publishing is blocked pending the requested manylinux glibc baseline and
library-repair proof.** No manylinux tag is applied to the current Linux wheel. It needs compatible glibc/libstdc++ plus rdma-core runtime
libraries and mlx5 providers (Ubuntu: libibverbs1, librdmacm1t64,
ibverbs-providers). RDMA use also requires appropriate NICs, drivers/device
access, network configuration and sufficient memlock. Build/import checks do
not qualify the LMCache runtime, throughput, or hardware transfers.

LMCache runtime is pinned to **0.5.6**, and native headers to release tag v0.5.6,
commit `3e11e10812b448142c6cdb177a3058652bd826fa` (not `f2b65521`). The
candidate runtime is vLLM 0.32.0, torch 2.13.0 and CUDA 13, with the complete
ranges in `preview-pins.json`. **These runtime pins are UNVERIFIED until the
planned correctness rerun; this package build does not validate the engine pair.**

The immutable LMCache header commit, CPython ABI, Rust/cargo, gcc and exact
setuptools/wheel/pybind11/cmake versions are in `preview-pins.json`; `BUILD-MANIFEST.json` records
the exact mono source commit, header and wheel hashes, installed versions,
Python patch version, provider hash and extension hash. Only the downloaded
registry/package caches may be reused for a build; artifacts always come from
the selected source. The new job consumes the exact mono SHA output of the RDMA volume release job,
so its server counterpart is the #167 RDMA/kvcache asset built at that same
commit, **not the public non-RDMA 4.51 binary**. Existing assets are unchanged.
Each wheel includes the same `.dist-info/BUILD_INFO`, with exact build-tool
versions and source commits; repacking regenerates its RECORD hashes.

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
RC feature query, and checks dynamic linkage. It never creates a connector
or opens an RDMA device. This deliberately separates wheel importability from
runtime LMCache integration testing.
