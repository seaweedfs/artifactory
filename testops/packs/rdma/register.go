// Package rdma registers SeaweedFS RDMA lab-gate actions.
//
// The pack intentionally stays thin: it does not import product internals or
// implement RDMA itself. It drives the M01/M02 lab runner, parses the stable
// PASS/perf witness lines, and exposes them as scenario variables.
package rdma

import tr "github.com/seaweedfs/artifactory/testops"

// Tier groups RDMA lab actions in `sw-test-runner list`.
const Tier = "rdma"

// RegisterPack registers RDMA-specific actions.
func RegisterPack(r *tr.Registry) {
	r.RegisterFunc("rdma_run_mono_gate", Tier, runMonoGate)
	r.SetRequiredParams("rdma_run_mono_gate", []string{"ref"})
}
