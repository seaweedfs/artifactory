package testrunner

import (
	"encoding/json"
	"fmt"
	"os"
	"path/filepath"
	"sync"
	"time"
)

// RunStatus is the mutable run-control state. Written as
// status.json under the run directory. Updated at phase boundaries
// while the run executes; reaches a terminal state at finalize.
//
// Schema version 1. Forward-compat note: new fields land as
// optional with omitempty; readers must tolerate unknown keys.
type RunStatus struct {
	SchemaVersion int    `json:"schema_version"`
	RunID         string `json:"run_id"`
	Scenario      string `json:"scenario"`

	// Optional suite/provenance fields. Product-specific suites may set
	// these at the top level so operators can read one status file and know
	// which source/build they are looking at.
	ProductCommit     string `json:"product_commit,omitempty"`
	RunnerCommit      string `json:"runner_commit,omitempty"`
	RemoteProductRoot string `json:"remote_product_root,omitempty"`

	// Lifecycle. State transitions: queued -> running -> (pass|fail|cancelled|error).
	State string `json:"state"`

	// Phase progress. CurrentPhase/CurrentAction track the engine's
	// current execution point. Empty when not running.
	CurrentPhase  string `json:"current_phase,omitempty"`
	CurrentAction string `json:"current_action,omitempty"`

	// PhasesTotal/PhasesDone are the simple counters most operators
	// want when polling.
	PhasesTotal int `json:"phases_total"`
	PhasesDone  int `json:"phases_done"`

	// Per-phase summary so a caller can read status.json once and
	// see which phase failed without opening result.json.
	Phases []PhaseStatus `json:"phases,omitempty"`

	// Timing. Start/End in RFC3339 UTC. UpdatedAt is the last
	// successful write to status.json.
	StartedAt  string  `json:"started_at,omitempty"`
	EndedAt    string  `json:"ended_at,omitempty"`
	WallClockS float64 `json:"wall_clock_s,omitempty"`
	UpdatedAt  string  `json:"updated_at"`

	// Failure summary (one line). Full detail lives in result.json.
	ErrorSummary string `json:"error_summary,omitempty"`

	// Where to find more detail. Always-set so a watcher can
	// navigate to logs/result/scenario without re-deriving paths.
	ArtifactDir string `json:"artifact_dir"`
}

// PhaseStatus is the per-phase line in status.json.
type PhaseStatus struct {
	Name        string `json:"name"`
	State       string `json:"state"` // running | pass | fail | skipped
	RunID       string `json:"run_id,omitempty"`
	ArtifactDir string `json:"artifact_dir,omitempty"`
	RunDir      string `json:"run_dir,omitempty"`
	PhasesDone  int    `json:"phases_done,omitempty"`
	PhasesTotal int    `json:"phases_total,omitempty"`
	StartedAt   string `json:"started_at,omitempty"`
	EndedAt     string `json:"ended_at,omitempty"`
	Error       string `json:"error,omitempty"`
}

// Run lifecycle states. Match the discipline in current-plan
// docs: queued before engine.Run, running once it starts,
// terminal pass/fail/cancelled/error.
const (
	RunStateQueued    = "queued"
	RunStateRunning   = "running"
	RunStatePass      = "pass"
	RunStateFail      = "fail"
	RunStateCancelled = "cancelled"
	RunStateError     = "error"
)

const (
	PhaseStateRunning = "running"
	PhaseStatePass    = "pass"
	PhaseStateFail    = "fail"
	PhaseStateSkipped = "skipped"
)

// StatusWriter atomically writes status.json under the run dir
// and maintains a sibling `latest` pointer in the parent results
// root so `swblock status --latest` can find the most-recent run.
//
// All updates are serialized through a mutex. Status writes use
// write-temp + rename for atomicity (so a polling reader never
// sees a half-written file).
type StatusWriter struct {
	dir         string // run dir (status.json lives here)
	resultsRoot string // parent dir (latest pointer lives here)

	mu     sync.Mutex
	cancel chan struct{} // closed on cancel detection
	status RunStatus
}

