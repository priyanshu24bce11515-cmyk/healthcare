import {
  Activity,
  CalendarClock,
  HeartPulse,
  HeartHandshake,
  Pill,
  ShieldCheck,
  Sparkles,
  Watch,
} from "lucide-react";
import { useAuth } from "../lib/auth";

const FEATURES: { icon: typeof HeartPulse; title: string; desc: string }[] = [
  { icon: Watch, title: "Wearable tracking", desc: "Heart rate, steps, sleep, BP & glucose — streamed and monitored with instant alerts." },
  { icon: Activity, title: "Health Risk Score", desc: "An explainable wellness score across sleep, activity, adherence & heart trends." },
  { icon: Sparkles, title: "Smart recommendations", desc: "Personalized wellness tips generated from your own recent data — each one explained." },
  { icon: Pill, title: "Medication adherence", desc: "Reminders, dose tracking, and pattern detection when doses get missed." },
  { icon: CalendarClock, title: "Telemedicine", desc: "Book consultations against real provider availability." },
  { icon: HeartHandshake, title: "Family & caregivers", desc: "Give loved ones a secure, scoped view of vitals, medication & alerts." },
];

export function Landing() {
  const { login } = useAuth();

  return (
    <div className="min-h-screen bg-plane">
      {/* top bar */}
      <header className="mx-auto flex max-w-6xl items-center px-6 py-5">
        <div className="flex items-center gap-2.5">
          <span
            className="flex h-9 w-9 items-center justify-center rounded-xl"
            style={{ backgroundColor: "var(--role-base)" }}
          >
            <HeartPulse className="h-5 w-5" style={{ color: "var(--role-accent)" }} />
          </span>
          <span className="text-sm font-semibold text-ink-primary">Healthcare &amp; Wellness</span>
        </div>
      </header>

      {/* hero */}
      <section className="mx-auto max-w-6xl px-6 pb-12 pt-10 sm:pt-16">
        <div className="animate-fade-in flex flex-col items-center text-center">
          <span
            className="mb-5 inline-flex items-center gap-1.5 rounded-full border px-3 py-1 text-xs font-medium"
            style={{ borderColor: "var(--border)", color: "var(--role-accent)" }}
          >
            <Sparkles className="h-3.5 w-3.5" /> AI Preventive Care &amp; Family Health Monitoring
          </span>
          <h1 className="max-w-3xl text-4xl font-semibold tracking-tight text-ink-primary sm:text-5xl">
            Personalized Healthcare &amp; Wellness Platform
          </h1>
          <p className="mt-5 max-w-2xl text-base text-ink-secondary sm:text-lg">
            Track your health metrics from wearables, get explainable wellness guidance, manage medications, book
            telemedicine visits, and share a secure view with family — all in one place.
          </p>
          <div className="mt-8 flex flex-col items-center gap-3 sm:flex-row">
            <button
              onClick={login}
              className="flex items-center gap-2 rounded-xl px-6 py-3 text-sm font-semibold text-white shadow-md transition-opacity duration-150 hover:opacity-90"
              style={{ backgroundColor: "var(--role-accent)" }}
            >
              <ShieldCheck className="h-4 w-4" />
              Sign in with Microsoft
            </button>
            <a
              href="#features"
              className="rounded-xl border border-line-border bg-surface px-6 py-3 text-sm font-medium text-ink-secondary transition-colors duration-150 hover:text-ink-primary"
            >
              Explore features
            </a>
          </div>
          <p className="mt-4 text-xs text-ink-muted">
            General wellness guidance, never medical diagnosis
          </p>
        </div>
      </section>

      {/* features */}
      <section id="features" className="mx-auto max-w-6xl px-6 pb-20">
        <div className="grid grid-cols-1 gap-4 sm:grid-cols-2 lg:grid-cols-3">
          {FEATURES.map((f, i) => {
            const Icon = f.icon;
            return (
              <div
                key={f.title}
                className="animate-fade-in rounded-2xl border border-line-border bg-surface p-5 shadow-sm transition-shadow duration-200 hover:shadow-md"
                style={{ borderLeftColor: "var(--role-accent)", borderLeftWidth: "3px", animationDelay: `${Math.min(i * 60, 300)}ms` }}
              >
                <span
                  className="mb-3 flex h-10 w-10 items-center justify-center rounded-xl"
                  style={{ backgroundColor: "var(--role-base)" }}
                >
                  <Icon className="h-5 w-5" style={{ color: "var(--role-accent)" }} />
                </span>
                <h3 className="text-sm font-semibold text-ink-primary">{f.title}</h3>
                <p className="mt-1 text-sm text-ink-secondary">{f.desc}</p>
              </div>
            );
          })}
        </div>
      </section>

      {/* footer */}
      <footer className="border-t border-line-grid">
        <div className="mx-auto flex max-w-6xl flex-col items-center justify-between gap-2 px-6 py-6 text-xs text-ink-muted sm:flex-row">
          <span>Built on Microsoft Azure · Python Functions · Azure SQL · Entra External ID</span>
          <span>AI Preventive Care &amp; Family Health Monitoring</span>
        </div>
      </footer>
    </div>
  );
}
