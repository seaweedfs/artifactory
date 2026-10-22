package testrunner

import (
	"encoding/json"
	"os"
	"path/filepath"
	"strings"
	"testing"
)

func TestValidateBundle_ProtocolSuitePass(t *testing.T) {
	root := writeSuiteBundleFixture(t, true)
	report, err := ValidateBundle(root, BundleValidationOptions{
		RequirePass:         true,
		RequireTiming:       true,
		RequireChildBundles: true,
		ExpectScenario:      "protocol-release-gate-suite",
		ExpectCommitPrefix:  "abc123",
		ExpectedChildren: []string{
			"iscsi-p6-alua-failover",
			"nvme-p4-multipath-failover",
			"nvme-p5-csi-protocol",
			"iscsi-p8-compat-soak",
		},
	})
	if err != nil {
		t.Fatal(err)
	}
	if !report.OK {
		t.Fatalf("report errors: %v", report.Errors)
	}
	if report.ProductCommit != "abc1234" {
		t.Fatalf("product commit = %q", report.ProductCommit)
	}
}

func TestValidateBundle_BetaHardeningSuitePass(t *testing.T) {
	children := []string{
		"iscsi-p6-alua-failover",
		"nvme-p4-multipath-failover",
		"nvme-p5-csi-protocol",
		"iscsi-p8-compat-soak",
		"csi-lifecycle-component",
		"csi-rf1-durable-restart",
		"operations-status-diagnostics",
		"returned-replica-component",
		"iscsi-returned-replica",
		"cleanup-residue",
	}
	root := writeSuiteBundleFixtureWithChildren(t, true, "beta-hardening-gate", children)
	report, err := ValidateBundle(root, BundleValidationOptions{
		RequirePass:         true,
		RequireTiming:       true,
		RequireChildBundles: true,
		ExpectScenario:      "beta-hardening-gate",
		ExpectCommitPrefix:  "abc123",
		ExpectedChildren:    children,
	})
	if err != nil {
		t.Fatal(err)
	}
	if !report.OK {
		t.Fatalf("report errors: %v", report.Errors)
	}
}

func TestValidateBundle_BetaHardeningMissingCleanupResidueFails(t *testing.T) {
	children := []string{
		"iscsi-p6-alua-failover",
		"nvme-p4-multipath-failover",
		"nvme-p5-csi-protocol",
		"iscsi-p8-compat-soak",
		"csi-lifecycle-component",
		"csi-rf1-durable-restart",
		"operations-status-diagnostics",
		"returned-replica-component",
		"iscsi-returned-replica",
	}
	root := writeSuiteBundleFixtureWithChildren(t, true, "beta-hardening-gate", children)
	expected := append(append([]string{}, children...), "cleanup-residue")
	report, err := ValidateBundle(root, BundleValidationOptions{
		RequirePass:         true,
		RequireTiming:       true,
		RequireChildBundles: true,
		ExpectScenario:      "beta-hardening-gate",
		ExpectedChildren:    expected,
	})
	if err != nil {
		t.Fatal(err)
	}
	if report.OK {
		t.Fatal("expected missing cleanup-residue child to fail")
	}
	if !strings.Contains(strings.Join(report.Errors, "\n"), "cleanup-residue") {
		t.Fatalf("errors = %v, want cleanup-residue", report.Errors)
	}
}

func TestValidateBundle_MissingChildResultFails(t *testing.T) {
	root := writeSuiteBundleFixture(t, true)
	missing := filepath.Join(root, "nvme-p5-csi-protocol", "runs", "nvme-p5-csi-protocol-run", "result.json")
	if err := os.Remove(missing); err != nil {
		t.Fatal(err)
	}
	report, err := ValidateBundle(root, BundleValidationOptions{
		RequirePass:         true,
		RequireChildBundles: true,
		ExpectedChildren: []string{
			"iscsi-p6-alua-failover",
			"nvme-p4-multipath-failover",
			"nvme-p5-csi-protocol",
			"iscsi-p8-compat-soak",
		},
	})
	if err != nil {
		t.Fatal(err)
	}
	if report.OK {
		t.Fatal("expected missing child result to fail")
	}
	if !strings.Contains(strings.Join(report.Errors, "\n"), "missing child result.json") {
		t.Fatalf("errors = %v, want missing child result.json", report.Errors)
	}
}

func TestValidateBundle_MissingCopiedChildStatusFails(t *testing.T) {
	root := writeSuiteBundleFixture(t, true)
	missing := filepath.Join(root, "nvme-p4-multipath-failover", "runs", "nvme-p4-multipath-failover-run", "status.json")
	if err := os.Remove(missing); err != nil {
		t.Fatal(err)
	}
	report, err := ValidateBundle(root, BundleValidationOptions{
		RequirePass:         true,
		RequireChildBundles: true,
		ExpectedChildren: []string{
			"iscsi-p6-alua-failover",
			"nvme-p4-multipath-failover",
			"nvme-p5-csi-protocol",
			"iscsi-p8-compat-soak",
		},
	})
	if err != nil {
		t.Fatal(err)
	}
	if report.OK {
		t.Fatal("expected missing child status to fail")
	}
	if !strings.Contains(strings.Join(report.Errors, "\n"), "missing child status.json") {
		t.Fatalf("errors = %v, want missing child status.json", report.Errors)
	}
}

