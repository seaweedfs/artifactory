// Command testops-controller is a small, safe web/API front end for the
// controller-lite queue.
//
// It does not execute arbitrary commands. A POST only writes one RDMA request
// .env file into the existing queue; the systemd worker consumes that file and
// runs the gate under the lab lock.
package main

import (
	"encoding/json"
	"errors"
	"flag"
	"fmt"
	"html/template"
	"log"
	"mime"
	"net/http"
	"os"
	"path/filepath"
	"regexp"
	"sort"
	"strings"
	"time"
)

const (
	defaultQueueDir  = "/mnt/smb/work/share/testops/queue/rdma-ci"
	defaultStateDir  = "/mnt/smb/work/share/testops/state/rdma-ci"
	defaultLogDir    = "/mnt/smb/work/share/testops/logs/rdma-ci"
	defaultDashboard = "http://192.168.1.181:9099/"
	defaultMonoRepo  = "git@github.com:seaweedfs/seaweed-mono.git"
	defaultRunBy     = "testops-controller"
	defaultTeam      = "rdma"
	defaultProject   = "rdma-ci"
	defaultTestID    = "rdma-unified-lab-gate"
	maxFieldLen      = 256
	maxListedFiles   = 30
)

var safeValue = regexp.MustCompile(`^[A-Za-z0-9._@:/+=,-]+$`)

type config struct {
	queueDir     string
	stateDir     string
	logDir       string
	dashboardURL string
	token        string
}

type server struct {
	cfg config
	now func() time.Time
}

type submitRequest struct {
	MonoRef  string `json:"mono_ref"`
	MonoRepo string `json:"mono_repo"`
	RunBy    string `json:"run_by"`
	Team     string `json:"team"`
	Project  string `json:"project"`
	TestID   string `json:"test_id"`
}

type submitResponse struct {
	RequestID string `json:"request_id"`
	QueuePath string `json:"queue_path"`
	State     string `json:"state"`
}

type fileEntry struct {
	Name    string `json:"name"`
	Path    string `json:"path"`
	Size    int64  `json:"size"`
	ModTime string `json:"mod_time"`
}

type statusResponse struct {
	Queue        []fileEntry       `json:"queue"`
	Running      []fileEntry       `json:"running"`
	Done         []fileEntry       `json:"done"`
	Failed       []fileEntry       `json:"failed"`
	StatusFiles  []fileEntry       `json:"status_files"`
	LastRun      map[string]any    `json:"last_run,omitempty"`
	DashboardURL string            `json:"dashboard_url"`
	Paths        map[string]string `json:"paths"`
}

func main() {
	port := flag.Int("port", 9109, "listen port")
	queueDir := flag.String("queue", defaultQueueDir, "RDMA CI queue directory")
	stateDir := flag.String("state", defaultStateDir, "RDMA CI state directory")
	logDir := flag.String("logs", defaultLogDir, "RDMA CI log directory")
	dashboardURL := flag.String("dashboard", defaultDashboard, "dashboard URL shown in the UI")
	token := flag.String("token", os.Getenv("TESTOPS_CONTROLLER_TOKEN"), "optional submit token; POSTs must send it as X-TestOps-Token or form/json token")
	flag.Parse()

	s := &server{
		cfg: config{
			queueDir:     *queueDir,
			stateDir:     *stateDir,
			logDir:       *logDir,
			dashboardURL: *dashboardURL,
			token:        *token,
		},
		now: time.Now,
	}
	if err := s.ensureDirs(); err != nil {
		log.Fatalf("init dirs: %v", err)
	}

	mux := http.NewServeMux()
	mux.HandleFunc("/", s.handleIndex)
	mux.HandleFunc("/api/status", s.handleAPIStatus)
	mux.HandleFunc("/api/rdma/submit", s.handleSubmit)
	mux.HandleFunc("/healthz", func(w http.ResponseWriter, _ *http.Request) { fmt.Fprintln(w, "ok") })

	log.Printf("testops-controller on http://localhost:%d queue=%s state=%s", *port, *queueDir, *stateDir)
	log.Fatal(http.ListenAndServe(fmt.Sprintf(":%d", *port), mux))
}

func (s *server) ensureDirs() error {
	for _, dir := range []string{
		s.cfg.queueDir,
		filepath.Join(s.cfg.stateDir, "running"),
		filepath.Join(s.cfg.stateDir, "done"),
		filepath.Join(s.cfg.stateDir, "failed"),
		filepath.Join(s.cfg.stateDir, "status"),
		s.cfg.logDir,
	} {
		if err := os.MkdirAll(dir, 0o755); err != nil {
			return fmt.Errorf("%s: %w", dir, err)
		}
	}
	return nil
}

