package v3block

import (
	"context"
	"encoding/json"
	"fmt"
	"strings"
	"time"

	tr "github.com/seaweedfs/artifactory/testops"
	"github.com/seaweedfs/artifactory/testops/actions"
)

// V3 status response — only the fields we care about. Deliberately does NOT
// include V2-shaped fields (Role, HasLease) — see v3-phase-15-control-plane-evolution.md §3.
type v3StatusResponse struct {
	Volumes []struct {
		VolumeID  string `json:"volume_id"`
		Primary   string `json:"primary"`         // replica_id of current primary, or empty
		Epoch     uint64 `json:"epoch"`
		EV        uint64 `json:"endpoint_version"`
		Mode      string `json:"mode"`            // recovering | ready | superseded | etc
		Supersede bool   `json:"supersede"`
	} `json:"volumes"`
}

// v3Status returns V3-shaped status for a volume.
//
// Two modes (one of master_url OR log_path required):
//   master_url    HTTP base URL of the master (canonical, requires G17-lite /status endpoint — not yet shipped)
//   log_path      path to a blockvolume daemon log; greps last "adopted as PRIMARY at <r>@N" line (interim mode)
//   volume_id     volume to query (required)
//
// Interim log-path mode is the only mode that works today; it parses the most
// recent "blockvolume: volume <vol> adopted as PRIMARY at <replica>@<epoch>" line.
// When G17-lite ships an HTTP /status endpoint on blockmaster, scenarios can
// switch to master_url mode without changing action semantics.
//
// SaveAs writes:
//   <save_as>_primary   primary replica_id, or empty if no primary
//   <save_as>_epoch     epoch as decimal string (HTTP mode only; log mode parses from log line)
func v3Status(ctx context.Context, actx *tr.ActionContext, act tr.Action) (map[string]string, error) {
	node, err := actions.GetNode(actx, act.Node)
	if err != nil {
		return nil, fmt.Errorf("v3_status: %w", err)
	}
	volumeID := requireParam(act, actx, "volume_id")
	if volumeID == "" {
		return nil, fmt.Errorf("v3_status: volume_id param required")
	}
	masterURL := requireParam(act, actx, "master_url")
	logPath := requireParam(act, actx, "log_path")
	if masterURL == "" && logPath == "" {
		return nil, fmt.Errorf("v3_status: one of master_url or log_path required")
	}

	if logPath != "" {
		return v3StatusFromLog(ctx, actx, act, node, logPath, volumeID)
	}

	resp, err := fetchStatus(ctx, node, masterURL)
	if err != nil {
		return nil, fmt.Errorf("v3_status: %w", err)
	}
	for _, v := range resp.Volumes {
		if v.VolumeID == volumeID {
			out := map[string]string{
				"value":   v.Primary,
				"primary": v.Primary,
				"epoch":   fmt.Sprintf("%d", v.Epoch),
				"ev":      fmt.Sprintf("%d", v.EV),
				"mode":    v.Mode,
			}
			actx.Log("  v3_status(%s): primary=%s epoch=%d ev=%d mode=%s",
				volumeID, v.Primary, v.Epoch, v.EV, v.Mode)
			if act.SaveAs != "" {
				actx.Vars[act.SaveAs+"_primary"] = v.Primary
				actx.Vars[act.SaveAs+"_epoch"] = out["epoch"]
				actx.Vars[act.SaveAs+"_ev"] = out["ev"]
				actx.Vars[act.SaveAs+"_mode"] = v.Mode
			}
			return out, nil
		}
	}
	return nil, fmt.Errorf("v3_status: volume %q not in master /status", volumeID)
}

// v3StatusFromLog parses the most recent "adopted as PRIMARY" log line.
// Pattern: "blockvolume: volume <volume_id> adopted as PRIMARY at <replica_id>@<epoch>"
func v3StatusFromLog(ctx context.Context, actx *tr.ActionContext, act tr.Action, node nodeRunner, logPath, volumeID string) (map[string]string, error) {
	pattern := fmt.Sprintf("blockvolume: volume %s adopted as PRIMARY at ", volumeID)
	cmd := fmt.Sprintf("grep -F %q %q 2>/dev/null | tail -1", pattern, logPath)
	stdout, _, _, _ := node.Run(ctx, cmd)
	line := strings.TrimSpace(stdout)
	if line == "" {
		return map[string]string{"value": "", "primary": "", "mode": "no_primary_yet"}, nil
	}
	// Extract "<replica>@<epoch>" suffix.
	idx := strings.LastIndex(line, "adopted as PRIMARY at ")
	if idx < 0 {
		return nil, fmt.Errorf("v3_status: malformed log line: %s", line)
	}
	tail := strings.TrimSpace(line[idx+len("adopted as PRIMARY at "):])
	at := strings.IndexByte(tail, '@')
	if at < 0 {
		return nil, fmt.Errorf("v3_status: cannot parse replica@epoch from %q", tail)
	}
	primary := tail[:at]
	epoch := tail[at+1:]
	// Trim trailing tokens (e.g., " (EV=1)")
	if sp := strings.IndexAny(epoch, " \t)"); sp >= 0 {
		epoch = epoch[:sp]
	}
	out := map[string]string{
		"value":   primary,
		"primary": primary,
		"epoch":   epoch,
		"mode":    "primary",
	}
	actx.Log("  v3_status(%s) [log]: primary=%s epoch=%s", volumeID, primary, epoch)
	if act.SaveAs != "" {
		actx.Vars[act.SaveAs+"_primary"] = primary
		actx.Vars[act.SaveAs+"_epoch"] = epoch
		actx.Vars[act.SaveAs+"_mode"] = "primary"
	}
	return out, nil
}

