//! The Linux implementation of the platform contract (Ubuntu 24.04 LTS is the supported baseline).
//!
//! POSIX has nothing that behaves like a kill-on-close Job Object, so the same guarantee is built
//! out of two mechanisms that do exist:
//!
//! * the child is spawned into **its own process group** (`setsid`-style), which is what makes it
//!   possible to stop the whole tree — the sidecar, the Tor daemon it starts, and anything they
//!   start — instead of only the process we happen to hold;
//! * a **parent-death signal** is armed in the child before it execs, so a desktop that dies
//!   unexpectedly does not leave its backend behind. That is the part `kill` alone cannot do.
//!
//! Secrets live in the Secret Service (GNOME Keyring or any compatible provider) and nowhere else.
//! When the store cannot be reached the answer is a typed refusal — never a file, never plaintext.

use std::collections::HashMap;
use std::ffi::OsString;
use std::fs;
use std::io::{self, Write};
use std::path::{Path, PathBuf};
use std::process::{Child, Command, Stdio};
use std::sync::atomic::{AtomicU32, Ordering};

use super::SECURE_STORAGE_UNAVAILABLE;

const SERVICE: &str = "canalla-llm";
const AUTOSTART_FILE: &str = "canalla-llm.desktop";

// ------------------------------------------------------------------------------------- ownership

/// POSIX ownership is the process group, not an object: the group id is the child's pid.
pub struct OwnedJob(AtomicU32);

pub fn own_child(child: &Child) -> Option<OwnedJob> {
    // `configure_spawn` put the child in its own group, so the pid *is* the group id.
    let pid = child.id();
    if pid == 0 {
        return None;
    }
    Some(OwnedJob(AtomicU32::new(pid)))
}

impl OwnedJob {
    #[allow(dead_code)]
    pub fn group(&self) -> u32 {
        self.0.load(Ordering::Relaxed)
    }
}

/// Is `pid` still a running process? A zombie is not: it has already exited and only waits to be
/// reaped, and reporting it as alive would keep a dead backend looking healthy.
pub fn process_alive(pid: u32) -> bool {
    let Ok(stat) = fs::read_to_string(format!("/proc/{pid}/stat")) else {
        return false;
    };
    // `pid (name) state ...` — the name may contain spaces and brackets, so the state is read after
    // the closing parenthesis rather than by splitting the whole line.
    let Some(rest) = stat.rsplit_once(')').map(|(_, rest)| rest) else {
        return false;
    };
    !matches!(
        rest.trim_start().chars().next(),
        None | Some('Z') | Some('X')
    )
}

pub fn terminate_pid(pid: u32) {
    if pid != 0 {
        unsafe {
            libc::kill(pid as libc::pid_t, libc::SIGTERM);
        }
    }
}

/// Stop the tree the desktop started: the process group, with the same escalation the Windows
/// `taskkill /T /F` does.
pub fn terminate_tree(pid: u32) {
    if pid == 0 {
        return;
    }
    let group = -(pid as libc::pid_t);
    unsafe {
        libc::kill(group, libc::SIGTERM);
    }
    for _ in 0..20 {
        if !process_alive(pid) {
            break;
        }
        std::thread::sleep(std::time::Duration::from_millis(150));
    }
    unsafe {
        libc::kill(group, libc::SIGKILL);
    }
}

// ------------------------------------------------------------------------------------ spawning

/// Put the child in its own process group and arm the parent-death signal.
///
/// The signal is delivered when the *thread* that forked exits, which is the documented POSIX
/// behaviour of `PR_SET_PDEATHSIG`; the desktop spawns the backend from its main thread, so this is
/// the same lifetime. The second check closes the race where the parent died between `fork` and
/// `prctl`, in which case the child must not linger either.
pub fn configure_spawn(command: &mut Command) {
    use std::os::unix::process::CommandExt;
    command.process_group(0);
    unsafe {
        command.pre_exec(|| {
            if libc::prctl(libc::PR_SET_PDEATHSIG, libc::SIGTERM) == -1 {
                return Err(io::Error::last_os_error());
            }
            if libc::getppid() == 1 {
                return Err(io::Error::other("parent_exited_before_setup"));
            }
            Ok(())
        });
    }
}

/// The same thing: the retry exists for the Windows job-breakaway case, which has no POSIX partner.
pub fn configure_spawn_plain(command: &mut Command) {
    configure_spawn(command);
}

