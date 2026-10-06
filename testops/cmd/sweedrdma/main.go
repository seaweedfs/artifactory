// Command sweedrdma is the SeaweedFS RDMA lab test runner.
package main

import (
	"os"

	tr "github.com/seaweedfs/artifactory/testops"
	"github.com/seaweedfs/artifactory/testops/actions"
	"github.com/seaweedfs/artifactory/testops/cli"
	"github.com/seaweedfs/artifactory/testops/packs/rdma"
)

func main() {
	register := func(r *tr.Registry) {
		actions.RegisterCore(r)
		rdma.RegisterPack(r)
	}
	os.Exit(cli.Run(register, os.Args[1:]))
}
