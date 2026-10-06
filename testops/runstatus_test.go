package testrunner

import (
	"os"
	"path/filepath"
	"strings"
	"testing"
)

// TestStatusWriter_Lifecycle walks the canonical state transitions
// and verifies status.json content + the latest pointer at each
// step. Mirrors the engine's real call sequence so the schema is
// validated against the actual usage shape.
func TestStatusWriter_Lifecycle(t *testing.T) {
	root := t.TempDir()
	runDir := filepath.Join(root, "run-1")
	if err := os.MkdirAll(runDir, 0755); err != nil {
		t.Fatal(err)
	}
	w, err := NewStatusWriter(runDir, root, RunStatus{
		RunID:       "run-1",
		Scenario:    "demo",
		State:       RunStateQueued,
		PhasesTotal: 3,
	})
	if err != nil {
		t.Fatalf("init: %v", err)
	}

	// Initial write created status.json + latest pointer.
	if got := mustReadStatus(t, runDir); got.State != RunStateQueued {
		t.Errorf("initial state = %s, want queued", got.State)
	}
	latest, err := ReadLatest(root)
	if err != nil || latest != "run-1" {
		t.Errorf("latest pointer = %q err=%v, want run-1", latest, err)
	}

	// Run starts.
	if err := w.SetState(RunStateRunning); err != nil {
		t.Fatalf("set running: %v", err)
	}
	if got := mustReadStatus(t, runDir); got.State != RunStateRunning || got.StartedAt == "" {
		t.Errorf("after running: state=%s started=%q", got.State, got.StartedAt)
	}

	// Phase 1 starts and passes.
	if err := w.PhaseStarted("pre_clean"); err != nil {
		t.Fatal(err)
	}
	if got := mustReadStatus(t, runDir); got.CurrentPhase != "pre_clean" || len(got.Phases) != 1 {
		t.Errorf("after pre_clean started: current=%q phases=%d", got.CurrentPhase, len(got.Phases))
	}
	if err := w.PhaseFinished("pre_clean", PhaseStatePass, ""); err != nil {
		t.Fatal(err)
	}
	if got := mustReadStatus(t, runDir); got.PhasesDone != 1 || got.Phases[0].State != PhaseStatePass {
		t.Errorf("after pre_clean done: done=%d state=%s", got.PhasesDone, got.Phases[0].State)
	}

	// Phase 2 fails with a message.
	if err := w.PhaseStarted("nvme_dynamic_pvc"); err != nil {
		t.Fatal(err)
	}
	if err := w.PhaseFinished("nvme_dynamic_pvc", PhaseStateFail, "kernel rejected NQN"); err != nil {
		t.Fatal(err)
	}
	got := mustReadStatus(t, runDir)
	if got.PhasesDone != 2 {
		t.Errorf("phases_done = %d, want 2", got.PhasesDone)
	}
	if got.Phases[1].State != PhaseStateFail || got.Phases[1].Error != "kernel rejected NQN" {
		t.Errorf("failed phase shape wrong: %+v", got.Phases[1])
	}

	// Run finalizes failed.
	if err := w.Finalize(RunStateFail, "phase nvme_dynamic_pvc failed"); err != nil {
		t.Fatal(err)
	}
	got = mustReadStatus(t, runDir)
	if got.State != RunStateFail || got.EndedAt == "" {
		t.Errorf("terminal: state=%s ended=%q", got.State, got.EndedAt)
	}
	if got.WallClockS <= 0 {
		t.Errorf("wall_clock_s = %v, want > 0", got.WallClockS)
	}
	if got.ErrorSummary != "phase nvme_dynamic_pvc failed" {
		t.Errorf("error_summary = %q", got.ErrorSummary)
	}
	if got.ArtifactDir != runDir {
		t.Errorf("artifact_dir = %q, want %q", got.ArtifactDir, runDir)
	}
}

