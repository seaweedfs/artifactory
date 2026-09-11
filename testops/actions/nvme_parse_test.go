package actions

import (
	"encoding/json"
	"os"
	"path/filepath"
	"strings"
	"testing"
)

// TestParseListSubsys_Fixtures walks actions/testdata/nvme_list_subsys/
// and round-trips every <name>.json input through parseListSubsys,
// comparing the result to <name>.golden.json. Every parsing action
// in M1 ships with a fixture set; this test is the contract.
//
// Adding a new fixture: drop <name>.json (real captured output)
// and <name>.golden.json (expected ListSubsysView marshalled with
// 2-space indent) in the testdata dir. The test picks them up
// automatically.
func TestParseListSubsys_Fixtures(t *testing.T) {
	dir := filepath.Join("testdata", "nvme_list_subsys")
	entries, err := os.ReadDir(dir)
	if err != nil {
		t.Fatalf("read testdata dir: %v", err)
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
				t.Fatalf("read golden: %v (every fixture must have a golden)", err)
			}

			got, err := parseListSubsys(string(input))
			if err != nil {
				t.Fatalf("parseListSubsys: %v", err)
			}

			gotJSON, err := json.MarshalIndent(got, "", "  ")
			if err != nil {
				t.Fatalf("marshal got: %v", err)
			}
			wantJSON := strings.TrimSpace(string(golden))
			gotStr := strings.TrimSpace(string(gotJSON))
			if gotStr != wantJSON {
				t.Errorf("parsed view differs from golden\n--- got ---\n%s\n--- want ---\n%s",
					gotStr, wantJSON)
			}
		})
		count++
	}
	if count == 0 {
		t.Fatal("no fixtures found; M1 contract requires at least one")
	}
}

// TestParseListSubsys_FindByNQN exercises the lookup helper that
// findNVMeDevice and the assert actions both depend on.
func TestParseListSubsys_FindByNQN(t *testing.T) {
	input, err := os.ReadFile(filepath.Join("testdata", "nvme_list_subsys", "multipath_two_tcp.json"))
	if err != nil {
		t.Fatalf("read fixture: %v", err)
	}
	view, err := parseListSubsys(string(input))
	if err != nil {
		t.Fatalf("parse: %v", err)
	}

	cases := []struct {
		nqn        string
		wantPaths  int
		wantPolicy string
	}{
		{"nqn.2026-05.io.seaweedfs:mpath-v1", 2, "numa"},
		{"nqn.1994-11.com.samsung:nvme:PM9B1:M.2:S6B5NF5WA55643", 1, "numa"},
		{"nqn.does-not-exist", 0, ""},
	}
	for _, tc := range cases {
		t.Run(tc.nqn, func(t *testing.T) {
			sub := view.findByNQN(tc.nqn)
			if tc.wantPaths == 0 {
				if sub != nil {
					t.Fatalf("findByNQN(%q) = %+v, want nil", tc.nqn, sub)
				}
				return
			}
			if sub == nil {
				t.Fatalf("findByNQN(%q) = nil, want match", tc.nqn)
			}
			if got := len(sub.Paths); got != tc.wantPaths {
				t.Errorf("paths = %d, want %d", got, tc.wantPaths)
			}
			if sub.IOPolicy != tc.wantPolicy {
				t.Errorf("io_policy = %q, want %q", sub.IOPolicy, tc.wantPolicy)
			}
		})
	}
}

// TestParseListSubsys_FlatShape exercises the older nvme-cli output
// where the top level is a single object, not an array of hosts.
func TestParseListSubsys_FlatShape(t *testing.T) {
	flat := `{
	  "Subsystems": [{
	    "Name": "nvme-subsys1",
	    "NQN": "nqn.test:flat",
	    "Paths": [{"Name": "nvme1", "Transport": "tcp", "State": "live"}]
	  }]
	}`
	view, err := parseListSubsys(flat)
	if err != nil {
		t.Fatalf("parse: %v", err)
	}
	if len(view.Subsystems) != 1 {
		t.Fatalf("subsystems = %d, want 1", len(view.Subsystems))
	}
	if view.Subsystems[0].NQN != "nqn.test:flat" {
		t.Errorf("nqn = %q", view.Subsystems[0].NQN)
	}
}

// TestParseListSubsys_Empty handles the kernel-not-yet case where
// nvme list-subsys prints empty output.
func TestParseListSubsys_Empty(t *testing.T) {
	view, err := parseListSubsys("")
	if err != nil {
		t.Fatalf("parse empty: %v", err)
	}
	if view == nil || len(view.Subsystems) != 0 {
		t.Fatalf("empty input should give empty view, got %+v", view)
	}
}
