package predicate

import (
	"encoding/json"
	"fmt"
	"math"
	"strings"

	"github.com/apache/iceberg-go"

	"github.com/cryo-lake/cryo/engine-go/internal/protocol"
)

// HasPredicates reports whether the operation carries real (bindable) predicate
// branches. Display-only "_unpartitioned" terms are ignored — they mean full-table.
func HasPredicates(op *protocol.OperationRequest) bool {
	for _, predicate := range op.Predicates {
		for _, term := range predicate.Terms {
			if !isUnpartitionedSentinel(term.BaseColumn) {
				return true
			}
		}
	}
	return false
}

// UnsupportedReason returns a human-readable reason when execute-mode compaction
// cannot honor the planner predicates.
func UnsupportedReason(op *protocol.OperationRequest) string {
	for _, predicate := range op.Predicates {
		for _, term := range predicate.Terms {
			if reason := termUnsupportedReason(term); reason != "" {
				return reason
			}
		}
	}
	return ""
}

func termUnsupportedReason(term protocol.PredicateTermRequest) string {
	switch term.Transform {
	case "bucket", "truncate", "unsupported":
		return fmt.Sprintf(
			"rewrite_data_files does not support partition transform '%s' on column '%s'",
			term.Transform, term.BaseColumn,
		)
	case "identity", "day", "month", "year":
		return ""
	default:
		return fmt.Sprintf("rewrite_data_files does not support partition transform '%s'", term.Transform)
	}
}

// unpartitionedSentinel is the scanner display label for tables with an empty
// partition spec. It is not a real schema column; binders panic on it
// ("could not bind reference '_unpartitioned'"). Treat as full-table scope.
const unpartitionedSentinel = "_unpartitioned"

func isUnpartitionedSentinel(name string) bool {
	trimmed := strings.TrimSpace(name)
	return trimmed == "" || strings.EqualFold(trimmed, unpartitionedSentinel)
}

// BuildRowFilter translates Cryo OR-of-AND partition predicates into an iceberg row filter.
// Returns an unbound expression; iceberg-go's Scan binds it internally.
// Predicates that only reference the unpartitioned sentinel become AlwaysTrue (full table).
func BuildRowFilter(_ *iceberg.Schema, predicates []protocol.PredicateRequest) (iceberg.BooleanExpression, error) {
	if len(predicates) == 0 {
		return iceberg.AlwaysTrue{}, nil
	}

	branches := make([]iceberg.BooleanExpression, 0, len(predicates))
	for _, predicate := range predicates {
		if len(predicate.Terms) == 0 {
			continue
		}
		branch, err := buildAndBranch(predicate)
		if err != nil {
			return nil, err
		}
		if branch == nil {
			// Sentinel-only branch: full-table scope for this OR arm.
			return iceberg.AlwaysTrue{}, nil
		}
		branches = append(branches, branch)
	}
	if len(branches) == 0 {
		return iceberg.AlwaysTrue{}, nil
	}
	if len(branches) == 1 {
		return branches[0], nil
	}
	return iceberg.NewOr(branches[0], branches[1], branches[2:]...), nil
}

func buildAndBranch(predicate protocol.PredicateRequest) (iceberg.BooleanExpression, error) {
	terms := make([]iceberg.BooleanExpression, 0, len(predicate.Terms))
	for _, term := range predicate.Terms {
		if isUnpartitionedSentinel(term.BaseColumn) {
			// Skip display-only sentinel; remaining real terms still constrain the row.
			continue
		}
		expr, err := buildTermExpr(term)
		if err != nil {
			return nil, err
		}
		terms = append(terms, expr)
	}
	if len(terms) == 0 {
		// All terms were the unpartitioned sentinel → no row filter.
		return nil, nil
	}
	if len(terms) == 1 {
		return terms[0], nil
	}
	return iceberg.NewAnd(terms[0], terms[1], terms[2:]...), nil
}

func buildTermExpr(term protocol.PredicateTermRequest) (iceberg.BooleanExpression, error) {
	if isUnpartitionedSentinel(term.BaseColumn) {
		return iceberg.AlwaysTrue{}, nil
	}
	ref := iceberg.Reference(term.BaseColumn)
	if len(term.Value) == 0 || string(term.Value) == "null" {
		if term.Transform == "identity" {
			return iceberg.IsNull(ref), nil
		}
		return nil, fmt.Errorf("null partition value unsupported for transform %s", term.Transform)
	}

	colType := ""
	if term.ColType != nil {
		colType = *term.ColType
	}

	switch term.Transform {
	case "identity":
		return buildIdentityExpr(ref, term.Value, colType)
	case "day", "month", "year":
		return buildRangeExpr(ref, term.Transform, term.Value, colType)
	default:
		return nil, fmt.Errorf("unsupported transform %s", term.Transform)
	}
}