// TestStatusWriter_AtomicWrite verifies that status.json is never
// observed half-written (the rename trick). We can't truly race the
// FS in a unit test, but we can prove the temp file gets cleaned and
// the destination always parses.
func TestStatusWriter_AtomicWrite(t *testing.T) {
	root := t.TempDir()
	runDir := filepath.Join(root, "run-atomic")
	if err := os.MkdirAll(runDir, 0755); err != nil {
		t.Fatal(err)
	}
	w, err := NewStatusWriter(runDir, root, RunStatus{RunID: "run-atomic"})
	if err != nil {
		t.Fatal(err)
	}
	for i := 0; i < 50; i++ {
		_ = w.PhaseStarted("p")
		_ = w.PhaseFinished("p", PhaseStatePass, "")
	}
	// The .tmp file should not linger after a successful write.
	if _, err := os.Stat(filepath.Join(runDir, "status.json.tmp")); err == nil {
		t.Errorf(".tmp file should not exist after rename")
	}
	if _, err := ReadStatus(runDir); err != nil {
		t.Errorf("status.json must always be parseable: %v", err)
	}
}

// TestStatusWriter_CancelDetection verifies the control/cancel
// signal is detected. Operators (or QA's cancel CLI) drop a file
// at run-dir/control/cancel; the engine polls between phases.
func TestStatusWriter_CancelDetection(t *testing.T) {
	root := t.TempDir()
	runDir := filepath.Join(root, "run-cancel")
	if err := os.MkdirAll(runDir, 0755); err != nil {
		t.Fatal(err)
	}
	w, err := NewStatusWriter(runDir, root, RunStatus{RunID: "run-cancel"})
	if err != nil {
		t.Fatal(err)
	}
	if w.CancelRequested() {
		t.Error("no cancel before file dropped")
	}
	if err := os.MkdirAll(filepath.Join(runDir, "control"), 0755); err != nil {
		t.Fatal(err)
	}
	if err := os.WriteFile(filepath.Join(runDir, "control", "cancel"), []byte(""), 0644); err != nil {
		t.Fatal(err)
	}
	if !w.CancelRequested() {
		t.Error("cancel should be detected after file drop")
	}
}

func TestStatusWriter_CancelledFinalizeKeepsSummary(t *testing.T) {
	root := t.TempDir()
	runDir := filepath.Join(root, "run-cancelled")
	if err := os.MkdirAll(runDir, 0755); err != nil {
		t.Fatal(err)
	}
	w, err := NewStatusWriter(runDir, root, RunStatus{RunID: "run-cancelled"})
	if err != nil {
		t.Fatal(err)
	}
	if err := w.Finalize(RunStateCancelled, "cancelled before phase k8s_fio"); err != nil {
		t.Fatal(err)
	}
	got := mustReadStatus(t, runDir)
	if got.State != RunStateCancelled {
		t.Errorf("state = %s, want cancelled", got.State)
	}
	if got.ErrorSummary != "cancelled before phase k8s_fio" {
		t.Errorf("error_summary = %q", got.ErrorSummary)
	}
}

// TestReadLatest_Missing returns "" with no error when the pointer
// has not yet been written. CLI `status --latest` relies on this
// to print a friendly message instead of erroring.
func TestReadLatest_Missing(t *testing.T) {
	got, err := ReadLatest(t.TempDir())
	if err != nil {
		t.Errorf("err = %v, want nil for missing pointer", err)
	}
	if got != "" {
		t.Errorf("got %q, want empty", got)
	}
}

// TestReadLatest_TrimsNewline verifies the pointer round-trips even
// when written with trailing newlines (e.g., `echo run-X > latest`).
func TestReadLatest_TrimsNewline(t *testing.T) {
	root := t.TempDir()
	if err := os.WriteFile(filepath.Join(root, "latest"), []byte("run-X\r\n"), 0644); err != nil {
		t.Fatal(err)
	}
	got, _ := ReadLatest(root)
	if got != "run-X" {
		t.Errorf("got %q, want run-X (newline-trimmed)", got)
	}
}

// TestStatusWriter_DirRequired surfaces config errors at construction
// time, not on first write.
func TestStatusWriter_DirRequired(t *testing.T) {
	_, err := NewStatusWriter("", "", RunStatus{})
	if err == nil || !strings.Contains(err.Error(), "dir required") {
		t.Errorf("err = %v, want one mentioning 'dir required'", err)
	}
}

func mustReadStatus(t *testing.T, dir string) *RunStatus {
	t.Helper()
	s, err := ReadStatus(dir)
	if err != nil {
		t.Fatalf("read status: %v", err)
	}
	return s
}
