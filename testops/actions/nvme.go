package actions

import (
	"context"
	"encoding/base64"
	"encoding/json"
	"fmt"
	"strconv"
	"strings"
	"time"

	tr "github.com/seaweedfs/artifactory/testops"
	"github.com/seaweedfs/artifactory/testops/infra"
)

func base64Decode(s string) ([]byte, error) {
	return base64.StdEncoding.DecodeString(s)
}

// RegisterNVMeActions registers NVMe/TCP client actions.
func RegisterNVMeActions(r *tr.Registry) {
	r.RegisterFunc("nvme_connect", tr.TierBlock, nvmeConnect)
	r.RegisterFunc("nvme_disconnect", tr.TierBlock, nvmeDisconnect)
	r.RegisterFunc("nvme_get_device", tr.TierBlock, nvmeGetDevice)
	r.RegisterFunc("nvme_cleanup", tr.TierBlock, nvmeCleanup)
	r.RegisterFunc("nvme_id_ctrl", tr.TierBlock, nvmeIdCtrl)
	r.RegisterFunc("nvme_id_ns", tr.TierBlock, nvmeIdNs)
	r.RegisterFunc("nvme_read_ana_log", tr.TierBlock, nvmeReadANALog)
	r.RegisterFunc("nvme_assert_subsystem", tr.TierBlock, nvmeAssertSubsystem)
}

// nvmeConnect connects to an NVMe/TCP target.
// Params: target (required). Uses TargetSpec.NvmePort and NQN().
// Returns: value = NQN (for subsequent disconnect).
func nvmeConnect(ctx context.Context, actx *tr.ActionContext, act tr.Action) (map[string]string, error) {
	targetName := act.Target
	if targetName == "" {
		return nil, fmt.Errorf("nvme_connect: target is required")
	}

	spec, ok := actx.Scenario.Targets[targetName]
	if !ok {
		return nil, fmt.Errorf("nvme_connect: target %q not in scenario", targetName)
	}

	host, err := GetTargetHost(actx, targetName)
	if err != nil {
		return nil, err
	}

	node, err := GetNode(actx, act.Node)
	if err != nil {
		return nil, fmt.Errorf("nvme_connect: %w", err)
	}

	nqn := spec.NQN()
	port := spec.NvmePort
	if port == 0 {
		port = 4420
	}

	actx.Log("  nvme connect %s -> %s:%d nqn=%s", targetName, host, port, nqn)
	cmd := fmt.Sprintf("nvme connect -t tcp -n %s -a %s -s %d", nqn, host, port)
	stdout, stderr, code, err := node.RunRoot(ctx, cmd)
	if err != nil || code != 0 {
		// Treat "already connected" as success.
		if strings.Contains(stdout+stderr, "already connected") {
			actx.Log("  already connected")
			return map[string]string{"value": nqn}, nil
		}
		return nil, fmt.Errorf("nvme_connect: code=%d stdout=%s stderr=%s err=%v", code, stdout, stderr, err)
	}

	return map[string]string{"value": nqn}, nil
}

// nvmeDisconnect disconnects from an NVMe/TCP target.
// Params: target (required).
func nvmeDisconnect(ctx context.Context, actx *tr.ActionContext, act tr.Action) (map[string]string, error) {
	targetName := act.Target
	if targetName == "" {
		return nil, fmt.Errorf("nvme_disconnect: target is required")
	}

	spec, ok := actx.Scenario.Targets[targetName]
	if !ok {
		return nil, fmt.Errorf("nvme_disconnect: target %q not in scenario", targetName)
	}

	node, err := GetNode(actx, act.Node)
	if err != nil {
		return nil, fmt.Errorf("nvme_disconnect: %w", err)
	}

	nqn := spec.NQN()
	actx.Log("  nvme disconnect nqn=%s", nqn)
	cmd := fmt.Sprintf("nvme disconnect -n %s", nqn)
	stdout, stderr, code, err := node.RunRoot(ctx, cmd)
	if err != nil || code != 0 {
		outStr := stdout + stderr
		// Treat "not connected" / "no subsystem" as success (idempotent).
		if strings.Contains(outStr, "not connected") || strings.Contains(outStr, "No subsystemtype") || strings.Contains(outStr, "Invalid argument") {
			actx.Log("  already disconnected")
			return nil, nil
		}
		return nil, fmt.Errorf("nvme_disconnect: code=%d output=%s err=%v", code, outStr, err)
	}

	return nil, nil
}

