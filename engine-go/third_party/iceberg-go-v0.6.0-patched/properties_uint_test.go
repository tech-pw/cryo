package iceberg

import "testing"

// PropUInt narrows a uint64 parse to uint, so an out-of-range value must come
// back as the default rather than a wrapped result.
func TestPropUInt(t *testing.T) {
	const def uint = 7

	cases := []struct {
		name string
		raw  string
		want uint
	}{
		{"fits", "42", 42},
		{"zero", "0", 0},
		{"largest accepted", "4294967295", 4294967295},
		{"too large for uint", "4294967296", def},
		{"max uint64", "18446744073709551615", def},
		{"negative", "-1", def},
		{"not a number", "many", def},
		{"empty", "", def},
	}
	for _, tc := range cases {
		t.Run(tc.name, func(t *testing.T) {
			got := PropUInt(Properties{"k": tc.raw}, "k", def)
			if got != tc.want {
				t.Errorf("PropUInt(%q) = %d, want %d", tc.raw, got, tc.want)
			}
		})
	}

	if got := PropUInt(Properties{}, "missing", def); got != def {
		t.Errorf("missing key = %d, want default %d", got, def)
	}
}
