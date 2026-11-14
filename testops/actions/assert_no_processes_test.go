package actions

import (
	"context"
	"os"
	"os/exec"
	"path/filepath"
	"runtime"
	"testing"

	tr "github.com/seaweedfs/artifactory/testops"
)

func TestAssertNoProcesses_RequiresPattern(t *testing.T) {
	actx := &tr.ActionContext{Vars: map[string]string{}, Log: func(string, ...interface{}) {}}
	if _, err := assertNoProcesses(context.Background(), actx, tr.Action{}); err == nil {
		t.Fatal("expected error when pattern missing")
	}
}

// TestAssertNoProcesses_PASSWhenNoMatch + FAILWhenMatch use a fake
// pgrep shim earlier on PATH that prints whatever PGREP_OUTPUT_FILE
// contains. Skipped on Windows.
func TestAssertNoProcesses_FakePgrep(t *testing.T) {
	if runtime.GOOS == "windows" {
		t.Skip("fake pgrep shim relies on POSIX sh")
	}
	tmp := t.TempDir()
	shim := filepath.Join(tmp, "pgrep")
	body := `#!/bin/sh
if [ -f "$PGREP_OUTPUT_FILE" ]; then
  cat "$PGREP_OUTPUT_FILE"
fi
exit "${PGREP_EXIT:-0}"
`
	if err := os.WriteFile(shim, []byte(body), 0755); err != nil {
		t.Fatal(err)
	}
	oldPATH := os.Getenv("PATH")
	t.Cleanup(func() { os.Setenv("PATH", oldPATH) })
	os.Setenv("PATH", tmp+string(os.PathListSeparator)+oldPATH)

	cases := []struct {
		name        string
		pgrepOutput string
		pgrepExit   string
		wantErr     bool
	}{
		{name: "no-match-pgrep-exit-1", pgrepOutput: "", pgrepExit: "1", wantErr: false},
		{name: "no-match-pgrep-exit-0", pgrepOutput: "", pgrepExit: "0", wantErr: false},
		{name: "one-match", pgrepOutput: "12345 blockmaster --listen :19333\n", pgrepExit: "0", wantErr: true},
		{name: "two-matches", pgrepOutput: "12345 blockmaster\n67890 blockvolume --master 127.0.0.1:19333\n", pgrepExit: "0", wantErr: true},
	}

	for _, c := range cases {
		t.Run(c.name, func(t *testing.T) {
			outFile := filepath.Join(tmp, "pgrep_out.txt")
			os.WriteFile(outFile, []byte(c.pgrepOutput), 0644)
			os.Setenv("PGREP_OUTPUT_FILE", outFile)
			os.Setenv("PGREP_EXIT", c.pgrepExit)
			t.Cleanup(func() {
				os.Unsetenv("PGREP_OUTPUT_FILE")
				os.Unsetenv("PGREP_EXIT")
			})

			actx := &tr.ActionContext{Vars: map[string]string{}, Log: func(string, ...interface{}) {}}
			out, err := assertNoProcesses(context.Background(), actx, tr.Action{
				Action: "assert_no_processes",
				Params: map[string]string{"pattern": "blockmaster|blockvolume"},
			})
			if (err != nil) != c.wantErr {
				t.Fatalf("err=%v wantErr=%v out=%v", err, c.wantErr, out)
			}
			if !c.wantErr {
				if out["count"] != "0" {
					t.Errorf("expected count=0 on PASS, got %v", out)
				}
			}
		})
	}
}

func TestAssertNoProcesses_BinaryMissingErrors(t *testing.T) {
	if runtime.GOOS == "windows" {
		t.Skip("relies on POSIX sh exit semantics")
	}
	if _, err := exec.LookPath("sh"); err != nil {
		t.Skip("no /bin/sh")
	}
	actx := &tr.ActionContext{Vars: map[string]string{}, Log: func(string, ...interface{}) {}}
	// Missing binary → exit 127 from the shell. Action treats anything
	// outside {0, 1} as a hard error so a misconfigured host doesn't
	// silently PASS this assertion.
	_, err := assertNoProcesses(context.Background(), actx, tr.Action{
		Action: "assert_no_processes",
		Params: map[string]string{
			"pattern": "anything",
			"binary":  "/no/such/binary-totally-missing",
		},
	})
	if err == nil {
		t.Fatal("expected hard error when pgrep binary missing")
	}
}
