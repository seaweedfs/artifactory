package v3block

import (
	"context"
	"fmt"
	"strconv"
	"strings"
	"time"

	tr "github.com/seaweedfs/artifactory/testops"
	"github.com/seaweedfs/artifactory/testops/actions"
)

// spawnDaemon backgrounds a daemon via `setsid -f`, then discovers its PID via
// `ps -eo pid,args | grep <bin> | grep <discriminator>`. The discriminator is
// usually a unique flag value like `--replica-id=r1` so multiple instances of
// the same binary can be distinguished. Pass empty discriminator if there will
// only be one instance.
//
// Mirrors V2 infra/target.go::Start().
func spawnDaemon(ctx context.Context, node nodeRunner, bin string, args []string, logPath, discriminator string) (string, error) {
	cmd := fmt.Sprintf("setsid -f %s %s >%s 2>&1",
		shellQuote(bin), strings.Join(quoteAll(args), " "), shellQuote(logPath))
	_, stderr, code, err := node.Run(ctx, cmd)
	if err != nil || code != 0 {
		return "", fmt.Errorf("spawn: code=%d stderr=%s err=%v", code, stderr, err)
	}
	// Allow the kernel a moment to set up the process before we grep for it.
	time.Sleep(100 * time.Millisecond)
	// `--` terminates option parsing so discriminators starting with `--` work.
	psFilter := fmt.Sprintf("grep -F -- %s | grep -v grep", shellQuote(bin))
	if discriminator != "" {
		psFilter += fmt.Sprintf(" | grep -F -- %s", shellQuote(discriminator))
	}
	psCmd := fmt.Sprintf("ps -eo pid,args | %s | awk '{print $1}' | head -1", psFilter)
	stdout, _, _, _ := node.Run(ctx, psCmd)
	pidStr := strings.TrimSpace(stdout)
	if pidStr == "" {
		return "", fmt.Errorf("spawn: pid not found via ps for %s (discriminator=%q); check log %s",
			bin, discriminator, logPath)
	}
	if _, err := strconv.Atoi(pidStr); err != nil {
		return "", fmt.Errorf("spawn: malformed pid %q: %w", pidStr, err)
	}
	return pidStr, nil
}

// shellQuote wraps s in single quotes, escaping any embedded single quotes.
func shellQuote(s string) string {
	if s == "" {
		return "''"
	}
	if !strings.ContainsAny(s, " \t\n'\"\\$`") {
		return s
	}
	return "'" + strings.ReplaceAll(s, "'", `'\''`) + "'"
}

func quoteAll(args []string) []string {
	out := make([]string, len(args))
	for i, a := range args {
		out[i] = shellQuote(a)
	}
	return out
}

