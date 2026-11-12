"""Data coordinator for the HESCOM integration.

One refresh performs at most seven read-only calls, in three waves:

1. ``getCustomerEntity`` — the only call allowed to abort the refresh.
2. Account status, bill enquiry, payment ledger, online transactions and
   complaint list, gathered concurrently.  Each is optional.
3. ``getMeterDetails`` for the bill found in wave 2.

Optional results are cached in ``_last_good`` so a single failing endpoint
degrades one entity instead of blanking the whole device.

The poll interval is chosen on every cycle: a slow ordinary cadence, a fast one
while a new bill is imminently expected, and a medium one once the bill is
overdue and generation appears to be running late.
"""

from __future__ import annotations

import asyncio
import logging
from dataclasses import dataclass, field
from datetime import date, datetime, timedelta
from typing import Any

from homeassistant.config_entries import ConfigEntry
from homeassistant.const import UTC
from homeassistant.core import HomeAssistant
from homeassistant.exceptions import ConfigEntryAuthFailed
from homeassistant.helpers.storage import Store
from homeassistant.helpers.update_coordinator import DataUpdateCoordinator, UpdateFailed
from homeassistant.util import dt as dt_util

from .api import HescomApi, HescomApiError, HescomAuthError
from .archive import async_archive_bill, bill_pdf_path, bill_pdf_url
from .const import (
    CONF_ARCHIVE_PDFS,
    CONF_DELAY_INTERVAL,
    CONF_SCAN_INTERVAL,
    CONF_WATCH_AFTER_DAYS,
    CONF_WATCH_INTERVAL,
    CONF_WATCH_MAX_DAYS,
    DEFAULT_ARCHIVE_PDFS,
    DEFAULT_DELAY_INTERVAL,
    DEFAULT_SCAN_INTERVAL,
    DEFAULT_WATCH_AFTER_DAYS,
    DEFAULT_WATCH_INTERVAL,
    DEFAULT_WATCH_MAX_DAYS,
    DOMAIN,
    EVENT_NEW_BILL,
    PDF_RETRY_INTERVAL,
    STORAGE_KEY,
    STORAGE_VERSION,
)
from .helpers import as_float, as_int, as_text, dig, parse_date, parse_datetime

_LOGGER = logging.getLogger(__name__)

#: Online payment rows in these states are abandoned checkouts, not payments.
_ABANDONED_TRANSACTION_STATUS = {"", "0"}


@dataclass
class HescomData:
    """Everything one refresh produced, already parsed and normalised."""

    account_id: str

    # Identity -----------------------------------------------------------
    name: str | None = None
    address: str | None = None
    phone: str | None = None
    email: str | None = None
    subdivision: str | None = None

    # Connection ---------------------------------------------------------
    account_status: str | None = None
    service_agreement_id: str | None = None
    connection_type: str | None = None
    tariff: str | None = None
    load_kw: float | None = None
    phase: str | None = None
    meter_type: str | None = None
    meter_serial: str | None = None
    bill_cycle: str | None = None
    legacy_rr_no: str | None = None

    # Money --------------------------------------------------------------
    due_amount: float | None = None
    payoff_balance: float | None = None
    bill_due_date: date | None = None
    alert_info: str | None = None
    bill_id: str | None = None

    # Readings -----------------------------------------------------------
    last_bill_read: date | None = None
    current_reading: float | None = None
    previous_reading: float | None = None
    usage: float | None = None
    reading_unit: str | None = None
    reading_start: datetime | None = None
    reading_end: datetime | None = None

    # History ------------------------------------------------------------
    payments: list[dict[str, Any]] = field(default_factory=list)
    complaints: list[dict[str, Any]] = field(default_factory=list)

    # Bill document ------------------------------------------------------
    pdf_url: str | None = None
    pdf_path: str | None = None

    # Bookkeeping --------------------------------------------------------
    last_success: datetime | None = None
    last_error: str | None = None
    failures: list[str] = field(default_factory=list)

    @property
    def is_active(self) -> bool | None:
        """``True`` when the connection is live, ``None`` when unknown."""
        if not self.account_status:
            return None
        return self.account_status.strip().upper() == "ACTIVE"

    @property
    def has_dues(self) -> bool:
        return bool(self.due_amount and self.due_amount > 0)

    @property
    def last_payment(self) -> dict[str, Any] | None:
        return self.payments[0] if self.payments else None


