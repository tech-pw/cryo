package predicate

import (
	"encoding/json"
	"testing"

	"github.com/cryo-lake/cryo/engine-go/internal/protocol"
)

func TestCandidateFieldNamesDaySuffix(t *testing.T) {
	colType := "date"
	term := protocol.PredicateTermRequest{
		BaseColumn: "event_date",
		Transform:  "day",
		Value:      json.RawMessage(`20337`),
		ColType:    &colType,
	}
	names := candidateFieldNames(term)
	if !contains(names, "event_date_day") {
		t.Fatalf("expected event_date_day in %v", names)
	}
}

func TestNumericLiteralMatchesEpochDay(t *testing.T) {
	actual := int32(20337)
	if !numericLiteralMatches(actual, json.RawMessage(`20337`)) {
		t.Fatal("expected epoch day match")
	}
}

func TestBucketTransformUnsupported(t *testing.T) {
	op := &protocol.OperationRequest{
		Predicates: []protocol.PredicateRequest{
			{Terms: []protocol.PredicateTermRequest{
				{BaseColumn: "user_id", Transform: "bucket", Value: json.RawMessage(`3`)},
			}},
		},
	}
	if reason := UnsupportedReason(op); reason == "" {
		t.Fatal("expected unsupported bucket transform")
	}
}

func TestDayRangeBuildsAndExpression(t *testing.T) {
	colType := "date"
	term := protocol.PredicateTermRequest{
		BaseColumn: "event_date",
		Transform:  "day",
		Value:      json.RawMessage(`20337`),
		ColType:    &colType,
	}
	expr, err := buildTermExpr(term)
	if err != nil {
		t.Fatalf("build term: %v", err)
	}
	if expr.String() == "" {
		t.Fatal("expected non-empty expression")
	}
}

func contains(values []string, target string) bool {
	for _, value := range values {
		if value == target {
			return true
		}
	}
	return false
}
