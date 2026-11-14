package s3

import (
	"context"
	"fmt"
	"strings"
	"time"

	tr "github.com/seaweedfs/artifactory/testops"
	"github.com/seaweedfs/artifactory/testops/actions"
	"github.com/seaweedfs/artifactory/testops/infra"
)

// endpointFor resolves the S3 endpoint host:port from the action param or var,
// defaulting to 127.0.0.1:<s3_port|8333>.
func endpointFor(actx *tr.ActionContext, act tr.Action) string {
	if ep := act.Params["endpoint"]; ep != "" {
		return ep
	}
	if ep := actx.Vars["s3_endpoint"]; ep != "" {
		return ep
	}
	port := act.Params["s3_port"]
	if port == "" {
		if p := actx.Vars["s3_port"]; p != "" {
			port = p
		} else {
			port = "8333"
		}
	}
	return "127.0.0.1:" + port
}

// s3StartStack starts an all-in-one weed stack with the S3 gateway on a node and
// waits for the S3 port to answer.
//
// Params: node, weed (binary path, default "weed"), data_dir (default
// /tmp/sw-s3-<run_id>), s3_port (default 8333), ip (default 127.0.0.1).
// Saves: s3_endpoint=<ip>:<s3_port> for later actions; returns the PID.
func s3StartStack(ctx context.Context, actx *tr.ActionContext, act tr.Action) (map[string]string, error) {
	node, err := actions.GetNode(actx, act.Node)
	if err != nil {
		return nil, fmt.Errorf("s3_start_stack: %w", err)
	}
	weed := act.Params["weed"]
	if weed == "" {
		weed = "weed"
	}
	s3Port := act.Params["s3_port"]
	if s3Port == "" {
		s3Port = "8333"
	}
	ip := act.Params["ip"]
	if ip == "" {
		ip = "127.0.0.1"
	}
	dataDir := act.Params["data_dir"]
	if dataDir == "" {
		dataDir = "/tmp/sw-s3-" + actx.Vars["run_id"]
	}

	node.Run(ctx, fmt.Sprintf("mkdir -p %s", dataDir))
	cmd := fmt.Sprintf(
		"sh -c 'nohup %s server -dir=%s -ip=%s -s3 -s3.port=%s -filer </dev/null >%s/weed.log 2>&1 & echo $!'",
		weed, dataDir, ip, s3Port, dataDir)
	stdout, stderr, code, err := node.Run(ctx, cmd)
	if err != nil || code != 0 {
		return nil, fmt.Errorf("s3_start_stack: start failed: code=%d stderr=%s err=%v", code, stderr, err)
	}
	pid := strings.TrimSpace(stdout)
	endpoint := ip + ":" + s3Port
	actx.Vars["s3_endpoint"] = endpoint
	actx.Log("  weed S3 stack starting on %s (PID %s, data %s)", endpoint, pid, dataDir)

	readyCtx, cancel := context.WithTimeout(ctx, 60*time.Second)
	defer cancel()
	for {
		select {
		case <-readyCtx.Done():
			return nil, fmt.Errorf("s3_start_stack: S3 gateway not ready on %s within timeout (see %s/weed.log)", endpoint, dataDir)
		case <-time.After(2 * time.Second):
			check := fmt.Sprintf("curl -s -o /dev/null -w '%%{http_code}' http://%s/ 2>/dev/null", endpoint)
			out, _, _, _ := node.Run(readyCtx, check)
			switch strings.TrimSpace(out) {
			case "200", "403", "404":
				// Any HTTP answer means the S3 listener is up.
				actx.Log("  S3 gateway ready on %s", endpoint)
				if act.SaveAs != "" {
					actx.Vars[act.SaveAs] = pid
				}
				return map[string]string{"value": pid}, nil
			}
		}
	}
}

// s3StopStack kills the weed stack and (optionally) removes the data dir.
// Params: node, data_dir (if set, removed), keep_data (any non-empty = keep).
func s3StopStack(ctx context.Context, actx *tr.ActionContext, act tr.Action) (map[string]string, error) {
	node, err := actions.GetNode(actx, act.Node)
	if err != nil {
		return nil, fmt.Errorf("s3_stop_stack: %w", err)
	}
	// pkill is async (SIGTERM); wait until the process is actually gone so the
	// zero-residue assertion that usually follows does not race the teardown.
	node.Run(ctx, "pkill -f '[w]eed server .*-s3' || true")
	for i := 0; i < 20; i++ {
		out, _, _, _ := node.Run(ctx, "pgrep -f '[w]eed server .*-s3' | wc -l")
		if strings.TrimSpace(out) == "0" {
			break
		}
		if i == 10 {
			node.Run(ctx, "pkill -9 -f '[w]eed server .*-s3' || true")
		}
		time.Sleep(500 * time.Millisecond)
	}
	if dataDir := act.Params["data_dir"]; dataDir != "" && act.Params["keep_data"] == "" {
		node.Run(ctx, fmt.Sprintf("rm -rf %s", dataDir))
	}
	actx.Log("  S3 stack stopped")
	return nil, nil
}

