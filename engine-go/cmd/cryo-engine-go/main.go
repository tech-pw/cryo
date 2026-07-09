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
)

func main() {
	log.SetOutput(os.Stderr)
	log.SetFlags(0)

	args := os.Args[1:]
	if len(args) == 0 || args[0] != "execute" {
		fmt.Fprintln(os.Stderr, "usage: cryo-engine-go execute < request.json")
		os.Exit(2)
	}

	// os.Exit skips deferred cleanup — stop profiles explicitly before exit.
	stopProfiles := startProfiles()

	input, err := io.ReadAll(os.Stdin)
	if err != nil {
		stopProfiles()
		writeResponse(protocol.ExecuteResponse{
			OK:    false,
			Error: strPtr(fmt.Sprintf("failed to read stdin: %v", err)),
		}, 1)
		return
	}

	var request protocol.ExecuteRequest
	if err := json.Unmarshal(input, &request); err != nil {
		stopProfiles()
		writeResponse(protocol.ExecuteResponse{
			OK:    false,
			Error: strPtr(fmt.Sprintf("failed to parse request: %v", err)),
		}, 1)
		return
	}

	response := ops.Execute(context.Background(), &request)
	stopProfiles()
	writeResponse(response, exitCode(response))
}

// startProfiles enables CPU + heap profiling when CRYO_ENGINE_PPROF_DIR is set.
// Writes cpu-*.pprof and heap-*.pprof into that directory for go tool pprof.
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

func writeResponse(response protocol.ExecuteResponse, code int) {
	payload, err := json.Marshal(response)
	if err != nil {
		fmt.Fprintf(os.Stderr, "failed to marshal response: %v\n", err)
		os.Exit(1)
	}
	fmt.Println(string(payload))
	os.Exit(code)
}

func exitCode(response protocol.ExecuteResponse) int {
	if response.OK {
		return 0
	}
	return 1
}

func strPtr(value string) *string {
	return &value
}

// Ensure binary name compatibility when invoked as cryo-engine.
func init() {
	if strings.HasSuffix(os.Args[0], "cryo-engine") && !strings.Contains(os.Args[0], "cryo-engine-go") {
		log.Printf("running cryo-engine-go via %s alias", os.Args[0])
	}
}
