
import { useEffect, useState } from "react";
import {
  Activity,
  AlertTriangle,
  ArrowUpRight,
  Bell,
  CheckCircle2,
  ChevronRight,
  Clock3,
  LayoutDashboard,
  Menu,
  Radio,
  RefreshCw,
  ShieldCheck,
  Wifi,
  WifiOff,
} from "lucide-react";
import { API_BASE_URL, getHealth } from "./api/client.js";
import "./App.css";

export default function App() {
  const [backend, setBackend] = useState("checking");
  const [health, setHealth] = useState(null);
  const [lastChecked, setLastChecked] = useState(null);
  const [mobileMenu, setMobileMenu] = useState(false);

  async function checkBackend() {
    setBackend("checking");

    try {
      const data = await getHealth({
        signal: AbortSignal.timeout(4000),
      });
      setHealth(data);
      setBackend(data.status === "healthy" ? "online" : "error");
    } catch {
      setBackend("offline");
      setHealth(null);
    } finally {
      setLastChecked(new Date());
    }
  }

  useEffect(() => {
    checkBackend();
  }, []);

  const navItems = [
    { name: "Overview", icon: LayoutDashboard },
    { name: "Incidents", icon: AlertTriangle },
    { name: "Analytics", icon: Activity },
    { name: "Response Center", icon: ShieldCheck },
  ];

  return (
    <div className="app-shell">
      <aside className={`sidebar ${mobileMenu ? "sidebar-open" : ""}`}>
        <div className="brand">
          <div className="brand-mark"><Radio size={24} /></div>
          <div>
            <h1>RIPPLE</h1>
            <p>CAMPUS RESILIENCE</p>
          </div>
        </div>

        <div className="workspace-label">WORKSPACE</div>
        <nav className="navigation">
          {navItems.map(({ name, icon: Icon }, index) => (
            <button
              key={name}
              className={`nav-item ${index === 0 ? "active" : ""}`}
              onClick={() => {
                setMobileMenu(false);
                if (index !== 0) {
                  alert(`${name} will be enabled when the relevant data and API are available.`);
                }
              }}
            >
              <Icon size={19} />
              <span>{name}</span>
              {index === 0 && <span className="active-dot" />}
            </button>
          ))}
        </nav>

        <div className="sidebar-bottom">
          <div className="resilience-card">
            <ShieldCheck size={23} />
            <h3>Resilience starts with awareness.</h3>
            <p>One campus. One shared picture. Better decisions.</p>
          </div>
          <div className="sidebar-footer">
            <span className="footer-dot" />
            Ripple Datathon Project
            <span className="version">v1.0</span>
          </div>
        </div>
      </aside>

      <main className="main-panel">
        <header className="topbar">
          <button
            className="menu-button"
            onClick={() => setMobileMenu(!mobileMenu)}
            aria-label="Toggle navigation"
          >
            <Menu size={22} />
          </button>
          <div className="breadcrumb">
            Workspace <ChevronRight size={15} /> <strong>Overview</strong>
          </div>
          <div className="topbar-right">
            <span className="environment"><span /> DEVELOPMENT</span>
            <button className="icon-button" aria-label="Notifications">
              <Bell size={19} />
            </button>
            <div className="avatar">RC</div>
          </div>
        </header>

        <div className="page-content">
          <section className="welcome-row">
            <div>
              <div className="eyebrow"><span /> CAMPUS SITUATIONAL AWARENESS</div>
              <h2>Campus overview<span className="heading-period">.</span></h2>
              <p className="page-description">
                Monitor system readiness and prepare for informed campus response.
              </p>
            </div>
            <button className="refresh-button" onClick={checkBackend}>
              <RefreshCw size={16} className={backend === "checking" ? "spin" : ""} />
              Refresh status
            </button>
          </section>

          <section className={`connection-banner ${backend}`}>
            <div className="connection-icon">
              {backend === "online" ? <CheckCircle2 size={23} /> :
                backend === "offline" ? <WifiOff size={23} /> :
                <Wifi size={23} />}
            </div>
            <div className="connection-copy">
              <strong>
                {backend === "online" ? "Backend connected" :
                  backend === "offline" ? "Backend not reachable" :
                  backend === "error" ? "Backend response needs attention" :
                  "Checking backend connection"}
              </strong>
              <p>
                {backend === "online"
                  ? "The health endpoint responded successfully."
                  : backend === "offline"
                  ? "Start the FastAPI server, then refresh this status."
                  : backend === "error"
                  ? "The server responded, but health status was unexpected."
                  : "Contacting the local Ripple API…"}
              </p>
            </div>
            <span className="connection-status">
              {backend === "online" ? "OPERATIONAL" :
                backend === "checking" ? "CHECKING" : "NOT READY"}
            </span>
          </section>

          <div className="section-heading">
            <div>
              <h3>System readiness</h3>
              <p>Live checks from the currently available backend.</p>
            </div>
            <span className="live-label"><span /> LIVE CHECK</span>
          </div>

          <section className="metric-grid">
            <article className="metric-card">
              <div className="metric-top">
                <span className="metric-icon purple"><Activity size={20} /></span>
                <span className="metric-tag">API</span>
              </div>
              <p className="metric-label">Backend status</p>
              <h3 className="metric-value">
                {backend === "online" ? "Healthy" :
                  backend === "checking" ? "Checking…" : "Unavailable"}
              </h3>
              <div className="metric-foot">
                <span className={backend === "online" ? "positive" : "muted"}>
                  {backend === "online" ? "Health check passed" : "Awaiting successful check"}
                </span>
              </div>
            </article>

            <article className="metric-card">
              <div className="metric-top">
                <span className="metric-icon green"><ShieldCheck size={20} /></span>
                <span className="metric-tag">SERVICE</span>
              </div>
              <p className="metric-label">Health endpoint</p>
              <h3 className="metric-value">{health?.status ?? "—"}</h3>
              <div className="metric-foot">
                <span className="muted">GET /api/health</span>
              </div>
            </article>

            <article className="metric-card">
              <div className="metric-top">
                <span className="metric-icon amber"><Clock3 size={20} /></span>
                <span className="metric-tag">CHECK</span>
              </div>
              <p className="metric-label">Last checked</p>
              <h3 className="metric-value time-value">
                {lastChecked
                  ? lastChecked.toLocaleTimeString([], { hour: "2-digit", minute: "2-digit", second: "2-digit" })
                  : "Pending"}
              </h3>
              <div className="metric-foot">
                <span className="muted">Local browser time</span>
              </div>
            </article>

            <article className="metric-card">
              <div className="metric-top">
                <span className="metric-icon blue"><AlertTriangle size={20} /></span>
                <span className="metric-tag">DATA</span>
              </div>
              <p className="metric-label">Crisis data feed</p>
              <h3 className="metric-value">Not configured</h3>
              <div className="metric-foot">
                <span className="muted">Dataset/API not connected</span>
              </div>
            </article>
          </section>

          <section className="lower-grid">
            <article className="panel api-panel">
              <div className="panel-heading">
                <div>
                  <h3>API connectivity</h3>
                  <p>Endpoints identified in the current backend code.</p>
                </div>
                <span className="panel-icon"><Wifi size={19} /></span>
              </div>

              <div className="endpoint-row">
                <div className="method">GET</div>
                <div className="endpoint-info">
                  <strong>/</strong>
                  <span>Backend welcome endpoint</span>
                </div>
                <span className="endpoint-badge">Available in code</span>
              </div>
              <div className="endpoint-row">
                <div className="method">GET</div>
                <div className="endpoint-info">
                  <strong>/api/health</strong>
                  <span>Health check endpoint</span>
                </div>
                <span className={`endpoint-badge ${backend === "online" ? "success" : ""}`}>
                  {backend === "online" ? "Responding" : "Not verified"}
                </span>
              </div>

              <button
                className="text-link"
                onClick={() => window.open(`${API_BASE_URL}/docs`, "_blank", "noopener,noreferrer")}
              >
                Open API documentation <ArrowUpRight size={16} />
              </button>
            </article>

            <article className="panel readiness-panel">
              <div className="panel-heading">
                <div>
                  <h3>Integration checklist</h3>
                  <p>Current implementation milestones.</p>
                </div>
                <span className="panel-icon"><CheckCircle2 size={19} /></span>
              </div>
              <div className="check-row">
                <CheckCircle2 size={18} className="check-done" />
                <div><strong>React frontend created</strong><span>Vite development environment</span></div>
                <span className="mini-state done">DONE</span>
              </div>
              <div className="check-row">
                <CheckCircle2 size={18} className="check-done" />
                <div><strong>Health check implemented</strong><span>Connected to GET /api/health</span></div>
                <span className="mini-state done">DONE</span>
              </div>
              <div className="check-row">
                <Clock3 size={18} className="check-pending" />
                <div><strong>Dataset integration</strong><span>Awaiting dataset details</span></div>
                <span className="mini-state pending">NEXT</span>
              </div>
            </article>
          </section>

          <footer className="page-footer">
            <span>RIPPLE <span className="footer-separator">/</span> CAMPUS IN CRISIS</span>
            <span>Built for the Datathon <span className="footer-separator">·</span> Evidence-led response</span>
          </footer>
        </div>
      </main>
    </div>
  );
}
