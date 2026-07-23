package actions

import (
	"context"
	"fmt"
	"sort"
	"strconv"
	"strings"
	"time"

	tr "github.com/seaweedfs/artifactory/testops"
)

// RegisterSystemActions registers system/assert actions.
func RegisterSystemActions(r *tr.Registry) {
	r.RegisterFunc("exec", tr.TierCore, execAction)
	r.RegisterFunc("sleep", tr.TierCore, sleepAction)
	r.RegisterFunc("assert_equal", tr.TierCore, assertEqual)
	r.RegisterFunc("assert_greater", tr.TierCore, assertGreater)
	r.RegisterFunc("assert_status", tr.TierCore, assertStatus)
	r.RegisterFunc("assert_contains", tr.TierCore, assertContains)
	r.RegisterFunc("print", tr.TierCore, printAction)
	r.RegisterFunc("collect_path", tr.TierCore, collectPath)
	r.SetRequiredParams("collect_path", []string{"path"})
	r.RegisterFunc("fsck_ext4", tr.TierBlock, fsckExt4)
	r.RegisterFunc("fsck_xfs", tr.TierBlock, fsckXfs)
	r.RegisterFunc("grep_log", tr.TierCore, grepLog)
}

func execAction(ctx context.Context, actx *tr.ActionContext, act tr.Action) (map[string]string, error) {
	cmd := act.Params["cmd"]
	if cmd == "" {
		return nil, fmt.Errorf("exec: cmd param required")
	}

	// Optional env injection: any param key starting with "env." is
	// treated as an environment variable for the subprocess.
	//
	//   - action: exec
	//     cmd: "bash run.sh"
	//     env.SW_BLOCK_ARTIFACT_DIR: "{{ bundle_dir }}/phases/foo"
	//     env.SW_BLOCK_ITER: "5"
	//
	// Variables are POSIX-quoted and prepended to the command, so this
	// works for both local exec and SSH-dispatched (node:) calls.
	cmd = prefixEnvVars(cmd, act.Params)

	node, err := GetNode(actx, act.Node)
	if err != nil {
		return nil, err
	}

	root := act.Params["root"] == "true"
	var stdout, stderr string
	var code int
	if root {
		stdout, stderr, code, err = node.RunRoot(ctx, cmd)
	} else {
		stdout, stderr, code, err = node.Run(ctx, cmd)
	}
	if err != nil {
		return nil, fmt.Errorf("exec: %w", err)
	}
	if code != 0 {
		return nil, fmt.Errorf("exec: code=%d stderr=%s", code, stderr)
	}

	return map[string]string{"value": strings.TrimSpace(stdout)}, nil
}

// prefixEnvVars walks params for keys starting with "env." and emits
// a POSIX `KEY=value KEY2=value2 cmd` prefix. Keys are sorted for
// deterministic ordering; values are single-quote escaped so embedded
// spaces, $, ` and ' are safe over both local sh -c and SSH. Returns
// cmd unchanged when no env.* params are present.
func prefixEnvVars(cmd string, params map[string]string) string {
	if len(params) == 0 {
		return cmd
	}
	keys := make([]string, 0)
	for k := range params {
		if strings.HasPrefix(k, "env.") && len(k) > 4 {
			keys = append(keys, k)
		}
	}
	if len(keys) == 0 {
		return cmd
	}
	sort.Strings(keys)
	var b strings.Builder
	for _, k := range keys {
		name := k[4:]
		b.WriteString(name)
		b.WriteString("=")
		b.WriteString(execShellQuote(params[k]))
		b.WriteString(" ")
	}
	// Apply the environment to the entire command, not just the first
	// simple command before a shell operator such as && or |.
	b.WriteString("sh -c ")
	b.WriteString(execShellQuote(cmd))
	return b.String()
}

// execShellQuote single-quote escapes a value for POSIX shell. Empty
// becomes an empty quoted string; otherwise wraps in single quotes and escapes any
// embedded single-quote by closing-escaping-reopening.
func execShellQuote(s string) string {
	if s == "" {
		return "''"
	}
	if !strings.ContainsAny(s, " \t\n'\"\\$`!(){}[]<>?*&|;#~^") {
		return s
	}
	return "'" + strings.ReplaceAll(s, "'", `'\''`) + "'"
}

func sleepAction(ctx context.Context, actx *tr.ActionContext, act tr.Action) (map[string]string, error) {
	d := act.Params["duration"]
	if d == "" {
		d = "1s"
	}

	dur, err := time.ParseDuration(d)
	if err != nil {
		return nil, fmt.Errorf("sleep: invalid duration %q: %w", d, err)
	}

	select {
	case <-time.After(dur):
		return nil, nil
	case <-ctx.Done():
		return nil, ctx.Err()
	}
}

func assertEqual(ctx context.Context, actx *tr.ActionContext, act tr.Action) (map[string]string, error) {
	actual := act.Params["actual"]
	expected := act.Params["expected"]

	// Reject empty strings — prevents false positives when an upstream action
	// failed silently and returned empty. "" == "" would hide real failures.
	if actual == "" && expected == "" {
		return nil, fmt.Errorf("assert_equal: both actual and expected are empty — likely upstream action failure")
	}
	if actual == "" {
		return nil, fmt.Errorf("assert_equal: actual is empty (expected %q) — likely upstream action failure", expected)
	}
	if expected == "" {
		return nil, fmt.Errorf("assert_equal: expected is empty (actual %q) — likely upstream action failure", actual)
	}

	if actual != expected {
		return nil, fmt.Errorf("assert_equal: %q != %q", actual, expected)
	}
	return nil, nil
}

