package actions

import (
	"context"
	"crypto/sha256"
	"encoding/hex"
	"fmt"
	"os"
	"os/exec"
	"path/filepath"
	"strings"
	"time"

	tr "github.com/seaweedfs/artifactory/testops"
)

// RegisterBuildActions registers build-time primitives.
//
//	go_build      — compile a Go package; record path+sha256.
//	docker_build  — build a container image; record tag+digest.
//	ctr_load      — load a built docker image into k3s/kind containerd.
//	image_digest  — look up the digest of an existing image.
//
// All actions are TierCore (product-agnostic) and run on the
// controller's local host by default; pass `node:` to dispatch to a
// remote node via the standard infra.Node abstraction.
func RegisterBuildActions(r *tr.Registry) {
	r.RegisterFunc("go_build", tr.TierCore, goBuild)
	r.SetRequiredParams("go_build", []string{"package"})

	r.RegisterFunc("docker_build", tr.TierCore, dockerBuild)
	r.SetRequiredParams("docker_build", []string{"dockerfile", "tag"})

	r.RegisterFunc("ctr_load", tr.TierCore, ctrLoad)
	r.SetRequiredParams("ctr_load", []string{"images"})

	r.RegisterFunc("image_digest", tr.TierCore, imageDigest)
	r.SetRequiredParams("image_digest", []string{"image"})
}

// goBuild runs `go build -o <out> <package>` in <cwd>.
//
// Required params:
//
//	package — Go package path (e.g. "./cmd/blockmaster")
//
// Optional params:
//
//	cwd     — working directory; defaults to the current directory
//	out     — output binary path (relative to cwd); default: <cwd>/<basename(package)>
//	tags    — comma-separated build tags
//	ldflags — value passed to -ldflags
//
// Output (saved via Action.SaveAs):
//
//	the absolute path to the built binary.
//
// Side effect: when actx.Bundle != nil, records a ProvBinary entry
// (path, sha256, package, node, action id) so provenance.json
// pins the binary content.
//
// node: when set, runs the build on the named node via SSH. The
// resulting binary lives on that node; sha256 is computed there.
func goBuild(ctx context.Context, actx *tr.ActionContext, act tr.Action) (map[string]string, error) {
	pkg := act.Params["package"]
	if pkg == "" {
		return nil, fmt.Errorf("go_build: package param required")
	}
	cwd := act.Params["cwd"]
	out := act.Params["out"]
	tags := act.Params["tags"]
	ldflags := act.Params["ldflags"]

	if out == "" {
		out = filepath.Base(pkg)
		// Strip leading ./ from `./cmd/foo` style paths.
		out = strings.TrimPrefix(out, "./")
	}

	args := []string{"build"}
	if tags != "" {
		args = append(args, "-tags", tags)
	}
	if ldflags != "" {
		args = append(args, "-ldflags", ldflags)
	}
	args = append(args, "-o", out, pkg)

	var (
		absOut string
		sum    string
		err    error
	)
	if act.Node == "" {
		absOut, sum, err = goBuildLocal(ctx, cwd, args, out, actx)
	} else {
		absOut, sum, err = goBuildRemote(ctx, actx, act.Node, cwd, args, out)
	}
	if err != nil {
		return nil, err
	}

	actx.Log("  go_build: %s sha256=%s", absOut, sum)

	if actx.Bundle != nil {
		actx.Bundle.RecordBinary(tr.ProvBinary{
			Path:    absOut,
			SHA256:  sum,
			Package: pkg,
			Node:    act.Node,
			BuiltBy: act.SaveAs,
		})
	}

	return map[string]string{
		"value":  absOut, // primary output for save_as: binary path
		"path":   absOut,
		"sha256": sum,
	}, nil
}

