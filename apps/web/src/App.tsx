import {
  useEffect,
  useRef,
  useState,
  type FormEvent,
  type ReactNode,
} from "react";
import {
  Activity,
  ArrowDownToLine,
  ArrowRight,
  ArrowUp,
  ArrowUpRight,
  Check,
  CheckCheck,
  CheckCircle2,
  ChevronDown,
  ChevronRight,
  Circle,
  Clock3,
  Command,
  File,
  FileText,
  FlaskConical,
  Folder,
  FolderOpen,
  Layers3,
  LayoutGrid,
  Loader2,
  MessageSquare,
  MoreHorizontal,
  Paperclip,
  Plus,
  Search,
  Settings2,
  ShieldCheck,
  Sparkles,
  Upload,
  Users,
  X,
  Zap,
} from "lucide-react";
import { api } from "./api";
import { demoProjects, initialDemoMessages } from "./demo";
import type {
  ChatMessage,
  Experiment,
  Project,
  Team,
  WorkspaceSettings,
} from "./types";
import SettingsPanel from "./SettingsPanel";
import ProjectsDirectory from "./ProjectsDirectory";
import ProjectSettingsPanel from "./ProjectSettingsPanel";

type Tab = "overview" | "teams" | "experiments" | "files";
const tabs: { id: Tab; label: string }[] = [
  { id: "overview", label: "Обзор" },
  { id: "teams", label: "Команды" },
  { id: "experiments", label: "Эксперименты" },
  { id: "files", label: "Файлы" },
];
const clock = () =>
  new Date().toLocaleTimeString("ru-RU", {
    hour: "2-digit",
    minute: "2-digit",
  });
const identifier = () => crypto.randomUUID();
function size(bytes: number) {
  return bytes > 1048576
    ? `${(bytes / 1048576).toFixed(1)} МБ`
    : `${Math.max(1, Math.round(bytes / 1024))} КБ`;
}
function readDemoChat(): Record<string, ChatMessage[]> {
  try {
    return {
      ...initialDemoMessages,
      ...JSON.parse(localStorage.getItem("sonder-demo-chat") || "{}"),
    };
  } catch {
    return initialDemoMessages;
  }
}
function TeamIcon({
  type,
  size: px = 19,
}: {
  type: Team["icon"];
  size?: number;
}) {
  return type === "layers" ? (
    <Layers3 size={px} />
  ) : type === "shield" ? (
    <ShieldCheck size={px} />
  ) : (
    <Sparkles size={px} />
  );
}
function Badge({
  children,
  tone = "neutral",
}: {
  children: ReactNode;
  tone?: string;
}) {
  return <span className={`badge ${tone}`}>{children}</span>;
}
function Modal({
  children,
  close,
  label,
  wide = false,
}: {
  children: ReactNode;
  close: () => void;
  label: string;
  wide?: boolean;
}) {
  useEffect(() => {
    const before = document.activeElement as HTMLElement;
    const handler = (e: KeyboardEvent) => {
      if (e.key === "Escape") close();
    };
    document.addEventListener("keydown", handler);
    return () => {
      document.removeEventListener("keydown", handler);
      before?.focus();
    };
  }, [close]);
  return (
    <div
      className="modal-backdrop"
      onMouseDown={(e) => {
        if (e.target === e.currentTarget) close();
      }}
    >
      <section
        className={`modal ${wide ? "wide" : ""}`}
        role="dialog"
        aria-modal="true"
        aria-label={label}
      >
        <button
          className="icon-button modal-close"
          onClick={close}
          aria-label="Закрыть"
        >
          <X size={19} />
        </button>
        {children}
      </section>
    </div>
  );
}

