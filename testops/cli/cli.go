// Package cli is the sw-test-runner command-line entry point.
//
// Product wrappers compose a registry (core actions + their product pack)
// and call cli.Run(register, args). The CLI handles subcommand dispatch,
// scenario loading, run-bundle creation, and output.
//
// Minimal product wrapper:
//
//	package main
//
//	import (
//	    "os"
//	    tr "github.com/seaweedfs/artifactory/testops"
//	    "github.com/seaweedfs/artifactory/testops/actions"
//	    "github.com/seaweedfs/artifactory/testops/cli"
//	    myproduct "github.com/example/my-product/pack"
//	)
//
//	func main() {
//	    register := func(r *tr.Registry) {
//	        actions.RegisterCore(r)
//	        myproduct.RegisterPack(r)
//	    }
//	    os.Exit(cli.Run(register, os.Args[1:]))
//	}
package cli

import (
	"archive/tar"
	"compress/gzip"
	"context"
	"encoding/json"
	"flag"
	"fmt"
	"io"
	"log"
	"os"
	"os/exec"
	"os/signal"
	"path/filepath"
	"regexp"
	"runtime/debug"
	"sort"
	"strings"
	"time"

	tr "github.com/seaweedfs/artifactory/testops"
	"github.com/seaweedfs/artifactory/testops/actions"
	"github.com/seaweedfs/artifactory/testops/infra"
)

// pkgRegister is set by Run; subcommand handlers below call it
// instead of a hard-coded registerAll. This keeps the CLI logic
// agnostic of which product packs are linked.
var pkgRegister func(*tr.Registry)

// Run dispatches a sw-test-runner subcommand against the supplied
// product-pack registration callback. Returns a process exit code.
func Run(register func(*tr.Registry), args []string) int {
	if register == nil {
		fmt.Fprintln(os.Stderr, "cli.Run: register callback is required")
		return 2
	}
	pkgRegister = register

	if len(args) < 1 {
		usage()
		return 1
	}

	switch args[0] {
	case "run":
		runCmd(args[1:])
	case "suite":
		suiteCmd(args[1:])
	case "coordinator":
		coordinatorCmd(args[1:])
	case "agent":
		agentCmd(args[1:])
	case "console":
		consoleCmd(args[1:])
	case "validate":
		validateCmd(args[1:])
	case "validate-bundle":
		return validateBundleCmd(args[1:])
	case "list":
		listCmd()
	case "status":
		return statusCmd(args[1:])
	case "list-runs":
		return listRunsCmd(args[1:])
	case "cancel":
		return cancelCmd(args[1:])
	case "help", "-h", "--help":
		usage()
	default:
		fmt.Fprintf(os.Stderr, "unknown command: %s\n", args[0])
		usage()
		return 1
	}
	return 0
}

func usage() {
	fmt.Fprintf(os.Stderr, `sw-test-runner — YAML-driven test platform for SeaweedFS BlockVol

Usage:
  sw-test-runner run [flags] <scenario.yaml>           Run a test scenario (SSH mode)
  sw-test-runner suite [flags] <suite.yaml>            Deploy once, run N scenarios
  sw-test-runner coordinator [flags] <scenario.yaml>   Run as coordinator (multi-node)
  sw-test-runner agent [flags]                         Run as agent on test node
  sw-test-runner console [flags]                       Start web console server
  sw-test-runner validate <scenario.yaml>              Validate YAML without running
  sw-test-runner validate-bundle [flags] <run-dir>     Validate completed result/status bundle
  sw-test-runner list [flags]                          List registered actions
  sw-test-runner list-runs [-results-dir <dir>]        List run bundles under results-dir
  sw-test-runner status [-results-dir <dir>] [run_id]  Show status.json (or --latest)
  sw-test-runner cancel [-results-dir <dir>] [run_id]  Request cancel for a running run
  sw-test-runner help                                  Show this help

Common flags:
  -tiers <tiers>       Comma-separated enabled tiers: core,block,devops,chaos (default: all)

Run flags:
  -output <path>       Write JSON results to file
  -junit <path>        Write JUnit XML to file
  -html <path>         Write HTML report to file
  -baseline <path>     Compare against baseline JSON
  -artifacts <path>    Collect artifacts on failure to this directory

Coordinator flags:
  -port <port>         Listen port for agent registration (default: 9000)
  -token <token>       Auth token for agent communication
  -dry-run             Print execution plan without running
  -output <path>       Write JSON results to file
  -junit <path>        Write JUnit XML to file
  -html <path>         Write HTML report to file
  -artifacts <path>    Download artifacts from agents to this directory
  -timeout <duration>  Agent registration timeout (default: 30s)

Agent flags:
  -port <port>         Listen port (default: 9100)
  -coordinator <url>   Coordinator URL (e.g. http://192.168.1.100:9000)
  -token <token>       Auth token for coordinator communication
  -nodes <names>       Comma-separated node names this agent handles
  -allow-exec          Enable /exec endpoint for ad-hoc commands
  -persistent          Stay running, re-register with coordinator on each run

Console flags:
  -port <port>            Listen port (default: 9090)
  -token <token>          Auth token for agents
  -scenarios-dir <path>   Directory containing scenario YAML files
`)
}

func runCmd(args []string) {
	fs := flag.NewFlagSet("run", flag.ExitOnError)
	outputPath := fs.String("output", "", "Write JSON results to file (also written to run bundle)")
	junitPath := fs.String("junit", "", "Write JUnit XML to file (also written to run bundle)")
	htmlPath := fs.String("html", "", "Write HTML report to file (also written to run bundle)")
	baselinePath := fs.String("baseline", "", "Compare against baseline JSON")
	artifactsDir := fs.String("artifacts", "", "Collect artifacts on failure to this directory")
	tiers := fs.String("tiers", "", "Comma-separated list of enabled tiers (core,block,devops,chaos)")
	resultsDir := fs.String("results-dir", "results", "Root directory for per-run result bundles")
	noBundle := fs.Bool("no-bundle", false, "Disable automatic run bundle creation")
	allowMutating := fs.Bool("allow-mutating", false, "Permit actions registered as mutating (docker_push, infra-affecting chaos, etc.)")
	envOverrides := newKVFlag()
	fs.Var(envOverrides, "env", "Set scenario.env entry (repeatable). Format: KEY=VALUE. Overrides any same-named entry from the YAML.")
	metaOverrides := newKVFlag()
	fs.Var(metaOverrides, "meta", "Set run metadata (repeatable). Format: KEY=VALUE. Recorded in manifest.json for the dashboard (e.g. project, run_by, team, test_id).")
	fs.Parse(args)

	if fs.NArg() < 1 {
		fmt.Fprintln(os.Stderr, "error: scenario file required")
		os.Exit(1)
	}
	scenarioFile := fs.Arg(0)

	logger := log.New(os.Stderr, "", log.LstdFlags)

	scenario, err := tr.ParseFile(scenarioFile)
	if err != nil {
		logger.Fatalf("parse scenario: %v", err)
	}

	// Apply --env overrides over scenario.Env. CLI wins so an operator
	// can substitute product_root, secrets, profile flags etc. without
	// editing the YAML.
	if len(envOverrides.values) > 0 {
		if scenario.Env == nil {
			scenario.Env = make(map[string]string)
		}
		for k, v := range envOverrides.values {
			scenario.Env[k] = v
		}
	}

	// Create run bundle (automatic unless --no-bundle).
	var bundle *tr.RunBundle
	var statusWriter *tr.StatusWriter
	if !*noBundle {
		bundle, err = tr.CreateRunBundle(*resultsDir, scenarioFile, os.Args)
		if err != nil {
			logger.Printf("warning: failed to create run bundle: %v (continuing without)", err)
		} else {
			logger.Printf("run bundle: %s", bundle.Dir)
			// Merge run-context metadata (project, run_by, team, ...) into the
			// manifest so a shared dashboard can group/filter runs by who/which
			// project ran them. The scenario's own metadata block is already in.
			if len(metaOverrides.values) > 0 {
				if mErr := bundle.MergeMetadata(metaOverrides.values); mErr != nil {
					logger.Printf("warning: failed to record -meta: %v", mErr)
				}
			}
			// Inject run_id / bundle_dir / artifacts_dir into scenario env so
			// phases can route their per-step artifacts under the bundle. The
			// scenario references these as {{ run_id }} / {{ bundle_dir }} /
			// {{ artifacts_dir }} (flat var namespace).
			if scenario.Env == nil {
				scenario.Env = make(map[string]string)
			}
			scenario.Env["run_id"] = bundle.Manifest.RunID
			scenario.Env["bundle_dir"] = bundle.Dir
			scenario.Env["artifacts_dir"] = bundle.ArtifactsDir()

			// Initialize the run-control status writer. status.json
			// + the parent latest pointer are how `swblock status`
			// reports run progress without parsing logs.
			initial := tr.RunStatus{
				RunID:       bundle.Manifest.RunID,
				Scenario:    scenario.Name,
				State:       tr.RunStateQueued,
				PhasesTotal: countSchedulablePhases(scenario.Phases),
			}
			sw, swErr := tr.NewStatusWriter(bundle.Dir, *resultsDir, initial)
			if swErr != nil {
				logger.Printf("warning: status writer init failed: %v (run-control disabled)", swErr)
			} else {
				statusWriter = sw
			}
		}
	}

	// Set up signal handling.
	ctx, cancel := signal.NotifyContext(context.Background(), os.Interrupt)
	defer cancel()

	// Create registry with all actions.
	registry := tr.NewRegistry()
	pkgRegister(registry)
	if *tiers != "" {
		registry.EnableTiers(parseTiers(*tiers))
	}
	registry.AllowMutating = *allowMutating

	logFunc := func(format string, args ...interface{}) {
		logger.Printf(format, args...)
	}

	// Create engine.
	engine := tr.NewEngine(registry, logFunc)

	// Wire run-control: phase progress and cancel signal both flow
	// through the StatusWriter when one was created.
	if statusWriter != nil {
		engine.PhaseHook = func(when, name, state, errMsg string) {
			switch when {
			case "before":
				_ = statusWriter.PhaseStarted(name)
			case "after":
				switch state {
				case string(tr.StatusPass):
					_ = statusWriter.PhaseFinished(name, tr.PhaseStatePass, "")
				case string(tr.StatusFail):
					_ = statusWriter.PhaseFinished(name, tr.PhaseStateFail, errMsg)
				default:
					_ = statusWriter.PhaseFinished(name, state, errMsg)
				}
			}
		}
		engine.CancelCheck = statusWriter.CancelRequested
		_ = statusWriter.SetState(tr.RunStateRunning)
	}

	// Set up infrastructure.
	actx, err := setupActionContext(scenario, logFunc)
	if err != nil {
		logger.Fatalf("setup: %v", err)
	}
	defer cleanupNodes(actx)

	// Hand the run bundle to action handlers via ActionContext so build
	// actions can populate provenance.json. nil-safe for --no-bundle.
	actx.Bundle = bundle

	// Cluster lifecycle: try attach, fall back to managed if needed.
	clusterMgr := tr.NewClusterManager(scenario.Cluster, logFunc)
	if err := clusterMgr.Setup(ctx, actx); err != nil {
		logger.Fatalf("cluster setup: %v", err)
	}
	defer clusterMgr.Teardown(ctx)

	if clusterMgr.Skipped() {
		logger.Printf("scenario skipped: cluster not available (fallback=skip)")
		os.Exit(0)
	}

	// If bundle has an artifacts dir, use it as the default.
	if bundle != nil && *artifactsDir == "" {
		*artifactsDir = bundle.ArtifactsDir()
	}

	// Run scenario.
	result := engine.Run(ctx, scenario, actx)

	// Print summary.
	tr.PrintSummary(os.Stdout, result)

	// Finalize run bundle (always writes result.json, result.xml, result.html).
	if bundle != nil {
		if err := bundle.Finalize(result); err != nil {
			logger.Printf("warning: finalize run bundle: %v", err)
		} else {
			logger.Printf("run bundle finalized: %s", bundle.Dir)
		}
	}

	// Finalize run-control status. The engine PhaseHook already
	// recorded per-phase state; here we set the terminal run-level
	// state from the result.
	if statusWriter != nil {
		terminal, summary := terminalRunState(result)
		if err := statusWriter.Finalize(terminal, summary); err != nil {
			logger.Printf("warning: finalize status: %v", err)
		}
	}

	// Write explicit output files (in addition to the bundle).
	if *outputPath != "" {
		if err := tr.WriteJSON(result, *outputPath); err != nil {
			logger.Printf("write JSON: %v", err)
		}
	}
	if *junitPath != "" {
		if err := tr.WriteJUnitXML(result, *junitPath); err != nil {
			logger.Printf("write JUnit: %v", err)
		}
	}
	if *htmlPath != "" {
		if err := tr.WriteHTMLReport(result, *htmlPath); err != nil {
			logger.Printf("write HTML: %v", err)
		}
	}

	if *baselinePath != "" {
		regressions, err := tr.BaselineCompare(result, *baselinePath)
		if err != nil {
			logger.Printf("baseline compare: %v", err)
		} else if len(regressions) > 0 {
			fmt.Fprintln(os.Stdout, "\nREGRESSIONS:")
			for _, r := range regressions {
				fmt.Fprintf(os.Stdout, "  - %s\n", r)
			}
		} else {
			fmt.Fprintln(os.Stdout, "\nNo regressions detected.")
		}
	}

	// Collect artifacts on failure.
	if result.Status == tr.StatusFail && *artifactsDir != "" {
		collectArtifacts(actx, *artifactsDir, logger)
	}

	if result.Status == tr.StatusFail {
		os.Exit(1)
	}
}

