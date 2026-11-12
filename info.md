# HESCOM

Read-only Home Assistant integration for HESCOM (Karnataka) electricity
accounts. Dues, due date, meter readings, payments, complaints and bill PDFs —
as native entities, with a refresh cadence that speeds up while a new bill is
expected and slows down when billing runs late.

**Highlights**

- Native sensors, binary sensors and buttons, one device per account
- Bill PDFs archived to `/config/www/hescom` and served at `/local/hescom/`
- `hescom_new_bill` event plus two ready-made automation blueprints
- Automatic recovery when HESCOM rotates its request signature
- Read-only: no payments, no service requests, no credentials

See the [README](README.md) for installation, options and troubleshooting.
