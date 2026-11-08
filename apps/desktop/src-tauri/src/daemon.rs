//! Orion "brain" (Python daemon) runtime manager.
//!
//! The desktop app owns the daemon so Orion is one program, not a launcher + console windows:
//!   1. locate a Python 3.10+ interpreter (ORION_PYTHON, the `py` launcher, then python/python3),
//!   2. create a private virtualenv in the app's local-data folder and keep it in sync with
//!      requirements.txt (re-installs only when the file's hash changes, e.g. after an update),
//!   3. start `main.py` hidden (no console), log to <local data>/logs/daemon.log,
//!   4. relaunch on exit code 75 (self-evolve hot reload), roll back the last patch if the reloaded
//!      daemon crashes straight away, and restart with back-off on unexpected crashes,
//!   5. shut it down gracefully when Orion quits or installs an update.
//!
//! Progress is broadcast to the UI as `orion://daemon` events (and kept for `daemon_status`).

use serde::Serialize;
use sha2::{Digest, Sha256};
use std::fs::{self, File, OpenOptions};
use std::io::{BufRead, BufReader, Read, Write};
use std::net::{SocketAddr, TcpStream};
use std::path::{Path, PathBuf};
use std::process::{Child, Command, Stdio};
use std::sync::{Arc, Mutex};
use std::thread;
use std::time::{Duration, Instant};
use tauri::{AppHandle, Emitter, Manager};

pub const DAEMON_PORT: u16 = 9120;
const RESTART_EXIT_CODE: i32 = 75;
const CRASH_WINDOW: Duration = Duration::from_secs(90);
const MAX_CRASH_RESTARTS: u32 = 3;
const LOG_ROTATE_BYTES: u64 = 5 * 1024 * 1024;

#[derive(Clone, Serialize, Debug)]
pub struct DaemonStatus {
    /// locating_python | python_missing | creating_env | installing | starting | running |
    /// external | restarting | stopped | error
    pub stage: String,
    pub message: String,
    pub detail: Option<String>,
    pub progress: Option<f32>,
}

impl DaemonStatus {
    fn new(stage: &str, message: impl Into<String>) -> Self {
        Self { stage: stage.into(), message: message.into(), detail: None, progress: None }
    }
}

#[derive(Default)]
struct Inner {
    child: Option<Child>,
    status: Option<DaemonStatus>,
    stopping: bool,
    generation: u64,
}

#[derive(Clone)]
pub struct DaemonManager {
    app: AppHandle,
    inner: Arc<Mutex<Inner>>,
}

#[derive(Clone)]
struct Paths {
    daemon_dir: PathBuf,
    runtime_dir: PathBuf,
    venv_dir: PathBuf,
    log_file: PathBuf,
    dev_venv: Option<PathBuf>,
}

impl DaemonManager {
    pub fn new(app: AppHandle) -> Self {
        Self { app, inner: Arc::new(Mutex::new(Inner::default())) }
    }

    pub fn status(&self) -> DaemonStatus {
        self.inner
            .lock()
            .unwrap()
            .status
            .clone()
            .unwrap_or_else(|| DaemonStatus::new("starting", "Waking Orion's brain…"))
    }

    fn set_status(&self, status: DaemonStatus) {
        log::info!("[daemon] {}: {}", status.stage, status.message);
        self.inner.lock().unwrap().status = Some(status.clone());
        let _ = self.app.emit("orion://daemon", status);
    }

    pub fn log_dir(&self) -> PathBuf {
        self.paths().map(|p| p.log_file.parent().unwrap().to_path_buf()).unwrap_or_default()
    }

