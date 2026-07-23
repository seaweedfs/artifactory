// Command weedv1 is the V1 weed product test runner.
// It registers core actions plus the V1 product pack stub.
//
// As of today the V1 pack handlers return "not yet implemented" errors;
// this binary exists to demonstrate the registration shape and to prove
// the multi-product story compiles. Wire real V1 actions in packs/v1weed/
// when the V1 lab is reactivated.
package main

import (
	"os"

	tr "github.com/seaweedfs/artifactory/testops"
	"github.com/seaweedfs/artifactory/testops/actions"
	"github.com/seaweedfs/artifactory/testops/cli"
	"github.com/seaweedfs/artifactory/testops/packs/v1weed"
)

func main() {
	register := func(r *tr.Registry) {
		actions.RegisterCore(r)
		v1weed.RegisterPack(r)
	}
	os.Exit(cli.Run(register, os.Args[1:]))
}
