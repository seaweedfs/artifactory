package actions

import (
	"context"
	"encoding/json"
	"os"
	"path/filepath"
	"strings"
	"testing"

	tr "github.com/seaweedfs/artifactory/testops"
)

// TestParseIdCtrl_Fixtures walks actions/testdata/nvme_id_ctrl/ and
// round-trips every <name>.json input through parseIdCtrl, comparing
// to <name>.golden.json. Same contract shape as parseListSubsys
// fixtures: drop a file pair, the test picks it up.
func TestParseIdCtrl_Fixtures(t *testing.T) {
	walkParserFixtures(t, "nvme_id_ctrl", func(input string) (interface{}, error) {
		return parseIdCtrl(input)
	})
}

// TestParseIdNs_Fixtures is the matching contract for parseIdNs.
func TestParseIdNs_Fixtures(t *testing.T) {
	walkParserFixtures(t, "nvme_id_ns", func(input string) (interface{}, error) {
		return parseIdNs(input)
	})
}

// walkParserFixtures is the shared fixture-test driver. Each parser
// just supplies its parse function; the driver handles fixture
// discovery, golden diff, and missing-golden errors.
func walkParserFixtures(t *testing.T, dirName string, parse func(string) (interface{}, error)) {
	t.Helper()
	dir := filepath.Join("testdata", dirName)
	entries, err := os.ReadDir(dir)
	if err != nil {
		t.Fatalf("read testdata dir %s: %v", dir, err)
	}
	count := 0
	for _, e := range entries {
		name := e.Name()
		if !strings.HasSuffix(name, ".json") || strings.HasSuffix(name, ".golden.json") {
			continue
		}
		base := strings.TrimSuffix(name, ".json")
		t.Run(base, func(t *testing.T) {
			input, err := os.ReadFile(filepath.Join(dir, name))
			if err != nil {
				t.Fatalf("read input: %v", err)
			}
			golden, err := os.ReadFile(filepath.Join(dir, base+".golden.json"))
			if err != nil {
				t.Fatalf("read golden: %v", err)
			}
			got, err := parse(string(input))
			if err != nil {
				t.Fatalf("parse: %v", err)
			}
			gotJSON, err := json.MarshalIndent(got, "", "  ")
			if err != nil {
				t.Fatalf("marshal: %v", err)
			}
			gotStr := strings.TrimSpace(string(gotJSON))
			wantStr := strings.TrimSpace(string(golden))
			if gotStr != wantStr {
				t.Errorf("parsed view differs from golden\n--- got ---\n%s\n--- want ---\n%s",
					gotStr, wantStr)
			}
		})
		count++
	}
	if count == 0 {
		t.Fatalf("no fixtures in %s; M1 contract requires at least one", dir)
	}
}

// TestParseIdCtrl_TrimsPaddedFields verifies the wire-format fixed-
// width string fields (sn, mn, fr, subnqn) get whitespace-trimmed.
func TestParseIdCtrl_TrimsPaddedFields(t *testing.T) {
	in := `{"vid":0,"ssvid":0,"sn":"abc   ","mn":"foo bar       ","fr":"v1   ","cmic":10,"cntlid":1,"nn":1,"subnqn":"nqn.x:y  "}`
	c, err := parseIdCtrl(in)
	if err != nil {
		t.Fatalf("parse: %v", err)
	}
	cases := []struct{ name, got, want string }{
		{"sn", c.SN, "abc"},
		{"mn", c.MN, "foo bar"},
		{"fr", c.FR, "v1"},
		{"subnqn", c.SubNQN, "nqn.x:y"},
	}
	for _, tc := range cases {
		if tc.got != tc.want {
			t.Errorf("%s = %q, want %q", tc.name, tc.got, tc.want)
		}
	}
}

// TestParseIdCtrl_Empty errors clearly on empty input (helps the
// caller log a useful "kernel returned no id-ctrl" message rather
// than a generic JSON parse error).
func TestParseIdCtrl_Empty(t *testing.T) {
	_, err := parseIdCtrl("")
	if err == nil {
		t.Fatal("empty input should error")
	}
	if !strings.Contains(err.Error(), "empty") {
		t.Errorf("error = %v, want one mentioning 'empty'", err)
	}
}

// TestParseIdNs_KeyFields demonstrates a P4-fix-shaped assertion:
// the V3 multipath fixture has nmic=1 (NMIC.SHARED) and anagrpid=1.
// Regression of the 035702f product fix would flip nmic to 0 and
// fail this test with field-level diff in the fixture-walk above.
// This test pins the human-meaningful expectations alongside.
func TestParseIdNs_KeyFields(t *testing.T) {
	input, err := os.ReadFile(filepath.Join("testdata", "nvme_id_ns", "v3_multipath.json"))
	if err != nil {
		t.Fatalf("read: %v", err)
	}
	n, err := parseIdNs(string(input))
	if err != nil {
		t.Fatalf("parse: %v", err)
	}
	if n.NMIC != 1 {
		t.Errorf("NMIC = %d, want 1 (NMIC.SHARED for native multipath)", n.NMIC)
	}
	if n.ANAGRPID != 1 {
		t.Errorf("ANAGRPID = %d, want 1", n.ANAGRPID)
	}
	if len(n.NGUID) != 32 {
		t.Errorf("NGUID len = %d, want 32 hex chars", len(n.NGUID))
	}
	if len(n.EUI64) != 16 {
		t.Errorf("EUI64 len = %d, want 16 hex chars", len(n.EUI64))
	}
	// EUI64 = NGUID[:8] (16 hex chars) is the V3 derivation rule.
	if n.NGUID[:16] != n.EUI64 {
		t.Errorf("EUI64 (%s) does not equal NGUID[:16] (%s)", n.EUI64, n.NGUID[:16])
	}
}

// TestParseIdCtrl_KeyFields_V3Multipath is the matching id-ctrl
// pinning: cmic=0x0a (multi-controller + ANA) is the c0e4a6a fix.
func TestParseIdCtrl_KeyFields_V3Multipath(t *testing.T) {
	input, err := os.ReadFile(filepath.Join("testdata", "nvme_id_ctrl", "v3_multipath_r1.json"))
	if err != nil {
		t.Fatalf("read: %v", err)
	}
	c, err := parseIdCtrl(string(input))
	if err != nil {
		t.Fatalf("parse: %v", err)
	}
	if c.CMIC != 0x0a {
		t.Errorf("CMIC = 0x%02x, want 0x0a (multi-controller + ANA)", c.CMIC)
	}
	if c.CNTLID == 0 {
		t.Errorf("CNTLID = 0; per-controller cntlid must be non-zero (60d3533 fix)")
	}
	if c.NN != 1 {
		t.Errorf("NN = %d, want 1", c.NN)
	}
	if c.SubNQN == "" {
		t.Errorf("SubNQN empty")
	}
}

// TestNVMeActions_Registration_Identify confirms PR-2 registers the
// new actions alongside the existing four.
func TestNVMeActions_Registration_Identify(t *testing.T) {
	registry := tr.NewRegistry()
	RegisterNVMeActions(registry)
	for _, name := range []string{"nvme_id_ctrl", "nvme_id_ns"} {
		if _, err := registry.Get(name); err != nil {
			t.Errorf("action %q not registered: %v", name, err)
		}
	}
}

// _ = context.Background placeholder; reserved for future end-to-end
// action tests against a fake infra.Node.
var _ = context.Background
