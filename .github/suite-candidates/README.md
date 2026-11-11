Foreign workflow files swept from seaweedfs/seaweed-mono, kept here
for porting. They DO NOT RUN from this directory. To port one, move
it into .github/workflows/ and rewire it to the repo's dispatch
pattern: `on: repository_dispatch` types mono-push /
mono-merge-request (plus workflow_dispatch), the run-name convention
`mono:<sha> · branch:<ref> · runner:<tp01|hosted>`, the
clone-mono-source action, and the tp01/hosted runner inputs. Suites
nobody ports should be deleted rather than parked forever.