func terminalRunState(result *tr.ScenarioResult) (state, summary string) {
	switch result.Status {
	case tr.StatusPass:
		return tr.RunStatePass, ""
	case tr.StatusFail:
		if result.Cancelled {
			return tr.RunStateCancelled, result.Error
		}
		return tr.RunStateFail, result.Error
	default:
		return tr.RunStateError, result.Error
	}
}

func collectArtifacts(actx *tr.ActionContext, dir string, logger *log.Logger) {
	logger.Printf("collecting artifacts to %s ...", dir)
	// Find any node for dmesg/lsblk collection.
	var clientNode *infra.Node
	for _, n := range actx.Nodes {
		if nn, ok := n.(*infra.Node); ok {
			clientNode = nn
			break
		}
	}
	if clientNode == nil {
		logger.Printf("no nodes available for artifact collection")
		return
	}

	collector := infra.NewArtifactCollector(dir, clientNode, logger)
	for name, tgt := range actx.Targets {
		if lc, ok := tgt.(infra.LogCollector); ok {
			collector.CollectLabeled(lc, name)
		}
	}
}

func suiteCmd(args []string) {
	fs := flag.NewFlagSet("suite", flag.ExitOnError)
	resultsDir := fs.String("results-dir", "", "Override suite evidence.save_to")
	skipDeploy := fs.Bool("skip-deploy", false, "Skip the deploy stage (assume binaries pre-deployed)")
	tiers := fs.String("tiers", "", "Comma-separated list of enabled tiers")
	envOverrides := newKVFlag()
	fs.Var(envOverrides, "env", "Set suite.env entry (repeatable). Format: KEY=VALUE. Overrides any same-named entry from the YAML.")
	fs.Parse(args)

	if fs.NArg() < 1 {
		fmt.Fprintln(os.Stderr, "error: suite YAML file required")
		os.Exit(1)
	}
	suiteFile := fs.Arg(0)

	logger := log.New(os.Stderr, "", log.LstdFlags)

	suite, err := tr.ParseSuiteFile(suiteFile)
	if err != nil {
		logger.Fatalf("parse suite: %v", err)
	}
	if suite.Env == nil {
		suite.Env = make(map[string]string)
	}
	for k, v := range envOverrides.values {
		suite.Env[k] = v
	}

	saveDir := suite.Evidence.SaveTo
	if *resultsDir != "" {
		saveDir = *resultsDir
	}
	if saveDir == "" {
		saveDir = "results/" + suite.Name
	}

	if isNativeSuite(suite) {
		code := runNativeSuite(suiteFile, suite, saveDir, *tiers, logger)
		os.Exit(code)
	}

	ctx, cancel := signal.NotifyContext(context.Background(), os.Interrupt)
	defer cancel()

	registry := tr.NewRegistry()
	pkgRegister(registry)
	if *tiers != "" {
		registry.EnableTiers(parseTiers(*tiers))
	}

	// Build a temporary scenario just for the topology (node connections).
	topoScenario := &tr.Scenario{
		Name:     suite.Name + "-deploy",
		Topology: suite.Topology,
		Env:      suite.Env,
	}

	logFunc := func(format string, args ...interface{}) {
		logger.Printf(format, args...)
	}

	actx, err := setupActionContext(topoScenario, logFunc)
	if err != nil {
		logger.Fatalf("setup nodes: %v", err)
	}
	defer cleanupNodes(actx)

	// Merge suite env into actx vars.
	for k, v := range suite.Env {
		actx.Vars[k] = v
	}

	logger.Printf("=== SUITE: %s (%d scenarios) ===", suite.Name, len(suite.Scenarios))

	// --- Deploy stage ---
	if !*skipDeploy && len(suite.Deploy.Binaries) > 0 {
		logger.Printf("[deploy] killing stale processes...")
		for _, port := range suite.Deploy.KillPorts {
			for _, nodeRunner := range actx.Nodes {
				nodeRunner.Run(ctx, fmt.Sprintf("sudo fuser -k %d/tcp 2>/dev/null; true", port))
			}
		}
		time.Sleep(2 * time.Second)

		logger.Printf("[deploy] cleaning directories...")
		for _, dir := range suite.Deploy.CleanDirs {
			for _, nodeRunner := range actx.Nodes {
				nodeRunner.Run(ctx, fmt.Sprintf("sudo rm -rf %s; true", dir))
			}
		}

		// Build if configured.
		if suite.Deploy.Build != nil {
			logger.Printf("[deploy] building...")
			repoDir := suite.Deploy.Build.RepoDir
			if repoDir == "" {
				repoDir = actx.Vars["repo_dir"]
			}
			if repoDir == "" {
				repoDir = "."
			}
			for _, target := range suite.Deploy.Build.Targets {
				var buildPkg string
				var outputName string
				switch target {
				case "weed":
					buildPkg = "./weed"
					outputName = "weed-linux"
				case "sw-test-runner":
					buildPkg = "./weed/storage/blockvol/testrunner/cmd/sw-test-runner/"
					outputName = "sw-test-runner-linux"
				default:
					logger.Fatalf("[deploy] unknown build target: %s", target)
				}
				goos := suite.Deploy.Build.GOOS
				if goos == "" {
					goos = "linux"
				}
				goarch := suite.Deploy.Build.GOARCH
				if goarch == "" {
					goarch = "amd64"
				}
				buildCmd := fmt.Sprintf("cd %s && GOOS=%s GOARCH=%s CGO_ENABLED=0 go build -o %s %s",
					repoDir, goos, goarch, outputName, buildPkg)
				logger.Printf("[deploy] building %s...", target)
				ln := tr.NewLocalNode("build-host")
				_, stderr, code, err := ln.Run(ctx, buildCmd)
				if err != nil || code != 0 {
					logger.Fatalf("[deploy] build %s failed: code=%d stderr=%s err=%v", target, code, stderr, err)
				}
			}
		}

		// Deploy binaries.
		deployNodes := suite.Deploy.Nodes
		if len(deployNodes) == 0 {
			for name := range actx.Nodes {
				deployNodes = append(deployNodes, name)
			}
		}
		for _, bin := range suite.Deploy.Binaries {
			for _, nodeName := range deployNodes {
				nodeRunner, ok := actx.Nodes[nodeName]
				if !ok {
					logger.Fatalf("[deploy] node %s not found", nodeName)
				}
				logger.Printf("[deploy] uploading %s → %s:%s", bin.Local, nodeName, bin.Remote)
				nodeRunner.Run(ctx, fmt.Sprintf("mkdir -p %s", filepath.Dir(bin.Remote)))
				if err := nodeRunner.Upload(bin.Local, bin.Remote); err != nil {
					logger.Fatalf("[deploy] upload %s to %s: %v", bin.Local, nodeName, err)
				}
				nodeRunner.Run(ctx, fmt.Sprintf("chmod +x %s", bin.Remote))
			}
		}
		logger.Printf("[deploy] complete")
	}

	// --- Test stage: run each scenario ---
	suiteResult := &tr.SuiteResult{
		Name:   suite.Name,
		Status: "PASS",
	}

	for i, sc := range suite.Scenarios {
		scenarioFile := sc.Path
		scenarioID := sc.ID
		if scenarioID == "" {
			scenarioID = fmt.Sprintf("scenario-%d", i+1)
		}

		logger.Printf("")
		logger.Printf("=== [%d/%d] %s: %s ===", i+1, len(suite.Scenarios), scenarioID, scenarioFile)

		scenario, err := tr.ParseFile(scenarioFile)
		if err != nil {
			logger.Printf("SKIP %s: parse error: %v", scenarioID, err)
			suiteResult.Scenarios = append(suiteResult.Scenarios, tr.SuiteScenarioResult{
				ID:   scenarioID,
				Path: scenarioFile,
				Result: &tr.ScenarioResult{
					Name:   scenarioID,
					Status: tr.StatusFail,
				},
			})
			suiteResult.Status = "FAIL"
			continue
		}

		// Create a per-scenario run bundle.
		bundleDir := filepath.Join(saveDir, scenarioID)
		bundle, err := tr.CreateRunBundle(bundleDir, scenarioFile, os.Args)
		if err != nil {
			logger.Printf("warning: create run bundle for %s: %v", scenarioID, err)
		}
		if bundle != nil && scenario.Env == nil {
			scenario.Env = make(map[string]string)
		}
		if bundle != nil {
			scenario.Env["run_id"] = bundle.Manifest.RunID
			scenario.Env["bundle_dir"] = bundle.Dir
			scenario.Env["artifacts_dir"] = bundle.ArtifactsDir()
		}

		// Run scenario on the first node via SSH. This is necessary because
		// scenarios make direct HTTP calls to cluster IPs (e.g. RDMA 10.0.0.x)
		// which are not reachable from the local Windows build host.
		// The runner binary must already be deployed to the node.
		runNode := "m01"
		if suite.Deploy.Nodes != nil && len(suite.Deploy.Nodes) > 0 {
			runNode = suite.Deploy.Nodes[0]
		}
		nodeRunner, ok := actx.Nodes[runNode]
		if !ok {
			logger.Printf("SKIP %s: run node %s not found in topology", scenarioID, runNode)
			suiteResult.Status = "FAIL"
			continue
		}

		// Upload scenario YAML to the run node.
		remoteScenario := "/opt/work/suite-scenario.yaml"
		if err := nodeRunner.Upload(scenarioFile, remoteScenario); err != nil {
			logger.Printf("SKIP %s: upload scenario: %v", scenarioID, err)
			suiteResult.Status = "FAIL"
			continue
		}

		// Execute sw-test-runner on the remote node.
		remoteRunner := "/opt/work/sw-test-runner"
		remoteResultsDir := fmt.Sprintf("results/%s/%s", suite.Name, scenarioID)
		runCmd := fmt.Sprintf("%s run %s --results-dir %s", remoteRunner, remoteScenario, remoteResultsDir)
		logger.Printf("[run] %s on %s", scenarioID, runNode)
		stdout, stderr, code, err := nodeRunner.Run(ctx, runCmd)
		if err != nil {
			logger.Printf("SKIP %s: run error: %v", scenarioID, err)
			suiteResult.Status = "FAIL"
			continue
		}

		// Print remote output.
		if stdout != "" {
			fmt.Print(stdout)
		}
		if stderr != "" {
			fmt.Fprint(os.Stderr, stderr)
		}

		status := tr.StatusPass
		if code != 0 {
			status = tr.StatusFail
			suiteResult.Status = "FAIL"
		}

		// Download results from remote node.
		if bundle != nil {
			if sshNode, ok := nodeRunner.(*infra.Node); ok {
				remoteResultDir := fmt.Sprintf("%s/", remoteResultsDir)
				latestCmd := fmt.Sprintf("ls -td %s*/ 2>/dev/null | head -1", remoteResultDir)
				latestDir, _, _, _ := nodeRunner.Run(ctx, latestCmd)
				latestDir = strings.TrimSpace(latestDir)
				if latestDir != "" {
					for _, fname := range []string{"result.json", "result.xml", "result.html", "manifest.json"} {
						remotePath := latestDir + fname
						localPath := filepath.Join(bundle.Dir, fname)
						if err := sshNode.Download(remotePath, localPath); err != nil {
							logger.Printf("  download %s: %v (skipping)", fname, err)
						}
					}
				}
			}
		}

		suiteResult.Scenarios = append(suiteResult.Scenarios, tr.SuiteScenarioResult{
			ID:   scenarioID,
			Path: scenarioFile,
			Result: &tr.ScenarioResult{
				Name:   scenarioID,
				Status: status,
			},
		})
	}

	// --- Evidence collection ---
	if saveDir != "" {
		timestamp := time.Now().Format("20060102-150405")
		evidenceDir := filepath.Join(saveDir, fmt.Sprintf("%s-%s", suite.Name, timestamp))
		if err := os.MkdirAll(evidenceDir, 0755); err != nil {
			logger.Printf("warning: create evidence dir: %v", err)
		} else {
			logger.Printf("[evidence] collecting to %s", evidenceDir)

			// Pull glog from all nodes — only files created during this suite run.
			glogDir := filepath.Join(evidenceDir, "glog")
			os.MkdirAll(glogDir, 0755)
			for nodeName, nodeRunner := range actx.Nodes {
				sshNode, ok := nodeRunner.(*infra.Node)
				if !ok {
					continue
				}
				for _, pattern := range suite.Evidence.GlogPatterns {
					// Only files modified in the last 10 minutes (covers the run window).
					cmd := fmt.Sprintf("find /tmp -maxdepth 1 -name 'weed.*.INFO.*' -mmin -10 2>/dev/null | sort")
					if !strings.Contains(pattern, "weed") {
						cmd = fmt.Sprintf("ls -t %s 2>/dev/null | head -3", pattern)
					}
					stdout, _, _, err := nodeRunner.Run(ctx, cmd)
					if err != nil {
						continue
					}
					collected := 0
					for _, f := range strings.Split(strings.TrimSpace(stdout), "\n") {
						f = strings.TrimSpace(f)
						if f == "" {
							continue
						}
						localPath := filepath.Join(glogDir, nodeName+"-"+filepath.Base(f))
						sshNode.Download(f, localPath)
						collected++
					}
					if collected > 0 {
						logger.Printf("[evidence] %d glog files from %s", collected, nodeName)
					}
				}
			}

			// Pull debug endpoints via SSH to the run node.
			runNodeName := suite.Evidence.RunNode
			if runNodeName == "" {
				runNodeName = "m01"
			}
			if runNode, ok := actx.Nodes[runNodeName]; ok {
				for _, ep := range suite.Evidence.DebugEndpoints {
					stdout, _, _, err := runNode.Run(ctx, fmt.Sprintf("curl -s --max-time 3 %s 2>/dev/null", ep))
					if err != nil || len(stdout) < 2 {
						continue
					}
					// Derive filename from endpoint URL.
					epName := strings.ReplaceAll(ep, "http://", "")
					epName = strings.ReplaceAll(epName, "/", "_")
					epName = strings.ReplaceAll(epName, ":", "-")
					localPath := filepath.Join(evidenceDir, "debug-"+epName+".json")
					os.WriteFile(localPath, []byte(stdout), 0644)
					logger.Printf("[evidence] debug: %s", localPath)
				}
			}

			// Pull result bundle from run node.
			if runNode, ok := actx.Nodes[runNodeName]; ok {
				if sshNode, ok := runNode.(*infra.Node); ok {
					latestCmd := "ls -td results/*/ 2>/dev/null | head -1"
					latestDir, _, _, _ := runNode.Run(ctx, latestCmd)
					latestDir = strings.TrimSpace(latestDir)
					if latestDir != "" {
						for _, fname := range []string{"result.json", "result.xml", "result.html", "manifest.json", "scenario.yaml"} {
							sshNode.Download(latestDir+fname, filepath.Join(evidenceDir, fname))
						}
					}
				}
			}

			// Copy console log.
			logger.Printf("[evidence] saved to %s", evidenceDir)
		}
	}

	// --- Summary ---
	logger.Printf("")
	logger.Printf("=== SUITE RESULT: %s ===", suiteResult.Status)
	for _, sc := range suiteResult.Scenarios {
		logger.Printf("  [%s] %s: %s", sc.Result.Status, sc.ID, sc.Path)
	}

	if suiteResult.Status == "FAIL" {
		os.Exit(1)
	}
}

