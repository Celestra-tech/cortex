# Northwind Information Security Policy

Version 5.0. Owner: Security Engineering.

## Access control

Access follows least privilege. Production access requires a hardware security
key and is granted for a maximum of 8 hours per request through the access
portal. Access reviews run every quarter; unused access is removed.

## Credentials

API keys and service credentials live in the secret manager, never in source
control, tickets, or chat. Keys used by services must expire within 90 days and
are rotated with an overlap window so that deployments never break. A leaked
credential is revoked immediately and reported to security@northwind.example.

## Data classification

- Public: approved for anyone.
- Internal: employees and contractors under NDA.
- Confidential: customer data and financials; encrypted at rest and in transit.
- Restricted: credentials and key material; never leaves the secret manager.

## Incident response

Report suspected incidents within one hour of discovery. Security Engineering
triages every report within four hours and leads the post-incident review,
which is published internally within five business days.
