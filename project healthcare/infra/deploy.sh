#!/usr/bin/env bash
# Deploys the P63 Bicep template to an existing resource group, then prints
# the exact manual steps still required afterward (things that are
# genuinely not ARM/Bicep resources — see infra/README.md "What's
# deliberately manual" for why each one can't be automated here).
#
# Usage:
#   infra/deploy.sh <resource-group> [parameters-file]
#
# Prerequisites: `az login` already done, on the target subscription
# (`az account set --subscription <id>`), and a resource group already
# created (`az group create -n <rg> -l eastus`).
set -euo pipefail

RESOURCE_GROUP="${1:?Usage: infra/deploy.sh <resource-group> [parameters-file]}"
PARAMS_FILE="${2:-$(dirname "$0")/main.parameters.json}"
BICEP_FILE="$(dirname "$0")/main.bicep"

if [ ! -f "$PARAMS_FILE" ]; then
  echo "Parameters file not found: $PARAMS_FILE" >&2
  echo "Copy infra/main.parameters.example.json to infra/main.parameters.json and fill in a real SQL admin password first." >&2
  exit 1
fi

echo "== Validating template =="
az deployment group validate \
  --resource-group "$RESOURCE_GROUP" \
  --template-file "$BICEP_FILE" \
  --parameters "@$PARAMS_FILE"

echo "== Deploying =="
az deployment group create \
  --resource-group "$RESOURCE_GROUP" \
  --template-file "$BICEP_FILE" \
  --parameters "@$PARAMS_FILE" \
  --name "p63-deploy-$(date +%Y%m%d%H%M%S)" \
  --output json > /tmp/p63-deploy-output.json

echo "== Outputs =="
python3 -c "
import json
with open('/tmp/p63-deploy-output.json') as f:
    d = json.load(f)
for k, v in d.get('properties', {}).get('outputs', {}).items():
    print(f'{k}: {v[\"value\"]}')
"

cat <<'EOF'

== Manual steps still required (see infra/README.md for full detail) ==
1. If you set sqlAadAdminObjectId/sqlAadAdminLogin, connect to the new database
   AS THAT ADMIN and run infra/sql/grant_managed_identity.sql (replace
   <FUNCTION_APP_NAME> with the functionAppName output above) — this is what
   actually lets the Function App's managed identity read/write the database.
2. Run infra/sql/schema.sql against the new database (same admin connection).
3. Run `python scripts/seed_data.py --reset` (or your own bootstrap) to populate
   demo data, or manually INSERT the first unclaimed Provider row for a real
   deployment.
4. Create the Microsoft Entra External ID tenant + app registrations (portal-only,
   see infra/README.md), then re-run this script with externalIdTenantSubdomain/
   externalIdTenantId/externalIdApiClientId/externalIdSpaClientId filled in —
   this turns on authsettingsV2 and is what actually rejects anonymous requests.
5. Copy the functionAppDefaultHostName / staticWebAppDefaultHostname outputs into
   web/.env (VITE_API_BASE_URL) and wire up the Static Web App's GitHub connection
   (or let .github/workflows/deploy.yml handle ongoing deploys once its secrets
   are set).
EOF
