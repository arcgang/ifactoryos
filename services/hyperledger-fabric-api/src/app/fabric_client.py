"""Hyperledger Fabric client — wraps fabric-sdk-py over gRPC/TLS.

The client is injected via dependency injection so tests can substitute a fake.
Credentials (TLS CA cert, client cert/key) are injected by HashiCorp Vault agent
at pod startup into the paths declared in Settings.
"""

from __future__ import annotations

import asyncio
import json
import logging
import uuid
from dataclasses import dataclass

logger = logging.getLogger(__name__)


class FabricUnavailableError(Exception):
    """Fabric peer is unreachable or transaction endorsement failed."""


class FabricTimeoutError(Exception):
    """Fabric transaction commit timed out."""


class FabricHashConflictError(Exception):
    """certificateId already anchored with a different hash."""


class CertificateNotFoundError(Exception):
    """No world-state entry for the requested certificateId."""


@dataclass
class FabricAnchorResult:
    transaction_id: str
    block_number: int
    anchored_at: str  # ISO 8601 UTC


@dataclass
class FabricCertificateData:
    certificate_id: str
    vin: str
    certificate_hash: str
    validator_id: str
    issued_at: str
    transaction_id: str
    block_number: int
    anchored_at: str


class FabricClient:
    """Async façade over the Hyperledger Fabric SDK.

    In production, the submit/query calls use fabric-sdk-py's Gateway API.
    The class is kept framework-agnostic so that tests can subclass or mock it.
    """

    def __init__(
        self,
        peer_endpoint: str,
        tls_cert_path: str,
        client_cert_path: str,
        client_key_path: str,
        channel_name: str,
        chaincode_name: str,
        msp_id: str,
        commit_timeout_s: float,
    ) -> None:
        self._peer_endpoint = peer_endpoint
        self._tls_cert_path = tls_cert_path
        self._client_cert_path = client_cert_path
        self._client_key_path = client_key_path
        self._channel_name = channel_name
        self._chaincode_name = chaincode_name
        self._msp_id = msp_id
        self._commit_timeout_s = commit_timeout_s

    async def issue_certificate(
        self,
        certificate_id: str,
        vin: str,
        certificate_hash: str,
        validator_id: str,
        issued_at: str,
    ) -> tuple[FabricAnchorResult, bool]:
        """Submit IssueCertificate to the chaincode.

        Returns (FabricAnchorResult, idempotent):
          - idempotent=False on new issuance
          - idempotent=True on duplicate submission with same hash
        Raises FabricHashConflictError when certificateId exists with different hash.
        Raises FabricUnavailableError on endorsement/network failure.
        Raises FabricTimeoutError when commit exceeds self._commit_timeout_s.
        """
        try:
            return await asyncio.wait_for(
                self._submit_issue(certificate_id, vin, certificate_hash, validator_id, issued_at),
                timeout=self._commit_timeout_s,
            )
        except asyncio.TimeoutError as err:
            raise FabricTimeoutError(
                f"Fabric commit timed out after {self._commit_timeout_s}s"
            ) from err

    async def _submit_issue(
        self,
        certificate_id: str,
        vin: str,
        certificate_hash: str,
        validator_id: str,
        issued_at: str,
    ) -> tuple[FabricAnchorResult, bool]:
        # Production: connect to peer via fabric-sdk-py Gateway.
        # The SDK raises an exception on endorsement failure; we map it below.
        try:
            from hfc.fabric import Client as HFClient  # type: ignore[import-untyped]
        except ImportError:
            # fabric-sdk-py not installed — raise to surface clearly in dev.
            raise FabricUnavailableError(
                "fabric-sdk-py is not installed; set FABRIC_PEER_ENDPOINT or install the SDK"
            )

        raise FabricUnavailableError("Direct SDK wiring not implemented in this scaffold")  # pragma: no cover

    async def get_certificate(self, certificate_id: str) -> FabricCertificateData:
        """Query GetCertificate from the chaincode world state.

        Raises CertificateNotFoundError when absent.
        Raises FabricUnavailableError on peer query failure.
        """
        try:
            return await self._query_get(certificate_id)
        except CertificateNotFoundError:
            raise
        except (OSError, RuntimeError, ConnectionError, TimeoutError) as err:
            raise FabricUnavailableError(f"Fabric ledger query failed: {err}") from err

    async def _query_get(self, certificate_id: str) -> FabricCertificateData:
        raise FabricUnavailableError("Direct SDK wiring not implemented in this scaffold")  # pragma: no cover


_client: FabricClient | None = None


def get_fabric_client() -> FabricClient:
    global _client
    if _client is None:
        from app.config import get_settings

        cfg = get_settings()
        _client = FabricClient(
            peer_endpoint=cfg.fabric_peer_endpoint,
            tls_cert_path=cfg.fabric_tls_cert_path,
            client_cert_path=cfg.fabric_client_cert_path,
            client_key_path=cfg.fabric_client_key_path,
            channel_name=cfg.fabric_channel_name,
            chaincode_name=cfg.fabric_chaincode_name,
            msp_id=cfg.fabric_msp_id,
            commit_timeout_s=cfg.fabric_commit_timeout_s,
        )
    return _client
