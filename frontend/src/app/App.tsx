import {
  ArrowUpRight,
  ChevronRight,
  Clock3,
  Folder,
  LayoutGrid,
  MessageSquare,
  MoreHorizontal,
  Plus,
  Search,
  Settings2,
  Users,
  X,
  Zap,
} from "lucide-react";
import { useEffect, useRef, useState, type FormEvent } from "react";
import Badge from "../components/Badge";
import Modal from "../components/Modal";
import TeamIcon from "../components/TeamIcon";
import { demoProjects, initialDemoMessages } from "../data/demo";
import ChatPanel from "../features/chat/ChatPanel";
import CreateProject from "../features/projects/CreateProject";
import ProjectDetailsPanel from "../features/projects/ProjectDetailsPanel";
import ProjectsDirectory from "../features/projects/ProjectsDirectory";
import ExperimentRows from "../features/research/ExperimentRows";
import RunPanel, { statusLabels } from "../features/research/RunPanel";
import ResearchHistory from "../features/research/ResearchHistory";
import TeamCard from "../features/research/TeamCard";
import Materials from "../features/research/Materials";
import { shortText } from "../lib/presentation";
import ResearchPages from "../features/research/ResearchWorkspace";
import TeamStaffing from "../features/research/TeamStaffing";
import ProjectSettingsPanel from "../features/settings/ProjectSettingsPanel";
import SettingsPanel from "../features/settings/SettingsPanel";
import { api } from "../lib/api";
import type {
  ChatMessage,
  ChatIntent,
  Experiment,
  Project,
  Team,
  WorkspaceSettings,
} from "../types";

type Tab = "overview" | "teams" | "experiments" | "files" | "runs" | "pages";
const tabs: { id: Tab; label: string }[] = [
  { id: "overview", label: "Результаты" },
  { id: "files", label: "Материалы" },
  { id: "runs", label: "Исследование" },
];
const clock = () =>
  new Date().toLocaleTimeString("ru-RU", {
    hour: "2-digit",
    minute: "2-digit",
  });
const identifier = () => crypto.randomUUID();

function readDemoChat(): Record<string, ChatMessage[]> {
  try {
    return {
      ...initialDemoMessages,
      ...JSON.parse(localStorage.getItem("dolphinlab-demo-chat") || "{}"),
    };
  } catch {
    return initialDemoMessages;
  }
}

