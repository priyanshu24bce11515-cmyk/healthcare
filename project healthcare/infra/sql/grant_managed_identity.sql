-- One-time, post-deployment step: grants the Function App's system-assigned
-- managed identity permission to read/write the database. This is NOT an ARM/
-- Bicep resource — CREATE USER ... FROM EXTERNAL PROVIDER is T-SQL, run against
-- the database itself, and only works when connected as the SQL server's
-- Microsoft Entra admin (see the sqlAadAdmin* parameters in main.bicep and
-- infra/README.md). Safe to re-run: CREATE USER fails harmlessly if the user
-- already exists, so re-running only matters if you want to re-grant roles.
--
-- Replace <FUNCTION_APP_NAME> with the deployment's `functionAppName` output
-- (main.bicep) — the managed identity's display name inside Microsoft Entra
-- is exactly the Function App's resource name.
--
-- Run this while connected to the target database (not master), authenticated
-- as the Microsoft Entra admin set on the SQL server.

CREATE USER [<FUNCTION_APP_NAME>] FROM EXTERNAL PROVIDER;
ALTER ROLE db_datareader ADD MEMBER [<FUNCTION_APP_NAME>];
ALTER ROLE db_datawriter ADD MEMBER [<FUNCTION_APP_NAME>];
