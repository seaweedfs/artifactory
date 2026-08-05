package testrunner

import (
	"encoding/json"
	"os"
	"path/filepath"
	"strings"
	"testing"
	"time"
)

func TestCreateRunBundle_CreatesDirectoryAndFiles(t *testing.T) {
	tmpDir := t.TempDir()

	// Write a minimal scenario file.
	scenarioContent := "name: test-bundle\ntimeout: 1m\nphases:\n- name: test\n  actions:\n  - action: print\n    msg: hello\n"
	scenarioFile := filepath.Join(tmpDir, "test.yaml")
	os.WriteFile(scenarioFile, []byte(scenarioContent), 0644)

	bundle, err := CreateRunBundle(filepath.Join(tmpDir, "results"), scenarioFile, []string{"run", "test.yaml"})
	if err != nil {
		t.Fatalf("CreateRunBundle: %v", err)
	}

	// Run directory exists.
	if _, err := os.Stat(bundle.Dir); err != nil {
		t.Fatalf("run dir missing: %v", err)
	}

	// Artifacts subdirectory exists.
	if _, err := os.Stat(bundle.ArtifactsDir()); err != nil {
		t.Fatalf("artifacts dir missing: %v", err)
	}

	// manifest.json exists and is valid.
	manifestData, err := os.ReadFile(filepath.Join(bundle.Dir, "manifest.json"))
	if err != nil {
		t.Fatalf("read manifest: %v", err)
	}
	var manifest RunManifest
	if err := json.Unmarshal(manifestData, &manifest); err != nil {
		t.Fatalf("parse manifest: %v", err)
	}
	if manifest.RunID == "" {
		t.Error("RunID is empty")
	}
	if manifest.ScenarioName != "test-bundle" {
		t.Errorf("ScenarioName = %q, want test-bundle", manifest.ScenarioName)
	}
	if manifest.ScenarioSHA256 == "" {
		t.Error("ScenarioSHA256 is empty")
	}
	if manifest.StartedAt == "" {
		t.Error("StartedAt is empty")
	}

	// scenario.yaml is a frozen copy.
	copied, err := os.ReadFile(filepath.Join(bundle.Dir, "scenario.yaml"))
	if err != nil {
		t.Fatalf("read scenario copy: %v", err)
	}
	if string(copied) != scenarioContent {
		t.Errorf("scenario copy mismatch: got %q", string(copied))
	}

	// Run ID matches directory name.
	dirName := filepath.Base(bundle.Dir)
	if dirName != manifest.RunID {
		t.Errorf("dir name %q != RunID %q", dirName, manifest.RunID)
	}
}

func TestRunBundle_Finalize_WritesAllOutputs(t *testing.T) {
	tmpDir := t.TempDir()

	scenarioFile := filepath.Join(tmpDir, "test.yaml")
	os.WriteFile(scenarioFile, []byte("name: finalize-test\ntimeout: 1m\nphases:\n- name: test\n  actions:\n  - action: print\n    msg: hello\n"), 0644)

	bundle, err := CreateRunBundle(filepath.Join(tmpDir, "results"), scenarioFile, []string{"run"})
	if err != nil {
		t.Fatalf("CreateRunBundle: %v", err)
	}

	result := &ScenarioResult{
		Name:     "finalize-test",
		Status:   StatusPass,
		Duration: 5 * time.Second,
		Phases: []PhaseResult{
			{Name: "setup", Status: StatusPass, Duration: 1 * time.Second},
		},
	}

	if err := bundle.Finalize(result); err != nil {
		t.Fatalf("Finalize: %v", err)
	}

	// result.json exists.
	resultPath := filepath.Join(bundle.Dir, "result.json")
	if _, err := os.Stat(resultPath); err != nil {
		t.Error("result.json missing")
	}
	resultData, err := os.ReadFile(resultPath)
	if err != nil {
		t.Fatalf("read result.json: %v", err)
	}
	var written ScenarioResult
	if err := json.Unmarshal(resultData, &written); err != nil {
		t.Fatalf("parse result.json: %v", err)
	}
	if written.RunID != bundle.Manifest.RunID {
		t.Errorf("result run_id = %q, want %q", written.RunID, bundle.Manifest.RunID)
	}
	if written.StartedAt == "" {
		t.Error("result started_at not set")
	}
	if written.EndedAt == "" {
		t.Error("result ended_at not set")
	}
	if written.WallClockS <= 0 {
		t.Errorf("result wall_clock_s = %v, want > 0", written.WallClockS)
	}
	// result.xml exists.
	if _, err := os.Stat(filepath.Join(bundle.Dir, "result.xml")); err != nil {
		t.Error("result.xml missing")
	}
	// result.html exists.
	if _, err := os.Stat(filepath.Join(bundle.Dir, "result.html")); err != nil {
		t.Error("result.html missing")
	}

	// manifest.json updated with FinishedAt and Status.
	manifestData, _ := os.ReadFile(filepath.Join(bundle.Dir, "manifest.json"))
	var manifest RunManifest
	json.Unmarshal(manifestData, &manifest)
	if manifest.FinishedAt == "" {
		t.Error("FinishedAt not set after Finalize")
	}
	if manifest.Status != "PASS" {
		t.Errorf("Status = %q, want PASS", manifest.Status)
	}
}