// nvmeGetDevice finds the block device path for an NVMe/TCP connection.
// Params: target (required). Polls nvme list-subsys until device appears.
// Returns: value = /dev/nvmeXn1
func nvmeGetDevice(ctx context.Context, actx *tr.ActionContext, act tr.Action) (map[string]string, error) {
	targetName := act.Target
	if targetName == "" {
		return nil, fmt.Errorf("nvme_get_device: target is required")
	}

	spec, ok := actx.Scenario.Targets[targetName]
	if !ok {
		return nil, fmt.Errorf("nvme_get_device: target %q not in scenario", targetName)
	}

	node, err := GetNode(actx, act.Node)
	if err != nil {
		return nil, fmt.Errorf("nvme_get_device: %w", err)
	}

	nqn := spec.NQN()
	actx.Log("  waiting for NVMe device for nqn=%s ...", nqn)

	// Poll for up to 10 seconds.
	deadline := time.After(10 * time.Second)
	ticker := time.NewTicker(500 * time.Millisecond)
	defer ticker.Stop()

	for {
		select {
		case <-ctx.Done():
			return nil, ctx.Err()
		case <-deadline:
			return nil, fmt.Errorf("nvme_get_device: timeout waiting for device (nqn=%s)", nqn)
		case <-ticker.C:
			dev, findErr := findNVMeDevice(ctx, node, nqn)
			if findErr != nil {
				continue // retry
			}
			if dev != "" {
				actx.Log("  found device: %s", dev)
				return map[string]string{"value": dev}, nil
			}
		}
	}
}

// nvmeCleanup disconnects all NVMe/TCP subsystems matching our prefix.
func nvmeCleanup(ctx context.Context, actx *tr.ActionContext, act tr.Action) (map[string]string, error) {
	node, err := GetNode(actx, act.Node)
	if err != nil {
		return nil, fmt.Errorf("nvme_cleanup: %w", err)
	}

	cmd := "nvme disconnect-all 2>/dev/null || true"
	node.RunRoot(ctx, cmd)
	actx.Log("  nvme disconnect-all complete")
	return nil, nil
}

// findNVMeDevice resolves the merged namespace device path for the
// given NQN. Under native NVMe multipath both controllers share one
// namespace device that hangs off /sys/class/nvme-subsystem/<sub>/,
// not off either controller. Walk sysfs first; if that returns
// nothing (e.g. multipath disabled, single controller), fall back
// to deriving /dev/<path-name>n1 from the controller name.
//
// Returns "" when the NQN is not yet present (caller polls).
func findNVMeDevice(ctx context.Context, node *infra.Node, nqn string) (string, error) {
	cmd := "nvme list-subsys -o json 2>/dev/null"
	stdout, _, code, err := node.RunRoot(ctx, cmd)
	if err != nil || code != 0 {
		return "", fmt.Errorf("nvme list-subsys failed: code=%d err=%v", code, err)
	}

	view, parseErr := parseListSubsys(stdout)
	if parseErr != nil {
		return "", fmt.Errorf("nvme list-subsys parse: %w", parseErr)
	}

	sub := view.findByNQN(nqn)
	if sub == nil {
		return "", nil // NQN not yet present
	}

	// Preferred: sysfs walk gives the merged ns device name.
	devs, sysErr := nsDevicesViaSysfs(ctx, node, nqn)
	if sysErr == nil && len(devs) > 0 {
		return devs[0], nil
	}

	// Fallback: derive /dev/<controller>n1. Single-path topologies
	// produce the right answer; under multipath this is the bug we
	// fixed by preferring sysfs.
	for _, p := range sub.Paths {
		if p.Name == "" {
			continue
		}
		if strings.EqualFold(p.Transport, "tcp") && strings.EqualFold(p.State, "live") {
			return "/dev/" + p.Name + "n1", nil
		}
	}
	for _, p := range sub.Paths {
		if p.Name != "" {
			return "/dev/" + p.Name + "n1", nil
		}
	}
	return "", nil
}

// nsDevicesViaSysfs returns the namespace devices owned by the
// subsystem matching nqn, by walking /sys/class/nvme-subsystem.
// Empty result with nil error means "no subsystem matched" — the
// caller should not treat that as failure, just as "not yet."
func nsDevicesViaSysfs(ctx context.Context, node *infra.Node, nqn string) ([]string, error) {
	cmd := fmt.Sprintf(`
for d in /sys/class/nvme-subsystem/*/; do
  if [ "$(cat "$d/subsysnqn" 2>/dev/null)" = "%s" ]; then
    for entry in "$d"*; do
      base=$(basename "$entry")
      case "$base" in
        nvme[0-9]*n[0-9]*) echo "/dev/$base" ;;
      esac
    done
  fi
done`, nqn)
	stdout, _, code, err := node.RunRoot(ctx, cmd)
	if err != nil || code != 0 {
		return nil, fmt.Errorf("sysfs walk: code=%d err=%v", code, err)
	}
	var devs []string
	for _, line := range strings.Split(strings.TrimSpace(stdout), "\n") {
		line = strings.TrimSpace(line)
		if line == "" {
			continue
		}
		devs = append(devs, line)
	}
	return devs, nil
}

// ListSubsysView is the structured result of parsing
// `nvme list-subsys -o json`. Pure-data, deterministic; safe for
// fixture-test round-trip.
type ListSubsysView struct {
	Subsystems []Subsystem `json:"subsystems"`
}

// Subsystem is one NVMe subsystem (post-merge), addressed by NQN
// and reachable through one or more controller Paths.
type Subsystem struct {
	Name     string `json:"name"`
	NQN      string `json:"nqn"`
	IOPolicy string `json:"io_policy,omitempty"`
	Paths    []Path `json:"paths"`
}

