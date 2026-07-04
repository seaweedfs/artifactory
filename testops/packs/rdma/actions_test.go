package rdma

import "testing"

func TestParseGateOutput(t *testing.T) {
	out := `
UNIFIED_PROXY_GET_BENCH_RESULT label=rc-push mib_s=11645.5 floor_mib_s=5120
UNIFIED_PROXY_GET_BENCH_RESULT label=rc-pull mib_s=5550.8 floor_mib_s=0
UNIFIED_PROXY_GET_BENCH_RESULT label=dc-push mib_s=10141.1 floor_mib_s=5120
SW-RDMA-OBJECT-BENCH-SUCCESS path=/obj mib_s=11324.4
UNIFIED_VFS_READ_MATRIX label=rc4 name=file ms=248 bytes=134217728 mib_s=516.13 pipes=4
UNIFIED_VFS_WRITE name=file ms=634 bytes=134217728 mib_s=201.89
RDMA_CI_RUN_ID=20260629-215902-rdma-lab-gate-runner-tools-unified
RDMA_CI_MONO_SHA=1fb9be830695b8e97c3dc8380389695f0a6cb961
RDMA_CI_PASS=1
RDMA_CI_LOADER_ROWS=9
`
	got := parseGateOutput(out)
	assertKV(t, got, "pass", "1")
	assertKV(t, got, "run_id", "20260629-215902-rdma-lab-gate-runner-tools-unified")
	assertKV(t, got, "loader_rows", "9")
	assertKV(t, got, "perf_rc_push_mib_s", "11645.5")
	assertKV(t, got, "perf_rc_pull_mib_s", "5550.8")
	assertKV(t, got, "perf_dc_push_mib_s", "10141.1")
	assertKV(t, got, "perf_object_bench_mib_s", "11324.4")
	assertKV(t, got, "perf_vfs_read_rc4_mib_s", "516.13")
	assertKV(t, got, "perf_vfs_write_latest_mib_s", "201.89")
}

func TestParseNixlProviderOutput(t *testing.T) {
	out := `
NIXL_PROVIDER_GET_GATE_PASS
NIXL_PROVIDER_READ_BENCH_RESULT mib_s=6815.6 floor_mib_s=5120
NIXL_PROVIDER_READ_BENCH_GATE_PASS
NIXL_PROVIDER_PUT_NORMAL_GET bytes=20971520 sha=abc123 expected=abc123
NIXL_PROVIDER_PUT_GATE_PASS
NIXL_PROVIDER_CPU_GATE_PASS
`
	got := parseNixlProviderOutput(out)
	assertKV(t, got, "pass", "1")
	assertKV(t, got, "get_pass", "1")
	assertKV(t, got, "bench_pass", "1")
	assertKV(t, got, "put_pass", "1")
	assertKV(t, got, "read_mib_s", "6815.6")
	assertKV(t, got, "read_floor_mib_s", "5120")
	assertKV(t, got, "put_bytes", "20971520")
	assertKV(t, got, "put_sha", "abc123")
	assertKV(t, got, "put_expected_sha", "abc123")
	assertKV(t, got, "put_sha_match", "1")
}

func assertKV(t *testing.T, got map[string]string, key, want string) {
	t.Helper()
	if got[key] != want {
		t.Fatalf("%s = %q, want %q", key, got[key], want)
	}
}