func TestRunBundle_UniqueRunIDs(t *testing.T) {
	tmpDir := t.TempDir()
	scenarioFile := filepath.Join(tmpDir, "test.yaml")
	os.WriteFile(scenarioFile, []byte("name: unique-test\ntimeout: 1m\nphases:\n- name: test\n  actions:\n  - action: print\n    msg: hello\n"), 0644)

	ids := make(map[string]bool)
	for i := 0; i < 10; i++ {
		bundle, err := CreateRunBundle(filepath.Join(tmpDir, "results"), scenarioFile, nil)
		if err != nil {
			t.Fatalf("iteration %d: %v", i, err)
		}
		id := bundle.Manifest.RunID
		if ids[id] {
			t.Fatalf("duplicate RunID: %s", id)
		}
		ids[id] = true
	}
}

func TestRunBundle_Finalize_WritesProvenanceJSON(t *testing.T) {
	tmpDir := t.TempDir()
	scenarioFile := filepath.Join(tmpDir, "test.yaml")
	os.WriteFile(scenarioFile, []byte("name: prov-test\ntimeout: 1m\nphases:\n- name: p\n  actions:\n  - action: print\n    msg: hi\n"), 0644)

	bundle, err := CreateRunBundle(filepath.Join(tmpDir, "results"), scenarioFile, []string{"run"})
	if err != nil {
		t.Fatalf("CreateRunBundle: %v", err)
	}

	bundle.RecordBinary(ProvBinary{Path: "/tmp/blockmaster", SHA256: "abcd1234", Package: "./cmd/blockmaster"})
	bundle.RecordBinary(ProvBinary{Path: "/tmp/blockvolume", SHA256: "ef567890", Package: "./cmd/blockvolume", Node: "m02"})
	bundle.RecordImage(ProvImage{Tag: "sw-block:local", Digest: "sha256:8865ebbb", BuiltBy: "build_block"})

	if err := bundle.Finalize(&ScenarioResult{Name: "prov-test", Status: StatusPass}); err != nil {
		t.Fatalf("Finalize: %v", err)
	}

	provData, err := os.ReadFile(filepath.Join(bundle.Dir, "provenance.json"))
	if err != nil {
		t.Fatalf("read provenance.json: %v", err)
	}
	var prov Provenance
	if err := json.Unmarshal(provData, &prov); err != nil {
		t.Fatalf("parse provenance.json: %v", err)
	}

	if prov.RunID != bundle.Manifest.RunID {
		t.Errorf("RunID = %q, want %q", prov.RunID, bundle.Manifest.RunID)
	}
	if prov.Scenario.Name != "prov-test" {
		t.Errorf("Scenario.Name = %q, want prov-test", prov.Scenario.Name)
	}
	if prov.Scenario.SHA256 == "" {
		t.Error("Scenario.SHA256 empty")
	}
	if len(prov.Binaries) != 2 {
		t.Fatalf("Binaries count = %d, want 2", len(prov.Binaries))
	}
	if prov.Binaries[0].Path != "/tmp/blockmaster" || prov.Binaries[0].SHA256 != "abcd1234" {
		t.Errorf("Binaries[0] = %+v", prov.Binaries[0])
	}
	if prov.Binaries[1].Node != "m02" {
		t.Errorf("Binaries[1].Node = %q, want m02", prov.Binaries[1].Node)
	}
	if len(prov.Images) != 1 || prov.Images[0].Tag != "sw-block:local" {
		t.Errorf("Images = %+v", prov.Images)
	}
	if prov.Host.Arch == "" {
		t.Error("Host.Arch empty (runtime.GOARCH should always be set)")
	}
}

func TestRunBundle_DirAndArtifactsDirAccessible(t *testing.T) {
	// CLI injects bundle.Dir and bundle.ArtifactsDir() into scenario.Env
	// as bundle_dir / artifacts_dir. Verify those paths exist and the
	// artifacts dir is a subdir of the bundle dir.
	tmpDir := t.TempDir()
	scenarioFile := filepath.Join(tmpDir, "test.yaml")
	os.WriteFile(scenarioFile, []byte("name: dir-test\nphases:\n- name: p\n  actions:\n  - action: print\n    msg: hi\n"), 0644)

	bundle, err := CreateRunBundle(filepath.Join(tmpDir, "results"), scenarioFile, nil)
	if err != nil {
		t.Fatalf("CreateRunBundle: %v", err)
	}
	if _, err := os.Stat(bundle.Dir); err != nil {
		t.Errorf("bundle.Dir not a real directory: %v", err)
	}
	if _, err := os.Stat(bundle.ArtifactsDir()); err != nil {
		t.Errorf("bundle.ArtifactsDir() not a real directory: %v", err)
	}
	if !strings.HasPrefix(bundle.ArtifactsDir(), bundle.Dir) {
		t.Errorf("ArtifactsDir %q not a subdir of Dir %q", bundle.ArtifactsDir(), bundle.Dir)
	}
}