/// The environment a tool the user asked for may see. The application's own secrets are never here,
/// and neither is anything the shell would have added for the user.
pub fn sanitized_env() -> HashMap<String, String> {
    let mut env = HashMap::new();
    for key in [
        "HOME",
        "USER",
        "LOGNAME",
        "LANG",
        "LC_ALL",
        "LC_CTYPE",
        "TZ",
        "TMPDIR",
        "SHELL",
        "XDG_RUNTIME_DIR",
        "XDG_DATA_HOME",
        "XDG_CONFIG_HOME",
        "XDG_CACHE_HOME",
        "DISPLAY",
        "WAYLAND_DISPLAY",
    ] {
        if let Ok(value) = std::env::var(key) {
            env.insert(key.to_string(), value);
        }
    }
    env.insert(
        "PATH".into(),
        "/usr/local/bin:/usr/bin:/bin:/usr/local/sbin:/usr/sbin:/sbin".into(),
    );
    // A tool that needs a remote must not stop and ask for credentials in a pipe that has no user.
    env.insert("GIT_TERMINAL_PROMPT".into(), "0".into());
    env
}

// ---------------------------------------------------------------------------------------- paths

/// `$XDG_DATA_HOME/alex-llm` (or `~/.local/share/alex-llm`), which is the same directory the Python
/// half calls its data root. If the two disagreed, the desktop and its backend would read different
/// databases.
pub fn data_root_default() -> PathBuf {
    let base = match std::env::var("XDG_DATA_HOME") {
        Ok(value) if !value.is_empty() => PathBuf::from(value),
        _ => home().join(".local").join("share"),
    };
    base.join("alex-llm")
}

fn home() -> PathBuf {
    std::env::var("HOME")
        .map(PathBuf::from)
        .unwrap_or_else(|_| PathBuf::from("/"))
}

fn config_home() -> PathBuf {
    match std::env::var("XDG_CONFIG_HOME") {
        Ok(value) if !value.is_empty() => PathBuf::from(value),
        _ => home().join(".config"),
    }
}

/// The user's folders. `user-dirs.dirs` is where the desktop environment records them (a moved or
/// renamed Desktop is honoured); the conventional names are the fallback, and only when they exist.
pub fn known_folders() -> (Option<String>, Option<String>, Option<String>) {
    let configured = fs::read_to_string(config_home().join("user-dirs.dirs")).unwrap_or_default();
    let read = |key: &str| -> Option<String> {
        for line in configured.lines() {
            let line = line.trim();
            if !line.starts_with(key) {
                continue;
            }
            let value = line.split_once('=')?.1.trim().trim_matches('"');
            let path = value.replace("$HOME", &home().to_string_lossy());
            if Path::new(&path).is_dir() {
                return Some(path);
            }
        }
        None
    };
    let conventional = |name: &str| -> Option<String> {
        let path = home().join(name);
        path.is_dir().then(|| path.to_string_lossy().into_owned())
    };
    (
        read("XDG_DESKTOP_DIR").or_else(|| conventional("Desktop")),
        read("XDG_DOCUMENTS_DIR").or_else(|| conventional("Documents")),
        read("XDG_DOWNLOAD_DIR").or_else(|| conventional("Downloads")),
    )
}

pub struct SystemFacts {
    pub platform: &'static str,
    pub os_version: String,
    pub cpu_logical_processors: u32,
    pub ram_total_mb: u64,
    pub ram_avail_mb: u64,
    pub disk_free_gb: u64,
}

pub fn system_info() -> SystemFacts {
    let os_version = fs::read_to_string("/etc/os-release")
        .ok()
        .and_then(|text| {
            text.lines()
                .find_map(|line| line.strip_prefix("PRETTY_NAME=").map(str::to_string))
        })
        .map(|value| value.trim().trim_matches('"').to_string())
        .or_else(|| {
            fs::read_to_string("/proc/sys/kernel/osrelease")
                .ok()
                .map(|value| value.trim().to_string())
        })
        .unwrap_or_else(|| "linux".into());
    let meminfo = fs::read_to_string("/proc/meminfo").unwrap_or_default();
    let field = |key: &str| -> u64 {
        meminfo
            .lines()
            .find_map(|line| {
                let (name, rest) = line.split_once(':')?;
                (name == key).then(|| rest.trim().split_whitespace().next()?.parse::<u64>().ok())?
            })
            .unwrap_or(0)
            / 1024
    };
    SystemFacts {
        platform: "linux",
        os_version,
        cpu_logical_processors: std::thread::available_parallelism()
            .map(|value| value.get() as u32)
            .unwrap_or(0),
        ram_total_mb: field("MemTotal"),
        ram_avail_mb: field("MemAvailable"),
        disk_free_gb: disk_free_gb("/"),
    }
}

