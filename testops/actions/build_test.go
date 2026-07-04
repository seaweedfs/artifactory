package actions

import (
	"context"
	"os"
	"os/exec"
	"path/filepath"
	"runtime"
	"strings"
	"testing"

	tr "github.com/seaweedfs/artifactory/testops"
)

// TestGoBuild_LocalBuildsAndRecordsProvenance compiles a tiny hello.go
// in a temp dir and verifies the action returns a path/sha256 pair and
// pushes a ProvBinary into the run bundle.
//
// Skips when no `go` binary is on PATH (rare on CI; common on stripped
// containers — better skip than fail).
func TestGoBuild_LocalBuildsAndRecordsProvenance(t *testing.T) {
	if _, err := exec.LookPath("go"); err != nil {
		t.Skipf("no go binary on PATH: %v", err)
	}

	tmp := t.TempDir()
	mod := "module hello\n\ngo 1.20\n"
	src := "package main\n\nfunc main() { println(\"hi\") }\n"
	if err := os.WriteFile(filepath.Join(tmp, "go.mod"), []byte(mod), 0644); err != nil {
		t.Fatal(err)
	}
	if err := os.WriteFile(filepath.Join(tmp, "main.go"), []byte(src), 0644); err != nil {
		t.Fatal(err)
	}

	bundle := newTestBundle(t)
	actx := &tr.ActionContext{
		Vars:   map[string]string{},
		Log:    func(string, ...interface{}) {},
		Bundle: bundle,
	}

	outName := "hello"
	if runtime.GOOS == "windows" {
		outName = "hello.exe"
	}
	act := tr.Action{
		Action: "go_build",
		Params: map[string]string{
			"package": ".",
			"cwd":     tmp,
			"out":     outName,
		},
		SaveAs: "hello_path",
	}

	out, err := goBuild(context.Background(), actx, act)
	if err != nil {
		t.Fatalf("goBuild: %v", err)
	}

	wantPath := filepath.Join(tmp, outName)
	if out["path"] != wantPath {
		t.Errorf("path = %q, want %q", out["path"], wantPath)
	}
	// save_as in the engine reads output["value"]; for go_build that
	// must equal the binary path so downstream phases can reference it
	// via {{ master_path }}.
	if out["value"] != wantPath {
		t.Errorf("value = %q, want %q (save_as relies on this)", out["value"], wantPath)
	}
	if len(out["sha256"]) != 64 {
		t.Errorf("sha256 = %q (len=%d), want 64-char hex", out["sha256"], len(out["sha256"]))
	}
	if _, err := os.Stat(wantPath); err != nil {
		t.Errorf("output binary missing: %v", err)
	}

	prov := bundle.Provenance()
	if len(prov.Binaries) != 1 {
		t.Fatalf("Binaries len = %d, want 1", len(prov.Binaries))
	}
	if prov.Binaries[0].Path != wantPath {
		t.Errorf("Binaries[0].Path = %q, want %q", prov.Binaries[0].Path, wantPath)
	}
	if prov.Binaries[0].SHA256 != out["sha256"] {
		t.Errorf("Binaries[0].SHA256 = %q, want %q", prov.Binaries[0].SHA256, out["sha256"])
	}
	if prov.Binaries[0].Package != "." {
		t.Errorf("Binaries[0].Package = %q, want '.'", prov.Binaries[0].Package)
	}
}

func TestGoBuild_RequiresPackageParam(t *testing.T) {
	actx := &tr.ActionContext{
		Vars: map[string]string{},
		Log:  func(string, ...interface{}) {},
	}
	_, err := goBuild(context.Background(), actx, tr.Action{Action: "go_build"})
	if err == nil {
		t.Fatal("expected error on missing package param")
	}
}

func TestGoBuild_NilBundleDoesNotPanic(t *testing.T) {
	if _, err := exec.LookPath("go"); err != nil {
		t.Skipf("no go binary on PATH: %v", err)
	}
	tmp := t.TempDir()
	os.WriteFile(filepath.Join(tmp, "go.mod"), []byte("module hello\n\ngo 1.20\n"), 0644)
	os.WriteFile(filepath.Join(tmp, "main.go"), []byte("package main\nfunc main(){}\n"), 0644)

	actx := &tr.ActionContext{
		Vars: map[string]string{},
		Log:  func(string, ...interface{}) {},
		// Bundle: nil
	}
	outName := "hello"
	if runtime.GOOS == "windows" {
		outName = "hello.exe"
	}
	_, err := goBuild(context.Background(), actx, tr.Action{
		Params: map[string]string{
			"package": ".",
			"cwd":     tmp,
			"out":     outName,
		},
	})
	if err != nil {
		t.Fatalf("goBuild with nil Bundle: %v", err)
	}
}