// v3StartBlockmaster spawns cmd/blockmaster as a background process.
//
// Required params:
//   bin           absolute path to the blockmaster binary on the node
//   listen        listen address (e.g. 127.0.0.1:9333 or 0.0.0.0:9333)
//   authority_dir directory for the durable authority store
//
// Optional params:
//   topology              path to a topology YAML file (--topology)
//   cluster_spec          path to a cluster-spec YAML (--cluster-spec)
//   lifecycle_store       directory for G9D lifecycle store (--lifecycle-store);
//                         REQUIRED when cluster_spec is set
//   product_loop_interval product loop interval (--lifecycle-product-loop-interval),
//                         e.g. "100ms"; required to drive cluster-spec → assignment
//   expected_slots        --expected-slots-per-volume (defaults to blockmaster's own default of 3)
//   extra_args            free-form whitespace-separated extra flags appended verbatim
//   log_path              where to redirect stderr (default: <run_dir>/blockmaster.log)
//   ready_timeout         seconds to wait for "lock acquired" log line (default 15)
//
// SaveAs writes:
//   <save_as>_pid    process id
//   <save_as>_addr   the address the daemon ended up listening on (echo of listen)
func v3StartBlockmaster(ctx context.Context, actx *tr.ActionContext, act tr.Action) (map[string]string, error) {
	node, err := actions.GetNode(actx, act.Node)
	if err != nil {
		return nil, fmt.Errorf("v3_start_blockmaster: %w", err)
	}
	bin := requireParam(act, actx, "bin")
	if bin == "" {
		return nil, fmt.Errorf("v3_start_blockmaster: bin param required")
	}
	listen := requireParam(act, actx, "listen")
	if listen == "" {
		return nil, fmt.Errorf("v3_start_blockmaster: listen param required")
	}
	authorityDir := requireParam(act, actx, "authority_dir")
	if authorityDir == "" {
		return nil, fmt.Errorf("v3_start_blockmaster: authority_dir param required")
	}
	topology := optionalParam(act, "topology")
	clusterSpec := optionalParam(act, "cluster_spec")
	lifecycleStore := optionalParam(act, "lifecycle_store")
	productLoopInterval := optionalParam(act, "product_loop_interval")
	expectedSlots := optionalParam(act, "expected_slots")
	extraArgs := optionalParam(act, "extra_args")
	logPath := optionalParam(act, "log_path")
	if logPath == "" {
		logPath = fmt.Sprintf("%s/blockmaster.log", actx.TempRoot)
	}
	readyTimeout := actions.ParseInt(optionalParam(act, "ready_timeout"), 15)

	args := []string{
		"--listen", listen,
		"--authority-store", authorityDir,
	}
	if topology != "" {
		args = append(args, "--topology", topology)
	}
	if clusterSpec != "" {
		args = append(args, "--cluster-spec", clusterSpec)
	}
	if lifecycleStore != "" {
		args = append(args, "--lifecycle-store", lifecycleStore)
	}
	if productLoopInterval != "" {
		args = append(args, "--lifecycle-product-loop-interval", productLoopInterval)
	}
	if expectedSlots != "" {
		args = append(args, "--expected-slots-per-volume", expectedSlots)
	}
	if extraArgs != "" {
		for _, a := range strings.Fields(extraArgs) {
			args = append(args, a)
		}
	}

	if _, _, _, err := node.Run(ctx, fmt.Sprintf("mkdir -p %s", shellQuote(authorityDir))); err != nil {
		return nil, fmt.Errorf("v3_start_blockmaster: mkdir authority_dir: %w", err)
	}
	// blockmaster typically singleton — discriminator empty
	pid, err := spawnDaemon(ctx, node, bin, args, logPath, "")
	if err != nil {
		return nil, fmt.Errorf("v3_start_blockmaster: %w", err)
	}

	if err := waitForLogLine(ctx, node, logPath, "blockmaster: lock acquired", readyTimeout); err != nil {
		_, _, _, _ = node.Run(ctx, fmt.Sprintf("kill %s 2>/dev/null || true", pid))
		return nil, fmt.Errorf("v3_start_blockmaster: not ready: %w", err)
	}

	actx.Log("  v3_start_blockmaster: pid=%s listen=%s log=%s", pid, listen, logPath)
	if act.SaveAs != "" {
		actx.Vars[act.SaveAs+"_pid"] = pid
		actx.Vars[act.SaveAs+"_addr"] = listen
		actx.Vars[act.SaveAs+"_log"] = logPath
	}
	return map[string]string{"value": pid}, nil
}

