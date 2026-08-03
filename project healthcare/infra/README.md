# Infrastructure — deploying the Azure resources

This deploys everything in [docs/BLUEPRINT.md](../docs/BLUEPRINT.md) Part 3 except the identity tenant (created separately in the portal — not a deployable Bicep resource type) and Azure Personalizer (retired — not used).

**Identity provider note:** the brief/blueprint name "Azure AD B2C," but classic B2C has been closed to new customers since May 2025. This project uses **Microsoft Entra External ID** instead — Microsoft's actively-developed successor, using the exact same confirmed-working `authsettingsV2`/EasyAuth bearer-token pattern (a documented substitution, same style as the existing Azure Personalizer swap).

## Prerequisites
- Signed in with `az login`, on the intended subscription (`az account show`)
- A resource group already created (Part 1.4): `az group create -n rg-health-p63 -l eastus`
- A **budget + cost alert** already set (Part 1.5) — do this before deploying anything

## Deploy

1. Copy `main.parameters.example.json` to `main.parameters.json` and fill in a real SQL admin password (never commit this file with a real password). Optionally also fill in `sqlAadAdminObjectId`/`sqlAadAdminLogin` (your own account's or a group's Microsoft Entra object ID — `az ad signed-in-user show --query id`) so the template sets a Microsoft Entra admin on the SQL server; leave both empty to skip that (SQL admin/password login still works either way, but the Function App's managed-identity connection won't until this is set — see step 4 below).
2. Validate, then deploy — either directly:

```bash
az deployment group validate \
  -g rg-health-p63 \
  -f infra/main.bicep \
  -p infra/main.parameters.json

az deployment group create \
  -g rg-health-p63 \
  -f infra/main.bicep \
  -p infra/main.parameters.json
```

   or via the wrapper script, which also prints the manual steps below automatically after deploying:

```bash
infra/deploy.sh rg-health-p63
```

3. Note the outputs (`functionAppName`, `sqlServerFqdn`, `languageEndpoint`, `keyVaultUri`, `staticWebAppDefaultHostname`, and others — see `main.bicep`'s `output` section for the full list). Update `functions/local.settings.json` (copy from `local.settings.json.example`) and `web/.env` (copy from `.env.example`) with the real values for local dev against the deployed resources, or leave `local.settings.json` pointing at local emulators for pure local dev.
4. **Grant the Function App's managed identity database access** — this is a separate step from setting the SQL Entra admin in step 1, and is required for the Function App to connect at all (`SQL_CONNECTION_STRING` uses `Authentication=ActiveDirectoryMsi`): connect to the new database *as the Microsoft Entra admin set in step 1* (e.g. via `sqlcmd` with Entra auth, or the portal's Query Editor) and run `infra/sql/grant_managed_identity.sql`, replacing `<FUNCTION_APP_NAME>` with the `functionAppName` output. Then run `infra/sql/schema.sql` against the same database, then `python scripts/seed_data.py --reset`.
5. `deployApim` and `deployIotHub` default to `false` — flip them to `true` in your parameters file once the MVP works (Part 12, Phase 2).
6. The template sets `ENVIRONMENT=production` on the deployed Function App unconditionally — this is what hard-blocks the dev-only demo-auth escape hatches (`ALLOW_DEMO_PRINCIPAL`, the unverified-bearer path) regardless of any other app setting. There is deliberately no parameter to turn this off; a non-production deployment should just be a separate resource group, not a flag.

## What's deliberately manual
- **Entra External ID tenant + app registrations + user flow** — portal only. Steps: (1) create an "External configuration" tenant, note its subdomain + tenant ID; (2) register a Web API app, expose a scope, note its client ID; (3) register a SPA (public client) app with redirect URIs for `http://localhost:5173` and the deployed Static Web App hostname, grant it the API's scope, note its client ID; (4) create a sign-up/sign-in user flow and associate the SPA app with it. Once you have all four values, set `externalIdTenantSubdomain`, `externalIdTenantId`, `externalIdApiClientId`, and `externalIdSpaClientId` in your parameters file and redeploy — this turns on the Function App's native authentication (`authsettingsV2` in `main.bicep`), which is what actually rejects anonymous requests and produces a trustworthy `x-ms-client-principal`. Until all four are set, the Function App still accepts anonymous requests, so keep `ALLOW_DEMO_PRINCIPAL` unset in every deployed environment (it's not in `main.bicep`'s app settings, so it defaults to off).
- **First-provider bootstrap on a real (non-seeded) deployment** — `POST /providers` requires an existing provider (chicken-and-egg). On seeded/demo data this is a non-issue (`scripts/seed_data.py` creates unclaimed provider rows to claim via `POST /providers/claim`). On a fresh real deployment with no seed data, run one manual `INSERT INTO Providers (...)` (or a one-off script using `shared.db` directly) before anyone signs in, the same way the identity tenant itself is a manual one-time step.
- **Azure OpenAI resource** — request access and deploy a chat model when you reach Phase 3; then set `AZURE_OPENAI_*` app settings and `USE_AOAI_RECOMMENDATIONS=true`.
- **Static Web App ↔ GitHub connection** — either set `properties.repositoryUrl`/`branch`/`repositoryToken` on the `staticSites` resource, or connect it once via the portal/CLI (`az staticwebapp create` with `--source`), or let the GitHub Actions workflow in `.github/workflows/deploy.yml` handle deploys instead.
- **EasyAuth reliability watch-item**: this configuration has not yet been deployed/tested live. If it proves flaky on the current `Y1` (Linux Consumption) plan, moving `appServicePlan`/`functionApp` to `FC1` (Flex Consumption) is a contained, mechanical bicep change — Microsoft's current default recommendation for new Python Function Apps.
- **Wearable/EHR integration credentials** (`functions/integrations/wearables.py`, `functions/integrations/ehr_fhir.py`) — deliberately not in `main.bicep`'s app settings, since every one of `FITBIT_CLIENT_ID`/`FITBIT_CLIENT_SECRET`/`FITBIT_REDIRECT_URI`/`FITBIT_VERIFICATION_CODE`/`GOOGLE_FIT_*`/`EHR_FHIR_BASE_URL`/`EHR_CLIENT_ID`/`EHR_CLIENT_SECRET`/`PHI_ENCRYPTION_KEY` is optional and the code no-ops cleanly without it. Once you register a real developer app with a vendor (or stand up/connect to a real FHIR server), set these via `az functionapp config appsettings set` (ideally sourced from Key Vault references, not plaintext app settings, given `PHI_ENCRYPTION_KEY` in particular is a real secret).

## Monitoring
Application Insights (wired via `APPLICATIONINSIGHTS_CONNECTION_STRING`) captures Function App request/dependency/exception telemetry automatically — no extra setup needed. In addition, `main.bicep` sends diagnostic logs from the Function App, the SQL database, and Key Vault to the shared Log Analytics workspace (`logAnalyticsWorkspaceId` output) — SQL query/error/deadlock telemetry and Key Vault `AuditEvent` logs are a HIPAA-relevant control on top of this app's own `AuditLog` table, since `AuditLog` only records application-level "who viewed what," not raw database access.
