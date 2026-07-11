package main

import (
	"context"
	"encoding/json"
	"fmt"
	"io"
	"log"
	"os"
	"path/filepath"
	"runtime"
	"runtime/pprof"
	"strings"
	"time"

	"github.com/cryo-lake/cryo/engine-go/internal/ops"
	"github.com/cryo-lake/cryo/engine-go/internal/protocol"
	"github.com/cryo-lake/cryo/engine-go/internal/scanstats"
)

func main() {
	log.SetOutput(os.Stderr)
	log.SetFlags(0)

	args := os.Args[1:]
	if len(args) == 0 {
		usage()
		os.Exit(2)
	}

	stopProfiles := startProfiles()

	switch args[0] {
	case "execute":
		runExecute(stopProfiles)
	case "scan-stats":
		runScanStats(stopProfiles)
	case "aggregate-fixture":
		runAggregateFixture(stopProfiles)
	default:
		stopProfiles()
		usage()
		os.Exit(2)
	}
}

func usage() {
	fmt.Fprintln(os.Stderr, "usage: cryo-engine-go execute < request.json")
	fmt.Fprintln(os.Stderr, "       cryo-engine-go scan-stats < request.json")
	fmt.Fprintln(os.Stderr, "       cryo-engine-go aggregate-fixture < fixture.json")
}

func runExecute(stopProfiles func()) {
	input, err := io.ReadAll(os.Stdin)
	if err != nil {
		stopProfiles()
		writeExecuteResponse(protocol.ExecuteResponse{
			OK:    false,
			Error: strPtr(fmt.Sprintf("failed to read stdin: %v", err)),
		}, 1)
		return
	}

	var request protocol.ExecuteRequest
	if err := json.Unmarshal(input, &request); err != nil {
		stopProfiles()
		writeExecuteResponse(protocol.ExecuteResponse{
			OK:    false,
			Error: strPtr(fmt.Sprintf("failed to parse request: %v", err)),
		}, 1)
		return
	}

	response := ops.Execute(context.Background(), &request)
	stopProfiles()
	writeExecuteResponse(response, exitCode(response.OK))
}

func runScanStats(stopProfiles func()) {
	input, err := io.ReadAll(os.Stdin)
	if err != nil {
		stopProfiles()
		writeScanStatsResponse(protocol.ScanStatsResponse{
			OK:    false,
			Error: strPtr(fmt.Sprintf("failed to read stdin: %v", err)),
		}, 1)
		return
	}

	var request protocol.ScanStatsRequest
	if err := json.Unmarshal(input, &request); err != nil {
		stopProfiles()
		writeScanStatsResponse(protocol.ScanStatsResponse{
			OK:    false,
			Error: strPtr(fmt.Sprintf("failed to parse request: %v", err)),
		}, 1)
		return
	}

	response := scanstats.Run(context.Background(), &request)
	stopProfiles()
	writeScanStatsResponse(response, exitCode(response.OK))
}

func runAggregateFixture(stopProfiles func()) {
	input, err := io.ReadAll(os.Stdin)
	if err != nil {
		stopProfiles()
		writeScanStatsResponse(protocol.ScanStatsResponse{
			OK:    false,
			Error: strPtr(fmt.Sprintf("failed to read stdin: %v", err)),
		}, 1)
		return
	}
	var request protocol.AggregateFixtureRequest
	if err := json.Unmarshal(input, &request); err != nil {
		stopProfiles()
		writeScanStatsResponse(protocol.ScanStatsResponse{
			OK:    false,
			Error: strPtr(fmt.Sprintf("failed to parse fixture: %v", err)),
		}, 1)
		return
	}
	response := scanstats.AggregateFromFixture(&request)
	stopProfiles()
	writeScanStatsResponse(response, exitCode(response.OK))
}

func startProfiles() func() {
	dir := strings.TrimSpace(os.Getenv("CRYO_ENGINE_PPROF_DIR"))
	if dir == "" {
		return func() {}
	}
	if err := os.MkdirAll(dir, 0o755); err != nil {
		log.Printf("pprof: mkdir %s: %v", dir, err)
		return func() {}
	}
	stamp := time.Now().UTC().Format("20060102T150405Z")
	cpuPath := filepath.Join(dir, fmt.Sprintf("cpu-%s.pprof", stamp))
	heapPath := filepath.Join(dir, fmt.Sprintf("heap-%s.pprof", stamp))

	cpuFile, err := os.Create(cpuPath)
	if err != nil {
		log.Printf("pprof: create cpu profile: %v", err)
		return func() {}
	}
	if err := pprof.StartCPUProfile(cpuFile); err != nil {
		log.Printf("pprof: start cpu profile: %v", err)
		_ = cpuFile.Close()
		return func() {}
	}
	log.Printf("pprof: writing CPU profile to %s", cpuPath)

	return func() {
		pprof.StopCPUProfile()
		_ = cpuFile.Close()

		runtime.GC()
		heapFile, err := os.Create(heapPath)
		if err != nil {
			log.Printf("pprof: create heap profile: %v", err)
			return
		}
		defer heapFile.Close()
		if err := pprof.WriteHeapProfile(heapFile); err != nil {
			log.Printf("pprof: write heap profile: %v", err)
			return
		}
		log.Printf("pprof: wrote heap profile to %s", heapPath)
	}
}

func writeExecuteResponse(response protocol.ExecuteResponse, code int) {
	payload, err := json.Marshal(response)
	if err != nil {
		fmt.Fprintf(os.Stderr, "failed to marshal response: %v\n", err)
		os.Exit(1)
	}
	fmt.Println(string(payload))
	os.Exit(code)
}

func writeScanStatsResponse(response protocol.ScanStatsResponse, code int) {
	payload, err := json.Marshal(response)
	if err != nil {
		fmt.Fprintf(os.Stderr, "failed to marshal response: %v\n", err)
		os.Exit(1)
	}
	fmt.Println(string(payload))
	os.Exit(code)
}

func exitCode(ok bool) int {
	if ok {
		return 0
	}
	return 1
}

func strPtr(value string) *string {
	return &value
}

func init() {
	if strings.HasSuffix(os.Args[0], "cryo-engine") && !strings.Contains(os.Args[0], "cryo-engine-go") {
		log.Printf("running cryo-engine-go via %s alias", os.Args[0])
	}
}
