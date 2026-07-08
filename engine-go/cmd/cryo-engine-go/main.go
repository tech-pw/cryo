package main

import (
	"context"
	"encoding/json"
	"fmt"
	"io"
	"log"
	"os"
	"strings"

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

	input, err := io.ReadAll(os.Stdin)
	if err != nil {
		writeResponse(protocol.ExecuteResponse{
			OK:    false,
			Error: strPtr(fmt.Sprintf("failed to read stdin: %v", err)),
		}, 1)
		return
	}

	var request protocol.ExecuteRequest
	if err := json.Unmarshal(input, &request); err != nil {
		writeResponse(protocol.ExecuteResponse{
			OK:    false,
			Error: strPtr(fmt.Sprintf("failed to parse request: %v", err)),
		}, 1)
		return
	}

	response := ops.Execute(context.Background(), &request)
	writeResponse(response, exitCode(response))
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
