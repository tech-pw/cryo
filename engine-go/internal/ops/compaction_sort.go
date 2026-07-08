package ops

import (
	"encoding/json"
	"fmt"
	"strings"

	"github.com/apache/iceberg-go"
	"github.com/apache/iceberg-go/table"
)

const (
	zorderUnavailableMsg = "z-order compaction is not supported in cryo-engine-go; route OPTIMIZE to Trino/Spark"
	sortOrderRequiredMsg = "native sort compaction requires a non-unsorted table sort order " +
		"(set write.sort-order on the table) or route OPTIMIZE with sort_columns to Trino/Spark"
)

func validateCompactionSortMode(tbl *table.Table, raw json.RawMessage) error {
	if len(zorderColumns(raw)) > 0 || strategyParam(raw) == "zorder" {
		return fmt.Errorf("%s", zorderUnavailableMsg)
	}
	if !sortCompactionRequested(raw) {
		return nil
	}
	sortOrder := tbl.SortOrder()
	if sortOrder.IsUnsorted() {
		return fmt.Errorf("%s", sortOrderRequiredMsg)
	}
	requested := sortColumns(raw)
	if len(requested) == 0 {
		return nil
	}
	if err := assertSortColumnsMatchTableOrder(tbl.Schema(), sortOrder, requested, sortDirections(raw)); err != nil {
		return err
	}
	return nil
}

func sortCompactionRequested(raw json.RawMessage) bool {
	switch strategyParam(raw) {
	case "sort":
		return true
	default:
		return len(sortColumns(raw)) > 0
	}
}

func strategyParam(raw json.RawMessage) string {
	return strings.ToLower(strings.TrimSpace(stringParam(raw, "strategy", "")))
}

func sortColumns(raw json.RawMessage) []string {
	return stringSliceParam(raw, "sort_columns")
}

func zorderColumns(raw json.RawMessage) []string {
	return stringSliceParam(raw, "zorder_columns")
}

func sortDirections(raw json.RawMessage) []string {
	directions := stringSliceParam(raw, "sort_directions")
	if len(directions) == 0 {
		return nil
	}
	return directions
}

func assertSortColumnsMatchTableOrder(
	schema *iceberg.Schema,
	sortOrder table.SortOrder,
	columns []string,
	directions []string,
) error {
	if sortOrder.IsUnsorted() {
		return fmt.Errorf("%s", sortOrderRequiredMsg)
	}
	tableFields := sortOrderFieldNames(schema, sortOrder)
	if len(tableFields) == 0 {
		return fmt.Errorf("%s", sortOrderRequiredMsg)
	}
	if len(columns) != len(tableFields) {
		return fmt.Errorf(
			"sort_columns %v do not match table sort order %v; route to Trino/Spark or align columns",
			columns, tableFields,
		)
	}
	for i, col := range columns {
		if !strings.EqualFold(strings.TrimSpace(col), tableFields[i]) {
			return fmt.Errorf(
				"sort_columns %v do not match table sort order %v; route to Trino/Spark or align columns",
				columns, tableFields,
			)
		}
		if len(directions) > i {
			if !sortDirectionMatches(sortOrder, i, directions[i]) {
				return fmt.Errorf(
					"sort_directions for %q do not match table sort order; route to Trino/Spark",
					col,
				)
			}
		}
	}
	return nil
}

func sortOrderFieldNames(schema *iceberg.Schema, sortOrder table.SortOrder) []string {
	names := make([]string, 0, sortOrder.Len())
	for _, field := range sortOrder.Fields() {
		col, ok := schema.FindFieldByID(field.SourceID())
		if !ok {
			continue
		}
		names = append(names, col.Name)
	}
	return names
}

func sortDirectionMatches(sortOrder table.SortOrder, index int, direction string) bool {
	want := strings.ToLower(strings.TrimSpace(direction))
	if want == "" {
		return true
	}
	var got table.SortDirection
	pos := 0
	for _, field := range sortOrder.Fields() {
		if pos == index {
			got = field.Direction
			break
		}
		pos++
	}
	switch want {
	case "asc", "ascending":
		return got == table.SortASC
	case "desc", "descending":
		return got == table.SortDESC
	default:
		return false
	}
}

func stringParam(raw json.RawMessage, key, defaultValue string) string {
	if len(raw) == 0 {
		return defaultValue
	}
	var params map[string]json.RawMessage
	if err := json.Unmarshal(raw, &params); err != nil {
		return defaultValue
	}
	value, ok := params[key]
	if !ok {
		return defaultValue
	}
	var asString string
	if err := json.Unmarshal(value, &asString); err == nil {
		return asString
	}
	return defaultValue
}

func stringSliceParam(raw json.RawMessage, key string) []string {
	if len(raw) == 0 {
		return nil
	}
	var params map[string]json.RawMessage
	if err := json.Unmarshal(raw, &params); err != nil {
		return nil
	}
	value, ok := params[key]
	if !ok {
		return nil
	}
	var items []string
	if err := json.Unmarshal(value, &items); err == nil {
		return items
	}
	return nil
}
