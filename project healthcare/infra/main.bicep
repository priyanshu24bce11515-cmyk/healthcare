// P63 Preventive Care Platform — resources from docs/BLUEPRINT.md Part 3.
// One resource group, one region. AD B2C tenant is created separately in the
// portal (not a deployable Bicep resource) — see docs/BLUEPRINT.md Part 1.
// Azure Personalizer is retired and intentionally NOT provisioned here.

@description('Short project prefix used to build resource names')
param namePrefix string = 'p63health'

@description('Region for all resources')
param location string = resourceGroup().location

@description('Region for the Static Web App — Azure Static Web Apps is not offered in every region (notably not East US); defaults to the nearest supported region (East US 2) while every other resource stays on `location`')
param staticWebAppLocation string = 'eastus2'

@description('Azure SQL administrator login (set a strong password via the deployment parameter, not committed to source)')
param sqlAdminLogin string = 'p63admin'

@secure()
@description('Azure SQL administrator password')
param sqlAdminPassword string

@description('Deploy API Management (Consumption). Skippable for the MVP — see Part 9.')
param deployApim bool = false

@description('Deploy IoT Hub (Free F1) for realistic wearable ingestion. Optional.')
param deployIotHub bool = false

@description('Object ID of the Microsoft Entra user/group to set as this SQL server\'s Azure AD admin (required for any Microsoft Entra/managed-identity authentication, including the Function App\'s own connection — see infra/README.md). Leave empty to skip AAD admin setup entirely (SQL admin/password login still works either way).')
param sqlAadAdminObjectId string = ''

@description('Display name for the Microsoft Entra admin set on the SQL server (a user\'s UPN or a group\'s display name) — cosmetic, shown in the portal. Required if sqlAadAdminObjectId is set.')
param sqlAadAdminLogin string = ''

var sqlAadAdminConfigured = !empty(sqlAadAdminObjectId) && !empty(sqlAadAdminLogin)

@description('Microsoft Entra External ID tenant subdomain (without .onmicrosoft.com / .ciamlogin.com), e.g. "p63health". Documented substitution for the brief\'s "AD B2C" (classic B2C closed to new customers since May 2025). Leave empty until the tenant + app registrations are created (manual portal step) — until then the Function App keeps accepting anonymous requests, which is why ALLOW_DEMO_PRINCIPAL must stay unset here in the meantime.')
param externalIdTenantSubdomain string = ''

@description('Microsoft Entra External ID tenant ID (GUID) — shown on the tenant\'s Overview page once created')
param externalIdTenantId string = ''

@description('Entra External ID Web API app registration client ID (the app that exposes the API scope the SPA requests)')
param externalIdApiClientId string = ''

@description('Entra External ID SPA (public client) app registration client ID — the token-requesting app allowed to call this API')
param externalIdSpaClientId string = ''

var externalIdConfigured = !empty(externalIdTenantSubdomain) && !empty(externalIdTenantId) && !empty(externalIdApiClientId) && !empty(externalIdSpaClientId)

var uniqueSuffix = uniqueString(resourceGroup().id)
var storageName = toLower('${namePrefix}st${uniqueSuffix}')
var functionAppName = '${namePrefix}-func-${uniqueSuffix}'
var appServicePlanName = '${namePrefix}-plan-${uniqueSuffix}'
var appInsightsName = '${namePrefix}-appi-${uniqueSuffix}'
var logAnalyticsName = '${namePrefix}-log-${uniqueSuffix}'
var sqlServerName = '${namePrefix}-sql-${uniqueSuffix}'
var sqlDbName = '${namePrefix}db'
var languageName = '${namePrefix}-lang-${uniqueSuffix}'
var keyVaultName = toLower('${namePrefix}kv${uniqueSuffix}')
var staticWebAppName = '${namePrefix}-web-${uniqueSuffix}'
var commsServiceName = '${namePrefix}-acs-${uniqueSuffix}'
var apimName = '${namePrefix}-apim-${uniqueSuffix}'
var iotHubName = '${namePrefix}-iot-${uniqueSuffix}'

