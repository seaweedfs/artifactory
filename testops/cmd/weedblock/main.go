// Command weedblock is the V2 weed-block product test runner.
// It registers core actions plus the V2 block + KV product packs.
//
// Operator entry:
//
//	weedblock list
//	weedblock validate scenarios/internal/recovery-baseline-failover.yaml
//	weedblock run scenarios/internal/recovery-baseline-failover.yaml
//
// Use this binary against a V2 hardware lab where the weed binary is
// pre-installed at /opt/work/weed and the V2 master is reachable.
package main

import (
	"os"

	tr "github.com/seaweedfs/artifactory/testops"
	"github.com/seaweedfs/artifactory/testops/actions"
	"github.com/seaweedfs/artifactory/testops/cli"
	"github.com/seaweedfs/artifactory/testops/packs/block"
	"github.com/seaweedfs/artifactory/testops/packs/kv"
)

func main() {
	register := func(r *tr.Registry) {
		actions.RegisterCore(r)
		block.RegisterPack(r)
		kv.RegisterPack(r)
	}
	os.Exit(cli.Run(register, os.Args[1:]))
}
