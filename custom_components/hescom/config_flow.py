"""Config and options flow for the HESCOM integration."""

from __future__ import annotations

import logging
from typing import Any

import voluptuous as vol

from homeassistant.config_entries import ConfigEntry, ConfigFlow, ConfigFlowResult, OptionsFlow
from homeassistant.core import callback
from homeassistant.helpers import selector

from .api import HescomApi, HescomApiError, HescomAuthError
from .const import (
    CONF_ACCOUNT_ID,
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
    MAX_INTERVAL,
    MIN_INTERVAL,
    NAME,
)

_LOGGER = logging.getLogger(__name__)


def _minutes(minimum: int, maximum: int) -> selector.NumberSelector:
    return selector.NumberSelector(
        selector.NumberSelectorConfig(
            min=minimum,
            max=maximum,
            step=1,
            mode=selector.NumberSelectorMode.BOX,
            unit_of_measurement="min",
        )
    )


def _days(minimum: int, maximum: int) -> selector.NumberSelector:
    return selector.NumberSelector(
        selector.NumberSelectorConfig(
            min=minimum,
            max=maximum,
            step=1,
            mode=selector.NumberSelectorMode.BOX,
            unit_of_measurement="d",
        )
    )


class HescomConfigFlow(ConfigFlow, domain=DOMAIN):
    """Ask for the HESCOM account id and prove it works."""

    VERSION = 1

    async def async_step_user(
        self, user_input: dict[str, Any] | None = None
    ) -> ConfigFlowResult:
        errors: dict[str, str] = {}

        if user_input is not None:
            account_id = str(user_input[CONF_ACCOUNT_ID]).strip()
            if not account_id.isdigit():
                errors[CONF_ACCOUNT_ID] = "invalid_account"
            else:
                api = HescomApi(self.hass, account_id)
                try:
                    entity = await api.async_validate()
                except HescomAuthError:
                    errors["base"] = "invalid_auth"
                except HescomApiError as err:
                    _LOGGER.debug("HESCOM validation failed for %s: %s", account_id, err)
                    errors["base"] = "cannot_connect"
                except Exception:  # noqa: BLE001 - never leak a traceback to the form
                    _LOGGER.exception("Unexpected error validating HESCOM account %s", account_id)
                    errors["base"] = "unknown"
                else:
                    await self.async_set_unique_id(account_id)
                    self._abort_if_unique_id_configured()

                    title = NAME
                    name = entity.get("accountName")
                    if isinstance(name, str) and name.strip():
                        title = f"{NAME} {name.strip().title()}"
                    return self.async_create_entry(
                        title=title, data={CONF_ACCOUNT_ID: account_id}
                    )

        schema = vol.Schema(
            {
                vol.Required(CONF_ACCOUNT_ID): selector.TextSelector(
                    selector.TextSelectorConfig(
                        type=selector.TextSelectorType.TEXT,
                        autocomplete="off",
                    )
                )
            }
        )
        return self.async_show_form(
            step_id="user",
            data_schema=schema,
            errors=errors,
            description_placeholders={"portal": "hescom.co.in"},
        )

    @staticmethod
    @callback
    def async_get_options_flow(entry: ConfigEntry) -> OptionsFlow:
        return HescomOptionsFlow(entry)


class HescomOptionsFlow(OptionsFlow):
    """Tune refresh cadences, the bill-watch window and PDF archiving."""

    def __init__(self, entry: ConfigEntry | None = None) -> None:
        # Home Assistant injects ``config_entry`` itself on recent releases and
        # the attribute is read-only there; older releases need it passed in.
        if entry is not None:
            try:
                self.config_entry = entry  # type: ignore[misc]
            except AttributeError:
                pass

    async def async_step_init(
        self, user_input: dict[str, Any] | None = None
    ) -> ConfigFlowResult:
        if user_input is not None:
            return self.async_create_entry(title="", data=user_input)

        options = self.config_entry.options
        defaults: dict[str, Any] = {
            CONF_SCAN_INTERVAL: options.get(CONF_SCAN_INTERVAL, DEFAULT_SCAN_INTERVAL),
            CONF_WATCH_INTERVAL: options.get(CONF_WATCH_INTERVAL, DEFAULT_WATCH_INTERVAL),
            CONF_DELAY_INTERVAL: options.get(CONF_DELAY_INTERVAL, DEFAULT_DELAY_INTERVAL),
            CONF_WATCH_AFTER_DAYS: options.get(
                CONF_WATCH_AFTER_DAYS, DEFAULT_WATCH_AFTER_DAYS
            ),
            CONF_WATCH_MAX_DAYS: options.get(CONF_WATCH_MAX_DAYS, DEFAULT_WATCH_MAX_DAYS),
            CONF_ARCHIVE_PDFS: options.get(CONF_ARCHIVE_PDFS, DEFAULT_ARCHIVE_PDFS),
        }

        schema = vol.Schema(
            {
                vol.Required(
                    CONF_SCAN_INTERVAL, default=defaults[CONF_SCAN_INTERVAL]
                ): _minutes(MIN_INTERVAL, MAX_INTERVAL),
                vol.Required(
                    CONF_WATCH_INTERVAL, default=defaults[CONF_WATCH_INTERVAL]
                ): _minutes(MIN_INTERVAL, MAX_INTERVAL),
                vol.Required(
                    CONF_DELAY_INTERVAL, default=defaults[CONF_DELAY_INTERVAL]
                ): _minutes(MIN_INTERVAL, MAX_INTERVAL),
                vol.Required(
                    CONF_WATCH_AFTER_DAYS, default=defaults[CONF_WATCH_AFTER_DAYS]
                ): _days(1, 90),
                vol.Required(
                    CONF_WATCH_MAX_DAYS, default=defaults[CONF_WATCH_MAX_DAYS]
                ): _days(1, 60),
                vol.Required(
                    CONF_ARCHIVE_PDFS, default=defaults[CONF_ARCHIVE_PDFS]
                ): selector.BooleanSelector(),
            }
        )
        return self.async_show_form(step_id="init", data_schema=schema)


__all__ = ["HescomConfigFlow", "HescomOptionsFlow"]