// ---------- Storage (required by Functions) ----------
resource storage 'Microsoft.Storage/storageAccounts@2023-01-01' = {
  name: storageName
  location: location
  sku: { name: 'Standard_LRS' }
  kind: 'StorageV2'
  properties: {
    minimumTlsVersion: 'TLS1_2'
    allowBlobPublicAccess: false
  }
}

// ---------- Monitoring ----------
resource logAnalytics 'Microsoft.OperationalInsights/workspaces@2023-09-01' = {
  name: logAnalyticsName
  location: location
  properties: {
    sku: { name: 'PerGB2018' }
    retentionInDays: 30
  }
}

resource appInsights 'Microsoft.Insights/components@2020-02-02' = {
  name: appInsightsName
  location: location
  kind: 'web'
  properties: {
    Application_Type: 'web'
    WorkspaceResourceId: logAnalytics.id
  }
}

// ---------- Key Vault ----------
resource keyVault 'Microsoft.KeyVault/vaults@2023-07-01' = {
  name: keyVaultName
  location: location
  properties: {
    sku: { family: 'A', name: 'standard' }
    tenantId: subscription().tenantId
    enableRbacAuthorization: true
  }
}

resource keyVaultDiagnostics 'Microsoft.Insights/diagnosticSettings@2021-05-01-preview' = {
  scope: keyVault
  name: 'keyvault-to-log-analytics'
  properties: {
    workspaceId: logAnalytics.id
    logs: [
      { category: 'AuditEvent', enabled: true }
    ]
    metrics: [
      { category: 'AllMetrics', enabled: true }
    ]
  }
}

// ---------- Azure SQL (Basic) ----------
resource sqlServer 'Microsoft.Sql/servers@2023-08-01' = {
  name: sqlServerName
  location: location
  properties: {
    administratorLogin: sqlAdminLogin
    administratorLoginPassword: sqlAdminPassword
    minimalTlsVersion: '1.2'
  }
}

resource sqlDb 'Microsoft.Sql/servers/databases@2023-08-01' = {
  parent: sqlServer
  name: sqlDbName
  location: location
  sku: { name: 'Basic', tier: 'Basic' }
  properties: {
    maxSizeBytes: 2147483648
  }
}

// HIPAA-style control: every query against the PHI database is logged,
// independent of and in addition to this app's own AuditLog table (which
// only records application-level "who viewed what" events, not raw SQL).
resource sqlDbDiagnostics 'Microsoft.Insights/diagnosticSettings@2021-05-01-preview' = {
  scope: sqlDb
  name: 'sql-to-log-analytics'
  properties: {
    workspaceId: logAnalytics.id
    logs: [
      { category: 'SQLInsights', enabled: true }
      { category: 'Errors', enabled: true }
      { category: 'DatabaseWaitStatistics', enabled: true }
      { category: 'Timeouts', enabled: true }
      { category: 'Blocks', enabled: true }
      { category: 'Deadlocks', enabled: true }
    ]
    metrics: [
      { category: 'Basic', enabled: true }
    ]
  }
}

resource sqlFirewallAllowAzure 'Microsoft.Sql/servers/firewallRules@2023-08-01' = {
  parent: sqlServer
  name: 'AllowAzureServices'
  properties: {
    startIpAddress: '0.0.0.0'
    endIpAddress: '0.0.0.0'
  }
}

// Required before ANY Microsoft Entra/managed-identity SQL auth works,
// including the Function App's own connection (SQL_CONNECTION_STRING below
// uses Authentication=ActiveDirectoryMsi). Setting this admin does NOT by
// itself grant the Function App access — that additionally needs a
// `CREATE USER [...] FROM EXTERNAL PROVIDER` run once inside the database
// by whoever this admin is (see infra/README.md and infra/sql/grant_managed_identity.sql
// for the exact one-time script) — that step is T-SQL, not an ARM/Bicep resource.
resource sqlAadAdmin 'Microsoft.Sql/servers/administrators@2023-08-01' = if (sqlAadAdminConfigured) {
  parent: sqlServer
  name: 'ActiveDirectory'
  properties: {
    administratorType: 'ActiveDirectory'
    login: sqlAadAdminLogin
    sid: sqlAadAdminObjectId
    tenantId: subscription().tenantId
  }
}

