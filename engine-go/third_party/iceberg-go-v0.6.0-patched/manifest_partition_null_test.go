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

package iceberg

import (
	"testing"

	"github.com/stretchr/testify/assert"
	"github.com/stretchr/testify/require"
	"github.com/twmb/avro/atype"
)

// Regression for PDAE-8279: a manifest entry whose Avro-encoded partition data has a nil
// value for a logical-typed field (e.g. a row whose partition-source column was itself
// null) previously panicked with "interface conversion: interface {} is nil, not int32"
// instead of decoding to "no value". Crashed the whole native maintenance subprocess in
// production (table hive.icesheet.silver_offline_drip_read_status_2, 2026-08-14) and took
// down every other job packed into the same batch dispatch pod with it.
func TestConvertAvroValueToIcebergType_NilValueDoesNotPanic(t *testing.T) {
	cases := []struct {
		name        string
		logicalType string
	}{
		{"date", atype.Date},
		{"time_millis", atype.TimeMillis},
		{"time_micros", atype.TimeMicros},
		{"timestamp_millis", atype.TimestampMillis},
		{"timestamp_micros", atype.TimestampMicros},
		{"timestamp_nanos", atype.TimestampNanos},
	}

	for _, tc := range cases {
		t.Run(tc.name, func(t *testing.T) {
			d := &dataFile{
				fieldIDToLogicalType: map[int]string{1: tc.logicalType},
			}

			require.NotPanics(t, func() {
				got := d.convertAvroValueToIcebergType(nil, 1)
				assert.Nil(t, got, "a nil Avro value must decode to nil (no value), not panic")
			})
		})
	}
}

// A non-nil value of the expected type must still decode correctly -- the nil check must
// not shadow the real conversion path for a genuinely-present value.
func TestConvertAvroValueToIcebergType_NonNilValueStillConverts(t *testing.T) {
	d := &dataFile{
		fieldIDToLogicalType: map[int]string{1: atype.Date},
	}

	got := d.convertAvroValueToIcebergType(int32(42), 1)
	assert.Equal(t, Date(42), got)
}

// End-to-end through Partition(): the real crash site (table/scanner.go's
// GetPartitionRecord -> Partition() -> initializeMapData() -> convertAvroValueToIcebergType)
// must not panic when a manifest's partition data map holds an explicit nil for a
// logical-typed field, and the resulting partition map must carry nil for that field id
// rather than silently dropping it or fabricating a zero value.
func TestPartition_NilPartitionFieldValueDoesNotPanic(t *testing.T) {
	d := &dataFile{
		PartitionData:        map[string]any{"event_date_day": nil},
		fieldNameToID:        map[string]int{"event_date_day": 1000},
		fieldIDToLogicalType: map[int]string{1000: atype.Date},
	}

	var partition map[int]any
	require.NotPanics(t, func() {
		partition = d.Partition()
	})

	value, ok := partition[1000]
	require.True(t, ok, "the field id must still be present in the partition map")
	assert.Nil(t, value)
}