func isNativeSuite(suite *tr.SuiteConfig) bool {
	mode := strings.ToLower(strings.TrimSpace(suite.Mode))
	if mode == "chain" || mode == "native" || mode == "local" {
		return true
	}
	return suite.Deploy.Build == nil &&
		len(suite.Deploy.Binaries) == 0 &&
		len(suite.Deploy.KillPorts) == 0 &&
		len(suite.Deploy.CleanDirs) == 0 &&
		len(suite.Topology.Nodes) == 0 &&
		len(suite.Topology.Agents) == 0
}

type nativeSuiteChild struct {
	Name        string `json:"name"`
	Status      string `json:"status"`
	RunID       string `json:"run_id,omitempty"`
	ArtifactDir string `json:"artifact_dir"`
	RunDir      string `json:"run_dir,omitempty"`
	PhasesDone  int    `json:"phases_done,omitempty"`
	PhasesTotal int    `json:"phases_total,omitempty"`
	Error       string `json:"error,omitempty"`
}

type nativeSuiteResult struct {
	SchemaVersion     string             `json:"schema_version"`
	RunID             string             `json:"run_id"`
	Scenario          string             `json:"scenario"`
	SourceCommit      string             `json:"source_commit,omitempty"`
	ProductCommit     string             `json:"product_commit,omitempty"`
	RunnerCommit      string             `json:"runner_commit,omitempty"`
	RemoteProductRoot string             `json:"remote_product_root,omitempty"`
	Status            string             `json:"status"`
	Summary           string             `json:"summary"`
	StartedAt         string             `json:"started_at"`
	EndedAt           string             `json:"ended_at,omitempty"`
	WallClockS        float64            `json:"wall_clock_s,omitempty"`
	PhaseResults      []nativeSuiteChild `json:"phase_results"`
	ArtifactDir       string             `json:"artifact_dir"`
	Artifacts         map[string]string  `json:"artifacts,omitempty"`
	NonClaims         []string           `json:"non_claims,omitempty"`
}