func TestValidateBundle_ZeroWallClockFails(t *testing.T) {
	root := writeSuiteBundleFixture(t, true)
	for _, name := range []string{"result.json", "status.json"} {
		path := filepath.Join(root, name)
		var doc map[string]any
		data, err := os.ReadFile(path)
		if err != nil {
			t.Fatal(err)
		}
		if err := json.Unmarshal(data, &doc); err != nil {
			t.Fatal(err)
		}
		doc["wall_clock_s"] = 0
		writeJSONFixture(t, path, doc)
	}
	report, err := ValidateBundle(root, BundleValidationOptions{RequireTiming: true})
	if err != nil {
		t.Fatal(err)
	}
	if report.OK {
		t.Fatal("expected zero wall_clock_s to fail")
	}
	if !strings.Contains(strings.Join(report.Errors, "\n"), "wall_clock_s must be > 0") {
		t.Fatalf("errors = %v, want wall_clock_s error", report.Errors)
	}
}

func TestValidateBundle_CommitMismatchFails(t *testing.T) {
	root := writeSuiteBundleFixture(t, true)
	report, err := ValidateBundle(root, BundleValidationOptions{ExpectCommitPrefix: "deadbeef"})
	if err != nil {
		t.Fatal(err)
	}
	if report.OK {
		t.Fatal("expected commit mismatch to fail")
	}
	if !strings.Contains(strings.Join(report.Errors, "\n"), "no commit field matches") {
		t.Fatalf("errors = %v", report.Errors)
	}
}

func writeSuiteBundleFixture(t *testing.T, pass bool) string {
	t.Helper()
	children := []string{
		"iscsi-p6-alua-failover",
		"nvme-p4-multipath-failover",
		"nvme-p5-csi-protocol",
		"iscsi-p8-compat-soak",
	}
	return writeSuiteBundleFixtureWithChildren(t, pass, "protocol-release-gate-suite", children)
}

func writeSuiteBundleFixtureWithChildren(t *testing.T, pass bool, scenario string, children []string) string {
	t.Helper()
	root := t.TempDir()
	status := "pass"
	if !pass {
		status = "fail"
	}
	var phases []map[string]any
	for _, child := range children {
		stepDir := filepath.Join(root, child)
		runID := child + "-run"
		runDir := filepath.Join(stepDir, "runs", runID)
		if err := os.MkdirAll(runDir, 0755); err != nil {
			t.Fatal(err)
		}
		if err := os.WriteFile(filepath.Join(stepDir, "child-run.txt"), []byte(runID+"\n"), 0644); err != nil {
			t.Fatal(err)
		}
		writeJSONFixture(t, filepath.Join(runDir, "status.json"), map[string]any{
			"run_id":       runID,
			"scenario":     child,
			"state":        status,
			"phases_done":  6,
			"phases_total": 6,
		})
		writeJSONFixture(t, filepath.Join(runDir, "result.json"), map[string]any{
			"name":   child,
			"status": strings.ToUpper(status),
		})
		phases = append(phases, map[string]any{
			"name":         child,
			"status":       status,
			"run_id":       runID,
			"artifact_dir": stepDir,
			"run_dir":      runDir,
			"phases_done":  6,
			"phases_total": 6,
		})
	}
	common := map[string]any{
		"run_id":              "suite-run",
		"scenario":            scenario,
		"product_commit":      "abc1234",
		"runner_commit":       "runner123",
		"remote_product_root": "/tmp/product",
		"started_at":          "2026-05-09T00:00:00Z",
		"ended_at":            "2026-05-09T00:20:00Z",
		"wall_clock_s":        1200.0,
		"artifact_dir":        root,
	}
	result := cloneMap(common)
	result["schema_version"] = "1.0"
	result["status"] = status
	result["summary"] = "suite " + status
	result["phase_results"] = phases
	statusDoc := cloneMap(common)
	statusDoc["schema_version"] = 1
	statusDoc["state"] = status
	statusDoc["phases_total"] = len(children)
	statusDoc["phases_done"] = len(children)
	statusDoc["phases"] = phases
	writeJSONFixture(t, filepath.Join(root, "result.json"), result)
	writeJSONFixture(t, filepath.Join(root, "status.json"), statusDoc)
	return root
}

func writeJSONFixture(t *testing.T, path string, v any) {
	t.Helper()
	data, err := json.MarshalIndent(v, "", "  ")
	if err != nil {
		t.Fatal(err)
	}
	if err := os.WriteFile(path, append(data, '\n'), 0644); err != nil {
		t.Fatal(err)
	}
}

func cloneMap(in map[string]any) map[string]any {
	out := make(map[string]any, len(in))
	for k, v := range in {
		out[k] = v
	}
	return out
}