fn disk_free_gb(path: &str) -> u64 {
    use std::ffi::CString;
    let Ok(target) = CString::new(path) else {
        return 0;
    };
    unsafe {
        let mut stats: libc::statvfs = std::mem::zeroed();
        if libc::statvfs(target.as_ptr(), &mut stats) != 0 {
            return 0;
        }
        let available = stats.f_bavail as u128 * stats.f_frsize as u128;
        (available / (1024 * 1024 * 1024)) as u64
    }
}

/// `rename(2)` is atomic and replaces the destination, which is exactly what `ReplaceFileW` buys on
/// Windows.
pub fn replace_file(tmp: &Path, dest: &Path) -> io::Result<()> {
    fs::rename(tmp, dest)
}

// -------------------------------------------------------------------------------------- secrets

fn secret_tool() -> OsString {
    // LibreSecret's own client. It speaks the Secret Service protocol over the session bus, so the
    // secret never touches our process arguments or a file of ours.
    std::env::var_os("ALEX_SECRET_TOOL").unwrap_or_else(|| OsString::from("secret-tool"))
}

fn run_secret_tool(args: &[&str], input: Option<&str>) -> Result<String, String> {
    let mut command = Command::new(secret_tool());
    command
        .args(args)
        .stdin(if input.is_some() {
            Stdio::piped()
        } else {
            Stdio::null()
        })
        .stdout(Stdio::piped())
        .stderr(Stdio::piped());
    let mut child = command
        .spawn()
        .map_err(|_| SECURE_STORAGE_UNAVAILABLE.to_string())?;
    if let Some(value) = input {
        let mut stdin = child.stdin.take().ok_or(SECURE_STORAGE_UNAVAILABLE)?;
        stdin
            .write_all(value.as_bytes())
            .map_err(|_| SECURE_STORAGE_UNAVAILABLE.to_string())?;
        drop(stdin);
    }
    let output = child
        .wait_with_output()
        .map_err(|_| SECURE_STORAGE_UNAVAILABLE.to_string())?;
    if output.status.success() {
        return Ok(String::from_utf8_lossy(&output.stdout).to_string());
    }
    let stderr = String::from_utf8_lossy(&output.stderr).to_lowercase();
    // "no such interface", a missing bus, a locked collection: all of them mean the store is not
    // usable right now, and none of them is a reason to write the secret somewhere else.
    if stderr.contains("cannot autolaunch")
        || stderr.contains("no such interface")
        || stderr.contains("the name org.freedesktop.secrets")
        || stderr.contains("locked collection")
        || stderr.contains("not found")
        || stderr.contains("connection")
    {
        return Err(SECURE_STORAGE_UNAVAILABLE.to_string());
    }
    Err(if args[0] == "lookup" {
        "missing".to_string()
    } else {
        // The reason travels with the failure (never the value): a store that refuses has to say
        // why, or the next person reads the source to find out.
        let detail: String = String::from_utf8_lossy(&output.stderr)
            .trim()
            .chars()
            .take(200)
            .collect();
        format!(
            "secret_service_failed:{}:{detail}",
            output.status.code().unwrap_or(-1)
        )
    })
}

/// The Secret Service, and nothing else: POSIX gets no file fallback, because a plaintext-shaped
/// secret next to the database is worse than an honest refusal.
pub fn secret_read(target: &str, _fallback: &Path) -> Result<String, String> {
    run_secret_tool(&["lookup", "service", SERVICE, "target", target], None)
        .map(|value| value.trim_end_matches('\n').to_string())
}

pub fn secret_write(target: &str, value: &str, _fallback: &Path) -> Result<(), String> {
    // The label is what a user sees in Seahorse/keyring; the lookup attributes are ours.
    let label = format!("Canalla LLM: {target}");
    run_secret_tool(
        &[
            "store", "--label", &label, "service", SERVICE, "target", target,
        ],
        Some(value),
    )
    .map(|_| ())
}

pub fn secret_delete(target: &str, _fallback: &Path) -> Result<(), String> {
    match run_secret_tool(&["clear", "service", SERVICE, "target", target], None) {
        // Nothing stored is not a failure: an uninstall must not fail on a clean machine.
        Err(error) if error == "missing" => Ok(()),
        other => other.map(|_| ()),
    }
}