func runNativeSuite(suiteFile string, suite *tr.SuiteConfig, resultsRoot, tiers string, logger *log.Logger) int {
	ctx, cancel := signal.NotifyContext(context.Background(), os.Interrupt)
	defer cancel()

	start := time.Now().UTC()
	runID := fmt.Sprintf("%s-%04x", start.Format("20060102-150405"), start.UnixNano()&0xffff)
	runDir := filepath.Join(resultsRoot, runID)
	if err := os.MkdirAll(runDir, 0755); err != nil {
		logger.Printf("create suite run dir: %v", err)
		return 1
	}
	if err := copyFile(suiteFile, filepath.Join(runDir, "scenario.yaml")); err != nil {
		logger.Printf("warning: copy suite yaml: %v", err)
	}
	suiteLogPath := filepath.Join(runDir, "suite.log")
	suiteLog, _ := os.Create(suiteLogPath)
	if suiteLog != nil {
		defer suiteLog.Close()
	}
	logSuite := func(format string, args ...interface{}) {
		msg := fmt.Sprintf(format, args...)
		line := fmt.Sprintf("[%s] [suite] %s", time.Now().UTC().Format("15:04:05"), msg)
		logger.Print(line)
		if suiteLog != nil {
			fmt.Fprintln(suiteLog, line)
		}
	}

	sourceCommit := gitRevParse(filepath.Dir(suiteFile))
	productCommit := ""
	runnerCommit := runnerGitRevision()
	statusWriter, err := tr.NewStatusWriter(runDir, resultsRoot, tr.RunStatus{
		RunID:             runID,
		Scenario:          suite.Name,
		ProductCommit:     productCommit,
		RunnerCommit:      runnerCommit,
		RemoteProductRoot: suite.Env["product_root"],
		State:             tr.RunStateQueued,
		PhasesTotal:       len(suite.Scenarios),
	})
	if err != nil {
		logger.Printf("status writer init: %v", err)
		return 1
	}
	_ = statusWriter.SetState(tr.RunStateRunning)

	children := initialNativeSuiteChildren(suite, runDir)
	writeSuiteResult := func(status, summary string, terminal bool) {
		endedAt := ""
		if terminal {
			endedAt = time.Now().UTC().Format(time.RFC3339Nano)
		}
		result := nativeSuiteResult{
			SchemaVersion:     "1.0",
			RunID:             runID,
			Scenario:          suite.Name,
			SourceCommit:      sourceCommit,
			ProductCommit:     productCommit,
			RunnerCommit:      runnerCommit,
			RemoteProductRoot: suite.Env["product_root"],
			Status:            status,
			Summary:           summary,
			StartedAt:         start.Format(time.RFC3339Nano),
			EndedAt:           endedAt,
			WallClockS:        time.Since(start).Seconds(),
			PhaseResults:      children,
			ArtifactDir:       runDir,
			Artifacts:         map[string]string{"suite_log": suiteLogPath},
			NonClaims: []string{
				"Runner-native orchestration of child scenarios; each child owns product-level assertions.",
				"Does not add claims beyond the child scenario contracts.",
			},
		}
		if data, err := json.MarshalIndent(result, "", "  "); err == nil {
			_ = os.WriteFile(filepath.Join(runDir, "result.json"), data, 0644)
		}
	}
	writeSuiteResult("running", "suite running", false)

	registry := tr.NewRegistry()
	pkgRegister(registry)
	if tiers != "" {
		registry.EnableTiers(parseTiers(tiers))
	}

	suiteStatus := tr.RunStatePass
	summary := "suite passed"
	suiteBase := filepath.Dir(suiteFile)
	observedProductCommit := ""
	childrenMissingProductCommit := []string{}

	logSuite("run_id=%s", runID)
	logSuite("suite=%s children=%d", suite.Name, len(suite.Scenarios))
	for i, sc := range suite.Scenarios {
		name := sc.ID
		if name == "" {
			name = fmt.Sprintf("scenario-%d", i+1)
		}
		scenarioFile := resolveSuitePath(suiteBase, sc.Path)
		children[i].Name = name
		children[i].ArtifactDir = filepath.Join(runDir, name)
		if err := os.MkdirAll(children[i].ArtifactDir, 0755); err != nil {
			children[i].Status = tr.RunStateError
			children[i].Error = err.Error()
			suiteStatus = tr.RunStateError
			summary = fmt.Sprintf("suite failed at %s", name)
			_ = statusWriter.PhaseStarted(name)
			_ = statusWriter.PhaseFinished(name, tr.PhaseStateFail, err.Error())
			break
		}

		if statusWriter.CancelRequested() {
			suiteStatus = tr.RunStateCancelled
			summary = fmt.Sprintf("cancelled before child %s", name)
			break
		}
		logSuite("run child=%s scenario=%s", name, scenarioFile)
		_ = statusWriter.PhaseStarted(name)
		result, childRunID, childRunDir, err := runNativeSuiteChild(ctx, registry, suite, name, scenarioFile, children[i].ArtifactDir, logger)
		childStatus := tr.RunStateError
		childErr := ""
		childDone := 0
		childTotal := 0
		if result != nil {
			childStatus = string(result.Status)
			childErr = result.Error
		}
		if childRunDir != "" {
			if s, readErr := tr.ReadStatus(childRunDir); readErr == nil {
				childStatus = s.State
				childDone = s.PhasesDone
				childTotal = s.PhasesTotal
				if childErr == "" {
					childErr = s.ErrorSummary
				}
			}
		}
		if err != nil && childErr == "" {
			childErr = err.Error()
		}
		children[i].Status = childStatus
		children[i].RunID = childRunID
		children[i].RunDir = childRunDir
		children[i].PhasesDone = childDone
		children[i].PhasesTotal = childTotal
		children[i].Error = childErr
		commit, commitFound, commitErr := discoverProductCommitFromChildBundle(childRunDir)
		if commitErr != nil {
			childErr = fmt.Sprintf("child product commit evidence invalid: %v", commitErr)
			children[i].Status = tr.RunStateFail
			children[i].RunID = childRunID
			children[i].RunDir = childRunDir
			children[i].PhasesDone = childDone
			children[i].PhasesTotal = childTotal
			children[i].Error = childErr
			_ = statusWriter.PhaseMetadata(name, childRunID, children[i].ArtifactDir, childRunDir, childDone, childTotal)
			_ = statusWriter.PhaseFinished(name, tr.PhaseStateFail, childErr)
			logSuite("FAIL child=%s run_id=%s error=%s", name, childRunID, childErr)
			suiteStatus = tr.RunStateFail
			summary = fmt.Sprintf("suite failed at %s", name)
			break
		}
		if commitFound && commit != "" && observedProductCommit == "" {
			if len(childrenMissingProductCommit) > 0 {
				childErr = fmt.Sprintf("child product commit evidence present at %s but earlier children missing evidence: %s", name, strings.Join(childrenMissingProductCommit, ","))
				children[i].Status = tr.RunStateFail
				children[i].RunID = childRunID
				children[i].RunDir = childRunDir
				children[i].PhasesDone = childDone
				children[i].PhasesTotal = childTotal
				children[i].Error = childErr
				_ = statusWriter.PhaseMetadata(name, childRunID, children[i].ArtifactDir, childRunDir, childDone, childTotal)
				_ = statusWriter.PhaseFinished(name, tr.PhaseStateFail, childErr)
				logSuite("FAIL child=%s run_id=%s error=%s", name, childRunID, childErr)
				suiteStatus = tr.RunStateFail
				summary = fmt.Sprintf("suite failed at %s", name)
				break
			}
			observedProductCommit = commit
			productCommit = commit
			_ = statusWriter.SetProvenance(productCommit, runnerCommit, suite.Env["product_root"])
			logSuite("product_commit=%s source=child:%s", productCommit, name)
		} else if commitFound && commit != "" && commit != observedProductCommit {
			childErr = fmt.Sprintf("child product commit %s differs from suite product commit %s", commit, observedProductCommit)
			children[i].Status = tr.RunStateFail
			children[i].RunID = childRunID
			children[i].RunDir = childRunDir
			children[i].PhasesDone = childDone
			children[i].PhasesTotal = childTotal
			children[i].Error = childErr
			_ = statusWriter.PhaseMetadata(name, childRunID, children[i].ArtifactDir, childRunDir, childDone, childTotal)
			_ = statusWriter.PhaseFinished(name, tr.PhaseStateFail, childErr)
			logSuite("FAIL child=%s run_id=%s error=%s", name, childRunID, childErr)
			suiteStatus = tr.RunStateFail
			summary = fmt.Sprintf("suite failed at %s", name)
			break
		} else if !commitFound {
			if observedProductCommit != "" {
				childErr = fmt.Sprintf("child product commit evidence missing after suite product commit was established as %s", observedProductCommit)
				children[i].Status = tr.RunStateFail
				children[i].RunID = childRunID
				children[i].RunDir = childRunDir
				children[i].PhasesDone = childDone
				children[i].PhasesTotal = childTotal
				children[i].Error = childErr
				_ = statusWriter.PhaseMetadata(name, childRunID, children[i].ArtifactDir, childRunDir, childDone, childTotal)
				_ = statusWriter.PhaseFinished(name, tr.PhaseStateFail, childErr)
				logSuite("FAIL child=%s run_id=%s error=%s", name, childRunID, childErr)
				suiteStatus = tr.RunStateFail
				summary = fmt.Sprintf("suite failed at %s", name)
				break
			}
			childrenMissingProductCommit = append(childrenMissingProductCommit, name)
		}
		_ = statusWriter.PhaseMetadata(name, childRunID, children[i].ArtifactDir, childRunDir, childDone, childTotal)
		if childStatus == string(tr.StatusPass) || childStatus == tr.RunStatePass {
			_ = statusWriter.PhaseFinished(name, tr.PhaseStatePass, "")
			logSuite("PASS child=%s run_id=%s", name, childRunID)
			writeSuiteResult("running", "suite running", false)
			continue
		}
		_ = statusWriter.PhaseFinished(name, tr.PhaseStateFail, childErr)
		logSuite("FAIL child=%s run_id=%s error=%s", name, childRunID, childErr)
		suiteStatus = tr.RunStateFail
		summary = fmt.Sprintf("suite failed at %s", name)
		break
	}

	if suiteStatus == tr.RunStatePass {
		logSuite("PASS: %s", suite.Name)
		writeSuiteResult("pass", summary, true)
		_ = statusWriter.Finalize(tr.RunStatePass, "")
		return 0
	}
	writeSuiteResult(suiteStatus, summary, true)
	_ = statusWriter.Finalize(suiteStatus, summary)
	return 1
}