// v3StartBlockvolume spawns cmd/blockvolume as a background process.
//
// Required params:
//   bin            absolute path to the blockvolume binary
//   master         master gRPC address (e.g. 127.0.0.1:9333)
//   server_id      server id (e.g. s1)
//   replica_id     replica id (e.g. r1, r2)
//   volume_id      volume id (e.g. v1)
//   data_addr      data plane listen (e.g. 127.0.0.1:19101)
//   ctrl_addr      control plane listen (e.g. 127.0.0.1:19102)
//   durable_root   directory for durable storage
//
// Optional params:
//   iscsi_listen   if set, expose iSCSI target on this address
//   iscsi_iqn      IQN string when iscsi_listen is set
//   recovery_mode  legacy | dual-lane (default dual-lane)
//   log_path       where to redirect stderr (default: <run_dir>/blockvolume-<replica_id>.log)
//   ready_timeout  seconds to wait for "durable recovered" log line (default 15)
//
// SaveAs writes:
//   <save_as>_pid    process id
//   <save_as>_log    log path
func v3StartBlockvolume(ctx context.Context, actx *tr.ActionContext, act tr.Action) (map[string]string, error) {
	node, err := actions.GetNode(actx, act.Node)
	if err != nil {
		return nil, fmt.Errorf("v3_start_blockvolume: %w", err)
	}
	bin := requireParam(act, actx, "bin")
	master := requireParam(act, actx, "master")
	serverID := requireParam(act, actx, "server_id")
	replicaID := requireParam(act, actx, "replica_id")
	volumeID := requireParam(act, actx, "volume_id")
	dataAddr := requireParam(act, actx, "data_addr")
	ctrlAddr := requireParam(act, actx, "ctrl_addr")
	durableRoot := requireParam(act, actx, "durable_root")
	for k, v := range map[string]string{
		"bin": bin, "master": master, "server_id": serverID, "replica_id": replicaID,
		"volume_id": volumeID, "data_addr": dataAddr, "ctrl_addr": ctrlAddr, "durable_root": durableRoot,
	} {
		if v == "" {
			return nil, fmt.Errorf("v3_start_blockvolume: %s param required", k)
		}
	}
	iscsiListen := optionalParam(act, "iscsi_listen")
	iscsiIQN := optionalParam(act, "iscsi_iqn")
	recoveryMode := optionalParam(act, "recovery_mode")
	if recoveryMode == "" {
		recoveryMode = "dual-lane"
	}
	logPath := optionalParam(act, "log_path")
	if logPath == "" {
		logPath = fmt.Sprintf("%s/blockvolume-%s.log", actx.TempRoot, replicaID)
	}
	readyTimeout := actions.ParseInt(optionalParam(act, "ready_timeout"), 15)

	args := []string{
		"--master", master,
		"--server-id", serverID,
		"--replica-id", replicaID,
		"--volume-id", volumeID,
		"--data-addr", dataAddr,
		"--ctrl-addr", ctrlAddr,
		"--durable-root", durableRoot,
		"--durable-impl", "walstore",
		"--durable-blocks", "256",
		"--durable-blocksize", "4096",
		"--heartbeat-interval", "200ms",
		"--recovery-mode", recoveryMode,
		"--t1-readiness",
	}
	if iscsiListen != "" {
		args = append(args, "--iscsi-listen", iscsiListen)
		if iscsiIQN != "" {
			args = append(args, "--iscsi-iqn", iscsiIQN)
		}
	}

	if _, _, _, err := node.Run(ctx, fmt.Sprintf("mkdir -p %s", shellQuote(durableRoot))); err != nil {
		return nil, fmt.Errorf("v3_start_blockvolume(%s): mkdir durable_root: %w", replicaID, err)
	}
	// Disambiguate by --replica-id since multiple blockvolume instances coexist.
	discriminator := fmt.Sprintf("--replica-id %s", replicaID)
	pid, err := spawnDaemon(ctx, node, bin, args, logPath, discriminator)
	if err != nil {
		return nil, fmt.Errorf("v3_start_blockvolume(%s): %w", replicaID, err)
	}

	if err := waitForLogLine(ctx, node, logPath, "blockvolume: durable recovered", readyTimeout); err != nil {
		_, _, _, _ = node.Run(ctx, fmt.Sprintf("kill %s 2>/dev/null || true", pid))
		return nil, fmt.Errorf("v3_start_blockvolume(%s): not ready: %w", replicaID, err)
	}

	actx.Log("  v3_start_blockvolume(%s): pid=%s log=%s", replicaID, pid, logPath)
	if act.SaveAs != "" {
		actx.Vars[act.SaveAs+"_pid"] = pid
		actx.Vars[act.SaveAs+"_log"] = logPath
	}
	return map[string]string{"value": pid}, nil
}