// Path is one controller route into a subsystem.
type Path struct {
	Name      string `json:"name"`
	Transport string `json:"transport"`
	Address   string `json:"address,omitempty"`
	State     string `json:"state,omitempty"`
}

// findByNQN returns the first subsystem matching nqn, or nil.
func (v *ListSubsysView) findByNQN(nqn string) *Subsystem {
	for i := range v.Subsystems {
		if v.Subsystems[i].NQN == nqn {
			return &v.Subsystems[i]
		}
	}
	return nil
}

// parseListSubsys parses the JSON output of `nvme list-subsys -o
// json`. Handles both the host-wrapper shape (top-level array of
// hosts, each with a Subsystems array) and the older flat shape
// (single object with Subsystems). Pure function; fixture-tested.
func parseListSubsys(stdout string) (*ListSubsysView, error) {
	stdout = strings.TrimSpace(stdout)
	if stdout == "" {
		return &ListSubsysView{}, nil
	}

	view := &ListSubsysView{}

	// Try host-wrapper shape first.
	var hosts []rawHost
	if err := json.Unmarshal([]byte(stdout), &hosts); err == nil {
		for _, h := range hosts {
			for _, raw := range h.Subsystems {
				view.Subsystems = append(view.Subsystems, raw.toSubsystem())
			}
		}
		return view, nil
	}

	// Flat shape (older nvme-cli).
	var single rawHost
	if err := json.Unmarshal([]byte(stdout), &single); err != nil {
		return nil, err
	}
	for _, raw := range single.Subsystems {
		view.Subsystems = append(view.Subsystems, raw.toSubsystem())
	}
	return view, nil
}

type rawHost struct {
	Subsystems []rawSubsystem `json:"Subsystems"`
}

type rawSubsystem struct {
	Name     string    `json:"Name"`
	NQN      string    `json:"NQN"`
	IOPolicy string    `json:"IOPolicy"`
	Paths    []rawPath `json:"Paths"`
}

type rawPath struct {
	Name      string `json:"Name"`
	Transport string `json:"Transport"`
	Address   string `json:"Address"`
	State     string `json:"State"`
}

func (r rawSubsystem) toSubsystem() Subsystem {
	out := Subsystem{
		Name:     r.Name,
		NQN:      r.NQN,
		IOPolicy: r.IOPolicy,
	}
	for _, p := range r.Paths {
		out.Paths = append(out.Paths, Path{
			Name:      p.Name,
			Transport: p.Transport,
			Address:   p.Address,
			State:     p.State,
		})
	}
	return out
}

// ============================================================
// nvme_id_ctrl / nvme_id_ns
// ============================================================

// IdCtrl is the parsed Identify Controller view exposing the fields
// our assertions care about. Spec field names are preserved.
type IdCtrl struct {
	VID       uint16 `json:"vid"`
	SSVID     uint16 `json:"ssvid"`
	SN        string `json:"sn"`
	MN        string `json:"mn"`
	FR        string `json:"fr"`
	CMIC      uint8  `json:"cmic"`
	MDTS      uint8  `json:"mdts"`
	CNTLID    uint16 `json:"cntlid"`
	Ver       uint32 `json:"ver"`
	OACS      uint16 `json:"oacs"`
	ANATT     uint8  `json:"anatt"`
	ANACAP    uint8  `json:"anacap"`
	ANAGRPMAX uint32 `json:"anagrpmax"`
	NANAGRPID uint32 `json:"nanagrpid"`
	NN        uint32 `json:"nn"`
	SubNQN    string `json:"subnqn"`
}

// IdNs is the parsed Identify Namespace view.
type IdNs struct {
	NSZE     uint64 `json:"nsze"`
	NCAP     uint64 `json:"ncap"`
	NUSE     uint64 `json:"nuse"`
	NSFEAT   uint8  `json:"nsfeat"`
	NLBAF    uint8  `json:"nlbaf"`
	FLBAS    uint8  `json:"flbas"`
	NMIC     uint8  `json:"nmic"`
	ANAGRPID uint32 `json:"anagrpid"`
	NGUID    string `json:"nguid"`
	EUI64    string `json:"eui64"`
}

// parseIdCtrl parses the JSON output of `nvme id-ctrl -o json`.
// Trims trailing whitespace on string fields (sn/mn/fr are space-
// padded to fixed widths in the wire format).
func parseIdCtrl(stdout string) (*IdCtrl, error) {
	stdout = strings.TrimSpace(stdout)
	if stdout == "" {
		return nil, fmt.Errorf("parseIdCtrl: empty input")
	}
	var c IdCtrl
	if err := json.Unmarshal([]byte(stdout), &c); err != nil {
		return nil, fmt.Errorf("parseIdCtrl: %w", err)
	}
	c.SN = strings.TrimSpace(c.SN)
	c.MN = strings.TrimSpace(c.MN)
	c.FR = strings.TrimSpace(c.FR)
	c.SubNQN = strings.TrimSpace(c.SubNQN)
	return &c, nil
}

