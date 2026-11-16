package testrunner

import (
	"encoding/json"
	"fmt"
	"os"
	"path/filepath"
	"strings"
)

// BundleValidationOptions controls offline validation of a completed
// run/suite bundle. It intentionally works on loose JSON maps so product-owned
// suite wrappers can add fields without requiring a platform schema release.
type BundleValidationOptions struct {
	RequirePass         bool
	RequireTiming       bool
	RequireChildBundles bool
	ExpectScenario      string
	ExpectCommitPrefix  string
	ExpectedChildren    []string
}

// BundleValidationReport is a machine-readable summary of validate-bundle.
type BundleValidationReport struct {
	Dir           string   `json:"dir"`
	OK            bool     `json:"ok"`
	RunID         string   `json:"run_id,omitempty"`
	Scenario      string   `json:"scenario,omitempty"`
	Status        string   `json:"status,omitempty"`
	ProductCommit string   `json:"product_commit,omitempty"`
	Errors        []string `json:"errors,omitempty"`
}

// ValidateBundle verifies the result/status metadata for a run bundle without
// touching the test lab. It is the platform-level counterpart to product-side
// post-run artifact inspection.
func ValidateBundle(dir string, opts BundleValidationOptions) (*BundleValidationReport, error) {
	result, err := readJSONMap(filepath.Join(dir, "result.json"))
	if err != nil {
		return nil, err
	}
	status, err := readJSONMap(filepath.Join(dir, "status.json"))
	if err != nil {
		return nil, err
	}
	manifest, _ := readJSONMap(filepath.Join(dir, "manifest.json"))
	provenance, _ := readJSONMap(filepath.Join(dir, "provenance.json"))

	report := &BundleValidationReport{
		Dir:           dir,
		RunID:         firstStringIn([]map[string]any{result, status}, "run_id"),
		Scenario:      firstStringIn([]map[string]any{result, status, manifest}, "scenario", "name", "scenario_name"),
		Status:        firstStringIn([]map[string]any{result, status, manifest}, "status", "state"),
		ProductCommit: firstCommit(result, status, manifest, provenance),
	}
	var errors []string

	resultRunID := stringField(result, "run_id")
	statusRunID := stringField(status, "run_id")
	if resultRunID == "" || statusRunID == "" || resultRunID != statusRunID {
		errors = append(errors, fmt.Sprintf("run_id differs between result.json and status.json: %q vs %q", resultRunID, statusRunID))
	}
	if r, s := firstStringIn([]map[string]any{result}, "scenario", "name"), firstStringIn([]map[string]any{status}, "scenario"); r != "" && s != "" && r != s {
		errors = append(errors, fmt.Sprintf("scenario differs between result.json and status.json: %q vs %q", r, s))
	}
	if opts.ExpectScenario != "" && report.Scenario != opts.ExpectScenario {
		errors = append(errors, fmt.Sprintf("scenario %q does not match expected %q", report.Scenario, opts.ExpectScenario))
	}

	resultStatus := normalizeBundleStatus(firstStringIn([]map[string]any{result}, "status"))
	statusState := normalizeBundleStatus(firstStringIn([]map[string]any{status}, "state", "status"))
	if resultStatus != "" && statusState != "" && resultStatus != statusState {
		errors = append(errors, fmt.Sprintf("result/status terminal state differs: %q vs %q", resultStatus, statusState))
	}
	if opts.RequirePass {
		if resultStatus != "pass" {
			errors = append(errors, fmt.Sprintf("result status is %q, expected pass", firstStringIn([]map[string]any{result}, "status")))
		}
		if statusState != "pass" {
			errors = append(errors, fmt.Sprintf("status state is %q, expected pass", firstStringIn([]map[string]any{status}, "state", "status")))
		}
	}

	if opts.RequireTiming {
		requireTiming("result.json", result, opts.RequirePass, &errors)
		requireTiming("status.json", status, opts.RequirePass, &errors)
	}

	if opts.ExpectCommitPrefix != "" {
		var candidates []string
		collectCommitCandidates(&candidates, result)
		collectCommitCandidates(&candidates, status)
		collectCommitCandidates(&candidates, manifest)
		collectCommitCandidates(&candidates, provenance)
		matched := false
		for _, c := range candidates {
			if strings.HasPrefix(c, opts.ExpectCommitPrefix) {
				matched = true
				break
			}
		}
		if !matched {
			errors = append(errors, fmt.Sprintf("no commit field matches prefix %q (candidates=%v)", opts.ExpectCommitPrefix, candidates))
		}
	}

	if len(opts.ExpectedChildren) > 0 {
		validateChildBundles(dir, result, status, opts, &errors)
	} else if opts.RequirePass {
		done, doneOK := intField(status, "phases_done")
		total, totalOK := intField(status, "phases_total")
		if doneOK && totalOK && done != total {
			errors = append(errors, fmt.Sprintf("phases_done=%d phases_total=%d", done, total))
		}
	}

	report.Status = coalesceStatus(resultStatus, statusState, report.Status)
	report.Errors = errors
	report.OK = len(errors) == 0
	return report, nil
}

