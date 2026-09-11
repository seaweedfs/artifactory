package actions

import (
	"context"
	"fmt"
	"os"
	pathpkg "path"
	"path/filepath"
	"strings"
	"time"

	tr "github.com/seaweedfs/artifactory/testops"
)

// collectPath copies an arbitrary file or directory from a node into the
// controller-side run bundle. Directories are downloaded as .tgz archives.
//
// Params:
//   - path: remote/local path to collect (required)
//   - dest: controller-side destination directory (default: artifacts_dir)
//   - name: destination filename override
func collectPath(ctx context.Context, actx *tr.ActionContext, act tr.Action) (map[string]string, error) {
	src := strings.TrimSpace(act.Params["path"])
	if src == "" {
		return nil, fmt.Errorf("collect_path: path param required")
	}

	node, err := GetNode(actx, act.Node)
	if err != nil {
		return nil, fmt.Errorf("collect_path: %w", err)
	}

	destDir := strings.TrimSpace(act.Params["dest"])
	if destDir == "" {
		destDir = actx.Vars["artifacts_dir"]
	}
	if destDir == "" {
		destDir = actx.Vars["__artifacts_dir"]
	}
	if destDir == "" && actx.Bundle != nil {
		destDir = actx.Bundle.ArtifactsDir()
	}
	if destDir == "" {
		return nil, fmt.Errorf("collect_path: dest not set and no run bundle artifacts_dir available")
	}
	if err := os.MkdirAll(destDir, 0755); err != nil {
		return nil, fmt.Errorf("collect_path: mkdir %s: %w", destDir, err)
	}

	name := strings.TrimSpace(act.Params["name"])
	if name == "" {
		name = filepath.Base(strings.TrimRight(src, "/"))
	}
	if name == "" || name == "." || name == "/" {
		return nil, fmt.Errorf("collect_path: cannot derive destination name from %q", src)
	}

	testCmd := fmt.Sprintf("if [ -d %s ]; then echo dir; elif [ -f %s ]; then echo file; else echo missing; fi",
		execShellQuote(src), execShellQuote(src))
	kindOut, stderr, code, err := node.Run(ctx, testCmd)
	if err != nil || code != 0 {
		return nil, fmt.Errorf("collect_path: stat %s: code=%d stderr=%s err=%v", src, code, stderr, err)
	}
	kind := strings.TrimSpace(kindOut)
	if kind == "missing" || kind == "" {
		return nil, fmt.Errorf("collect_path: %s does not exist", src)
	}

	switch kind {
	case "file":
		localPath := filepath.Join(destDir, name)
		if err := node.Download(src, localPath); err != nil {
			return nil, fmt.Errorf("collect_path: download file: %w", err)
		}
		actx.Log("  collect_path: %s -> %s", src, localPath)
		return map[string]string{"value": localPath}, nil
	case "dir":
		archiveName := name
		if !strings.HasSuffix(archiveName, ".tgz") && !strings.HasSuffix(archiveName, ".tar.gz") {
			archiveName += ".tgz"
		}
		localPath := filepath.Join(destDir, archiveName)
		remoteArchive := fmt.Sprintf("/tmp/sw-test-runner-collect-%d.tgz", time.Now().UnixNano())
		parent := pathpkg.Dir(strings.TrimRight(src, "/"))
		base := pathpkg.Base(strings.TrimRight(src, "/"))
		tarCmd := collectPathTarCommand(remoteArchive, parent, base)
		if _, stderr, code, err := node.Run(ctx, tarCmd); err != nil || code != 0 {
			return nil, fmt.Errorf("collect_path: archive dir: code=%d stderr=%s err=%v", code, stderr, err)
		}
		defer node.Run(context.Background(), fmt.Sprintf("rm -f %s", execShellQuote(remoteArchive)))
		if err := node.Download(remoteArchive, localPath); err != nil {
			return nil, fmt.Errorf("collect_path: download archive: %w", err)
		}
		actx.Log("  collect_path: %s -> %s", src, localPath)
		return map[string]string{"value": localPath}, nil
	default:
		return nil, fmt.Errorf("collect_path: unsupported path kind %q for %s", kind, src)
	}
}

func collectPathTarCommand(archive, parent, base string) string {
	return fmt.Sprintf(`err="$(mktemp)"
tar -czf %s -C %s %s 2>"$err"
rc=$?
cat "$err" >&2
if [ "$rc" -eq 1 ] && grep -qi 'file changed as we read it' "$err" && [ -s %s ]; then
  rm -f "$err"
  exit 0
fi
rm -f "$err"
exit "$rc"`,
		execShellQuote(archive), execShellQuote(parent), execShellQuote(base), execShellQuote(archive))
}
