package testrunner

import (
	"crypto/sha256"
	"encoding/hex"
	"encoding/json"
	"fmt"
	"io"
	"os"
	"os/exec"
	"path/filepath"
	"runtime"
	"strings"
	"sync"
	"time"
)

// RunManifest records the identity and provenance of a single test run.
// Written to manifest.json in the run bundle directory.
type RunManifest struct {
	RunID          string `json:"run_id"`
	StartedAt      string `json:"started_at"`
	FinishedAt     string `json:"finished_at,omitempty"`
	ScenarioName   string `json:"scenario_name"`
	ScenarioFile   string `json:"scenario_file"`
	ScenarioSHA256 string `json:"scenario_sha256"`
	RunnerVersion  string `json:"runner_version,omitempty"`
	GitSHA         string `json:"git_sha,omitempty"`
	Host           string `json:"host,omitempty"`
	Status         string `json:"status,omitempty"`
	CommandLine    string `json:"command_line,omitempty"`
	// Metadata is free-form, dashboard-friendly identity for this run: the
	// scenario's `metadata:` block (test_id, team, owner, ...) merged with any
	// run-context passed via `run -meta key=value` (project, run_by, ...).
	Metadata map[string]string `json:"metadata,omitempty"`
}

// RunBundle manages the per-run output directory.
type RunBundle struct {
	Dir          string // absolute path to the run directory
	Manifest     RunManifest
	scenarioData []byte // frozen copy of the input YAML

	provMu     sync.Mutex
	provenance Provenance
}

// Provenance records the build identities pinned by a run.
// Written to provenance.json at Finalize. Designed to be the
// reproducibility anchor: two runs with identical Provenance ran
// against identical inputs.
//
// Field discipline (also enforced in spec v1-roadmap):
//   - never embed secrets, env dumps, or scenario param values;
//   - missing data is a zero value, not an estimate;
//   - all hashes are sha256 hex.
type Provenance struct {
	RunID            string       `json:"run_id"`
	FrameworkVersion string       `json:"framework_version,omitempty"`
	Scenario         ProvScenario `json:"scenario"`
	Git              ProvGit      `json:"git"`
	Host             ProvHost     `json:"host"`
	Images           []ProvImage  `json:"images"`
	Binaries         []ProvBinary `json:"binaries"`
}

// ProvScenario records the scenario identity (name + frozen-bytes hash).
type ProvScenario struct {
	Name   string `json:"name"`
	SHA256 string `json:"sha256"`
}

// ProvGit records the source-control state of the runner's working tree.
// Dirty is true if the tree had uncommitted changes at run start.
type ProvGit struct {
	SHA   string `json:"sha,omitempty"`
	Dirty bool   `json:"dirty"`
}

// ProvHost records where the run executed.
type ProvHost struct {
	Name   string `json:"name,omitempty"`
	OS     string `json:"os,omitempty"`
	Arch   string `json:"arch,omitempty"`
	Kernel string `json:"kernel,omitempty"`
}

// ProvImage records a container image built or pinned during the run.
// Tag identifies what the run consumed; Digest is the content-addressed
// pin. BuiltBy records the action id that produced the image when known.
type ProvImage struct {
	Tag     string `json:"tag,omitempty"`
	Digest  string `json:"digest,omitempty"`
	BuiltBy string `json:"built_by,omitempty"`
}

// ProvBinary records a binary built during the run. Path is the
// runner-local file system path; SHA256 is the post-build content hash.
type ProvBinary struct {
	Path    string `json:"path"`
	SHA256  string `json:"sha256"`
	Package string `json:"package,omitempty"`
	Node    string `json:"node,omitempty"`
	BuiltBy string `json:"built_by,omitempty"`
}