func initialNativeSuiteChildren(suite *tr.SuiteConfig, runDir string) []nativeSuiteChild {
	children := make([]nativeSuiteChild, 0, len(suite.Scenarios))
	for i, sc := range suite.Scenarios {
		name := sc.ID
		if name == "" {
			name = fmt.Sprintf("scenario-%d", i+1)
		}
		children = append(children, nativeSuiteChild{
			Name:        name,
			Status:      "pending",
			ArtifactDir: filepath.Join(runDir, name),
		})
	}
	return children
}

func runNativeSuiteChild(ctx context.Context, registry *tr.Registry, suite *tr.SuiteConfig, childName, scenarioFile, childDir string, logger *log.Logger) (*tr.ScenarioResult, string, string, error) {
	scenario, err := tr.ParseFile(scenarioFile)
	if err != nil {
		return nil, "", "", fmt.Errorf("parse child scenario: %w", err)
	}
	if scenario.Env == nil {
		scenario.Env = make(map[string]string)
	}
	for k, v := range suite.Env {
		scenario.Env[k] = v
	}
	resultsRoot := filepath.Join(childDir, "runs")
	bundle, err := tr.CreateRunBundle(resultsRoot, scenarioFile, os.Args)
	if err != nil {
		return nil, "", "", fmt.Errorf("create child bundle: %w", err)
	}
	scenario.Env["run_id"] = bundle.Manifest.RunID
	scenario.Env["bundle_dir"] = bundle.Dir
	scenario.Env["artifacts_dir"] = bundle.ArtifactsDir()
	_ = os.WriteFile(filepath.Join(childDir, "child-run.txt"), []byte(bundle.Manifest.RunID+"\n"), 0644)

	statusWriter, err := tr.NewStatusWriter(bundle.Dir, resultsRoot, tr.RunStatus{
		RunID:       bundle.Manifest.RunID,
		Scenario:    scenario.Name,
		State:       tr.RunStateQueued,
		PhasesTotal: countSchedulablePhases(scenario.Phases),
	})
	if err != nil {
		return nil, bundle.Manifest.RunID, bundle.Dir, fmt.Errorf("child status writer: %w", err)
	}

	logFunc := func(format string, args ...interface{}) {
		logger.Printf("[%s] "+format, append([]interface{}{childName}, args...)...)
	}
	engine := tr.NewEngine(registry, logFunc)
	engine.PhaseHook = func(when, name, state, errMsg string) {
		switch when {
		case "before":
			_ = statusWriter.PhaseStarted(name)
		case "after":
			switch state {
			case string(tr.StatusPass):
				_ = statusWriter.PhaseFinished(name, tr.PhaseStatePass, "")
			case string(tr.StatusFail):
				_ = statusWriter.PhaseFinished(name, tr.PhaseStateFail, errMsg)
			default:
				_ = statusWriter.PhaseFinished(name, state, errMsg)
			}
		}
	}
	engine.CancelCheck = statusWriter.CancelRequested
	_ = statusWriter.SetState(tr.RunStateRunning)

	actx, err := setupActionContext(scenario, logFunc)
	if err != nil {
		_ = statusWriter.Finalize(tr.RunStateError, err.Error())
		return nil, bundle.Manifest.RunID, bundle.Dir, fmt.Errorf("setup child: %w", err)
	}
	defer cleanupNodes(actx)
	actx.Bundle = bundle

	clusterMgr := tr.NewClusterManager(scenario.Cluster, logFunc)
	if err := clusterMgr.Setup(ctx, actx); err != nil {
		_ = statusWriter.Finalize(tr.RunStateError, err.Error())
		return nil, bundle.Manifest.RunID, bundle.Dir, fmt.Errorf("cluster setup child: %w", err)
	}
	defer clusterMgr.Teardown(ctx)
	if clusterMgr.Skipped() {
		result := &tr.ScenarioResult{Name: scenario.Name, Status: tr.StatusPass}
		_ = bundle.Finalize(result)
		_ = statusWriter.Finalize(tr.RunStatePass, "")
		return result, bundle.Manifest.RunID, bundle.Dir, nil
	}

	result := engine.Run(ctx, scenario, actx)
	if err := bundle.Finalize(result); err != nil {
		logger.Printf("[%s] warning: finalize child bundle: %v", childName, err)
	}
	terminal, summary := terminalRunState(result)
	_ = statusWriter.Finalize(terminal, summary)
	if result.Status == tr.StatusFail {
		return result, bundle.Manifest.RunID, bundle.Dir, fmt.Errorf("%s", result.Error)
	}
	return result, bundle.Manifest.RunID, bundle.Dir, nil
}

func discoverProductCommitFromChildBundle(childRunDir string) (string, bool, error) {
	if childRunDir == "" {
		return "", false, nil
	}
	candidates := []string{
		filepath.Join(childRunDir, "artifacts", "remote-phases.tgz"),
	}
	for _, candidate := range candidates {
		commit, found, err := gitRevisionFromAlphaImagesTgz(candidate)
		if err != nil {
			return "", false, err
		}
		if found {
			return commit, true, nil
		}
	}
	return "", false, nil
}

