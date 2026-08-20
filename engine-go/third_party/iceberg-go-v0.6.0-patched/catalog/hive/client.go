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
	"context"
	"fmt"
	"net/url"
	"strconv"
	"time"

	"github.com/beltran/gohive"
	"github.com/beltran/gohive/hive_metastore"
)

// hiveConnectTimeout bounds a single ConnectToMetastore attempt. gohive.ConnectToMetastore
// (meta.go, v1.8.1) opens the socket via thrift.NewTSocket(addr) -- a bare hostPort string, not
// thrift's own NewTSocketTimeout/SetConnTimeout constructors -- so the underlying net.Dial has
// no deadline at all and blocks on Go's/the OS's own default, which for a black-holed
// connection (packets silently dropped, no RST -- the common case for a stale/rotated metastore
// IP behind an internal DNS record, as opposed to an actively-refused port) can run for minutes.
//
// Found live, 2026-08-20: a batched native dispatch stuck at the (then-already-raised, per
// PDAE-8546) 4h activeDeadlineSeconds killed the whole pod, wiping every member's outcome the
// deadline-kill path can't recover logs for. Root cause traced to individual catalog.Load
// attempts each taking ~1,664s (27.7 min) against a metastore hostname whose DNS had rotated to
// a since-recycled pod IP (confirmed live: the failing IPs in the pod's own logs matched no
// currently-running pod, while a fresh in-pod DNS lookup of the same hostname resolved to a
// third, different address entirely -- an internal NLB/DNS-based failover, not a Cryo-side
// cache). catalogLoadAttempts' own 4-attempt/2-4-8s-backoff design (this package's other
// comment, catalog.go) already assumes each attempt is cheap and fails fast; it silently stops
// being true the moment a single attempt can itself run for half an hour, which is exactly what
// turned a 32-table batch that normally finishes in ~11 minutes (see cryo/native_admit.py's own
// timing baseline) into one still running two hours in with 1 millicore of CPU used -- not slow,
// genuinely hung waiting on a dead socket.
//
// 45s is comfortably longer than any real, live metastore round-trip (sub-second per the
// GetDatabase/GetTable calls actually made) while still bounding the worst case to a small,
// known cost per attempt -- 4 attempts * 45s cap + the existing 14s of backoff is at most ~3.3
// minutes total per table, not up to 4 hours.
const hiveConnectTimeout = 45 * time.Second

type HiveClient interface {
	Close() error

	GetDatabase(ctx context.Context, name string) (*hive_metastore.Database, error)
	CreateDatabase(ctx context.Context, database *hive_metastore.Database) error
	AlterDatabase(ctx context.Context, dbname string, db *hive_metastore.Database) error
	DropDatabase(ctx context.Context, name string, deleteData, cascade bool) error
	GetAllDatabases(ctx context.Context) ([]string, error)

	GetTable(ctx context.Context, dbName, tableName string) (*hive_metastore.Table, error)
	CreateTable(ctx context.Context, tbl *hive_metastore.Table) error
	AlterTable(ctx context.Context, dbName, tableName string, newTbl *hive_metastore.Table) error
	DropTable(ctx context.Context, dbName, tableName string, deleteData bool) error
	GetTables(ctx context.Context, dbName, pattern string) ([]string, error)

	Lock(ctx context.Context, request *hive_metastore.LockRequest) (*hive_metastore.LockResponse, error)
	CheckLock(ctx context.Context, lockId int64) (*hive_metastore.LockResponse, error)
	Unlock(ctx context.Context, lockId int64) error
}

type thriftClient struct {
	*gohive.HiveMetastoreClient
}

func newHiveClient(uri string, opts *HiveOptions) (HiveClient, error) {
	parsed, err := url.Parse(uri)
	if err != nil {
		return nil, fmt.Errorf("invalid URI: %w", err)
	}

	host := parsed.Hostname()
	portStr := parsed.Port()
	if portStr == "" {
		portStr = "9083"
	}
	port, err := strconv.Atoi(portStr)
	if err != nil {
		return nil, fmt.Errorf("invalid port: %w", err)
	}

	config := gohive.NewMetastoreConnectConfiguration()
	config.TransportMode = "binary"

	client, err := connectToMetastoreWithTimeout(host, port, config, hiveConnectTimeout)
	if err != nil {
		return nil, fmt.Errorf("failed to connect to metastore: %w", err)
	}

	return &thriftClient{HiveMetastoreClient: client}, nil
}