function CreateProject({
  close,
  created,
}: {
  close: () => void;
  created: (project: Project, warning?: string) => void;
}) {
  const [name, setName] = useState("");
  const [description, setDescription] = useState("");
  const [files, setFiles] = useState<File[]>([]);
  const [busy, setBusy] = useState(false);
  const [error, setError] = useState("");
  const [drag, setDrag] = useState(false);
  const picker = useRef<HTMLInputElement>(null);
  const addFiles = (items: File[]) =>
    setFiles((before) => [
      ...before,
      ...items.filter(
        (item) =>
          !before.some((f) => f.name === item.name && f.size === item.size),
      ),
    ]);
  async function submit(e: FormEvent) {
    e.preventDefault();
    if (!name.trim() || busy) return;
    setBusy(true);
    setError("");
    let project: Project;
    try {
      project = await api.create(name.trim(), description.trim());
    } catch (e) {
      setError((e as Error).message);
      setBusy(false);
      return;
    }
    const failures: string[] = [];
    for (const file of files) {
      try {
        project.files.push(await api.upload(project.id, file));
      } catch {
        failures.push(file.name);
      }
    }
    created(
      project,
      failures.length
        ? `Проект сохранён. Не удалось загрузить: ${failures.join(", ")}. Повтори загрузку во вкладке «Файлы».`
        : undefined,
    );
  }
  return (
    <Modal close={close} label="Новый проект">
      <div className="modal-symbol">
        <FolderPlusIcon />
      </div>
      <h2>Новое исследование</h2>
      <p className="muted modal-intro">
        Задача, данные и пространство для команды.
      </p>
      <form onSubmit={submit}>
        <label className="field-label">
          Название
          <input
            autoFocus
            required
            maxLength={120}
            placeholder="Например, прогноз спроса"
            value={name}
            onChange={(e) => setName(e.target.value)}
          />
        </label>
        <label className="field-label">
          Задача
          <textarea
            rows={3}
            placeholder="Что нужно получить? Какие ограничения важны?"
            value={description}
            onChange={(e) => setDescription(e.target.value)}
            maxLength={20000}
          />
        </label>
        <input
          ref={picker}
          type="file"
          multiple
          hidden
          onChange={(e) => {
            addFiles(Array.from(e.target.files || []));
            e.target.value = "";
          }}
        />
        <button
          type="button"
          className={`dropzone ${drag ? "dragging" : ""}`}
          onClick={() => picker.current?.click()}
          onDragOver={(e) => {
            e.preventDefault();
            setDrag(true);
          }}
          onDragLeave={() => setDrag(false)}
          onDrop={(e) => {
            e.preventDefault();
            setDrag(false);
            addFiles(Array.from(e.dataTransfer.files));
          }}
        >
          <Upload size={23} />
          <span>
            Перетащи файлы или <b>выбери на компьютере</b>
          </span>
          <small>Датасет, task.md, пример результата · до 100 МБ на файл</small>
        </button>
        {files.length > 0 && (
          <ul className="upload-list">
            {files.map((f, i) => (
              <li key={`${f.name}-${i}`}>
                <FileText size={16} />
                <span>{f.name}</span>
                <small>{size(f.size)}</small>
                <button
                  type="button"
                  className="icon-button"
                  aria-label={`Убрать ${f.name}`}
                  onClick={() =>
                    setFiles((before) =>
                      before.filter((_, index) => index !== i),
                    )
                  }
                >
                  <X size={14} />
                </button>
              </li>
            ))}
          </ul>
        )}
        {error && (
          <p className="form-error" role="alert">
            {error}
          </p>
        )}
        <div className="modal-actions">
          <button
            type="button"
            className="button ghost"
            onClick={close}
            disabled={busy}
          >
            Отмена
          </button>
          <button
            className="button primary"
            type="submit"
            disabled={!name.trim() || busy}
          >
            {busy ? <Loader2 size={16} className="spin" /> : <Plus size={16} />}
            {busy ? "Сохраняем…" : "Создать проект"}
          </button>
        </div>
      </form>
    </Modal>
  );
}
function FolderPlusIcon() {
  return <FolderOpen size={25} strokeWidth={1.5} />;
}