// CreateRunBundle creates a timestamped run directory under resultsRoot.
// Directory name: YYYYMMDD-HHMMSS-<short-id>
// Creates: manifest.json (partial), scenario.yaml (frozen copy).
func CreateRunBundle(resultsRoot, scenarioFile string, cmdLine []string) (*RunBundle, error) {
	now := time.Now()

	// Read and hash the scenario file.
	scenarioData, err := os.ReadFile(scenarioFile)
	if err != nil {
		return nil, fmt.Errorf("read scenario: %w", err)
	}
	h := sha256.Sum256(scenarioData)
	scenarioHash := hex.EncodeToString(h[:])

	// Parse scenario name from the file (with correct base dir for includes).
	scenario, err := ParseWithBase(scenarioData, filepath.Dir(scenarioFile))
	if err != nil {
		return nil, fmt.Errorf("parse scenario for manifest: %w", err)
	}

	// Generate run ID: timestamp + short hash of (scenario + time).
	ts := now.Format("20060102-150405")
	idSeed := sha256.Sum256([]byte(fmt.Sprintf("%s-%d", scenarioFile, now.UnixNano())))
	shortID := hex.EncodeToString(idSeed[:2]) // 4 hex chars
	runID := ts + "-" + shortID

	// Create directory.
	runDir := filepath.Join(resultsRoot, runID)
	if err := os.MkdirAll(runDir, 0755); err != nil {
		return nil, fmt.Errorf("create run dir: %w", err)
	}
	if err := os.MkdirAll(filepath.Join(runDir, "artifacts"), 0755); err != nil {
		return nil, fmt.Errorf("create artifacts dir: %w", err)
	}

	// Build manifest.
	manifest := RunManifest{
		RunID:          runID,
		StartedAt:      now.UTC().Format(time.RFC3339),
		ScenarioName:   scenario.Name,
		ScenarioFile:   scenarioFile,
		ScenarioSHA256: scenarioHash,
		RunnerVersion:  Version(),
		GitSHA:         gitSHA(),
		Host:           hostname(),
		CommandLine:    strings.Join(cmdLine, " "),
		Metadata:       cloneMeta(scenario.Metadata),
	}

	b := &RunBundle{
		Dir:          runDir,
		Manifest:     manifest,
		scenarioData: scenarioData,
		provenance: Provenance{
			RunID:            runID,
			FrameworkVersion: Version(),
			Scenario:         ProvScenario{Name: scenario.Name, SHA256: scenarioHash},
			Git:              ProvGit{SHA: gitSHA(), Dirty: gitDirty()},
			Host: ProvHost{
				Name:   hostname(),
				OS:     osRelease(),
				Arch:   runtime.GOARCH,
				Kernel: kernelRelease(),
			},
			Images:   []ProvImage{},
			Binaries: []ProvBinary{},
		},
	}

	// Write frozen scenario copy.
	scenarioDst := filepath.Join(runDir, "scenario.yaml")
	if err := os.WriteFile(scenarioDst, scenarioData, 0644); err != nil {
		return nil, fmt.Errorf("write scenario copy: %w", err)
	}

	// Write initial manifest (will be updated at finalize).
	if err := b.writeManifest(); err != nil {
		return nil, err
	}

	return b, nil
}

// Finalize writes the final result files into the run bundle.
func (b *RunBundle) Finalize(result *ScenarioResult) error {
	// Update manifest with final status and time.
	b.Manifest.FinishedAt = time.Now().UTC().Format(time.RFC3339)
	b.Manifest.Status = string(result.Status)
	if err := b.writeManifest(); err != nil {
		return err
	}

	// Write result.json. Vars carry user-supplied save_as outputs
	// which may include credentials when the scenario captured them
	// from CHAP/SSH/token-fetch actions; redact by key name before
	// the file hits disk.
	redacted := *result
	if redacted.RunID == "" {
		redacted.RunID = b.Manifest.RunID
	}
	if redacted.StartedAt == "" {
		redacted.StartedAt = b.Manifest.StartedAt
	}
	if redacted.EndedAt == "" {
		redacted.EndedAt = b.Manifest.FinishedAt
	}
	if redacted.WallClockS <= 0 {
		redacted.WallClockS = scenarioWallClockSeconds(redacted.StartedAt, redacted.EndedAt, redacted.Duration)
	}
	redacted.Vars = RedactMap(result.Vars)
	if err := WriteJSON(&redacted, filepath.Join(b.Dir, "result.json")); err != nil {
		return fmt.Errorf("write result.json: %w", err)
	}

	// Write result.xml (JUnit).
	if err := WriteJUnitXML(result, filepath.Join(b.Dir, "result.xml")); err != nil {
		return fmt.Errorf("write result.xml: %w", err)
	}

	// Write result.html.
	if err := WriteHTMLReport(result, filepath.Join(b.Dir, "result.html")); err != nil {
		return fmt.Errorf("write result.html: %w", err)
	}

	// Write provenance.json (v1 additive artifact).
	if err := b.writeProvenance(); err != nil {
		return fmt.Errorf("write provenance.json: %w", err)
	}

	return nil
}

func scenarioWallClockSeconds(startedAt, endedAt string, duration time.Duration) float64 {
	if duration > 0 {
		return duration.Seconds()
	}
	if startedAt == "" || endedAt == "" {
		return 0
	}
	started, err := time.Parse(time.RFC3339, startedAt)
	if err != nil {
		return 0
	}
	ended, err := time.Parse(time.RFC3339, endedAt)
	if err != nil {
		return 0
	}
	if ended.Before(started) {
		return 0
	}
	return ended.Sub(started).Seconds()
}

// RecordImage records an image (tag + digest) that this run produced
// or pinned. Safe to call from parallel phases. The order images are
// listed in provenance.json reflects the order RecordImage was called.
func (b *RunBundle) RecordImage(img ProvImage) {
	if b == nil {
		return
	}
	b.provMu.Lock()
	defer b.provMu.Unlock()
	b.provenance.Images = append(b.provenance.Images, img)
}

