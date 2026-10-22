// Package v1weed is a stub showing the pack-registration shape for
// hypothetical V1 SeaweedFS product (weed v1.5 binary). It is not
// hooked up to a real V1 lab — the actions return errors at runtime —
// but demonstrates how a new product slots into the testrunner.
//
// Real V1 implementation would replace each handler body with shell-out
// commands against the V1 weed binary, similar to packs/block (V2 weed).
package v1weed

import (
	"context"
	"fmt"

	tr "github.com/seaweedfs/artifactory/testops"
)

// RegisterPack registers V1-specific actions on the registry.
// Action names use a v1_ prefix to avoid collision with V2 (no prefix)
// or V3 (v3_ prefix) actions.
func RegisterPack(r *tr.Registry) {
	r.RegisterFunc("v1_start_weed", tr.TierDevOps, v1StartWeed)
	r.RegisterFunc("v1_create_volume", tr.TierBlock, v1CreateVolume)
	r.RegisterFunc("v1_status", tr.TierBlock, v1Status)
}

// v1StartWeed would shell out to the V1 weed binary to start a master/volume.
// Stub implementation; intentionally returns an error so test runs surface
// the gap instead of silently passing.
func v1StartWeed(ctx context.Context, actx *tr.ActionContext, act tr.Action) (map[string]string, error) {
	return nil, fmt.Errorf("v1_start_weed: not yet implemented (pack stub)")
}

func v1CreateVolume(ctx context.Context, actx *tr.ActionContext, act tr.Action) (map[string]string, error) {
	return nil, fmt.Errorf("v1_create_volume: not yet implemented (pack stub)")
}

func v1Status(ctx context.Context, actx *tr.ActionContext, act tr.Action) (map[string]string, error) {
	return nil, fmt.Errorf("v1_status: not yet implemented (pack stub)")
}