func (s *server) handleIndex(w http.ResponseWriter, r *http.Request) {
	if r.URL.Path != "/" {
		http.NotFound(w, r)
		return
	}
	status, err := s.status()
	if err != nil {
		http.Error(w, err.Error(), http.StatusInternalServerError)
		return
	}
	w.Header().Set("Content-Type", "text/html; charset=utf-8")
	if err := pageTmpl.Execute(w, status); err != nil {
		log.Printf("render: %v", err)
	}
}

func (s *server) handleAPIStatus(w http.ResponseWriter, r *http.Request) {
	if r.Method != http.MethodGet {
		http.Error(w, "method not allowed", http.StatusMethodNotAllowed)
		return
	}
	status, err := s.status()
	if err != nil {
		http.Error(w, err.Error(), http.StatusInternalServerError)
		return
	}
	writeJSON(w, status, http.StatusOK)
}

func (s *server) handleSubmit(w http.ResponseWriter, r *http.Request) {
	if r.Method != http.MethodPost {
		http.Error(w, "method not allowed", http.StatusMethodNotAllowed)
		return
	}
	if !s.authorized(r) {
		http.Error(w, "unauthorized", http.StatusUnauthorized)
		return
	}
	req, err := parseSubmit(r)
	if err != nil {
		http.Error(w, err.Error(), http.StatusBadRequest)
		return
	}
	resp, err := s.submit(req)
	if err != nil {
		http.Error(w, err.Error(), http.StatusBadRequest)
		return
	}
	writeJSON(w, resp, http.StatusAccepted)
}

func (s *server) authorized(r *http.Request) bool {
	if s.cfg.token == "" {
		return true
	}
	if r.Header.Get("X-TestOps-Token") == s.cfg.token {
		return true
	}
	if err := r.ParseForm(); err == nil && r.Form.Get("token") == s.cfg.token {
		return true
	}
	return false
}

func parseSubmit(r *http.Request) (submitRequest, error) {
	var req submitRequest
	ct, _, _ := mime.ParseMediaType(r.Header.Get("Content-Type"))
	if strings.EqualFold(ct, "application/json") {
		var raw map[string]string
		if err := json.NewDecoder(r.Body).Decode(&raw); err != nil {
			return req, fmt.Errorf("invalid JSON: %w", err)
		}
		req = submitRequest{
			MonoRef:  raw["mono_ref"],
			MonoRepo: raw["mono_repo"],
			RunBy:    raw["run_by"],
			Team:     raw["team"],
			Project:  raw["project"],
			TestID:   raw["test_id"],
		}
	} else {
		if err := r.ParseForm(); err != nil {
			return req, fmt.Errorf("invalid form: %w", err)
		}
		req = submitRequest{
			MonoRef:  r.Form.Get("mono_ref"),
			MonoRepo: r.Form.Get("mono_repo"),
			RunBy:    r.Form.Get("run_by"),
			Team:     r.Form.Get("team"),
			Project:  r.Form.Get("project"),
			TestID:   r.Form.Get("test_id"),
		}
	}
	req = withSubmitDefaults(req)
	return req, validateSubmit(req)
}

func withSubmitDefaults(req submitRequest) submitRequest {
	if req.MonoRef == "" {
		req.MonoRef = "main"
	}
	if req.MonoRepo == "" {
		req.MonoRepo = defaultMonoRepo
	}
	if req.RunBy == "" {
		req.RunBy = defaultRunBy
	}
	if req.Team == "" {
		req.Team = defaultTeam
	}
	if req.Project == "" {
		req.Project = defaultProject
	}
	if req.TestID == "" {
		req.TestID = defaultTestID
	}
	return req
}

func validateSubmit(req submitRequest) error {
	checks := map[string]string{
		"mono_ref":  req.MonoRef,
		"mono_repo": req.MonoRepo,
		"run_by":    req.RunBy,
		"team":      req.Team,
		"project":   req.Project,
		"test_id":   req.TestID,
	}
	for name, value := range checks {
		if err := validateField(name, value); err != nil {
			return err
		}
	}
	return nil
}

func validateField(name, value string) error {
	if value == "" {
		return fmt.Errorf("%s is required", name)
	}
	if len(value) > maxFieldLen {
		return fmt.Errorf("%s is too long", name)
	}
	if !safeValue.MatchString(value) {
		return fmt.Errorf("%s has unsupported characters", name)
	}
	return nil
}

