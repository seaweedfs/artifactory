package actions

import (
	"context"
	"os"
	"path/filepath"
	"runtime"
	"strings"
	"testing"

	tr "github.com/seaweedfs/artifactory/testops"
	"github.com/seaweedfs/artifactory/testops/infra"
)

func TestCollectPathFile(t *testing.T) {
	tmp := t.TempDir()
	src := filepath.Join(tmp, "source.txt")
	if err := os.WriteFile(src, []byte("hello"), 0644); err != nil {
		t.Fatalf("write source: %v", err)
	}
	dest := filepath.Join(tmp, "artifacts")

	node := &infra.Node{IsNative: true}
	actionPath := src
	if runtime.GOOS == "windows" {
		node = &infra.Node{IsLocal: true}
		actionPath = infra.ToWSLPath(src)
	}

	actx := &tr.ActionContext{
		Nodes: map[string]tr.NodeRunner{
			"local": node,
		},
		Vars: map[string]string{"artifacts_dir": dest},
		Log:  func(string, ...interface{}) {},
	}

	out, err := collectPath(context.Background(), actx, tr.Action{
		Action: "collect_path",
		Node:   "local",
		Params: map[string]string{"path": actionPath, "name": "copied.txt"},
	})
	if err != nil {
		t.Fatalf("collectPath: %v", err)
	}
	got := filepath.Join(dest, "copied.txt")
	if out["value"] != got {
		t.Fatalf("value: got %q want %q", out["value"], got)
	}
	data, err := os.ReadFile(got)
	if err != nil {
		t.Fatalf("read collected: %v", err)
	}
	if string(data) != "hello" {
		t.Fatalf("collected data: got %q", string(data))
	}
}

func TestCollectPathDirectoryArchiveCommandToleratesFileChangedWarning(t *testing.T) {
	cmd := collectPathTarCommand("/tmp/archive.tgz", "/tmp", "phase")
	for _, want := range []string{
		"file changed as we read it",
		`[ -s /tmp/archive.tgz ]`,
		`exit 0`,
	} {
		if !strings.Contains(cmd, want) {
			t.Fatalf("tar command missing %q:\n%s", want, cmd)
		}
	}
}

func TestCollectPathDirectoryArchives(t *testing.T) {
	tmp := t.TempDir()
	srcDir := filepath.Join(tmp, "phase")
	if err := os.MkdirAll(srcDir, 0755); err != nil {
		t.Fatalf("mkdir source: %v", err)
	}
	if err := os.WriteFile(filepath.Join(srcDir, "run.log"), []byte("pass"), 0644); err != nil {
		t.Fatalf("write source: %v", err)
	}
	dest := filepath.Join(tmp, "artifacts")

	node := &infra.Node{IsNative: true}
	actionPath := srcDir
	if runtime.GOOS == "windows" {
		node = &infra.Node{IsLocal: true}
		actionPath = infra.ToWSLPath(srcDir)
	}

	actx := &tr.ActionContext{
		Nodes: map[string]tr.NodeRunner{
			"local": node,
		},
		Vars: map[string]string{"artifacts_dir": dest},
		Log:  func(string, ...interface{}) {},
	}

	out, err := collectPath(context.Background(), actx, tr.Action{
		Action: "collect_path",
		Node:   "local",
		Params: map[string]string{"path": actionPath, "name": "phase"},
	})
	if err != nil {
		t.Fatalf("collectPath: %v", err)
	}
	got := filepath.Join(dest, "phase.tgz")
	if out["value"] != got {
		t.Fatalf("value: got %q want %q", out["value"], got)
	}
	st, err := os.Stat(got)
	if err != nil {
		t.Fatalf("stat archive: %v", err)
	}
	if st.Size() == 0 {
		t.Fatalf("archive is empty")
	}
}