    fn paths(&self) -> Result<Paths, String> {
        let local = self.app.path().app_local_data_dir().map_err(|e| e.to_string())?;
        let runtime_dir = local.join("runtime");
        let (daemon_dir, dev_venv) = if cfg!(debug_assertions) {
            // `tauri dev`: run straight from the repo and reuse its existing .venv.
            let dir = PathBuf::from(env!("CARGO_MANIFEST_DIR")).join("../../daemon");
            let dir = dir.canonicalize().unwrap_or(dir);
            let venv = dir.join(".venv");
            (dir, if venv.exists() { Some(venv) } else { None })
        } else {
            let res = self.app.path().resource_dir().map_err(|e| e.to_string())?;
            (res.join("daemon"), None)
        };
        Ok(Paths {
            daemon_dir,
            venv_dir: runtime_dir.join("venv"),
            log_file: local.join("logs").join("daemon.log"),
            runtime_dir,
            dev_venv,
        })
    }

    /// Kick off bootstrap + supervision on a background thread.
    pub fn start(&self) {
        let me = self.clone();
        let generation = {
            let mut inner = self.inner.lock().unwrap();
            inner.stopping = false;
            inner.generation += 1;
            inner.generation
        };
        thread::spawn(move || {
            if let Err(err) = me.bootstrap_and_supervise(generation) {
                let mut st = DaemonStatus::new("error", "Orion's brain could not start");
                st.detail = Some(err);
                me.set_status(st);
            }
        });
    }

    pub fn restart(&self) {
        self.stop(true);
        self.start();
    }

    /// Graceful shutdown: ask the daemon to exit, then kill it if it doesn't within a few seconds.
    pub fn stop(&self, quiet: bool) {
        let child = {
            let mut inner = self.inner.lock().unwrap();
            inner.stopping = true;
            inner.generation += 1;
            inner.child.take()
        };
        if let Some(mut child) = child {
            let _ = http_post_local("/shutdown");
            let deadline = Instant::now() + Duration::from_secs(5);
            while Instant::now() < deadline {
                if let Ok(Some(_)) = child.try_wait() {
                    break;
                }
                thread::sleep(Duration::from_millis(150));
            }
            let _ = child.kill();
            let _ = child.wait();
        }
        if !quiet {
            self.set_status(DaemonStatus::new("stopped", "Orion's brain is stopped"));
        }
    }

    fn current(&self, generation: u64) -> bool {
        let inner = self.inner.lock().unwrap();
        !inner.stopping && inner.generation == generation
    }

    fn bootstrap_and_supervise(&self, generation: u64) -> Result<(), String> {
        let paths = self.paths()?;
        fs::create_dir_all(&paths.runtime_dir).map_err(|e| e.to_string())?;
        fs::create_dir_all(paths.log_file.parent().unwrap()).map_err(|e| e.to_string())?;

        // OrionOS: the daemon is a systemd user service the HUD must never replace or spawn.
        if std::env::var_os("ORION_EXTERNAL_DAEMON").is_some() {
            loop {
                if !self.current(generation) {
                    return Ok(());
                }
                if port_open() {
                    let v = http_get_local("/health").and_then(|body| extract_version(&body)).unwrap_or_default();
                    self.set_status(DaemonStatus::new("external", format!("Connected to OrionOS brain v{v}")));
                    return Ok(());
                }
                self.set_status(DaemonStatus::new("starting", "Waiting for the OrionOS brain service…"));
                thread::sleep(Duration::from_secs(2));
            }
        }

        // A daemon is already listening (launch.bat / npm run dev:daemon, or one left over from
        // before an update). Reuse it only if it's the same version as this app.
        if port_open() {
            match http_get_local("/health").and_then(|body| extract_version(&body)) {
                Some(v) if v == self.app.package_info().version.to_string() || cfg!(debug_assertions) => {
                    self.set_status(DaemonStatus::new("external", format!("Connected to running Orion brain v{v}")));
                    return Ok(());
                }
                other => {
                    self.set_status(DaemonStatus::new(
                        "restarting",
                        format!("Replacing an older Orion brain ({})…", other.unwrap_or_else(|| "unknown".into())),
                    ));
                    let _ = http_post_local("/shutdown");
                    for _ in 0..40 {
                        if !port_open() {
                            break;
                        }
                        thread::sleep(Duration::from_millis(250));
                    }
                    if port_open() {
                        return Err(format!(
                            "Port {DAEMON_PORT} is in use by another program. Close it (or an old Orion console window) and restart Orion."
                        ));
                    }
                }
            }
        }

        let python = self.ensure_environment(&paths)?;
        if !self.current(generation) {
            return Ok(());
        }
        self.supervise(&paths, &python, generation)
    }

