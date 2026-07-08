package ops

import "strings"

func isExecuteMode(mode string) bool {
	switch strings.ToLower(strings.TrimSpace(mode)) {
	case "dry_run", "dry-run", "analyze":
		return false
	default:
		return true
	}
}
