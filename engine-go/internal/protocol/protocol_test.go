package protocol_test

import (
	"encoding/json"
	"testing"

	"github.com/cryo-lake/cryo/engine-go/internal/protocol"
)

func TestOperationResultOmitempty(t *testing.T) {
	success := true
	expired := uint64(2)
	result := protocol.OperationResult{
		Operation:        "expire_snapshots",
		Success:          success,
		SnapshotsExpired: &expired,
		DurationMS:       5,
	}
	payload, err := json.Marshal(result)
	if err != nil {
		t.Fatalf("marshal: %v", err)
	}
	var decoded map[string]any
	if err := json.Unmarshal(payload, &decoded); err != nil {
		t.Fatalf("unmarshal: %v", err)
	}
	if _, ok := decoded["files_rewritten"]; ok {
		t.Fatalf("expected files_rewritten to be omitted, got %#v", decoded)
	}
	if decoded["snapshots_expired"] != float64(2) {
		t.Fatalf("unexpected snapshots_expired: %#v", decoded["snapshots_expired"])
	}
}

func TestExecuteResponseRoundTrip(t *testing.T) {
	response := protocol.ExecuteResponse{
		OK:         true,
		TableRef:   "demo.orders",
		Operations: 1,
		Results: []protocol.OperationResult{
			{
				Operation:  "expire_snapshots",
				Success:    true,
				DurationMS: 3,
			},
		},
	}
	payload, err := json.Marshal(response)
	if err != nil {
		t.Fatalf("marshal: %v", err)
	}
	var decoded protocol.ExecuteResponse
	if err := json.Unmarshal(payload, &decoded); err != nil {
		t.Fatalf("unmarshal: %v", err)
	}
	if !decoded.OK || decoded.TableRef != "demo.orders" {
		t.Fatalf("unexpected response: %#v", decoded)
	}
}