func gitRevisionFromAlphaImagesTgz(path string) (string, bool, error) {
	f, err := os.Open(path)
	if err != nil {
		if os.IsNotExist(err) {
			return "", false, nil
		}
		return "", false, err
	}
	defer f.Close()
	gz, err := gzip.NewReader(f)
	if err != nil {
		return "", false, err
	}
	defer gz.Close()
	trd := tar.NewReader(gz)
	commits := make(map[string]string)
	for {
		hdr, err := trd.Next()
		if err == io.EOF {
			break
		}
		if err != nil {
			return "", false, err
		}
		if hdr == nil || hdr.Typeflag != tar.TypeReg {
			continue
		}
		name := filepath.ToSlash(hdr.Name)
		if !isPinBuildEvidencePath(name) {
			continue
		}
		data, err := io.ReadAll(trd)
		if err != nil {
			return "", false, err
		}
		commit, err := productCommitFromPinEvidence(name, string(data))
		if err != nil {
			return "", false, err
		}
		if commit == "" {
			continue
		}
		commits[commit] = name
		if len(commits) > 1 {
			return "", false, fmt.Errorf("mixed child product commit evidence in %s", path)
		}
	}
	for commit := range commits {
		return commit, true, nil
	}
	return "", false, nil
}

func productCommitFromPinEvidence(name, data string) (string, error) {
	switch {
	case strings.HasSuffix(name, "/alpha-images.env") || name == "alpha-images.env":
		commit := parseEnvValue(data, "GIT_REVISION")
		if !isCommitLike(commit) {
			return "", fmt.Errorf("%s has invalid GIT_REVISION %q", name, commit)
		}
		if dirty := parseEnvValue(data, "GIT_DIRTY"); strings.EqualFold(dirty, "true") {
			return "", fmt.Errorf("%s reports GIT_DIRTY=true", name)
		}
		return commit, nil
	case strings.HasSuffix(name, ".version.txt"):
		commit := parseRevisionField(data)
		if !isCommitLike(commit) {
			return "", fmt.Errorf("%s has invalid revision %q", name, commit)
		}
		if modified := parseKeyValueField(data, "modified"); strings.EqualFold(modified, "true") {
			return "", fmt.Errorf("%s reports modified=true", name)
		}
		return commit, nil
	case strings.HasSuffix(name, "/git.sha") || name == "git.sha":
		commit := strings.TrimSpace(data)
		if !isCommitLike(commit) {
			return "", fmt.Errorf("%s has invalid git sha %q", name, commit)
		}
		return commit, nil
	default:
		return "", nil
	}
}

func parseEnvValue(data, key string) string {
	prefix := key + "="
	for _, line := range strings.Split(data, "\n") {
		line = strings.TrimSpace(line)
		if strings.HasPrefix(line, prefix) {
			return strings.Trim(strings.TrimPrefix(line, prefix), `"'`)
		}
	}
	return ""
}

func parseRevisionField(data string) string {
	return parseKeyValueField(data, "revision")
}

func parseKeyValueField(data, key string) string {
	prefix := key + "="
	fields := strings.Fields(data)
	for _, field := range fields {
		if strings.HasPrefix(field, prefix) {
			return strings.Trim(strings.TrimPrefix(field, prefix), `"'`)
		}
	}
	return ""
}

var commitLikePattern = regexp.MustCompile(`^[0-9a-fA-F]{7,40}$`)

func isCommitLike(value string) bool {
	return commitLikePattern.MatchString(strings.TrimSpace(value))
}

func isPinBuildEvidencePath(name string) bool {
	name = strings.Trim(filepath.ToSlash(name), "/")
	parts := strings.Split(name, "/")
	var phase, file string
	if len(parts) == 2 {
		phase, file = parts[0], parts[1]
	} else if len(parts) == 3 {
		phase, file = parts[1], parts[2]
	} else {
		return false
	}
	if phase != "pin_build" && phase != "pin-build" {
		return false
	}
	return file == "alpha-images.env" || file == "git.sha" || strings.HasSuffix(file, ".version.txt")
}

func runnerGitRevision() string {
	info, ok := debug.ReadBuildInfo()
	if !ok {
		return ""
	}
	for _, setting := range info.Settings {
		if setting.Key == "vcs.revision" {
			return setting.Value
		}
	}
	return ""
}

func resolveSuitePath(base, p string) string {
	if filepath.IsAbs(p) {
		return p
	}
	return filepath.Join(base, p)
}

func gitRevParse(dir string) string {
	if dir == "" {
		dir = "."
	}
	out, err := exec.Command("git", "-C", dir, "rev-parse", "HEAD").Output()
	if err != nil {
		return ""
	}
	return strings.TrimSpace(string(out))
}

func copyFile(src, dst string) error {
	data, err := os.ReadFile(src)
	if err != nil {
		return err
	}
	if err := os.MkdirAll(filepath.Dir(dst), 0755); err != nil {
		return err
	}
	return os.WriteFile(dst, data, 0644)
}

func coordinatorCmd(args []string) {
	fs := flag.NewFlagSet("coordinator", flag.ExitOnError)
	port := fs.Int("port", 9000, "Listen port for agent registration")
	token := fs.String("token", "", "Auth token for agent communication")
	dryRun := fs.Bool("dry-run", false, "Print execution plan without running")
	outputPath := fs.String("output", "", "Write JSON results to file")
	junitPath := fs.String("junit", "", "Write JUnit XML to file")
	htmlPath := fs.String("html", "", "Write HTML report to file")
	artifactsDir := fs.String("artifacts", "", "Download artifacts from agents to this directory")
	regTimeout := fs.String("timeout", "30s", "Agent registration timeout")
	coordTiers := fs.String("tiers", "", "Comma-separated list of enabled tiers (core,block,devops,chaos)")
	fs.Parse(args)

	if fs.NArg() < 1 {
		fmt.Fprintln(os.Stderr, "error: scenario file required")
		os.Exit(1)
	}
	scenarioFile := fs.Arg(0)

	logger := log.New(os.Stderr, "", log.LstdFlags)

	scenario, err := tr.ParseFile(scenarioFile)
	if err != nil {
		logger.Fatalf("parse scenario: %v", err)
	}

	// Verify scenario has agents section.
	if len(scenario.Topology.Agents) == 0 {
		logger.Fatalf("scenario has no topology.agents section; use 'run' for SSH mode")
	}

	// Set up signal handling.
	ctx, cancel := signal.NotifyContext(context.Background(), os.Interrupt)
	defer cancel()

	// Create registry.
	registry := tr.NewRegistry()
	pkgRegister(registry)
	if *coordTiers != "" {
		registry.EnableTiers(parseTiers(*coordTiers))
	}

	// Create coordinator.
	coord := tr.NewCoordinator(tr.CoordinatorConfig{
		Port:     *port,
		Token:    *token,
		DryRun:   *dryRun,
		Expected: scenario.Topology.Agents,
		Logger:   log.New(os.Stderr, "[coord] ", log.LstdFlags),
	})

	if err := coord.Start(); err != nil {
		logger.Fatalf("start coordinator: %v", err)
	}
	defer coord.Stop()

	// Wait for agents.
	timeout, _ := time.ParseDuration(*regTimeout)
	if timeout == 0 {
		timeout = 30 * time.Second
	}
	logger.Printf("waiting for %d agents (timeout=%s)...", len(scenario.Topology.Agents), timeout)
	if err := coord.WaitForAgents(ctx, timeout); err != nil {
		logger.Fatalf("%v", err)
	}
	logger.Printf("all %d agents registered", len(scenario.Topology.Agents))

	// Run scenario.
	result := coord.RunScenario(ctx, scenario, registry)

	// Download artifacts from agents (on both pass and fail).
	remoteArtifactsDir := scenario.Artifacts.Dir
	if *artifactsDir != "" && remoteArtifactsDir != "" {
		coord.DownloadAllArtifacts(ctx, remoteArtifactsDir, *artifactsDir, result)
	}

	// Print summary.
	tr.PrintSummary(os.Stdout, result)

	if *outputPath != "" {
		if err := tr.WriteJSON(result, *outputPath); err != nil {
			logger.Printf("write JSON: %v", err)
		}
	}
	if *junitPath != "" {
		if err := tr.WriteJUnitXML(result, *junitPath); err != nil {
			logger.Printf("write JUnit: %v", err)
		}
	}
	if *htmlPath != "" {
		if err := tr.WriteHTMLReport(result, *htmlPath); err != nil {
			logger.Printf("write HTML: %v", err)
		} else {
			logger.Printf("HTML report written to %s", *htmlPath)
		}
	}

	if result.Status == tr.StatusFail {
		os.Exit(1)
	}
}

func agentCmd(args []string) {
	fs := flag.NewFlagSet("agent", flag.ExitOnError)
	port := fs.Int("port", 9100, "Listen port")
	coordURL := fs.String("coordinator", "", "Coordinator URL (e.g. http://192.168.1.100:9000)")
	token := fs.String("token", "", "Auth token")
	nodes := fs.String("nodes", "", "Comma-separated node names this agent handles")
	allowExec := fs.Bool("allow-exec", false, "Enable /exec endpoint")
	persistent := fs.Bool("persistent", false, "Stay running, re-register with coordinator on each run")
	fs.Parse(args)

	logger := log.New(os.Stderr, "[agent] ", log.LstdFlags)

	// Parse node names.
	var nodeNames []string
	if *nodes != "" {
		for _, n := range strings.Split(*nodes, ",") {
			n = strings.TrimSpace(n)
			if n != "" {
				nodeNames = append(nodeNames, n)
			}
		}
	}

	// Create registry.
	registry := tr.NewRegistry()
	pkgRegister(registry)

	// Create agent.
	agent := tr.NewAgent(tr.AgentConfig{
		Port:           *port,
		CoordinatorURL: *coordURL,
		Token:          *token,
		AllowExec:      *allowExec,
		Persistent:     *persistent,
		Nodes:          nodeNames,
		Registry:       registry,
		Logger:         logger,
	})

	// Set up signal handling.
	ctx, cancel := signal.NotifyContext(context.Background(), os.Interrupt)
	defer cancel()

	logger.Printf("starting agent (nodes=%v, exec=%v, persistent=%v)", nodeNames, *allowExec, *persistent)
	if err := agent.Start(ctx); err != nil {
		logger.Fatalf("agent error: %v", err)
	}
}

