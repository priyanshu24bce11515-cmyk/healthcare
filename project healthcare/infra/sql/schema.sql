-- P63 Preventive Care Platform — Azure SQL schema (docs/BLUEPRINT.md Part 5)
-- Synthetic/demo data only.
--
-- Run the WHOLE file once against a fresh database (verified end-to-end: the
-- CREATE TABLE section runs, then the migration section's guards all skip
-- since everything already exists). Do NOT re-run the whole file a second
-- time against an already-populated database — the CREATE TABLE statements
-- themselves aren't guarded and will fail with "already exists". To bring an
-- OLDER already-deployed database up to date after a code change, run only
-- the "Idempotent migrations" section below (from that comment to the end of
-- the file) — every statement there is guarded and safe to re-run any number
-- of times. PHI-bearing columns are marked "-- PHI" — synthetic data only in
-- this project, but the column shape is what a real deployment would need to
-- encrypt (Always Encrypted / app-layer AES-256).

SET QUOTED_IDENTIFIER ON;
SET ANSI_NULLS ON;

CREATE TABLE Patients (
    id           INT IDENTITY PRIMARY KEY,
    name         NVARCHAR(200) NOT NULL, -- PHI
    dob          DATE NOT NULL,          -- PHI
    sex          NVARCHAR(20),
    contact      NVARCHAR(200) NOT NULL, -- PHI
    b2cObjectId  NVARCHAR(100) NULL,     -- set once this patient claims their record (see POST /patients/claim)
    fhirPatientId NVARCHAR(100) NULL     -- this patient's Patient/{id} on the linked EHR, once connected (see integrations/ehr_fhir.py)
);

CREATE TABLE Providers (
    id              INT IDENTITY PRIMARY KEY,
    name            NVARCHAR(200) NOT NULL,
    specialty       NVARCHAR(100),
    contact         NVARCHAR(200),
    -- Set once a signed-in principal claims this row (see POST
    -- /providers/claim) — NULL means "seeded/registered, not yet claimed."
    principalUserId NVARCHAR(100) NULL
);

CREATE TABLE Caregivers (
    id              INT IDENTITY PRIMARY KEY,
    name            NVARCHAR(200) NOT NULL,
    contact         NVARCHAR(200) NOT NULL,
    patientId       INT NOT NULL FOREIGN KEY REFERENCES Patients(id),
    relationship    NVARCHAR(50),
    accessScope     NVARCHAR(200) NOT NULL DEFAULT 'vitals,adherence,alerts', -- what they can SEE
    accessLevel     NVARCHAR(20) NOT NULL DEFAULT 'view_only', -- view_only | full — whether they can also WRITE
    -- Set once the invited caregiver claims (accepts) this link (see POST
    -- /caregivers/claim) — NULL means "invited, not yet accepted."
    principalUserId NVARCHAR(100) NULL
);
CREATE INDEX IX_Caregivers_Patient ON Caregivers(patientId);

CREATE TABLE Vitals (
    id          INT IDENTITY PRIMARY KEY,
    patientId   INT NOT NULL FOREIGN KEY REFERENCES Patients(id),
    type        NVARCHAR(20) NOT NULL, -- heartRate | steps | sleep | bp_systolic | bp_diastolic | glucose | oxygenSaturation | temperature
    value       FLOAT NOT NULL,
    unit        NVARCHAR(20),
    recordedAt  DATETIME2 NOT NULL,
    source      NVARCHAR(30) NOT NULL DEFAULT 'simulated',
    isAnomaly   BIT NOT NULL DEFAULT 0  -- set when the reading crossed an alert threshold (see shared/alerts/rules.py)
);
CREATE INDEX IX_Vitals_Patient_Type_Recorded ON Vitals(patientId, type, recordedAt DESC);