// v3WaitPrimary polls until volume_id has primary == expected_replica_id, or deadline expires.
//
// Two modes (one of master_url OR log_path required):
//   master_url           HTTP /status (G17-lite forward-carry; not yet shipped)
//   log_path             grep blockvolume daemon log for adoption line (works today)
//
// Required params:
//   volume_id            volume to query
//   expected_replica_id  expected primary replica id (e.g. r1)
//
// Optional params:
//   timeout_seconds      default 30
//   poll_ms              default 500
func v3WaitPrimary(ctx context.Context, actx *tr.ActionContext, act tr.Action) (map[string]string, error) {
	node, err := actions.GetNode(actx, act.Node)
	if err != nil {
		return nil, fmt.Errorf("v3_wait_primary: %w", err)
	}
	volumeID := requireParam(act, actx, "volume_id")
	expected := requireParam(act, actx, "expected_replica_id")
	if volumeID == "" || expected == "" {
		return nil, fmt.Errorf("v3_wait_primary: volume_id and expected_replica_id required")
	}
	masterURL := requireParam(act, actx, "master_url")
	logPath := requireParam(act, actx, "log_path")
	if masterURL == "" && logPath == "" {
		return nil, fmt.Errorf("v3_wait_primary: one of master_url or log_path required")
	}
	timeoutSec := actions.ParseInt(optionalParam(act, "timeout_seconds"), 30)
	pollMs := actions.ParseInt(optionalParam(act, "poll_ms"), 500)
	start := time.Now()
	deadline := start.Add(time.Duration(timeoutSec) * time.Second)
	var lastSeen string

	for time.Now().Before(deadline) {
		select {
		case <-ctx.Done():
			return nil, ctx.Err()
		default:
		}
		var primary string
		if logPath != "" {
			res, err := v3StatusFromLog(ctx, actx, act, node, logPath, volumeID)
			if err == nil {
				primary = res["primary"]
			}
		} else {
			resp, err := fetchStatus(ctx, node, masterURL)
			if err == nil {
				for _, v := range resp.Volumes {
					if v.VolumeID == volumeID {
						primary = v.Primary
						break
					}
				}
			}
		}
		if primary != "" {
			lastSeen = primary
			if primary == expected {
				actx.Log("  v3_wait_primary(%s): primary=%s after %v", volumeID, expected, time.Since(start))
				return map[string]string{"value": expected}, nil
			}
		}
		time.Sleep(time.Duration(pollMs) * time.Millisecond)
	}
	return nil, fmt.Errorf("v3_wait_primary: timeout after %ds; last_seen_primary=%q expected=%q",
		timeoutSec, lastSeen, expected)
}

// v3AssertNoAuthorityLeak greps a log file for V2-shaped authority field names
// that V3 forbids per v3-phase-15-control-plane-evolution.md §7. Used after
// daemon shutdown to verify V3 daemons did not emit V2 lease/promote/demote
// vocabulary in their logs.
//
// Required params:
//   log_path   path to the log file to grep
//
// Optional params:
//   forbidden  comma-separated list of forbidden tokens
//              (default: "Role=,HasLease=,promoted=,demoted=,lease_ttl=")
func v3AssertNoAuthorityLeak(ctx context.Context, actx *tr.ActionContext, act tr.Action) (map[string]string, error) {
	node, err := actions.GetNode(actx, act.Node)
	if err != nil {
		return nil, fmt.Errorf("v3_assert_no_authority_leak: %w", err)
	}
	logPath := requireParam(act, actx, "log_path")
	if logPath == "" {
		return nil, fmt.Errorf("v3_assert_no_authority_leak: log_path param required")
	}
	forbiddenStr := optionalParam(act, "forbidden")
	if forbiddenStr == "" {
		forbiddenStr = "Role=,HasLease=,promoted=,demoted=,lease_ttl="
	}
	tokens := strings.Split(forbiddenStr, ",")
	for i := range tokens {
		tokens[i] = strings.TrimSpace(tokens[i])
	}

	for _, t := range tokens {
		if t == "" {
			continue
		}
		// grep -q exits 0 if found, 1 if not found. We pass when not found.
		cmd := fmt.Sprintf("grep -F -q %q %q 2>/dev/null && echo HIT || echo CLEAN", t, logPath)
		stdout, _, _, _ := node.Run(ctx, cmd)
		verdict := strings.TrimSpace(stdout)
		if verdict == "HIT" {
			return nil, fmt.Errorf(
				"v3_assert_no_authority_leak: forbidden token %q present in %s — V3 forbids V2 authority semantics in logs",
				t, logPath,
			)
		}
	}
	actx.Log("  v3_assert_no_authority_leak: %s clean (checked %d tokens)", logPath, len(tokens))
	return map[string]string{"value": "clean"}, nil
}

// fetchStatus runs `curl -s <master_url>/status` on the node and parses the response.
func fetchStatus(ctx context.Context, node nodeRunner, masterURL string) (*v3StatusResponse, error) {
	url := strings.TrimRight(masterURL, "/") + "/status"
	cmd := fmt.Sprintf("curl -s -m 2 %q 2>/dev/null", url)
	stdout, _, code, err := node.Run(ctx, cmd)
	if err != nil || code != 0 {
		return nil, fmt.Errorf("curl %s: code=%d err=%v", url, code, err)
	}
	var resp v3StatusResponse
	if err := json.Unmarshal([]byte(stdout), &resp); err != nil {
		return nil, fmt.Errorf("parse status response: %w (body: %s)", err, truncate(stdout, 200))
	}
	return &resp, nil
}

func truncate(s string, n int) string {
	if len(s) <= n {
		return s
	}
	return s[:n] + "..."
}
