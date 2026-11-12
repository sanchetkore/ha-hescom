# HESCOM for Home Assistant

A read-only Home Assistant integration for **HESCOM** (Hubli Electricity Supply
Company, Karnataka) consumer accounts. It polls the same public portal
(`hescom.co.in`) that the official web app uses and turns the result into
native entities: dues, due date, meter readings, payment history, complaints
and the bill PDF.

There is no login, no password and no scraping of private data — only the
consumer account number you already use on the portal.

> **Not affiliated with HESCOM.** This is an unofficial integration built
> against the public consumer portal. Availability depends entirely on that
> portal, and the API can change without notice.

---

## What you get

| Platform | Entity | Notes |
| --- | --- | --- |
| sensor | Due amount | ₹, with `bill_id`, `bill_due_date`, `alert` and `pdf_url` attributes |
| sensor | Payoff balance | ₹ |
| sensor | Bill due date | Date |
| sensor | Last payment amount / date | Merged ledger + payment gateway history |
| sensor | Account status | `ACTIVE`, `DISCONNECTED`, … with `service_agreement_id` |
| sensor | Sanctioned load | kW |
| sensor | Billed usage | kWh |
| sensor | Current / previous reading | kWh |
| sensor | Reading period start / end | Timestamps |
| sensor | Complaints / Open complaints | Counts, latest complaint as an attribute |
| sensor | Bill PDF | Local `/local/hescom/<bill>.pdf` URL |
| sensor | Tariff, Connection type, Meter serial, Bill number | Diagnostic |
| binary_sensor | Connection active | Connectivity |
| binary_sensor | Bill overdue | `problem`, on when money is owed past the printed due date |
| button | Refresh | Poll now |
| button | Download bill PDF | Fetch the PDF even if an earlier attempt failed |

All entities belong to a single `HESCOM <account number>` device, so entity ids
look like `sensor.hescom_1234567890_due_amount`.

## Installation

### HACS (recommended)

1. HACS → **Integrations** → ⋮ → **Custom repositories**.
2. Add `https://github.com/sanchetkore/ha-hescom`, category **Integration**.
3. Install **HESCOM**, restart Home Assistant.
4. **Settings → Devices & services → Add integration → HESCOM**.
5. Enter your consumer account number (digits only, e.g. `1234567890`).

### Manual

Copy `custom_components/hescom` into your Home Assistant `config` directory and
restart. Only `cryptography` is needed and Home Assistant already ships it.

## Refresh behaviour

The portal is polled on a cadence that depends on where you are in the billing
cycle. All three intervals are configurable in the integration options.

| Situation | Default interval |
| --- | --- |
| Nothing expected, nothing owed | 360 min |
| Inside the expected billing window | 60 min |
| Window passed, bill still missing | 180 min |

The expected window starts `25` days after the last meter reading reported by
HESCOM and lasts `12` days. If money is already due the normal interval is used
again, because the bill plainly exists.

### Manual refresh

- Press the **Refresh** button on the device.
- Or call the service:

```yaml
action: hescom.refresh
# data:
#   entry_id: <optional, refresh just one account>
```

### Bill generation delayed

If HESCOM is late, the integration switches to the slower "delayed" interval
instead of hammering the portal. Raise **Refresh interval when the bill is
late** if you want it even quieter, or lower it to keep checking.

## Options

**Settings → Devices & services → HESCOM → Configure**

| Option | Default | Meaning |
| --- | --- | --- |
| Normal refresh interval | 360 min | Ordinary polling |
| Refresh interval while a bill is expected | 60 min | Inside the billing window |
| Refresh interval when the bill is late | 180 min | Window passed, still no bill |
| Start watching after | 25 days | Measured from the last meter reading |
| Expected window length | 12 days | How long "on time" lasts |
| Save bill PDFs | on | Writes to `/config/www/hescom` |

Saving options reloads the integration.

## Bill PDFs

With archiving enabled, every new bill is downloaded once to
`/config/www/hescom/<bill id>.pdf` and exposed as:

```
/local/hescom/<bill id>.pdf
```

The file is cached: once it exists it is never fetched twice. A bill whose PDF
is not published yet is retried at most every six hours, so a missing document
does not cost a request on every poll. The **Download bill PDF** button forces
a fresh attempt.

## Events and blueprints

A new bill fires an event on the Home Assistant bus:

```yaml
event_type: hescom_new_bill
data:
  account_id: "1234567890"
  name: "SMT. ..."
  bill_id: "007929329141"
  previous_bill_id: "007928000001"
  due_amount: 0.0
  bill_due_date: "2026-11-20"
  last_bill_read: "2025-03-03"
  pdf_url: "/local/hescom/007929329141.pdf"
  entry_id: "<config entry id>"
```

The first refresh only records a baseline, so installing the integration does
not announce the bill you already have. The baseline is persisted and survives
restarts.

Two blueprints ship in `blueprints/automation/hescom/`:

- **HESCOM – new bill notification** — notify on `hescom_new_bill`.
- **HESCOM – bill due soon reminder** — daily reminder while a bill is unpaid
  and its due date is near.

Copy them to your `config/blueprints/automation/hescom/` directory, then create
an automation from the blueprint in the UI. Home Assistant does not discover
blueprints that live inside a custom component.

## Automation examples

Notify when a new bill appears:

```yaml
trigger:
  - platform: event
    event_type: hescom_new_bill
action:
  - action: notify.mobile_app_my_phone
    data:
      title: New HESCOM bill
      message: >-
        ₹{{ trigger.event.data.due_amount }} due on
        {{ trigger.event.data.bill_due_date }}.
```

Refresh before you leave the house:

```yaml
trigger:
  - platform: state
    entity_id: person.me
    to: "not_home"
action:
  - action: hescom.refresh
```

## Troubleshooting

**"HESCOM rejected the request signature"**
The portal rotates the request header when its frontend is redeployed. The
integration rediscovers it automatically (one 3.8 MB download, rate-limited to
once every five minutes). If it persists, the portal itself is probably down.

**Entities show as unavailable at setup**
HESCOM did not answer. Home Assistant retries with backoff. Partial failures
later do not blank the device: the last good value of each field is kept.

**No readings / usage is zero**
The portal only publishes readings for the bill it currently knows about. A
zero usage with identical current and previous readings means HESCOM has not
posted a new reading yet.

**PDF never appears**
Not every bill has a published PDF, and generation can lag the bill itself.
Check the `pdf_url` attribute of the **Bill PDF** sensor, then press
**Download bill PDF**.

## Privacy

- The integration never sends your credentials — there are none.
- Only the account number you configure is ever requested.
- No payment, complaint-creation, admin or email endpoints are used. Everything
  is read-only.
- Diagnostics redact name, address, phone, email and payment transaction ids.

## Scope

One config entry manages one consumer account. Add the integration again for a
second connection. EMI/payment actions, service request creation and account
enumeration are deliberately out of scope.

## License

MIT. See [LICENSE](LICENSE).
