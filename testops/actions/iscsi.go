package actions

import (
	"context"
	"fmt"
	"os/exec"
	"strconv"
	"strings"

	tr "github.com/seaweedfs/artifactory/testops"
	"github.com/seaweedfs/artifactory/testops/infra"
)

// RegisterISCSIActions registers iSCSI client actions.
func RegisterISCSIActions(r *tr.Registry) {
	r.RegisterFunc("iscsi_login", tr.TierBlock, iscsiLogin)
	r.RegisterFunc("iscsi_login_direct", tr.TierBlock, iscsiLoginDirect)
	r.RegisterFunc("iscsi_logout", tr.TierBlock, iscsiLogout)
	r.RegisterFunc("iscsi_discover", tr.TierBlock, iscsiDiscover)
	r.RegisterFunc("iscsi_cleanup", tr.TierBlock, iscsiCleanup)
	r.RegisterFunc("assert_no_active_iscsi_sessions", tr.TierBlock, assertNoActiveISCSISessions)
	r.RegisterFunc("assert_no_processes", tr.TierBlock, assertNoProcesses)
	r.SetRequiredParams("assert_no_processes", []string{"pattern"})
}

// assertNoProcesses runs `pgrep -af <pattern>` on the target node (or
// controller if node is empty) and fails if any matching process is
// found. Used in cleanup phases to surface stale daemons that earlier
// trap-based teardowns may have missed.
//
// Required params:
//
//	pattern   — passed to `pgrep -af`. Plain string; pgrep matches the
//	            full command line. E.g. "blockmaster" matches any
//	            process whose argv contains "blockmaster".
//
// Optional params:
//
//	binary    — defaults to "pgrep" (override e.g. "/usr/bin/pgrep")
//
// Output: { count, matches } — matches is the joined `pgrep` lines
// (empty on PASS).
//
// pgrep exits non-zero (1) when no process matches; this is the PASS
// path. Real exec errors (binary missing) surface as hard failures.
func assertNoProcesses(ctx context.Context, actx *tr.ActionContext, act tr.Action) (map[string]string, error) {
	pattern := act.Params["pattern"]
	if pattern == "" {
		return nil, fmt.Errorf("assert_no_processes: pattern param required")
	}
	bin := act.Params["binary"]
	if bin == "" {
		bin = "pgrep"
	}
	cmd := fmt.Sprintf("%s -af %s", bin, shellQuoteIscsi(pattern))

	stdout, stderr, code, err := iscsiadmRun(ctx, actx, act.Node, cmd)
	if err != nil {
		return nil, fmt.Errorf("assert_no_processes: exec: %w (stderr: %s)", err, strings.TrimSpace(stderr))
	}

	// pgrep exit codes per pgrep(1):
	//   0 = match found
	//   1 = no match
	//   2 = invalid args
	//   3 = fatal error (e.g. permission)
	// We accept 0 (with output parsed below) and 1 (clean PASS). Any
	// other code is a hard failure — typically "binary not found"
	// surfaces as 127 from the shell wrapper.
	if code != 0 && code != 1 {
		return nil, fmt.Errorf("assert_no_processes: %s exit=%d stderr=%s",
			bin, code, strings.TrimSpace(stderr))
	}

	out := strings.TrimSpace(stdout)
	if out == "" {
		return map[string]string{"value": "0", "count": "0", "matches": ""}, nil
	}
	lines := strings.Split(out, "\n")
	return nil, fmt.Errorf("assert_no_processes: %d process(es) match %q:\n%s",
		len(lines), pattern, strings.Join(lines, "\n"))
}

// shellQuoteIscsi single-quote escapes a value for safe shell embed.
// Same logic as actions/build.go's shellQuote but redeclared here to
// avoid cross-file dependency for one-line use.
func shellQuoteIscsi(s string) string {
	if s == "" {
		return "''"
	}
	if !strings.ContainsAny(s, " \t\n'\"\\$`!(){}[]<>?*&|;#~^") {
		return s
	}
	return "'" + strings.ReplaceAll(s, "'", `'\''`) + "'"
}