func goBuildLocal(ctx context.Context, cwd string, args []string, out string, actx *tr.ActionContext) (string, string, error) {
	cmd := exec.CommandContext(ctx, "go", args...)
	if cwd != "" {
		cmd.Dir = cwd
	}
	combined, err := cmd.CombinedOutput()
	if err != nil {
		return "", "", fmt.Errorf("go_build: %w (output: %s)", err, strings.TrimSpace(string(combined)))
	}

	resolved := out
	if !filepath.IsAbs(resolved) {
		base := cwd
		if base == "" {
			base, _ = os.Getwd()
		}
		resolved = filepath.Join(base, out)
	}
	sum, err := fileSHA256(resolved)
	if err != nil {
		return "", "", fmt.Errorf("go_build: hash output: %w", err)
	}
	return resolved, sum, nil
}

func goBuildRemote(ctx context.Context, actx *tr.ActionContext, nodeName, cwd string, args []string, out string) (string, string, error) {
	node, err := GetNode(actx, nodeName)
	if err != nil {
		return "", "", fmt.Errorf("go_build: %w", err)
	}
	cdPart := ""
	if cwd != "" {
		cdPart = fmt.Sprintf("cd %s && ", shellQuote(cwd))
	}
	// Quote each arg for shell safety.
	quoted := make([]string, len(args))
	for i, a := range args {
		quoted[i] = shellQuote(a)
	}
	cmd := fmt.Sprintf("%sgo %s", cdPart, strings.Join(quoted, " "))
	stdout, stderr, code, err := node.Run(ctx, cmd)
	if err != nil {
		return "", "", fmt.Errorf("go_build: ssh: %w", err)
	}
	if code != 0 {
		return "", "", fmt.Errorf("go_build: exit=%d stderr=%s stdout=%s",
			code, strings.TrimSpace(stderr), strings.TrimSpace(stdout))
	}

	// Resolve output path (cwd/out if relative).
	resolved := out
	if cwd != "" && !filepath.IsAbs(out) {
		// Use forward slashes; this path lives on a unix node.
		resolved = strings.TrimRight(cwd, "/") + "/" + out
	}

	// sha256sum on the remote.
	hashCmd := fmt.Sprintf("sha256sum %s | awk '{print $1}'", shellQuote(resolved))
	hashOut, hashErr, hashCode, err := node.Run(ctx, hashCmd)
	if err != nil {
		return "", "", fmt.Errorf("go_build: remote sha256: %w", err)
	}
	if hashCode != 0 {
		return "", "", fmt.Errorf("go_build: remote sha256: exit=%d stderr=%s",
			hashCode, strings.TrimSpace(hashErr))
	}
	return resolved, strings.TrimSpace(hashOut), nil
}

// fileSHA256 returns the hex-encoded sha256 of the named file's contents.
func fileSHA256(path string) (string, error) {
	f, err := os.Open(path)
	if err != nil {
		return "", err
	}
	defer f.Close()
	h := sha256.New()
	buf := make([]byte, 64*1024)
	for {
		n, rerr := f.Read(buf)
		if n > 0 {
			h.Write(buf[:n])
		}
		if rerr != nil {
			break
		}
	}
	return hex.EncodeToString(h.Sum(nil)), nil
}

// dockerBuild runs `docker build -f <dockerfile> -t <tag> [--build-arg K=V]... <cwd>`,
// then captures the image's local sha256 ID.
//
// Required params:
//
//	dockerfile — path to the Dockerfile, relative to cwd
//	tag        — output image tag (e.g. "sw-block:local")
//
// Optional params:
//
//	cwd         — build context dir; defaults to "."
//	build_args  — semicolon-separated K=V list (e.g. "GIT_SHA=abc;FOO=bar")
//	binary      — binary to invoke; defaults to "docker" (use "nerdctl" if appropriate)
//
// Output (saved via Action.SaveAs): the image tag.
//
// Side effect: when actx.Bundle != nil, records a ProvImage entry
// (tag, digest, built_by) in provenance.json.
func dockerBuild(ctx context.Context, actx *tr.ActionContext, act tr.Action) (map[string]string, error) {
	dockerfile := act.Params["dockerfile"]
	tag := act.Params["tag"]
	if dockerfile == "" {
		return nil, fmt.Errorf("docker_build: dockerfile param required")
	}
	if tag == "" {
		return nil, fmt.Errorf("docker_build: tag param required")
	}
	cwd := act.Params["cwd"]
	if cwd == "" {
		cwd = "."
	}
	bin := act.Params["binary"]
	if bin == "" {
		bin = "docker"
	}

	args := []string{"build", "-f", dockerfile, "-t", tag}
	if ba := act.Params["build_args"]; ba != "" {
		for _, kv := range strings.Split(ba, ";") {
			kv = strings.TrimSpace(kv)
			if kv == "" {
				continue
			}
			args = append(args, "--build-arg", kv)
		}
	}
	args = append(args, cwd)

	digest, err := dockerBuildExec(ctx, actx, act.Node, bin, args, tag)
	if err != nil {
		return nil, err
	}

	actx.Log("  docker_build: %s %s", tag, digest)
	if actx.Bundle != nil {
		actx.Bundle.RecordImage(tr.ProvImage{
			Tag:     tag,
			Digest:  digest,
			BuiltBy: act.SaveAs,
		})
	}

	return map[string]string{
		"value":  tag, // primary output for save_as: image tag
		"image":  tag,
		"digest": digest,
	}, nil
}

