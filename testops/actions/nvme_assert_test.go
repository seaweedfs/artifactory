package actions

import (
	"os"
	"path/filepath"
	"strings"
	"testing"
)

// loadFixturesForAssert loads the captured V3 multipath state and
// returns it as evidence ready for the pure evaluator. Reused
// across happy-path and regression tests.
func loadFixturesForAssert(t *testing.T) assertSubsystemEvidence {
	t.Helper()
	listSubsysIn, err := os.ReadFile(filepath.Join("testdata", "nvme_list_subsys", "multipath_two_tcp.json"))
	if err != nil {
		t.Fatalf("read list-subsys fixture: %v", err)
	}
	view, err := parseListSubsys(string(listSubsysIn))
	if err != nil {
		t.Fatalf("parse list-subsys: %v", err)
	}
	sub := view.findByNQN("nqn.2026-05.io.seaweedfs:mpath-v1")
	if sub == nil {
		t.Fatal("captured fixture lost mpath-v1 NQN")
	}

	idctrlR1, err := os.ReadFile(filepath.Join("testdata", "nvme_id_ctrl", "v3_multipath_r1.json"))
	if err != nil {
		t.Fatalf("read id-ctrl r1: %v", err)
	}
	c1, err := parseIdCtrl(string(idctrlR1))
	if err != nil {
		t.Fatalf("parse id-ctrl r1: %v", err)
	}
	idctrlR2, err := os.ReadFile(filepath.Join("testdata", "nvme_id_ctrl", "v3_multipath_r2.json"))
	if err != nil {
		t.Fatalf("read id-ctrl r2: %v", err)
	}
	c2, err := parseIdCtrl(string(idctrlR2))
	if err != nil {
		t.Fatalf("parse id-ctrl r2: %v", err)
	}

	idnsIn, err := os.ReadFile(filepath.Join("testdata", "nvme_id_ns", "v3_multipath.json"))
	if err != nil {
		t.Fatalf("read id-ns: %v", err)
	}
	ns, err := parseIdNs(string(idnsIn))
	if err != nil {
		t.Fatalf("parse id-ns: %v", err)
	}

	anaIn, err := os.ReadFile(filepath.Join("testdata", "nvme_read_ana_log", "v3_multipath_optimized.bin"))
	if err != nil {
		t.Fatalf("read ana log: %v", err)
	}
	log, err := parseANALog(anaIn)
	if err != nil {
		t.Fatalf("parse ana log: %v", err)
	}

	return assertSubsystemEvidence{
		Subsystem:   sub,
		NSDevices:   []string{"/dev/nvme1n1"}, // sysfs walk would return this
		Controllers: []*IdCtrl{c1, c2},
		Namespace:   ns,
		ANALog:      log,
	}
}

// TestAssertSubsystem_Demo_HappyPath demonstrates the assertion
// shape every QA scenario will use: one declarative spec covering
// all the fields P4 product fixes touch. Against the captured
// known-green V3 multipath state, all checks pass.
func TestAssertSubsystem_Demo_HappyPath(t *testing.T) {
	ev := loadFixturesForAssert(t)
	paths, nsDevs := 2, 1
	cmic, nmic := uint8(0x0a), uint8(0x01)
	anagrpid := uint32(1)
	spec := assertSubsystemSpec{
		Paths:            &paths,
		NamespaceDevices: &nsDevs,
		ANAState:         "optimized",
		CMIC:             &cmic,
		NMIC:             &nmic,
		ANAGRPID:         &anagrpid,
		CntlidDistinct:   true,
	}
	failures := evaluateAssertSubsystem(spec, ev)
	if len(failures) > 0 {
		t.Errorf("expected green, got %d failures:\n  - %s",
			len(failures), strings.Join(failures, "\n  - "))
	}
}

// TestAssertSubsystem_Demo_Regression demonstrates the regression
// failure shape: each P4 product fix's field flipped to a wrong
// value gives one human-readable failure with the actual delta.
//
// This is the proof that "the next time CMIC/NMIC/cntlid regresses,
// QA sees field-level diff in seconds, not 9 retries of 'connect
// rejected'." The same evidence runs through the same evaluator;
// only the spec differs.
func TestAssertSubsystem_Demo_Regression(t *testing.T) {
	ev := loadFixturesForAssert(t)
	wantPaths := 3       // truth: 2
	wantNsDevs := 2      // truth: 1
	wantCMIC := uint8(0) // truth: 0x0a
	wantNMIC := uint8(0) // truth: 0x01
	wantANAGRP := uint32(99)
	spec := assertSubsystemSpec{
		Paths:            &wantPaths,
		NamespaceDevices: &wantNsDevs,
		ANAState:         "inaccessible", // truth: optimized
		CMIC:             &wantCMIC,
		NMIC:             &wantNMIC,
		ANAGRPID:         &wantANAGRP,
	}
	failures := evaluateAssertSubsystem(spec, ev)

	expected := []string{
		"paths: expected=3 got=2",
		"namespace_devices: expected=2 got=1",
		"cmic[path=0]: expected=0x00 got=0x0a",
		"cmic[path=1]: expected=0x00 got=0x0a",
		"nmic: expected=0x00 got=0x01",
		"anagrpid: expected=99 got=1",
		"ana_state: expected=inaccessible got=optimized",
	}
	if len(failures) != len(expected) {
		t.Fatalf("expected %d failures, got %d:\n  - %s",
			len(expected), len(failures), strings.Join(failures, "\n  - "))
	}
	for i, want := range expected {
		if !strings.Contains(failures[i], want) {
			t.Errorf("failure[%d] = %q, want substring %q", i, failures[i], want)
		}
	}
}

