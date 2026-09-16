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

func TestPreRunCleanupISCSIReportsAndClearsMatchedSessions(t *testing.T) {
	if runtime.GOOS == "windows" {
		t.Skip("fake sudo/iscsiadm shim relies on POSIX sh")
	}

	tmp := installCleanupShims(t)
	sessions := filepath.Join(tmp, "sessions.txt")
	nodes := filepath.Join(tmp, "nodes.txt")
	calls := filepath.Join(tmp, "calls.txt")
	if err := os.WriteFile(sessions, []byte(
		"tcp: [161] 127.0.0.1:35621,1 iqn.2026-05.io.seaweedfs:os-smoke-v1 (non-flash)\n"+
			"tcp: [162] 127.0.0.1:3260,1 iqn.example.com:other (non-flash)\n"), 0644); err != nil {
		t.Fatal(err)
	}
	if err := os.WriteFile(nodes, []byte("127.0.0.1:35621,1 iqn.2026-05.io.seaweedfs:os-smoke-v1\n"), 0644); err != nil {
		t.Fatal(err)
	}

	t.Setenv("FAKE_ISCSI_SESSIONS", sessions)
	t.Setenv("FAKE_ISCSI_NODES", nodes)
	t.Setenv("FAKE_ISCSI_CALLS", calls)

	var logs []string
	actx := &tr.ActionContext{
		Nodes: map[string]tr.NodeRunner{"m02": &infra.Node{IsNative: true}},
		Vars:  map[string]string{},
		Log:   func(format string, args ...interface{}) { logs = append(logs, strings.TrimSpace(format)) },
	}
	out, err := preRunCleanup(context.Background(), actx, tr.Action{
		Action: "pre_run_cleanup",
		Node:   "m02",
		Params: map[string]string{
			"kill_patterns":       "__sw_runner_no_such_process__",
			"iscsi_logout_prefix": "iqn.2026-05.io.seaweedfs",
		},
	})
	if err != nil {
		t.Fatalf("preRunCleanup: %v\nstdout=%s\nstderr=%s", err, out["iscsi_cleanup_stdout"], out["iscsi_cleanup_stderr"])
	}
	if !strings.Contains(out["iscsi_cleanup_stdout"], "matched iSCSI sessions:") ||
		!strings.Contains(out["iscsi_cleanup_stdout"], "iqn.2026-05.io.seaweedfs:os-smoke-v1") ||
		!strings.Contains(out["iscsi_cleanup_stdout"], "remaining matching iSCSI sessions: 0") {
		t.Fatalf("cleanup stdout missing expected detail:\n%s", out["iscsi_cleanup_stdout"])
	}
	callBytes, err := os.ReadFile(calls)
	if err != nil {
		t.Fatal(err)
	}
	callLog := string(callBytes)
	if !strings.Contains(callLog, "-m node -T iqn.2026-05.io.seaweedfs:os-smoke-v1 --logout") {
		t.Fatalf("logout call not recorded:\n%s", callLog)
	}
	if strings.Contains(callLog, "iqn.example.com:other --logout") {
		t.Fatalf("unrelated IQN should not be logged out:\n%s", callLog)
	}
}

func TestPreRunCleanupISCSIDeletesNodeOnlyRecordsWithoutLogout(t *testing.T) {
	if runtime.GOOS == "windows" {
		t.Skip("fake sudo/iscsiadm shim relies on POSIX sh")
	}

	tmp := installCleanupShims(t)
	sessions := filepath.Join(tmp, "sessions.txt")
	nodes := filepath.Join(tmp, "nodes.txt")
	calls := filepath.Join(tmp, "calls.txt")
	if err := os.WriteFile(sessions, nil, 0644); err != nil {
		t.Fatal(err)
	}
	if err := os.WriteFile(nodes, []byte("127.0.0.1:35621,1 iqn.2026-05.io.seaweedfs:stale-node\n"), 0644); err != nil {
		t.Fatal(err)
	}

	t.Setenv("FAKE_ISCSI_SESSIONS", sessions)
	t.Setenv("FAKE_ISCSI_NODES", nodes)
	t.Setenv("FAKE_ISCSI_CALLS", calls)

	actx := &tr.ActionContext{
		Nodes: map[string]tr.NodeRunner{"m02": &infra.Node{IsNative: true}},
		Vars:  map[string]string{},
		Log:   func(string, ...interface{}) {},
	}
	out, err := preRunCleanup(context.Background(), actx, tr.Action{
		Action: "pre_run_cleanup",
		Node:   "m02",
		Params: map[string]string{
			"kill_patterns":       "__sw_runner_no_such_process__",
			"iscsi_logout_prefix": "iqn.2026-05.io.seaweedfs",
		},
	})
	if err != nil {
		t.Fatalf("preRunCleanup node-only: %v\nstdout=%s\nstderr=%s", err, out["iscsi_cleanup_stdout"], out["iscsi_cleanup_stderr"])
	}
	callBytes, err := os.ReadFile(calls)
	if err != nil {
		t.Fatal(err)
	}
	callLog := string(callBytes)
	if strings.Contains(callLog, "--logout") {
		t.Fatalf("node-only record should not be logged out:\n%s", callLog)
	}
	if !strings.Contains(callLog, "-m node -T iqn.2026-05.io.seaweedfs:stale-node -o delete") {
		t.Fatalf("node-only record should be deleted:\n%s", callLog)
	}
}