// s3MakeBucket creates a bucket via the S3 REST API (PUT /<bucket>).
// Params: endpoint (or s3_endpoint var), bucket, node.
func s3MakeBucket(ctx context.Context, actx *tr.ActionContext, act tr.Action) (map[string]string, error) {
	node, err := actions.GetNode(actx, act.Node)
	if err != nil {
		return nil, fmt.Errorf("s3_make_bucket: %w", err)
	}
	bucket := act.Params["bucket"]
	if bucket == "" {
		return nil, fmt.Errorf("s3_make_bucket: bucket param required")
	}
	endpoint := endpointFor(actx, act)
	cmd := fmt.Sprintf("curl -s -o /dev/null -w '%%{http_code}' -X PUT 'http://%s/%s' 2>/dev/null", endpoint, bucket)
	stdout, _, code, err := node.Run(ctx, cmd)
	if err != nil || code != 0 {
		return nil, fmt.Errorf("s3_make_bucket: curl failed code=%d err=%v", code, err)
	}
	http := strings.TrimSpace(stdout)
	if http != "200" && http != "204" {
		return nil, fmt.Errorf("s3_make_bucket: unexpected http=%s creating %s", http, bucket)
	}
	actx.Log("  created bucket %s (http %s)", bucket, http)
	return map[string]string{"value": http}, nil
}

// s3PutObject puts an object via PUT /<bucket>/<key> and returns its md5.
// Params: endpoint, bucket, key, and one of size (random) | data (inline) | file (local path).
func s3PutObject(ctx context.Context, actx *tr.ActionContext, act tr.Action) (map[string]string, error) {
	node, err := actions.GetNode(actx, act.Node)
	if err != nil {
		return nil, fmt.Errorf("s3_put_object: %w", err)
	}
	bucket := act.Params["bucket"]
	key := act.Params["key"]
	if bucket == "" || key == "" {
		return nil, fmt.Errorf("s3_put_object: bucket and key params required")
	}
	endpoint := endpointFor(actx, act)
	url := fmt.Sprintf("http://%s/%s/%s", endpoint, bucket, key)

	var cmd string
	switch {
	case act.Params["file"] != "":
		f := act.Params["file"]
		cmd = fmt.Sprintf("md5sum %s | awk '{print $1}' && curl -s -o /dev/null -X PUT --data-binary @%s '%s' 2>/dev/null", f, f, url)
	case act.Params["size"] != "":
		size := act.Params["size"]
		cmd = fmt.Sprintf("TF=/tmp/sw-s3-put-$$-$RANDOM.dat; dd if=/dev/urandom bs=%s count=1 2>/dev/null > $TF; md5sum $TF | awk '{print $1}'; curl -s -o /dev/null -X PUT --data-binary @$TF '%s' 2>/dev/null; rm -f $TF", size, url)
	case act.Params["data"] != "":
		data := act.Params["data"]
		cmd = fmt.Sprintf("TF=/tmp/sw-s3-put-$$-$RANDOM.dat; printf '%%s' '%s' > $TF; md5sum $TF | awk '{print $1}'; curl -s -o /dev/null -X PUT --data-binary @$TF '%s' 2>/dev/null; rm -f $TF", data, url)
	default:
		return nil, fmt.Errorf("s3_put_object: one of size|data|file required")
	}

	stdout, stderr, code, err := node.Run(ctx, cmd)
	if err != nil || code != 0 {
		return nil, fmt.Errorf("s3_put_object: code=%d stderr=%s err=%v", code, stderr, err)
	}
	md5 := strings.TrimSpace(strings.SplitN(strings.TrimSpace(stdout), "\n", 2)[0])
	actx.Log("  put s3://%s/%s md5=%s", bucket, key, md5)
	if act.SaveAs != "" {
		actx.Vars[act.SaveAs] = md5
	}
	return map[string]string{"value": md5}, nil
}

