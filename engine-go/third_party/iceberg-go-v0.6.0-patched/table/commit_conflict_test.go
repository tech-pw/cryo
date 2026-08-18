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

package table

import (
	"errors"
	"fmt"
	"testing"
)

func TestIsRetryableCommitConflictUnwrappedBranchChange(t *testing.T) {
	t.Parallel()

	// PDAE-8353 / live Hive rewrite_data_files: requirement validation fails
	// with this exact shape, no ErrCommitFailed wrap.
	err := errors.New(`requirement failed: branch "main" has changed: expected id 5740756594462960238, found 7046114876609983580`)
	if !IsRetryableCommitConflict(err) {
		t.Fatal("unwrapped branch-changed conflict must be retryable")
	}

	wrapped := WrapRetryableCommitConflict(err)
	if !errors.Is(wrapped, ErrCommitFailed) {
		t.Fatalf("wrap should attach ErrCommitFailed, got %v", wrapped)
	}
	if !errors.Is(wrapped, err) {
		t.Fatal("wrap should preserve the original error")
	}

	already := fmt.Errorf("%w: rest 409", ErrCommitFailed)
	if WrapRetryableCommitConflict(already) != already {
		t.Fatal("already-wrapped ErrCommitFailed should pass through")
	}

	other := errors.New("output file missing before commit")
	if IsRetryableCommitConflict(other) {
		t.Fatal("unrelated errors must not look like commit conflicts")
	}
	if WrapRetryableCommitConflict(other) != other {
		t.Fatal("unrelated errors should not be wrapped")
	}
}