// dockerAvailable reports whether `docker version` succeeds. Tests
// that need a real docker daemon skip when this returns false.
func dockerAvailable() bool {
	if _, err := exec.LookPath("docker"); err != nil {
		return false
	}
	return exec.Command("docker", "version", "--format", "{{.Server.Version}}").Run() == nil
}

func TestDockerBuild_RequiresDockerfileAndTag(t *testing.T) {
	actx := &tr.ActionContext{Vars: map[string]string{}, Log: func(string, ...interface{}) {}}
	if _, err := dockerBuild(context.Background(), actx, tr.Action{Action: "docker_build", Params: map[string]string{"tag": "x"}}); err == nil {
		t.Error("expected error when dockerfile is missing")
	}
	if _, err := dockerBuild(context.Background(), actx, tr.Action{Action: "docker_build", Params: map[string]string{"dockerfile": "x"}}); err == nil {
		t.Error("expected error when tag is missing")
	}
}

func TestCtrLoad_RequiresImagesParam(t *testing.T) {
	actx := &tr.ActionContext{Vars: map[string]string{}, Log: func(string, ...interface{}) {}}
	if _, err := ctrLoad(context.Background(), actx, tr.Action{Action: "ctr_load", Params: map[string]string{}}); err == nil {
		t.Error("expected error when images param missing")
	}
}

func TestCtrLoad_UnknownRuntimeRejected(t *testing.T) {
	actx := &tr.ActionContext{Vars: map[string]string{}, Log: func(string, ...interface{}) {}}
	_, err := ctrLoad(context.Background(), actx, tr.Action{
		Action: "ctr_load",
		Params: map[string]string{"images": "foo:bar", "runtime": "podman"},
	})
	if err == nil {
		t.Error("expected error on unknown runtime")
	}
}

func TestImageDigest_RequiresImageParam(t *testing.T) {
	actx := &tr.ActionContext{Vars: map[string]string{}, Log: func(string, ...interface{}) {}}
	if _, err := imageDigest(context.Background(), actx, tr.Action{Action: "image_digest", Params: map[string]string{}}); err == nil {
		t.Error("expected error when image param missing")
	}
}

// TestDockerBuild_RealDaemon exercises the full path against a real
// docker daemon. Skipped on hosts without docker.
func TestDockerBuild_RealDaemon(t *testing.T) {
	if !dockerAvailable() {
		t.Skip("no docker daemon")
	}
	tmp := t.TempDir()
	dockerfile := filepath.Join(tmp, "Dockerfile")
	if err := os.WriteFile(dockerfile, []byte("FROM scratch\nLABEL test=1\n"), 0644); err != nil {
		t.Fatal(err)
	}
	bundle := newTestBundle(t)
	actx := &tr.ActionContext{Vars: map[string]string{}, Log: func(string, ...interface{}) {}, Bundle: bundle}
	tag := "sw-testrunner-build-test:v0"
	out, err := dockerBuild(context.Background(), actx, tr.Action{
		Action: "docker_build",
		Params: map[string]string{"dockerfile": dockerfile, "tag": tag, "cwd": tmp},
		SaveAs: "img",
	})
	if err != nil {
		t.Fatalf("dockerBuild: %v", err)
	}
	defer exec.Command("docker", "rmi", "-f", tag).Run()
	if out["image"] != tag {
		t.Errorf("image=%q want %q", out["image"], tag)
	}
	if out["value"] != tag {
		t.Errorf("value=%q want %q (save_as relies on this)", out["value"], tag)
	}
	if !strings.HasPrefix(out["digest"], "sha256:") {
		t.Errorf("digest=%q want sha256: prefix", out["digest"])
	}
	prov := bundle.Provenance()
	if len(prov.Images) != 1 {
		t.Fatalf("Images len=%d, want 1", len(prov.Images))
	}
	if prov.Images[0].Tag != tag || !strings.HasPrefix(prov.Images[0].Digest, "sha256:") {
		t.Errorf("Images[0]=%+v", prov.Images[0])
	}
}

func newTestBundle(t *testing.T) *tr.RunBundle {
	t.Helper()
	tmp := t.TempDir()
	scenarioFile := filepath.Join(tmp, "s.yaml")
	if err := os.WriteFile(scenarioFile, []byte("name: build-test\nphases:\n- name: p\n  actions:\n  - action: print\n    msg: x\n"), 0644); err != nil {
		t.Fatalf("write scenario: %v", err)
	}
	b, err := tr.CreateRunBundle(filepath.Join(tmp, "results"), scenarioFile, nil)
	if err != nil {
		t.Fatalf("CreateRunBundle: %v", err)
	}
	return b
}