CREATE TABLE Medications (
    id           INT IDENTITY PRIMARY KEY,
    patientId    INT NOT NULL FOREIGN KEY REFERENCES Patients(id),
    name         NVARCHAR(200) NOT NULL, -- PHI
    dosage       NVARCHAR(50),           -- PHI
    schedule     NVARCHAR(20) NOT NULL,  -- morning | afternoon | night | daily | twice_daily | custom | as_needed
    customTimes  NVARCHAR(100) NULL,     -- comma-separated HH:MM, only used when schedule = 'custom'
    startDate    DATE NOT NULL,
    endDate      DATE NULL
);
CREATE INDEX IX_Medications_Patient_Active ON Medications(patientId, startDate, endDate);

CREATE TABLE AdherenceLog (
    id           INT IDENTITY PRIMARY KEY,
    medicationId INT NOT NULL FOREIGN KEY REFERENCES Medications(id),
    dueAt        DATETIME2 NOT NULL,
    takenAt      DATETIME2 NULL,
    status       NVARCHAR(10) NOT NULL DEFAULT 'pending' -- pending | taken | missed
);
CREATE INDEX IX_AdherenceLog_Med_Due ON AdherenceLog(medicationId, dueAt DESC);

CREATE TABLE Appointments (
    id         INT IDENTITY PRIMARY KEY,
    patientId  INT NOT NULL FOREIGN KEY REFERENCES Patients(id),
    providerId INT NOT NULL FOREIGN KEY REFERENCES Providers(id),
    startsAt   DATETIME2 NOT NULL,
    type       NVARCHAR(20) NOT NULL DEFAULT 'telemed', -- telemed | in-person
    status     NVARCHAR(20) NOT NULL DEFAULT 'requested', -- requested | confirmed | completed | cancelled
    notes      NVARCHAR(2000) NULL -- PHI (consultation notes)
);
CREATE INDEX IX_Appointments_Provider_Starts ON Appointments(providerId, startsAt);
CREATE INDEX IX_Appointments_Patient_Starts ON Appointments(patientId, startsAt DESC);

-- A provider's own weekly telemedicine availability (Mon=0..Sun=6). No rows
-- for a provider means "use the system default" (see api_schedule/blueprint.py).
CREATE TABLE ProviderAvailability (
    id          INT IDENTITY PRIMARY KEY,
    providerId  INT NOT NULL FOREIGN KEY REFERENCES Providers(id),
    dayOfWeek   TINYINT NOT NULL, -- 0=Monday .. 6=Sunday
    startHour   TINYINT NOT NULL, -- 0-23, inclusive
    endHour     TINYINT NOT NULL  -- 0-23, exclusive (last bookable slot is endHour-1)
);
CREATE INDEX IX_ProviderAvailability_Provider ON ProviderAvailability(providerId);

CREATE TABLE Goals (
    id        INT IDENTITY PRIMARY KEY,
    patientId INT NOT NULL FOREIGN KEY REFERENCES Patients(id),
    kind      NVARCHAR(20) NOT NULL, -- fitness | nutrition
    target    FLOAT NOT NULL,
    progress  FLOAT NOT NULL DEFAULT 0, -- patient-reported baseline; fitness progress is recomputed live from Vitals (see api_dashboard)
    period    NVARCHAR(20) NOT NULL DEFAULT 'weekly' -- daily | weekly | monthly
);
CREATE INDEX IX_Goals_Patient ON Goals(patientId);

CREATE TABLE Recommendations (
    id            INT IDENTITY PRIMARY KEY,
    patientId     INT NOT NULL FOREIGN KEY REFERENCES Patients(id),
    text          NVARCHAR(500) NOT NULL,
    reason        NVARCHAR(500) NOT NULL,
    category      NVARCHAR(50),  -- Exercise | Nutrition | Sleep | Stress | Medication | Hydration | Screening | MentalHealth
    priority      NVARCHAR(10) NOT NULL DEFAULT 'Medium', -- Low | Medium | High
    priorityScore DECIMAL(4,3) NULL, -- 0.000-1.000, the engine's raw ranking score behind `priority`
    source        NVARCHAR(10) NOT NULL DEFAULT 'rule',   -- rule | llm
    generatedAt   DATETIME2 NOT NULL DEFAULT SYSUTCDATETIME(),
    actedOn       BIT NOT NULL DEFAULT 0,     -- patient marked "I did this" — feeds the next ranking (see shared/recommend.py)
    dismissedAt   DATETIME2 NULL              -- patient dismissed it — damps this category next time
);
CREATE INDEX IX_Recommendations_Patient_Generated ON Recommendations(patientId, generatedAt DESC);

