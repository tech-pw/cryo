package predicate

import (
	"encoding/json"
	"testing"

	"github.com/apache/iceberg-go"

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

func TestIdentityDateEpochDayBuildsExpression(t *testing.T) {
	colType := "date"
	for _, raw := range []string{`20643`, `"20643"`} {
		term := protocol.PredicateTermRequest{
			BaseColumn: "report_date",
			Transform:  "identity",
			Value:      json.RawMessage(raw),
			ColType:    &colType,
		}
		expr, err := buildTermExpr(term)
		if err != nil {
			t.Fatalf("identity date epoch %s: %v", raw, err)
		}
		if expr.String() == "" {
			t.Fatalf("expected non-empty expression for %s", raw)
		}
	}
}

func TestIdentityTimestampEpochDayBuildsExpression(t *testing.T) {
	colType := "timestamp"
	for _, raw := range []string{`20643`, `"20643"`} {
		term := protocol.PredicateTermRequest{
			BaseColumn: "event_ts",
			Transform:  "identity",
			Value:      json.RawMessage(raw),
			ColType:    &colType,
		}
		expr, err := buildTermExpr(term)
		if err != nil {
			t.Fatalf("identity timestamp epoch %s: %v", raw, err)
		}
		if expr.String() == "" {
			t.Fatalf("expected non-empty expression for %s", raw)
		}
	}
}

func TestIdentityLiteralMatchesEpochDayString(t *testing.T) {
	actual := iceberg.Date(20643)
	if !identityLiteralMatches(actual, json.RawMessage(`20643`)) {
		t.Fatal("expected numeric epoch day to match Date")
	}
	if !identityLiteralMatches(actual, json.RawMessage(`"20643"`)) {
		t.Fatal("expected string epoch day to match Date")
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