func consoleCmd(args []string) {
	fs := flag.NewFlagSet("console", flag.ExitOnError)
	port := fs.Int("port", 9090, "Listen port for web UI")
	token := fs.String("token", "", "Auth token for agents")
	scenariosDir := fs.String("scenarios-dir", ".", "Directory containing scenario YAML files")
	consoleTiers := fs.String("tiers", "", "Comma-separated list of enabled tiers")
	fs.Parse(args)

	logger := log.New(os.Stderr, "[console] ", log.LstdFlags)

	registry := tr.NewRegistry()
	pkgRegister(registry)
	if *consoleTiers != "" {
		registry.EnableTiers(parseTiers(*consoleTiers))
	}

	console := tr.NewConsole(tr.ConsoleConfig{
		Port:        *port,
		Token:       *token,
		ScenarioDir: *scenariosDir,
		Registry:    registry,
		Logger:      logger,
	})

	ctx, cancel := signal.NotifyContext(context.Background(), os.Interrupt)
	defer cancel()

	if err := console.Start(ctx); err != nil {
		logger.Fatalf("console error: %v", err)
	}
}

func validateCmd(args []string) {
	if len(args) < 1 {
		fmt.Fprintln(os.Stderr, "error: scenario file required")
		os.Exit(1)
	}

	scenario, err := tr.ParseFile(args[0])
	if err != nil {
		fmt.Fprintf(os.Stderr, "INVALID: %v\n", err)
		os.Exit(1)
	}

	// Registry-dependent strict checks: every action must be
	// registered, every required param must be present.
	registry := tr.NewRegistry()
	pkgRegister(registry)
	if problems := tr.ValidateAgainstRegistry(scenario, registry); len(problems) > 0 {
		fmt.Fprintf(os.Stderr, "INVALID: %s\n", scenario.Name)
		for _, p := range problems {
			fmt.Fprintf(os.Stderr, "  - %v\n", p)
		}
		os.Exit(1)
	}

	// Surface any mutating actions so the operator knows --allow-mutating
	// will be required at run time.
	var mutating []string
	for _, ph := range scenario.Phases {
		for _, act := range ph.Actions {
			if registry.IsMutating(act.Action) {
				mutating = append(mutating, fmt.Sprintf("%s (phase %q)", act.Action, ph.Name))
			}
		}
	}

	fmt.Printf("VALID: %s (%d phases, %d targets)\n",
		scenario.Name, len(scenario.Phases), len(scenario.Targets))
	if len(mutating) > 0 {
		fmt.Println("  mutating actions present (require --allow-mutating at run):")
		for _, m := range mutating {
			fmt.Printf("    - %s\n", m)
		}
	}
}

func validateBundleCmd(args []string) int {
	fs := flag.NewFlagSet("validate-bundle", flag.ExitOnError)
	requirePass := fs.Bool("require-pass", false, "Require top-level and child statuses to be pass")
	requireTiming := fs.Bool("require-timing", false, "Require started_at, ended_at, and wall_clock_s in result/status")
	requireChildBundles := fs.Bool("require-child-bundles", false, "Require each listed child run_dir to contain status.json and result.json")
	profile := fs.String("profile", "", "Named validation profile (currently: protocol-release-gate, beta-hardening)")
	expectScenario := fs.String("expect-scenario", "", "Expected scenario name")
	expectCommit := fs.String("expect-commit", "", "Expected product/source/git commit prefix")
	children := fs.String("children", "", "Comma-separated expected child phase names, in order")
	jsonOut := fs.Bool("json", false, "Print validation report as JSON")
	fs.Parse(args)

	if fs.NArg() < 1 {
		fmt.Fprintln(os.Stderr, "error: run bundle directory required")
		return 2
	}
	opts := tr.BundleValidationOptions{
		RequirePass:         *requirePass,
		RequireTiming:       *requireTiming,
		RequireChildBundles: *requireChildBundles,
		ExpectScenario:      *expectScenario,
		ExpectCommitPrefix:  *expectCommit,
		ExpectedChildren:    splitCSV(*children),
	}
	if err := applyBundleValidationProfile(*profile, &opts); err != nil {
		fmt.Fprintf(os.Stderr, "error: %v\n", err)
		return 2
	}
	report, err := tr.ValidateBundle(fs.Arg(0), opts)
	if err != nil {
		fmt.Fprintf(os.Stderr, "INVALID: %v\n", err)
		return 1
	}
	if *jsonOut {
		data, _ := json.MarshalIndent(report, "", "  ")
		fmt.Println(string(data))
	} else if report.OK {
		fmt.Printf("VALID: bundle %s", report.Dir)
		if report.RunID != "" {
			fmt.Printf(" run_id=%s", report.RunID)
		}
		if report.Status != "" {
			fmt.Printf(" status=%s", report.Status)
		}
		if report.ProductCommit != "" {
			fmt.Printf(" commit=%s", report.ProductCommit)
		}
		fmt.Println()
	} else {
		fmt.Fprintf(os.Stderr, "INVALID: bundle %s\n", report.Dir)
		for _, e := range report.Errors {
			fmt.Fprintf(os.Stderr, "  - %s\n", e)
		}
	}
	if !report.OK {
		return 1
	}
	return 0
}

func applyBundleValidationProfile(profile string, opts *tr.BundleValidationOptions) error {
	switch strings.TrimSpace(profile) {
	case "":
		return nil
	case "protocol-release-gate":
		opts.RequirePass = true
		opts.RequireTiming = true
		opts.RequireChildBundles = true
		if opts.ExpectScenario == "" {
			opts.ExpectScenario = "protocol-release-gate-suite"
		}
		if len(opts.ExpectedChildren) == 0 {
			opts.ExpectedChildren = []string{
				"iscsi-p6-alua-failover",
				"nvme-p4-multipath-failover",
				"nvme-p5-csi-protocol",
				"iscsi-p8-compat-soak",
			}
		}
		return nil
	case "beta-hardening":
		opts.RequirePass = true
		opts.RequireTiming = true
		opts.RequireChildBundles = true
		if opts.ExpectScenario == "" {
			opts.ExpectScenario = "beta-hardening-gate"
		}
		if len(opts.ExpectedChildren) == 0 {
			opts.ExpectedChildren = []string{
				"iscsi-p6-alua-failover",
				"nvme-p4-multipath-failover",
				"nvme-p5-csi-protocol",
				"iscsi-p8-compat-soak",
				"csi-lifecycle-component",
				"csi-rf1-durable-restart",
				"operations-status-diagnostics",
				"returned-replica-component",
				"iscsi-returned-replica",
				"cleanup-residue",
			}
		}
		return nil
	default:
		return fmt.Errorf("unknown validate-bundle profile %q", profile)
	}
}

func listCmd() {
	// Parse --tiers flag from remaining args.
	fs := flag.NewFlagSet("list", flag.ExitOnError)
	listTiers := fs.String("tiers", "", "Comma-separated list of enabled tiers")
	fs.Parse(os.Args[2:])

	registry := tr.NewRegistry()
	pkgRegister(registry)
	if *listTiers != "" {
		registry.EnableTiers(parseTiers(*listTiers))
	}

	byTier := registry.ListByTier()
	tierOrder := []string{tr.TierCore, tr.TierBlock, tr.TierDevOps, tr.TierChaos, actions.TierK8s}
	// Append any product-pack tiers not in the fixed order (e.g. s3, vfs) so a
	// new pack's actions are visible in `list` without editing this slice.
	known := make(map[string]bool, len(tierOrder))
	for _, t := range tierOrder {
		known[t] = true
	}
	extra := make([]string, 0)
	for t := range byTier {
		if !known[t] {
			extra = append(extra, t)
		}
	}
	sort.Strings(extra)
	tierOrder = append(tierOrder, extra...)

	fmt.Println("Registered actions:")
	for _, tier := range tierOrder {
		names := byTier[tier]
		if len(names) == 0 {
			continue
		}
		// Skip tiers that are not enabled (if filtering).
		if len(registry.EnabledTiers) > 0 && !registry.EnabledTiers[tier] {
			continue
		}
		sort.Strings(names)
		fmt.Printf("\n  [%s]\n", tier)
		for _, name := range names {
			fmt.Printf("    - %s\n", name)
		}
	}
	fmt.Println()
}

