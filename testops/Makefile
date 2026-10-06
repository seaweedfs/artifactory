# sw-test-runner — build, test, and wiki helpers.
# Recipes use real tabs (GNU make). Run `make help` for targets.

BIN    := bin
CMDS   := sw-test-runner swblock weedblock weedv1 sweeds3 testops-dashboard testops-controller
GOFLAGS ?=

.PHONY: all help build test vet clean wiki wiki-build $(addprefix build-,$(CMDS))

all: build

help:
	@echo "targets:"
	@echo "  build         build every cmd/ binary into ./$(BIN)"
	@echo "  build-<name>  build one binary ($(CMDS))"
	@echo "  test          go test ./... -count=1"
	@echo "  vet           go vet ./..."
	@echo "  wiki          serve the MkDocs wiki (needs requirements-docs.txt)"
	@echo "  wiki-build    build the static wiki site into ./site"
	@echo "  clean         remove ./$(BIN) and ./site"

build: $(addprefix build-,$(CMDS))

build-%:
	@mkdir -p $(BIN)
	go build $(GOFLAGS) -o $(BIN)/$* ./cmd/$*

test:
	go test ./... -count=1

vet:
	go vet ./...

wiki:
	mkdocs serve

wiki-build:
	mkdocs build

clean:
	rm -rf $(BIN) site
