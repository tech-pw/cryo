package protocol

import "encoding/json"

// ExecuteRequest mirrors cryo-engine/src/main.rs ExecuteRequest JSON.
type ExecuteRequest struct {
	TableRef          string             `json:"table_ref"`
	CatalogURI        string             `json:"catalog_uri"`
	CatalogType       string             `json:"catalog_type,omitempty"`
	Warehouse         string             `json:"warehouse"`
	Mode              string             `json:"mode"`
	Operations        []OperationRequest `json:"operations"`
	Params            json.RawMessage    `json:"params,omitempty"`
	S3Endpoint        *string            `json:"s3_endpoint,omitempty"`
	S3AccessKey       *string            `json:"s3_access_key,omitempty"`
	S3SecretKey       *string            `json:"s3_secret_key,omitempty"`
	CatalogProperties map[string]string  `json:"catalog_properties,omitempty"`
}

// PredicateTermRequest mirrors cryo-engine PredicateTermRequest.
type PredicateTermRequest struct {
	BaseColumn     string          `json:"base_column"`
	Transform      string          `json:"transform"`
	Value          json.RawMessage `json:"value"`
	ColType        *string         `json:"col_type,omitempty"`
	PartitionField *string         `json:"partition_field,omitempty"`
}

// PredicateRequest mirrors cryo-engine PredicateRequest.
type PredicateRequest struct {
	Terms []PredicateTermRequest `json:"terms"`
}

// OperationRequest mirrors cryo-engine OperationRequest.
type OperationRequest struct {
	OperationType string             `json:"type"`
	Params        json.RawMessage    `json:"params,omitempty"`
	Predicates    []PredicateRequest `json:"predicates,omitempty"`
}

// ExecuteResponse mirrors cryo-engine ExecuteResponse JSON.
type ExecuteResponse struct {
	OK         bool              `json:"ok"`
	TableRef   string            `json:"table_ref"`
	Operations int               `json:"operations"`
	Results    []OperationResult `json:"results"`
	Error      *string           `json:"error,omitempty"`
}

// OperationResult mirrors cryo-engine OperationResult JSON.
type OperationResult struct {
	Operation          string  `json:"operation"`
	Success            bool    `json:"success"`
	FilesRewritten     *uint64 `json:"files_rewritten,omitempty"`
	InputFiles         *uint64 `json:"input_files,omitempty"`
	OutputFiles        *uint64 `json:"output_files,omitempty"`
	BytesRemoved       *uint64 `json:"bytes_removed,omitempty"`
	BytesWritten       *uint64 `json:"bytes_written,omitempty"`
	SnapshotsExpired   *uint64 `json:"snapshots_expired,omitempty"`
	ManifestsRewritten *uint64 `json:"manifests_rewritten,omitempty"`
	Error              *string `json:"error,omitempty"`
	DurationMS         uint64  `json:"duration_ms"`
}