// parseIdNs parses the JSON output of `nvme id-ns -o json`.
func parseIdNs(stdout string) (*IdNs, error) {
	stdout = strings.TrimSpace(stdout)
	if stdout == "" {
		return nil, fmt.Errorf("parseIdNs: empty input")
	}
	var n IdNs
	if err := json.Unmarshal([]byte(stdout), &n); err != nil {
		return nil, fmt.Errorf("parseIdNs: %w", err)
	}
	return &n, nil
}

// nvmeIdCtrl runs `nvme id-ctrl -o json <dev>` on the node and returns
// the parsed IdCtrl. The device path is taken from params.dev or
// resolved via the target's NQN.
//
// Params:
//
//	target: optional, used for sysfs device resolution if dev not set
//	dev:    optional, controller device path (e.g. /dev/nvme1)
//	node:   optional, defaults to local
//
// Returns: value = JSON-marshalled IdCtrl
func nvmeIdCtrl(ctx context.Context, actx *tr.ActionContext, act tr.Action) (map[string]string, error) {
	dev, err := resolveNVMeCtrlDevice(ctx, actx, act)
	if err != nil {
		return nil, fmt.Errorf("nvme_id_ctrl: %w", err)
	}
	node, err := GetNode(actx, act.Node)
	if err != nil {
		return nil, fmt.Errorf("nvme_id_ctrl: %w", err)
	}
	cmd := fmt.Sprintf("nvme id-ctrl -o json %s 2>/dev/null", dev)
	stdout, stderr, code, err := node.RunRoot(ctx, cmd)
	if err != nil || code != 0 {
		return nil, fmt.Errorf("nvme_id_ctrl: code=%d stderr=%s err=%v", code, stderr, err)
	}
	parsed, err := parseIdCtrl(stdout)
	if err != nil {
		return nil, fmt.Errorf("nvme_id_ctrl: parse: %w", err)
	}
	out, err := json.Marshal(parsed)
	if err != nil {
		return nil, fmt.Errorf("nvme_id_ctrl: marshal: %w", err)
	}
	actx.Log("  id-ctrl %s: cmic=%d cntlid=%d nn=%d subnqn=%s",
		dev, parsed.CMIC, parsed.CNTLID, parsed.NN, parsed.SubNQN)
	return map[string]string{"value": string(out)}, nil
}

// nvmeIdNs runs `nvme id-ns -o json <ns_dev>` on the node and returns
// the parsed IdNs.
//
// Params:
//
//	target: optional, used for sysfs device resolution if dev not set
//	dev:    optional, namespace device path (e.g. /dev/nvme1n1)
//	node:   optional, defaults to local
//
// Returns: value = JSON-marshalled IdNs
func nvmeIdNs(ctx context.Context, actx *tr.ActionContext, act tr.Action) (map[string]string, error) {
	dev, err := resolveNVMeNSDevice(ctx, actx, act)
	if err != nil {
		return nil, fmt.Errorf("nvme_id_ns: %w", err)
	}
	node, err := GetNode(actx, act.Node)
	if err != nil {
		return nil, fmt.Errorf("nvme_id_ns: %w", err)
	}
	cmd := fmt.Sprintf("nvme id-ns -o json %s 2>/dev/null", dev)
	stdout, stderr, code, err := node.RunRoot(ctx, cmd)
	if err != nil || code != 0 {
		return nil, fmt.Errorf("nvme_id_ns: code=%d stderr=%s err=%v", code, stderr, err)
	}
	parsed, err := parseIdNs(stdout)
	if err != nil {
		return nil, fmt.Errorf("nvme_id_ns: parse: %w", err)
	}
	out, err := json.Marshal(parsed)
	if err != nil {
		return nil, fmt.Errorf("nvme_id_ns: marshal: %w", err)
	}
	actx.Log("  id-ns %s: nsze=%d nmic=%d anagrpid=%d nguid=%s",
		dev, parsed.NSZE, parsed.NMIC, parsed.ANAGRPID, parsed.NGUID)
	return map[string]string{"value": string(out)}, nil
}

// resolveNVMeCtrlDevice picks a controller device path: explicit
// params.dev wins; otherwise the first live path under the target's
// NQN. Empty string is an error (caller cannot proceed).
func resolveNVMeCtrlDevice(ctx context.Context, actx *tr.ActionContext, act tr.Action) (string, error) {
	if dev, ok := act.Params["dev"]; ok && dev != "" {
		return dev, nil
	}
	if act.Target == "" {
		return "", fmt.Errorf("either params.dev or target is required")
	}
	spec, ok := actx.Scenario.Targets[act.Target]
	if !ok {
		return "", fmt.Errorf("target %q not in scenario", act.Target)
	}
	node, err := GetNode(actx, act.Node)
	if err != nil {
		return "", err
	}
	stdout, _, _, _ := node.RunRoot(ctx, "nvme list-subsys -o json 2>/dev/null")
	view, err := parseListSubsys(stdout)
	if err != nil {
		return "", fmt.Errorf("list-subsys parse: %w", err)
	}
	sub := view.findByNQN(spec.NQN())
	if sub == nil {
		return "", fmt.Errorf("NQN %q not present", spec.NQN())
	}
	for _, p := range sub.Paths {
		if p.Name != "" && strings.EqualFold(p.State, "live") {
			return "/dev/" + p.Name, nil
		}
	}
	return "", fmt.Errorf("no live controller path for %s", spec.NQN())
}

