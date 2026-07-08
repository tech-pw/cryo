package predicate

import (
	"encoding/json"
	"fmt"
	"strings"
	"time"

	"github.com/apache/iceberg-go"
)

func dayStart(value json.RawMessage) (time.Time, error) {
	if len(value) == 0 || string(value) == "null" {
		return time.Time{}, fmt.Errorf("missing day partition value")
	}
	if n, ok := jsonAsInt64(value); ok {
		return time.Date(1970, 1, 1, 0, 0, 0, 0, time.UTC).AddDate(0, 0, int(n)), nil
	}
	var text string
	if err := json.Unmarshal(value, &text); err == nil {
		if n, ok := jsonAsInt64(json.RawMessage(text)); ok {
			return time.Date(1970, 1, 1, 0, 0, 0, 0, time.UTC).AddDate(0, 0, int(n)), nil
		}
		parsed, err := time.Parse("2006-01-02", strings.TrimSpace(text)[:min(len(strings.TrimSpace(text)), 10)])
		if err != nil {
			return time.Time{}, err
		}
		return parsed, nil
	}
	return time.Time{}, fmt.Errorf("invalid day partition value")
}

func monthStart(value json.RawMessage) (time.Time, error) {
	if len(value) == 0 || string(value) == "null" {
		return time.Time{}, fmt.Errorf("missing month partition value")
	}
	if n, ok := jsonAsInt64(value); ok {
		years := int(n) / 12
		month := int(n)%12 + 1
		return time.Date(1970+years, time.Month(month), 1, 0, 0, 0, 0, time.UTC), nil
	}
	var text string
	if err := json.Unmarshal(value, &text); err == nil {
		trimmed := strings.TrimSpace(text)
		if n, ok := jsonAsInt64(json.RawMessage(trimmed)); ok && len(trimmed) < 4 {
			years := int(n) / 12
			month := int(n)%12 + 1
			return time.Date(1970+years, time.Month(month), 1, 0, 0, 0, 0, time.UTC), nil
		}
		if len(trimmed) == 7 {
			trimmed += "-01"
		}
		parsed, err := time.Parse("2006-01-02", trimmed[:10])
		if err != nil {
			return time.Time{}, err
		}
		return time.Date(parsed.Year(), parsed.Month(), 1, 0, 0, 0, 0, time.UTC), nil
	}
	return time.Time{}, fmt.Errorf("invalid month partition value")
}

func yearStart(value json.RawMessage) (time.Time, error) {
	if len(value) == 0 || string(value) == "null" {
		return time.Time{}, fmt.Errorf("missing year partition value")
	}
	if n, ok := jsonAsInt64(value); ok {
		return time.Date(1970+int(n), 1, 1, 0, 0, 0, 0, time.UTC), nil
	}
	var text string
	if err := json.Unmarshal(value, &text); err == nil {
		trimmed := strings.TrimSpace(text)
		if n, ok := jsonAsInt64(json.RawMessage(trimmed)); ok && len(trimmed) < 4 {
			return time.Date(1970+int(n), 1, 1, 0, 0, 0, 0, time.UTC), nil
		}
		if len(trimmed) >= 4 {
			return time.Date(parseYear(trimmed), 1, 1, 0, 0, 0, 0, time.UTC), nil
		}
	}
	return time.Time{}, fmt.Errorf("invalid year partition value")
}

func addMonth(value time.Time) time.Time {
	if value.Month() == time.December {
		return time.Date(value.Year()+1, time.January, 1, 0, 0, 0, 0, time.UTC)
	}
	return time.Date(value.Year(), value.Month()+1, 1, 0, 0, 0, 0, time.UTC)
}

func parseYear(text string) int {
	var year int
	fmt.Sscanf(text[:4], "%d", &year)
	return year
}

func jsonAsInt64(value json.RawMessage) (int64, bool) {
	var asInt int64
	if err := json.Unmarshal(value, &asInt); err == nil {
		return asInt, true
	}
	var asFloat float64
	if err := json.Unmarshal(value, &asFloat); err == nil {
		return int64(asFloat), true
	}
	return 0, false
}

func temporalLiteral(start time.Time, colType string) any {
	if strings.Contains(strings.ToLower(colType), "timestamp") {
		return iceberg.Timestamp(start.UnixMicro())
	}
	return iceberg.Date(int32(start.Sub(time.Date(1970, 1, 1, 0, 0, 0, 0, time.UTC)).Hours() / 24))
}

func min(a, b int) int {
	if a < b {
		return a
	}
	return b
}
