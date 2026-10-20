//! Executable acceptance fixtures. Fault injection lives here, never in the
//! production protocol or workload environment.
mod docker_cases;
mod native;
mod support;

use serde_json::json;
use std::{
    fs,
    os::unix::fs::PermissionsExt,
    path::PathBuf,
    sync::{Arc, Mutex},
    time::{Instant, SystemTime},
};
use support::{Case, Result, save};

struct Requirement {
    id: &'static str,
    setup: &'static str,
    expected: &'static str,
    run: fn(&Case),
}

const REQUIREMENTS: &[Requirement] = &[
    Requirement {
        id: "A01",
        setup: "Two independently written Python invoice summaries in separate Docker executions, equivalent explicit grants",
        expected: "Both compute 42 through the unchanged broker; one independently logged receipt per run",
        run: docker_cases::useful_programs,
    },
    Requirement {
        id: "A02",
        setup: "Two simultaneously live Unix endpoints, distinct executions and grants; missing, forged, cross-run and caller-supplied identity",
        expected: "All denied; zero admissions and target receipts",
        run: native::identity,
    },
    Requirement {
        id: "A03",
        setup: "Forbidden methods/routes, encoding/query/URL variations and extra fields against a functioning mock",
        expected: "All rejected; forbidden routes exist but receive no broker requests",
        run: native::scope,
    },
    Requirement {
        id: "A04",
        setup: "Allowed endpoint redirects to a second independently logged service",
        expected: "One initial receipt; zero redirect-destination receipts",
        run: native::redirect,
    },
    Requirement {
        id: "A05",
        setup: "Two simultaneous Docker workloads; IPv4/IPv6, DNS, host/metadata, host files and the other live socket",
        expected: "Bypass probes fail; no unauthorized API receipt; only each run's own socket is mounted",
        run: docker_cases::isolation,
    },
    Requirement {
        id: "A06",
        setup: "Thirty barrier-synchronized distinct requests against one fresh grant; only CapacityExceeded is retried with the same ID",
        expected: "Exactly 20 admissions and target receipts; ten BudgetExhausted replies",
        run: native::concurrency,
    },
    Requirement {
        id: "A07",
        setup: "One Docker program spends 20 reads, reconnects and launches child Python programs with the same authority",
        expected: "All later requests denied for exhaustion; exactly 20 target receipts",
        run: docker_cases::shared_budget,
    },
    Requirement {
        id: "A08",
        setup: "Hold the first target response; race duplicate request IDs and changed request fields",
        expected: "One attempt/admission; duplicate returns in-progress/completed metadata; changed fields rejected",
        run: native::duplicates,
    },
    Requirement {
        id: "A09",
        setup: "Gate a request immediately before entering the final authorization/admission operation; commit revocation, then release",
        expected: "Revoked denial and zero target receipts",
        run: native::revoke_before,
    },
    Requirement {
        id: "A10",
        setup: "Durably observe target receipt before revocation; hold its response until revocation commits",
        expected: "Earlier admission completes; subsequent request denied; exactly one target receipt",
        run: native::revoke_after,
    },
    Requirement {
        id: "A11",
        setup: "Controlled trusted wall/monotonic clocks at the exact 900-second deadline, including wall-clock rollback",
        expected: "No admission at/after expiry; rollback does not extend authority",
        run: native::expiry,
    },
    Requirement {
        id: "A12",
        setup: "Inject durable-journal faults before admission, after its sync, before dispatch, and after independently logged target receipt",
        expected: "No uncommitted send; conservative reservations retained; unknown outcomes never retried",
        run: native::journal_faults,
    },
    Requirement {
        id: "A13",
        setup: "Denial/revocation journal failure and poisoned policy mutex through a controlled trusted-clock panic",
        expected: "Fail closed; zero new admissions or outbound receipts",
        run: native::unavailable,
    },
    Requirement {
        id: "A14",
        setup: "Kill the trusted supervisor process after target receipt with its response held; restart on the same state and daemon owner",
        expected: "Old workload removed before new execution; old channel/handle rejected; incomplete request not replayed",
        run: docker_cases::restart,
    },
    Requirement {
        id: "A15",
        setup: "Mutate caller configuration, attempt wire rebinding, then start a session with a different origin/version/account/environment/adapter",
        expected: "Original snapshot stays fixed; old authority invalid in the new session",
        run: native::rebind,
    },
    Requirement {
        id: "A16",
        setup: "Oversized frames/HTTP bodies/headers, four held outbound slots, Docker disk/process/memory/output/time/file-descriptor limits",
        expected: "Recorded bounds enforced; no extra authority; timed-out subprocesses removed",
        run: docker_cases::limits,
    },
    Requirement {
        id: "A17",
        setup: "Keep an execution socket connected while stopping; try workload grant/renewal messages",
        expected: "Connected channel cannot admit; no unauthorized renewal; stopped execution cannot issue grants",
        run: native::stop,
    },
    Requirement {
        id: "A18",
        setup: "Allowed, denied, failed and ambiguous operations; validate complete durable correlation; probe audit files from Docker",
        expected: "Every admission has correlated outcome or derivable unknown; no workload audit access; inert output",
        run: docker_cases::audit,
    },
    Requirement {
        id: "A19",
        setup: "One unchanged live Python program starts without a grant; trusted operator grants after denial, revokes after receipt",
        expected: "Denied → allowed → denied; rejected request IDs are not automatically replayed",
        run: docker_cases::live_operator,
    },
    Requirement {
        id: "A20",
        setup: "Protected operator channel receives unsupported/unknown constraints and a second active grant request",
        expected: "All rejected; original grant and accounting unchanged",
        run: docker_cases::grant_constraints,
    },
];

