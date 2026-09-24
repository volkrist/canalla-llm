//! One contract, one implementation per platform.
//!
//! The product runs on Windows today and must run on Ubuntu 24.04. The two systems disagree about
//! almost everything the desktop needs — how to own a child process, where a secret goes, how a
//! login entry is registered, where the user's folders are — so those disagreements live here and
//! nowhere else. A caller asks for the guarantee ("the tree this desktop started must not survive
//! it"), never for the mechanism.
//!
//! What the contract has to cover, and why:
//!
//! * **process ownership** — the desktop owns the backend it started: it must be able to stop it
//!   and its children, and a desktop that dies must not leave an orphan behind. Windows owns its
//!   children with a kill-on-close Job Object; POSIX uses a process group the child is put into plus
//!   a parent-death signal, because it has no equivalent object.
//! * **secret storage** — Credential Manager on Windows, the Secret Service on Linux, and a typed
//!   refusal on either when the real store cannot be reached. There is no plaintext fallback.
//! * **login registration** — the registry `Run` value on Windows, an XDG autostart entry on Linux.
//!   Both are read back after every write, so the UI can never show a state the machine does not
//!   have.
//! * **the machine's own facts** — the user's well-known folders, a short system summary, an atomic
//!   file replacement, and the platform's default data root (which the Python half has to agree
//!   with, because both halves use one directory).

/// The typed refusal a platform returns when its real secret store cannot be reached. It is part of
/// the shared contract — the frontend maps it to a user-facing message — and it is deliberately not
/// a fallback: writing the credential into a plain file would make every later check meaningless.
/// (Only the POSIX implementation ever produces it: Windows always has a store it can use.)
#[cfg_attr(windows, allow(dead_code))]
pub const SECURE_STORAGE_UNAVAILABLE: &str = "secure_storage_unavailable";

#[cfg(windows)]
mod windows;

#[cfg(unix)]
mod linux;

#[cfg(windows)]
pub use windows::*;

#[cfg(unix)]
pub use linux::*;
