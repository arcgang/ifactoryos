// Package certificate implements the Hyperledger Fabric chaincode for
// Digital Birth Certificate anchoring on the certificate-channel.
//
// Exported functions:
//   - IssueCertificate: write a new world-state entry or replay idempotently
//   - GetCertificate:   read and return the stored certificate entry
package certificate

import (
	"encoding/json"
	"fmt"

	"github.com/hyperledger/fabric-contract-api-go/contractapi"
)

// CertificateAsset is the world-state entry stored under certificateId.
type CertificateAsset struct {
	CertificateID   string `json:"certificateId"`
	VIN             string `json:"vin"`
	CertificateHash string `json:"certificateHash"`
	ValidatorID     string `json:"validatorId"`
	IssuedAt        string `json:"issuedAt"`
}

// SmartContract provides the chaincode functions.
type SmartContract struct {
	contractapi.Contract
}

// IssueCertificate writes a new CertificateAsset to the world state.
//
// Idempotent on certificateId:
//   - if the key already exists with the same certificateHash, the existing
//     asset is returned without creating a new ledger entry.
//   - if the key already exists with a different certificateHash, an error is
//     returned whose message the calling Python service maps to HTTP 409.
func (s *SmartContract) IssueCertificate(
	ctx contractapi.TransactionContextInterface,
	certificateID string,
	vin string,
	certificateHash string,
	validatorID string,
	issuedAt string,
) (*CertificateAsset, error) {
	existing, err := ctx.GetStub().GetState(certificateID)
	if err != nil {
		return nil, fmt.Errorf("failed to read world state: %w", err)
	}

	if existing != nil {
		var stored CertificateAsset
		if err = json.Unmarshal(existing, &stored); err != nil {
			return nil, fmt.Errorf("failed to unmarshal existing asset: %w", err)
		}
		if stored.CertificateHash == certificateHash {
			// Idempotent replay — same hash, return existing without error.
			return &stored, nil
		}
		// Hash mismatch — signal conflict to the calling service.
		return nil, fmt.Errorf("HASH_CONFLICT: certificateId %s already anchored with a different hash", certificateID)
	}

	asset := CertificateAsset{
		CertificateID:   certificateID,
		VIN:             vin,
		CertificateHash: certificateHash,
		ValidatorID:     validatorID,
		IssuedAt:        issuedAt,
	}

	assetJSON, err := json.Marshal(asset)
	if err != nil {
		return nil, fmt.Errorf("failed to marshal asset: %w", err)
	}

	if err = ctx.GetStub().PutState(certificateID, assetJSON); err != nil {
		return nil, fmt.Errorf("failed to put state: %w", err)
	}

	return &asset, nil
}

// GetCertificate reads a CertificateAsset from the world state by certificateId.
//
// Returns a not-found error when the key is absent so the caller can map it
// to HTTP 404.
func (s *SmartContract) GetCertificate(
	ctx contractapi.TransactionContextInterface,
	certificateID string,
) (*CertificateAsset, error) {
	data, err := ctx.GetStub().GetState(certificateID)
	if err != nil {
		return nil, fmt.Errorf("failed to read world state: %w", err)
	}
	if data == nil {
		return nil, fmt.Errorf("NOT_FOUND: certificate %s does not exist", certificateID)
	}

	var asset CertificateAsset
	if err = json.Unmarshal(data, &asset); err != nil {
		return nil, fmt.Errorf("failed to unmarshal asset: %w", err)
	}

	return &asset, nil
}