function TeamCard({ team, open }: { team: Team; open: () => void }) {
  return (
    <button className={`team-card ${team.state}`} onClick={open}>
      <div className="team-card-top">
        <span className={`team-symbol ${team.icon}`}>
          <TeamIcon type={team.icon} />
        </span>
        <span className={`state-dot ${team.state}`} />
        <span className="team-open">
          <ArrowUpRight size={17} />
        </span>
      </div>
      <h3>{team.name}</h3>
      <p className="team-focus">{team.focus}</p>
      <div className="team-stage">
        <span className={`tiny-dot ${team.state}`} />
        {team.stage}
      </div>
      <p className="team-description">{team.current}</p>
      <div className="team-card-bottom">
        <div className="avatar-stack">
          {team.members.map((m) => (
            <span
              key={m.name}
              className={`avatar ${m.color}`}
              title={`${m.name} · ${m.role}`}
            >
              {m.name[0]}
            </span>
          ))}
        </div>
        <span className="team-updated">{team.updated}</span>
      </div>
    </button>
  );
}
function ExperimentRows({
  rows,
  open,
}: {
  rows: Experiment[];
  open: (experiment: Experiment) => void;
}) {
  return (
    <div className="experiment-list">
      <div className="experiment-head">
        <span>Эксперимент</span>
        <span>Результат</span>
        <span>Время</span>
        <span />
      </div>
      {rows.map((exp) => (
        <button
          className="experiment-row"
          key={exp.id}
          onClick={() => open(exp)}
        >
          <span className={`experiment-status ${exp.status}`}>
            {exp.status === "running" ? (
              <Loader2 className="spin" size={17} />
            ) : exp.status === "verified" ? (
              <CheckCircle2 size={17} />
            ) : (
              <Circle size={17} />
            )}
          </span>
          <span className="experiment-name">
            <strong>{exp.name}</strong>
            <small>
              {exp.id} <span>·</span> {exp.team}
            </small>
          </span>
          <span
            className={`experiment-score ${exp.status === "verified" ? "scored" : ""}`}
          >
            {exp.score}
            {exp.status === "running" && <small>Обучается</small>}
          </span>
          <span className="experiment-duration">{exp.duration}</span>
          <ChevronRight size={15} />
        </button>
      ))}
    </div>
  );
}

