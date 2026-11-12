"""Async client for the read-only HESCOM consumer API.

Only endpoints that the HESCOM consumer portal uses to *display* information are
wrapped here.  Nothing in this module can move money, raise service requests or
touch an administrative surface.
"""

from __future__ import annotations

import asyncio
import base64
import binascii
import json
import logging
import re
from datetime import datetime
from typing import Any

import aiohttp

from homeassistant.core import HomeAssistant
from homeassistant.helpers.aiohttp_client import async_get_clientsession
from homeassistant.util import dt as dt_util

from .const import (
    API_HOST,
    BASE_CCB,
    BASE_HESCOM,
    DEFAULT_SERVICE_KEY,
    DISCOM_ID,
    KEY_REFRESH_COOLDOWN,
    PORTAL_INDEX,
    REQUEST_TIMEOUT,
    USER_AGENT,
)
from .crypto import unwrap

_LOGGER = logging.getLogger(__name__)

#: ``main.<hash>.js`` is the bundle that carries the rotating service key.
_BUNDLE_RE = re.compile(r"main\.[0-9a-fA-F]{16}\.js")
_SERVICE_KEY_RE = re.compile(r'appServiceKey:"([^"]+)"')

#: Status words that mean the backend rejected the call.
_BAD_STATUS = {"FAIL", "FAILURE", "ERROR", "FAILED"}

_JSON_CONTENT_TYPES = ("application/json", "text/json", "text/plain")


class HescomApiError(Exception):
    """A HESCOM API call did not succeed."""


class HescomAuthError(HescomApiError):
    """The service key was rejected and could not be refreshed."""