func buildIdentityExpr(ref iceberg.Reference, value json.RawMessage, colType string) (iceberg.BooleanExpression, error) {
	lowered := strings.ToLower(colType)
	if strings.Contains(lowered, "bool") {
		var asBool bool
		if err := json.Unmarshal(value, &asBool); err != nil {
			return nil, err
		}
		return iceberg.EqualTo(ref, asBool), nil
	}
	// Iceberg date/timestamp identity partitions store epoch-day (or ISO) values.
	// Convert via dayStart so digit strings like "20643" become Date/Timestamp literals.
	if lowered == "date" || strings.HasPrefix(lowered, "date") || strings.Contains(lowered, "timestamp") {
		start, err := dayStart(value)
		if err != nil {
			return nil, err
		}
		switch lit := temporalLiteral(start, colType).(type) {
		case iceberg.Date:
			return iceberg.EqualTo(ref, lit), nil
		case iceberg.Timestamp:
			return iceberg.EqualTo(ref, lit), nil
		default:
			return nil, fmt.Errorf("unsupported identity temporal literal type %T", lit)
		}
	}
	if isNumericType(lowered) {
		if n, ok := jsonAsInt64(value); ok {
			if strings.Contains(lowered, "long") {
				return iceberg.EqualTo(ref, n), nil
			}
			if n < math.MinInt32 || n > math.MaxInt32 {
				return nil, fmt.Errorf("identity predicate value %d overflows column type %s", n, colType)
			}
			return iceberg.EqualTo(ref, int32(n)), nil
		}
	}
	var asString string
	if err := json.Unmarshal(value, &asString); err == nil {
		return iceberg.EqualTo(ref, asString), nil
	}
	if n, ok := jsonAsInt64(value); ok {
		return iceberg.EqualTo(ref, n), nil
	}
	return nil, fmt.Errorf("unsupported identity predicate value for column type %s", colType)
}

func buildRangeExpr(ref iceberg.Reference, transform string, value json.RawMessage, colType string) (iceberg.BooleanExpression, error) {
	var start, end any
	switch transform {
	case "day":
		startTime, startErr := dayStart(value)
		if startErr != nil {
			return nil, startErr
		}
		start = temporalLiteral(startTime, colType)
		end = temporalLiteral(startTime.AddDate(0, 0, 1), colType)
	case "month":
		startTime, startErr := monthStart(value)
		if startErr != nil {
			return nil, startErr
		}
		start = temporalLiteral(startTime, colType)
		end = temporalLiteral(addMonth(startTime), colType)
	case "year":
		startTime, startErr := yearStart(value)
		if startErr != nil {
			return nil, startErr
		}
		start = temporalLiteral(startTime, colType)
		end = temporalLiteral(startTime.AddDate(1, 0, 0), colType)
	default:
		return nil, fmt.Errorf("unsupported transform %s", transform)
	}
	return rangeExpr(ref, start, end)
}

func rangeExpr(ref iceberg.Reference, start, end any) (iceberg.BooleanExpression, error) {
	switch startVal := start.(type) {
	case iceberg.Date:
		endVal := end.(iceberg.Date)
		return iceberg.NewAnd(
			iceberg.GreaterThanEqual(ref, startVal),
			iceberg.LessThan(ref, endVal),
		), nil
	case iceberg.Timestamp:
		endVal := end.(iceberg.Timestamp)
		return iceberg.NewAnd(
			iceberg.GreaterThanEqual(ref, startVal),
			iceberg.LessThan(ref, endVal),
		), nil
	default:
		return nil, fmt.Errorf("unsupported temporal literal type %T", start)
	}
}

func isNumericType(colType string) bool {
	return strings.Contains(colType, "int") ||
		strings.Contains(colType, "long") ||
		strings.Contains(colType, "double") ||
		strings.Contains(colType, "float") ||
		strings.Contains(colType, "decimal") ||
		strings.Contains(colType, "numeric")
}