// resolveNVMeNSDevice picks a namespace device path: explicit
// params.dev wins; otherwise the merged ns device under the
// subsystem matching the target's NQN.
func resolveNVMeNSDevice(ctx context.Context, actx *tr.ActionContext, act tr.Action) (string, error) {
	if dev, ok := act.Params["dev"]; ok && dev != "" {
		return dev, nil
	}
	if act.Target == "" {
		return "", fmt.Errorf("either params.dev or target is required")
	}
	spec, ok := actx.Scenario.Targets[act.Target]
	if !ok {
		return "", fmt.Errorf("target %q not in scenario", act.Target)
	}
	node, err := GetNode(actx, act.Node)
	if err != nil {
		return "", err
	}
	dev, err := findNVMeDevice(ctx, node, spec.NQN())
	if err != nil {
		return "", err
	}
	if dev == "" {
		return "", fmt.Errorf("no namespace device for %s", spec.NQN())
	}
	return dev, nil
}

// ============================================================
// nvme_read_ana_log
// ============================================================

// ANA state codes (NVMe Base Spec §5.16.1.13 ANA Log Page).
const (
	ANAStateOptimized      = 0x01
	ANAStateNonOptimized   = 0x02
	ANAStateInaccessible   = 0x03
	ANAStatePersistentLoss = 0x04
	ANAStateChange         = 0x0F
)

// ANALog is the parsed ANA log page. M1 covers the single-group
// shape (group_count=1, one NSID per group) which matches V3's
// current model. Multi-group support stays out until a product
// scenario needs it; the parser handles count>1 but the test
// fixture set is single-group.
type ANALog struct {
	ChangeCount uint64     `json:"change_count"`
	GroupCount  uint16     `json:"group_count"`
	Groups      []ANAGroup `json:"groups"`
}

// ANAGroup is one ANA group descriptor.
type ANAGroup struct {
	GroupID          uint32   `json:"group_id"`
	NSIDCount        uint32   `json:"nsid_count"`
	GroupChangeCount uint64   `json:"group_change_count"`
	State            uint8    `json:"state"`
	StateName        string   `json:"state_name"`
	NSIDs            []uint32 `json:"nsids"`
}

// anaStateName maps the raw state byte to a human-readable name.
// Unknown states get "unknown_<hex>" so the bug surfaces cleanly
// in goldens rather than silently being categorized as anything.
func anaStateName(state uint8) string {
	switch state {
	case ANAStateOptimized:
		return "optimized"
	case ANAStateNonOptimized:
		return "non_optimized"
	case ANAStateInaccessible:
		return "inaccessible"
	case ANAStatePersistentLoss:
		return "persistent_loss"
	case ANAStateChange:
		return "change"
	default:
		return fmt.Sprintf("unknown_0x%02x", state)
	}
}

// parseANALog parses the ANA log page binary returned by
// `nvme get-log <dev> -i 0x0c -b`.
//
// Binary layout (little-endian; NVMe Base Spec §5.16.1.13):
//
//	[ 0:  8) change_count       (u64)
//	[ 8: 10) group_count        (u16)
//	[10: 16) reserved
//	[16: 16+group): per-group descriptor:
//	  [+0:  +4) group_id           (u32)
//	  [+4:  +8) nsid_count         (u32)
//	  [+8: +16) group_change_count (u64)
//	  [+16]    state              (u8)
//	  [+17:+20] reserved
//	  [+20:+20+4*nsid_count] nsids (u32 each)
//
// Minimum 16 bytes required (header). Per-group is at least 24
// bytes header + 4*nsid_count bytes of NSIDs.
func parseANALog(data []byte) (*ANALog, error) {
	if len(data) < 16 {
		return nil, fmt.Errorf("parseANALog: %d bytes < 16-byte header minimum", len(data))
	}
	out := &ANALog{
		ChangeCount: leU64(data[0:8]),
		GroupCount:  leU16(data[8:10]),
	}
	off := 16
	for i := 0; i < int(out.GroupCount); i++ {
		if off+24 > len(data) {
			return nil, fmt.Errorf("parseANALog: group %d header truncated at offset %d", i, off)
		}
		g := ANAGroup{
			GroupID:          leU32(data[off : off+4]),
			NSIDCount:        leU32(data[off+4 : off+8]),
			GroupChangeCount: leU64(data[off+8 : off+16]),
			State:            data[off+16],
		}
		g.StateName = anaStateName(g.State)
		off += 20
		need := int(g.NSIDCount) * 4
		if off+need > len(data) {
			return nil, fmt.Errorf("parseANALog: group %d NSID list truncated at offset %d (need %d bytes for %d NSIDs)",
				i, off, need, g.NSIDCount)
		}
		for j := uint32(0); j < g.NSIDCount; j++ {
			g.NSIDs = append(g.NSIDs, leU32(data[off:off+4]))
			off += 4
		}
		out.Groups = append(out.Groups, g)
	}
	return out, nil
}

