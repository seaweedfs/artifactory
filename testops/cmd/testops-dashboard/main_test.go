package main

import (
	"encoding/json"
	"net/http"
	"net/http/httptest"
	"os"
	"path/filepath"
	"strings"
	"testing"
	"time"
)

func TestDashboardSubmitDisabledByDefault(t *testing.T) {
	s := &server{root: t.TempDir()}
	req := httptest.NewRequest(http.MethodPost, "/api/rdma/submit", strings.NewReader(`{"mono_ref":"main"}`))
	req.Header.Set("Content-Type", "application/json")
	rec := httptest.NewRecorder()
	s.handleAPISuite(rec, req)
	if rec.Code != http.StatusNotFound {
		t.Fatalf("status = %d, want %d", rec.Code, http.StatusNotFound)
	}
}

func TestDashboardControllerSubmitWritesQueue(t *testing.T) {
	tmp := t.TempDir()
	s := dashboardTestServer(tmp)
	s.controller.now = func() time.Time { return time.Date(2026, 6, 30, 13, 0, 0, 123, time.UTC) }
	if err := s.ensureControlDirs(); err != nil {
		t.Fatalf("ensure: %v", err)
	}
	req := httptest.NewRequest(http.MethodPost, "/api/rdma/submit", strings.NewReader(`{"mono_ref":"rdma/api","run_by":"dash-test"}`))
	req.Header.Set("Content-Type", "application/json")
	rec := httptest.NewRecorder()
	s.handleAPISuite(rec, req)
	if rec.Code != http.StatusAccepted {
		t.Fatalf("status = %d, want %d; body=%s", rec.Code, http.StatusAccepted, rec.Body.String())
	}
	var resp controlSubmitResponse
	if err := json.Unmarshal(rec.Body.Bytes(), &resp); err != nil {
		t.Fatalf("json: %v", err)
	}
	raw, err := os.ReadFile(resp.QueuePath)
	if err != nil {
		t.Fatalf("read queue: %v", err)
	}
	text := string(raw)
	for _, want := range []string{
		"REQUEST_ID='20260630-130000-000000123-rdma_api-",
		"TESTOPS_SUITE='rdma'",
		"TESTOPS_REF='rdma/api'",
		"TESTOPS_MONO_REF='rdma/api'",
		"TESTOPS_RUN_BY='dash-test'",
	} {
		if !strings.Contains(text, want) {
			t.Fatalf("missing %q in:\n%s", want, text)
		}
	}
}

func TestDashboardControllerSubmitUsesSuiteDefaults(t *testing.T) {
	tmp := t.TempDir()
	s := dashboardTestServer(tmp)
	s.controller.now = func() time.Time { return time.Date(2026, 6, 30, 14, 0, 0, 0, time.UTC) }
	if err := s.ensureControlDirs(); err != nil {
		t.Fatalf("ensure: %v", err)
	}
	req := httptest.NewRequest(http.MethodPost, "/api/rdma/submit", strings.NewReader(`{"run_by":"dash-test"}`))
	req.Header.Set("Content-Type", "application/json")
	rec := httptest.NewRecorder()
	s.handleAPISuite(rec, req)
	if rec.Code != http.StatusAccepted {
		t.Fatalf("status = %d, want %d; body=%s", rec.Code, http.StatusAccepted, rec.Body.String())
	}
	var resp controlSubmitResponse
	if err := json.Unmarshal(rec.Body.Bytes(), &resp); err != nil {
		t.Fatalf("json: %v", err)
	}
	raw, err := os.ReadFile(resp.QueuePath)
	if err != nil {
		t.Fatalf("read queue: %v", err)
	}
	text := string(raw)
	for _, want := range []string{
		"TESTOPS_SUITE='rdma'",
		"TESTOPS_REF='main'",
		"TESTOPS_MONO_REF='main'",
		"TESTOPS_PROJECT='rdma-ci'",
	} {
		if !strings.Contains(text, want) {
			t.Fatalf("missing %q in:\n%s", want, text)
		}
	}
}

