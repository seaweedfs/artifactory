package v3block

import (
	"context"
	"fmt"

	tr "github.com/seaweedfs/artifactory/testops"
	"github.com/seaweedfs/artifactory/testops/actions"
)

// v3ApplyClusterSpec writes a cluster-spec YAML to the node so a blockmaster
// started with --cluster-spec=<path> can pick it up. Does NOT restart the
// master — that is the scenario's job (typically v3_start_blockmaster runs
// after this with the same path).
//
// Required params:
//   path     destination path on the node (e.g. /tmp/v3-run/cluster-spec.yaml)
//   content  inline YAML body
//
// Alternative: instead of inline content, a scenario can use exec to scp/cp
// a file in. v3_apply_cluster_spec is convenience for the common case.
//
// SaveAs writes:
//   <save_as>_path  the path the file was written to
func v3ApplyClusterSpec(ctx context.Context, actx *tr.ActionContext, act tr.Action) (map[string]string, error) {
	node, err := actions.GetNode(actx, act.Node)
	if err != nil {
		return nil, fmt.Errorf("v3_apply_cluster_spec: %w", err)
	}
	path := requireParam(act, actx, "path")
	content := requireParam(act, actx, "content")
	if path == "" || content == "" {
		return nil, fmt.Errorf("v3_apply_cluster_spec: path and content params required")
	}

	// Write via heredoc to avoid shell-quoting nightmares with multiline YAML.
	// The 'CLUSTER_SPEC_EOF' sentinel is unlikely to appear in real cluster specs.
	cmd := fmt.Sprintf(
		"mkdir -p \"$(dirname %q)\" && cat > %q <<'CLUSTER_SPEC_EOF'\n%s\nCLUSTER_SPEC_EOF\n",
		path, path, content,
	)
	stdout, stderr, code, err := node.Run(ctx, cmd)
	if err != nil || code != 0 {
		return nil, fmt.Errorf("v3_apply_cluster_spec: write failed: code=%d err=%v stderr=%s stdout=%s",
			code, err, stderr, stdout)
	}

	actx.Log("  v3_apply_cluster_spec: wrote %d bytes to %s", len(content), path)
	if act.SaveAs != "" {
		actx.Vars[act.SaveAs+"_path"] = path
	}
	return map[string]string{"value": path}, nil
}