func dockerBuildExec(ctx context.Context, actx *tr.ActionContext, node, bin string, args []string, tag string) (string, error) {
	if node == "" {
		cmd := exec.CommandContext(ctx, bin, args...)
		out, err := cmd.CombinedOutput()
		if err != nil {
			return "", fmt.Errorf("docker_build: %w (output tail: %s)", err, tailLines(string(out), 8))
		}
		return inspectDigestLocal(ctx, bin, tag)
	}
	n, err := GetNode(actx, node)
	if err != nil {
		return "", fmt.Errorf("docker_build: %w", err)
	}
	quoted := make([]string, len(args))
	for i, a := range args {
		quoted[i] = shellQuote(a)
	}
	stdout, stderr, code, err := n.Run(ctx, bin+" "+strings.Join(quoted, " "))
	if err != nil {
		return "", fmt.Errorf("docker_build: ssh: %w", err)
	}
	if code != 0 {
		return "", fmt.Errorf("docker_build: exit=%d stderr=%s", code, tailLines(stderr+stdout, 8))
	}
	return inspectDigestRemote(ctx, n, bin, tag)
}

func inspectDigestLocal(ctx context.Context, bin, tag string) (string, error) {
	out, err := exec.CommandContext(ctx, bin, "inspect", "--format", "{{.Id}}", tag).Output()
	if err != nil {
		return "", fmt.Errorf("inspect: %w", err)
	}
	return strings.TrimSpace(string(out)), nil
}

func inspectDigestRemote(ctx context.Context, n NodeRunner, bin, tag string) (string, error) {
	cmd := fmt.Sprintf("%s inspect --format '{{.Id}}' %s", bin, shellQuote(tag))
	stdout, stderr, code, err := n.Run(ctx, cmd)
	if err != nil {
		return "", fmt.Errorf("ssh inspect: %w", err)
	}
	if code != 0 {
		return "", fmt.Errorf("inspect exit=%d stderr=%s", code, strings.TrimSpace(stderr))
	}
	return strings.TrimSpace(stdout), nil
}

// NodeRunner is the same interface as tr.NodeRunner; redeclared here
// to avoid pulling the import path through every helper.
type NodeRunner = tr.NodeRunner

