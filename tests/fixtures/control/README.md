Answers for the control tests (stage 10).

- `adminvps-modal-*.json` — the HostingRenewals modals of an AdminVPS service as the
  cabinet answered on 2026-11-08 to the read-only request its buttons send
  (`mgformtype=read`). Only opened, nothing confirmed. The service id and the domain are
  replaced, the CSRF token is masked.

Answers to the requests that change something (power, autorenewal, renewal, paying an
invoice) were not recorded: no live server could be used for that. The tests build them
inline, after the shapes the cabinets' own scripts expect. Each such test says so.
