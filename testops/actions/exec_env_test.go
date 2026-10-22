package actions

import (
	"context"
	"os"
	"path/filepath"
	"runtime"
	"testing"

	tr "github.com/seaweedfs/artifactory/testops"
	"github.com/seaweedfs/artifactory/testops/infra"
)

func TestPrefixEnvVars_NoEnvKeysReturnsCmdUnchanged(t *testing.T) {
	got := prefixEnvVars("bash run.sh", map[string]string{
		"cmd":    "bash run.sh",
		"target": "primary",
	})
	if got != "bash run.sh" {
		t.Errorf("got %q, want unchanged", got)
	}
}

func TestPrefixEnvVars_BuildsSortedPrefix(t *testing.T) {
	got := prefixEnvVars("bash run.sh", map[string]string{
		"cmd":           "bash run.sh",
		"env.BAR":       "two",
		"env.FOO":       "one",
		"env.SW_ITER":   "5",
		"unrelated_key": "ignored",
	})
	want := "BAR=two FOO=one SW_ITER=5 sh -c 'bash run.sh'"
	if got != want {
		t.Errorf("got %q\nwant %q", got, want)
	}
}

func TestPrefixEnvVars_QuotesValuesWithSpacesAndSpecials(t *testing.T) {
	got := prefixEnvVars("run", map[string]string{
		"env.A": "a b",        // space → quote
		"env.B": "x$y",        // $ → quote
		"env.C": "plain",      // no special → no quote
		"env.D": `with'quote`, // single quote → escape
	})
	want := `A='a b' B='x$y' C=plain D='with'\''quote' sh -c run`
	if got != want {
		t.Errorf("got %q\nwant %q", got, want)
	}
}

func TestPrefixEnvVars_EmptyValueIsEmptyQuotes(t *testing.T) {
	got := prefixEnvVars("run", map[string]string{
		"env.EMPTY": "",
	})
	if got != "EMPTY='' sh -c run" {
		t.Errorf("got %q, want \"EMPTY='' sh -c run\"", got)
	}
}

func TestPrefixEnvVars_RejectsBareEnvDot(t *testing.T) {
	// "env." with no name after the dot is malformed; should be ignored,
	// not produce a "= value" empty-name prefix.
	got := prefixEnvVars("run", map[string]string{
		"env.":   "lost",
		"env.OK": "kept",
	})
	if got != "OK=kept sh -c run" {
		t.Errorf("got %q, want OK=kept sh -c run", got)
	}
}

func TestPrefixEnvVars_AppliesToCompoundCommand(t *testing.T) {
	got := prefixEnvVars("cd /tmp && bash run.sh", map[string]string{
		"env.FOO": "bar",
	})
	want := "FOO=bar sh -c 'cd /tmp && bash run.sh'"
	if got != want {
		t.Errorf("got %q\nwant %q", got, want)
	}
}

func TestGrepLogPatternStartingWithDash(t *testing.T) {
	tmp := t.TempDir()
	logPath := filepath.Join(tmp, "run.log")
	if err := os.WriteFile(logPath, []byte("--nvme-listen=127.0.0.1:4420\n"), 0644); err != nil {
		t.Fatalf("write log: %v", err)
	}
	node := &infra.Node{IsNative: true}
	actionPath := logPath
	if runtime.GOOS == "windows" {
		node = &infra.Node{IsLocal: true}
		actionPath = infra.ToWSLPath(logPath)
	}
	actx := &tr.ActionContext{
		Nodes: map[string]tr.NodeRunner{
			"local": node,
		},
		Log: func(string, ...interface{}) {},
	}
	out, err := grepLog(context.Background(), actx, tr.Action{
		Action: "grep_log",
		Node:   "local",
		Params: map[string]string{
			"path":    actionPath,
			"pattern": "--nvme-listen=",
		},
	})
	if err != nil {
		t.Fatalf("grepLog: %v", err)
	}
	if out["value"] != "1" {
		t.Fatalf("count=%q, want 1", out["value"])
	}
}
