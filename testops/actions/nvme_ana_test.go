package actions

import (
	"encoding/json"
	"os"
	"path/filepath"
	"strings"
	"testing"

	tr "github.com/seaweedfs/artifactory/testops"
)

// TestParseANALog_Fixtures walks actions/testdata/nvme_read_ana_log/
// and round-trips every <name>.bin input through parseANALog,
// comparing the marshalled struct to <name>.golden.json.
//
// This is the binary-input variant of the M1 fixture contract
// (parseListSubsys + parseIdCtrl/Ns are JSON-string inputs;
// ANA log is bytes). Adding a fixture: drop <name>.bin (real
// kernel output) and <name>.golden.json (expected ANALog
// marshalled with 2-space indent).
func TestParseANALog_Fixtures(t *testing.T) {
	dir := filepath.Join("testdata", "nvme_read_ana_log")
	entries, err := os.ReadDir(dir)
	if err != nil {
		t.Fatalf("read testdata dir: %v", err)
	}
	count := 0
	for _, e := range entries {
		name := e.Name()
		if !strings.HasSuffix(name, ".bin") {
			continue
		}
		base := strings.TrimSuffix(name, ".bin")
		t.Run(base, func(t *testing.T) {
			input, err := os.ReadFile(filepath.Join(dir, name))
			if err != nil {
				t.Fatalf("read input: %v", err)
			}
			golden, err := os.ReadFile(filepath.Join(dir, base+".golden.json"))
			if err != nil {
				t.Fatalf("read golden: %v", err)
			}
			got, err := parseANALog(input)
			if err != nil {
				t.Fatalf("parseANALog: %v", err)
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
		t.Fatal("no fixtures in nvme_read_ana_log; M1 contract requires at least one")
	}
}

// TestParseANALog_StateNames verifies every documented ANA state
// (and the unknown-fallback shape).
func TestParseANALog_StateNames(t *testing.T) {
	cases := []struct {
		raw  uint8
		want string
	}{
		{0x01, "optimized"},
		{0x02, "non_optimized"},
		{0x03, "inaccessible"},
		{0x04, "persistent_loss"},
		{0x0F, "change"},
		{0x00, "unknown_0x00"},
		{0xFF, "unknown_0xff"},
	}
	for _, tc := range cases {
		got := anaStateName(tc.raw)
		if got != tc.want {
			t.Errorf("anaStateName(0x%02x) = %q, want %q", tc.raw, got, tc.want)
		}
	}
}

// TestParseANALog_TooShort errors clearly when the kernel returned
// fewer bytes than the 16-byte header.
func TestParseANALog_TooShort(t *testing.T) {
	_, err := parseANALog(make([]byte, 8))
	if err == nil {
		t.Fatal("8-byte input should error")
	}
	if !strings.Contains(err.Error(), "16-byte header") {
		t.Errorf("error = %v, want one mentioning '16-byte header'", err)
	}
}

// TestParseANALog_GroupTruncated errors clearly when a group
// descriptor's NSID list extends beyond the buffer.
func TestParseANALog_GroupTruncated(t *testing.T) {
	// header says group_count=1, group claims nsid_count=2, but only
	// 4 bytes of NSID payload follow (need 8).
	buf := make([]byte, 40)
	buf[8] = 1                       // group_count = 1
	buf[16] = 1                      // group_id = 1
	buf[20] = 2                      // nsid_count = 2 (claims 2 NSIDs)
	buf[32] = ANAStateOptimized      // state
	buf[36] = 1                      // nsid[0] = 1; nsid[1] would need bytes 40-43 → truncated
	_, err := parseANALog(buf)
	if err == nil {
		t.Fatal("truncated group should error")
	}
	if !strings.Contains(err.Error(), "truncated") {
		t.Errorf("error = %v, want one mentioning 'truncated'", err)
	}
}

// TestParseANALog_KeyFields_V3Multipath is the pinned-field test for
// the V3 happy path: single group, group_id=1, nsid=1, state=optimized.
// The summarize_ana_log gate in the bash failover smoke checked the
// same shape; the assertion lives here in Go now.
func TestParseANALog_KeyFields_V3Multipath(t *testing.T) {
	input, err := os.ReadFile(filepath.Join("testdata", "nvme_read_ana_log", "v3_multipath_optimized.bin"))
	if err != nil {
		t.Fatalf("read: %v", err)
	}
	a, err := parseANALog(input)
	if err != nil {
		t.Fatalf("parse: %v", err)
	}
	if a.GroupCount != 1 {
		t.Errorf("group_count = %d, want 1", a.GroupCount)
	}
	if len(a.Groups) != 1 {
		t.Fatalf("len(groups) = %d, want 1", len(a.Groups))
	}
	g := a.Groups[0]
	if g.GroupID != 1 {
		t.Errorf("group_id = %d, want 1", g.GroupID)
	}
	if g.NSIDCount != 1 {
		t.Errorf("nsid_count = %d, want 1", g.NSIDCount)
	}
	if len(g.NSIDs) != 1 || g.NSIDs[0] != 1 {
		t.Errorf("nsids = %v, want [1]", g.NSIDs)
	}
	if g.State != ANAStateOptimized {
		t.Errorf("state = 0x%02x, want 0x01 (optimized)", g.State)
	}
	if g.StateName != "optimized" {
		t.Errorf("state_name = %q, want \"optimized\"", g.StateName)
	}
}

// TestNVMeActions_Registration_ANALog confirms PR-3 registers the
// new action.
func TestNVMeActions_Registration_ANALog(t *testing.T) {
	registry := tr.NewRegistry()
	RegisterNVMeActions(registry)
	if _, err := registry.Get("nvme_read_ana_log"); err != nil {
		t.Errorf("action nvme_read_ana_log not registered: %v", err)
	}
}
