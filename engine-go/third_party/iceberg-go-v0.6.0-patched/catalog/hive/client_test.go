// Licensed to the Apache Software Foundation (ASF) under one
// or more contributor license agreements.  See the NOTICE file
// distributed with this work for additional information
// regarding copyright ownership.  The ASF licenses this file
// to you under the Apache License, Version 2.0 (the
// "License"); you may not use this file except in compliance
// with the License.  You may obtain a copy of the License at
//
//   http://www.apache.org/licenses/LICENSE-2.0
//
// Unless required by applicable law or agreed to in writing,
// software distributed under the License is distributed on an
// "AS IS" BASIS, WITHOUT WARRANTIES OR CONDITIONS OF ANY
// KIND, either express or implied.  See the License for the
// specific language governing permissions and limitations
// under the License.

package hive

import (
	"errors"
	"testing"
	"time"
)

// Regression tests for connectWithTimeout, added 2026-08-20 after a live incident:
// gohive.ConnectToMetastore has no timeout of its own (thrift.NewTSocket(addr) with no deadline),
// so a single connect attempt against a stale/black-holed metastore IP could block for the
// attempt's entire lifetime -- confirmed live at ~27.7 minutes per attempt, turning a batch that
// normally finishes in ~11 minutes into one still running (idle, 1m CPU) two hours into a 4h
// activeDeadlineSeconds kill. See hiveConnectTimeout's doc comment in client.go for the full
// incident writeup.

func TestConnectWithTimeout_ReturnsFastSuccessImmediately(t *testing.T) {
	start := time.Now()
	got, err := connectWithTimeout(func() (string, error) {
		return "connected", nil
	}, 100*time.Millisecond)
	elapsed := time.Since(start)

	if err != nil {
		t.Fatalf("expected no error, got %v", err)
	}
	if got != "connected" {
		t.Fatalf("expected %q, got %q", "connected", got)
	}
	if elapsed >= 100*time.Millisecond {
		t.Fatalf("expected to return well before the timeout, took %s", elapsed)
	}
}

func TestConnectWithTimeout_ReturnsFastErrorImmediately(t *testing.T) {
	wantErr := errors.New("connection refused")
	start := time.Now()
	_, err := connectWithTimeout(func() (string, error) {
		return "", wantErr
	}, 100*time.Millisecond)
	elapsed := time.Since(start)

	if !errors.Is(err, wantErr) {
		t.Fatalf("expected %v, got %v", wantErr, err)
	}
	if elapsed >= 100*time.Millisecond {
		t.Fatalf("expected to return well before the timeout, took %s", elapsed)
	}
}

func TestConnectWithTimeout_TimesOutInsteadOfBlockingForever(t *testing.T) {
	// Simulates the exact live failure: a connect call that blocks indefinitely (the
	// black-holed-socket case gohive.ConnectToMetastore has no protection against on its own).
	blocked := make(chan struct{})
	defer close(blocked)

	start := time.Now()
	_, err := connectWithTimeout(func() (string, error) {
		<-blocked // never fires within the test's lifetime -- simulates a hung dial
		return "connected", nil
	}, 50*time.Millisecond)
	elapsed := time.Since(start)

	if err == nil {
		t.Fatal("expected a timeout error, got nil")
	}
	// Bounded, not exact -- CI scheduling jitter is real, but this must never approach the
	// live incident's ~27 minutes, or even a lenient multi-second margin of the configured
	// timeout, or the fix has regressed back to unbounded blocking.
	if elapsed < 50*time.Millisecond || elapsed > 2*time.Second {
		t.Fatalf("expected elapsed time close to the 50ms timeout, got %s", elapsed)
	}
}

func TestConnectWithTimeout_SlowSuccessStillTimesOut(t *testing.T) {
	// A connect that WOULD have succeeded, just too slowly -- must still time out rather than
	// waiting for it, since a hung metastore connection often does eventually resolve (success
	// or failure) well past any duration worth waiting for inside a retry loop.
	slow := make(chan struct{})
	go func() {
		time.Sleep(200 * time.Millisecond)
		close(slow)
	}()

	_, err := connectWithTimeout(func() (string, error) {
		<-slow
		return "connected", nil
	}, 20*time.Millisecond)

	if err == nil {
		t.Fatal("expected a timeout error even though the underlying connect would have succeeded")
	}
}
