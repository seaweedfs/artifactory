package rdma

import (
	"context"
	"fmt"
	"regexp"
	"sort"
	"strings"

	tr "github.com/seaweedfs/artifactory/testops"
	"github.com/seaweedfs/artifactory/testops/actions"
)

const (
	defaultMonoRepo    = "git@github.com:seaweedfs/seaweed-mono.git"
	defaultM01Workdir  = "/opt/rdma-lab-ci/work"
	defaultM02Workdir  = "/opt/rdma-lab-ci/work"
	defaultArtifactDir = "/opt/rdma-lab-ci/artifacts"
	defaultRunner      = "/opt/rdma-lab-ci/artifactory/rdma-lab-ci/run-mono-rdma-lab.sh"
)

var (
	lineKVPattern      = regexp.MustCompile(`(?m)^([A-Z0-9_]+)=(\S+)$`)
	proxyBenchPattern  = regexp.MustCompile(`UNIFIED_PROXY_GET_BENCH_RESULT label=([a-z0-9-]+) mib_s=([0-9.]+)`)
	vfsReadPattern     = regexp.MustCompile(`UNIFIED_VFS_READ_MATRIX label=([a-z0-9-]+).* mib_s=([0-9.]+)`)
	vfsWritePattern    = regexp.MustCompile(`UNIFIED_VFS_WRITE name=\S+ .* mib_s=([0-9.]+)`)
	objectBenchPattern = regexp.MustCompile(`SW-RDMA-OBJECT-BENCH-SUCCESS .* mib_s=([0-9.]+)`)
	nixlBenchPattern   = regexp.MustCompile(`NIXL_PROVIDER_READ_BENCH_RESULT mib_s=([0-9.]+) floor_mib_s=([0-9.]+)`)
	nixlPutPattern     = regexp.MustCompile(`NIXL_PROVIDER_PUT_NORMAL_GET bytes=([0-9]+) sha=([a-f0-9]+) expected=([a-f0-9]+)`)
)

func runMonoGate(ctx context.Context, actx *tr.ActionContext, act tr.Action) (map[string]string, error) {
	node, err := actions.GetNode(actx, act.Node)
	if err != nil {
		return nil, fmt.Errorf("rdma_run_mono_gate: %w", err)
	}

	ref := act.Params["ref"]
	if strings.TrimSpace(ref) == "" {
		return nil, fmt.Errorf("rdma_run_mono_gate: ref param required")
	}

	profile := paramOr(act.Params, "profile", "unified")
	monoRepo := paramOr(act.Params, "mono_repo", defaultMonoRepo)
	m01Workdir := paramOr(act.Params, "m01_workdir", defaultM01Workdir)
	m02Workdir := paramOr(act.Params, "m02_workdir", defaultM02Workdir)
	artifactDir := paramOr(act.Params, "artifact_dir", defaultArtifactDir)
	runner := paramOr(act.Params, "runner", defaultRunner)
	enableDC := boolParam(act.Params["enable_dc"], true)

	cmd := strings.Join([]string{
		"MONO_REPO=" + shellQuote(monoRepo),
		"M01_WORKDIR=" + shellQuote(m01Workdir),
		"M02_WORKDIR=" + shellQuote(m02Workdir),
		"ARTIFACT_DIR=" + shellQuote(artifactDir),
		"bash",
		shellQuote(runner),
		"--ref", shellQuote(ref),
		"--profile", shellQuote(profile),
	}, " ")
	if enableDC {
		cmd += " --enable-dc"
	}

	stdout, stderr, code, err := node.Run(ctx, cmd)
	combined := strings.TrimSpace(stdout + "\n" + stderr)
	if err != nil {
		return nil, fmt.Errorf("rdma_run_mono_gate: run error: %w", err)
	}
	if code != 0 {
		return nil, fmt.Errorf("rdma_run_mono_gate: exit=%d output=%s", code, tail(combined, 4096))
	}

	parsed := parseGateOutput(combined)
	if parsed["pass"] != "1" {
		return nil, fmt.Errorf("rdma_run_mono_gate: pass witness missing output=%s", tail(combined, 4096))
	}

	out := map[string]string{
		"value":              gateSummary(parsed),
		"__rdma_gate_pass":   parsed["pass"],
		"__rdma_run_id":      parsed["run_id"],
		"__rdma_mono_ref":    ref,
		"__rdma_mono_sha":    parsed["mono_sha"],
		"__rdma_loader_rows": parsed["loader_rows"],
	}
	for k, v := range parsed {
		if strings.HasPrefix(k, "perf_") {
			out["__rdma_"+k] = v
		}
	}
	return out, nil
}

