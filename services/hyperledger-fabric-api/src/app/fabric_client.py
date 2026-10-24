"""Hyperledger Fabric gRPC client.

In production, SPIFFE/SPIRE-issued X.509 SVIDs and Fabric peer TLS certs are
injected by Vault Agent Injector at pod startup via the paths in Settings.
No credentials are stored in the container image.

This module exposes two async functions:
  - submit_transaction: invoke a chaincode function and wait for commit
  - query_transaction_hash: query the ledger for the hash stored under a tx id
"""

from __future__ import annotations

import asyncio
import json
import logging
from dataclasses import dataclass

logger = logging.getLogger(__name__)


@dataclass
class FabricCommitResult:
    transaction_id: str
    block_number: int


class FabricTimeoutError(Exception):
    """Raised when the Fabric commit does not confirm within the deadline."""


class FabricPeerError(Exception):
    """Raised when the Fabric peer is unreachable or endorsement fails."""


async def submit_transaction(
    *,
    certificate_id: str,
    vin: str,
    certificate_hash: str,
    gate_result_snapshot: dict,
    health_score: float,
    validator_id: str,
    issued_at: str,
    source_system: str,
    shift_id: str | None,
    timeout_seconds: float,
) -> FabricCommitResult:
    """Submit a certificate-issue transaction to the Fabric peer.

    In production this uses fabric-sdk-py over gRPC/mTLS.  The stub below
    simulates the async behaviour so the rest of the service can be tested
    and integrated without a live Fabric network.
    """
    from app.config import settings

    if settings.fabric_peer_endpoint.startswith("localhost") or settings.fabric_peer_endpoint.startswith("stub"):
        return await _stub_submit(certificate_id=certificate_id, timeout_seconds=timeout_seconds)

    return await _real_submit(
        certificate_id=certificate_id,
        vin=vin,
        certificate_hash=certificate_hash,
        gate_result_snapshot=gate_result_snapshot,
        health_score=health_score,
        validator_id=validator_id,
        issued_at=issued_at,
        source_system=source_system,
        shift_id=shift_id,
        timeout_seconds=timeout_seconds,
        settings=settings,
    )


async def query_transaction_hash(
    *,
    transaction_id: str,
    timeout_seconds: float = 10.0,
) -> str | None:
    """Query the Fabric ledger for the certificate hash stored under transaction_id.

    Returns the hex hash string, or None if the transaction is not found.
    """
    from app.config import settings

    if settings.fabric_peer_endpoint.startswith("localhost") or settings.fabric_peer_endpoint.startswith("stub"):
        return await _stub_query_hash(transaction_id=transaction_id)

    return await _real_query_hash(
        transaction_id=transaction_id,
        timeout_seconds=timeout_seconds,
        settings=settings,
    )


# ---------------------------------------------------------------------------
# Stub implementations (local dev / unit tests)
# ---------------------------------------------------------------------------

async def _stub_submit(*, certificate_id: str, timeout_seconds: float) -> FabricCommitResult:
    """Deterministic stub: returns a fake tx id derived from certificate_id."""
    import hashlib

    tx_id = hashlib.sha256(certificate_id.encode()).hexdigest()
    block_number = abs(hash(certificate_id)) % 1_000_000 + 1
    return FabricCommitResult(transaction_id=tx_id, block_number=block_number)


async def _stub_query_hash(*, transaction_id: str) -> str | None:
    return None


# ---------------------------------------------------------------------------
# Real implementations (production — fabric-sdk-py over gRPC/mTLS)
# ---------------------------------------------------------------------------

async def _real_submit(
    *,
    certificate_id: str,
    vin: str,
    certificate_hash: str,
    gate_result_snapshot: dict,
    health_score: float,
    validator_id: str,
    issued_at: str,
    source_system: str,
    shift_id: str | None,
    timeout_seconds: float,
    settings,
) -> FabricCommitResult:
    try:
        # fabric-sdk-py gateway pattern
        from hfc.fabric import Client  # type: ignore[import]
    except ImportError as err:
        raise FabricPeerError("fabric-sdk-py not installed") from err

    try:
        loop = asyncio.get_running_loop()
        result = await asyncio.wait_for(
            loop.run_in_executor(
                None,
                lambda: _blocking_fabric_submit(
                    settings=settings,
                    certificate_id=certificate_id,
                    vin=vin,
                    certificate_hash=certificate_hash,
                    gate_result_snapshot=gate_result_snapshot,
                    health_score=health_score,
                    validator_id=validator_id,
                    issued_at=issued_at,
                    source_system=source_system,
                    shift_id=shift_id,
                ),
            ),
            timeout=timeout_seconds,
        )
        return result
    except asyncio.TimeoutError as err:
        raise FabricTimeoutError(
            f"Fabric commit timed out after {timeout_seconds}s"
        ) from err
    except Exception as err:
        raise FabricPeerError(f"Fabric peer error: {err}") from err


def _blocking_fabric_submit(
    *,
    settings,
    certificate_id: str,
    vin: str,
    certificate_hash: str,
    gate_result_snapshot: dict,
    health_score: float,
    validator_id: str,
    issued_at: str,
    source_system: str,
    shift_id: str | None,
) -> FabricCommitResult:
    from hfc.fabric import Client  # type: ignore[import]

    client = Client(net_profile=None)
    client.new_channel(settings.fabric_channel_name)

    args = [
        certificate_id,
        vin,
        certificate_hash,
        json.dumps(gate_result_snapshot),
        str(health_score),
        validator_id,
        issued_at,
        source_system,
        shift_id or "",
    ]

    response = client.chaincode_invoke(
        requestor=None,
        channel_name=settings.fabric_channel_name,
        peers=[settings.fabric_peer_endpoint],
        args=args,
        cc_name=settings.fabric_chaincode_name,
        wait_for_event=True,
        wait_for_event_timeout=settings.fabric_transaction_timeout_seconds,
    )

    tx_id: str = response.get("txId", "")
    block_number: int = int(response.get("blockNumber", 0))
    return FabricCommitResult(transaction_id=tx_id, block_number=block_number)


async def _real_query_hash(
    *,
    transaction_id: str,
    timeout_seconds: float,
    settings,
) -> str | None:
    try:
        from hfc.fabric import Client  # type: ignore[import]
    except ImportError as err:
        raise FabricPeerError("fabric-sdk-py not installed") from err

    try:
        loop = asyncio.get_running_loop()
        result = await asyncio.wait_for(
            loop.run_in_executor(
                None,
                lambda: _blocking_fabric_query(
                    settings=settings,
                    transaction_id=transaction_id,
                ),
            ),
            timeout=timeout_seconds,
        )
        return result
    except asyncio.TimeoutError:
        logger.warning("Fabric hash query timed out for txId=%s", transaction_id)
        return None
    except Exception as err:
        raise FabricPeerError(f"Fabric peer error during query: {err}") from err


def _blocking_fabric_query(*, settings, transaction_id: str) -> str | None:
    from hfc.fabric import Client  # type: ignore[import]

    client = Client(net_profile=None)
    response = client.chaincode_query(
        requestor=None,
        channel_name=settings.fabric_channel_name,
        peers=[settings.fabric_peer_endpoint],
        args=[transaction_id],
        cc_name=settings.fabric_chaincode_name,
    )
    return response.get("certificateHash") if response else None
