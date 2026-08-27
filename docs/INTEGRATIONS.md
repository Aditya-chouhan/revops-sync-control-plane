# Integration contract

## HubSpot

The preview maps the canonical record to a company `properties` object. Standard-like fields include `domain`, `name`, `industry`, `numberofemployees`, and `lifecyclestage`. The `gtm_*` fields are intentional custom-property placeholders and must exist in the target portal before live delivery.

The delivery boundary uses a private-app bearer token supplied at runtime. No OAuth flow, portal ID, private-app token, or successful write is included in the repository.

## Salesforce

The preview maps to Account fields. `Name`, `Website`, `Industry`, and `NumberOfEmployees` are standard-shaped. Fields ending in `__c` are explicit custom-field contracts and must be deployed in the target org before live delivery.

The REST API version is configuration, not a timeless claim. A real deployment should set `SALESFORCE_API_VERSION` to a version supported by its org.

## Loop prevention

Source precedence is field-specific. A Salesforce-owned field observed from HubSpot cannot overwrite the canonical Salesforce value merely because it arrived later. Conversely, HubSpot owns lifecycle and marketing-consent fields in this demonstration. Intended writes are fingerprinted from target, account, operation, and payload so identical canonical state cannot create another outbox row.

## Secrets and writes

`.env.example` contains empty placeholders only. Live delivery requires a global switch, provider switch, and provider credentials. The FastAPI router does not expose the delivery method.

This is a safe public integration boundary, not proof that Aditya has access to a private HubSpot portal or Salesforce production org.