export default function App() {
  const [projects, setProjects] = useState<Project[]>(demoProjects);
  const [directory, setDirectory] = useState(false);
  const [settingsOpen, setSettingsOpen] = useState(false);
  const [projectSettingsOpen, setProjectSettingsOpen] = useState(false);
  const [settings, setSettings] = useState<WorkspaceSettings>({
    display_name: "Исследователь",
    base_url: "",
    model: "",
    key_configured: false,
    execution_enabled: false,
  });
  const [selectedId, setSelectedId] = useState(demoProjects[0].id);
  const [tab, setTab] = useState<Tab>("overview");
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
  const [directive, setDirective] = useState(false);
  const [sending, setSending] = useState(false);
  const [uploading, setUploading] = useState(false);
  const [chatMobile, setChatMobile] = useState(false);
  const [chatError, setChatError] = useState("");
  const [historyLoading, setHistoryLoading] = useState(false);
  const project = projects.find((p) => p.id === selectedId) || projects[0];
  const chatEnd = useRef<HTMLDivElement>(null);
  const uploadPicker = useRef<HTMLInputElement>(null);
  const timer = useRef<ReturnType<typeof setTimeout> | undefined>(undefined);
  const selectedRef = useRef(selectedId);
  useEffect(() => {
    selectedRef.current = selectedId;
    setDraft("");
    setChatError("");
    setDirective(false);
  }, [selectedId]);
  useEffect(() => {
    api
      .settings()
      .then(setSettings)
      .catch(() => {});
    api
      .projects()
      .then((actual) => setProjects([...demoProjects, ...actual]))
      .catch(() => {
        /* Demo remains available without the local API. */
      });
    return () => clearTimeout(timer.current);
  }, []);
  useEffect(() => {
    try {
      localStorage.setItem("sonder-demo-chat", JSON.stringify(demoChats));
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
    const events = new EventSource(`/api/projects/${project.id}/events`);
    events.addEventListener("update", () => {
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
          if (active) setMessages(items);
        })
        .catch(() => {});
    });
    return () => {
      active = false;
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
    setDirectory(false);
    setSelectedId(id);
    setTab("overview");
    setEventsOpen(false);
    setChatMobile(false);
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
      const message = await api.send(id, content, isDirective);
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
  async function upload(files: FileList | null) {
    if (!files?.length) return;
    if (project.demo) {
      setNotice(
        "Это демонстрационный проект. Создай свой, чтобы загрузить данные.",
      );
      return;
    }
    setUploading(true);
    const id = project.id;
    for (const file of Array.from(files)) {
      try {
        const saved = await api.upload(id, file);
        setProjects((before) =>
          before.map((p) =>
            p.id === id ? { ...p, files: [...p.files, saved] } : p,
          ),
        );
      } catch (e) {
        setNotice((e as Error).message);
      }
    }
    setUploading(false);
  }
  function downloadDemo() {
    const text =
      "# Демоотчёт Sonder\n\nВымышленные данные для просмотра дизайна.\nПроект: " +
      project.name +
      "\nМетрика: " +
      project.metric +
      "\nРезультат: " +
      project.score +
      "\n\nЭто не результат реального ML-эксперимента.\n";
    const url = URL.createObjectURL(
      new Blob([text], { type: "text/markdown;charset=utf-8" }),
    );
    const a = document.createElement("a");
    a.href = url;
    a.download = "demo-report.md";
    a.click();
    URL.revokeObjectURL(url);
  }

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
          sonder<span className="brand-period">.</span>
        </a>
        <button className="workspace-switch" onClick={() => setDirectory(true)}>
          <span className="workspace-avatar">
            {settings.display_name[0]?.toUpperCase() || "Я"}
          </span>
          <span>
            Личное пространство<small>{settings.display_name}</small>
          </span>
          <ChevronDown size={14} />
        </button>
        <button className="search-button" onClick={() => setSearchOpen(true)}>
          <Search size={16} />
          <span>Найти проект</span>
          <kbd>⌘ K</kbd>
        </button>
        <button
          aria-label="Все проекты"
          className={`all-projects-link ${directory ? "active" : ""}`}
          onClick={() => {
            setDirectory(true);
            setEventsOpen(false);
          }}
        >
          <LayoutGrid size={15} />
          Все проекты<span>{projects.length}</span>
        </button>
        <div className="sidebar-section-title">
          <span>ПРОЕКТЫ</span>
          <button
            className="icon-button"
            onClick={() => setCreateOpen(true)}
            aria-label="Новый проект"
          >
            <Plus size={16} />
          </button>
        </div>
        <nav className="project-nav" aria-label="Проекты">
          {projects.map((p) => (
            <button
              key={p.id}
              className={`project-nav-item ${!directory && project.id === p.id ? "active" : ""}`}
              onClick={() => select(p.id)}
            >
              <span className={`nav-project-dot ${p.status}`} />
              <span>{p.name}</span>
              {p.demo && <span className="nav-demo">Демо</span>}
            </button>
          ))}
        </nav>
        <button
          className="new-project-button"
          onClick={() => setCreateOpen(true)}
        >
          <Plus size={16} />
          Новый проект
        </button>
        <div className="sidebar-bottom">
          <div className="demo-note">
            <span className="demo-note-icon">
              <Sparkles size={17} />
            </span>
            <strong>Пространство для идей</strong>
            <p>
              От первого вопроса
              <br />
              до проверенного результата.
            </p>
          </div>
          <button
            className="profile-button"
            onClick={() => setSettingsOpen(true)}
          >
            <span className="profile-avatar">
              {settings.display_name[0]?.toUpperCase() || "Я"}
            </span>
            <span>
              {settings.display_name}
              <small>Настройки и API</small>
            </span>
            <Settings2 size={16} />
          </button>
        </div>
      </aside>
      <div className="project-area">
        <header className="topbar">
          <div className="breadcrumbs">
            <Folder size={15} />
            <button
              className="breadcrumb-link"
              onClick={() => setDirectory(true)}
            >
              Проекты
            </button>
            {!directory && (
              <>
                <ChevronRight size={12} />
                <strong>{project.name}</strong>
              </>
            )}
          </div>
          <div className="topbar-actions">
            <button
              className="icon-button"
              onClick={() => setSettingsOpen(true)}
              aria-label="Настройки профиля и API"
              title="Настройки профиля и API"
            >
              <Settings2 size={17} />
            </button>
            {!directory && project.demo && (
              <span className="demo-badge">
                <span />
                Деморежим
              </span>
            )}
            {!directory && (
              <button
                className={`icon-button ${eventsOpen ? "selected" : ""}`}
                onClick={() => setEventsOpen(!eventsOpen)}
                aria-label="События проекта"
                title="События проекта"
              >
                <Activity size={18} />
              </button>
            )}
            <button
              className="icon-button mobile-chat-button"
              onClick={() => setChatMobile(!chatMobile)}
              aria-label="Чат с PM"
            >
              <MessageSquare size={18} />
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
              <div className="project-heading">
                <div>
                  <div className="eyebrow">
                    ИССЛЕДОВАНИЕ <span>/</span>{" "}
                    {project.demo
                      ? "DEMO"
                      : project.id.slice(0, 6).toUpperCase()}
                  </div>
                  <h1>{project.name}</h1>
                  <p>
                    {project.description ||
                      "Добавь описание задачи и исходные данные."}
                  </p>
                </div>
                <div className="project-heading-actions">
                  <button
                    className="project-settings-button"
                    onClick={() => setProjectSettingsOpen(true)}
                    title="Настройки проекта"
                  >
                    <Settings2 size={14} />
                    <span>Настроить</span>
                  </button>
                  <Badge
                    tone={
                      project.status === "running"
                        ? "green"
                        : project.status === "completed"
                          ? "neutral"
                          : "amber"
                    }
                  >
                    <span className={`tiny-dot ${project.status}`} />
                    {project.status === "running"
                      ? "В работе"
                      : project.status === "completed"
                        ? "Завершён"
                        : "Черновик"}
                  </Badge>
                </div>
              </div>
              <div className="tabs" role="tablist" aria-label="Разделы проекта">
                {tabs.map((t) => (
                  <button
                    role="tab"
                    aria-selected={tab === t.id}
                    className={tab === t.id ? "active" : ""}
                    onClick={() => setTab(t.id)}
                    key={t.id}
                  >
                    {t.label}
                    {t.id === "teams" && project.teams.length > 0 && (
                      <span>{project.teams.length}</span>
                    )}
                  </button>
                ))}
                <span className="tabs-trailing">
                  {project.demo ? "Обновлено только что" : "Данные сохранены"}
                </span>
              </div>
              {eventsOpen && (
                <section className="events-popover">
                  <div className="section-heading">
                    <h2>События проекта</h2>
                    <button
                      className="icon-button"
                      onClick={() => setEventsOpen(false)}
                      aria-label="Закрыть события"
                    >
                      <X size={16} />
                    </button>
                  </div>
                  {project.events.length ? (
                    project.events.map((event) => (
                      <div className="popover-event" key={event.id}>
                        <small>{event.time}</small>
                        <div>
                          <strong>{event.title}</strong>
                          <p>{event.detail}</p>
                        </div>
                      </div>
                    ))
                  ) : (
                    <p className="muted">
                      События появятся после запуска исследования.
                    </p>
                  )}
                </section>
              )}

              {(tab === "overview" || tab === "teams") && (
                <>
                  {tab === "overview" && (
                    <div className="summary-grid">
                      <section className="best-result">
                        <div className="summary-label">
                          <span className="result-check">
                            <Check size={13} />
                          </span>
                          Лучший результат
                          <span className="metric-label">
                            {project.metric || "Метрика не выбрана"}
                          </span>
                        </div>
                        <div className="result-value">
                          {project.score || "—"}
                          {project.score && project.id === "demo-retention" && (
                            <span>
                              <ArrowUpRight size={13} />
                              +0.0182
                            </span>
                          )}
                        </div>
                        <div className="result-bottom">
                          <span>
                            {project.score
                              ? "Независимая проверка пройдена"
                              : "Появится после первого эксперимента"}
                          </span>
                          {project.score && (
                            <button
                              onClick={downloadDemo}
                              aria-label="Скачать демонстрационный отчёт"
                              title="Демонстрационный отчёт"
                            >
                              <ArrowDownToLine size={15} />
                            </button>
                          )}
                        </div>
                        <div className="mini-chart" aria-hidden="true">
                          <svg viewBox="0 0 140 60">
                            <path d="M0 55L20 49L38 51L57 35L76 38L94 18L112 21L140 2" />
                          </svg>
                        </div>
                      </section>
                      <section className="summary-card">
                        <span className="summary-label">
                          <Users size={15} />
                          Команды
                        </span>
                        <div className="stat-value">
                          {project.teams.length}
                          <small>
                            {project.teams.length
                              ? `${project.teams.filter((t) => t.state === "running").length} исследуют`
                              : "готовы к формированию"}
                          </small>
                        </div>
                        <div className="summary-bottom">
                          <span className="tiny-dot running" />
                          {project.teams.reduce(
                            (n, t) => n + t.members.length,
                            0,
                          )}{" "}
                          агентов в проекте
                        </div>
                      </section>
                      <section className="summary-card">
                        <span className="summary-label">
                          <FlaskConical size={15} />
                          Эксперименты
                        </span>
                        <div className="stat-value">
                          {project.experiments.length}
                          <small>
                            {
                              project.experiments.filter(
                                (e) => e.status === "verified",
                              ).length
                            }{" "}
                            проверено
                          </small>
                        </div>
                        <div className="summary-bottom">
                          <Clock3 size={13} />
                          {project.experiments.some(
                            (e) => e.status === "running",
                          )
                            ? "Один эксперимент выполняется"
                            : "Нет активных экспериментов"}
                        </div>
                      </section>
                    </div>
                  )}
                  <section className="teams-section">
                    <div className="section-heading">
                      <h2>
                        Команды{" "}
                        <span>
                          {project.teams.length
                            ? String(project.teams.length).padStart(2, "0")
                            : ""}
                        </span>
                      </h2>
                      {project.teams.length > 0 && (
                        <span className="section-hint">
                          <span className="tiny-dot running" />
                          Работают автономно
                        </span>
                      )}
                    </div>
                    {project.teams.length > 0 ? (
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
                      <div className="empty-state">
                        <span className="empty-icon">
                          <Users size={27} strokeWidth={1.3} />
                        </span>
                        <h3>
                          {project.status === "completed"
                            ? "Работа завершена"
                            : "Здесь соберётся команда"}
                        </h3>
                        <p>
                          {project.demo
                            ? "Открой «Отток клиентов», чтобы посмотреть активное исследование."
                            : "Проект сохранён. Загрузка данных и история чата уже доступны; исполнение агентов подключается следующим этапом."}
                        </p>
                        {!project.demo && (
                          <button
                            className="button secondary"
                            onClick={() => setTab("files")}
                          >
                            <Upload size={15} />
                            Добавить данные
                          </button>
                        )}
                      </div>
                    )}
                  </section>
                </>
              )}
              {(tab === "overview" || tab === "experiments") && (
                <section className="experiments-section">
                  <div className="section-heading">
                    <h2>
                      {tab === "overview"
                        ? "Последние эксперименты"
                        : "Эксперименты"}
                    </h2>
                    {tab === "overview" && project.experiments.length > 0 && (
                      <button
                        className="text-button"
                        onClick={() => setTab("experiments")}
                      >
                        Все эксперименты
                        <ArrowRight size={14} />
                      </button>
                    )}
                  </div>
                  {project.experiments.length ? (
                    <ExperimentRows
                      rows={
                        tab === "overview"
                          ? project.experiments.slice(0, 3)
                          : project.experiments
                      }
                      open={setExperiment}
                    />
                  ) : (
                    <div className="quiet-empty">
                      <FlaskConical size={20} />
                      <span>Пока нет экспериментов</span>
                    </div>
                  )}
                </section>
              )}
              {tab === "overview" && (
                <section className="activity-section">
                  <div className="section-heading">
                    <h2>Ход исследования</h2>
                    <span className="section-hint">Последние события</span>
                  </div>
                  <div className="activity-feed">
                    {project.events.map((event, index) => (
                      <div className="feed-row" key={event.id}>
                        <span className="feed-time">{event.time}</span>
                        <div className={`feed-marker ${event.type}`}>
                          {event.type === "result" ? (
                            <Check size={11} />
                          ) : (
                            <span />
                          )}
                          {index < project.events.length - 1 && <i />}
                        </div>
                        <div>
                          <strong>{event.title}</strong>
                          <p>{event.detail}</p>
                        </div>
                      </div>
                    ))}
                    {!project.events.length && (
                      <p className="muted">
                        Здесь появится история работы команды.
                      </p>
                    )}
                  </div>
                </section>
              )}
              {tab === "files" && (
                <section>
                  <div className="section-heading">
                    <h2>
                      Файлы проекта <span>{project.files.length}</span>
                    </h2>
                    <button
                      className="button secondary small"
                      disabled={uploading}
                      onClick={() =>
                        project.demo
                          ? setNotice(
                              "Создай свой проект, чтобы загрузить файлы.",
                            )
                          : uploadPicker.current?.click()
                      }
                    >
                      {uploading ? (
                        <Loader2 size={14} className="spin" />
                      ) : (
                        <Plus size={14} />
                      )}
                      Добавить файлы
                    </button>
                  </div>
                  <input
                    type="file"
                    ref={uploadPicker}
                    hidden
                    multiple
                    onChange={(e) => {
                      void upload(e.target.files);
                      e.target.value = "";
                    }}
                  />
                  <div className="file-list">
                    {project.files.map((file) => (
                      <div className="file-row" key={file.id}>
                        <span
                          className={`file-symbol ${file.name.endsWith(".md") ? "markdown" : ""}`}
                        >
                          {file.name.endsWith(".md") ? (
                            <FileText size={21} />
                          ) : (
                            <File size={21} />
                          )}
                        </span>
                        <span>
                          <strong>{file.name}</strong>
                          <small>{file.kind}</small>
                        </span>
                        <span className="file-size">{size(file.size)}</span>
                        {project.demo ? (
                          <Badge>Демо</Badge>
                        ) : (
                          <a
                            className="icon-button"
                            href={`/api/projects/${project.id}/files/${file.id}`}
                            aria-label={`Скачать ${file.name}`}
                          >
                            <ArrowDownToLine size={17} />
                          </a>
                        )}
                      </div>
                    ))}
                  </div>
                  {!project.files.length && (
                    <button
                      className="dropzone file-empty"
                      onClick={() => uploadPicker.current?.click()}
                    >
                      <Upload size={25} />
                      <span>Добавить исходные данные</span>
                      <small>Датасет, task.md или пример результата</small>
                    </button>
                  )}
                </section>
              )}
              <footer className="content-footer">
                <span className="footer-spark">✳</span>{" "}
                {project.demo
                  ? "Демонстрационный проект · все результаты вымышлены"
                  : "Локальный проект · модель не вызывается"}
              </footer>
            </>
          )}
        </main>
      </div>

      <aside
        className={`chat-panel ${chatMobile ? "mobile-open" : ""}`}
        aria-label="Чат с project manager"
      >
        <header className="chat-header">
          <span className="pm-avatar">
            <Sparkles size={19} strokeWidth={1.5} />
            <i />
          </span>
          <div>
            <h2>Project manager</h2>
            <span>{project.demo ? "На связи" : "История проекта"}</span>
          </div>
          <button
            className="icon-button"
            title="О project manager"
            aria-label="О project manager"
            onClick={() =>
              setNotice(
                project.demo
                  ? "Демочат: ответы подготовлены для просмотра дизайна, модель не вызывается."
                  : "Сообщения сохраняются на сервере. Ответы PM будут доступны после подключения исполнения агентов.",
              )
            }
          >
            <MoreHorizontal size={20} />
          </button>
          <button
            className="icon-button mobile-chat-button"
            onClick={() => setChatMobile(false)}
            aria-label="Скрыть чат"
          >
            <X size={18} />
          </button>
        </header>
        <div className="chat-scope">
          <span className="tiny-dot" />
          {project.name}
          <ChevronDown size={12} />
        </div>
        <div className="chat-messages">
          <div className="chat-day">
            {project.demo ? "Сегодня · демонстрация" : "История переписки"}
          </div>
          {historyLoading && (
            <p className="chat-loading">
              <Loader2 size={16} className="spin" />
              Загружаем историю…
            </p>
          )}
          {!historyLoading && messages.length === 0 && (
            <div className="chat-welcome">
              <Sparkles size={26} strokeWidth={1.3} />
              <h3>Начнём с задачи</h3>
              <p>
                Расскажи, что хочешь исследовать. Переписка останется в проекте.
              </p>
            </div>
          )}
          {messages.map((message) => (
            <div className={`chat-message ${message.role}`} key={message.id}>
              {message.role === "assistant" && (
                <div className="message-author">
                  <span className="mini-pm">
                    <Sparkles size={12} />
                  </span>
                  Project manager<span>{message.time}</span>
                </div>
              )}
              {message.directive && (
                <span className="directive-label">
                  <ArrowUpRight size={11} />
                  Поручение техлиду
                </span>
              )}
              <div className="message-body">{message.content}</div>
              {message.role === "user" && (
                <div className="message-time">
                  {message.time}
                  <CheckCheck size={12} />
                </div>
              )}
              {message.status === "pending" && (
                <div className="pending-message">
                  Сохранено · ожидает подключения PM
                </div>
              )}
            </div>
          ))}
          {sending && (
            <div className="typing">
              <span />
              <span />
              <span />
            </div>
          )}
          <div ref={chatEnd} />
        </div>
        <div className="chat-compose-area">
          {project.demo && (
            <div className="suggestions">
              <button
                onClick={() => void send(undefined, "Какой сейчас прогресс?")}
                disabled={sending}
              >
                Какой прогресс?
                <ArrowUpRight size={12} />
              </button>
              <button
                onClick={() => {
                  setDirective(true);
                  document.getElementById("chat-input")?.focus();
                }}
              >
                Дать поручение
                <Plus size={12} />
              </button>
            </div>
          )}
          {chatError && (
            <p className="form-error" role="alert">
              {chatError}
            </p>
          )}
          <form
            className={`chat-compose ${directive ? "directive" : ""}`}
            onSubmit={send}
          >
            {directive && (
              <div className="compose-mode">
                <ArrowUpRight size={12} />
                Поручение техлиду
                <button
                  type="button"
                  onClick={() => setDirective(false)}
                  aria-label="Обычное сообщение"
                >
                  <X size={12} />
                </button>
              </div>
            )}
            <textarea
              id="chat-input"
              value={draft}
              onChange={(e) => setDraft(e.target.value)}
              placeholder={
                directive
                  ? "Что нужно изменить в работе?"
                  : "Спроси или предложи идею…"
              }
              rows={2}
              maxLength={20000}
              onKeyDown={(e) => {
                if (e.key === "Enter" && !e.shiftKey) {
                  e.preventDefault();
                  void send();
                }
              }}
            />
            <div className="compose-tools">
              <button
                type="button"
                className="icon-button"
                onClick={() => {
                  setTab("files");
                  setChatMobile(false);
                }}
                aria-label="Файлы проекта"
                title="Файлы проекта"
              >
                <Paperclip size={16} />
              </button>
              <button
                type="button"
                className={`compose-directive ${directive ? "active" : ""}`}
                onClick={() => setDirective(!directive)}
                title="Передать поручение техлиду"
              >
                <Command size={13} />
                Поручение
              </button>
              <button
                className="send-button"
                type="submit"
                aria-label="Отправить сообщение"
                disabled={!draft.trim() || sending || historyLoading}
              >
                {sending ? (
                  <Loader2 size={16} className="spin" />
                ) : (
                  <ArrowUp size={18} />
                )}
              </button>
            </div>
          </form>
          <div className="chat-footnote">
            {project.demo
              ? "Деморежим · без вызовов модели"
              : "Сообщения сохраняются · исполнение PM не подключено"}
          </div>
        </div>
      </aside>

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
            <h2>{team.name}</h2>
            <Badge tone={team.state === "running" ? "green" : "neutral"}>
              {team.state === "running" ? "В работе" : "Ожидание"}
            </Badge>
          </div>
          <p className="muted">{team.focus}</p>
          <div className="team-current">
            <span className="eyebrow">СЕЙЧАС</span>
            <p>{team.current}</p>
          </div>
          <h3 className="member-heading">
            Участники <span>{team.members.length}</span>
          </h3>
          <div className="member-list">
            {team.members.map((m) => (
              <div key={m.name} className="member-row">
                <span className={`avatar ${m.color}`}>{m.name[0]}</span>
                <div>
                  <strong>
                    {m.name}
                    <span>{m.role}</span>
                  </strong>
                  <p>{m.state}</p>
                </div>
                <span className={`tiny-dot ${team.state}`} />
              </div>
            ))}
          </div>
          <div className="team-modal-footer">
            <ShieldCheck size={15} />В команде есть независимый evaluator
          </div>
        </Modal>
      )}
      {experiment && (
        <Modal close={() => setExperiment(null)} label={experiment.name}>
          <span className="eyebrow">{experiment.id} · ДЕМО</span>
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