// ---------- AI Language for Health (F0) ----------
resource language 'Microsoft.CognitiveServices/accounts@2023-05-01' = {
  name: languageName
  location: location
  kind: 'TextAnalytics'
  sku: { name: 'F0' }
  properties: {
    customSubDomainName: languageName
    publicNetworkAccess: 'Enabled'
  }
}

// ---------- Communication Services (reminders/alerts email) ----------
resource comms 'Microsoft.Communication/communicationServices@2023-04-01' = {
  name: commsServiceName
  location: 'global'
  properties: {
    dataLocation: 'United States'
  }
}

// ---------- Function App (Consumption, Python) ----------
resource appServicePlan 'Microsoft.Web/serverfarms@2023-01-01' = {
  name: appServicePlanName
  location: location
  sku: { name: 'Y1', tier: 'Dynamic' }
  properties: {
    reserved: true // Linux, required for Python Functions
  }
}

resource functionApp 'Microsoft.Web/sites@2023-01-01' = {
  name: functionAppName
  location: location
  kind: 'functionapp,linux'
  identity: {
    type: 'SystemAssigned'
  }
  properties: {
    serverFarmId: appServicePlan.id
    httpsOnly: true
    siteConfig: {
      linuxFxVersion: 'Python|3.11'
      // The SPA calls this Function App directly (no linkedBackends/reverse
      // proxy configured — see infra/README.md), so it's cross-origin and
      // needs CORS. Local dev doesn't hit this: Vite's dev proxy is same-origin.
      cors: {
        allowedOrigins: [
          'http://localhost:5173'
          'https://${staticWebApp.properties.defaultHostname}'
        ]
      }
      appSettings: [
        { name: 'AzureWebJobsStorage', value: 'DefaultEndpointsProtocol=https;AccountName=${storage.name};AccountKey=${storage.listKeys().keys[0].value};EndpointSuffix=${environment().suffixes.storage}' }
        { name: 'FUNCTIONS_EXTENSION_VERSION', value: '~4' }
        { name: 'FUNCTIONS_WORKER_RUNTIME', value: 'python' }
        // Deliberately explicit rather than relying on config.py's default:
        // this is what hard-blocks the dev-only demo-auth escape hatches
        // (shared/auth.py::get_principal) regardless of whether
        // ALLOW_DEMO_PRINCIPAL ever gets set here by mistake.
        { name: 'ENVIRONMENT', value: 'production' }
        { name: 'APPLICATIONINSIGHTS_CONNECTION_STRING', value: appInsights.properties.ConnectionString }
        { name: 'SQL_CONNECTION_STRING', value: 'Driver={ODBC Driver 18 for SQL Server};Server=tcp:${sqlServer.properties.fullyQualifiedDomainName},1433;Database=${sqlDbName};Authentication=ActiveDirectoryMsi;' }
        { name: 'LANGUAGE_ENDPOINT', value: language.properties.endpoint }
        { name: 'KEYVAULT_URI', value: keyVault.properties.vaultUri }
        { name: 'COMMS_CONNECTION_STRING', value: 'endpoint=https://${comms.properties.hostName}/' }
        { name: 'USE_AOAI_RECOMMENDATIONS', value: 'false' }
      ]
    }
  }
}

resource functionAppDiagnostics 'Microsoft.Insights/diagnosticSettings@2021-05-01-preview' = {
  scope: functionApp
  name: 'functionapp-to-log-analytics'
  properties: {
    workspaceId: logAnalytics.id
    logs: [
      { category: 'FunctionAppLogs', enabled: true }
    ]
    metrics: [
      { category: 'AllMetrics', enabled: true }
    ]
  }
}