class HescomCoordinator(DataUpdateCoordinator[HescomData]):
    """Polls the HESCOM API and holds the last good value of every field."""

    config_entry: ConfigEntry

    def __init__(self, hass: HomeAssistant, entry: ConfigEntry, api: HescomApi) -> None:
        self.entry = entry
        self.api = api
        self.account_id = api.account_id
        self._options: dict[str, Any] = {**entry.data, **entry.options}
        self._last_good: dict[str, Any] = {}
        self._pdf_attempts: dict[str, datetime] = {}
        self._edit_lock = asyncio.Lock()

        self._store: Store[dict[str, Any]] = Store(
            hass, STORAGE_VERSION, f"{STORAGE_KEY}.{entry.entry_id}"
        )
        self._baseline_bill_id: str | None = None
        self._store_loaded = False

        super().__init__(
            hass,
            _LOGGER,
            name=f"{DOMAIN} {self.account_id}",
            update_interval=timedelta(minutes=self._setting(CONF_SCAN_INTERVAL, DEFAULT_SCAN_INTERVAL)),
            config_entry=entry,
        )

    # ------------------------------------------------------------------
    # Options
    # ------------------------------------------------------------------
    def _setting(self, key: str, default: int) -> int:
        value = as_int(self._options.get(key))
        return value if value and value > 0 else default

    @property
    def archive_enabled(self) -> bool:
        value = self._options.get(CONF_ARCHIVE_PDFS)
        return DEFAULT_ARCHIVE_PDFS if value is None else bool(value)

    @property
    def baseline_bill_id(self) -> str | None:
        """The most recent bill seen when this integration was last refreshed."""
        return self._baseline_bill_id

    # ------------------------------------------------------------------
    # Lifecycle
    # ------------------------------------------------------------------
    async def async_setup(self) -> None:
        """Load the persisted bill baseline before the first refresh."""
        if self._store_loaded:
            return
        stored = await self._store.async_load()
        if isinstance(stored, dict):
            self._baseline_bill_id = as_text(stored.get("bill_id"))
        self._store_loaded = True

    async def _async_save_baseline(self) -> None:
        try:
            await self._store.async_save({"bill_id": self._baseline_bill_id})
        except (OSError, ValueError) as err:
            _LOGGER.warning("Could not persist the HESCOM bill baseline: %s", err)

    async def async_force_refresh(self) -> None:
        """Refresh immediately, then restore the computed cadence."""
        await self.async_refresh()

    # ------------------------------------------------------------------
    # Refresh
    # ------------------------------------------------------------------
    async def _async_update_data(self) -> HescomData:
        await self.async_setup()

        # Wave 1: the profile.  Everything else can degrade, this cannot.
        try:
            entity = await self.api.async_get_customer_entity()
        except HescomAuthError as err:
            # Reauth is impossible (there is no login), but this still pauses
            # polling and surfaces the problem in the UI instead of retrying
            # a rejected key every cycle.
            raise ConfigEntryAuthFailed(
                f"HESCOM rejected the service key: {err}"
            ) from err
        except HescomApiError as err:
            raise UpdateFailed(f"Could not read the HESCOM account: {err}") from err

        person_id = as_text(entity.get("perId"))

        # Wave 2: optional detail endpoints, concurrently.
        names = (
            "account_status",
            "enquiry",
            "payments",
            "online_transactions",
            "complaints",
            "reads",
        )
        results = await asyncio.gather(
            self.api.async_get_account_status(),
            self.api.async_enquire_consumer_details(),
            self.api.async_get_payments(),
            self.api.async_get_online_transactions(person_id or ""),
            self.api.async_get_complaints(person_id or ""),
            self.api.async_get_reads(),
            return_exceptions=True,
        )
        settled: dict[str, Any] = {}
        failures: list[str] = []
        for name, result in zip(names, results):
            if isinstance(result, BaseException):
                failures.append(name)
                _LOGGER.debug("HESCOM %s call failed: %s", name, result)
                settled[name] = None
            else:
                settled[name] = result

        enquiry = settled.get("enquiry") or {}
        bill_id = as_text(enquiry.get("lastBillId")) or self._remember("bill_id")

        # Wave 3: readings for the current bill.
        readings: dict[str, Any] = {}
        if bill_id:
            try:
                readings = await self.api.async_get_meter_details(bill_id)
            except HescomApiError as err:
                failures.append("meter_details")
                _LOGGER.debug("HESCOM meter readings call failed: %s", err)
        if not readings:
            readings = self._remember("readings") or {}

        data = self._build(entity, enquiry, settled, readings, bill_id, failures)

        self.update_interval = timedelta(minutes=self._next_interval(data))
        _LOGGER.debug(
            "HESCOM %s next poll in %s minutes", self.account_id, self.update_interval
        )

        await self._handle_new_bill(data)
        await self._maybe_archive(data)
        return data

    # ------------------------------------------------------------------
    # Shaping
    # ------------------------------------------------------------------
    def _build(
        self,
        entity: dict[str, Any],
        enquiry: dict[str, Any],
        settled: dict[str, Any],
        readings: dict[str, Any],
        bill_id: str | None,
        failures: list[str],
    ) -> HescomData:
        status = settled.get("account_status") or self._remember("account_status") or {}
        reads = settled.get("reads") or self._remember("reads") or {}
        history = settled.get("payments")
        online = settled.get("online_transactions")
        complaints = settled.get("complaints")

        if history is None:
            history = self._remember("history") or []
        if online is None:
            online = self._remember("online") or []
        if complaints is None:
            complaints = self._remember("complaints") or []

        data = HescomData(
            account_id=self.account_id,
            # Priority: profile, then enquiry, then the older SOAP snapshot.
            name=(
                as_text(entity.get("accountName"))
                or as_text(enquiry.get("consumerName"))
                or as_text(reads.get("consumerName"))
            ),
            address=(
                as_text(entity.get("serviceAddress"))
                or as_text(enquiry.get("address"))
            ),
            phone=as_text(entity.get("consumerPhone")),
            email=as_text(entity.get("consumerEmailId")),
            subdivision=as_text(entity.get("subdivisionName")),
            account_status=as_text(status.get("accountStatus")),
            service_agreement_id=as_text(status.get("serviceAgreementId")),
            connection_type=as_text(entity.get("accountType")),
            tariff=as_text(entity.get("tariff")) or as_text(reads.get("categoryName")),
            load_kw=as_float(entity.get("loadKw")) or as_float(reads.get("loadKW")),
            phase=as_text(entity.get("phase")),
            meter_type=as_text(entity.get("meterType")),
            meter_serial=(
                as_text(dig(enquiry, "meterDetails", "meterSerialNo"))
                or as_text(dig(reads, "meterBadgeDetails", "serialNbr"))
            ),
            bill_cycle=as_text(reads.get("billCycle")),
            legacy_rr_no=as_text(reads.get("legacyRRNo")),
            due_amount=as_float(entity.get("dueAmount")),
            payoff_balance=as_float(entity.get("payoffBalance")),
            bill_due_date=parse_date(entity.get("billDueDate")),
            alert_info=as_text(entity.get("alertInfo")),
            bill_id=bill_id,
            last_bill_read=(
                parse_date(enquiry.get("lastBillMeterReadDTTM"))
                or parse_date(dig(reads, "meterBadgeDetails", "lastBillMeterReadDTTM"))
                or self._remember("last_bill_read")
            ),
            current_reading=as_float(readings.get("CurrentReading")),
            previous_reading=as_float(readings.get("PreviousReading")),
            usage=as_float(readings.get("Usage")),
            reading_unit=as_text(readings.get("Unit")),
            reading_start=parse_datetime(readings.get("StartReadDateTime")),
            reading_end=parse_datetime(readings.get("EndReadDateTime")),
            payments=_merge_payments(history or [], online or []),
            complaints=sorted(
                (row for row in complaints or [] if isinstance(row, dict)),
                key=lambda row: parse_date(row.get("Date") or row.get("createdDate"))
                or date.min,
                reverse=True,
            ),
            last_success=dt_util.now(UTC),
            failures=failures,
        )

        data.pdf_path = str(bill_pdf_path(self.hass, bill_id)) if bill_id else None
        data.pdf_url = bill_pdf_url(bill_id) if bill_id else None

        self._last_good.update(
            {
                "bill_id": bill_id,
                "readings": readings,
                "account_status": status,
                "reads": reads,
                "history": history,
                "online": online,
                "complaints": complaints,
                "last_bill_read": data.last_bill_read,
            }
        )
        return data

    def _remember(self, key: str) -> Any:
        return self._last_good.get(key)

    # ------------------------------------------------------------------
    # Cadence
    # ------------------------------------------------------------------
    def _next_interval(self, data: HescomData) -> int:
        """Pick the poll interval for the cycle that follows this refresh."""
        normal = self._setting(CONF_SCAN_INTERVAL, DEFAULT_SCAN_INTERVAL)
        watch = self._setting(CONF_WATCH_INTERVAL, DEFAULT_WATCH_INTERVAL)
        delayed = self._setting(CONF_DELAY_INTERVAL, DEFAULT_DELAY_INTERVAL)

        # Money owed means the bill already exists: no reason to poll harder.
        if data.has_dues:
            return normal

        last_read = data.last_bill_read
        if last_read is None:
            return normal

        try:
            today = dt_util.now().date()
            after = self._setting(CONF_WATCH_AFTER_DAYS, DEFAULT_WATCH_AFTER_DAYS)
            span = self._setting(CONF_WATCH_MAX_DAYS, DEFAULT_WATCH_MAX_DAYS)
            window_start = last_read + timedelta(days=after)
            window_end = window_start + timedelta(days=span)
        except (TypeError, OverflowError):
            return normal

        if today < window_start:
            return normal
        if today <= window_end:
            return watch
        return delayed

    # ------------------------------------------------------------------
    # New-bill detection
    # ------------------------------------------------------------------
    async def _handle_new_bill(self, data: HescomData) -> None:
        bill_id = data.bill_id
        if not bill_id:
            return

        async with self._edit_lock:
            if self._baseline_bill_id is None:
                # First sighting: adopt silently, this is the baseline.
                self._baseline_bill_id = bill_id
                await self._async_save_baseline()
                _LOGGER.debug(
                    "Recorded bill %s as the HESCOM baseline for %s",
                    bill_id,
                    self.account_id,
                )
                return

            if bill_id == self._baseline_bill_id:
                return

            previous = self._baseline_bill_id
            self._baseline_bill_id = bill_id
            await self._async_save_baseline()

        _LOGGER.info(
            "New HESCOM bill for %s: %s (was %s)", self.account_id, bill_id, previous
        )
        self.hass.bus.async_fire(
            EVENT_NEW_BILL,
            {
                "account_id": self.account_id,
                "name": data.name,
                "bill_id": bill_id,
                "previous_bill_id": previous,
                "due_amount": data.due_amount,
                "bill_due_date": data.bill_due_date.isoformat()
                if data.bill_due_date
                else None,
                "last_bill_read": data.last_bill_read.isoformat()
                if data.last_bill_read
                else None,
                "pdf_url": data.pdf_url,
                "entry_id": self.entry.entry_id,
            },
        )

    # ------------------------------------------------------------------
    # PDF archive
    # ------------------------------------------------------------------
    async def _maybe_archive(self, data: HescomData) -> None:
        if not self.archive_enabled or not data.bill_id:
            return

        bill_id = data.bill_id
        path = bill_pdf_path(self.hass, bill_id)
        try:
            if await self.hass.async_add_executor_job(path.is_file):
                data.pdf_url = bill_pdf_url(bill_id)
                return
        except OSError:
            pass

        # Bills with no published PDF would otherwise be retried every cycle.
        last_try = self._pdf_attempts.get(bill_id)
        if last_try is not None and dt_util.now(UTC) - last_try < PDF_RETRY_INTERVAL:
            return
        self._pdf_attempts[bill_id] = dt_util.now(UTC)

        url = await async_archive_bill(self.hass, self.api, bill_id)
        if url:
            data.pdf_url = url

    async def async_archive_now(self, *, force: bool = False) -> str | None:
        """Archive the current bill, bypassing the retry cooldown."""
        bill_id = self.data.bill_id if self.data else self._remember("bill_id")
        if not bill_id:
            return None
        url = await async_archive_bill(self.hass, self.api, bill_id, force=force)
        if url and self.data:
            self.data.pdf_url = url
            self.async_update_listeners()
        return url


