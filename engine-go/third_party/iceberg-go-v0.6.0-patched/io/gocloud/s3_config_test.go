package gocloud

import (
	"context"
	"testing"

	"github.com/aws/aws-sdk-go-v2/aws"
	"github.com/aws/aws-sdk-go-v2/credentials"
	"github.com/apache/iceberg-go/io"
	"github.com/apache/iceberg-go/utils"
)

func TestVendedS3KeysOverrideContextConfig(t *testing.T) {
	ctx := utils.WithAwsConfig(context.Background(), &aws.Config{
		Credentials: credentials.NewStaticCredentialsProvider("static-key", "static-secret", ""),
	})
	cfg, err := awsConfigForS3(ctx, map[string]string{
		io.S3AccessKeyID:     "vended-key",
		io.S3SecretAccessKey: "vended-secret",
		io.S3SessionToken:    "vended-token",
	})
	if err != nil {
		t.Fatal(err)
	}
	got, err := cfg.Credentials.Retrieve(context.Background())
	if err != nil {
		t.Fatal(err)
	}
	if got.AccessKeyID != "vended-key" || got.SecretAccessKey != "vended-secret" || got.SessionToken != "vended-token" {
		t.Fatalf("context static keys won over vended credentials: %+v", got)
	}
}

func TestContextStaticKeysUsedWhenTableHasNone(t *testing.T) {
	ctx := utils.WithAwsConfig(context.Background(), &aws.Config{
		Credentials: credentials.NewStaticCredentialsProvider("static-key", "static-secret", "static-token"),
	})
	cfg, err := awsConfigForS3(ctx, map[string]string{})
	if err != nil {
		t.Fatal(err)
	}
	got, err := cfg.Credentials.Retrieve(context.Background())
	if err != nil {
		t.Fatal(err)
	}
	if got.AccessKeyID != "static-key" {
		t.Fatalf("expected static source key, got %+v", got)
	}
}