// assertNoActiveISCSISessions runs `iscsiadm -m session` on the target
// node (or controller if node is empty) and fails the action unless
// the output reports no active sessions.
//
// Optional params:
//
//	iqn_substr — only fail if a session for an IQN containing this
//	             substring is present. Default: any session fails.
//	             Use this to ignore unrelated iSCSI sessions on shared
//	             lab hosts (e.g. existing dm-mpath stacks).
//	binary    — defaults to "iscsiadm" (use "sudo iscsiadm" if needed)
//
// Output: { sessions, count } — raw `iscsiadm` output and matched count.
//
// iscsiadm exits with a non-zero code (e.g. 21) when there are no
// sessions and prints "iscsiadm: No active sessions." This action
// treats both forms ("0 sessions" + non-zero exit, or successful
// session list with zero matching IQNs) as PASS, and failure of the
// command itself (e.g. missing binary) as a hard error.
func assertNoActiveISCSISessions(ctx context.Context, actx *tr.ActionContext, act tr.Action) (map[string]string, error) {
	iqnSubstr := act.Params["iqn_substr"]
	bin := act.Params["binary"]
	if bin == "" {
		bin = "sudo iscsiadm"
	}
	cmd := bin + " -m session"

	stdout, stderr, code, err := iscsiadmRun(ctx, actx, act.Node, cmd)
	if err != nil {
		return nil, fmt.Errorf("assert_no_active_iscsi_sessions: exec: %w", err)
	}
	combined := stdout + "\n" + stderr

	// "iscsiadm: No active sessions." (any exit code) is unambiguous PASS.
	if strings.Contains(combined, "No active sessions") {
		return map[string]string{"sessions": strings.TrimSpace(combined), "count": "0"}, nil
	}
	// Successful command with empty output (some distros) also PASS.
	if code == 0 && strings.TrimSpace(stdout) == "" {
		return map[string]string{"sessions": "", "count": "0"}, nil
	}
	if code != 0 {
		return nil, fmt.Errorf("assert_no_active_iscsi_sessions: %s exit=%d stderr=%s",
			bin, code, strings.TrimSpace(stderr))
	}

	// Parse sessions: each line is "tcp: [N] host:port,tpgt iqn ..."
	matched := 0
	var hits []string
	for _, line := range strings.Split(stdout, "\n") {
		line = strings.TrimSpace(line)
		if line == "" {
			continue
		}
		if iqnSubstr != "" && !strings.Contains(line, iqnSubstr) {
			continue
		}
		matched++
		hits = append(hits, line)
	}

	if matched == 0 {
		return map[string]string{"sessions": strings.TrimSpace(stdout), "count": "0"}, nil
	}
	return nil, fmt.Errorf("assert_no_active_iscsi_sessions: %d active session(s) match%s:\n%s",
		matched, iqnSubstrSuffix(iqnSubstr), strings.Join(hits, "\n"))
}

func iqnSubstrSuffix(s string) string {
	if s == "" {
		return ""
	}
	return " (iqn_substr=" + s + ")"
}

// iscsiadmRun is a thin shim over local exec / remote ssh used by
// iscsi-related assert/probe actions. iscsiadm's "no sessions" exit
// code is non-zero, so callers must inspect output, not just code.
func iscsiadmRun(ctx context.Context, actx *tr.ActionContext, nodeName, cmd string) (string, string, int, error) {
	if nodeName == "" {
		c := exec.CommandContext(ctx, "sh", "-c", cmd)
		out, err := c.Output()
		stdout := string(out)
		stderr := ""
		code := 0
		if err != nil {
			if ee, ok := err.(*exec.ExitError); ok {
				stderr = string(ee.Stderr)
				code = ee.ExitCode()
				err = nil
			}
		}
		return stdout, stderr, code, err
	}
	n, err := GetNode(actx, nodeName)
	if err != nil {
		return "", "", 0, err
	}
	return n.Run(ctx, cmd)
}

// iscsiLogin discovers + logs into the target, returns the device path.
func iscsiLogin(ctx context.Context, actx *tr.ActionContext, act tr.Action) (map[string]string, error) {
	targetName := act.Target
	if targetName == "" {
		return nil, fmt.Errorf("iscsi_login: target is required")
	}

	spec, ok := actx.Scenario.Targets[targetName]
	if !ok {
		return nil, fmt.Errorf("iscsi_login: target %q not in scenario", targetName)
	}

	host, err := GetTargetHost(actx, targetName)
	if err != nil {
		return nil, err
	}

	// Get the initiator node (first available or explicit).
	node, err := GetNode(actx, act.Node)
	if err != nil {
		return nil, fmt.Errorf("iscsi_login: %w", err)
	}

	client := infra.NewISCSIClient(node)
	iqn := spec.IQN()
	port := spec.ISCSIPort

	actx.Log("  discovering %s:%d ...", host, port)
	iqns, err := client.Discover(ctx, host, port)
	if err != nil {
		return nil, fmt.Errorf("iscsi_login discover: %w", err)
	}

	// Find matching IQN.
	found := false
	for _, q := range iqns {
		if q == iqn {
			found = true
			break
		}
	}
	if !found {
		return nil, fmt.Errorf("iscsi_login: IQN %s not found in discovery (got %v)", iqn, iqns)
	}

	actx.Log("  logging in to %s ...", iqn)
	dev, err := client.Login(ctx, iqn)
	if err != nil {
		return nil, fmt.Errorf("iscsi_login: %w", err)
	}

	actx.Log("  device: %s", dev)
	return map[string]string{"value": dev}, nil
}