// setupActionContext creates nodes, targets, and the action context from the scenario.
func setupActionContext(s *tr.Scenario, logFunc func(string, ...interface{})) (*tr.ActionContext, error) {
	actx := &tr.ActionContext{
		Scenario: s,
		Nodes:    make(map[string]tr.NodeRunner),
		Targets:  make(map[string]tr.TargetRunner),
		Vars:     make(map[string]string),
		Log:      logFunc,
	}

	// Create and connect nodes.
	for name, spec := range s.Topology.Nodes {
		node := &infra.Node{
			Host:    resolveTopologyValue(spec.Host, s.Env),
			User:    resolveTopologyValue(spec.User, s.Env),
			KeyFile: resolveTopologyValue(spec.KeyFile, s.Env),
			IsLocal: spec.IsLocal,
		}
		if err := node.Connect(); err != nil {
			return nil, fmt.Errorf("connect node %s: %w", name, err)
		}
		actx.Nodes[name] = node
	}

	// Create targets.
	for name, spec := range s.Targets {
		nodeRunner, ok := actx.Nodes[spec.Node]
		if !ok {
			return nil, fmt.Errorf("target %s: node %s not found", name, spec.Node)
		}
		node, ok := nodeRunner.(*infra.Node)
		if !ok {
			return nil, fmt.Errorf("target %s: node %s is not infra.Node", name, spec.Node)
		}
		htSpec := infra.HATargetSpec{
			VolSize:             spec.VolSize,
			WALSize:             spec.WALSize,
			IQN:                 spec.IQN(),
			ISCSIPort:           spec.ISCSIPort,
			AdminPort:           spec.AdminPort,
			ReplicaDataPort:     spec.ReplicaDataPort,
			ReplicaCtrlPort:     spec.ReplicaCtrlPort,
			RebuildPort:         spec.RebuildPort,
			TPGID:               spec.TPGID,
			NvmePort:            spec.NvmePort,
			NQN:                 spec.NQN(),
			MaxConcurrentWrites: spec.MaxConcurrentWrites,
			NvmeIOQueues:        spec.NvmeIOQueues,
		}
		ht := infra.NewHATargetFromSpec(node, name, htSpec)
		actx.Targets[name] = ht
	}

	return actx, nil
}

func resolveTopologyValue(s string, vars map[string]string) string {
	for k, v := range vars {
		s = strings.ReplaceAll(s, "{{ "+k+" }}", v)
		s = strings.ReplaceAll(s, "{{"+k+"}}", v)
	}
	return s
}

func cleanupNodes(actx *tr.ActionContext) {
	for _, n := range actx.Nodes {
		n.Close()
	}
}

// parseTiers splits a comma-separated tier string into a slice.
func parseTiers(s string) []string {
	var tiers []string
	for _, t := range strings.Split(s, ",") {
		t = strings.TrimSpace(t)
		if t != "" {
			tiers = append(tiers, t)
		}
	}
	return tiers
}

func splitCSV(s string) []string {
	var out []string
	for _, item := range strings.Split(s, ",") {
		item = strings.TrimSpace(item)
		if item != "" {
			out = append(out, item)
		}
	}
	return out
}

// kvFlag is a flag.Value collecting repeated KEY=VALUE pairs into a
// map. Used by --env to let an operator override scenario.env entries
// from the CLI without editing the YAML.
type kvFlag struct {
	values map[string]string
}

func newKVFlag() *kvFlag { return &kvFlag{values: make(map[string]string)} }

func (k *kvFlag) String() string {
	if k == nil || len(k.values) == 0 {
		return ""
	}
	parts := make([]string, 0, len(k.values))
	for kk, vv := range k.values {
		parts = append(parts, kk+"="+vv)
	}
	return strings.Join(parts, ",")
}

func (k *kvFlag) Set(s string) error {
	if k.values == nil {
		k.values = make(map[string]string)
	}
	idx := strings.Index(s, "=")
	if idx <= 0 {
		return fmt.Errorf("--env requires KEY=VALUE form, got %q", s)
	}
	key := strings.TrimSpace(s[:idx])
	val := s[idx+1:]
	if key == "" {
		return fmt.Errorf("--env empty KEY")
	}
	k.values[key] = val
	return nil
}

// countSchedulablePhases returns how many phases the engine is
// expected to dispatch given Repeat counts. Used as the initial
// PhasesTotal in status.json so a watcher can compute progress %
// without re-parsing the scenario.
func countSchedulablePhases(phases []tr.Phase) int {
	n := 0
	for _, p := range phases {
		count := p.Repeat
		if count <= 0 {
			count = 1
		}
		n += count
	}
	return n
}

// statusCmd prints status.json for a run. Three call shapes:
//
//	swblock status                           # latest under default results dir
//	swblock status -results-dir <root>        # latest under <root>
//	swblock status [-results-dir <root>] <id> # explicit run id
//
// Exit code 0 on found+terminal-pass, 1 on found+terminal-fail or
// running, 2 on missing/error.
func statusCmd(args []string) int {
	fs := flag.NewFlagSet("status", flag.ContinueOnError)
	resultsDir := fs.String("results-dir", "results", "Root directory of run bundles")
	jsonOut := fs.Bool("json", false, "Print full status.json verbatim")
	if err := fs.Parse(args); err != nil {
		return 2
	}
	rest := fs.Args()
	var runID string
	if len(rest) > 0 {
		runID = rest[0]
	} else {
		latest, err := tr.ReadLatest(*resultsDir)
		if err != nil {
			fmt.Fprintf(os.Stderr, "status: read latest: %v\n", err)
			return 2
		}
		if latest == "" {
			fmt.Fprintf(os.Stderr, "status: no runs found under %s\n", *resultsDir)
			return 2
		}
		runID = latest
	}
	runDir := filepath.Join(*resultsDir, runID)
	s, err := tr.ReadStatus(runDir)
	if err != nil {
		fmt.Fprintf(os.Stderr, "status: %v\n", err)
		return 2
	}
	if *jsonOut {
		data, _ := json.MarshalIndent(s, "", "  ")
		fmt.Println(string(data))
	} else {
		fmt.Printf("run_id:        %s\n", s.RunID)
		fmt.Printf("scenario:      %s\n", s.Scenario)
		fmt.Printf("state:         %s\n", s.State)
		fmt.Printf("phases:        %d/%d\n", s.PhasesDone, s.PhasesTotal)
		if s.CurrentPhase != "" {
			fmt.Printf("current_phase: %s\n", s.CurrentPhase)
		}
		if s.StartedAt != "" {
			fmt.Printf("started_at:    %s\n", s.StartedAt)
		}
		if s.EndedAt != "" {
			fmt.Printf("ended_at:      %s\n", s.EndedAt)
		}
		if s.ErrorSummary != "" {
			fmt.Printf("error:         %s\n", s.ErrorSummary)
		}
		fmt.Printf("artifact_dir:  %s\n", s.ArtifactDir)
		if len(s.Phases) > 0 {
			fmt.Println("phase_results:")
			for _, p := range s.Phases {
				marker := "[" + p.State + "]"
				fmt.Printf("  %-10s %s", marker, p.Name)
				if p.Error != "" {
					fmt.Printf("    error=%s", p.Error)
				}
				fmt.Println()
			}
		}
	}
	switch s.State {
	case tr.RunStatePass:
		return 0
	case tr.RunStateFail, tr.RunStateError, tr.RunStateCancelled:
		return 1
	default:
		// queued/running -> exit 1 too: caller knows it's not done yet
		return 1
	}
}

// listRunsCmd prints a one-line summary per run found under
// results-dir. Useful when latest pointer is stale or you want to
// pick by date/status.
func listRunsCmd(args []string) int {
	fs := flag.NewFlagSet("list-runs", flag.ContinueOnError)
	resultsDir := fs.String("results-dir", "results", "Root directory of run bundles")
	if err := fs.Parse(args); err != nil {
		return 2
	}
	entries, err := os.ReadDir(*resultsDir)
	if err != nil {
		fmt.Fprintf(os.Stderr, "list-runs: %v\n", err)
		return 2
	}
	latest, _ := tr.ReadLatest(*resultsDir)
	count := 0
	for _, e := range entries {
		if !e.IsDir() {
			continue
		}
		runDir := filepath.Join(*resultsDir, e.Name())
		s, err := tr.ReadStatus(runDir)
		if err != nil {
			continue
		}
		marker := " "
		if e.Name() == latest {
			marker = "*"
		}
		fmt.Printf("%s %-30s %-10s %3d/%d  %s\n",
			marker, s.RunID, s.State, s.PhasesDone, s.PhasesTotal, s.Scenario)
		count++
	}
	if count == 0 {
		fmt.Fprintf(os.Stderr, "list-runs: no runs under %s\n", *resultsDir)
		return 1
	}
	return 0
}

// cancelCmd writes control/cancel under the run dir. The engine
// polls CancelCheck between phases and stops when the file appears.
// Always-phases still run for cleanup.
func cancelCmd(args []string) int {
	fs := flag.NewFlagSet("cancel", flag.ContinueOnError)
	resultsDir := fs.String("results-dir", "results", "Root directory of run bundles")
	if err := fs.Parse(args); err != nil {
		return 2
	}
	rest := fs.Args()
	var runID string
	if len(rest) > 0 {
		runID = rest[0]
	} else {
		latest, _ := tr.ReadLatest(*resultsDir)
		if latest == "" {
			fmt.Fprintf(os.Stderr, "cancel: no run id given and no latest pointer\n")
			return 2
		}
		runID = latest
	}
	runDir := filepath.Join(*resultsDir, runID)
	ctlDir := filepath.Join(runDir, "control")
	if err := os.MkdirAll(ctlDir, 0755); err != nil {
		fmt.Fprintf(os.Stderr, "cancel: %v\n", err)
		return 2
	}
	if err := os.WriteFile(filepath.Join(ctlDir, "cancel"), []byte(""), 0644); err != nil {
		fmt.Fprintf(os.Stderr, "cancel: %v\n", err)
		return 2
	}
	fmt.Printf("cancel requested for %s\n", runID)
	return 0
}
