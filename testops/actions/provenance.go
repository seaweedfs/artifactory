package actions

import (
	"context"
	"fmt"
	"strings"

	tr "github.com/seaweedfs/artifactory/testops"
)

// RegisterProvenanceActions registers emit_provenance — the single, product-
// agnostic place a gate declares the common control-plane envelope so ONE
// qa-assert validates any product's bundle. See
// docs/control-plane-product-contract.md §1 and docs/qa-bundle-assert.md.
func RegisterProvenanceActions(r *tr.Registry) {
	r.RegisterFunc("emit_provenance", tr.TierCore, emitProvenance)
	r.SetRequiredParams("emit_provenance", []string{"product"})
}

// emitProvenance returns the common envelope keys (plus any __-prefixed params
// verbatim, which are the product rows). All __-prefixed keys auto-propagate into
// result.json vars, so a scenario calls this once at the end of a passing gate:
//
//	- action: emit_provenance
//	  product: s3
//	  tested_ref: "{{ weed_ref }}"
//	  tested_sha: "{{ weed_sha }}"
//	  __s3_object_count: "{{ obj_count }}"
//	  __s3_roundtrip_sha_match: "1"
//
// tested_sha/lab_run_id are stamped by the worker/build (not inferred from the
// runner-cwd git); lab_run_id defaults to the runner run_id.
func emitProvenance(_ context.Context, actx *tr.ActionContext, act tr.Action) (map[string]string, error) {
	product := strings.TrimSpace(act.Params["product"])
	if product == "" {
		return nil, fmt.Errorf("emit_provenance: product param required")
	}
	out := map[string]string{
		"__product":     product,
		"__gate_pass":   provDefault(act.Params["gate_pass"], "1"),
		"__gate_status": provDefault(act.Params["gate_status"], "ok"),
	}
	if v := strings.TrimSpace(act.Params["tested_ref"]); v != "" {
		out["__tested_ref"] = v
	}
	if v := strings.TrimSpace(act.Params["tested_sha"]); v != "" {
		out["__tested_sha"] = v
	}
	labRun := strings.TrimSpace(act.Params["lab_run_id"])
	if labRun == "" {
		labRun = actx.Vars["run_id"]
	}
	if labRun != "" {
		out["__lab_run_id"] = labRun
	}
	// Product rows: pass through any __-prefixed param verbatim.
	for k, v := range act.Params {
		if strings.HasPrefix(k, "__") {
			out[k] = v
		}
	}
	return out, nil
}

func provDefault(v, def string) string {
	if strings.TrimSpace(v) == "" {
		return def
	}
	return v
}
