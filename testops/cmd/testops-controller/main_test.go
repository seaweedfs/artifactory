package main

import (
	"encoding/json"
	"net/http"
	"net/http/httptest"
	"net/url"
	"os"
	"path/filepath"
	"strings"
	"testing"
	"time"
)

func TestSubmitWritesQueueEnv(t *testing.T) {
	tmp := t.TempDir()
	s := testServer(tmp)
	s.now = func() time.Time { return time.Date(2026, 6, 30, 12, 0, 0, 0, time.UTC) }

	req := submitRequest{MonoRef: "rdma/test-branch", RunBy: "agent1"}
	resp, err := s.submit(req)
	if err != nil {
		t.Fatalf("submit: %v", err)
	}
	if resp.State != "queued" {
		t.Fatalf("state = %q, want queued", resp.State)
	}
	raw, err := os.ReadFile(resp.QueuePath)
	if err != nil {
		t.Fatalf("read queue file: %v", err)
	}
	text := string(raw)
	for _, want := range []string{
		"REQUEST_ID='20260630-120000-000000000-rdma_test-branch-",
		"TESTOPS_MONO_REF='rdma/test-branch'",
		"TESTOPS_MONO_REPO='git@github.com:seaweedfs/seaweed-mono.git'",
		"TESTOPS_RUN_BY='agent1'",
		"TESTOPS_TEAM='rdma'",
		"TESTOPS_PROJECT='rdma-ci'",
		"TESTOPS_TEST_ID='rdma-unified-lab-gate'",
	} {
		if !strings.Contains(text, want) {
			t.Fatalf("queue env missing %q in:\n%s", want, text)
		}
	}
}

func TestSubmitRejectsUnsafeRef(t *testing.T) {
	tmp := t.TempDir()
	s := testServer(tmp)
	_, err := s.submit(submitRequest{MonoRef: "main;rm -rf /"})
	if err == nil {
		t.Fatal("expected unsafe ref to fail")
	}
}

func TestSubmitAPIRequiresTokenWhenConfigured(t *testing.T) {
	tmp := t.TempDir()
	s := testServer(tmp)
	s.cfg.token = "secret"

	form := url.Values{"mono_ref": {"main"}}
	req := httptest.NewRequest(http.MethodPost, "/api/rdma/submit", strings.NewReader(form.Encode()))
	req.Header.Set("Content-Type", "application/x-www-form-urlencoded")
	rec := httptest.NewRecorder()
	s.handleSubmit(rec, req)
	if rec.Code != http.StatusUnauthorized {
		t.Fatalf("status = %d, want %d", rec.Code, http.StatusUnauthorized)
	}

	req = httptest.NewRequest(http.MethodPost, "/api/rdma/submit", strings.NewReader(form.Encode()))
	req.Header.Set("Content-Type", "application/x-www-form-urlencoded")
	req.Header.Set("X-TestOps-Token", "secret")
	rec = httptest.NewRecorder()
	s.handleSubmit(rec, req)
	if rec.Code != http.StatusAccepted {
		t.Fatalf("status = %d, want %d; body=%s", rec.Code, http.StatusAccepted, rec.Body.String())
	}
	var out submitResponse
	if err := json.Unmarshal(rec.Body.Bytes(), &out); err != nil {
		t.Fatalf("json: %v", err)
	}
	if out.RequestID == "" || out.QueuePath == "" {
		t.Fatalf("incomplete response: %+v", out)
	}
}

func TestStatusReadsQueueAndLastRun(t *testing.T) {
	tmp := t.TempDir()
	s := testServer(tmp)
	if err := s.ensureDirs(); err != nil {
		t.Fatalf("ensure: %v", err)
	}
	if err := os.WriteFile(filepath.Join(s.cfg.queueDir, "queued.env"), []byte("x=1\n"), 0o644); err != nil {
		t.Fatalf("write queue: %v", err)
	}
	statusDir := filepath.Join(s.cfg.stateDir, "status")
	last := []byte(`{"request_id":"abc","state":"pass"}`)
	if err := os.WriteFile(filepath.Join(statusDir, "last-run.json"), last, 0o644); err != nil {
		t.Fatalf("write last: %v", err)
	}
	st, err := s.status()
	if err != nil {
		t.Fatalf("status: %v", err)
	}
	if len(st.Queue) != 1 || st.Queue[0].Name != "queued.env" {
		t.Fatalf("queue = %+v", st.Queue)
	}
	if got := st.LastRun["state"]; got != "pass" {
		t.Fatalf("last state = %v, want pass", got)
	}
}

func testServer(root string) *server {
	return &server{
		cfg: config{
			queueDir:     filepath.Join(root, "queue"),
			stateDir:     filepath.Join(root, "state"),
			logDir:       filepath.Join(root, "logs"),
			dashboardURL: defaultDashboard,
		},
		now: time.Now,
	}
}
