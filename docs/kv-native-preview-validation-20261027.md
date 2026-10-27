# KV native preview local build proof, 2026-10-27

Code: artifactory candidate `4c1474d4f4b400d0fa91b1e9c1d7e7d4b7ca148b`.
Mono: `40bbf02aeccb4880cea2612c057cd273e668bfb9`.
LMCache headers: v0.5.6, `3e11e10812b448142c6cdb177a3058652bd826fa`.

Under grant `6ae0360ca3b2a891b6b3e41a`, canonical build lease
`codex03-kv-native-build-20261027T054306Z` acquired at 05:43:06Z and released
at 05:45:12Z, elapsed 125.25 s, outcome BUILT_IMPORT_VERIFIED. The ledger
release was read back; the SSH flock holder exited before the release record.
Root retained: `/data/nvme/build/codex03/kv-native-preview-20261027`.
No connector instance, RDMA device or server was opened. No workflow dispatch,
CI, merge or release publication occurred.

The checked-in cargo/pip build commands passed on M02 Ubuntu24.04, with fresh
source/target directories (only the downloaded Cargo registry cache copied).
The corrected v0.5.6 headers were used; the earlier f2b65521 candidate was not.

| Wheel | Bytes | SHA-256 |
| --- | ---: | --- |
| `seaweedkv_lmcache-0.1.0-py3-none-any.whl` | 177,635 | `e9a27322de4423035079ae7ff1d231f9cd9f088b68bcd06ea483fb95741d145e` |
| `seaweedkv_native-0.1.0-cp312-cp312-linux_x86_64.whl` | 4,618,210 | `18072bf9d56c70f061d72f3fc171d79cc10838fae60802c13ee400403b63ec18` |

Both wheels install with --no-deps into a fresh source-free CPython3.12 venv and
import successfully. Module paths are verified inside that venv. The native
wheel includes the Rust provider and extension; provider bytes match the wheel.
The static feature query exposes RC. No connection/transfer was exercised.
`ldd` resolves libibverbs, librdmacm, libmlx5 and the system dependencies without
missing libraries. Both wheel RECORDs and their identical BUILD_INFO were also
checked locally after copying the artifacts to the evidence share.

BUILD_INFO records the actual checked versions: pip25.0.1, setuptools80.9.0,
wheel0.45.1, pybind11 2.13.6, cmake3.31.6, rustc/cargo1.93.0 and gcc13.3.0;
Python is 3.12.3. The native provider SHA-256 is
`572e3e675b74dbbbce6895099918df22dddba81809e03c45428ccdf1e65665f0`.

The copied wheels were bundled offline on the evidence share with the same
member layout as the workflow tar step (the native build/import verification
ran on M02). This is not a second host build or a released asset.

**Current scope:** decision `6ae03830` accepts linux_x86_64 / Ubuntu24.04 / glibc
>=2.39 release assets, not PyPI; manylinux is deferred to GA. RDMA libraries must
remain system-provided at >=the builder's version. The original build proof
above is unchanged. Decision `6ae03c93` additionally permits system OpenSSL 3,
libstdc++ and libgcc_s; no bundling or static linking. The approved allowlist now
accepts the captured DT_NEEDED observations offline. Unknown DSOs and bundled
system-library copies are refused by local controls. Exact ELF observations are
in `build-proof/dt-needed-observed.json`. BUILD_INFO generation and fixture-wheel
RECORD tests include Ubuntu package prerequisites. The original built wheels
predate those metadata additions: this is offline policy/fixture validation,
not a second host build or a claim that their BUILD_INFO already has the additions.
Only RC is supported in this preview; DC is compiled but unsupported. The
vLLM0.32.0/LMCache0.5.6/torch2.13.0/CUDA13 runtime matrix remains UNVERIFIED
pending the correctness rerun; LMCache/vLLM/GPU runtime was not installed or run.
The matching preview volume still must be built at the manifest's exact mono
commit using the #167 RDMA/kvcache job, not the public 4.51 binary. The workflow
now consumes that job's actual SHA, ensuring future bundle/server identity.

Raw logs, wheels, BUILD_INFO, source/header digests, ldd, and lease records:
`V:/share/rdma/kv-native-preview-codex03-20261027/build-proof/`.

## Review amendment controls (local only)

- R1: direct DT_NEEDED for BOTH installed ELFs is checked before any package
  import/load; wheel contents are checked for bundled system libraries. Unknown
  direct SONAMEs (including extension-only libUNAPPROVED.so.7), missing/unresolved
  dependencies and bundled RDMA variants refuse. Resolved transitive libnl is
  retained as system prerequisite evidence, not compared to the direct allowlist.
- S1: tag-keyed workflow serialization plus checked source/digest pairing.
  CPU model exposes 2 mixed final pairs among 6 unconstrained upload orders;
  serialized complete runs avoid them. Digest/source mutations, stale manifest
  after partial overwrite, missing assets and wrong build digests refuse.
- R2: both wheels retain PREVIEW, platform, release-only/not-PyPI, system
  prerequisites, RC-only and runtime UNVERIFIED. Missing/changed policy negatives
  refuse; actual captured wheels were repacked offline with new metadata and
  RECORDs verified, with every ELF byte unchanged. This is not a rebuild/install.
  Receipts: `review-amendment/restamp-receipt.json` beside the original evidence.
  Shim: b42bd1781e38dec3c96853daf70df35a96d85d606b027c87ee3075515eafc102.
  Native: c9684632f44390729858cc6451824ac709812f079334d760c1f1f0a45f928e0f.
  Repack uses the observed rdma-core upstream floor 50.0, not a new observation
  of Ubuntu package patch revisions. Future builder stamps query exact versions.

Eight tests across linkage, metadata/RECORD and pairing pass locally; actionlint
and diff checks pass. Native runtime/real release publication remain untested.