func (s *server) submit(req submitRequest) (submitResponse, error) {
	req = withSubmitDefaults(req)
	if err := validateSubmit(req); err != nil {
		return submitResponse{}, err
	}
	if err := s.ensureDirs(); err != nil {
		return submitResponse{}, err
	}
	now := s.now().UTC()
	safeRef := sanitizeForID(req.MonoRef)
	requestID := fmt.Sprintf("%s-%09d-%s-%d", now.Format("20060102-150405"), now.Nanosecond(), safeRef, os.Getpid())
	reqPath := filepath.Join(s.cfg.queueDir, requestID+".env")
	if _, err := os.Stat(reqPath); err == nil {
		return submitResponse{}, errors.New("request id collision")
	}
	body := buildEnv(requestID, req)
	tmp, err := os.CreateTemp(s.cfg.queueDir, "."+requestID+".*.tmp")
	if err != nil {
		return submitResponse{}, err
	}
	tmpName := tmp.Name()
	defer os.Remove(tmpName)
	if _, err := tmp.WriteString(body); err != nil {
		tmp.Close()
		return submitResponse{}, err
	}
	if err := tmp.Close(); err != nil {
		return submitResponse{}, err
	}
	if err := os.Rename(tmpName, reqPath); err != nil {
		return submitResponse{}, err
	}
	return submitResponse{RequestID: requestID, QueuePath: reqPath, State: "queued"}, nil
}

func buildEnv(requestID string, req submitRequest) string {
	lines := []string{
		"REQUEST_ID=" + shellQuote(requestID),
		"TESTOPS_MONO_REF=" + shellQuote(req.MonoRef),
		"TESTOPS_MONO_REPO=" + shellQuote(req.MonoRepo),
		"TESTOPS_RUN_BY=" + shellQuote(req.RunBy),
		"TESTOPS_TEAM=" + shellQuote(req.Team),
		"TESTOPS_PROJECT=" + shellQuote(req.Project),
		"TESTOPS_TEST_ID=" + shellQuote(req.TestID),
		"TESTOPS_BRANCH=" + shellQuote(req.MonoRef),
		"TESTOPS_COMMIT=" + shellQuote(req.MonoRef),
	}
	return strings.Join(lines, "\n") + "\n"
}

func shellQuote(value string) string {
	return "'" + strings.ReplaceAll(value, "'", `'\''`) + "'"
}

func sanitizeForID(value string) string {
	var b strings.Builder
	for _, r := range value {
		if (r >= 'A' && r <= 'Z') || (r >= 'a' && r <= 'z') || (r >= '0' && r <= '9') || r == '.' || r == '_' || r == '-' {
			b.WriteRune(r)
		} else {
			b.WriteByte('_')
		}
	}
	out := b.String()
	if len(out) > 80 {
		return out[:80]
	}
	return out
}

func (s *server) status() (statusResponse, error) {
	last, _ := readJSONMap(filepath.Join(s.cfg.stateDir, "status", "last-run.json"))
	return statusResponse{
		Queue:        listFiles(s.cfg.queueDir, "*.env"),
		Running:      listFiles(filepath.Join(s.cfg.stateDir, "running"), "*.env"),
		Done:         listFiles(filepath.Join(s.cfg.stateDir, "done"), "*.env"),
		Failed:       listFiles(filepath.Join(s.cfg.stateDir, "failed"), "*.env"),
		StatusFiles:  listFiles(filepath.Join(s.cfg.stateDir, "status"), "*.json"),
		LastRun:      last,
		DashboardURL: s.cfg.dashboardURL,
		Paths: map[string]string{
			"queue":  s.cfg.queueDir,
			"state":  s.cfg.stateDir,
			"logs":   s.cfg.logDir,
			"status": filepath.Join(s.cfg.stateDir, "status"),
		},
	}, nil
}

func listFiles(dir, pattern string) []fileEntry {
	matches, _ := filepath.Glob(filepath.Join(dir, pattern))
	out := make([]fileEntry, 0, len(matches))
	for _, path := range matches {
		info, err := os.Stat(path)
		if err != nil || info.IsDir() {
			continue
		}
		out = append(out, fileEntry{
			Name:    info.Name(),
			Path:    path,
			Size:    info.Size(),
			ModTime: info.ModTime().Format(time.RFC3339),
		})
	}
	sort.Slice(out, func(i, j int) bool { return out[i].ModTime > out[j].ModTime })
	if len(out) > maxListedFiles {
		return out[:maxListedFiles]
	}
	return out
}