func leU16(b []byte) uint16 { return uint16(b[0]) | uint16(b[1])<<8 }
func leU32(b []byte) uint32 {
	return uint32(b[0]) | uint32(b[1])<<8 | uint32(b[2])<<16 | uint32(b[3])<<24
}
func leU64(b []byte) uint64 {
	return uint64(b[0]) | uint64(b[1])<<8 | uint64(b[2])<<16 | uint64(b[3])<<24 |
		uint64(b[4])<<32 | uint64(b[5])<<40 | uint64(b[6])<<48 | uint64(b[7])<<56
}

// nvmeReadANALog runs `nvme get-log <ns_dev> -i 0x0c -l <len> -b` on
// the node and returns the parsed ANALog.
//
// Params:
//
//	target: optional, used for sysfs device resolution
//	dev:    optional, namespace device path
//	length: optional, log page byte length (default 40 — single
//	        group + single NSID, matches V3 today)
//	node:   optional
//
// Returns: value = JSON-marshalled ANALog
func nvmeReadANALog(ctx context.Context, actx *tr.ActionContext, act tr.Action) (map[string]string, error) {
	dev, err := resolveNVMeNSDevice(ctx, actx, act)
	if err != nil {
		return nil, fmt.Errorf("nvme_read_ana_log: %w", err)
	}
	node, err := GetNode(actx, act.Node)
	if err != nil {
		return nil, fmt.Errorf("nvme_read_ana_log: %w", err)
	}
	length := "40"
	if v, ok := act.Params["length"]; ok && v != "" {
		length = v
	}
	tmpFile := fmt.Sprintf("/tmp/sw-test-runner-ana-%d.bin", time.Now().UnixNano())
	cmd := fmt.Sprintf("nvme get-log %s -i 0x0c -l %s -b > %s 2>/dev/null && cat %s | base64 && rm -f %s",
		dev, length, tmpFile, tmpFile, tmpFile)
	stdout, stderr, code, err := node.RunRoot(ctx, cmd)
	if err != nil || code != 0 {
		return nil, fmt.Errorf("nvme_read_ana_log: code=%d stderr=%s err=%v", code, stderr, err)
	}
	binData, err := decodeBase64Trimmed(stdout)
	if err != nil {
		return nil, fmt.Errorf("nvme_read_ana_log: base64 decode: %w", err)
	}
	parsed, err := parseANALog(binData)
	if err != nil {
		return nil, fmt.Errorf("nvme_read_ana_log: parse: %w", err)
	}
	out, err := json.Marshal(parsed)
	if err != nil {
		return nil, fmt.Errorf("nvme_read_ana_log: marshal: %w", err)
	}
	if len(parsed.Groups) > 0 {
		actx.Log("  ana-log %s: groups=%d state=%s nsid_count=%d",
			dev, parsed.GroupCount, parsed.Groups[0].StateName, parsed.Groups[0].NSIDCount)
	} else {
		actx.Log("  ana-log %s: groups=0 (empty)", dev)
	}
	return map[string]string{"value": string(out)}, nil
}

// decodeBase64Trimmed decodes base64 with whitespace tolerated.
// nvme get-log emits raw bytes; we base64 them on the wire to
// survive ssh/exec text channels cleanly.
func decodeBase64Trimmed(s string) ([]byte, error) {
	s = strings.TrimSpace(s)
	s = strings.ReplaceAll(s, "\n", "")
	s = strings.ReplaceAll(s, "\r", "")
	return base64Decode(s)
}

// ============================================================
// nvme_assert_subsystem
// ============================================================
//
// Composite declarative assertion. Replaces ~30 lines of bash
// gate logic per scenario (path-count grep, sysfs walk, identity
// regex, ANA state byte check) with one YAML block.
//
// Each P4 product fix from 2026-05-07 corresponds to ONE field
// here; a regression of any flips the matching expectation:
//   c0e4a6a (CMIC bit 1)             → cmic
//   035702f (NMIC.SHARED bit 0)      → nmic
//   60d3533 (per-replica cntlid)     → cntlid_distinct
//   ANA / multipath wire             → paths / namespace_devices /
//                                       ana_state / anagrpid

// assertSubsystemSpec collects every per-field expectation. nil
// pointers / empty strings mean "no assertion on this field."
type assertSubsystemSpec struct {
	Paths            *int
	NamespaceDevices *int
	ANAState         string
	CMIC             *uint8
	NMIC             *uint8
	ANAGRPID         *uint32
	CntlidDistinct   bool // every controller has a distinct cntlid
}

// assertSubsystemEvidence is the observed state, gathered by the
// action body. Pure evaluator below operates on this.
type assertSubsystemEvidence struct {
	Subsystem   *Subsystem
	NSDevices   []string
	Controllers []*IdCtrl // one per path; aligned by index with Subsystem.Paths
	Namespace   *IdNs     // collected from first ns device, optional
	ANALog      *ANALog   // optional
}

