package certificate_test

import (
	"encoding/json"
	"strings"
	"testing"

	"github.com/hyperledger/fabric-chaincode-go/shimtest"
	"github.com/hyperledger/fabric-contract-api-go/contractapi"

	certificate "github.com/bmw/ifactoryos/hyperledger-fabric/chaincode/certificate"
)

const (
	_certID   = "550e8400-e29b-41d4-a716-446655440000"
	_vin      = "WBA3A5C55FK123456"
	_hash     = "a3f2c1d4e5b6a7f8c9d0e1f2a3b4c5d6e7f8a9b0c1d2e3f4a5b6c7d8e9f0a1b2"
	_altHash  = "b3f2c1d4e5b6a7f8c9d0e1f2a3b4c5d6e7f8a9b0c1d2e3f4a5b6c7d8e9f0a1b3"
	_valID    = "validator-uuid-1234"
	_issuedAt = "2026-10-13T08:00:00Z"
)

func newStub() (*shimtest.MockStub, contractapi.TransactionContextInterface) {
	sc := &certificate.SmartContract{}
	cc, _ := contractapi.NewChaincode(sc)
	stub := shimtest.NewMockStub("certificate", cc)
	stub.MockTransactionStart("tx1")

	ctx := &contractapi.TransactionContext{}
	ctx.SetStub(stub)

	return stub, ctx
}

// ---------------------------------------------------------------------------
// Happy path — new issuance
// ---------------------------------------------------------------------------

func TestIssueCertificate_NewAsset_ReturnsAsset(t *testing.T) {
	_, ctx := newStub()
	sc := &certificate.SmartContract{}

	asset, err := sc.IssueCertificate(ctx, _certID, _vin, _hash, _valID, _issuedAt)
	if err != nil {
		t.Fatalf("IssueCertificate returned unexpected error: %v", err)
	}
	if asset == nil {
		t.Fatal("expected non-nil asset")
	}
	if asset.CertificateID != _certID {
		t.Errorf("certificateId = %q, want %q", asset.CertificateID, _certID)
	}
	if asset.VIN != _vin {
		t.Errorf("vin = %q, want %q", asset.VIN, _vin)
	}
	if asset.CertificateHash != _hash {
		t.Errorf("certificateHash = %q, want %q", asset.CertificateHash, _hash)
	}
}

func TestIssueCertificate_NewAsset_PersistedInWorldState(t *testing.T) {
	stub, ctx := newStub()
	sc := &certificate.SmartContract{}

	_, err := sc.IssueCertificate(ctx, _certID, _vin, _hash, _valID, _issuedAt)
	if err != nil {
		t.Fatalf("IssueCertificate failed: %v", err)
	}

	raw := stub.State[_certID]
	if raw == nil {
		t.Fatal("world state does not contain the certificate key")
	}

	var stored certificate.CertificateAsset
	if err = json.Unmarshal(raw, &stored); err != nil {
		t.Fatalf("world state value is not valid JSON: %v", err)
	}
	if stored.CertificateHash != _hash {
		t.Errorf("stored hash = %q, want %q", stored.CertificateHash, _hash)
	}
}

// ---------------------------------------------------------------------------
// Idempotent replay — same hash
// ---------------------------------------------------------------------------

func TestIssueCertificate_IdempotentReplay_SameHash_ReturnsExisting(t *testing.T) {
	_, ctx := newStub()
	sc := &certificate.SmartContract{}

	first, _ := sc.IssueCertificate(ctx, _certID, _vin, _hash, _valID, _issuedAt)
	second, err := sc.IssueCertificate(ctx, _certID, _vin, _hash, _valID, _issuedAt)

	if err != nil {
		t.Fatalf("second IssueCertificate returned unexpected error: %v", err)
	}
	if second == nil {
		t.Fatal("expected non-nil asset on idempotent replay")
	}
	if first.CertificateID != second.CertificateID {
		t.Errorf("idempotent replay returned different certificateId")
	}
}

// ---------------------------------------------------------------------------
// Hash conflict — different hash
// ---------------------------------------------------------------------------

func TestIssueCertificate_HashConflict_ReturnsError(t *testing.T) {
	_, ctx := newStub()
	sc := &certificate.SmartContract{}

	_, err := sc.IssueCertificate(ctx, _certID, _vin, _hash, _valID, _issuedAt)
	if err != nil {
		t.Fatalf("first issuance failed unexpectedly: %v", err)
	}

	_, conflictErr := sc.IssueCertificate(ctx, _certID, _vin, _altHash, _valID, _issuedAt)
	if conflictErr == nil {
		t.Fatal("expected error on hash conflict, got nil")
	}
	if !strings.Contains(conflictErr.Error(), "HASH_CONFLICT") {
		t.Errorf("error message %q does not contain HASH_CONFLICT", conflictErr.Error())
	}
}

// ---------------------------------------------------------------------------
// GetCertificate — found
// ---------------------------------------------------------------------------

func TestGetCertificate_Found_ReturnsAsset(t *testing.T) {
	_, ctx := newStub()
	sc := &certificate.SmartContract{}

	_, _ = sc.IssueCertificate(ctx, _certID, _vin, _hash, _valID, _issuedAt)

	asset, err := sc.GetCertificate(ctx, _certID)
	if err != nil {
		t.Fatalf("GetCertificate returned unexpected error: %v", err)
	}
	if asset == nil {
		t.Fatal("expected non-nil asset")
	}
	if asset.CertificateHash != _hash {
		t.Errorf("certificateHash = %q, want %q", asset.CertificateHash, _hash)
	}
}

func TestGetCertificate_Found_HashMatchesPersisted(t *testing.T) {
	_, ctx := newStub()
	sc := &certificate.SmartContract{}

	_, _ = sc.IssueCertificate(ctx, _certID, _vin, _hash, _valID, _issuedAt)
	asset, _ := sc.GetCertificate(ctx, _certID)

	if asset.CertificateID != _certID {
		t.Errorf("certificateId = %q, want %q", asset.CertificateID, _certID)
	}
	if asset.VIN != _vin {
		t.Errorf("vin = %q, want %q", asset.VIN, _vin)
	}
}

// ---------------------------------------------------------------------------
// GetCertificate — not found
// ---------------------------------------------------------------------------

func TestGetCertificate_NotFound_ReturnsError(t *testing.T) {
	_, ctx := newStub()
	sc := &certificate.SmartContract{}

	_, err := sc.GetCertificate(ctx, "non-existent-id")
	if err == nil {
		t.Fatal("expected error for absent certificate, got nil")
	}
	if !strings.Contains(err.Error(), "NOT_FOUND") {
		t.Errorf("error message %q does not contain NOT_FOUND", err.Error())
	}
}
