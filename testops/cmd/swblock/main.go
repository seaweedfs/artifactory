// Command swblock is the V3 seaweed-block product test runner.
// It registers core actions plus the V3 product pack only.
//
// Operator entry:
//
//	swblock list
//	swblock validate scenarios/v3-foo.yaml
//	swblock run scenarios/v3-foo.yaml
//
// V3 product wrappers running outside this repo can use the exact same
// pattern, replacing the v3block import with their own pack.
package main

import (
	"os"

	tr "github.com/seaweedfs/artifactory/testops"
	"github.com/seaweedfs/artifactory/testops/actions"
	"github.com/seaweedfs/artifactory/testops/cli"
	"github.com/seaweedfs/artifactory/testops/packs/v3block"
)

func main() {
	register := func(r *tr.Registry) {
		actions.RegisterCore(r)
		actions.RegisterISCSIActions(r)
		actions.RegisterNVMeActions(r)
		actions.RegisterK8sActions(r)
		v3block.RegisterPack(r)
	}
	os.Exit(cli.Run(register, os.Args[1:]))
}