func assertGreater(ctx context.Context, actx *tr.ActionContext, act tr.Action) (map[string]string, error) {
	actualStr := act.Params["actual"]
	threshStr := act.Params["threshold"]
	if threshStr == "" {
		threshStr = act.Params["expected"] // backward compat
	}

	actual, err := strconv.ParseFloat(actualStr, 64)
	if err != nil {
		return nil, fmt.Errorf("assert_greater: cannot parse actual %q as number: %w", actualStr, err)
	}
	threshold, err := strconv.ParseFloat(threshStr, 64)
	if err != nil {
		return nil, fmt.Errorf("assert_greater: cannot parse threshold %q as number: %w", threshStr, err)
	}

	if actual <= threshold {
		return nil, fmt.Errorf("assert_greater: %.2f <= %.2f", actual, threshold)
	}
	return nil, nil
}

func assertStatus(ctx context.Context, actx *tr.ActionContext, act tr.Action) (map[string]string, error) {
	tgt, err := getHATarget(actx, act.Target)
	if err != nil {
		return nil, err
	}

	st, err := tgt.Status(ctx)
	if err != nil {
		return nil, fmt.Errorf("assert_status: %w", err)
	}

	if role, ok := act.Params["role"]; ok {
		if st.Role != role {
			return nil, fmt.Errorf("assert_status: role %q != expected %q", st.Role, role)
		}
	}
	if healthy, ok := act.Params["healthy"]; ok {
		expectedHealthy := healthy == "true"
		if st.Healthy != expectedHealthy {
			return nil, fmt.Errorf("assert_status: healthy=%v != expected=%v", st.Healthy, expectedHealthy)
		}
	}
	if hasLease, ok := act.Params["has_lease"]; ok {
		expectedLease := hasLease == "true"
		if st.HasLease != expectedLease {
			return nil, fmt.Errorf("assert_status: has_lease=%v != expected=%v", st.HasLease, expectedLease)
		}
	}

	return nil, nil
}

func assertContains(ctx context.Context, actx *tr.ActionContext, act tr.Action) (map[string]string, error) {
	haystack := act.Params["value"]
	needle := act.Params["contains"]

	if !strings.Contains(haystack, needle) {
		return nil, fmt.Errorf("assert_contains: %q not found in %q", needle, haystack)
	}
	return nil, nil
}

func printAction(ctx context.Context, actx *tr.ActionContext, act tr.Action) (map[string]string, error) {
	msg := act.Params["msg"]
	if msg == "" {
		msg = act.Params["message"]
	}
	actx.Log("  [print] %s", msg)
	return nil, nil
}

// fsckExt4 runs e2fsck -fn on an unmounted ext4 device. Fails if exit code >= 4.
// Params: device (required)
func fsckExt4(ctx context.Context, actx *tr.ActionContext, act tr.Action) (map[string]string, error) {
	device := act.Params["device"]
	if device == "" {
		return nil, fmt.Errorf("fsck_ext4: device param required")
	}

	node, err := GetNode(actx, act.Node)
	if err != nil {
		return nil, err
	}

	stdout, stderr, code, err := node.RunRoot(ctx, fmt.Sprintf("e2fsck -fn %s 2>&1", device))
	if err != nil {
		return nil, fmt.Errorf("fsck_ext4: %w", err)
	}
	// e2fsck exit codes: 0=clean, 1=errors corrected, 2=reboot needed, 4+=serious error.
	if code >= 4 {
		return nil, fmt.Errorf("fsck_ext4: code=%d output=%s stderr=%s", code, stdout, stderr)
	}

	output := strings.TrimSpace(stdout)
	return map[string]string{"value": output}, nil
}

// fsckXfs runs xfs_repair -n on an unmounted XFS device. Fails if non-zero exit.
// Params: device (required)
func fsckXfs(ctx context.Context, actx *tr.ActionContext, act tr.Action) (map[string]string, error) {
	device := act.Params["device"]
	if device == "" {
		return nil, fmt.Errorf("fsck_xfs: device param required")
	}

	node, err := GetNode(actx, act.Node)
	if err != nil {
		return nil, err
	}

	stdout, stderr, code, err := node.RunRoot(ctx, fmt.Sprintf("xfs_repair -n %s 2>&1", device))
	if err != nil {
		return nil, fmt.Errorf("fsck_xfs: %w", err)
	}
	if code != 0 {
		return nil, fmt.Errorf("fsck_xfs: code=%d output=%s stderr=%s", code, stdout, stderr)
	}

	output := strings.TrimSpace(stdout)
	return map[string]string{"value": output}, nil
}

// grepLog counts occurrences of a pattern in a file. Returns count as value.
// Params: path (required), pattern (required)
func grepLog(ctx context.Context, actx *tr.ActionContext, act tr.Action) (map[string]string, error) {
	path := act.Params["path"]
	if path == "" {
		return nil, fmt.Errorf("grep_log: path param required")
	}
	pattern := act.Params["pattern"]
	if pattern == "" {
		return nil, fmt.Errorf("grep_log: pattern param required")
	}

	node, err := GetNode(actx, act.Node)
	if err != nil {
		return nil, err
	}

	cmd := fmt.Sprintf("grep -c -- %s %s || true", execShellQuote(pattern), execShellQuote(path))
	stdout, _, _, err := node.Run(ctx, cmd)
	if err != nil {
		return nil, fmt.Errorf("grep_log: %w", err)
	}

	count := strings.TrimSpace(stdout)
	if count == "" {
		count = "0"
	}

	return map[string]string{"value": count}, nil
}
