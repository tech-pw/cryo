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

package internal

import (
	"testing"
	"unicode/utf8"
)

func TestTruncateLowerBoundTextHindiMatchesSpark(t *testing.T) {
	const hindi = "नमस्ते दुनिया यह एक परीक्षण है और अधिक पाठ"
	got := TruncateLowerBoundText(hindi, 16)
	if !utf8.ValidString(got) {
		t.Fatalf("lower bound must be valid UTF-8; bytes=%x", []byte(got))
	}
	if n := utf8.RuneCountInString(got); n != 16 {
		t.Fatalf("expected 16 code points, got %d (%q)", n, got)
	}
	want := string([]rune(hindi)[:16])
	if got != want {
		t.Fatalf("got %q want %q", got, want)
	}
	// Old byte-slice bug: hindi[:16] is not valid UTF-8 (Spark decode failure).
	if utf8.ValidString(hindi[:16]) {
		t.Fatal("test setup: expected hindi[:16] to be invalid UTF-8")
	}
}

func TestTruncateLowerBoundTextShortString(t *testing.T) {
	got := TruncateLowerBoundText("हिंदी", 16)
	if got != "हिंदी" {
		t.Fatalf("short string must be unchanged: %q", got)
	}
}

func TestTruncateUpperBoundTextIncrementsLastCodePoint(t *testing.T) {
	// Spark/Java UnicodeUtil.truncateStringMax: truncate to 16 code points, then
	// setLength(offset of last) + append incremented code point → still 16 long.
	// "abcdefghijklmnopX" → "abcdefghijklmnop" → increment 'p' → "abcdefghijklmnoq"
	in := "abcdefghijklmnopX"
	got := TruncateUpperBoundText(in, 16)
	if got != "abcdefghijklmnoq" {
		t.Fatalf("got %q want abcdefghijklmnoq", got)
	}
	if !utf8.ValidString(got) {
		t.Fatalf("upper bound invalid UTF-8: %x", []byte(got))
	}
}

func TestTruncateUpperBoundTextSkipsSurrogateRange(t *testing.T) {
	// Java UnicodeUtil: code point U+D7FF increments to U+E000 (skip surrogates).
	in := "abcdefghijklmno" + string(rune(0xD7FF)) + "Z"
	got := TruncateUpperBoundText(in, 16)
	want := "abcdefghijklmno" + string(rune(0xE000))
	if got != want {
		t.Fatalf("got %q (%x) want %q (%x)", got, []byte(got), want, []byte(want))
	}
	if !utf8.ValidString(got) {
		t.Fatalf("upper bound must be valid UTF-8 after surrogate skip: %x", []byte(got))
	}
}

func TestTruncateUpperBoundTextHindi(t *testing.T) {
	const hindi = "नमस्ते दुनिया यह एक परीक्षण है और अधिक पाठ"
	got := TruncateUpperBoundText(hindi, 16)
	if got == "" {
		t.Fatal("expected non-empty upper bound")
	}
	if !utf8.ValidString(got) {
		t.Fatalf("Hindi upper bound invalid UTF-8: %x", []byte(got))
	}
	if utf8.RuneCountInString(got) > 16 {
		t.Fatalf("upper bound longer than truncate width: %d", utf8.RuneCountInString(got))
	}
	lower := TruncateLowerBoundText(hindi, 16)
	if got <= lower {
		t.Fatalf("truncated upper bound %q must be > lower %q", got, lower)
	}
}
