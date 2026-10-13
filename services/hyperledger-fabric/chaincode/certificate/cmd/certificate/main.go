package main

import (
	"log"

	"github.com/hyperledger/fabric-contract-api-go/contractapi"

	certificate "github.com/bmw/ifactoryos/hyperledger-fabric/chaincode/certificate"
)

func main() {
	cc, err := contractapi.NewChaincode(&certificate.SmartContract{})
	if err != nil {
		log.Panicf("Error creating certificate chaincode: %v", err)
	}
	if err = cc.Start(); err != nil {
		log.Panicf("Error starting certificate chaincode: %v", err)
	}
}