// ---------- Function App authentication (EasyAuth v2 against Entra External ID) ----------
// A no-op (AllowAnonymous) until the four externalId* parameters above are
// filled in, so this is safe to deploy before the tenant exists. Once set,
// this is what actually stops the Function App from accepting direct
// anonymous calls — the x-ms-client-principal header shared/auth.py trusts
// is only trustworthy once this is turned on. registration.clientId is this
// API's own app registration (validates the token's audience); the SPA's
// client ID goes in allowedApplications (the calling app allowed to invoke
// this API), not registration.clientId — these are two different apps.
resource functionAppAuth 'Microsoft.Web/sites/config@2023-01-01' = {
  parent: functionApp
  name: 'authsettingsV2'
  properties: {
    platform: {
      enabled: externalIdConfigured
    }
    globalValidation: {
      unauthenticatedClientAction: externalIdConfigured ? 'Return401' : 'AllowAnonymous'
      redirectToProvider: externalIdConfigured ? 'azureActiveDirectory' : null
    }
    identityProviders: {
      azureActiveDirectory: externalIdConfigured ? {
        enabled: true
        registration: {
          openIdIssuer: 'https://${externalIdTenantSubdomain}.ciamlogin.com/${externalIdTenantId}/v2.0'
          clientId: externalIdApiClientId
        }
        validation: {
          defaultAuthorizationPolicy: {
            allowedApplications: [
              externalIdSpaClientId
            ]
          }
        }
      } : {
        enabled: false
      }
    }
  }
}

// Function App identity gets least-privilege roles (Part 10 checklist).
resource kvSecretsUserRole 'Microsoft.Authorization/roleAssignments@2022-04-01' = {
  name: guid(keyVault.id, functionApp.id, 'KeyVaultSecretsUser')
  scope: keyVault
  properties: {
    principalId: functionApp.identity.principalId
    principalType: 'ServicePrincipal'
    roleDefinitionId: subscriptionResourceId('Microsoft.Authorization/roleDefinitions', '4633458b-17de-408a-b874-0445c86b69e6') // Key Vault Secrets User
  }
}

resource languageUserRole 'Microsoft.Authorization/roleAssignments@2022-04-01' = {
  name: guid(language.id, functionApp.id, 'CognitiveServicesUser')
  scope: language
  properties: {
    principalId: functionApp.identity.principalId
    principalType: 'ServicePrincipal'
    roleDefinitionId: subscriptionResourceId('Microsoft.Authorization/roleDefinitions', 'a97b65f3-24c7-4388-baec-2e87135dc908') // Cognitive Services User
  }
}

// ---------- Static Web App (front-end) ----------
resource staticWebApp 'Microsoft.Web/staticSites@2023-01-01' = {
  name: staticWebAppName
  location: staticWebAppLocation
  sku: { name: 'Free', tier: 'Free' }
  properties: {
    provider: 'None' // wire up GitHub/DevOps repo post-deploy, or via CI/CD (Part 16 step 14)
  }
}

// ---------- Optional: API Management (Consumption) ----------
resource apim 'Microsoft.ApiManagement/service@2023-05-01-preview' = if (deployApim) {
  name: apimName
  location: location
  sku: { name: 'Consumption', capacity: 0 }
  properties: {
    publisherName: 'P63 Preventive Care Team'
    publisherEmail: 'admin@example.com'
  }
}

// ---------- Optional: IoT Hub (Free F1) ----------
resource iotHub 'Microsoft.Devices/IotHubs@2023-06-30' = if (deployIotHub) {
  name: iotHubName
  location: location
  sku: { name: 'F1', capacity: 1 }
}

output functionAppName string = functionApp.name
output functionAppDefaultHostName string = functionApp.properties.defaultHostName
output functionAppPrincipalId string = functionApp.identity.principalId
output sqlServerName string = sqlServer.name
output sqlServerFqdn string = sqlServer.properties.fullyQualifiedDomainName
output sqlDatabaseName string = sqlDbName
output languageEndpoint string = language.properties.endpoint
output keyVaultUri string = keyVault.properties.vaultUri
output keyVaultName string = keyVault.name
output staticWebAppName string = staticWebApp.name
output staticWebAppDefaultHostname string = staticWebApp.properties.defaultHostname
output storageAccountName string = storage.name
output logAnalyticsWorkspaceId string = logAnalytics.id
output appInsightsName string = appInsights.name
output commsServiceName string = comms.name
output apimGatewayUrl string = deployApim ? (apim.?properties.?gatewayUrl ?? '') : ''