// TestAssertSubsystem_Demo_OmittedFieldsSkipped demonstrates that
// an empty spec gives no failures (fields the YAML doesn't
// mention are not asserted). Same evaluator, different spec —
// same evidence runs trivially.
func TestAssertSubsystem_Demo_OmittedFieldsSkipped(t *testing.T) {
	ev := loadFixturesForAssert(t)
	spec := assertSubsystemSpec{} // assert nothing
	failures := evaluateAssertSubsystem(spec, ev)
	if len(failures) != 0 {
		t.Errorf("empty spec should produce no failures, got: %v", failures)
	}
}

// TestAssertSubsystem_Demo_NQNAbsent shows the early-exit shape
// when the NQN never showed up at all.
func TestAssertSubsystem_Demo_NQNAbsent(t *testing.T) {
	paths := 2
	spec := assertSubsystemSpec{Paths: &paths}
	failures := evaluateAssertSubsystem(spec, assertSubsystemEvidence{})
	if len(failures) != 1 || !strings.Contains(failures[0], "NQN not present") {
		t.Errorf("expected single 'NQN not present' failure, got: %v", failures)
	}
}

// TestAssertSubsystem_Demo_CntlidDistinct verifies the multi-
// controller fix (60d3533) is testable: same cntlid on both
// controllers fails; distinct passes.
func TestAssertSubsystem_Demo_CntlidDistinct(t *testing.T) {
	ev := loadFixturesForAssert(t)
	// Real fixtures: r1 has cntlid=2, r2 has cntlid=1. distinct → ok.
	spec := assertSubsystemSpec{CntlidDistinct: true}
	if got := evaluateAssertSubsystem(spec, ev); len(got) != 0 {
		t.Errorf("distinct cntlid pair should pass, got: %v", got)
	}

	// Synthesize regression: both controllers report cntlid=1.
	regressed := ev
	regressed.Controllers = []*IdCtrl{
		{CNTLID: 1},
		{CNTLID: 1},
	}
	failures := evaluateAssertSubsystem(spec, regressed)
	if len(failures) != 1 || !strings.Contains(failures[0], "duplicates") {
		t.Errorf("duplicate cntlid should fail with 'duplicates', got: %v", failures)
	}
}

// TestParseAssertSubsystemSpec_HexAndDec verifies YAML param
// parsing: hex (0x0a / 0X0A), decimal, mixed.
func TestParseAssertSubsystemSpec_HexAndDec(t *testing.T) {
	spec, err := parseAssertSubsystemSpec(map[string]string{
		"paths":             "2",
		"namespace_devices": "1",
		"cmic":              "0x0a",
		"nmic":              "1",
		"ana_state":         "optimized",
		"anagrpid":          "1",
		"cntlid_distinct":   "true",
	})
	if err != nil {
		t.Fatalf("parse: %v", err)
	}
	if spec.Paths == nil || *spec.Paths != 2 {
		t.Errorf("paths = %v", spec.Paths)
	}
	if spec.CMIC == nil || *spec.CMIC != 0x0a {
		t.Errorf("cmic = %v (want 0x0a)", spec.CMIC)
	}
	if spec.NMIC == nil || *spec.NMIC != 1 {
		t.Errorf("nmic = %v", spec.NMIC)
	}
	if spec.ANAState != "optimized" {
		t.Errorf("ana_state = %q", spec.ANAState)
	}
	if !spec.CntlidDistinct {
		t.Errorf("cntlid_distinct = false")
	}
}

// TestParseAssertSubsystemSpec_BadValues errors clearly on bad
// hex/decimal so YAML typos surface at scenario validation time.
func TestParseAssertSubsystemSpec_BadValues(t *testing.T) {
	cases := []struct {
		field, value string
	}{
		{"paths", "abc"},
		{"cmic", "not-a-hex"},
		{"anagrpid", "-1"},
	}
	for _, tc := range cases {
		_, err := parseAssertSubsystemSpec(map[string]string{tc.field: tc.value})
		if err == nil {
			t.Errorf("field=%s value=%s should error", tc.field, tc.value)
		}
	}
}