func readJSONMap(path string) (map[string]any, error) {
	raw, err := os.ReadFile(path)
	if err != nil {
		return nil, err
	}
	var doc map[string]any
	if err := json.Unmarshal(raw, &doc); err != nil {
		return nil, err
	}
	return doc, nil
}

func writeJSON(w http.ResponseWriter, value any, status int) {
	w.Header().Set("Content-Type", "application/json")
	w.WriteHeader(status)
	_ = json.NewEncoder(w).Encode(value)
}

var pageTmpl = template.Must(template.New("page").Parse(`<!doctype html>
<html lang="en">
<head>
  <meta charset="utf-8">
  <meta name="viewport" content="width=device-width, initial-scale=1">
  <title>TestOps Controller</title>
  <style>
    body { font-family: system-ui, -apple-system, Segoe UI, sans-serif; margin: 2rem; color: #1f2937; }
    main { max-width: 1100px; margin: 0 auto; }
    h1 { margin-bottom: .25rem; }
    h2 { margin-top: 2rem; }
    form, section { border: 1px solid #d1d5db; border-radius: 8px; padding: 1rem; }
    label { display: block; margin: .75rem 0 .25rem; font-weight: 600; }
    input { width: min(38rem, 100%); padding: .5rem; border: 1px solid #9ca3af; border-radius: 6px; }
    button { margin-top: 1rem; padding: .6rem .9rem; border: 0; border-radius: 6px; background: #14532d; color: white; font-weight: 700; cursor: pointer; }
    table { width: 100%; border-collapse: collapse; margin-top: .5rem; }
    th, td { padding: .45rem; border-bottom: 1px solid #e5e7eb; text-align: left; font-size: .92rem; }
    code { background: #f3f4f6; padding: .1rem .25rem; border-radius: 4px; }
    .muted { color: #6b7280; }
    .grid { display: grid; grid-template-columns: repeat(auto-fit, minmax(240px, 1fr)); gap: 1rem; }
    .pill { display: inline-block; padding: .15rem .45rem; border-radius: 999px; background: #e5e7eb; }
  </style>
</head>
<body>
<main>
  <h1>TestOps Controller</h1>
  <p class="muted">Safe RDMA gate submitter. The worker still owns execution and the lab lock.</p>

  <form method="post" action="/api/rdma/submit">
    <h2>Submit RDMA Gate</h2>
    <label for="mono_ref">mono ref</label>
    <input id="mono_ref" name="mono_ref" value="main" required>
    <label for="run_by">run by</label>
    <input id="run_by" name="run_by" value="testops-controller" required>
    <label for="token">submit token, if configured</label>
    <input id="token" name="token" type="password">
    <input type="hidden" name="team" value="rdma">
    <input type="hidden" name="project" value="rdma-ci">
    <input type="hidden" name="test_id" value="rdma-unified-lab-gate">
    <button type="submit">Queue RDMA Gate</button>
  </form>

  <h2>Status</h2>
  <div class="grid">
    <section><strong>Queued</strong> <span class="pill">{{len .Queue}}</span></section>
    <section><strong>Running</strong> <span class="pill">{{len .Running}}</span></section>
    <section><strong>Done</strong> <span class="pill">{{len .Done}}</span></section>
    <section><strong>Failed</strong> <span class="pill">{{len .Failed}}</span></section>
  </div>

  <p><a href="{{.DashboardURL}}">Open dashboard</a> · <a href="/api/status">JSON status</a></p>

  <h2>Last Run</h2>
  {{if .LastRun}}
  <section>
    {{range $k, $v := .LastRun}}<div><code>{{$k}}</code>: {{$v}}</div>{{end}}
  </section>
  {{else}}
  <p class="muted">No status file yet. The next worker-run request will create one.</p>
  {{end}}

  <h2>Running</h2>
  {{template "files" .Running}}
  <h2>Queue</h2>
  {{template "files" .Queue}}
  <h2>Recent Done</h2>
  {{template "files" .Done}}
  <h2>Recent Failed</h2>
  {{template "files" .Failed}}
</main>
</body>
</html>
{{define "files"}}
{{if .}}
<table><thead><tr><th>Name</th><th>Modified</th><th>Size</th></tr></thead><tbody>
{{range .}}<tr><td><code>{{.Name}}</code></td><td>{{.ModTime}}</td><td>{{.Size}}</td></tr>{{end}}
</tbody></table>
{{else}}<p class="muted">None.</p>{{end}}
{{end}}`))