// NewStatusWriter creates a writer scoped to dir. dir must already
// exist. resultsRoot is dir's parent and is used to update the
// `latest` pointer; pass "" to disable.
func NewStatusWriter(dir, resultsRoot string, initial RunStatus) (*StatusWriter, error) {
	if dir == "" {
		return nil, fmt.Errorf("status writer: dir required")
	}
	if initial.SchemaVersion == 0 {
		initial.SchemaVersion = 1
	}
	initial.ArtifactDir = dir
	w := &StatusWriter{
		dir:         dir,
		resultsRoot: resultsRoot,
		status:      initial,
		cancel:      make(chan struct{}),
	}
	if err := w.write(); err != nil {
		return nil, fmt.Errorf("status writer: initial write: %w", err)
	}
	if resultsRoot != "" {
		if err := writeLatest(resultsRoot, initial.RunID); err != nil {
			return nil, fmt.Errorf("status writer: write latest: %w", err)
		}
	}
	return w, nil
}

// SetState updates the top-level run state and writes.
func (w *StatusWriter) SetState(state string) error {
	w.mu.Lock()
	defer w.mu.Unlock()
	w.status.State = state
	now := time.Now().UTC().Format(time.RFC3339Nano)
	if state == RunStateRunning && w.status.StartedAt == "" {
		w.status.StartedAt = now
	}
	if isTerminal(state) {
		w.status.EndedAt = now
		w.setWallClockLocked()
	}
	return w.write()
}

// SetProvenance updates optional suite-level provenance fields. It is used by
// suite orchestration when child evidence reveals the product commit after the
// suite status writer has already been initialized.
func (w *StatusWriter) SetProvenance(productCommit, runnerCommit, remoteProductRoot string) error {
	w.mu.Lock()
	defer w.mu.Unlock()
	if productCommit != "" {
		w.status.ProductCommit = productCommit
	}
	if runnerCommit != "" {
		w.status.RunnerCommit = runnerCommit
	}
	if remoteProductRoot != "" {
		w.status.RemoteProductRoot = remoteProductRoot
	}
	return w.write()
}

// PhaseStarted marks a phase started and writes.
func (w *StatusWriter) PhaseStarted(name string) error {
	w.mu.Lock()
	defer w.mu.Unlock()
	w.status.CurrentPhase = name
	w.status.CurrentAction = ""
	w.status.Phases = append(w.status.Phases, PhaseStatus{
		Name:      name,
		State:     PhaseStateRunning,
		StartedAt: time.Now().UTC().Format(time.RFC3339Nano),
	})
	return w.write()
}

// PhaseFinished marks the current phase finished with state and
// optional error message, and bumps PhasesDone.
func (w *StatusWriter) PhaseFinished(name, state, errMsg string) error {
	w.mu.Lock()
	defer w.mu.Unlock()
	now := time.Now().UTC().Format(time.RFC3339Nano)
	for i := range w.status.Phases {
		ph := &w.status.Phases[i]
		if ph.Name == name && ph.State == PhaseStateRunning {
			ph.State = state
			ph.EndedAt = now
			ph.Error = errMsg
			break
		}
	}
	if state == PhaseStatePass || state == PhaseStateFail {
		w.status.PhasesDone++
	}
	if w.status.CurrentPhase == name {
		w.status.CurrentPhase = ""
		w.status.CurrentAction = ""
	}
	return w.write()
}

// PhaseMetadata annotates a phase row with child-run pointers. It is
// intentionally optional: normal scenario runs do not need these fields, while
// suite-level status uses them so validate-bundle can prove child evidence is
// present without opening a separate index file.
func (w *StatusWriter) PhaseMetadata(name, runID, artifactDir, runDir string, phasesDone, phasesTotal int) error {
	w.mu.Lock()
	defer w.mu.Unlock()
	for i := range w.status.Phases {
		ph := &w.status.Phases[i]
		if ph.Name == name {
			ph.RunID = runID
			ph.ArtifactDir = artifactDir
			ph.RunDir = runDir
			ph.PhasesDone = phasesDone
			ph.PhasesTotal = phasesTotal
			break
		}
	}
	return w.write()
}