// v3StartBlockcsi spawns cmd/blockcsi as a background process.
//
// Required params:
//   bin       absolute path to the blockcsi binary
//   endpoint  CSI endpoint (e.g. tcp://127.0.0.1:0 or unix:///tmp/csi.sock)
//   master    blockmaster gRPC address for status lookup
//
// Optional params:
//   node_id        CSI node ID (default hostname)
//   iqn_prefix     fallback IQN prefix (default iqn.2026-05.io.seaweedfs)
//   log_path       where to redirect stderr (default: <run_dir>/blockcsi.log)
//   ready_timeout  seconds to wait for "csi: ready" log line (default 15)
//
// SaveAs writes:
//   <save_as>_pid  process id
//   <save_as>_log  log path
func v3StartBlockcsi(ctx context.Context, actx *tr.ActionContext, act tr.Action) (map[string]string, error) {
	node, err := actions.GetNode(actx, act.Node)
	if err != nil {
		return nil, fmt.Errorf("v3_start_blockcsi: %w", err)
	}
	bin := requireParam(act, actx, "bin")
	endpoint := requireParam(act, actx, "endpoint")
	master := requireParam(act, actx, "master")
	for k, v := range map[string]string{"bin": bin, "endpoint": endpoint, "master": master} {
		if v == "" {
			return nil, fmt.Errorf("v3_start_blockcsi: %s param required", k)
		}
	}
	nodeID := optionalParam(act, "node_id")
	iqnPrefix := optionalParam(act, "iqn_prefix")
	logPath := optionalParam(act, "log_path")
	if logPath == "" {
		logPath = fmt.Sprintf("%s/blockcsi.log", actx.TempRoot)
	}
	readyTimeout := actions.ParseInt(optionalParam(act, "ready_timeout"), 15)

	args := []string{
		"--endpoint", endpoint,
		"--master", master,
	}
	if nodeID != "" {
		args = append(args, "--node-id", nodeID)
	}
	if iqnPrefix != "" {
		args = append(args, "--iqn-prefix", iqnPrefix)
	}

	pid, err := spawnDaemon(ctx, node, bin, args, logPath, "")
	if err != nil {
		return nil, fmt.Errorf("v3_start_blockcsi: %w", err)
	}

	// blockcsi readiness — wait briefly for the process to settle and check it's still running.
	time.Sleep(time.Duration(readyTimeout) * time.Second / 5)
	checkCmd := fmt.Sprintf("kill -0 %s 2>/dev/null && echo alive || echo dead", pid)
	chk, _, _, _ := node.Run(ctx, checkCmd)
	if strings.TrimSpace(chk) != "alive" {
		return nil, fmt.Errorf("v3_start_blockcsi: process exited; see log %s", logPath)
	}

	actx.Log("  v3_start_blockcsi: pid=%s endpoint=%s log=%s", pid, endpoint, logPath)
	if act.SaveAs != "" {
		actx.Vars[act.SaveAs+"_pid"] = pid
		actx.Vars[act.SaveAs+"_log"] = logPath
	}
	return map[string]string{"value": pid}, nil
}

// requireParam returns act.Params[k] if set, else actx.Vars[k] (or "").
func requireParam(act tr.Action, actx *tr.ActionContext, k string) string {
	if v := act.Params[k]; v != "" {
		return v
	}
	return actx.Vars[k]
}

// optionalParam returns act.Params[k] without falling back to vars.
func optionalParam(act tr.Action, k string) string {
	return act.Params[k]
}

// waitForLogLine polls the given log file every 500ms until the substring
// appears or the deadline is hit.
type nodeRunner interface {
	Run(ctx context.Context, cmd string) (string, string, int, error)
}

func waitForLogLine(ctx context.Context, node nodeRunner, logPath, needle string, timeoutSec int) error {
	deadline := time.Now().Add(time.Duration(timeoutSec) * time.Second)
	for time.Now().Before(deadline) {
		select {
		case <-ctx.Done():
			return ctx.Err()
		default:
		}
		cmd := fmt.Sprintf("grep -F %q %q 2>/dev/null | head -1", needle, logPath)
		stdout, _, _, _ := node.Run(ctx, cmd)
		if strings.TrimSpace(stdout) != "" {
			return nil
		}
		time.Sleep(500 * time.Millisecond)
	}
	return fmt.Errorf("timeout after %ds waiting for %q in %s", timeoutSec, needle, logPath)
}
