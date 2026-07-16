# Walkthrough — get P63 running from scratch

**Audience:** someone who has never seen this project before. No prior context needed.
Follow the steps top to bottom and you'll have the whole app running on your own laptop in
about 20–30 minutes.

> **What is this?** P63 is a demo healthcare & wellness web app. Patients track health data,
> get wellness tips, manage medications, book doctor appointments, and share access with
> family. Doctors manage patients and turn notes into insurance claims. It has three parts:
> a **frontend** (the website you click around in), a **backend** (the API that does the
> work), and a **database** (where data is stored). This guide sets up all three locally.
>
> All data is **fake** (synthetic Telugu demo names) — there is no real patient data anywhere.

> 🔑 **About this copy:** this is a clean, shareable copy of the project. Real cloud
> credentials (Azure logins, keys, tenant IDs) have been removed and replaced with
> placeholders. **You don't need any Azure account or credentials to run it** — the guide
> below uses **demo mode**, which runs everything locally on your laptop.

---

## Part 0 — What you need to install first

Install these four things (skip any you already have). Reboot after installing Docker if
it asks.

| Tool | What it's for | Get it |
|---|---|---|
| **Docker Desktop** | runs the local database | https://www.docker.com/products/docker-desktop |
| **Python 3.11** | runs the backend | https://www.python.org/downloads (tick "Add to PATH") |
| **Node.js 20+** | runs the frontend | https://nodejs.org (LTS) |
| **Azure Functions Core Tools v4** | runs the backend API host | `npm install -g azure-functions-core-tools@4 --unsafe-perm true` |
| **ODBC Driver 18 for SQL Server** | lets Python talk to the DB | https://learn.microsoft.com/sql/connect/odbc/download-odbc-driver-for-sql-server |

**Check they're installed** — open a terminal (PowerShell on Windows) and run:
```powershell
docker --version
python --version
node --version
func --version
```
Each should print a version number. If any says "not recognized", that tool isn't installed
or isn't on your PATH yet.

> 💡 On Windows, use **PowerShell** for these commands. If you use Git Bash instead, note
> that Docker paths starting with `/opt/...` sometimes need to be written `//opt/...`.

---

## Part 1 — Get the code

Your teammate shared the project either as a **zip file** or as a **git repository link**.

- **If you got a zip:** unzip it somewhere sensible (e.g. your Desktop), then open a terminal
  inside the unzipped folder.
- **If you got a git link:** clone it and step in —
  ```powershell
  git clone <repo-url-your-teammate-shared>
  cd <project-folder>
  ```

Everything below is run from **inside the project folder** — the one that contains the
`functions/`, `web/`, and `infra/` folders.

---

## Part 2 — Start the database (Docker)

This starts a local SQL Server in a container. Copy-paste the whole block:

```powershell
docker run -e "ACCEPT_EULA=Y" -e "MSSQL_SA_PASSWORD=<YOUR_SQL_PASSWORD>" `
  -p 1433:1433 --name p63-sql -d mcr.microsoft.com/mssql/server:2022-latest