CREATE TABLE RiskScores (
    id          INT IDENTITY PRIMARY KEY,
    patientId   INT NOT NULL FOREIGN KEY REFERENCES Patients(id),
    area        NVARCHAR(20) NOT NULL, -- sleep | activity | adherence | heart | overall
    score       INT NOT NULL CHECK (score BETWEEN 0 AND 100), -- wellness score: HIGH = healthy (see shared/scoring/risk_score.py band())
    reason      NVARCHAR(500),
    computedAt  DATETIME2 NOT NULL DEFAULT SYSUTCDATETIME()
);
CREATE INDEX IX_RiskScores_Patient_Area_Computed ON RiskScores(patientId, area, computedAt DESC);

CREATE TABLE Claims (
    id              INT IDENTITY PRIMARY KEY,
    patientId       INT NOT NULL FOREIGN KEY REFERENCES Patients(id),
    provider        NVARCHAR(200),
    amount          DECIMAL(10,2) NULL,
    diagnosisCodes  NVARCHAR(200) NULL, -- PHI
    status          NVARCHAR(20) NOT NULL DEFAULT 'draft', -- draft | ready | submitted | processing | approved | denied
    denialReason    NVARCHAR(500) NULL,
    extractedFields NVARCHAR(MAX), -- JSON — PHI (extracted from clinical notes)
    missingFields   NVARCHAR(MAX)  -- JSON
);
CREATE INDEX IX_Claims_Patient ON Claims(patientId, id DESC);

-- Every Claims.status transition, append-style (not enforced immutable like
-- AuditLog — a provider can, in principle, correct a mis-set status — but in
-- practice this table is only ever inserted into, never updated).
CREATE TABLE ClaimStatusHistory (
    id        INT IDENTITY PRIMARY KEY,
    claimId   INT NOT NULL FOREIGN KEY REFERENCES Claims(id),
    status    NVARCHAR(20) NOT NULL,
    changedBy NVARCHAR(100) NOT NULL,
    changedAt DATETIME2 NOT NULL DEFAULT SYSUTCDATETIME(),
    note      NVARCHAR(500) NULL
);
CREATE INDEX IX_ClaimStatusHistory_Claim ON ClaimStatusHistory(claimId, changedAt);

