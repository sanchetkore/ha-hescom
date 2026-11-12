"""Local archive for downloaded bill PDFs.

Bills are written to ``<config>/www/hescom/<billId>.pdf`` which Home Assistant
serves at ``/local/hescom/<billId>.pdf``.  Files are never rewritten: once a
bill has been archived it stays available even after the utility rotates the
bill off the portal.
"""

from __future__ import annotations

import logging
from pathlib import Path

from homeassistant.core import HomeAssistant

from .api import HescomApi, HescomApiError
from .const import DOMAIN, PDF_SUBDIR, PDF_URL_PREFIX

_LOGGER = logging.getLogger(__name__)


def archive_dir(hass: HomeAssistant) -> Path:
    """The directory holding archived bills, created on demand."""
    return Path(hass.config.path(*PDF_SUBDIR))


def bill_pdf_path(hass: HomeAssistant, bill_id: str) -> Path:
    """Absolute path of the archived PDF for ``bill_id``."""
    return archive_dir(hass) / f"{_safe_name(bill_id)}.pdf"


def bill_pdf_url(bill_id: str) -> str:
    """The ``/local/...`` URL Home Assistant serves for an archived bill."""
    return f"{PDF_URL_PREFIX}/{_safe_name(bill_id)}.pdf"


def _safe_name(bill_id: str) -> str:
    """Keep bill ids path-safe; they are numeric but never assume it."""
    cleaned = "".join(char for char in str(bill_id) if char.isalnum() or char in "-_")
    return cleaned or "bill"


def _is_file(path: Path) -> bool:
    return path.is_file()


def _write_pdf(path: Path, payload: bytes) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_bytes(payload)


async def async_archive_bill(
    hass: HomeAssistant,
    api: HescomApi,
    bill_id: str | None,
    *,
    force: bool = False,
) -> str | None:
    """Ensure the PDF for ``bill_id`` is on disk, and return its local URL.

    Returns ``None`` when the bill has no retrievable PDF.  Existing files are
    left untouched unless ``force`` is set, so repeat calls cost no network
    traffic.
    """
    if not bill_id:
        return None

    path = bill_pdf_path(hass, bill_id)
    if not force and await hass.async_add_executor_job(_is_file, path):
        return bill_pdf_url(bill_id)

    try:
        payload = await api.async_get_bill_pdf(bill_id)
    except HescomApiError as err:
        _LOGGER.warning("Could not download the PDF for bill %s: %s", bill_id, err)
        return None

    if not payload:
        _LOGGER.debug("Bill %s has no PDF available yet", bill_id)
        return None

    try:
        await hass.async_add_executor_job(_write_pdf, path, payload)
    except OSError as err:
        _LOGGER.error("Could not save the PDF for bill %s: %s", bill_id, err)
        return None

    _LOGGER.info("Archived bill %s to %s", bill_id, path)
    hass.data.setdefault(DOMAIN, {}).setdefault("archived", {})[bill_id] = str(path)
    return bill_pdf_url(bill_id)


__all__ = [
    "archive_dir",
    "async_archive_bill",
    "bill_pdf_path",
    "bill_pdf_url",
]