func validateChildBundles(root string, result, status map[string]any, opts BundleValidationOptions, errors *[]string) {
	resultPhases, err := namedObjects(result, "phase_results")
	if err != nil {
		*errors = append(*errors, err.Error())
		return
	}
	statusPhases, err := namedObjects(status, "phases")
	if err != nil {
		*errors = append(*errors, err.Error())
		return
	}
	if got := resultPhases.order; !sameStringSlice(got, opts.ExpectedChildren) {
		*errors = append(*errors, fmt.Sprintf("result child order mismatch: got %v want %v", got, opts.ExpectedChildren))
	}
	if got := statusPhases.order; !sameStringSlice(got, opts.ExpectedChildren) {
		*errors = append(*errors, fmt.Sprintf("status child order mismatch: got %v want %v", got, opts.ExpectedChildren))
	}

	for _, child := range opts.ExpectedChildren {
		rp, rok := resultPhases.Get(child)
		sp, sok := statusPhases.Get(child)
		if !rok || !sok {
			*errors = append(*errors, fmt.Sprintf("%s: missing in result/status phases", child))
			continue
		}
		rs := normalizeBundleStatus(firstStringIn([]map[string]any{rp}, "status", "state"))
		ss := normalizeBundleStatus(firstStringIn([]map[string]any{sp}, "status", "state"))
		if rs != "" && ss != "" && rs != ss {
			*errors = append(*errors, fmt.Sprintf("%s: status differs between result/status: %q vs %q", child, rs, ss))
		}
		if opts.RequirePass && rs != "pass" {
			*errors = append(*errors, fmt.Sprintf("%s: status is %q, expected pass", child, firstStringIn([]map[string]any{rp}, "status", "state")))
		}
		runID := stringField(rp, "run_id")
		statusRunID := stringField(sp, "run_id")
		if runID == "" || statusRunID == "" || runID != statusRunID {
			*errors = append(*errors, fmt.Sprintf("%s: run_id differs between result/status: %q vs %q", child, runID, statusRunID))
		}
		if done, ok := intField(rp, "phases_done"); ok {
			if total, ok := intField(rp, "phases_total"); ok && opts.RequirePass && done != total {
				*errors = append(*errors, fmt.Sprintf("%s: phases_done=%d phases_total=%d", child, done, total))
			}
		}
		if opts.RequireChildBundles && runID != "" {
			// Validate the bundle being inspected, not only an absolute run_dir
			// pointer captured when the suite originally ran. Operators often
			// copy bundles for negative tests or archival; stale absolute paths
			// must not make a broken copy look valid.
			runDir := filepath.Join(root, child, "runs", runID)
			for _, name := range []string{"status.json", "result.json"} {
				if _, err := os.Stat(filepath.Join(runDir, name)); err != nil {
					*errors = append(*errors, fmt.Sprintf("%s: missing child %s at %s", child, name, runDir))
				}
			}
		}
	}
}

type orderedObjects struct {
	order []string
	data  map[string]map[string]any
}

