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
	"strings"
)

// Hive / Glue / JDBC catalogs (and client-side requirement validation in
// UpdateAndStageTable) often return optimistic-concurrency failures as a raw
// "requirement failed: branch \"main\" has changed: expected id …" string
// without wrapping ErrCommitFailed. REST catalogs wrap HTTP 409. Without the
// sentinel, Table.doCommit treats the conflict as terminal and rewrite_data_files
// never retries.
func IsRetryableCommitConflict(err error) bool {
	if err == nil {
		return false
	}
	if errors.Is(err, ErrCommitFailed) || errors.Is(err, ErrCommitDiverged) {
		return true
	}
	msg := err.Error()
	return strings.Contains(msg, "has changed: expected") ||
		strings.Contains(msg, "was created concurrently")
}

// WrapRetryableCommitConflict attaches ErrCommitFailed when err is an unwrapped
// optimistic-concurrency conflict so errors.Is and doCommit's retry loop agree.
func WrapRetryableCommitConflict(err error) error {
	if err == nil || errors.Is(err, ErrCommitFailed) {
		return err
	}
	if IsRetryableCommitConflict(err) {
		return fmt.Errorf("%w: %w", ErrCommitFailed, err)
	}
	return err
}