CREATE TABLE Alerts (
    id                 INT IDENTITY PRIMARY KEY,
    patientId          INT NOT NULL FOREIGN KEY REFERENCES Patients(id),
    kind               NVARCHAR(20) NOT NULL, -- vital | adherence | risk
    -- Only set when kind = 'vital' so re-sweep dedupe can distinguish
    -- "already alerted for this vital type" from "...some other vital type".
    vitalType          NVARCHAR(20) NULL,
    severity           NVARCHAR(20) NULL, -- info | warning | critical
    detail             NVARCHAR(300) NOT NULL,
    value              NVARCHAR(50),
    raisedAt           DATETIME2 NOT NULL DEFAULT SYSUTCDATETIME(),
    acknowledgedBy     NVARCHAR(100) NULL,
    acknowledgedAt     DATETIME2 NULL,
    providerNotifiedAt DATETIME2 NULL -- set when severity='critical' triggered the provider notification channel
);
CREATE INDEX IX_Alerts_Patient_Raised ON Alerts(patientId, raisedAt DESC);
-- Covers the hot path (dashboard + compute_risk's unacknowledged-count query)
-- without scanning acknowledged history once a patient has years of it.
CREATE INDEX IX_Alerts_Patient_Unacknowledged ON Alerts(patientId, raisedAt DESC) WHERE acknowledgedAt IS NULL;

-- In-app notification record for every reminder/alert/confirmation sent,
-- regardless of which channel(s) also fired (email/SMS). readAt is reserved
-- for a notifications-inbox UI (not yet built — tracked, not dead).
CREATE TABLE Notifications (
    id        INT IDENTITY PRIMARY KEY,
    patientId INT NOT NULL FOREIGN KEY REFERENCES Patients(id),
    type      NVARCHAR(50) NOT NULL,
    title     NVARCHAR(200) NOT NULL,
    body      NVARCHAR(1000) NOT NULL, -- PHI (may reference conditions/medications)
    channel   NVARCHAR(20) NOT NULL,   -- email | sms | in_app
    sentAt    DATETIME2 NULL,
    readAt    DATETIME2 NULL,
    createdAt DATETIME2 NOT NULL DEFAULT SYSUTCDATETIME()
);
CREATE INDEX IX_Notifications_Patient ON Notifications(patientId, createdAt DESC);

-- OAuth tokens for a patient's connected wearable/EHR account (Fitbit, Google
-- Fit, a generic FHIR device, or 'ehr'). Tokens are encrypted at the
-- application layer before insert (see shared/crypto.py) — this column never
-- holds plaintext. revokedAt IS NULL means "currently active" (see the
-- filtered index below); rows are never deleted so a revocation has a
-- permanent record.
CREATE TABLE DeviceAuthorizations (
    id              INT IDENTITY PRIMARY KEY,
    patientId       INT NOT NULL FOREIGN KEY REFERENCES Patients(id),
    deviceType      NVARCHAR(20) NOT NULL, -- fitbit | google_fit | fhir_device | ehr
    deviceId        NVARCHAR(100) NOT NULL, -- vendor's own id for this device/owner/server
    accessTokenEnc  NVARCHAR(500) NOT NULL, -- PHI (encrypted)
    refreshTokenEnc NVARCHAR(500) NULL,     -- PHI (encrypted)
    tokenExpiresAt  DATETIME2 NULL,
    authorizedAt    DATETIME2 NOT NULL DEFAULT SYSUTCDATETIME(),
    revokedAt       DATETIME2 NULL
);
CREATE INDEX IX_DeviceAuthorizations_Patient ON DeviceAuthorizations(patientId) WHERE revokedAt IS NULL;

-- Every EHR sync attempt (in: pulled from the EHR, out: pushed to it),
-- logged regardless of outcome — the boundary-crossing counterpart to
-- AuditLog for data that leaves/enters this app via a third-party FHIR
-- server (see integrations/ehr_fhir.py::EHRSyncService).
CREATE TABLE EHRSyncLog (
    id               INT IDENTITY PRIMARY KEY,
    patientId        INT NOT NULL FOREIGN KEY REFERENCES Patients(id),
    fhirResourceType NVARCHAR(50) NOT NULL, -- Patient | MedicationRequest | Condition | AllergyIntolerance | Immunization | Observation
    fhirResourceId   NVARCHAR(100) NULL,
    syncDirection    NVARCHAR(10) NOT NULL, -- in | out
    syncAt           DATETIME2 NOT NULL DEFAULT SYSUTCDATETIME(),
    status           NVARCHAR(20) NOT NULL, -- success | error
    errorMessage     NVARCHAR(500) NULL
);
CREATE INDEX IX_EHRSyncLog_Patient ON EHRSyncLog(patientId, syncAt DESC);

-- HIPAA audit trail. Append-only: see trg_AuditLog_append_only below, which
-- blocks UPDATE/DELETE at the database level, not just in application code.
CREATE TABLE AuditLog (
    id           INT IDENTITY PRIMARY KEY,
    actorId      NVARCHAR(100) NOT NULL,
    role         NVARCHAR(20) NOT NULL,
    action       NVARCHAR(100) NOT NULL,
    targetType   NVARCHAR(50) NOT NULL,
    targetId     NVARCHAR(50) NOT NULL,
    ipAddress    NVARCHAR(60) NULL,
    userAgent    NVARCHAR(300) NULL,
    phiAccessed  BIT NOT NULL DEFAULT 1,
    outcome      NVARCHAR(20) NOT NULL DEFAULT 'success', -- success | denied | error
    at           DATETIME2 NOT NULL DEFAULT SYSUTCDATETIME()
);
CREATE INDEX IX_AuditLog_Target ON AuditLog(targetType, targetId, at DESC);
CREATE INDEX IX_AuditLog_Actor ON AuditLog(actorId, at DESC);
GO
CREATE TRIGGER trg_AuditLog_append_only ON AuditLog
INSTEAD OF UPDATE, DELETE
AS
BEGIN
    RAISERROR('AuditLog is append-only (HIPAA): UPDATE and DELETE are not permitted.', 16, 1);