    /// Returns the interpreter to run the daemon with (venv python).
    fn ensure_environment(&self, paths: &Paths) -> Result<PathBuf, String> {
        if let Some(dev) = &paths.dev_venv {
            return Ok(venv_python(dev));
        }
        let req = paths.daemon_dir.join("requirements.txt");
        let req_hash = file_sha256(&req).ok_or_else(|| format!("missing {}", req.display()))?;
        let stamp = paths.runtime_dir.join("requirements.sha256");
        let venv_py = venv_python(&paths.venv_dir);

        if !venv_py.exists() {
            self.set_status(DaemonStatus::new("locating_python", "Looking for Python…"));
            let base = find_python().ok_or_else(|| {
                let mut st = DaemonStatus::new(
                    "python_missing",
                    "Orion needs Python 3.10 or newer (one-time setup).",
                );
                st.detail = Some("Click “Install Python” or install it from python.org, then press Retry.".into());
                self.set_status(st);
                "python_missing".to_string()
            })?;
            let mut st = DaemonStatus::new("creating_env", "Creating Orion's private Python environment…");
            st.detail = Some(format!("{} -m venv", base.join(" ")));
            self.set_status(st);
            let mut cmd = command(&base[0]);
            cmd.args(&base[1..]).arg("-m").arg("venv").arg(&paths.venv_dir);
            run_logged(cmd, &paths.log_file, |_| {})?;
            let _ = fs::remove_file(&stamp);
        }

        let installed = fs::read_to_string(&stamp).unwrap_or_default();
        if installed.trim() != req_hash {
            self.set_status(DaemonStatus::new(
                "installing",
                "Installing Orion's brain (first run or update — this can take a few minutes)…",
            ));
            let mut pip = command(&venv_py);
            pip.args(["-m", "pip", "install", "--upgrade", "pip", "--disable-pip-version-check"]);
            let _ = run_logged(pip, &paths.log_file, |_| {});

            let total = count_requirements(&req).max(1) as f32;
            let seen = Arc::new(Mutex::new(0usize));
            let me = self.clone();
            let mut cmd = command(&venv_py);
            cmd.args(["-m", "pip", "install", "--disable-pip-version-check", "-r"]).arg(&req);
            run_logged(cmd, &paths.log_file, move |line| {
                if line.starts_with("Collecting ") || line.starts_with("Requirement already satisfied") {
                    let mut n = seen.lock().unwrap();
                    *n += 1;
                    let mut st = DaemonStatus::new(
                        "installing",
                        "Installing Orion's brain (first run or update — this can take a few minutes)…",
                    );
                    st.detail = Some(line.chars().take(120).collect());
                    st.progress = Some((*n as f32 / (total * 1.6)).min(0.97));
                    me.set_status(st);
                }
            })
            .map_err(|e| format!("Installing Python packages failed: {e}. See logs/daemon.log."))?;
            fs::write(&stamp, &req_hash).map_err(|e| e.to_string())?;
        }
        Ok(venv_py)
    }

    fn spawn(&self, paths: &Paths, python: &Path) -> Result<Child, String> {
        rotate_log(&paths.log_file);
        let log = OpenOptions::new().create(true).append(true).open(&paths.log_file).map_err(|e| e.to_string())?;
        let log_err = log.try_clone().map_err(|e| e.to_string())?;
        let mut cmd = command(python);
        cmd.arg("main.py")
            .current_dir(&paths.daemon_dir)
            .env("PYTHONUNBUFFERED", "1")
            .env("PYTHONIOENCODING", "utf-8")
            .env("ORION_APP_VERSION", self.app.package_info().version.to_string())
            .stdout(Stdio::from(log))
            .stderr(Stdio::from(log_err));
        if !cfg!(debug_assertions) {
            cmd.env("ORION_PACKAGED", "1");
        }
        cmd.spawn().map_err(|e| format!("could not start the daemon: {e}"))
    }