```

> 🔑 `<YOUR_SQL_PASSWORD>` — pick any strong password you like for this local container.
> Whatever you choose, use the **same** password in **every** place it appears in this guide
> (Parts 2, 3, 4c, and 5b) so they all match.

Wait ~20 seconds for it to boot, then create the empty database:

```powershell
docker exec p63-sql /opt/mssql-tools18/bin/sqlcmd -S localhost -U sa -P "<YOUR_SQL_PASSWORD>" -C -Q "CREATE DATABASE p63health"
```

**✅ Checkpoint:** `docker ps` should show a container named `p63-sql` with status "Up".

> If you ever restart your PC, the container stops. Start it again with `docker start p63-sql`
> — your data is still there. You do **not** need to re-run this whole part.

---

## Part 3 — Create the tables (schema)

Copy the schema file into the container and run it into the database:

```powershell
docker cp infra/sql/schema.sql p63-sql:/tmp/schema.sql
docker exec p63-sql /opt/mssql-tools18/bin/sqlcmd -S localhost -U sa -P "<YOUR_SQL_PASSWORD>" -C -d p63health -i /tmp/schema.sql
```

**✅ Checkpoint:** it runs with no red error messages. (A few informational lines are normal.)

---

## Part 4 — Set up and seed the backend

**4a. Create a Python environment and install packages:**
```powershell
cd functions
python -m venv .venv
.venv\Scripts\Activate.ps1        # on Mac/Linux:  source .venv/bin/activate
pip install -r requirements.txt
cd ..
```

**4b. Install the seed-script packages:**
```powershell
pip install -r scripts/requirements.txt
```

**4c. Tell the tools how to reach the database** (this env var is read by both the seed
script and the backend). In the **same** PowerShell window:
```powershell
$env:SQL_CONNECTION_STRING = "Driver={ODBC Driver 18 for SQL Server};Server=tcp:localhost,1433;Database=p63health;UID=sa;PWD=<YOUR_SQL_PASSWORD>;TrustServerCertificate=yes;"
```
> On Mac/Linux instead: `export SQL_CONNECTION_STRING="Driver={ODBC Driver 18 for SQL Server};Server=tcp:localhost,1433;Database=p63health;UID=sa;PWD=<YOUR_SQL_PASSWORD>;TrustServerCertificate=yes;"`

**4d. Fill the database with fake demo data (10 patients, doctors, caregivers):**
```powershell
python scripts/seed_data.py --reset
```

**✅ Checkpoint:** it prints a list of demo accounts ending in `@p63care.in`, e.g.
`patient #1: saikiran.vanaparthi@p63care.in`. **Patient #1, Saikiran Vanaparthi, is the
star of the demo** — he has interesting data (alerts, a wellness score, tips).

---

## Part 5 — Start the backend (API)

**5a.** The backend's timers need a storage emulator called **Azurite**. Open a **new,
second** terminal window and run:
```powershell
npm install -g azurite      # one time only
azurite --silent
```
Leave this window running.

**5b.** Create the backend's config file. Back in your **first** terminal:
```powershell
cd functions
copy local.settings.json.example local.settings.json
```
Open `functions/local.settings.json` in any editor and make sure these two lines are set
(the rest can stay blank):
```json
"SQL_CONNECTION_STRING": "Driver={ODBC Driver 18 for SQL Server};Server=tcp:localhost,1433;Database=p63health;UID=sa;PWD=<YOUR_SQL_PASSWORD>;TrustServerCertificate=yes;",
"ALLOW_DEMO_PRINCIPAL": "true"
```

**5c.** Start it:
```powershell
func start
```

**✅ Checkpoint:** after a few seconds it prints a big list of functions and
`Host lock lease acquired`. You'll see `http://localhost:7071/api/...` URLs. **Leave this
window running.**

---

## Part 6 — Start the frontend (website)