func parseGateOutput(output string) map[string]string {
	result := map[string]string{}
	for _, match := range lineKVPattern.FindAllStringSubmatch(output, -1) {
		switch match[1] {
		case "RDMA_CI_PASS":
			result["pass"] = match[2]
		case "RDMA_CI_RUN_ID":
			result["run_id"] = match[2]
		case "RDMA_CI_MONO_SHA":
			result["mono_sha"] = match[2]
		case "RDMA_CI_LOADER_ROWS":
			result["loader_rows"] = match[2]
		}
	}
	for _, match := range proxyBenchPattern.FindAllStringSubmatch(output, -1) {
		result["perf_"+metricName(match[1])+"_mib_s"] = match[2]
	}
	for _, match := range vfsReadPattern.FindAllStringSubmatch(output, -1) {
		result["perf_vfs_read_"+metricName(match[1])+"_mib_s"] = match[2]
	}
	writes := vfsWritePattern.FindAllStringSubmatch(output, -1)
	if len(writes) > 0 {
		result["perf_vfs_write_latest_mib_s"] = writes[len(writes)-1][1]
	}
	if match := objectBenchPattern.FindStringSubmatch(output); len(match) == 2 {
		result["perf_object_bench_mib_s"] = match[1]
	}
	return result
}

func runNixlProviderGate(ctx context.Context, actx *tr.ActionContext, act tr.Action) (map[string]string, error) {
	node, err := actions.GetNode(actx, act.Node)
	if err != nil {
		return nil, fmt.Errorf("rdma_run_nixl_provider_gate: %w", err)
	}

	cmd := strings.TrimSpace(act.Params["command"])
	if cmd == "" {
		cmd = buildNixlProviderCommand(act.Params)
	}

	stdout, stderr, code, err := node.Run(ctx, cmd)
	combined := strings.TrimSpace(stdout + "\n" + stderr)
	if err != nil {
		return nil, fmt.Errorf("rdma_run_nixl_provider_gate: run error: %w", err)
	}
	if code != 0 {
		return nil, fmt.Errorf("rdma_run_nixl_provider_gate: exit=%d output=%s", code, tail(combined, 4096))
	}

	parsed := parseNixlProviderOutput(combined)
	if parsed["pass"] != "1" {
		return nil, fmt.Errorf("rdma_run_nixl_provider_gate: pass witness missing output=%s", tail(combined, 4096))
	}

	ref := strings.TrimSpace(act.Params["ref"])
	sha := strings.TrimSpace(act.Params["tested_sha"])
	if sha == "" {
		sha = strings.TrimSpace(act.Params["mono_sha"])
	}

	out := map[string]string{
		"value":                             nixlSummary(parsed),
		"__product":                         "rdma",
		"__gate_pass":                       "1",
		"__rdma_gate_pass":                  "1",
		"__rdma_nixl_provider_gate_pass":    "1",
		"__rdma_nixl_provider_get_pass":     parsed["get_pass"],
		"__rdma_nixl_provider_bench_pass":   parsed["bench_pass"],
		"__rdma_nixl_provider_put_pass":     parsed["put_pass"],
		"__rdma_nixl_provider_read_mib_s":   parsed["read_mib_s"],
		"__rdma_nixl_provider_read_floor":   parsed["read_floor_mib_s"],
		"__rdma_nixl_provider_put_bytes":    parsed["put_bytes"],
		"__rdma_nixl_provider_put_sha":      parsed["put_sha"],
		"__rdma_nixl_provider_put_expected": parsed["put_expected_sha"],
	}
	if ref != "" {
		out["__tested_ref"] = ref
		out["__rdma_mono_ref"] = ref
	}
	if sha != "" {
		out["__tested_sha"] = sha
		out["__rdma_mono_sha"] = sha
	}
	runID := strings.TrimSpace(act.Params["lab_run_id"])
	if runID == "" {
		runID = actx.Vars["run_id"]
	}
	if runID != "" {
		out["__lab_run_id"] = runID
		out["__rdma_run_id"] = runID
	}
	return out, nil
}

