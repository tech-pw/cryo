package testhook

import (
	"os"
	"strconv"
	"time"
)

// PauseBeforeCommit sleeps when CRYO_TEST_PAUSE_BEFORE_COMMIT is a positive integer
// (seconds). Used only by integration tests to interleave concurrent writers.
func PauseBeforeCommit() {
	raw := os.Getenv("CRYO_TEST_PAUSE_BEFORE_COMMIT")
	if raw == "" {
		return
	}
	seconds, err := strconv.Atoi(raw)
	if err != nil || seconds <= 0 {
		return
	}
	time.Sleep(time.Duration(seconds) * time.Second)
}