// s3GetObject gets an object via GET /<bucket>/<key> and returns its md5.
// Params: endpoint, bucket, key.
func s3GetObject(ctx context.Context, actx *tr.ActionContext, act tr.Action) (map[string]string, error) {
	node, err := actions.GetNode(actx, act.Node)
	if err != nil {
		return nil, fmt.Errorf("s3_get_object: %w", err)
	}
	bucket := act.Params["bucket"]
	key := act.Params["key"]
	if bucket == "" || key == "" {
		return nil, fmt.Errorf("s3_get_object: bucket and key params required")
	}
	endpoint := endpointFor(actx, act)
	cmd := fmt.Sprintf("curl -s 'http://%s/%s/%s' 2>/dev/null | md5sum | awk '{print $1}'", endpoint, bucket, key)
	stdout, _, code, err := node.Run(ctx, cmd)
	if err != nil || code != 0 {
		return nil, fmt.Errorf("s3_get_object: code=%d err=%v", code, err)
	}
	md5 := strings.TrimSpace(stdout)
	actx.Log("  get s3://%s/%s md5=%s", bucket, key, md5)
	if act.SaveAs != "" {
		actx.Vars[act.SaveAs] = md5
	}
	return map[string]string{"value": md5}, nil
}

// s3List lists a bucket (ListObjectsV2) and returns the raw count of <Key> tags.
// Params: endpoint, bucket.
func s3List(ctx context.Context, actx *tr.ActionContext, act tr.Action) (map[string]string, error) {
	node, err := actions.GetNode(actx, act.Node)
	if err != nil {
		return nil, fmt.Errorf("s3_list: %w", err)
	}
	bucket := act.Params["bucket"]
	if bucket == "" {
		return nil, fmt.Errorf("s3_list: bucket param required")
	}
	endpoint := endpointFor(actx, act)
	cmd := fmt.Sprintf("curl -s 'http://%s/%s?list-type=2' 2>/dev/null | grep -o '<Key>' | wc -l", endpoint, bucket)
	stdout, _, code, err := node.Run(ctx, cmd)
	if err != nil || code != 0 {
		return nil, fmt.Errorf("s3_list: code=%d err=%v", code, err)
	}
	count := strings.TrimSpace(stdout)
	actx.Log("  list s3://%s objects=%s", bucket, count)
	if act.SaveAs != "" {
		actx.Vars[act.SaveAs] = count
	}
	return map[string]string{"value": count}, nil
}

// s3VerifyRoundtrip is the convenience smoke: make bucket, put random object,
// get it back, assert md5 matches.
// Params: endpoint, bucket (default sw-smoke), key (default smoke.dat),
// size (default 1M), node.
func s3VerifyRoundtrip(ctx context.Context, actx *tr.ActionContext, act tr.Action) (map[string]string, error) {
	node, err := actions.GetNode(actx, act.Node)
	if err != nil {
		return nil, fmt.Errorf("s3_verify_roundtrip: %w", err)
	}
	bucket := act.Params["bucket"]
	if bucket == "" {
		bucket = "sw-smoke"
	}
	key := act.Params["key"]
	if key == "" {
		key = "smoke.dat"
	}
	size := act.Params["size"]
	if size == "" {
		size = "1M"
	}
	endpoint := endpointFor(actx, act)
	base := fmt.Sprintf("http://%s/%s", endpoint, bucket)
	cmd := fmt.Sprintf(`
set -e
curl -s -o /dev/null -X PUT '%s'
TF=/tmp/sw-s3-rt-$$.dat
dd if=/dev/urandom bs=%s count=1 2>/dev/null > $TF
PUT_MD5=$(md5sum $TF | awk '{print $1}')
curl -s -o /dev/null -X PUT --data-binary @$TF '%s/%s'
GET_MD5=$(curl -s '%s/%s' 2>/dev/null | md5sum | awk '{print $1}')
rm -f $TF
if [ "$PUT_MD5" = "$GET_MD5" ] && [ -n "$PUT_MD5" ]; then
  echo "OK bucket=%s key=%s put_md5=$PUT_MD5 get_md5=$GET_MD5"
else
  echo "FAIL bucket=%s key=%s put_md5=$PUT_MD5 get_md5=$GET_MD5"; exit 1
fi
`, base, size, base, key, base, key, bucket, key, bucket, key)

	stdout, stderr, code, err := node.Run(ctx, cmd)
	if err != nil || code != 0 {
		return nil, fmt.Errorf("s3_verify_roundtrip: FAIL stdout=%s stderr=%s code=%d err=%v",
			strings.TrimSpace(stdout), strings.TrimSpace(stderr), code, err)
	}
	actx.Log("  %s", strings.TrimSpace(stdout))
	return map[string]string{"value": strings.TrimSpace(stdout)}, nil
}

// keep the infra import (mirrors the kv pack) — node types flow through actions.
var _ = (*infra.Node)(nil)
