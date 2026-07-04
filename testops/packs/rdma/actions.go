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