// Finalize records the terminal state, summary, and ends timing.
func (w *StatusWriter) Finalize(state, summary string) error {
	w.mu.Lock()
	defer w.mu.Unlock()
	w.status.State = state
	if w.status.StartedAt == "" {
		w.status.StartedAt = time.Now().UTC().Format(time.RFC3339Nano)
	}
	if summary != "" && (state == RunStateFail || state == RunStateCancelled || state == RunStateError) {
		w.status.ErrorSummary = summary
	}
	w.status.EndedAt = time.Now().UTC().Format(time.RFC3339Nano)
	w.setWallClockLocked()
	w.status.CurrentPhase = ""
	w.status.CurrentAction = ""
	return w.write()
}

func (w *StatusWriter) setWallClockLocked() {
	if w.status.StartedAt == "" || w.status.EndedAt == "" {
		return
	}
	start, err := time.Parse(time.RFC3339Nano, w.status.StartedAt)
	if err != nil {
		return
	}
	end, err := time.Parse(time.RFC3339Nano, w.status.EndedAt)
	if err != nil {
		return
	}
	if end.After(start) {
		w.status.WallClockS = end.Sub(start).Seconds()
	}
}

// CancelRequested returns true if a control/cancel file exists in
// the run dir. Engines should poll this between phases.
func (w *StatusWriter) CancelRequested() bool {
	_, err := os.Stat(filepath.Join(w.dir, "control", "cancel"))
	return err == nil
}

// Snapshot returns a deep copy of the current status. Read-only;
// useful for tests and assertions.
func (w *StatusWriter) Snapshot() RunStatus {
	w.mu.Lock()
	defer w.mu.Unlock()
	out := w.status
	out.Phases = append([]PhaseStatus(nil), w.status.Phases...)
	return out
}

// write is the unlocked status persister. Caller must hold w.mu.
// Atomic via write-temp + rename so concurrent readers never see
// a partial file.
func (w *StatusWriter) write() error {
	w.status.UpdatedAt = time.Now().UTC().Format(time.RFC3339Nano)
	if w.status.SchemaVersion == 0 {
		w.status.SchemaVersion = 1
	}
	data, err := json.MarshalIndent(w.status, "", "  ")
	if err != nil {
		return err
	}
	dst := filepath.Join(w.dir, "status.json")
	tmp := dst + ".tmp"
	if err := os.WriteFile(tmp, data, 0644); err != nil {
		return err
	}
	return os.Rename(tmp, dst)
}

// writeLatest atomically updates the `latest` pointer file under
// resultsRoot. The pointer is plain text containing the run_id,
// chosen over a symlink for portability across Windows + Linux +
// SMB-mounted shares (where symlinks may not survive the mount).
func writeLatest(resultsRoot, runID string) error {
	dst := filepath.Join(resultsRoot, "latest")
	tmp := dst + ".tmp"
	if err := os.WriteFile(tmp, []byte(runID+"\n"), 0644); err != nil {
		return err
	}
	return os.Rename(tmp, dst)
}

// ReadLatest returns the run_id named by the `latest` pointer in
// resultsRoot, or "" with no error if no pointer exists.
func ReadLatest(resultsRoot string) (string, error) {
	data, err := os.ReadFile(filepath.Join(resultsRoot, "latest"))
	if err != nil {
		if os.IsNotExist(err) {
			return "", nil
		}
		return "", err
	}
	return trimNewline(string(data)), nil
}

// ReadStatus loads status.json from a run dir and returns the
// parsed RunStatus. Returns an error if the file is missing or
// malformed; callers expecting "no status yet" should check for
// os.IsNotExist on the wrapped error.
func ReadStatus(runDir string) (*RunStatus, error) {
	data, err := os.ReadFile(filepath.Join(runDir, "status.json"))
	if err != nil {
		return nil, err
	}
	var s RunStatus
	if err := json.Unmarshal(data, &s); err != nil {
		return nil, fmt.Errorf("parse status.json: %w", err)
	}
	return &s, nil
}

func isTerminal(state string) bool {
	switch state {
	case RunStatePass, RunStateFail, RunStateCancelled, RunStateError:
		return true
	}
	return false
}

func trimNewline(s string) string {
	for len(s) > 0 && (s[len(s)-1] == '\n' || s[len(s)-1] == '\r') {
		s = s[:len(s)-1]
	}
	return s
}