**6a.** Turn on **demo mode** so you don't need to set up a real login. Create a file called
`web/.env` with this one line:
```
VITE_DEMO_MODE=true
```
> (There's a `web/.env.example` you can copy, but this single line is all you need to get going.)

**6b.** Open a **third** terminal window:
```powershell
cd web
npm install
npm run dev
```

**✅ Checkpoint:** it prints `Local: http://localhost:5173/`. Open that in your browser.

**🎉 You should now see the P63 dashboard for Saikiran Vanaparthi.**

---

## Part 7 — How to use it (the tour)

You now have **three windows running**: Azurite, the backend (`func start`), and the
frontend (`npm run dev`). Keep all three open while you use the app.

In the top-right of the website there's a **role switcher** (this only appears in demo mode).
Use it to explore the app as three different kinds of user:

- **Patient** (patient ID 1 = Saikiran): Dashboard, Risk Score, Recommendations,
  Medications, Schedule, Analytics, Caregiver.
- **Provider** (a doctor): sees a **patient roster**, can register patients, prescribe
  meds, and use the **Claim Assistant** (paste a clinical note → it extracts diagnoses).
- **Caregiver** (family member): a **scoped, read-only view** of the linked patient's
  vitals, medications, and alerts.

Change the **patient ID number** next to the role switcher (1–10) to view different demo
patients. Patient 1 is the most interesting; the others are mostly "healthy."

**Try this quick end-to-end demo as a Patient (ID 1):**
1. **Dashboard** — see vitals, an overall wellness score, active alerts, recommendations.
2. **Risk Score** → click **Recompute** — watch the four area scores + reasons update.
3. **Recommendations** → click **Done** or **Dismiss** on a tip (this feeds the engine).
4. **Schedule** → pick a doctor → pick a time slot → book an appointment.
5. **Medications** → mark a dose **taken**; watch the adherence % change.
6. **Analytics** → toggle **7d / 30d / 90d** to see trends.

**(Optional) make the dashboard "live":** in a fourth terminal (with the same
`SQL_CONNECTION_STRING` set and the backend running), stream fake wearable readings:
```powershell
python scripts/device_simulator.py --patient-id 1 --base-url http://localhost:7071/api
```

---

## Part 8 — How the pieces fit together (plain English)

```
   You click around in the browser          →   web/  (React website, port 5173)
   The website asks the backend for data     →   functions/  (Python API, port 7071)
   The backend reads/writes the database      →   Docker SQL container (port 1433)
```

- The **frontend** (`web/`) is just the screens and buttons. It never touches the database
  directly — it always asks the backend.
- The **backend** (`functions/`) is the brains: it checks who you are, enforces that you can
  only see your own data, calculates scores, and reads/writes the database.
- The **database** stores everything: patients, vitals, medications, appointments, etc.

Each screen in the website maps to a folder in the backend, e.g. the **Medications** screen
(`web/src/pages/Meds.tsx`) calls the **med_reminders** backend
(`functions/med_reminders/blueprint.py`).

For the full technical picture (all tables, all API endpoints, the security model, the
integrations), read **[README.md](README.md)**.

---

## Part 9 — Starting up again next time

Once it's all installed, you don't repeat the setup. To run the app on a later day:

1. **Database:** `docker start p63-sql` (if it's not already running).
2. **Azurite:** `azurite --silent` in one terminal.
3. **Backend:** in `functions/`, set `$env:SQL_CONNECTION_STRING` (Part 4c) then `func start`.
4. **Frontend:** in `web/`, `npm run dev`.
5. Open http://localhost:5173.

To wipe and re-load fresh demo data at any time: `python scripts/seed_data.py --reset`.

---

## Part 10 — Troubleshooting

| Symptom | Fix |
|---|---|
| `docker: command not found` / Docker errors | Make sure **Docker Desktop is open and running** (whale icon in the tray). |
| Backend can't connect to DB / `Login failed` | Is the container up? `docker ps`. If not: `docker start p63-sql`. Double-check the password you chose matches everywhere (Parts 2, 3, 4c, 5b). |
| `pyodbc` / "Data source name not found" | The **ODBC Driver 18** isn't installed (Part 0). Install it and restart the terminal. |
| Backend starts but timers error / storage errors | **Azurite isn't running** (Part 5a). Start it in its own window. |
| Website loads but every panel shows an error | The **backend isn't running**, or `SQL_CONNECTION_STRING` wasn't set in the `func start` window. Restart `func start` with the env var set. |
| Website redirects to a Microsoft login instead of showing the app | You're not in demo mode. Make sure `web/.env` contains `VITE_DEMO_MODE=true`, then restart `npm run dev`. |
| No role switcher in the top-right | Same as above — demo mode isn't on. |
| Container `p63-sql` won't start ("name in use") | It already exists: `docker start p63-sql`. To fully recreate: `docker rm -f p63-sql` then redo Part 2. |
| Port already in use (7071 / 5173 / 1433) | Something else is using it. Close the other process, or restart your PC. |

Still stuck? The full architecture and a phase-by-phase log of how everything was built is
in **[README.md](README.md)**, and the original design spec is in **[docs/BLUEPRINT.md](docs/BLUEPRINT.md)**.

---

*This is a demo. Synthetic data only — no real patient information. Wellness guidance only,
never medical diagnosis.*