// connectToMetastoreWithTimeout bounds gohive.ConnectToMetastore, which has no timeout of its
// own -- see hiveConnectTimeout's doc comment for why this exists. gohive.ConnectToMetastore
// takes no context/deadline parameter, so the only way to bound it from the outside is to run it
// on its own goroutine and stop waiting on the result channel after the deadline; a timed-out
// attempt's goroutine is deliberately abandoned rather than killed (Go has no API to forcibly
// cancel a blocked syscall) -- it either eventually completes and its result is discarded here,
// or is cleaned up whenever its own underlying connection attempt errors out. This does not leak
// unboundedly: loadCatalogWithRetry's own attempt cap (4) bounds how many can ever be in flight
// per LoadTable call, and each one that does eventually connect closes its socket normally via
// gohive's own client lifecycle once garbage collected.
func connectToMetastoreWithTimeout(
	host string,
	port int,
	config *gohive.MetastoreConnectConfiguration,
	timeout time.Duration,
) (*gohive.HiveMetastoreClient, error) {
	return connectWithTimeout(
		func() (*gohive.HiveMetastoreClient, error) {
			return gohive.ConnectToMetastore(host, port, "NOSASL", config)
		},
		timeout,
	)
}

// connectWithTimeout races an arbitrary blocking connect func against a deadline. Split out from
// connectToMetastoreWithTimeout so the race/timeout logic itself is unit-testable with an
// injected func that sleeps/blocks deterministically, without needing a real (or even fake)
// Thrift metastore listener.
func connectWithTimeout[T any](
	connect func() (T, error),
	timeout time.Duration,
) (T, error) {
	type result struct {
		client T
		err    error
	}
	resultCh := make(chan result, 1)
	go func() {
		client, err := connect()
		resultCh <- result{client: client, err: err}
	}()

	select {
	case r := <-resultCh:
		return r.client, r.err
	case <-time.After(timeout):
		var zero T
		return zero, fmt.Errorf("connect timed out after %s", timeout)
	}
}

func (c *thriftClient) Close() error {
	if c.HiveMetastoreClient != nil {
		c.HiveMetastoreClient.Close()
		c.HiveMetastoreClient = nil
	}

	return nil
}

func (c *thriftClient) GetDatabase(ctx context.Context, name string) (*hive_metastore.Database, error) {
	return c.Client.GetDatabase(ctx, name)
}

func (c *thriftClient) CreateDatabase(ctx context.Context, database *hive_metastore.Database) error {
	return c.Client.CreateDatabase(ctx, database)
}

func (c *thriftClient) AlterDatabase(ctx context.Context, dbname string, db *hive_metastore.Database) error {
	return c.Client.AlterDatabase(ctx, dbname, db)
}

func (c *thriftClient) DropDatabase(ctx context.Context, name string, deleteData, cascade bool) error {
	return c.Client.DropDatabase(ctx, name, deleteData, cascade)
}

func (c *thriftClient) GetAllDatabases(ctx context.Context) ([]string, error) {
	return c.Client.GetAllDatabases(ctx)
}

func (c *thriftClient) GetTable(ctx context.Context, dbName, tableName string) (*hive_metastore.Table, error) {
	return c.Client.GetTable(ctx, dbName, tableName)
}

func (c *thriftClient) CreateTable(ctx context.Context, tbl *hive_metastore.Table) error {
	return c.Client.CreateTable(ctx, tbl)
}

func (c *thriftClient) AlterTable(ctx context.Context, dbName, tableName string, newTbl *hive_metastore.Table) error {
	return c.Client.AlterTable(ctx, dbName, tableName, newTbl)
}

func (c *thriftClient) DropTable(ctx context.Context, dbName, tableName string, deleteData bool) error {
	return c.Client.DropTable(ctx, dbName, tableName, deleteData)
}

func (c *thriftClient) GetTables(ctx context.Context, dbName, pattern string) ([]string, error) {
	return c.Client.GetTables(ctx, dbName, pattern)
}

func (c *thriftClient) Lock(ctx context.Context, request *hive_metastore.LockRequest) (*hive_metastore.LockResponse, error) {
	return c.Client.Lock(ctx, request)
}

func (c *thriftClient) CheckLock(ctx context.Context, lockId int64) (*hive_metastore.LockResponse, error) {
	return c.Client.CheckLock(ctx, &hive_metastore.CheckLockRequest{
		Lockid: lockId,
	})
}

func (c *thriftClient) Unlock(ctx context.Context, lockId int64) error {
	return c.Client.Unlock(ctx, &hive_metastore.UnlockRequest{
		Lockid: lockId,
	})
}