class HescomApi:
    """Thin, retrying wrapper around the HESCOM HTTP API."""

    def __init__(
        self,
        hass: HomeAssistant,
        account_id: str,
        service_key: str | None = None,
    ) -> None:
        """Set up the client.  No I/O happens here."""
        self.hass = hass
        self.account_id = account_id
        self._session = async_get_clientsession(hass)
        self._service_key = service_key or DEFAULT_SERVICE_KEY
        self._last_key_refresh: datetime | None = None
        self._key_lock = asyncio.Lock()

    @property
    def service_key(self) -> str:
        """The key currently used for the ``appServiceKey`` header."""
        return self._service_key

    # ------------------------------------------------------------------
    # Plumbing
    # ------------------------------------------------------------------
    def _headers(self) -> dict[str, str]:
        return {
            "appServiceKey": self._service_key,
            "Accept": "application/json",
            "User-Agent": USER_AGENT,
        }

    async def _request(
        self,
        method: str,
        url: str,
        *,
        params: dict[str, Any] | None = None,
        payload: dict[str, Any] | None = None,
        decrypt: bool = False,
        _retry: bool = True,
    ) -> Any:
        """Perform one API call, refreshing the service key once on a 401."""
        timeout = aiohttp.ClientTimeout(total=REQUEST_TIMEOUT)
        try:
            async with self._session.request(
                method,
                url,
                params=params,
                json=payload,
                headers=self._headers(),
                timeout=timeout,
                allow_redirects=True,
            ) as response:
                status = response.status
                body = await response.text()

                if status == 401:
                    if _retry and await self._refresh_service_key():
                        return await self._request(
                            method,
                            url,
                            params=params,
                            payload=payload,
                            decrypt=decrypt,
                            _retry=False,
                        )
                    raise HescomAuthError(
                        f"{method} {url} rejected the service key (HTTP 401)"
                    )

                if status != 200:
                    snippet = body.strip().replace("\n", " ")[:200]
                    raise HescomApiError(
                        f"{method} {url} returned HTTP {status}: {snippet}"
                    )
        except asyncio.TimeoutError as err:
            raise HescomApiError(f"{method} {url} timed out") from err
        except aiohttp.ClientError as err:
            raise HescomApiError(f"{method} {url} failed: {err}") from err

        try:
            data = self._decode(body, decrypt)
        except ValueError as err:
            raise HescomApiError(f"{method} {url} returned invalid JSON: {err}") from err

        self._raise_for_status(data, url)
        return data

    @staticmethod
    def _decode(body: str, decrypt: bool) -> Any:
        """Turn a response body into Python data, decrypting when asked."""
        text = body.strip()
        if not text:
            return None
        data = json.loads(text)
        return unwrap(data) if decrypt else data

    @staticmethod
    def _raise_for_status(data: Any, url: str) -> None:
        """Raise when a payload carries an explicit failure marker.

        HESCOM mixes ``status``, ``statusMsg``, ``statusCode`` and ``ResultCode``
        depending on the service, and most successes carry no marker at all.
        """
        if not isinstance(data, dict):
            return
        for key in ("status", "statusMsg", "resultStatus"):
            value = data.get(key)
            if isinstance(value, str) and value.strip().upper() in _BAD_STATUS:
                raise HescomApiError(f"{url} reported {key}={value!r}")
        error = data.get("error")
        if isinstance(error, dict) and error.get("message"):
            raise HescomApiError(f"{url} reported an error: {error['message']}")

    # ------------------------------------------------------------------
    # Service key discovery
    # ------------------------------------------------------------------
    async def async_refresh_service_key(self) -> str | None:
        """Rediscover the current ``appServiceKey`` from the frontend bundle."""
        if not await self._refresh_service_key():
            return None
        return self._service_key

    async def _refresh_service_key(self) -> bool:
        """Refresh the key unless that was attempted very recently."""
        async with self._key_lock:
            if self._last_key_refresh is not None:
                elapsed = dt_util.utcnow() - self._last_key_refresh
                if elapsed < KEY_REFRESH_COOLDOWN:
                    _LOGGER.debug(
                        "Skipping service key refresh; last attempt was %s ago", elapsed
                    )
                    return False
            self._last_key_refresh = dt_util.utcnow()

            try:
                key = await self._discover_service_key()
            except (HescomApiError, ValueError) as err:
                _LOGGER.warning("Could not refresh the HESCOM service key: %s", err)
                return False

            if not key:
                _LOGGER.warning("HESCOM bundle did not contain an appServiceKey")
                return False

            if key != self._service_key:
                _LOGGER.info("Refreshed the HESCOM service key")
                self._service_key = key
            else:
                _LOGGER.debug("HESCOM service key unchanged after refresh")
            return True

    async def _discover_service_key(self) -> str | None:
        """Fetch the Angular shell, then the JS bundle, then scrape the key."""
        timeout = aiohttp.ClientTimeout(total=REQUEST_TIMEOUT)
        try:
            async with self._session.get(
                PORTAL_INDEX,
                headers={"User-Agent": USER_AGENT},
                timeout=timeout,
            ) as response:
                if response.status != 200:
                    raise HescomApiError(
                        f"portal index returned HTTP {response.status}"
                    )
                index = await response.text()
        except asyncio.TimeoutError as err:
            raise HescomApiError("portal index timed out") from err
        except aiohttp.ClientError as err:
            raise HescomApiError(f"portal index failed: {err}") from err

        match = _BUNDLE_RE.search(index)
        if match is None:
            raise HescomApiError("could not locate main.*.js in the portal index")

        bundle_name = match.group(0)
        bundle_url = f"{API_HOST}/hescom/{bundle_name}"

        try:
            async with self._session.get(
                bundle_url,
                headers={"User-Agent": USER_AGENT},
                timeout=aiohttp.ClientTimeout(total=REQUEST_TIMEOUT * 3),
            ) as response:
                if response.status != 200:
                    raise HescomApiError(
                        f"{bundle_name} returned HTTP {response.status}"
                    )
                bundle = await response.text(errors="ignore")
        except asyncio.TimeoutError as err:
            raise HescomApiError("bundle download timed out") from err
        except aiohttp.ClientError as err:
            raise HescomApiError(f"bundle download failed: {err}") from err

        key_match = _SERVICE_KEY_RE.search(bundle)
        return key_match.group(1) if key_match else None

    # ------------------------------------------------------------------
    # Endpoints
    # ------------------------------------------------------------------
    async def async_get_customer_entity(self) -> dict[str, Any]:
        """``getCustomerEntity`` — the primary profile/due-amount source."""
        data = await self._request(
            "GET",
            f"{BASE_CCB}/api/getCustomerEntity",
            params={"ConsumerAccountId": self.account_id},
        )
        entity = data.get("consumerEntity") if isinstance(data, dict) else None
        if not isinstance(entity, dict):
            raise HescomApiError("getCustomerEntity did not return a consumerEntity")
        return entity

    async def async_get_account_status(self) -> dict[str, Any]:
        """``getAccountStatus`` — connection status plus service agreement id."""
        data = await self._request(
            "GET",
            f"{BASE_CCB}/api/getAccountStatus",
            params={"ConsumerAccountId": self.account_id},
        )
        status = data.get("consumerAccountStatus") if isinstance(data, dict) else None
        return status if isinstance(status, dict) else {}

    async def async_enquire_consumer_details(self) -> dict[str, Any]:
        """``enquireConsumerDetails`` — bill id and last meter-read date."""
        data = await self._request(
            "POST",
            f"{BASE_HESCOM}/api/v1/enquireConsumerDetails",
            payload={"accountId": self.account_id},
        )
        details = data.get("consumerDetails") if isinstance(data, dict) else None
        return details if isinstance(details, dict) else {}

    async def async_get_meter_details(self, bill_id: str) -> dict[str, Any]:
        """``getMeterDetails`` — readings for one bill."""
        data = await self._request(
            "POST",
            f"{BASE_HESCOM}/api/getMeterDetails",
            payload={
                "accountId": self.account_id,
                "billId": bill_id,
                "discomId": DISCOM_ID,
            },
        )
        return data if isinstance(data, dict) else {}

    async def async_get_meter_info(self) -> dict[str, Any]:
        """``get-meter-info`` — encrypted meter badge payload, if available."""
        data = await self._request(
            "POST",
            f"{BASE_HESCOM}/api/get-meter-info",
            payload={"accountId": self.account_id},
            decrypt=True,
        )
        return data if isinstance(data, dict) else {}

    async def async_get_reads(self) -> dict[str, Any]:
        """``fetchAccountReadsForMobileApp`` — SOAP-style consumer snapshot."""
        data = await self._request(
            "POST",
            f"{BASE_HESCOM}/api/v1/fetchAccountReadsForMobileApp",
            payload={"accountId": self.account_id},
        )
        soap = data.get("soapResponse") if isinstance(data, dict) else None
        return soap if isinstance(soap, dict) else {}

    async def async_get_payments(self) -> list[dict[str, Any]]:
        """``paymentsHistoryDetails`` — plaintext payment ledger."""
        data = await self._request(
            "POST",
            f"{BASE_HESCOM}/api/v1/paymentsHistoryDetails",
            payload={"AccountID": self.account_id},
        )
        rows = data.get("data") if isinstance(data, dict) else None
        return [row for row in rows or [] if isinstance(row, dict)]

    async def async_get_online_transactions(self, person_id: str) -> list[dict[str, Any]]:
        """``onlineTrasactionDetails`` — encrypted online payment attempts."""
        if not person_id:
            return []
        data = await self._request(
            "POST",
            f"{BASE_HESCOM}/api/onlineTrasactionDetails",
            payload={"personId": person_id},
            decrypt=True,
        )
        return _extract_transactions(data)

    async def async_get_complaints(self, person_id: str) -> list[dict[str, Any]]:
        """``trackBillComplaint`` — encrypted service-request list."""
        if not person_id:
            return []
        data = await self._request(
            "GET",
            f"{BASE_HESCOM}/api/trackBillComplaint",
            params={"personId": person_id},
            decrypt=True,
        )
        rows = None
        if isinstance(data, dict):
            rows = data.get("ServiceRequests") or data.get("serviceRequests")
        if not isinstance(rows, list):
            return []
        return [row for row in rows if isinstance(row, dict)]

    async def async_get_bill_pdf(self, bill_id: str) -> bytes | None:
        """``view-billpdf`` — the rendered bill as PDF bytes.

        The service answers ``{"ResultCode": "1", "Result": "<base64>"}``.  A
        non-success code means "no PDF for that bill", which is not an error.
        """
        data = await self._request(
            "POST",
            f"{BASE_HESCOM}/view-billpdf",
            payload={
                "accountId": self.account_id,
                "billId": bill_id,
                "discomId": DISCOM_ID,
            },
        )
        if not isinstance(data, dict):
            return None

        code = str(data.get("ResultCode", "")).strip()
        encoded = data.get("Result")
        if code not in {"1", "SUCCESS"} and not code.startswith("S"):
            _LOGGER.debug("view-billpdf returned ResultCode %r for %s", code, bill_id)
            return None
        if not isinstance(encoded, str) or not encoded:
            return None

        try:
            pdf = base64.b64decode(encoded)
        except (binascii.Error, ValueError) as err:
            _LOGGER.warning("Bill %s PDF was not valid base64: %s", bill_id, err)
            return None

        if not pdf.startswith(b"%PDF"):
            _LOGGER.warning("Bill %s payload is not a PDF document", bill_id)
            return None
        return pdf

    async def async_validate(self) -> dict[str, Any]:
        """Confirm the account id works; used by the config flow."""
        return await self.async_get_customer_entity()


def _extract_transactions(data: Any) -> list[dict[str, Any]]:
    """Pull the transaction rows out of whichever wrapper the API used."""
    if isinstance(data, list):
        return [row for row in data if isinstance(row, dict)]
    if not isinstance(data, dict):
        return []

    for key in ("transactionEntity", "transactionEntityList", "transactions", "data"):
        value = data.get(key)
        if isinstance(value, list):
            return [row for row in value if isinstance(row, dict)]
        if isinstance(value, dict):
            for nested in ("transactionEntity", "transactions"):
                rows = value.get(nested)
                if isinstance(rows, list):
                    return [row for row in rows if isinstance(row, dict)]
    return []


__all__ = [
    "HescomApi",
    "HescomApiError",
    "HescomAuthError",
]