    fn supervise(&self, paths: &Paths, python: &Path, generation: u64) -> Result<(), String> {
        let mut hot_reloaded_at: Option<Instant> = None;
        let mut rolled_back = false;
        let mut crashes: Vec<Instant> = Vec::new();

        loop {
            if !self.current(generation) {
                return Ok(());
            }
            self.set_status(DaemonStatus::new("starting", "Starting Orion's brain…"));
            let child = self.spawn(paths, python)?;
            let started = Instant::now();
            self.inner.lock().unwrap().child = Some(child);

            // Wait until it's listening (or it dies).
            let mut announced = false;
            let code = loop {
                thread::sleep(Duration::from_millis(400));
                let mut inner = self.inner.lock().unwrap();
                if inner.generation != generation {
                    return Ok(());
                }
                let Some(child) = inner.child.as_mut() else { return Ok(()) };
                match child.try_wait() {
                    Ok(Some(status)) => {
                        inner.child = None;
                        break status.code().unwrap_or(-1);
                    }
                    Ok(None) => {
                        drop(inner);
                        if !announced && port_open() {
                            announced = true;
                            self.set_status(DaemonStatus::new("running", "Orion's brain is online"));
                        }
                    }
                    Err(e) => return Err(e.to_string()),
                }
            };

            if !self.current(generation) {
                return Ok(());
            }
            log::warn!("[daemon] exited with code {code}");

            if code == RESTART_EXIT_CODE {
                hot_reloaded_at = Some(Instant::now());
                self.set_status(DaemonStatus::new("restarting", "Applying Orion's self-update…"));
                continue;
            }

            let after_reload = hot_reloaded_at
                .map(|t| started.duration_since(t) < Duration::from_secs(5) && started.elapsed() < CRASH_WINDOW)
                .unwrap_or(false);
            if after_reload && !rolled_back {
                rolled_back = true;
                self.set_status(DaemonStatus::new("restarting", "Self-update broke startup — rolling it back…"));
                let mut revert = command(python);
                revert.arg("self_evolve.py").arg("--revert-last").current_dir(&paths.daemon_dir);
                let _ = run_logged(revert, &paths.log_file, |_| {});
                continue;
            }

            crashes.retain(|t| t.elapsed() < Duration::from_secs(300));
            crashes.push(Instant::now());
            if crashes.len() as u32 > MAX_CRASH_RESTARTS {
                let mut st = DaemonStatus::new("error", "Orion's brain keeps crashing");
                st.detail = Some(format!("Exit code {code}. Open the logs for details, fix, then Retry."));
                self.set_status(st);
                return Ok(());
            }
            let mut st = DaemonStatus::new("restarting", "Orion's brain stopped unexpectedly — restarting…");
            st.detail = Some(format!("Exit code {code}"));
            self.set_status(st);
            thread::sleep(Duration::from_secs(2 * crashes.len() as u64));
        }
    }
}

// ------------------------------------------------------------------ helpers

fn command(program: impl AsRef<std::ffi::OsStr>) -> Command {
    #[allow(unused_mut)]
    let mut cmd = Command::new(program);
    #[cfg(windows)]
    {
        use std::os::windows::process::CommandExt;
        const CREATE_NO_WINDOW: u32 = 0x0800_0000;
        cmd.creation_flags(CREATE_NO_WINDOW);
    }
    cmd
}

fn venv_python(venv: &Path) -> PathBuf {
    if cfg!(windows) {
        venv.join("Scripts").join("python.exe")
    } else {
        venv.join("bin").join("python")
    }
}