func namedObjects(doc map[string]any, field string) (orderedObjects, error) {
	raw, ok := doc[field]
	if !ok {
		return orderedObjects{}, fmt.Errorf("%s missing", field)
	}
	items, ok := raw.([]any)
	if !ok {
		return orderedObjects{}, fmt.Errorf("%s must be a list", field)
	}
	out := orderedObjects{data: map[string]map[string]any{}}
	for _, item := range items {
		obj, ok := item.(map[string]any)
		if !ok {
			return orderedObjects{}, fmt.Errorf("%s entries must be objects", field)
		}
		name := stringField(obj, "name")
		if name == "" {
			return orderedObjects{}, fmt.Errorf("%s entry missing name", field)
		}
		out.order = append(out.order, name)
		out.data[name] = obj
	}
	return out, nil
}

func (o orderedObjects) Get(name string) (map[string]any, bool) {
	v, ok := o.data[name]
	return v, ok
}

func readJSONMap(path string) (map[string]any, error) {
	data, err := os.ReadFile(path)
	if err != nil {
		return nil, fmt.Errorf("read %s: %w", path, err)
	}
	var out map[string]any
	if err := json.Unmarshal(data, &out); err != nil {
		return nil, fmt.Errorf("parse %s: %w", path, err)
	}
	return out, nil
}

func firstStringIn(docs []map[string]any, keys ...string) string {
	for _, doc := range docs {
		for _, key := range keys {
			if v := stringField(doc, key); v != "" {
				return v
			}
		}
	}
	return ""
}

func stringField(doc map[string]any, key string) string {
	if doc == nil {
		return ""
	}
	if v, ok := doc[key].(string); ok {
		return v
	}
	return ""
}

func intField(doc map[string]any, key string) (int, bool) {
	switch v := doc[key].(type) {
	case float64:
		return int(v), true
	case int:
		return v, true
	default:
		return 0, false
	}
}

func normalizeBundleStatus(s string) string {
	switch strings.ToLower(s) {
	case "pass", "passed":
		return "pass"
	case "fail", "failed":
		return "fail"
	case "cancelled", "canceled":
		return "cancelled"
	case "error":
		return "error"
	case "running", "queued", "pending":
		return strings.ToLower(s)
	default:
		return strings.ToLower(s)
	}
}

func requireTiming(label string, doc map[string]any, requireEnded bool, errors *[]string) {
	if stringField(doc, "started_at") == "" {
		*errors = append(*errors, label+" missing started_at")
	}
	if requireEnded && stringField(doc, "ended_at") == "" {
		*errors = append(*errors, label+" missing ended_at")
	}
	value, ok := doc["wall_clock_s"]
	if !ok {
		*errors = append(*errors, label+" missing wall_clock_s")
		return
	}
	if !positiveNumber(value) {
		*errors = append(*errors, label+" wall_clock_s must be > 0")
	}
}

func positiveNumber(v any) bool {
	switch n := v.(type) {
	case float64:
		return n > 0
	case int:
		return n > 0
	case int64:
		return n > 0
	case json.Number:
		f, err := n.Float64()
		return err == nil && f > 0
	default:
		return false
	}
}

func collectCommitCandidates(out *[]string, doc map[string]any) {
	if doc == nil {
		return
	}
	for _, key := range []string{"product_commit", "source_commit", "git_sha"} {
		if v := stringField(doc, key); v != "" {
			*out = append(*out, v)
		}
	}
	if gitObj, ok := doc["git"].(map[string]any); ok {
		if v := stringField(gitObj, "sha"); v != "" {
			*out = append(*out, v)
		}
	}
}

func firstCommit(docs ...map[string]any) string {
	var candidates []string
	for _, doc := range docs {
		collectCommitCandidates(&candidates, doc)
	}
	if len(candidates) == 0 {
		return ""
	}
	return candidates[0]
}

func coalesceStatus(vals ...string) string {
	for _, v := range vals {
		if v != "" {
			return v
		}
	}
	return ""
}

func sameStringSlice(a, b []string) bool {
	if len(a) != len(b) {
		return false
	}
	for i := range a {
		if a[i] != b[i] {
			return false
		}
	}
	return true
}