def _merge_payments(
    history: list[dict[str, Any]], online: list[dict[str, Any]]
) -> list[dict[str, Any]]:
    """Fold the ledger and the gateway log into one payment list.

    The gateway record wins on a shared event id because it carries the
    transaction reference and the real timestamp; the ledger is authoritative
    for older payments that never went through the online gateway.
    """
    merged: dict[str, dict[str, Any]] = {}

    for row in history:
        if not isinstance(row, dict):
            continue
        event_id = as_text(row.get("PaymentEventID") or row.get("paymentEventId"))
        if not event_id:
            continue
        merged[event_id] = {
            "event_id": event_id,
            "amount": as_float(row.get("PaymentAmount")),
            "date": parse_datetime(row.get("PaymentDate")),
            "status": as_text(row.get("PaymentStatus")),
            "description": as_text(row.get("Description")),
            "transaction_id": as_text(row.get("transactionId")),
            "source": "ledger",
        }

    for row in online:
        if not isinstance(row, dict):
            continue
        event_id = as_text(row.get("paymentEventId"))
        if not event_id:
            continue
        transaction_status = as_text(row.get("transactionStatus")) or ""
        if transaction_status in _ABANDONED_TRANSACTION_STATUS:
            continue
        merged[event_id] = {
            "event_id": event_id,
            "amount": as_float(row.get("paymentAmount")),
            "date": parse_datetime(row.get("paymentDate")),
            "status": as_text(row.get("paymentStatus")),
            "description": as_text(row.get("pgErrorMsg") or row.get("pgErrorDetail")),
            "transaction_id": as_text(row.get("transactionId")),
            "source": "online",
        }

    def sort_key(row: dict[str, Any]) -> datetime:
        value = row.get("date")
        if value is None:
            return datetime.min
        # Mixed aware/naive timestamps would raise on comparison.
        return value.replace(tzinfo=None) if value.tzinfo else value

    return sorted(merged.values(), key=sort_key, reverse=True)


__all__ = ["HescomCoordinator", "HescomData"]
