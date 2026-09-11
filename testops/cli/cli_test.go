package cli

import (
	"archive/tar"
	"compress/gzip"
	"os"
	"path/filepath"
	"reflect"
	"testing"

	tr "github.com/seaweedfs/artifactory/testops"
)

func TestTerminalRunStateMapsCancelledResult(t *testing.T) {
	state, summary := terminalRunState(&tr.ScenarioResult{
		Status:    tr.StatusFail,
		Cancelled: true,
		Error:     "cancelled before phase k8s_fio",
	})
	if state != tr.RunStateCancelled {
		t.Fatalf("state = %s, want cancelled", state)
	}
	if summary != "cancelled before phase k8s_fio" {
		t.Fatalf("summary = %q", summary)
	}
}

func TestDiscoverProductCommitFromChildBundleReadsAlphaImagesEnv(t *testing.T) {
	childDir := t.TempDir()
	artifactsDir := filepath.Join(childDir, "artifacts")
	if err := os.MkdirAll(artifactsDir, 0755); err != nil {
		t.Fatal(err)
	}
	tgzPath := filepath.Join(artifactsDir, "remote-phases.tgz")
	writeTestTgz(t, tgzPath, map[string]string{
		"remote/pin_build/alpha-images.env": "SW_BLOCK_IMAGE=sw-block:local\nGIT_REVISION=abc123def456\nGIT_DIRTY=false\n",
	})
	commit, found, err := discoverProductCommitFromChildBundle(childDir)
	if err != nil {
		t.Fatal(err)
	}
	if !found {
		t.Fatal("commit evidence not found")
	}
	if commit != "abc123def456" {
		t.Fatalf("commit = %q, want abc123def456", commit)
	}
}

func TestDiscoverProductCommitFromChildBundleReadsVersionEvidence(t *testing.T) {
	childDir := t.TempDir()
	artifactsDir := filepath.Join(childDir, "artifacts")
	if err := os.MkdirAll(artifactsDir, 0755); err != nil {
		t.Fatal(err)
	}
	tgzPath := filepath.Join(artifactsDir, "remote-phases.tgz")
	writeTestTgz(t, tgzPath, map[string]string{
		"remote/pin_build/git.sha":                 "abc123def456\n",
		"remote/pin_build/blockvolume.version.txt": "blockvolume revision=abc123def456 modified=false\n",
	})
	commit, found, err := discoverProductCommitFromChildBundle(childDir)
	if err != nil {
		t.Fatal(err)
	}
	if !found {
		t.Fatal("commit evidence not found")
	}
	if commit != "abc123def456" {
		t.Fatalf("commit = %q, want abc123def456", commit)
	}
}

func TestDiscoverProductCommitFromChildBundleRejectsMixedEvidence(t *testing.T) {
	childDir := t.TempDir()
	artifactsDir := filepath.Join(childDir, "artifacts")
	if err := os.MkdirAll(artifactsDir, 0755); err != nil {
		t.Fatal(err)
	}
	tgzPath := filepath.Join(artifactsDir, "remote-phases.tgz")
	writeTestTgz(t, tgzPath, map[string]string{
		"remote/pin_build/git.sha":                 "abc123def456\n",
		"remote/pin_build/blockvolume.version.txt": "blockvolume revision=def456abc123 modified=false\n",
	})
	_, _, err := discoverProductCommitFromChildBundle(childDir)
	if err == nil {
		t.Fatal("expected mixed evidence error")
	}
}

func TestDiscoverProductCommitFromChildBundleIgnoresNonPinEnv(t *testing.T) {
	childDir := t.TempDir()
	artifactsDir := filepath.Join(childDir, "artifacts")
	if err := os.MkdirAll(artifactsDir, 0755); err != nil {
		t.Fatal(err)
	}
	tgzPath := filepath.Join(artifactsDir, "remote-phases.tgz")
	writeTestTgz(t, tgzPath, map[string]string{
		"remote/nvme-dynamic/alpha-images.env":             "GIT_REVISION=badbad1\n",
		"remote/pin_build/alpha-images.env":                "GIT_REVISION=abc123def456\n",
		"remote/other/pin_build/alpha-images.env":          "GIT_REVISION=badbad2\n",
		"remote/pin_build/not-alpha-images.env":            "GIT_REVISION=badbad3\n",
		"remote/child/copied/pin-build/alpha-images.env":   "GIT_REVISION=badbad4\n",
		"remote/child/copied/pin_build/alpha-images.env":   "GIT_REVISION=badbad5\n",
		"remote/child/copied/pin_build/alpha-images.env.1": "GIT_REVISION=badbad6\n",
	})
	commit, found, err := discoverProductCommitFromChildBundle(childDir)
	if err != nil {
		t.Fatal(err)
	}
	if !found {
		t.Fatal("commit evidence not found")
	}
	if commit != "abc123def456" {
		t.Fatalf("commit = %q, want abc123def456", commit)
	}
}