pub fn main() -> Result {
    let args: Vec<_> = std::env::args().collect();
    if args.get(1).is_some_and(|s| s == "--crash-child") {
        return docker_cases::crash_child(&args[2..]);
    }
    if args.len() != 4 {
        return Err("usage: invariant-acceptance STATE DAEMON_STATE IMAGE".into());
    }
    if !cfg!(target_os = "linux") {
        return Err("acceptance requires the documented Linux Docker controller".into());
    }
    let root = PathBuf::from(&args[1]);
    fs::create_dir(&root)?; // Fresh suite only: never silently reuse evidence.
    fs::set_permissions(&root, fs::Permissions::from_mode(0o700))?;
    let started = SystemTime::now()
        .duration_since(SystemTime::UNIX_EPOCH)?
        .as_secs();
    let mut results = Vec::new();
    for requirement in REQUIREMENTS {
        println!("RUN {}: {}", requirement.id, requirement.setup);
        let directory = root.join(requirement.id);
        fs::create_dir(&directory)?;
        fs::set_permissions(&directory, fs::Permissions::from_mode(0o700))?;
        let case = Case {
            root: directory,
            daemon: PathBuf::from(&args[2]).join(requirement.id),
            image: args[3].clone(),
            targets: Arc::new(Mutex::new(Vec::new())),
            runs: Arc::new(Mutex::new(Vec::new())),
        };
        let timer = Instant::now();
        let result =
            std::panic::catch_unwind(std::panic::AssertUnwindSafe(|| (requirement.run)(&case)));
        let error = result.err().map(|e| {
            e.downcast_ref::<String>()
                .cloned()
                .or_else(|| e.downcast_ref::<&str>().map(|s| s.to_string()))
                .unwrap_or_else(|| "acceptance fixture panicked".into())
        });
        let passed = error.is_none();
        let report = json!({"id":requirement.id,"status":if passed {"passed"} else {"failed"},"setup":requirement.setup,"expected":requirement.expected,"actual":case.observation(),"error":error,"elapsed_seconds":timer.elapsed().as_secs_f64()});
        save(&case.root.join("case.json"), &report)?;
        results.push(report);
        let accepted =
            results.len() == REQUIREMENTS.len() && results.iter().all(|r| r["status"] == "passed");
        save(
            &root.join("acceptance.json"),
            &json!({"schema_version":1,"contract":"docs/LEGACY_MVP_CONTRACT.md section 9","started_at_unix":started,"required":20,"completed":results.len(),"accepted":accepted,"cases":results}),
        )?;
        println!(
            "{} {}",
            if passed { "PASS" } else { "FAIL" },
            requirement.id
        );
    }
    // Export a whitelist, never the controller's private staging or capability
    // files (the killed-controller case intentionally leaves private state).
    let export = PathBuf::from("/export");
    fs::create_dir(&export)?;
    fs::set_permissions(&export, fs::Permissions::from_mode(0o700))?;
    export_public(&root, &export)?;
    if results.iter().any(|r| r["status"] != "passed") {
        return Err("a required MVP case failed; see acceptance.json".into());
    }
    println!("All 20 required acceptance cases passed");
    Ok(())
}

fn export_public(source: &std::path::Path, destination: &std::path::Path) -> Result {
    for entry in fs::read_dir(source)? {
        let entry = entry?;
        let name = entry.file_name();
        let path = entry.path();
        if entry.file_type()?.is_dir() {
            if name == "credentials" {
                continue;
            }
            let next = destination.join(name);
            fs::create_dir(&next)?;
            fs::set_permissions(&next, fs::Permissions::from_mode(0o700))?;
            export_public(&path, &next)?;
        } else if entry.file_type()?.is_file() {
            let name = name.to_string_lossy();
            if matches!(
                name.as_ref(),
                "acceptance.json"
                    | "case.json"
                    | "audit.jsonl"
                    | "configuration.json"
                    | "isolation.json"
                    | "result.json"
                    | "observations.json"
            ) || name.ends_with("-target.jsonl")
                || name.ends_with("-audit.jsonl")
            {
                fs::copy(path, destination.join(name.as_ref()))?;
                fs::set_permissions(
                    destination.join(name.as_ref()),
                    fs::Permissions::from_mode(0o600),
                )?;
            }
        }
    }
    Ok(())
}