END
GO

-- ---------------------------------------------------------------------------
-- Idempotent migrations below — safe to re-run against a database that was
-- created before a given column/table/index/trigger existed. Every CREATE
-- TABLE above already includes these for a *fresh* database; these guards
-- exist only to bring an older database up to date without a DROP/recreate.
-- ---------------------------------------------------------------------------

IF NOT EXISTS (SELECT 1 FROM sys.columns WHERE object_id = OBJECT_ID('Caregivers') AND name = 'principalUserId')
BEGIN
    ALTER TABLE Caregivers ADD principalUserId NVARCHAR(100) NULL;
END
IF NOT EXISTS (SELECT 1 FROM sys.columns WHERE object_id = OBJECT_ID('Caregivers') AND name = 'accessLevel')
BEGIN
    ALTER TABLE Caregivers ADD accessLevel NVARCHAR(20) NOT NULL DEFAULT 'view_only';
END

IF NOT EXISTS (SELECT 1 FROM sys.columns WHERE object_id = OBJECT_ID('Alerts') AND name = 'vitalType')
BEGIN
    ALTER TABLE Alerts ADD vitalType NVARCHAR(20) NULL;
END
IF NOT EXISTS (SELECT 1 FROM sys.columns WHERE object_id = OBJECT_ID('Alerts') AND name = 'severity')
BEGIN
    ALTER TABLE Alerts ADD severity NVARCHAR(20) NULL;
END
IF NOT EXISTS (SELECT 1 FROM sys.columns WHERE object_id = OBJECT_ID('Alerts') AND name = 'providerNotifiedAt')
BEGIN
    ALTER TABLE Alerts ADD providerNotifiedAt DATETIME2 NULL;
END

IF NOT EXISTS (SELECT 1 FROM sys.columns WHERE object_id = OBJECT_ID('Providers') AND name = 'principalUserId')
BEGIN
    ALTER TABLE Providers ADD principalUserId NVARCHAR(100) NULL;
END

IF NOT EXISTS (SELECT 1 FROM sys.columns WHERE object_id = OBJECT_ID('Patients') AND name = 'fhirPatientId')
BEGIN
    ALTER TABLE Patients ADD fhirPatientId NVARCHAR(100) NULL;
END

IF NOT EXISTS (SELECT 1 FROM sys.columns WHERE object_id = OBJECT_ID('Vitals') AND name = 'isAnomaly')
BEGIN
    ALTER TABLE Vitals ADD isAnomaly BIT NOT NULL DEFAULT 0;
END

IF NOT EXISTS (SELECT 1 FROM sys.columns WHERE object_id = OBJECT_ID('Medications') AND name = 'customTimes')
BEGIN
    ALTER TABLE Medications ADD customTimes NVARCHAR(100) NULL;
END

IF NOT EXISTS (SELECT 1 FROM sys.columns WHERE object_id = OBJECT_ID('Appointments') AND name = 'notes')
BEGIN
    ALTER TABLE Appointments ADD notes NVARCHAR(2000) NULL;
END

IF NOT EXISTS (SELECT 1 FROM sys.columns WHERE object_id = OBJECT_ID('Recommendations') AND name = 'actedOn')
BEGIN
    ALTER TABLE Recommendations ADD actedOn BIT NOT NULL DEFAULT 0;
END
IF NOT EXISTS (SELECT 1 FROM sys.columns WHERE object_id = OBJECT_ID('Recommendations') AND name = 'dismissedAt')
BEGIN
    ALTER TABLE Recommendations ADD dismissedAt DATETIME2 NULL;