/// There is no file fallback on this platform, so a reader never gets a value out of one.
pub fn secret_fallback_in_use(_fallback: &Path) -> bool {
    false
}

/// The store this installation uses, for the diagnostics the UI shows.
pub fn secret_kind() -> &'static str {
    if secret_read("probe", Path::new("/nonexistent")).is_err() && !secret_tool_present() {
        "unavailable"
    } else {
        "secret_service"
    }
}

fn secret_tool_present() -> bool {
    Command::new(secret_tool())
        .arg("--version")
        .stdin(Stdio::null())
        .stdout(Stdio::null())
        .stderr(Stdio::null())
        .status()
        .is_ok()
}

// ------------------------------------------------------------------------------- login entry

/// The command an XDG autostart entry runs. Desktop-entry syntax quotes the executable with double
/// quotes and escapes the three characters that are special inside them.
pub fn login_command(executable: &Path) -> String {
    let path = executable.to_string_lossy();
    let escaped = path
        .replace('\\', "\\\\")
        .replace('"', "\\\"")
        .replace('$', "\\$");
    format!("\"{escaped}\"")
}

fn autostart_path() -> PathBuf {
    config_home().join("autostart").join(AUTOSTART_FILE)
}

fn entry_body(command: &str) -> String {
    // The keys every desktop reads, plus the one GNOME-specific switch that lets a user keep the
    // file and disable it in the session settings without the product fighting them.
    format!(
        "[Desktop Entry]\n\
         Type=Application\n\
         Name=Canalla LLM\n\
         Comment=Canalla LLM chat, computer use and Tor\n\
         Exec={command}\n\
         Terminal=false\n\
         X-GNOME-Autostart-enabled=true\n"
    )
}

pub fn login_read() -> Result<Option<String>, String> {
    let path = autostart_path();
    let Ok(text) = fs::read_to_string(&path) else {
        return Ok(None);
    };
    let enabled = !text.contains("X-GNOME-Autostart-enabled=false");
    // The value is returned exactly as the entry holds it (quoted, as desktop entries require), so
    // a reader gets the same shape the writer reported - and the same shape Windows reports.
    let command = text.lines().find_map(|line| {
        line.strip_prefix("Exec=")
            .map(|value| value.trim().to_string())
    });
    match command {
        Some(command) if enabled => Ok(Some(command)),
        _ => Ok(None),
    }
}

pub fn login_write(enabled: bool, executable: &Path) -> Result<Option<String>, String> {
    let path = autostart_path();
    if !enabled {
        let _ = fs::remove_file(&path);
        return match login_read()? {
            None => Ok(None),
            Some(_) => Err("autostart_not_removed".to_string()),
        };
    }
    let command = login_command(executable);
    let dir = path.parent().ok_or("autostart_no_config_dir")?;
    fs::create_dir_all(dir).map_err(|error| format!("autostart_write_failed:{error}"))?;
    fs::write(&path, entry_body(&command))
        .map_err(|error| format!("autostart_write_failed:{error}"))?;
    match login_read()? {
        Some(found) if found == command => Ok(Some(command)),
        _ => Err("autostart_not_applied".to_string()),
    }
}

/// A per-user XDG autostart entry is the supported mechanism: no root, no service, no scheduler.
pub fn login_supported() -> bool {
    true
}

/// The product never asks for a password. On Linux a genuinely privileged action is the user's own
/// business (polkit/sudo in their terminal), so the tool reports that it cannot elevate.
pub fn elevation_available() -> bool {
    false
}

#[cfg(test)]
mod tests {
    use super::*;
    use std::sync::Mutex;

    /// One test at a time: they share the process environment and the autostart file.
    static TEST_LOCK: Mutex<()> = Mutex::new(());

    struct Sandbox {
        _guard: std::sync::MutexGuard<'static, ()>,
        previous: Option<OsString>,
    }

    impl Sandbox {
        fn new(label: &str) -> Self {
            // A test that panics while holding the lock must not take the others down with it.
            let guard = TEST_LOCK.lock().unwrap_or_else(|error| error.into_inner());
            let root = std::env::temp_dir()
                .join(format!("canalla-autostart-{label}-{}", std::process::id()));
            let _ = fs::remove_dir_all(&root);
            let previous = std::env::var_os("XDG_CONFIG_HOME");
            std::env::set_var("XDG_CONFIG_HOME", &root);
            Self {
                _guard: guard,
                previous,
            }
        }
    }