func buildNixlProviderCommand(params map[string]string) string {
	runner := strings.TrimSpace(params["runner"])
	powershell := paramOr(params, "powershell", "powershell.exe")
	args := []string{
		shellQuote(powershell),
		"-NoProfile",
		"-ExecutionPolicy", "Bypass",
		"-File", shellQuote(runner),
	}
	if v := strings.TrimSpace(params["mono_source_root"]); v != "" {
		args = append(args, "-MonoSourceRoot", shellQuote(v))
	}
	if v := strings.TrimSpace(params["nixl_source_root"]); v != "" {
		args = append(args, "-NixlSourceRoot", shellQuote(v))
	}
	if v := strings.TrimSpace(params["nixl_read_floor_mib_s"]); v != "" {
		args = append(args, "-NixlReadFloorMibS", shellQuote(v))
	}
	if boolParam(params["skip_mono_build"], false) {
		args = append(args, "-SkipMonoBuild")
	}
	if boolParam(params["skip_nixl_build"], false) {
		args = append(args, "-SkipNixlBuild")
	}
	if boolParam(params["keep_running"], false) {
		args = append(args, "-KeepRunning")
	}
	return strings.Join(args, " ")
}

func parseNixlProviderOutput(output string) map[string]string {
	result := map[string]string{}
	if strings.Contains(output, "NIXL_PROVIDER_CPU_GATE_PASS") {
		result["pass"] = "1"
	}
	if strings.Contains(output, "NIXL_PROVIDER_GET_GATE_PASS") {
		result["get_pass"] = "1"
	}
	if strings.Contains(output, "NIXL_PROVIDER_READ_BENCH_GATE_PASS") {
		result["bench_pass"] = "1"
	}
	if strings.Contains(output, "NIXL_PROVIDER_PUT_GATE_PASS") {
		result["put_pass"] = "1"
	}
	if match := nixlBenchPattern.FindStringSubmatch(output); len(match) == 3 {
		result["read_mib_s"] = match[1]
		result["read_floor_mib_s"] = match[2]
	}
	if match := nixlPutPattern.FindStringSubmatch(output); len(match) == 4 {
		result["put_bytes"] = match[1]
		result["put_sha"] = match[2]
		result["put_expected_sha"] = match[3]
		if match[2] == match[3] {
			result["put_sha_match"] = "1"
		}
	}
	return result
}

func nixlSummary(parsed map[string]string) string {
	keys := []string{
		"read_mib_s",
		"read_floor_mib_s",
		"put_bytes",
		"put_sha_match",
	}
	parts := make([]string, 0, len(keys))
	for _, key := range keys {
		if value := parsed[key]; value != "" {
			parts = append(parts, key+"="+value)
		}
	}
	return strings.Join(parts, " ")
}

func gateSummary(parsed map[string]string) string {
	keys := []string{
		"run_id",
		"mono_sha",
		"perf_rc_push_mib_s",
		"perf_rc_pull_mib_s",
		"perf_dc_push_mib_s",
		"perf_object_bench_mib_s",
		"perf_vfs_read_rc4_mib_s",
		"perf_vfs_write_latest_mib_s",
	}
	parts := make([]string, 0, len(keys))
	for _, key := range keys {
		if value := parsed[key]; value != "" {
			parts = append(parts, key+"="+value)
		}
	}
	sort.Strings(parts)
	return strings.Join(parts, " ")
}

func paramOr(params map[string]string, key, fallback string) string {
	if value := strings.TrimSpace(params[key]); value != "" {
		return value
	}
	return fallback
}

func boolParam(value string, fallback bool) bool {
	switch strings.ToLower(strings.TrimSpace(value)) {
	case "1", "true", "yes", "on":
		return true
	case "0", "false", "no", "off":
		return false
	default:
		return fallback
	}
}

func metricName(label string) string {
	return strings.ReplaceAll(label, "-", "_")
}

func shellQuote(value string) string {
	if value == "" {
		return "''"
	}
	return "'" + strings.ReplaceAll(value, "'", `'\''`) + "'"
}

func tail(value string, max int) string {
	if len(value) <= max {
		return value
	}
	return value[len(value)-max:]
}
