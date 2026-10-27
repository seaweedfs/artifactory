#!/usr/bin/env bash
# Builds only; no device opens or server/connector instances.
set -euo pipefail
mono=$(realpath "${1:?mono checkout}")
lmcache=$(realpath "${2:?LMCache checkout}")
out=$(realpath -m "${3:?output directory}")
scripts=$(cd -- "$(dirname -- "$0")" && pwd)
pins="$scripts/../kv-native-preview-pins.json"
python=${PYTHON:-python3.12}
mkdir -p "$out"
"$python" -I - "$pins" "$mono" "$lmcache" "$out" <<'PY'
import json, pathlib, subprocess, sys
pins, mono, lmcache, out = map(pathlib.Path, sys.argv[1:])
p = json.loads(pins.read_text())
assert p['provider_features'] == 'mlx5-dc' and p['python_abi'] == 'cp312'
assert sys.version_info[:2] == tuple(map(int, p['python_version'].split('.'))), sys.version
assert subprocess.check_output(['git','-C',str(lmcache),'rev-parse','HEAD'],text=True).strip() == p['lmcache_commit']
assert subprocess.check_output(['rustc','--version'],text=True).split()[1] == p['rust']
assert subprocess.check_output(['cargo','--version'],text=True).split()[1] == p['cargo']
assert subprocess.check_output(['gcc','-dumpfullversion'],text=True).strip() == p['gcc']
for repo in (mono, lmcache):
    assert not subprocess.check_output(['git','-C',str(repo),'status','--porcelain','--untracked-files=no']), f'dirty source: {repo}'
(out/'build-requirements.txt').write_text(''.join(f'{n}=={p[n]}\n' for n in ['pip','setuptools','wheel','pybind11','cmake']))
(out/'preview-pins.json').write_bytes(pins.read_bytes())
PY
"$python" -m venv "$out/build-venv"
"$out/build-venv/bin/python" -m pip install --disable-pip-version-check -r "$out/build-requirements.txt"
export PATH="$out/build-venv/bin:$PATH"
export CC=gcc CXX=g++ CARGO_TARGET_X86_64_UNKNOWN_LINUX_GNU_LINKER=gcc
(cd "$mono/enterprise/rust" && cargo build --locked --release --target x86_64-unknown-linux-gnu -p seaweedfs-sw-rdma-nixl --features mlx5-dc --lib --target-dir target/kv-native-preview)
export LMCACHE_ROOT="$lmcache"
export SW_RDMA_NIXL_LIB_DIR="$mono/enterprise/rust/target/kv-native-preview/x86_64-unknown-linux-gnu/release"
mkdir -p "$out/wheels"
"$out/build-venv/bin/python" -m pip wheel --no-build-isolation --no-deps --wheel-dir "$out/wheels" "$mono/enterprise/python/seaweedkv-lmcache" "$mono/enterprise/python/seaweedkv-native"
"$out/build-venv/bin/python" -I "$scripts/kv-native-preview-stamp.py" "$mono" "$lmcache" "$out"
"$python" -m venv "$out/install-venv"
"$out/install-venv/bin/python" -m pip install --no-index --no-deps "$out"/wheels/*.whl
(cd "$out" && "$out/install-venv/bin/python" -I "$scripts/kv-native-preview-verify.py" "$mono" "$lmcache" "$out")