    impl Drop for Sandbox {
        fn drop(&mut self) {
            match self.previous.take() {
                Some(value) => std::env::set_var("XDG_CONFIG_HOME", value),
                None => std::env::remove_var("XDG_CONFIG_HOME"),
            }
        }
    }

    #[test]
    fn the_autostart_entry_round_trips_through_the_xdg_file() {
        let _sandbox = Sandbox::new("roundtrip");
        let exe = Path::new("/usr/lib/canalla-llm/alex-llm");
        login_write(false, exe).unwrap();
        assert_eq!(login_read().unwrap(), None);

        let command = login_write(true, exe).unwrap().unwrap();
        assert_eq!(command, "\"/usr/lib/canalla-llm/alex-llm\"");
        assert_eq!(login_read().unwrap(), Some(command.clone()));
        let text = fs::read_to_string(autostart_path()).unwrap();
        assert!(text.contains("[Desktop Entry]"));
        assert!(text.contains("Type=Application"));
        assert!(text.contains("Name=Canalla LLM"));
        assert!(text.contains("X-GNOME-Autostart-enabled=true"));
        assert!(text.contains("Exec=\"/usr/lib/canalla-llm/alex-llm\""));

        login_write(false, exe).unwrap();
        assert_eq!(login_read().unwrap(), None);
        // A removal on a machine where nothing was ever registered must not fail.
        login_write(false, exe).unwrap();
    }

    #[test]
    fn an_entry_a_user_disabled_is_reported_as_off() {
        let _sandbox = Sandbox::new("disabled");
        let exe = Path::new("/usr/lib/canalla-llm/alex-llm");
        login_write(true, exe).unwrap();
        let path = autostart_path();
        let text = fs::read_to_string(&path).unwrap();
        fs::write(&path, text.replace("=true", "=false")).unwrap();
        assert_eq!(login_read().unwrap(), None);
    }

    #[test]
    fn an_executable_with_spaces_is_quoted() {
        assert_eq!(
            login_command(Path::new("/home/me/Canalla LLM/alex-llm")),
            "\"/home/me/Canalla LLM/alex-llm\""
        );
        assert_eq!(
            login_command(Path::new("/home/me/$odd/app")),
            "\"/home/me/\\$odd/app\""
        );
    }

    #[test]
    fn the_data_root_follows_xdg() {
        let _sandbox = Sandbox::new("data-root");
        let previous = std::env::var_os("XDG_DATA_HOME");
        std::env::set_var("XDG_DATA_HOME", "/tmp/canalla-xdg-data");
        assert_eq!(
            data_root_default(),
            PathBuf::from("/tmp/canalla-xdg-data/alex-llm")
        );
        match previous {
            Some(value) => std::env::set_var("XDG_DATA_HOME", value),
            None => std::env::remove_var("XDG_DATA_HOME"),
        }
    }

    #[test]
    fn a_missing_controller_is_not_alive() {
        // 0 is never a process, and a pid that cannot exist must not be reported as running.
        assert!(!process_alive(0));
        assert!(!process_alive(u32::MAX));
        assert!(process_alive(std::process::id()));
    }

    #[test]
    fn no_store_means_a_typed_refusal_and_no_file() {
        let _sandbox = Sandbox::new("secret-unavailable");
        // WSL has no Secret Service. Whatever the environment, the contract is the same: either the
        // secret came back, or the refusal is typed - and nothing was written to disk either way.
        let dir = std::env::temp_dir().join(format!("canalla-secret-{}", std::process::id()));
        let _ = fs::remove_dir_all(&dir);
        let _ = fs::create_dir_all(&dir);
        match secret_write("unit-probe", "value", &dir) {
            Ok(()) => {
                assert_eq!(secret_read("unit-probe", &dir).unwrap(), "value");
                secret_delete("unit-probe", &dir).unwrap();
            }
            Err(error) => assert_eq!(error, SECURE_STORAGE_UNAVAILABLE),
        }
        let leftover: Vec<_> = fs::read_dir(&dir).unwrap().filter_map(Result::ok).collect();
        assert!(
            leftover.is_empty(),
            "a secret must never reach the filesystem"
        );
        let _ = fs::remove_dir_all(&dir);
    }