// evaluateAssertSubsystem returns one human-readable failure per
// mismatched field. Empty result means everything matched.
//
// This is the pure evaluator: no I/O, no node, no shell. Unit-
// testable against synthesized evidence.
func evaluateAssertSubsystem(spec assertSubsystemSpec, ev assertSubsystemEvidence) []string {
	var failures []string

	if ev.Subsystem == nil {
		return []string{"subsystem: NQN not present in nvme list-subsys"}
	}

	if spec.Paths != nil {
		got := len(ev.Subsystem.Paths)
		if got != *spec.Paths {
			failures = append(failures,
				fmt.Sprintf("paths: expected=%d got=%d", *spec.Paths, got))
		}
	}

	if spec.NamespaceDevices != nil {
		got := len(ev.NSDevices)
		if got != *spec.NamespaceDevices {
			failures = append(failures,
				fmt.Sprintf("namespace_devices: expected=%d got=%d (devices=%v)",
					*spec.NamespaceDevices, got, ev.NSDevices))
		}
	}

	if spec.CMIC != nil {
		for i, c := range ev.Controllers {
			if c == nil {
				continue
			}
			if c.CMIC != *spec.CMIC {
				failures = append(failures,
					fmt.Sprintf("cmic[path=%d]: expected=0x%02x got=0x%02x",
						i, *spec.CMIC, c.CMIC))
			}
		}
	}

	if spec.CntlidDistinct {
		seen := map[uint16]int{}
		for i, c := range ev.Controllers {
			if c == nil {
				continue
			}
			if prev, ok := seen[c.CNTLID]; ok {
				failures = append(failures,
					fmt.Sprintf("cntlid_distinct: path[%d] cntlid=%d duplicates path[%d]",
						i, c.CNTLID, prev))
			}
			seen[c.CNTLID] = i
		}
	}

	if spec.NMIC != nil && ev.Namespace != nil {
		if ev.Namespace.NMIC != *spec.NMIC {
			failures = append(failures,
				fmt.Sprintf("nmic: expected=0x%02x got=0x%02x",
					*spec.NMIC, ev.Namespace.NMIC))
		}
	}

	if spec.ANAGRPID != nil && ev.Namespace != nil {
		if ev.Namespace.ANAGRPID != *spec.ANAGRPID {
			failures = append(failures,
				fmt.Sprintf("anagrpid: expected=%d got=%d",
					*spec.ANAGRPID, ev.Namespace.ANAGRPID))
		}
	}

	if spec.ANAState != "" && ev.ANALog != nil {
		if len(ev.ANALog.Groups) == 0 {
			failures = append(failures,
				fmt.Sprintf("ana_state: expected=%s got=<no groups in ANA log>", spec.ANAState))
		} else if ev.ANALog.Groups[0].StateName != spec.ANAState {
			failures = append(failures,
				fmt.Sprintf("ana_state: expected=%s got=%s (raw=0x%02x)",
					spec.ANAState, ev.ANALog.Groups[0].StateName, ev.ANALog.Groups[0].State))
		}
	}

	return failures
}

// parseAssertSubsystemSpec extracts the spec from action params.
// Each field is optional — only fields that appear in YAML are
// asserted.
func parseAssertSubsystemSpec(params map[string]string) (assertSubsystemSpec, error) {
	spec := assertSubsystemSpec{}
	if v, ok := params["paths"]; ok && v != "" {
		n, err := strconv.Atoi(v)
		if err != nil {
			return spec, fmt.Errorf("paths: %w", err)
		}
		spec.Paths = &n
	}
	if v, ok := params["namespace_devices"]; ok && v != "" {
		n, err := strconv.Atoi(v)
		if err != nil {
			return spec, fmt.Errorf("namespace_devices: %w", err)
		}
		spec.NamespaceDevices = &n
	}
	if v, ok := params["ana_state"]; ok && v != "" {
		spec.ANAState = v
	}
	if v, ok := params["cmic"]; ok && v != "" {
		n, err := parseHexOrDecU8(v)
		if err != nil {
			return spec, fmt.Errorf("cmic: %w", err)
		}
		spec.CMIC = &n
	}
	if v, ok := params["nmic"]; ok && v != "" {
		n, err := parseHexOrDecU8(v)
		if err != nil {
			return spec, fmt.Errorf("nmic: %w", err)
		}
		spec.NMIC = &n
	}
	if v, ok := params["anagrpid"]; ok && v != "" {
		n, err := strconv.ParseUint(v, 10, 32)
		if err != nil {
			return spec, fmt.Errorf("anagrpid: %w", err)
		}
		u := uint32(n)
		spec.ANAGRPID = &u
	}
	if v, ok := params["cntlid_distinct"]; ok && v != "" {
		spec.CntlidDistinct = (v == "true" || v == "1" || v == "yes")
	}
	return spec, nil
}