func TestGitRevisionFromAlphaImagesTgzRejectsMalformedRevision(t *testing.T) {
	dir := t.TempDir()
	tgzPath := filepath.Join(dir, "remote-phases.tgz")
	writeTestTgz(t, tgzPath, map[string]string{
		"remote/pin_build/alpha-images.env": "GIT_REVISION=unknown\n",
	})
	_, _, err := gitRevisionFromAlphaImagesTgz(tgzPath)
	if err == nil {
		t.Fatal("expected malformed revision error")
	}
}

func TestProductCommitFromPinEvidenceRejectsDirtyInputs(t *testing.T) {
	if _, err := productCommitFromPinEvidence("remote/pin_build/alpha-images.env", "GIT_REVISION=abc123def456\nGIT_DIRTY=true\n"); err == nil {
		t.Fatal("expected dirty alpha-images env error")
	}
	if _, err := productCommitFromPinEvidence("remote/pin_build/blockvolume.version.txt", "blockvolume revision=abc123def456 modified=true\n"); err == nil {
		t.Fatal("expected modified version error")
	}
}

func writeTestTgz(t *testing.T, path string, files map[string]string) {
	t.Helper()
	f, err := os.Create(path)
	if err != nil {
		t.Fatal(err)
	}
	defer f.Close()
	gz := gzip.NewWriter(f)
	defer gz.Close()
	tw := tar.NewWriter(gz)
	defer tw.Close()
	for name, body := range files {
		hdr := &tar.Header{Name: name, Mode: 0644, Size: int64(len(body))}
		if err := tw.WriteHeader(hdr); err != nil {
			t.Fatal(err)
		}
		if _, err := tw.Write([]byte(body)); err != nil {
			t.Fatal(err)
		}
	}
}

func TestTerminalRunStateMapsOrdinaryFailure(t *testing.T) {
	state, summary := terminalRunState(&tr.ScenarioResult{
		Status: tr.StatusFail,
		Error:  "phase failed",
	})
	if state != tr.RunStateFail {
		t.Fatalf("state = %s, want fail", state)
	}
	if summary != "phase failed" {
		t.Fatalf("summary = %q", summary)
	}
}

func TestApplyBundleValidationProfileProtocolReleaseGate(t *testing.T) {
	var opts tr.BundleValidationOptions
	if err := applyBundleValidationProfile("protocol-release-gate", &opts); err != nil {
		t.Fatal(err)
	}
	if !opts.RequirePass || !opts.RequireTiming || !opts.RequireChildBundles {
		t.Fatalf("profile did not enable strict gates: %+v", opts)
	}
	if opts.ExpectScenario != "protocol-release-gate-suite" {
		t.Fatalf("scenario = %q", opts.ExpectScenario)
	}
	if got, want := len(opts.ExpectedChildren), 4; got != want {
		t.Fatalf("children = %d, want %d", got, want)
	}
}

func TestApplyBundleValidationProfileBetaHardening(t *testing.T) {
	var opts tr.BundleValidationOptions
	if err := applyBundleValidationProfile("beta-hardening", &opts); err != nil {
		t.Fatal(err)
	}
	if !opts.RequirePass || !opts.RequireTiming || !opts.RequireChildBundles {
		t.Fatalf("profile did not enable strict gates: %+v", opts)
	}
	if opts.ExpectScenario != "beta-hardening-gate" {
		t.Fatalf("scenario = %q", opts.ExpectScenario)
	}
	wantChildren := []string{
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
	if !reflect.DeepEqual(opts.ExpectedChildren, wantChildren) {
		t.Fatalf("children = %+v, want %+v", opts.ExpectedChildren, wantChildren)
	}
}

func TestApplyBundleValidationProfilePreservesExplicitFields(t *testing.T) {
	opts := tr.BundleValidationOptions{
		ExpectScenario:   "custom",
		ExpectedChildren: []string{"one"},
	}
	if err := applyBundleValidationProfile("protocol-release-gate", &opts); err != nil {
		t.Fatal(err)
	}
	if opts.ExpectScenario != "custom" {
		t.Fatalf("scenario overwritten: %q", opts.ExpectScenario)
	}
	if len(opts.ExpectedChildren) != 1 || opts.ExpectedChildren[0] != "one" {
		t.Fatalf("children overwritten: %+v", opts.ExpectedChildren)
	}
}