// RecordBinary records a binary that this run built. Safe to call
// from parallel phases.
func (b *RunBundle) RecordBinary(bin ProvBinary) {
	if b == nil {
		return
	}
	b.provMu.Lock()
	defer b.provMu.Unlock()
	b.provenance.Binaries = append(b.provenance.Binaries, bin)
}

// Provenance returns a defensive copy of the current provenance state.
// Used by tests; production callers should let Finalize write the file.
func (b *RunBundle) Provenance() Provenance {
	b.provMu.Lock()
	defer b.provMu.Unlock()
	imgs := append([]ProvImage(nil), b.provenance.Images...)
	bins := append([]ProvBinary(nil), b.provenance.Binaries...)
	p := b.provenance
	p.Images = imgs
	p.Binaries = bins
	return p
}

func (b *RunBundle) writeProvenance() error {
	b.provMu.Lock()
	defer b.provMu.Unlock()
	data, err := json.MarshalIndent(b.provenance, "", "  ")
	if err != nil {
		return fmt.Errorf("marshal provenance: %w", err)
	}
	return os.WriteFile(filepath.Join(b.Dir, "provenance.json"), data, 0644)
}

// ArtifactsDir returns the path to the artifacts subdirectory.
func (b *RunBundle) ArtifactsDir() string {
	return filepath.Join(b.Dir, "artifacts")
}

// cloneMeta returns a copy of m (nil for empty) so the bundle doesn't alias the
// parsed scenario's map.
func cloneMeta(m map[string]string) map[string]string {
	if len(m) == 0 {
		return nil
	}
	out := make(map[string]string, len(m))
	for k, v := range m {
		out[k] = v
	}
	return out
}

// MergeMetadata merges run-context key/values (e.g. project, run_by, team) into
// the manifest's metadata map and rewrites manifest.json. Keys already present
// (e.g. from the scenario's metadata block) are overwritten. Safe on nil/empty.
func (b *RunBundle) MergeMetadata(kv map[string]string) error {
	if b == nil || len(kv) == 0 {
		return nil
	}
	if b.Manifest.Metadata == nil {
		b.Manifest.Metadata = make(map[string]string, len(kv))
	}
	for k, v := range kv {
		b.Manifest.Metadata[k] = v
	}
	return b.writeManifest()
}

func (b *RunBundle) writeManifest() error {
	data, err := json.MarshalIndent(b.Manifest, "", "  ")
	if err != nil {
		return fmt.Errorf("marshal manifest: %w", err)
	}
	return os.WriteFile(filepath.Join(b.Dir, "manifest.json"), data, 0644)
}

// CopyArtifact copies a file into the run bundle's artifacts directory.
func (b *RunBundle) CopyArtifact(src, name string) error {
	dst := filepath.Join(b.ArtifactsDir(), name)
	in, err := os.Open(src)
	if err != nil {
		return err
	}
	defer in.Close()
	out, err := os.Create(dst)
	if err != nil {
		return err
	}
	defer out.Close()
	_, err = io.Copy(out, in)
	return err
}

func hostname() string {
	h, _ := os.Hostname()
	return h
}

func gitSHA() string {
	out, err := exec.Command("git", "rev-parse", "--short", "HEAD").Output()
	if err != nil {
		return ""
	}
	return strings.TrimSpace(string(out))
}

// gitDirty returns true if the working tree has uncommitted changes.
// Returns false on any error (no git, not a repo, etc.) — clean state
// is the conservative default for the dirty flag.
func gitDirty() bool {
	out, err := exec.Command("git", "status", "--porcelain").Output()
	if err != nil {
		return false
	}
	return len(strings.TrimSpace(string(out))) > 0
}

// kernelRelease returns `uname -r` on unix-like systems, empty on Windows.
// Conservative: any error returns empty string rather than fabricating.
func kernelRelease() string {
	if runtime.GOOS == "windows" {
		return ""
	}
	out, err := exec.Command("uname", "-r").Output()
	if err != nil {
		return ""
	}
	return strings.TrimSpace(string(out))
}

// osRelease returns a short OS identifier. On Linux reads
// /etc/os-release PRETTY_NAME; on others returns runtime.GOOS.
func osRelease() string {
	if runtime.GOOS != "linux" {
		return runtime.GOOS
	}
	data, err := os.ReadFile("/etc/os-release")
	if err != nil {
		return runtime.GOOS
	}
	for _, line := range strings.Split(string(data), "\n") {
		if strings.HasPrefix(line, "PRETTY_NAME=") {
			v := strings.TrimPrefix(line, "PRETTY_NAME=")
			v = strings.Trim(v, `"`)
			return v
		}
	}
	return runtime.GOOS
}

// Version returns the runner version. Set at build time via ldflags.
var version = "dev"

func Version() string {
	return version
}
