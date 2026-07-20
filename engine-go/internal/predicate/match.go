package predicate

import (
	"encoding/json"
	"fmt"
	"strings"

	"github.com/apache/iceberg-go"
	"github.com/apache/iceberg-go/table"

	"github.com/cryo-lake/cryo/engine-go/internal/protocol"
)

// MatchesAnyDataFile returns true when the data file matches any OR-branch predicate.
// Sentinel-only "_unpartitioned" predicates match every file (full-table scope).
func MatchesAnyDataFile(tbl *table.Table, file iceberg.DataFile, predicates []protocol.PredicateRequest) (bool, error) {
	if !HasPredicates(&protocol.OperationRequest{Predicates: predicates}) {
		return true, nil
	}
	for _, predicate := range predicates {
		if len(predicate.Terms) == 0 {
			continue
		}
		match, err := matchesPredicate(tbl, file, predicate)
		if err != nil {
			return false, err
		}
		if match {
			return true, nil
		}
	}
	return false, nil
}

func matchesPredicate(tbl *table.Table, file iceberg.DataFile, predicate protocol.PredicateRequest) (bool, error) {
	for _, term := range predicate.Terms {
		if isUnpartitionedSentinel(term.BaseColumn) {
			continue
		}
		match, err := matchesTerm(tbl, file, term)
		if err != nil {
			return false, err
		}
		if !match {
			return false, nil
		}
	}
	// Empty after stripping sentinels → full-table match for this AND-branch.
	return true, nil
}

func matchesTerm(tbl *table.Table, file iceberg.DataFile, term protocol.PredicateTermRequest) (bool, error) {
	if isUnpartitionedSentinel(term.BaseColumn) {
		return true, nil
	}
	spec := tbl.Metadata().PartitionSpecByID(int(file.SpecID()))
	if spec == nil {
		return false, fmt.Errorf("partition spec %d not found", file.SpecID())
	}

	field, ok := findPartitionField(spec, term)
	if !ok {
		return false, fmt.Errorf("no partition field for predicate term %s", term.BaseColumn)
	}

	value, ok := file.Partition()[field.FieldID]
	if len(term.Value) == 0 || string(term.Value) == "null" {
		return !ok || value == nil, nil
	}
	if !ok || value == nil {
		return false, fmt.Errorf("partition value missing for predicate field %s", term.BaseColumn)
	}
	return literalMatchesTerm(value, term), nil
}

func findPartitionField(spec *iceberg.PartitionSpec, term protocol.PredicateTermRequest) (iceberg.PartitionField, bool) {
	candidates := candidateFieldNames(term)
	for _, field := range spec.Fields() {
		for _, name := range candidates {
			if field.Name == name {
				return field, true
			}
		}
	}
	for _, field := range spec.Fields() {
		if fieldMatchesTerm(field, term) {
			return field, true
		}
	}
	return iceberg.PartitionField{}, false
}

func fieldMatchesTerm(field iceberg.PartitionField, term protocol.PredicateTermRequest) bool {
	transformName := strings.ToLower(field.Transform.String())
	switch term.Transform {
	case "identity":
		return field.Name == term.BaseColumn || transformName == "identity"
	case "day":
		return transformName == "day"
	case "month":
		return transformName == "month"
	case "year":
		return transformName == "year"
	default:
		return field.Name == term.BaseColumn
	}
}

func candidateFieldNames(term protocol.PredicateTermRequest) []string {
	names := make([]string, 0, 3)
	if term.PartitionField != nil && *term.PartitionField != "" {
		names = append(names, *term.PartitionField)
	}
	base := term.BaseColumn
	switch term.Transform {
	case "identity":
		names = append(names, base)
	case "day":
		names = append(names, base+"_day", base)
	case "month":
		names = append(names, base+"_month", base)
	case "year":
		names = append(names, base+"_year", base)
	default:
		names = append(names, base+"_"+term.Transform, base)
	}
	return names
}

func literalMatchesTerm(actual any, term protocol.PredicateTermRequest) bool {
	switch term.Transform {
	case "identity":
		return identityLiteralMatches(actual, term.Value)
	case "day", "month", "year":
		return numericLiteralMatches(actual, term.Value)
	default:
		return false
	}
}

func identityLiteralMatches(actual any, expected json.RawMessage) bool {
	var expectedString string
	if err := json.Unmarshal(expected, &expectedString); err == nil {
		if actualString, ok := actual.(string); ok {
			return actualString == expectedString
		}
		// Digit strings are Iceberg epoch-days for identity date partitions.
		if n, ok := jsonAsInt64(json.RawMessage(expectedString)); ok {
			switch v := actual.(type) {
			case int32:
				return int64(v) == n
			case int64:
				return v == n
			case iceberg.Date:
				return int64(v) == n
			}
		}
	}
	if expectedNum, ok := jsonAsInt64(expected); ok {
		switch v := actual.(type) {
		case int32:
			return int64(v) == expectedNum
		case int64:
			return v == expectedNum
		case iceberg.Date:
			return int64(v) == expectedNum
		case bool:
			var expectedBool bool
			if err := json.Unmarshal(expected, &expectedBool); err == nil {
				return v == expectedBool
			}
		}
	}
	return false
}

func numericLiteralMatches(actual any, expected json.RawMessage) bool {
	expectedNum, ok := jsonAsInt64(expected)
	if !ok {
		var asString string
		if err := json.Unmarshal(expected, &asString); err != nil {
			return false
		}
		expectedNum, ok = jsonAsInt64(json.RawMessage(asString))
		if !ok {
			return false
		}
	}
	switch v := actual.(type) {
	case int32:
		return int64(v) == expectedNum
	case int64:
		return v == expectedNum
	case iceberg.Date:
		return int64(v) == expectedNum
	default:
		return false
	}
}