    /// The command name in `/proc/<pid>/stat` is the only field allowed to contain spaces and
    /// brackets, so the fields are read after its closing bracket. `[0]` is the state, `[1]` the
    /// parent, `[2]` the **process group**.
    fn stat_fields(pid: u32) -> Vec<String> {
        let text = fs::read_to_string(format!("/proc/{pid}/stat")).unwrap();
        let (_, rest) = text.rsplit_once(')').unwrap();
        rest.split_whitespace().map(str::to_string).collect()
    }

    /// Wait for a process to go away, bounded: a test that hangs is not a passing test.
    fn gone(pid: u32) -> bool {
        for _ in 0..100 {
            if !process_alive(pid) {
                return true;
            }
            std::thread::sleep(std::time::Duration::from_millis(100));
        }
        false
    }

    #[test]
    fn an_owned_child_is_its_own_process_group() {
        let mut command = Command::new("sleep");
        command.arg("30").stdout(Stdio::null());
        configure_spawn(&mut command);
        let child = command.spawn().unwrap();

        // The group id *is* the pid, which is what lets `terminate_tree` name the whole tree; a
        // child left in the desktop's own group would make that call signal the desktop too.
        assert_eq!(stat_fields(child.id())[2], child.id().to_string());
        let owned = own_child(&child).unwrap();
        assert_eq!(owned.group(), child.id());

        terminate_tree(child.id());
        assert!(gone(child.id()));
    }

    #[test]
    fn stopping_an_owned_child_takes_the_processes_it_started() {
        // `terminate_tree` is the POSIX answer to `taskkill /T /F`: the sidecar and the Tor daemon
        // it started have to stop together. A background job of a non-interactive shell stays in
        // the group, which is the same shape as the real tree.
        let report = std::env::temp_dir().join(format!("canalla-tree-{}.pid", std::process::id()));
        let _ = fs::remove_file(&report);
        let mut command = Command::new("sh");
        command.arg("-c").arg(format!(
            "sleep 30 & echo $! > \"{}\"; wait",
            report.display()
        ));
        configure_spawn(&mut command);
        let child = command.spawn().unwrap();

        let mut started = 0u32;
        for _ in 0..100 {
            if let Ok(pid) = fs::read_to_string(&report)
                .unwrap_or_default()
                .trim()
                .parse()
            {
                started = pid;
                break;
            }
            std::thread::sleep(std::time::Duration::from_millis(50));
        }
        assert_ne!(started, 0, "the child never reported what it started");
        assert!(process_alive(started));

        terminate_tree(child.id());
        assert!(
            gone(child.id()),
            "the owned child survived its own group being stopped"
        );
        assert!(
            gone(started),
            "a process the tree started outlived the tree"
        );
        let _ = fs::remove_file(&report);
    }

    /// Turns this test binary into the "parent that dies" for the test below.
    const PDEATHSIG_HELPER: &str = "CANALLA_PDEATHSIG_HELPER";

    #[test]
    fn an_armed_child_does_not_outlive_the_parent_that_died() {
        // A parent-death signal can only be proven by letting a real parent die, so this test
        // re-invokes its own binary as that parent. The helper arms a child, reports its pid and
        // exits without cleaning up; the kernel has to deliver SIGTERM. If it does not, the child
        // is still running and the test fails -- the guarantee is what is being checked, not the
        // call to `prctl`.
        if let Some(report) = std::env::var_os(PDEATHSIG_HELPER) {
            let mut command = Command::new("sleep");
            command.arg("120").stdout(Stdio::null());
            configure_spawn(&mut command);
            let child = command.spawn().unwrap();
            fs::write(PathBuf::from(report), child.id().to_string()).unwrap();
            // Abrupt on purpose: no terminate, no wait, no drop of the child handle.
            std::process::exit(0);
        }

        let report =
            std::env::temp_dir().join(format!("canalla-pdeathsig-{}.pid", std::process::id()));
        let _ = fs::remove_file(&report);
        let status = Command::new(std::env::current_exe().unwrap())
            .args([
                "platform::linux::tests::an_armed_child_does_not_outlive_the_parent_that_died",
                "--exact",
            ])
            .env(PDEATHSIG_HELPER, &report)
            .stdout(Stdio::null())
            .stderr(Stdio::null())
            .status()
            .unwrap();
        assert!(status.success(), "the helper parent did not exit cleanly");

        let armed: u32 = fs::read_to_string(&report).unwrap().trim().parse().unwrap();
        assert!(
            gone(armed),
            "the child (pid {armed}) outlived the parent that armed it"
        );
        let _ = fs::remove_file(&report);
    }
}