func TestRunBundle_Finalize_RedactsResultJSONVars(t *testing.T) {
	tmpDir := t.TempDir()
	scenarioFile := filepath.Join(tmpDir, "test.yaml")
	os.WriteFile(scenarioFile, []byte("name: redact-test\nphases:\n- name: p\n  actions:\n  - action: print\n    msg: hi\n"), 0644)

	bundle, err := CreateRunBundle(filepath.Join(tmpDir, "results"), scenarioFile, []string{"run"})
	if err != nil {
		t.Fatalf("CreateRunBundle: %v", err)
	}

	result := &ScenarioResult{
		Name:   "redact-test",
		Status: StatusPass,
		Vars: map[string]string{
			"chap_secret": "VERY-SECRET",
			"target_iqn":  "iqn.example",
			"api_token":   "Bearer xyz",
			"host":        "m02",
		},
	}
	if err := bundle.Finalize(result); err != nil {
		t.Fatalf("Finalize: %v", err)
	}

	data, err := os.ReadFile(filepath.Join(bundle.Dir, "result.json"))
	if err != nil {
		t.Fatalf("read result.json: %v", err)
	}
	body := string(data)
	for _, leaked := range []string{"VERY-SECRET", "Bearer xyz"} {
		if strings.Contains(body, leaked) {
			t.Errorf("result.json leaks secret %q:\n%s", leaked, body)
		}
	}
	for _, kept := range []string{"target_iqn", "iqn.example", "host", "m02"} {
		if !strings.Contains(body, kept) {
			t.Errorf("result.json missing non-secret %q", kept)
		}
	}
	if !strings.Contains(body, RedactedValue) {
		t.Errorf("result.json missing redaction marker; body:\n%s", body)
	}

	// Caller's struct must remain untouched (we copied before redacting).
	if result.Vars["chap_secret"] != "VERY-SECRET" {
		t.Errorf("Finalize mutated caller's Vars: %q", result.Vars["chap_secret"])
	}
}

func TestRunBundle_RecordOnNilBundleIsSafe(t *testing.T) {
	// Actions hold a *RunBundle that may be nil (e.g. --no-bundle, unit
	// tests). Record* must be no-ops on a nil receiver.
	var b *RunBundle
	b.RecordBinary(ProvBinary{Path: "/x", SHA256: "y"})
	b.RecordImage(ProvImage{Tag: "x:y"})
}

func TestRunBundle_ProvenanceConcurrentRecord(t *testing.T) {
	tmpDir := t.TempDir()
	scenarioFile := filepath.Join(tmpDir, "test.yaml")
	os.WriteFile(scenarioFile, []byte("name: race-test\nphases:\n- name: p\n  actions:\n  - action: print\n    msg: hi\n"), 0644)

	bundle, err := CreateRunBundle(filepath.Join(tmpDir, "results"), scenarioFile, nil)
	if err != nil {
		t.Fatalf("CreateRunBundle: %v", err)
	}

	const N = 50
	done := make(chan struct{})
	for i := 0; i < N; i++ {
		go func(i int) {
			bundle.RecordBinary(ProvBinary{Path: "/p", SHA256: "s"})
			bundle.RecordImage(ProvImage{Tag: "t"})
			done <- struct{}{}
			_ = i
		}(i)
	}
	for i := 0; i < N; i++ {
		<-done
	}
	prov := bundle.Provenance()
	if len(prov.Binaries) != N {
		t.Errorf("Binaries len = %d, want %d", len(prov.Binaries), N)
	}
	if len(prov.Images) != N {
		t.Errorf("Images len = %d, want %d", len(prov.Images), N)
	}
}

func TestRunBundle_CommandLineRecorded(t *testing.T) {
	tmpDir := t.TempDir()
	scenarioFile := filepath.Join(tmpDir, "test.yaml")
	os.WriteFile(scenarioFile, []byte("name: cmd-test\ntimeout: 1m\nphases:\n- name: test\n  actions:\n  - action: print\n    msg: hello\n"), 0644)

	bundle, err := CreateRunBundle(filepath.Join(tmpDir, "results"), scenarioFile,
		[]string{"sw-test-runner", "run", "--tiers", "block", "test.yaml"})
	if err != nil {
		t.Fatalf("CreateRunBundle: %v", err)
	}

	if !strings.Contains(bundle.Manifest.CommandLine, "--tiers") {
		t.Errorf("CommandLine = %q, want to contain --tiers", bundle.Manifest.CommandLine)
	}
}