/// Candidate interpreters, best first. Each entry is program + args.
fn find_python() -> Option<Vec<String>> {
    let mut candidates: Vec<Vec<String>> = Vec::new();
    if let Ok(p) = std::env::var("ORION_PYTHON") {
        candidates.push(vec![p]);
    }
    if cfg!(windows) {
        for v in ["-3.12", "-3.11", "-3.13", "-3.10", "-3"] {
            candidates.push(vec!["py".into(), v.into()]);
        }
        if let Ok(local) = std::env::var("LOCALAPPDATA") {
            for v in ["312", "311", "313", "310"] {
                candidates.push(vec![format!(r"{local}\Programs\Python\Python{v}\python.exe")]);
            }
        }
        candidates.push(vec!["python".into()]);
    } else {
        candidates.push(vec!["python3".into()]);
        candidates.push(vec!["python".into()]);
    }
    candidates.into_iter().find(|c| python_ok(c))
}

fn python_ok(candidate: &[String]) -> bool {
    let mut cmd = command(&candidate[0]);
    cmd.args(&candidate[1..])
        .args(["-c", "import sys, venv; print(sys.version_info >= (3, 10))"])
        .stdout(Stdio::piped())
        .stderr(Stdio::null());
    match cmd.output() {
        Ok(out) => out.status.success() && String::from_utf8_lossy(&out.stdout).trim() == "True",
        Err(_) => false,
    }
}

/// Run a command to completion, appending its output to the log and feeding each line to `on_line`.
fn run_logged(mut cmd: Command, log_file: &Path, mut on_line: impl FnMut(&str)) -> Result<(), String> {
    let mut log = OpenOptions::new().create(true).append(true).open(log_file).map_err(|e| e.to_string())?;
    let _ = writeln!(log, "\n$ {:?}", cmd);
    let mut child = cmd
        .stdout(Stdio::piped())
        .stderr(Stdio::piped())
        .spawn()
        .map_err(|e| format!("{e}"))?;
    let stderr = child.stderr.take();
    let err_log = log.try_clone().map_err(|e| e.to_string())?;
    let err_thread = thread::spawn(move || {
        let mut tail = String::new();
        if let Some(stderr) = stderr {
            let mut err_log = err_log;
            for line in BufReader::new(stderr).lines().map_while(Result::ok) {
                let _ = writeln!(err_log, "{line}");
                tail.push_str(&line);
                tail.push('\n');
                if tail.len() > 4000 {
                    let mut cut = tail.len() - 2000;
                    while !tail.is_char_boundary(cut) {
                        cut += 1;
                    }
                    tail = tail[cut..].to_string();
                }
            }
        }
        tail
    });
    if let Some(stdout) = child.stdout.take() {
        for line in BufReader::new(stdout).lines().map_while(Result::ok) {
            let _ = writeln!(log, "{line}");
            on_line(&line);
        }
    }
    let status = child.wait().map_err(|e| e.to_string())?;
    let tail = err_thread.join().unwrap_or_default();
    if status.success() {
        Ok(())
    } else {
        let last: Vec<&str> = tail.lines().rev().take(3).collect();
        Err(format!(
            "exit code {:?}: {}",
            status.code(),
            last.into_iter().rev().collect::<Vec<_>>().join(" | ")
        ))
    }
}

fn file_sha256(path: &Path) -> Option<String> {
    let mut f = File::open(path).ok()?;
    let mut buf = Vec::new();
    f.read_to_end(&mut buf).ok()?;
    let digest = Sha256::digest(&buf);
    Some(digest.iter().map(|b| format!("{b:02x}")).collect())
}

fn count_requirements(path: &Path) -> usize {
    fs::read_to_string(path)
        .map(|s| s.lines().filter(|l| !l.trim().is_empty() && !l.trim_start().starts_with('#')).count())
        .unwrap_or(10)
}

fn rotate_log(path: &Path) {
    if fs::metadata(path).map(|m| m.len() > LOG_ROTATE_BYTES).unwrap_or(false) {
        let _ = fs::rename(path, path.with_extension("log.1"));
    }
}

fn addr() -> SocketAddr {
    SocketAddr::from(([127, 0, 0, 1], DAEMON_PORT))
}

pub fn port_open() -> bool {
    TcpStream::connect_timeout(&addr(), Duration::from_millis(300)).is_ok()
}