END
IF NOT EXISTS (SELECT 1 FROM sys.columns WHERE object_id = OBJECT_ID('Recommendations') AND name = 'priorityScore')
BEGIN
    ALTER TABLE Recommendations ADD priorityScore DECIMAL(4,3) NULL;
END

IF NOT EXISTS (SELECT 1 FROM sys.columns WHERE object_id = OBJECT_ID('Claims') AND name = 'denialReason')
BEGIN
    ALTER TABLE Claims ADD denialReason NVARCHAR(500) NULL;
END

IF NOT EXISTS (SELECT 1 FROM sys.columns WHERE object_id = OBJECT_ID('AuditLog') AND name = 'ipAddress')
BEGIN
    ALTER TABLE AuditLog ADD ipAddress NVARCHAR(60) NULL;
END
IF NOT EXISTS (SELECT 1 FROM sys.columns WHERE object_id = OBJECT_ID('AuditLog') AND name = 'userAgent')
BEGIN
    ALTER TABLE AuditLog ADD userAgent NVARCHAR(300) NULL;
END
IF NOT EXISTS (SELECT 1 FROM sys.columns WHERE object_id = OBJECT_ID('AuditLog') AND name = 'phiAccessed')
BEGIN
    ALTER TABLE AuditLog ADD phiAccessed BIT NOT NULL DEFAULT 1;
END
IF NOT EXISTS (SELECT 1 FROM sys.columns WHERE object_id = OBJECT_ID('AuditLog') AND name = 'outcome')
BEGIN
    ALTER TABLE AuditLog ADD outcome NVARCHAR(20) NOT NULL DEFAULT 'success';
END

IF OBJECT_ID('ProviderAvailability', 'U') IS NULL
BEGIN
    CREATE TABLE ProviderAvailability (
        id          INT IDENTITY PRIMARY KEY,
        providerId  INT NOT NULL FOREIGN KEY REFERENCES Providers(id),
        dayOfWeek   TINYINT NOT NULL,
        startHour   TINYINT NOT NULL,
        endHour     TINYINT NOT NULL
    );
END

IF OBJECT_ID('ClaimStatusHistory', 'U') IS NULL
BEGIN
    CREATE TABLE ClaimStatusHistory (
        id        INT IDENTITY PRIMARY KEY,
        claimId   INT NOT NULL FOREIGN KEY REFERENCES Claims(id),
        status    NVARCHAR(20) NOT NULL,
        changedBy NVARCHAR(100) NOT NULL,
        changedAt DATETIME2 NOT NULL DEFAULT SYSUTCDATETIME(),
        note      NVARCHAR(500) NULL
    );
END

IF OBJECT_ID('Notifications', 'U') IS NULL
BEGIN
    CREATE TABLE Notifications (
        id        INT IDENTITY PRIMARY KEY,
        patientId INT NOT NULL FOREIGN KEY REFERENCES Patients(id),
        type      NVARCHAR(50) NOT NULL,
        title     NVARCHAR(200) NOT NULL,
        body      NVARCHAR(1000) NOT NULL,
        channel   NVARCHAR(20) NOT NULL,
        sentAt    DATETIME2 NULL,
        readAt    DATETIME2 NULL,
        createdAt DATETIME2 NOT NULL DEFAULT SYSUTCDATETIME()
    );
END

IF OBJECT_ID('DeviceAuthorizations', 'U') IS NULL
BEGIN
    CREATE TABLE DeviceAuthorizations (
        id              INT IDENTITY PRIMARY KEY,
        patientId       INT NOT NULL FOREIGN KEY REFERENCES Patients(id),
        deviceType      NVARCHAR(20) NOT NULL,
        deviceId        NVARCHAR(100) NOT NULL,
        accessTokenEnc  NVARCHAR(500) NOT NULL,
        refreshTokenEnc NVARCHAR(500) NULL,
        tokenExpiresAt  DATETIME2 NULL,
        authorizedAt    DATETIME2 NOT NULL DEFAULT SYSUTCDATETIME(),
        revokedAt       DATETIME2 NULL
    );
END