// parseHexOrDecU8 accepts "0x0a", "0x0A", "10", or "10".
func parseHexOrDecU8(s string) (uint8, error) {
	s = strings.TrimSpace(s)
	base := 10
	if strings.HasPrefix(s, "0x") || strings.HasPrefix(s, "0X") {
		s = s[2:]
		base = 16
	}
	n, err := strconv.ParseUint(s, base, 8)
	if err != nil {
		return 0, err
	}
	return uint8(n), nil
}

// nvmeAssertSubsystem gathers evidence and runs the pure evaluator.
//
// Params (all optional; fields present in YAML are asserted):
//
//	target:            required (provides NQN)
//	paths:             expected controller-path count under the NQN
//	namespace_devices: expected merged-ns-device count (1 for native multipath)
//	ana_state:         "optimized" | "non_optimized" | "inaccessible" | …
//	cmic:              "0x0a" or "10" (hex or decimal); checked per-path
//	nmic:              "0x01" or "1"; checked on the namespace
//	anagrpid:          decimal; checked on the namespace
//	cntlid_distinct:   "true" → assert every path has a unique cntlid
//	node:              optional, defaults to local
//
// Returns: nil on green; error with one bullet per failed field
// when any assertion fails.
func nvmeAssertSubsystem(ctx context.Context, actx *tr.ActionContext, act tr.Action) (map[string]string, error) {
	spec, err := parseAssertSubsystemSpec(act.Params)
	if err != nil {
		return nil, fmt.Errorf("nvme_assert_subsystem: spec: %w", err)
	}

	if act.Target == "" {
		return nil, fmt.Errorf("nvme_assert_subsystem: target is required")
	}
	targetSpec, ok := actx.Scenario.Targets[act.Target]
	if !ok {
		return nil, fmt.Errorf("nvme_assert_subsystem: target %q not in scenario", act.Target)
	}
	nqn := targetSpec.NQN()

	node, err := GetNode(actx, act.Node)
	if err != nil {
		return nil, fmt.Errorf("nvme_assert_subsystem: %w", err)
	}

	ev := assertSubsystemEvidence{}

	// 1. list-subsys → subsystem + paths.
	stdout, _, _, _ := node.RunRoot(ctx, "nvme list-subsys -o json 2>/dev/null")
	view, err := parseListSubsys(stdout)
	if err != nil {
		return nil, fmt.Errorf("nvme_assert_subsystem: list-subsys parse: %w", err)
	}
	ev.Subsystem = view.findByNQN(nqn)

	// 2. ns devices via sysfs.
	if ev.Subsystem != nil {
		devs, _ := nsDevicesViaSysfs(ctx, node, nqn)
		ev.NSDevices = devs
	}

	// 3. id-ctrl per path (only if a CMIC or cntlid_distinct check is requested).
	if (spec.CMIC != nil || spec.CntlidDistinct) && ev.Subsystem != nil {
		for _, p := range ev.Subsystem.Paths {
			if p.Name == "" {
				ev.Controllers = append(ev.Controllers, nil)
				continue
			}
			ctrlOut, _, _, _ := node.RunRoot(ctx,
				fmt.Sprintf("nvme id-ctrl -o json /dev/%s 2>/dev/null", p.Name))
			c, perr := parseIdCtrl(ctrlOut)
			if perr != nil {
				ev.Controllers = append(ev.Controllers, nil)
				continue
			}
			ev.Controllers = append(ev.Controllers, c)
		}
	}

	// 4. id-ns + ana log on first ns device (only if needed).
	if (spec.NMIC != nil || spec.ANAGRPID != nil || spec.ANAState != "") && len(ev.NSDevices) > 0 {
		nsDev := ev.NSDevices[0]
		if spec.NMIC != nil || spec.ANAGRPID != nil {
			nsOut, _, _, _ := node.RunRoot(ctx,
				fmt.Sprintf("nvme id-ns -o json %s 2>/dev/null", nsDev))
			n, perr := parseIdNs(nsOut)
			if perr == nil {
				ev.Namespace = n
			}
		}
		if spec.ANAState != "" {
			cmd := fmt.Sprintf(
				"f=$(mktemp) && nvme get-log %s -i 0x0c -l 40 -b > $f 2>/dev/null && cat $f | base64 && rm -f $f",
				nsDev)
			out, _, _, _ := node.RunRoot(ctx, cmd)
			binData, derr := decodeBase64Trimmed(out)
			if derr == nil {
				log, perr := parseANALog(binData)
				if perr == nil {
					ev.ANALog = log
				}
			}
		}
	}

	failures := evaluateAssertSubsystem(spec, ev)
	if len(failures) > 0 {
		actx.Log("  nvme_assert_subsystem FAIL: %d field(s) mismatched", len(failures))
		for _, f := range failures {
			actx.Log("    - %s", f)
		}
		return nil, fmt.Errorf("nvme_assert_subsystem: %d failure(s):\n  - %s",
			len(failures), strings.Join(failures, "\n  - "))
	}

	actx.Log("  nvme_assert_subsystem OK (target=%s nqn=%s)", act.Target, nqn)
	return nil, nil
}
