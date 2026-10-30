// Package s3 is the SeaweedFS S3 gateway product pack for sw-test-runner.
//
// It registers actions that exercise the S3 gateway the way a user would:
// start the all-in-one weed stack with the S3 gateway, create a bucket, put and
// get objects, and verify a checksum round-trip. Like every pack, it talks to
// the product only through shell-out + the S3 HTTP API (no daemon-internal
// imports), so the same runner drives Go (S3) and Rust (VFS) products.
//
// The smoke path uses anonymous S3 over plain curl (dependency-light, matches the
// kv pack style). Production S3 suites layer SigV4 creds / the aws CLI / the
// enterprise Go test suites + ceph s3-tests on top via `exec` — see
// docs/cross-product-testops-standard.md (§S3).
package s3

import tr "github.com/seaweedfs/artifactory/testops"

// Tier groups S3 actions in `swblock list` and the console /api/tiers.
const Tier = "s3"

// RegisterPack registers all S3-specific actions on the registry.
func RegisterPack(r *tr.Registry) {
	r.RegisterFunc("s3_start_stack", Tier, s3StartStack)
	r.RegisterFunc("s3_stop_stack", Tier, s3StopStack)
	r.RegisterFunc("s3_make_bucket", Tier, s3MakeBucket)
	r.RegisterFunc("s3_put_object", Tier, s3PutObject)
	r.RegisterFunc("s3_get_object", Tier, s3GetObject)
	r.RegisterFunc("s3_list", Tier, s3List)
	r.RegisterFunc("s3_verify_roundtrip", Tier, s3VerifyRoundtrip)
}