IF OBJECT_ID('EHRSyncLog', 'U') IS NULL
BEGIN
    CREATE TABLE EHRSyncLog (
        id               INT IDENTITY PRIMARY KEY,
        patientId        INT NOT NULL FOREIGN KEY REFERENCES Patients(id),
        fhirResourceType NVARCHAR(50) NOT NULL,
        fhirResourceId   NVARCHAR(100) NULL,
        syncDirection    NVARCHAR(10) NOT NULL,
        syncAt           DATETIME2 NOT NULL DEFAULT SYSUTCDATETIME(),
        status           NVARCHAR(20) NOT NULL,
        errorMessage     NVARCHAR(500) NULL
    );
END

IF NOT EXISTS (SELECT 1 FROM sys.triggers WHERE name = 'trg_AuditLog_append_only')
BEGIN
    EXEC('
        CREATE TRIGGER trg_AuditLog_append_only ON AuditLog
        INSTEAD OF UPDATE, DELETE
        AS
        BEGIN
            RAISERROR(''AuditLog is append-only (HIPAA): UPDATE and DELETE are not permitted.'', 16, 1);
        END
    ');
END

-- Indexes added after the initial tables existed (guarded so a fresh run,
-- where CREATE TABLE above already added them, doesn't collide).
IF NOT EXISTS (SELECT 1 FROM sys.indexes WHERE name = 'IX_Caregivers_Patient')
    CREATE INDEX IX_Caregivers_Patient ON Caregivers(patientId);
IF NOT EXISTS (SELECT 1 FROM sys.indexes WHERE name = 'IX_Medications_Patient_Active')
    CREATE INDEX IX_Medications_Patient_Active ON Medications(patientId, startDate, endDate);
IF NOT EXISTS (SELECT 1 FROM sys.indexes WHERE name = 'IX_Appointments_Patient_Starts')
    CREATE INDEX IX_Appointments_Patient_Starts ON Appointments(patientId, startsAt DESC);
IF NOT EXISTS (SELECT 1 FROM sys.indexes WHERE name = 'IX_Goals_Patient')
    CREATE INDEX IX_Goals_Patient ON Goals(patientId);
IF NOT EXISTS (SELECT 1 FROM sys.indexes WHERE name = 'IX_Claims_Patient')
    CREATE INDEX IX_Claims_Patient ON Claims(patientId, id DESC);
IF NOT EXISTS (SELECT 1 FROM sys.indexes WHERE name = 'IX_Notifications_Patient')
    CREATE INDEX IX_Notifications_Patient ON Notifications(patientId, createdAt DESC);
IF NOT EXISTS (SELECT 1 FROM sys.indexes WHERE name = 'IX_AuditLog_Actor')
    CREATE INDEX IX_AuditLog_Actor ON AuditLog(actorId, at DESC);
IF NOT EXISTS (SELECT 1 FROM sys.indexes WHERE name = 'IX_Alerts_Patient_Unacknowledged')
    CREATE INDEX IX_Alerts_Patient_Unacknowledged ON Alerts(patientId, raisedAt DESC) WHERE acknowledgedAt IS NULL;
IF NOT EXISTS (SELECT 1 FROM sys.indexes WHERE name = 'IX_ClaimStatusHistory_Claim')
    CREATE INDEX IX_ClaimStatusHistory_Claim ON ClaimStatusHistory(claimId, changedAt);
IF NOT EXISTS (SELECT 1 FROM sys.indexes WHERE name = 'IX_ProviderAvailability_Provider')
    CREATE INDEX IX_ProviderAvailability_Provider ON ProviderAvailability(providerId);
IF NOT EXISTS (SELECT 1 FROM sys.indexes WHERE name = 'IX_DeviceAuthorizations_Patient')
    CREATE INDEX IX_DeviceAuthorizations_Patient ON DeviceAuthorizations(patientId) WHERE revokedAt IS NULL;
IF NOT EXISTS (SELECT 1 FROM sys.indexes WHERE name = 'IX_EHRSyncLog_Patient')
    CREATE INDEX IX_EHRSyncLog_Patient ON EHRSyncLog(patientId, syncAt DESC);