func TestPreRunCleanupISCSIFailsOnLogoutError(t *testing.T) {
	if runtime.GOOS == "windows" {
		t.Skip("fake sudo/iscsiadm shim relies on POSIX sh")
	}

	tmp := installCleanupShims(t)
	sessions := filepath.Join(tmp, "sessions.txt")
	nodes := filepath.Join(tmp, "nodes.txt")
	calls := filepath.Join(tmp, "calls.txt")
	if err := os.WriteFile(sessions, []byte("tcp: [1] 127.0.0.1:3260,1 iqn.2026-05.io.seaweedfs:vol (non-flash)\n"), 0644); err != nil {
		t.Fatal(err)
	}
	if err := os.WriteFile(nodes, nil, 0644); err != nil {
		t.Fatal(err)
	}

	t.Setenv("FAKE_ISCSI_SESSIONS", sessions)
	t.Setenv("FAKE_ISCSI_NODES", nodes)
	t.Setenv("FAKE_ISCSI_CALLS", calls)
	t.Setenv("FAKE_ISCSI_FAIL_LOGOUT", "1")

	actx := &tr.ActionContext{
		Nodes: map[string]tr.NodeRunner{"m02": &infra.Node{IsNative: true}},
		Vars:  map[string]string{},
		Log:   func(string, ...interface{}) {},
	}
	out, err := preRunCleanup(context.Background(), actx, tr.Action{
		Action: "pre_run_cleanup",
		Node:   "m02",
		Params: map[string]string{
			"kill_patterns":       "__sw_runner_no_such_process__",
			"iscsi_logout_prefix": "iqn.2026-05.io.seaweedfs",
		},
	})
	if err == nil {
		t.Fatalf("expected logout failure, got nil; stdout=%s", out["iscsi_cleanup_stdout"])
	}
	if !strings.Contains(err.Error(), "iSCSI cleanup") {
		t.Fatalf("error should identify iSCSI cleanup, got %v", err)
	}
}

func installCleanupShims(t *testing.T) string {
	t.Helper()
	tmp := t.TempDir()
	sudo := `#!/bin/sh
if [ "$1" = "-n" ]; then shift; fi
exec "$@"
`
	iscsiadm := `#!/bin/sh
echo "$@" >> "$FAKE_ISCSI_CALLS"
if [ "$1" = "-m" ] && [ "$2" = "session" ]; then
  cat "$FAKE_ISCSI_SESSIONS"
  exit 0
fi
if [ "$1" = "-m" ] && [ "$2" = "node" ] && [ "$3" = "-T" ] && [ "$5" = "--logout" ]; then
  if [ "${FAKE_ISCSI_FAIL_LOGOUT:-0}" = "1" ]; then
    echo "forced logout failure" >&2
    exit 7
  fi
  : > "$FAKE_ISCSI_SESSIONS"
  exit 0
fi
if [ "$1" = "-m" ] && [ "$2" = "node" ] && [ "$3" = "-T" ] && [ "$5" = "-o" ] && [ "$6" = "delete" ]; then
  exit 0
fi
if [ "$1" = "-m" ] && [ "$2" = "node" ]; then
  cat "$FAKE_ISCSI_NODES"
  exit 0
fi
exit 0
`
	if err := os.WriteFile(filepath.Join(tmp, "sudo"), []byte(sudo), 0755); err != nil {
		t.Fatal(err)
	}
	if err := os.WriteFile(filepath.Join(tmp, "iscsiadm"), []byte(iscsiadm), 0755); err != nil {
		t.Fatal(err)
	}
	oldPATH := os.Getenv("PATH")
	t.Cleanup(func() { os.Setenv("PATH", oldPATH) })
	os.Setenv("PATH", tmp+string(os.PathListSeparator)+oldPATH)
	return tmp
}