export default function App() {
  const [projects, setProjects] = useState<Project[]>(demoProjects);
  const [directory, setDirectory] = useState(false);
  const [settingsOpen, setSettingsOpen] = useState(false);
  const [projectSettingsOpen, setProjectSettingsOpen] = useState(false);
  const [projectDetailsOpen, setProjectDetailsOpen] = useState(false);
  const [settings, setSettings] = useState<WorkspaceSettings>({
    display_name: "Исследователь",
    base_url: "",
    model: "",
    key_configured: false,
    execution_enabled: false,
  });
  const [selectedId, setSelectedId] = useState(
    () => new URLSearchParams(location.search).get("project") || "",
  );
  const [tab, setTab] = useState<Tab>(() => {
    const v = new URLSearchParams(location.search).get("view");
    return ["overview", "files", "runs", "teams", "experiments"].includes(
      v || "",
    )
      ? (v as Tab)
      : "overview";
  });
  const [createOpen, setCreateOpen] = useState(false);
  const [searchOpen, setSearchOpen] = useState(false);
  const [query, setQuery] = useState("");
  const [team, setTeam] = useState<Team | null>(null);
  const [experiment, setExperiment] = useState<Experiment | null>(null);
  const [notice, setNotice] = useState("");
  const [eventsOpen, setEventsOpen] = useState(false);
  const [demoChats, setDemoChats] = useState(readDemoChat);
  const [messages, setMessages] = useState<ChatMessage[]>([]);
  const [draft, setDraft] = useState("");
  const chatIntent: ChatIntent = "auto";
  const directive = false;
  const [sending, setSending] = useState(false);
  const [chatMobile, setChatMobile] = useState(false);
  const [chatError, setChatError] = useState("");
  const [historyLoading, setHistoryLoading] = useState(false);
  const project = projects.find((p) => p.id === selectedId) || projects[0];
  const chatEnd = useRef<HTMLDivElement>(null);
  const timer = useRef<ReturnType<typeof setTimeout> | undefined>(undefined);
  const selectedRef = useRef(selectedId);
  useEffect(() => {
    selectedRef.current = selectedId;
    setDraft("");
    setChatError("");
  }, [selectedId]);
  useEffect(() => {
    api
      .settings()
      .then(setSettings)
      .catch(() => {});
    api
      .projects()
      .then((actual) => {
        setProjects([...actual, ...demoProjects]);
        setSelectedId((id) =>
          [...actual, ...demoProjects].some((p) => p.id === id)
            ? id
            : actual.find((p) => !p.archived)?.id || demoProjects[0].id,
        );
      })
      .catch(() => {
        /* Demo remains available without the local API. */
      });
    return () => clearTimeout(timer.current);
  }, []);
  useEffect(() => {
    try {
      localStorage.setItem("dolphinlab-demo-chat", JSON.stringify(demoChats));
    } catch {
      setChatError(
        "Не удалось сохранить демочат в браузере. Текущая переписка доступна до закрытия страницы.",
      );
    }
  }, [demoChats]);
  useEffect(() => {
    if (project.demo) {
      setMessages(demoChats[project.id] || []);
      setHistoryLoading(false);
      return;
    }
    let active = true;
    setMessages([]);
    setHistoryLoading(true);
    api
      .messages(project.id)
      .then((items) => {
        if (active) setMessages(items);
      })
      .catch((e) => {
        if (active) setChatError(e.message);
      })
      .finally(() => {
        if (active) setHistoryLoading(false);
      });
    const cursor = Math.max(
      0,
      ...project.events.map((event) => Number(event.id) || 0),
    );
    const events = new EventSource(
      `/api/projects/${project.id}/events?after=${cursor}`,
    );
    let refreshTimer: ReturnType<typeof setTimeout> | undefined;
    events.addEventListener("update", (event) => {
      try {
        const item = JSON.parse((event as MessageEvent).data);
        if (item.kind === "run_state")
          setNotice(
            `Исследование: ${statusLabels[item.data.name] || item.data.name}`,
          );
        if (item.kind === "run_error")
          setNotice("В исследовании возникла ошибка. Открой историю запуска.");
      } catch {
        /* Invalid notifications do not discard authoritative project state. */
      }
      if (refreshTimer) return;
      refreshTimer = setTimeout(() => {
        refreshTimer = undefined;
        api
          .project(project.id)
          .then((updated) => {
            if (active)
              setProjects((before) =>
                before.map((p) => (p.id === updated.id ? updated : p)),
              );
          })
          .catch(() => {});
        api
          .messages(project.id)
          .then((items) => {
            if (active)
              setMessages((before) =>
                JSON.stringify(before) === JSON.stringify(items)
                  ? before
                  : items,
              );
          })
          .catch(() => {});
      }, 150);
    });
    return () => {
      active = false;
      clearTimeout(refreshTimer);
      events.close();
    };
  }, [project.id, project.demo, demoChats]);
  useEffect(() => {
    chatEnd.current?.scrollIntoView({ behavior: "smooth", block: "nearest" });
  }, [messages, sending]);
  useEffect(() => {
    if (!notice) return;
    const id = setTimeout(() => setNotice(""), 7000);
    return () => clearTimeout(id);
  }, [notice]);
  useEffect(() => {
    const onKey = (e: KeyboardEvent) => {
      if ((e.metaKey || e.ctrlKey) && e.key === "k") {
        e.preventDefault();
        setSearchOpen((value) => !value);
      }
    };
    window.addEventListener("keydown", onKey);
    return () => window.removeEventListener("keydown", onKey);
  }, []);

  function select(id: string) {
    const params = new URLSearchParams(location.search);
    params.delete("page");
    params.delete("publication");
    history.replaceState(null, "", `${location.pathname}?${params}`);
    setDirectory(false);
    setSelectedId(id);
    setTab("overview");
    setEventsOpen(false);
    setChatMobile(false);
  }
  function refreshProject() {
    api
      .project(project.id)
      .then((updated) =>
        setProjects((before) =>
          before.map((item) => (item.id === updated.id ? updated : item)),
        ),
      )
      .catch((error) => setNotice(error.message));
  }
  async function send(e?: FormEvent, suggested?: string) {
    e?.preventDefault();
    const content = (suggested || draft).trim();
    if (!content || sending || historyLoading) return;
    const id = project.id;
    const isDirective = directive;
    setDraft("");
    setSending(true);
    setChatError("");
    if (project.demo) {
      const message: ChatMessage = {
        id: identifier(),
        role: "user",
        content,
        time: clock(),
        directive: isDirective,
      };
      setDemoChats((before) => ({
        ...before,
        [id]: [...(before[id] || []), message],
      }));
      timer.current = setTimeout(() => {
        let reply = isDirective
          ? "Поручение передано техлиду. В рабочей системе здесь появится его решение и связанное задание. Сейчас мы в демонстрации интерфейса — реальные исследования не запускаются."
          : /результат|прогресс|статус|готов/i.test(content)
            ? "В этом демопроекте лучший проверенный результат — ROC AUC 0.8914. Две команды исследуют новые подходы, ещё одна ждёт кандидата для проверки. Все показатели здесь вымышлены."
            : /команд|кто|занима/i.test(content)
              ? "«Базовая модель» сравнивает алгоритмы. «Признаки и гипотезы» проверяет динамику активности. «Надёжность» независимо проверяет результат. Нажми на команду, чтобы посмотреть состав и задания."
              : /стоп|останов|пауз/i.test(content)
                ? "В деморежиме ничего не исполняется. В рабочем проекте я передам техлиду запрос на остановку, а текущие задания и результаты будут сохранены."
                : "Это демочат для настройки интерфейса. Здесь можно обсудить прогресс или оформить поручение техлиду. Сообщения сохраняются в этом браузере; модель не вызывается.";
        if (id === "demo-demand" && !isDirective)
          reply =
            "В демонстрационном проекте исследование завершено: MAE 12.48. История диалога и результаты остаются доступны. Это вымышленные данные.";
        setDemoChats((before) => ({
          ...before,
          [id]: [
            ...(before[id] || []),
            {
              id: identifier(),
              role: "assistant",
              content: reply,
              time: clock(),
            },
          ],
        }));
        setSending(false);
      }, 800);
      return;
    }
    try {
      const message = await api.send(id, content, isDirective, chatIntent);
      if (selectedRef.current === id)
        setMessages((before) =>
          before.some((m) => m.id === message.id)
            ? before
            : [...before, message],
        );
    } catch (e) {
      if (selectedRef.current === id) {
        setDraft(content);
        setChatError((e as Error).message);
      }
    } finally {
      setSending(false);
    }
  }
  useEffect(() => {
    if (!selectedId) return;
    const params = new URLSearchParams(location.search);
    params.set("project", selectedId);
    params.set("view", tab);
    history.replaceState(null, "", `${location.pathname}?${params}`);
  }, [selectedId, tab]);

  return (
    <div className="workspace">
      <aside className="sidebar">
        <a
          className="brand"
          href="#"
          onClick={(e) => {
            e.preventDefault();
            setDirectory(true);
          }}
        >
          <span className="brand-mark">
            <Zap size={20} fill="currentColor" strokeWidth={1} />
          </span>
          DolphinLab<span className="brand-period">.</span>
        </a>
        <button className="search-button" onClick={() => setSearchOpen(true)}>
          <Search size={16} />
          <span>Найти проект</span>
          <kbd>Ctrl K</kbd>
        </button>
        <button
          className={`all-projects-link ${directory ? "active" : ""}`}
          onClick={() => setDirectory(true)}
        >
          <LayoutGrid size={16} />
          Все проекты<span>{projects.filter((p) => !p.demo).length}</span>
        </button>
        <div className="sidebar-section-title">
          <span>МОИ ПРОЕКТЫ</span>
          <button
            className="icon-button"
            aria-label="Новый проект"
            onClick={() => setCreateOpen(true)}
          >
            <Plus size={17} />
          </button>
        </div>
        <nav className="project-nav" aria-label="Проекты">
          {projects
            .filter((p) => !p.archived && !p.demo)
            .map((p) => (
              <button
                key={p.id}
                title={p.name}
                className={`project-nav-item ${!directory && p.id === project.id ? "active" : ""}`}
                onClick={() => select(p.id)}
              >
                <span className={`tiny-dot ${p.status}`} />
                <span>{p.name}</span>
              </button>
            ))}
          {!projects.some((p) => !p.demo && !p.archived) && (
            <p className="sidebar-empty">Здесь будут ваши исследования</p>
          )}
          <div className="sidebar-section-title demo-label">ПРИМЕРЫ</div>
          {projects
            .filter((p) => p.demo)
            .map((p) => (
              <button
                key={p.id}
                className={`project-nav-item ${!directory && p.id === project.id ? "active" : ""}`}
                onClick={() => select(p.id)}
              >
                <Folder size={14} />
                <span>{p.name}</span>
              </button>
            ))}
        </nav>
        <button
          className="profile-button"
          onClick={() => setSettingsOpen(true)}
        >
          <span className="workspace-avatar">
            {settings.display_name[0] || "Я"}
          </span>
          <span>
            {settings.display_name}
            <small>Настройки пространства</small>
          </span>
          <Settings2 size={15} />
        </button>
      </aside>
      <div className="main-shell">
        <header className="topbar">
          <button
            className="breadcrumb-home"
            onClick={() => setDirectory(true)}
          >
            Моё пространство
          </button>
          <ChevronRight size={13} />
          <span>{directory ? "Проекты" : project.name}</span>
          <div className="topbar-actions">
            <button
              className="icon-button"
              aria-label="Открыть чат"
              onClick={() => setChatMobile((v) => !v)}
            >
              <MessageSquare size={18} />
            </button>
            <button
              className="icon-button"
              aria-label="Управление проектом"
              onClick={() => setProjectDetailsOpen(true)}
            >
              <MoreHorizontal size={19} />
            </button>
          </div>
        </header>
        <main className="main-content">
          {directory ? (
            <ProjectsDirectory
              projects={projects}
              select={select}
              create={() => setCreateOpen(true)}
              name={settings.display_name}
            />
          ) : (
            <>
              <header className="project-heading">
                <div>
                  {tab !== "overview" && tab !== "pages" && (
                    <div className="project-kicker">
                      {project.demo
                        ? "ПРИМЕР ИССЛЕДОВАНИЯ"
                        : "ИССЛЕДОВАТЕЛЬСКОЕ ПРОСТРАНСТВО"}
                    </div>
                  )}
                  <h1>{project.name}</h1>
                </div>
                <button
                  className="icon-button"
                  aria-label="Настройки проекта"
                  onClick={() => setProjectSettingsOpen(true)}
                >
                  <Settings2 size={18} />
                </button>
              </header>
              {tab !== "overview" && tab !== "pages" && (
                <div className="project-intro">
                  <p>{shortText(project.description, 110)}</p>
                  <Badge
                    tone={project.status === "completed" ? "green" : "neutral"}
                  >
                    {project.archived
                      ? "В архиве"
                      : statusLabels[
                          project.latest_run?.status || project.status
                        ] || "Новый проект"}
                  </Badge>
                </div>
              )}
              <nav
                className="project-tabs"
                aria-label="Разделы проекта"
                role="tablist"
              >
                {tabs.map((t) => (
                  <button
                    role="tab"
                    key={t.id}
                    aria-selected={
                      t.id === tab ||
                      (t.id === "runs" &&
                        ["teams", "experiments"].includes(tab)) ||
                      (t.id === "overview" && tab === "pages")
                    }
                    className={
                      t.id === tab ||
                      (t.id === "runs" &&
                        ["teams", "experiments"].includes(tab))
                        ? "active"
                        : ""
                    }
                    onClick={() => setTab(t.id)}
                  >
                    {t.label}
                  </button>
                ))}
              </nav>
              {(tab === "overview" || tab === "pages") && (
                <ResearchPages
                  key={project.id}
                  project={project}
                  enabled={settings.execution_enabled}
                  onDiscuss={() => {
                    setChatMobile(true);
                    document.getElementById("chat-input")?.focus();
                  }}
                />
              )}
              {tab === "files" && (
                <Materials
                  key={project.id}
                  project={project}
                  changed={refreshProject}
                />
              )}
              {["runs", "teams", "experiments"].includes(tab) && (
                <>
                  <div className="section-heading">
                    <div>
                      <span className="eyebrow">ХОД РАБОТЫ</span>
                      <h2>От вопроса к результату</h2>
                    </div>
                  </div>
                  <RunPanel
                    project={project}
                    enabled={settings.execution_enabled}
                    history={false}
                    openHistory={() => setEventsOpen((v) => !v)}
                    changed={refreshProject}
                  />
                  {!project.demo && (
                    <details className="workspace-disclosure">
                      <summary>Цель и история исследования</summary>
                      <ResearchHistory
                        key={project.id}
                        project={project}
                        expanded
                        enabled={settings.execution_enabled}
                        changed={refreshProject}
                      />
                    </details>
                  )}
                  <section className="teams-section">
                    <div className="section-heading">
                      <h3>Направления исследования</h3>
                      <span className="muted">
                        {project.teams.length
                          ? `Всего ${project.teams.length}`
                          : "Состав подбирается под задачу"}
                      </span>
                    </div>
                    {project.teams.length ? (
                      <div className="team-grid">
                        {project.teams.map((t) => (
                          <TeamCard
                            key={t.id}
                            team={t}
                            open={() => setTeam(t)}
                          />
                        ))}
                      </div>
                    ) : (
                      <div className="quiet-empty">
                        <Users size={22} />
                        <span>
                          Обсудите задачу с помощником. После запуска он
                          подключит нужных специалистов.
                        </span>
                      </div>
                    )}
                  </section>
                  {!!project.experiments.length && (
                    <details className="workspace-disclosure">
                      <summary>
                        Выполненные расчёты · {project.experiments.length}
                      </summary>
                      <ExperimentRows
                        rows={project.experiments}
                        open={setExperiment}
                      />
                    </details>
                  )}
                  <details
                    className="workspace-disclosure"
                    open={eventsOpen}
                    onToggle={(e) => setEventsOpen(e.currentTarget.open)}
                  >
                    <summary>Журнал и технические подробности</summary>
                    <RunPanel
                      project={project}
                      enabled={settings.execution_enabled}
                      history
                      changed={refreshProject}
                      openHistory={() => setEventsOpen(false)}
                    />
                    <div className="activity-feed">
                      {project.events.map((event) => (
                        <details key={event.id}>
                          <summary>
                            {event.time} · {event.title}
                          </summary>
                          <p>{event.detail}</p>
                        </details>
                      ))}
                    </div>
                  </details>
                </>
              )}
              {project.demo && (
                <p className="demo-notice">
                  Демонстрационные данные · пример для знакомства с
                  пространством
                </p>
              )}
            </>
          )}
        </main>
      </div>
      <ChatPanel
        project={project}
        executionEnabled={settings.execution_enabled}
        mobileOpen={chatMobile}
        loading={historyLoading}
        sending={sending}
        messages={messages}
        draft={draft}
        onHistory={() => setTab("runs")}
        error={chatError}
        chatEnd={chatEnd}
        onClose={() => setChatMobile(false)}
        onNotice={setNotice}
        onDraft={setDraft}
        onSend={send}
        onFiles={() => setTab("files")}
        onRetry={(messageId) => {
          api
            .retry(project.id, messageId)
            .then(() => api.messages(project.id))
            .then(setMessages)
            .catch((e) => setChatError(e.message));
        }}
      />
      {projectDetailsOpen && (
        <Modal
          close={() => setProjectDetailsOpen(false)}
          label="Управление проектом"
        >
          <ProjectDetailsPanel
            project={project}
            close={() => setProjectDetailsOpen(false)}
            saved={(updated) => {
              setProjects((before) =>
                before.map((p) => (p.id === updated.id ? updated : p)),
              );
              setProjectDetailsOpen(false);
              setNotice(
                updated.archived ? "Проект в архиве" : "Проект обновлён",
              );
            }}
            deleted={(removed) => {
              setProjects((before) =>
                before.filter((p) => p.id !== project.id),
              );
              setSelectedId(demoProjects[0].id);
              setDirectory(true);
              setProjectDetailsOpen(false);
              setNotice(
                removed
                  ? "Проект удалён"
                  : "Записи удалены. Часть файлов не удалось убрать; проверь хранилище.",
              );
            }}
          />
        </Modal>
      )}
      {projectSettingsOpen && (
        <Modal
          close={() => setProjectSettingsOpen(false)}
          label="Настройки проекта"
          wide
        >
          <ProjectSettingsPanel
            project={project}
            close={() => setProjectSettingsOpen(false)}
            saved={() => {
              setProjectSettingsOpen(false);
              setNotice(
                project.demo
                  ? "Настройки демопроекта сохранены"
                  : "Настройки проекта сохранены для следующего запуска",
              );
            }}
          />
        </Modal>
      )}
      {settingsOpen && (
        <Modal
          close={() => setSettingsOpen(false)}
          label="Настройки профиля и API"
          wide
        >
          <SettingsPanel
            close={() => setSettingsOpen(false)}
            saved={(value) => {
              setSettings(value);
              setSettingsOpen(false);
              setNotice("Настройки сохранены");
            }}
          />
        </Modal>
      )}
      {createOpen && (
        <CreateProject
          close={() => setCreateOpen(false)}
          created={(created, warning) => {
            setProjects((before) => [...before, created]);
            select(created.id);
            setCreateOpen(false);
            if (warning) setNotice(warning);
          }}
        />
      )}
      {searchOpen && (
        <Modal
          close={() => {
            setSearchOpen(false);
            setQuery("");
          }}
          label="Поиск проекта"
        >
          <div className="search-modal-input">
            <Search size={20} />
            <input
              autoFocus
              placeholder="Название проекта…"
              value={query}
              onChange={(e) => setQuery(e.target.value)}
            />
          </div>
          <div className="search-results">
            {projects
              .filter((p) => p.name.toLowerCase().includes(query.toLowerCase()))
              .map((p) => (
                <button
                  key={p.id}
                  onClick={() => {
                    select(p.id);
                    setSearchOpen(false);
                    setQuery("");
                  }}
                >
                  <Folder size={17} />
                  <span>{p.name}</span>
                  {p.demo && <Badge>Демо</Badge>}
                  <ArrowUpRight size={15} />
                </button>
              ))}
          </div>
        </Modal>
      )}
      {team && (
        <Modal close={() => setTeam(null)} label={team.name} wide>
          <div className={`team-symbol large ${team.icon}`}>
            <TeamIcon type={team.icon} size={25} />
          </div>
          <div className="team-modal-title">
            <h2>{shortText(team.name, 110)}</h2>
            <Badge tone={team.state === "running" ? "green" : "neutral"}>
              {team.state === "running"
                ? "В работе"
                : team.state === "closed"
                  ? "Завершена"
                  : "Ожидание"}
            </Badge>
          </div>
          <p className="muted">{shortText(team.focus, 240)}</p>
          <details className="technical-details">
            <summary>Внутренняя задача направления</summary>
            <p>{team.current}</p>
            <p>{team.composition_reason}</p>
          </details>
          <TeamStaffing team={team} />
        </Modal>
      )}
      {experiment && (
        <Modal close={() => setExperiment(null)} label={experiment.name}>
          <span className="eyebrow">РАСЧЁТ</span>
          <h2 className="experiment-modal-title">{experiment.name}</h2>
          <p className="muted">{experiment.team}</p>
          <div className="experiment-detail-score">
            <span>{experiment.score}</span>
            <Badge
              tone={experiment.status === "verified" ? "green" : "neutral"}
            >
              {experiment.status === "verified"
                ? "Проверен"
                : experiment.status === "running"
                  ? "Выполняется"
                  : experiment.status === "succeeded"
                    ? "Код выполнен"
                    : experiment.status === "failed"
                      ? "Ошибка кода"
                      : experiment.status === "cancelled"
                        ? "Отменён"
                        : "Без улучшения"}
            </Badge>
          </div>
          <p className="detail-copy">{experiment.detail}</p>
          <div className="experiment-detail-footer">
            <Clock3 size={15} />
            {experiment.duration}
          </div>
        </Modal>
      )}
      {notice && (
        <div className="toast" role="status">
          <span>{notice}</span>
          <button
            className="icon-button"
            onClick={() => setNotice("")}
            aria-label="Закрыть уведомление"
          >
            <X size={15} />
          </button>
        </div>
      )}
    </div>
  );
}