// iscsiLoginDirect discovers + logs into a target using explicit host, port, iqn params.
// Unlike iscsi_login, it does not require a target spec — useful for cluster-provisioned
// volumes whose iSCSI address comes from the master API response.
func iscsiLoginDirect(ctx context.Context, actx *tr.ActionContext, act tr.Action) (map[string]string, error) {
	host := act.Params["host"]
	if host == "" {
		return nil, fmt.Errorf("iscsi_login_direct: host param required")
	}
	portStr := act.Params["port"]
	if portStr == "" {
		return nil, fmt.Errorf("iscsi_login_direct: port param required")
	}
	port, err := strconv.Atoi(portStr)
	if err != nil {
		return nil, fmt.Errorf("iscsi_login_direct: invalid port %q: %w", portStr, err)
	}
	iqn := act.Params["iqn"]
	if iqn == "" {
		return nil, fmt.Errorf("iscsi_login_direct: iqn param required")
	}

	node, err := GetNode(actx, act.Node)
	if err != nil {
		return nil, fmt.Errorf("iscsi_login_direct: %w", err)
	}

	client := infra.NewISCSIClient(node)

	actx.Log("  discovering %s:%d ...", host, port)
	iqns, derr := client.Discover(ctx, host, port)
	if derr != nil {
		return nil, fmt.Errorf("iscsi_login_direct discover: %w", derr)
	}

	found := false
	for _, q := range iqns {
		if q == iqn {
			found = true
			break
		}
	}
	if !found {
		return nil, fmt.Errorf("iscsi_login_direct: IQN %s not found in discovery (got %v)", iqn, iqns)
	}

	actx.Log("  logging in to %s ...", iqn)
	dev, lerr := client.Login(ctx, iqn)
	if lerr != nil {
		return nil, fmt.Errorf("iscsi_login_direct: %w", lerr)
	}

	actx.Log("  device: %s", dev)
	return map[string]string{"value": dev}, nil
}

func iscsiLogout(ctx context.Context, actx *tr.ActionContext, act tr.Action) (map[string]string, error) {
	targetName := act.Target
	if targetName == "" {
		return nil, fmt.Errorf("iscsi_logout: target is required")
	}

	spec, ok := actx.Scenario.Targets[targetName]
	if !ok {
		return nil, fmt.Errorf("iscsi_logout: target %q not in scenario", targetName)
	}

	node, err := GetNode(actx, act.Node)
	if err != nil {
		return nil, fmt.Errorf("iscsi_logout: %w", err)
	}

	client := infra.NewISCSIClient(node)
	return nil, client.Logout(ctx, spec.IQN())
}

func iscsiDiscover(ctx context.Context, actx *tr.ActionContext, act tr.Action) (map[string]string, error) {
	targetName := act.Target
	if targetName == "" {
		return nil, fmt.Errorf("iscsi_discover: target is required")
	}

	spec, ok := actx.Scenario.Targets[targetName]
	if !ok {
		return nil, fmt.Errorf("iscsi_discover: target %q not in scenario", targetName)
	}

	host, err := GetTargetHost(actx, targetName)
	if err != nil {
		return nil, err
	}

	node, err := GetNode(actx, act.Node)
	if err != nil {
		return nil, fmt.Errorf("iscsi_discover: %w", err)
	}

	client := infra.NewISCSIClient(node)
	iqns, err := client.Discover(ctx, host, spec.ISCSIPort)
	if err != nil {
		return nil, err
	}

	return map[string]string{"value": fmt.Sprintf("%v", iqns)}, nil
}

func iscsiCleanup(ctx context.Context, actx *tr.ActionContext, act tr.Action) (map[string]string, error) {
	node, err := GetNode(actx, act.Node)
	if err != nil {
		return nil, fmt.Errorf("iscsi_cleanup: %w", err)
	}

	client := infra.NewISCSIClient(node)
	return nil, client.CleanupAll(ctx, "iqn.2024-01.com.seaweedfs:")
}