func TestDashboardControllerBlockSuiteSubmitWritesQueue(t *testing.T) {
	s := dashboardTestServer(t.TempDir())
	s.controller.now = func() time.Time { return time.Date(2026, 6, 30, 15, 0, 0, 0, time.UTC) }
	if err := s.ensureControlDirs(); err != nil {
		t.Fatalf("ensure: %v", err)
	}
	req := httptest.NewRequest(http.MethodPost, "/api/block/submit", strings.NewReader(`{"ref":"main","run_by":"block-test"}`))
	req.Header.Set("Content-Type", "application/json")
	rec := httptest.NewRecorder()
	s.handleAPISuite(rec, req)
	if rec.Code != http.StatusAccepted {
		t.Fatalf("status = %d, want %d; body=%s", rec.Code, http.StatusAccepted, rec.Body.String())
	}
	var resp controlSubmitResponse
	if err := json.Unmarshal(rec.Body.Bytes(), &resp); err != nil {
		t.Fatalf("json: %v", err)
	}
	raw, err := os.ReadFile(resp.QueuePath)
	if err != nil {
		t.Fatalf("read queue: %v", err)
	}
	text := string(raw)
	for _, want := range []string{
		"TESTOPS_SUITE='block'",
		"TESTOPS_REF='main'",
		"TESTOPS_PROJECT='block-ci'",
		"TESTOPS_RUN_BY='block-test'",
	} {
		if !strings.Contains(text, want) {
			t.Fatalf("missing %q in:\n%s", want, text)
		}
	}
}

func TestDashboardControllerStatusBySuite(t *testing.T) {
	s := dashboardTestServer(t.TempDir())
	if err := s.ensureControlDirs(); err != nil {
		t.Fatalf("ensure: %v", err)
	}
	req := httptest.NewRequest(http.MethodGet, "/api/controller/status?suite=block", nil)
	rec := httptest.NewRecorder()
	s.handleAPIControllerStatus(rec, req)
	if rec.Code != http.StatusOK {
		t.Fatalf("status = %d, want %d; body=%s", rec.Code, http.StatusOK, rec.Body.String())
	}
	var status controlStatus
	if err := json.Unmarshal(rec.Body.Bytes(), &status); err != nil {
		t.Fatalf("json: %v", err)
	}
	if status.Suite != "block" {
		t.Fatalf("suite = %q, want block", status.Suite)
	}
	if got := status.Paths["queue"]; !strings.Contains(got, filepath.Join("queue", "block-ci")) {
		t.Fatalf("queue path = %q", got)
	}
}

func TestDashboardControllerIndexRenders(t *testing.T) {
	tmp := t.TempDir()
	s := dashboardTestServer(tmp)
	if err := s.ensureControlDirs(); err != nil {
		t.Fatalf("ensure: %v", err)
	}
	rdmaSuite := s.controller.suites["rdma"]
	if err := os.WriteFile(filepath.Join(s.queueDir(rdmaSuite), "queued.env"), []byte("x=1\n"), 0o644); err != nil {
		t.Fatalf("write queue: %v", err)
	}
	req := httptest.NewRequest(http.MethodGet, "/", nil)
	rec := httptest.NewRecorder()
	s.handleIndex(rec, req)
	if rec.Code != http.StatusOK {
		t.Fatalf("status = %d", rec.Code)
	}
	body := rec.Body.String()
	for _, want := range []string{"TestOps Controller", "Queue rdma Gate", "project=block-ci", "queued"} {
		if !strings.Contains(body, want) {
			t.Fatalf("body missing %q", want)
		}
	}
}

func dashboardTestServer(root string) *server {
	return &server{
		root: filepath.Join(root, "results"),
		controller: &controlConfig{
			queueRoot: filepath.Join(root, "queue"),
			stateRoot: filepath.Join(root, "state"),
			logRoot:   filepath.Join(root, "logs"),
			suites:    defaultSuites(),
			now:       time.Now,
		},
	}
}
