package main

import (
	"context"
	"fmt"
	"os"
	"strconv"
	"strings"
	"time"

	"github.com/cryo-lake/cryo/engine-go/internal/fixtures"
)

func main() {
	if len(os.Args) >= 2 {
		switch os.Args[1] {
		case "count-live-rows":
			runCountLiveRows(os.Args[2:])
			return
		case "live-int-column":
			runLiveIntColumn(os.Args[2:])
			return
		case "seed-table":
			runSeedTable(os.Args[2:])
			return
		}
	}
	runSeed()
}

func runSeed() {
	ctx, cancel := context.WithTimeout(context.Background(), 5*time.Minute)
	defer cancel()

	cfg := fixtures.ConfigFromEnv()
	results, err := fixtures.SeedMOR(ctx, cfg)
	if err != nil {
		fmt.Fprintf(os.Stderr, "seed MOR fixtures failed: %v\n", err)
		os.Exit(1)
	}
	for _, result := range results {
		fmt.Printf("%s.table_ref=%s\n", result.Name, result.TableRef)
		fmt.Printf("%s.location=%s\n", result.Name, result.Location)
	}
}

func runCountLiveRows(args []string) {
	if len(args) != 1 {
		fmt.Fprintf(os.Stderr, "usage: seed-native-fixtures count-live-rows <namespace.table>\n")
		os.Exit(2)
	}
	ctx, cancel := context.WithTimeout(context.Background(), 2*time.Minute)
	defer cancel()
	count, err := fixtures.CountLiveRows(ctx, fixtures.ConfigFromEnv(), args[0])
	if err != nil {
		fmt.Fprintf(os.Stderr, "count live rows failed: %v\n", err)
		os.Exit(1)
	}
	fmt.Println(count)
}

func runLiveIntColumn(args []string) {
	if len(args) != 2 {
		fmt.Fprintf(os.Stderr, "usage: seed-native-fixtures live-int-column <namespace.table> <column>\n")
		os.Exit(2)
	}
	ctx, cancel := context.WithTimeout(context.Background(), 2*time.Minute)
	defer cancel()
	values, err := fixtures.LiveIntColumnValues(ctx, fixtures.ConfigFromEnv(), args[0], args[1])
	if err != nil {
		fmt.Fprintf(os.Stderr, "live column read failed: %v\n", err)
		os.Exit(1)
	}
	parts := make([]string, len(values))
	for i, v := range values {
		parts[i] = strconv.FormatInt(v, 10)
	}
	fmt.Println(strings.Join(parts, ","))
}

func runSeedTable(args []string) {
	if len(args) != 1 {
		fmt.Fprintf(os.Stderr, "usage: seed-native-fixtures seed-table <table_name>\n")
		os.Exit(2)
	}
	ctx, cancel := context.WithTimeout(context.Background(), 2*time.Minute)
	defer cancel()
	if err := fixtures.SeedMORTableByName(ctx, fixtures.ConfigFromEnv(), args[0]); err != nil {
		fmt.Fprintf(os.Stderr, "seed table failed: %v\n", err)
		os.Exit(1)
	}
}