// ctrLoad imports one or more docker-built images into a container
// runtime that is not docker (typically k3s or kind).
//
// Required params:
//
//	images  — comma-separated tags (e.g. "sw-block:local,sw-block-csi:local")
//
// Optional params:
//
//	runtime — k3s (default) | kind | docker (no-op)
//	cluster — kind cluster name (only when runtime=kind)
//
// Output (saved via Action.SaveAs): the imported image list joined by ",".
//
// Side effect: same as docker_build — records ProvImage per image so
// the digest pinning survives the docker→containerd handoff.
func ctrLoad(ctx context.Context, actx *tr.ActionContext, act tr.Action) (map[string]string, error) {
	imgList := act.Params["images"]
	if imgList == "" {
		return nil, fmt.Errorf("ctr_load: images param required")
	}
	runtime := act.Params["runtime"]
	if runtime == "" {
		runtime = "k3s"
	}
	cluster := act.Params["cluster"]

	images := splitTrim(imgList, ",")
	if len(images) == 0 {
		return nil, fmt.Errorf("ctr_load: no images parsed from %q", imgList)
	}

	switch runtime {
	case "docker":
		// Nothing to do — images already live in docker daemon.
	case "k3s":
		if err := ctrLoadK3s(ctx, actx, act.Node, images); err != nil {
			return nil, err
		}
	case "kind":
		if cluster == "" {
			return nil, fmt.Errorf("ctr_load: cluster param required for runtime=kind")
		}
		if err := ctrLoadKind(ctx, actx, act.Node, cluster, images); err != nil {
			return nil, err
		}
	default:
		return nil, fmt.Errorf("ctr_load: unknown runtime %q", runtime)
	}

	// Record provenance for each image we loaded.
	if actx.Bundle != nil {
		for _, img := range images {
			digest, _ := imageDigestLookup(ctx, actx, act.Node, img, "docker")
			actx.Bundle.RecordImage(tr.ProvImage{
				Tag:     img,
				Digest:  digest,
				BuiltBy: act.SaveAs,
			})
		}
	}

	imported := strings.Join(images, ",")
	actx.Log("  ctr_load: runtime=%s images=%s", runtime, imported)
	return map[string]string{
		"value":    imported, // primary output for save_as
		"imported": imported,
	}, nil
}

func ctrLoadK3s(ctx context.Context, actx *tr.ActionContext, node string, images []string) error {
	tar := fmt.Sprintf("/tmp/sw-images-%d.tar", time.Now().UnixNano())
	saveArgs := append([]string{"save"}, images...)
	saveArgs = append(saveArgs, "-o", tar)
	importCmd := fmt.Sprintf("sudo k3s ctr images import %s", shellQuote(tar))

	if node == "" {
		if out, err := exec.CommandContext(ctx, "docker", saveArgs...).CombinedOutput(); err != nil {
			return fmt.Errorf("ctr_load: docker save: %w (out: %s)", err, tailLines(string(out), 4))
		}
		defer os.Remove(tar)
		out, err := exec.CommandContext(ctx, "sh", "-c", importCmd).CombinedOutput()
		if err != nil {
			return fmt.Errorf("ctr_load: import: %w (out: %s)", err, tailLines(string(out), 4))
		}
		return nil
	}
	n, err := GetNode(actx, node)
	if err != nil {
		return fmt.Errorf("ctr_load: %w", err)
	}
	saveCmd := "docker " + shellQuoteAll(saveArgs)
	if _, stderr, code, err := n.Run(ctx, saveCmd); err != nil || code != 0 {
		return fmt.Errorf("ctr_load: ssh save: err=%v code=%d stderr=%s", err, code, strings.TrimSpace(stderr))
	}
	defer n.Run(ctx, "rm -f "+shellQuote(tar))
	if _, stderr, code, err := n.Run(ctx, importCmd); err != nil || code != 0 {
		return fmt.Errorf("ctr_load: ssh import: err=%v code=%d stderr=%s", err, code, strings.TrimSpace(stderr))
	}
	return nil
}

func ctrLoadKind(ctx context.Context, actx *tr.ActionContext, node, cluster string, images []string) error {
	for _, img := range images {
		args := []string{"load", "docker-image", "--name", cluster, img}
		if node == "" {
			if out, err := exec.CommandContext(ctx, "kind", args...).CombinedOutput(); err != nil {
				return fmt.Errorf("ctr_load (kind): %w (out: %s)", err, tailLines(string(out), 4))
			}
			continue
		}
		n, err := GetNode(actx, node)
		if err != nil {
			return fmt.Errorf("ctr_load: %w", err)
		}
		cmd := "kind " + shellQuoteAll(args)
		if _, stderr, code, err := n.Run(ctx, cmd); err != nil || code != 0 {
			return fmt.Errorf("ctr_load (kind ssh): err=%v code=%d stderr=%s", err, code, strings.TrimSpace(stderr))
		}
	}
	return nil
}

