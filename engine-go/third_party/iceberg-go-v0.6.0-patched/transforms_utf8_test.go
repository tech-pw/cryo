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

package iceberg_test

import (
	"testing"
	"unicode/utf8"

	"github.com/apache/iceberg-go"
	"github.com/stretchr/testify/assert"
	"github.com/stretchr/testify/require"
)

// Regression: byte-sliced truncate(16) on Hindi (and other multi-byte UTF-8)
// produced invalid lower_bounds that Spark failed to decode when reading $files.
func TestTruncateStringDoesNotSplitUTF8(t *testing.T) {
	transform := iceberg.TruncateTransform{Width: 1}

	out := transform.Apply(iceberg.Optional[iceberg.Literal]{
		Valid: true,
		Val:   iceberg.StringLiteral("éx"),
	})
	require.True(t, out.Valid)
	got := string(out.Val.(iceberg.StringLiteral))
	require.True(t, utf8.ValidString(got), "truncate produced invalid UTF-8: %q", got)
	assert.Equal(t, "é", got)

	fn, err := transform.Transformer(iceberg.PrimitiveTypes.String)
	require.NoError(t, err)
	transformed := fn("éx").(string)
	require.True(t, utf8.ValidString(transformed), "transformer produced invalid UTF-8: %q", transformed)
	assert.Equal(t, "é", transformed)

	// Binary truncate remains byte-based (Iceberg spec).
	binary := transform.Apply(iceberg.Optional[iceberg.Literal]{
		Valid: true,
		Val:   iceberg.BinaryLiteral([]byte("éx")),
	})
	require.True(t, binary.Valid)
	assert.Equal(t, iceberg.BinaryLiteral([]byte{0xc3}), binary.Val)
}

func TestTruncateStringHindiMetricsWidth16(t *testing.T) {
	// Mirrors Spark write.metadata.metrics.default=truncate(16) on Hindi text.
	const hindi = "नमस्ते दुनिया यह एक परीक्षण है और अधिक पाठ"
	require.Greater(t, utf8.RuneCountInString(hindi), 16)
	require.Greater(t, len(hindi), 16) // multi-byte: byte len >> code-point len

	transform := iceberg.TruncateTransform{Width: 16}
	out := transform.Apply(iceberg.Optional[iceberg.Literal]{
		Valid: true,
		Val:   iceberg.StringLiteral(hindi),
	})
	require.True(t, out.Valid)
	got := string(out.Val.(iceberg.StringLiteral))

	require.True(t, utf8.ValidString(got), "truncate(16) must stay valid UTF-8; bytes=%x", []byte(got))
	assert.Equal(t, 16, utf8.RuneCountInString(got))
	assert.Equal(t, string([]rune(hindi)[:16]), got)

	// Contrasts the old bug: first 16 bytes of Hindi are not valid UTF-8.
	invalid := hindi[:16]
	require.False(t, utf8.ValidString(invalid))
}
