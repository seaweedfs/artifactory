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

// TestAssertNoActiveISCSISessions exercises the action with a fake
// iscsiadm shim placed earlier on PATH than the real binary. The shim
// prints whatever the test puts in IQNS_FILE, with the exit code
// requested by EXIT_CODE.
//
// Skipped on Windows where the sh-based shim and PATH override are
// fragile; the assertion logic is platform-independent and is fully
// exercised on Linux CI.
func TestAssertNoActiveISCSISessions(t *testing.T) {
	if runtime.GOOS == "windows" {
		t.Skip("fake iscsiadm shim relies on POSIX sh")
	}

	tmp := t.TempDir()
	shim := filepath.Join(tmp, "sudo")
	body := `#!/bin/sh
# Args ignored. Print whatever IQNS_FILE contains; exit per EXIT_CODE.
out=""
if [ -f "$IQNS_FILE" ]; then
  out="$(cat "$IQNS_FILE")"
fi
echo "$out"
exit "${EXIT_CODE:-0}"
`
	if err := os.WriteFile(shim, []byte(body), 0755); err != nil {
		t.Fatal(err)
	}
	// Also place an "iscsiadm" shim of the same content so direct
	// invocations work too.
	if err := os.WriteFile(filepath.Join(tmp, "iscsiadm"), []byte(body), 0755); err != nil {
		t.Fatal(err)
	}

	oldPATH := os.Getenv("PATH")
	t.Cleanup(func() { os.Setenv("PATH", oldPATH) })
	os.Setenv("PATH", tmp+string(os.PathListSeparator)+oldPATH)

	cases := []struct {
		name     string
		out      string
		exitCode string
		params   map[string]string
		wantErr  bool
	}{
		{
			name:     "no-sessions-message",
			out:      "iscsiadm: No active sessions.",
			exitCode: "21",
			wantErr:  false,
		},
		{
			name:     "empty-stdout-zero-exit",
			out:      "",
			exitCode: "0",
			wantErr:  false,
		},
		{
			name:     "one-session-fails",
			out:      "tcp: [1] 127.0.0.1:3260,1 iqn.2026-05.io.seaweedfs:vol-1 (non-flash)",
			exitCode: "0",
			wantErr:  true,
		},
		{
			name:     "iqn-substr-filter-no-match",
			out:      "tcp: [1] 127.0.0.1:3260,1 iqn.example.com:other (non-flash)",
			exitCode: "0",
			params:   map[string]string{"iqn_substr": "io.seaweedfs"},
			wantErr:  false,
		},
		{
			name:     "iqn-substr-filter-match",
			out:      "tcp: [1] 127.0.0.1:3260,1 iqn.2026-05.io.seaweedfs:vol-1 (non-flash)",
			exitCode: "0",
			params:   map[string]string{"iqn_substr": "io.seaweedfs"},
			wantErr:  true,
		},
	}

	for _, c := range cases {
		t.Run(c.name, func(t *testing.T) {
			iqnsFile := filepath.Join(tmp, "iqns.txt")
			if err := os.WriteFile(iqnsFile, []byte(c.out), 0644); err != nil {
				t.Fatal(err)
			}
			os.Setenv("IQNS_FILE", iqnsFile)
			os.Setenv("EXIT_CODE", c.exitCode)
			t.Cleanup(func() {
				os.Unsetenv("IQNS_FILE")
				os.Unsetenv("EXIT_CODE")
			})

			actx := &tr.ActionContext{
				Vars: map[string]string{},
				Log:  func(string, ...interface{}) {},
			}
			params := c.params
			if params == nil {
				params = map[string]string{}
			}
			out, err := assertNoActiveISCSISessions(context.Background(), actx, tr.Action{
				Action: "assert_no_active_iscsi_sessions",
				Params: params,
			})
			if (err != nil) != c.wantErr {
				t.Fatalf("err=%v wantErr=%v out=%v", err, c.wantErr, out)
			}
			if !c.wantErr && !strings.Contains(out["count"], "0") {
				t.Errorf("expected count=0 on PASS, got %v", out)
			}
		})
	}
}

// TestAssertNoActiveISCSISessions_BinaryMissingErrors confirms that a
// truly missing iscsiadm binary surfaces as a hard error rather than a
// false PASS (the default `sudo iscsiadm` shells out via `sh -c`).
func TestAssertNoActiveISCSISessions_BinaryMissingErrors(t *testing.T) {
	if runtime.GOOS == "windows" {
		t.Skip("relies on POSIX sh exit semantics")
	}
	if _, err := exec.LookPath("sh"); err != nil {
		t.Skip("no /bin/sh")
	}

	actx := &tr.ActionContext{
		Vars: map[string]string{},
		Log:  func(string, ...interface{}) {},
	}
	_, err := assertNoActiveISCSISessions(context.Background(), actx, tr.Action{
		Action: "assert_no_active_iscsi_sessions",
		Params: map[string]string{"binary": "/no/such/binary-totally-missing"},
	})
	if err == nil {
		t.Fatal("expected error when iscsiadm binary is missing")
	}
}