/// Where the daemon's per-start API token may be, in the same order the daemon and every other client use
/// (apps/daemon/api_token.py): $ORION_TOKEN_FILE, then $XDG_RUNTIME_DIR/orion/daemon.token (not on Windows),
/// then <ORION_DATA_DIR or ~/.orion>/daemon.token.
fn token_candidates(env: &dyn Fn(&str) -> Option<String>, windows: bool) -> Vec<PathBuf> {
    let mut out = Vec::new();
    if let Some(f) = env("ORION_TOKEN_FILE").filter(|v| !v.trim().is_empty()) {
        out.push(PathBuf::from(f.trim()));
    }
    if !windows {
        if let Some(rt) = env("XDG_RUNTIME_DIR").filter(|v| !v.trim().is_empty()) {
            out.push(PathBuf::from(rt.trim()).join("orion").join("daemon.token"));
        }
    }
    let home = env(if windows { "USERPROFILE" } else { "HOME" }).unwrap_or_default();
    let data = match env("ORION_DATA_DIR").filter(|v| !v.trim().is_empty()) {
        Some(d) if d.starts_with('~') => PathBuf::from(&home).join(d.trim_start_matches('~').trim_start_matches(['/', '\\'])),
        Some(d) => PathBuf::from(d),
        None => PathBuf::from(&home).join(".orion"),
    };
    out.push(data.join("daemon.token"));
    out
}

/// This run's API token, or None while the daemon hasn't written one (review B1).
pub fn read_token() -> Option<String> {
    let env = |k: &str| std::env::var(k).ok();
    token_candidates(&env, cfg!(windows))
        .into_iter()
        .find_map(|p| fs::read_to_string(p).ok().map(|t| t.trim().to_string()).filter(|t| !t.is_empty()))
}

/// Minimal HTTP/1.1 client for the local daemon (no Origin header => treated as a local process).
fn http_local(method: &str, path: &str) -> Option<String> {
    let mut stream = TcpStream::connect_timeout(&addr(), Duration::from_millis(800)).ok()?;
    stream.set_read_timeout(Some(Duration::from_secs(3))).ok()?;
    let auth = read_token().map(|t| format!("Authorization: Bearer {t}\r\n")).unwrap_or_default();
    let req = format!(
        "{method} {path} HTTP/1.1\r\nHost: 127.0.0.1:{DAEMON_PORT}\r\n{auth}Content-Length: 0\r\nConnection: close\r\n\r\n"
    );
    stream.write_all(req.as_bytes()).ok()?;
    let mut body = String::new();
    let _ = stream.read_to_string(&mut body);
    Some(body)
}

fn http_get_local(path: &str) -> Option<String> {
    http_local("GET", path)
}

fn http_post_local(path: &str) -> Option<String> {
    http_local("POST", path)
}

fn extract_version(http_response: &str) -> Option<String> {
    let body = http_response.split("\r\n\r\n").nth(1)?;
    let json: serde_json::Value = serde_json::from_str(body.trim()).ok()?;
    json.get("version")?.as_str().map(str::to_string)
}

#[cfg(test)]
mod tests {
    use super::*;

    #[test]
    fn token_lookup_order() {
        let vars = |k: &str| match k {
            "XDG_RUNTIME_DIR" => Some("/run/user/1000".to_string()),
            "HOME" => Some("/home/orion".to_string()),
            "USERPROFILE" => Some("C:\\Users\\me".to_string()),
            _ => None,
        };
        let linux = token_candidates(&vars, false);
        assert_eq!(linux[0], PathBuf::from("/run/user/1000/orion/daemon.token"));
        assert_eq!(linux[1], PathBuf::from("/home/orion/.orion/daemon.token"));
        let win = token_candidates(&vars, true);
        assert_eq!(win.len(), 1);
        assert!(win[0].ends_with("daemon.token"));
    }

    #[test]
    fn parses_health_version() {
        let resp = "HTTP/1.1 200 OK\r\ncontent-type: application/json\r\n\r\n{\"status\":\"ok\",\"version\":\"2.0.0\"}";
        assert_eq!(extract_version(resp).as_deref(), Some("2.0.0"));
        assert_eq!(extract_version("garbage"), None);
    }
}
