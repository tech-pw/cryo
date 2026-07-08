package fixtures

import (
	"context"
	"fmt"

	"github.com/apache/iceberg-go/table"
)

// Result describes a seeded fixture table.
type Result struct {
	Name     string
	TableRef string
	Location string
}

// SeedMOR creates MOR fixture tables on the REST catalog (position/equality/mixed/partial).
func SeedMOR(ctx context.Context, cfg Config) ([]Result, error) {
	cat, err := connectCatalog(ctx, cfg)
	if err != nil {
		return nil, err
	}
	if err := ensureNamespace(ctx, cat); err != nil {
		return nil, err
	}

	var results []Result
	seeders := []struct {
		name string
		fn   func(context.Context, Config) (*Result, error)
	}{
		{MORPosTable, seedMORPos},
		{MOREqTable, seedMOREq},
		{MORMixedTable, seedMORMixed},
		{MORPartialTable, seedMORPartial},
	}
	for _, seeder := range seeders {
		result, err := seeder.fn(ctx, cfg)
		if err != nil {
			return nil, fmt.Errorf("seed %s: %w", seeder.name, err)
		}
		results = append(results, *result)
	}
	return results, nil
}

// SeedMORTableByName recreates a single MOR fixture table by name.
func SeedMORTableByName(ctx context.Context, cfg Config, name string) error {
	seeders := map[string]func(context.Context, Config) (*Result, error){
		MORPosTable:     seedMORPos,
		MOREqTable:      seedMOREq,
		MORMixedTable:   seedMORMixed,
		MORPartialTable: seedMORPartial,
	}
	seeder, ok := seeders[name]
	if !ok {
		return fmt.Errorf("unknown MOR fixture table %q", name)
	}
	cat, err := connectCatalog(ctx, cfg)
	if err != nil {
		return err
	}
	if err := ensureNamespace(ctx, cat); err != nil {
		return err
	}
	if _, err := seeder(ctx, cfg); err != nil {
		return err
	}
	return nil
}

func seedMORPos(ctx context.Context, cfg Config) (*Result, error) {
	cat, err := connectCatalog(ctx, cfg)
	if err != nil {
		return nil, err
	}
	tbl, err := recreateTable(ctx, cat, MORPosTable)
	if err != nil {
		return nil, err
	}
	tbl, idToPath, err := appendManySmallFiles(ctx, tbl, 10)
	if err != nil {
		return nil, err
	}
	path, ok := idToPath[0]
	if !ok {
		return nil, fmt.Errorf("expected data file for id=0")
	}
	tbl, err = commitPositionDeletes(ctx, tbl, path, []int64{0})
	if err != nil {
		return nil, err
	}
	return fixtureResult(MORPosTable, tbl)
}

func seedMOREq(ctx context.Context, cfg Config) (*Result, error) {
	cat, err := connectCatalog(ctx, cfg)
	if err != nil {
		return nil, err
	}
	tbl, err := recreateTable(ctx, cat, MOREqTable)
	if err != nil {
		return nil, err
	}
	tbl, _, err = appendManySmallFiles(ctx, tbl, 10)
	if err != nil {
		return nil, err
	}
	tbl, err = commitEqualityDelete(ctx, tbl, []int64{2})
	if err != nil {
		return nil, err
	}
	return fixtureResult(MOREqTable, tbl)
}

func seedMORMixed(ctx context.Context, cfg Config) (*Result, error) {
	cat, err := connectCatalog(ctx, cfg)
	if err != nil {
		return nil, err
	}
	tbl, err := recreateTable(ctx, cat, MORMixedTable)
	if err != nil {
		return nil, err
	}
	tbl, idToPath, err := appendManySmallFiles(ctx, tbl, 10)
	if err != nil {
		return nil, err
	}
	path, ok := idToPath[1]
	if !ok {
		return nil, fmt.Errorf("expected data file for id=1")
	}
	tbl, err = commitEqualityDelete(ctx, tbl, []int64{4})
	if err != nil {
		return nil, err
	}
	tbl, err = commitPositionDeletes(ctx, tbl, path, []int64{0})
	if err != nil {
		return nil, err
	}
	return fixtureResult(MORMixedTable, tbl)
}

func seedMORPartial(ctx context.Context, cfg Config) (*Result, error) {
	cat, err := connectCatalog(ctx, cfg)
	if err != nil {
		return nil, err
	}
	tbl, err := recreateTable(ctx, cat, MORPartialTable)
	if err != nil {
		return nil, err
	}

	// Low-seq survivors (not targeted by partial compaction in e2e).
	tbl, err = appendOneRow(ctx, tbl, 1, "survivor-a")
	if err != nil {
		return nil, err
	}
	tbl, err = appendOneRow(ctx, tbl, 2, "survivor-b")
	if err != nil {
		return nil, err
	}
	tbl, err = commitEqualityDelete(ctx, tbl, []int64{99})
	if err != nil {
		return nil, err
	}
	// High-seq file that e2e compacts alone; eq-delete must be retained (seq ordering).
	tbl, err = appendOneRow(ctx, tbl, 50, "compact-me")
	if err != nil {
		return nil, err
	}
	return fixtureResult(MORPartialTable, tbl)
}

func fixtureResult(name string, tbl *table.Table) (*Result, error) {
	if tbl == nil {
		return nil, fmt.Errorf("nil table for %s", name)
	}
	return &Result{
		Name:     name,
		TableRef: fmt.Sprintf("%s.%s", Namespace, name),
		Location: tbl.Location(),
	}, nil
}