// imageDigest returns the digest of an image already known to docker.
//
// Required params:
//
//	image — the tag to look up (e.g. "sw-block:local" or "ghcr.io/foo/bar:tag")
//
// Optional params:
//
//	pull   — "true" to `docker pull` first (default: false; lookup-only)
//	binary — defaults to "docker"
//
// Output: { digest, exists } — exists is "true" or "false".
//
// Side effect: records ProvImage{Tag, Digest} when found.
func imageDigest(ctx context.Context, actx *tr.ActionContext, act tr.Action) (map[string]string, error) {
	image := act.Params["image"]
	if image == "" {
		return nil, fmt.Errorf("image_digest: image param required")
	}
	pull := act.Params["pull"] == "true"
	bin := act.Params["binary"]
	if bin == "" {
		bin = "docker"
	}

	if pull {
		if act.Node == "" {
			if out, err := exec.CommandContext(ctx, bin, "pull", image).CombinedOutput(); err != nil {
				return nil, fmt.Errorf("image_digest: pull: %w (out: %s)", err, tailLines(string(out), 4))
			}
		} else {
			n, err := GetNode(actx, act.Node)
			if err != nil {
				return nil, fmt.Errorf("image_digest: %w", err)
			}
			cmd := fmt.Sprintf("%s pull %s", bin, shellQuote(image))
			if _, stderr, code, err := n.Run(ctx, cmd); err != nil || code != 0 {
				return nil, fmt.Errorf("image_digest: pull ssh: err=%v code=%d stderr=%s",
					err, code, strings.TrimSpace(stderr))
			}
		}
	}

	digest, err := imageDigestLookup(ctx, actx, act.Node, image, bin)
	if err != nil {
		return map[string]string{"value": "", "digest": "", "exists": "false"}, nil
	}
	if actx.Bundle != nil {
		actx.Bundle.RecordImage(tr.ProvImage{
			Tag:     image,
			Digest:  digest,
			BuiltBy: act.SaveAs,
		})
	}
	return map[string]string{
		"value":  digest, // primary output for save_as
		"digest": digest,
		"exists": "true",
	}, nil
}

func imageDigestLookup(ctx context.Context, actx *tr.ActionContext, node, image, bin string) (string, error) {
	if node == "" {
		return inspectDigestLocal(ctx, bin, image)
	}
	n, err := GetNode(actx, node)
	if err != nil {
		return "", err
	}
	return inspectDigestRemote(ctx, n, bin, image)
}

// tailLines returns the last n newline-separated lines of s, useful for
// keeping error messages compact.
func tailLines(s string, n int) string {
	lines := strings.Split(strings.TrimRight(s, "\n"), "\n")
	if len(lines) <= n {
		return strings.Join(lines, "\n")
	}
	return strings.Join(lines[len(lines)-n:], "\n")
}

// splitTrim splits s by sep, trims whitespace, drops empty entries.
func splitTrim(s, sep string) []string {
	parts := strings.Split(s, sep)
	out := make([]string, 0, len(parts))
	for _, p := range parts {
		if p = strings.TrimSpace(p); p != "" {
			out = append(out, p)
		}
	}
	return out
}

// shellQuoteAll quotes a slice of args and joins with spaces.
func shellQuoteAll(args []string) string {
	q := make([]string, len(args))
	for i, a := range args {
		q[i] = shellQuote(a)
	}
	return strings.Join(q, " ")
}

// shellQuote is a minimal POSIX single-quote escaper for ssh-side
// command construction. Wraps in single quotes; escapes embedded
// single quotes by closing+escape+reopening.
func shellQuote(s string) string {
	if s == "" {
		return "''"
	}
	if !strings.ContainsAny(s, " \t\n'\"\\$`!(){}[]<>?*&|;#~^") {
		return s
	}
	return "'" + strings.ReplaceAll(s, "'", `'\''`) + "'"
}
