//! yyc-apply: how a push to DE0CH/YYC reaches the cluster (deploy-workflows.md 6; tools/yyc-apply/README.md).
//! Drives kubectl (server-side apply, dry runs) and kustomize; owns the inventory, the deletion gate, the order and
//! the waits. Knows nothing about YYC AI beyond its flags.

mod apply;
mod check;
mod git;
mod inventory;
mod kube;
mod obj;
mod plan;

use anyhow::{Context, Result};
use clap::{Args, Parser, Subcommand};
use inventory::InvRef;
use obj::Id;
use std::path::PathBuf;
use std::process::ExitCode;

#[derive(Parser)]
#[command(
    name = "yyc-apply",
    about = "render, plan, gate, apply, wait, prune (deploy-workflows.md 6)"
)]
struct Cli {
    /// kubectl binary (talks to $KUBECONFIG)
    #[arg(long, global = true, default_value = "kubectl")]
    kubectl: String,
    #[arg(long, global = true, default_value = "kustomize")]
    kustomize: String,
    #[command(subcommand)]
    cmd: Cmd,
}

#[derive(Args, Clone)]
struct Render {
    /// the kustomize folder applied by the deployer
    #[arg(long)]
    folder: PathBuf,
    /// the sealed folder (SealedSecrets only), applied as --sealed-as
    #[arg(long)]
    sealed: Option<PathBuf>,
    /// an object id left out of render, plan and apply (an object another owner still applies)
    #[arg(long)]
    exclude: Vec<String>,
    /// the folder's own namespaces (objects elsewhere apply first, with the cluster-scoped ones)
    #[arg(long, value_delimiter = ',', default_value = "yyc-ai,yyc-ai-fabric")]
    namespaces: Vec<String>,
    #[arg(long, default_value = "system:serviceaccount:yyc-deploy:sealed-secrets-applier")]
    sealed_as: String,
    /// the git checkout (trailers, ancestry)
    #[arg(long, default_value = ".")]
    repo: PathBuf,
}

#[derive(Subcommand)]
enum Cmd {
    /// items 9-12 of the check job (no cluster access)
    Check {
        #[command(flatten)]
        r: Render,
        /// RBAC files the rights are read from (the deployer's, the fence's)
        #[arg(long, required = true)]
        access: Vec<PathBuf>,
        #[arg(long)]
        secrets: Option<PathBuf>,
        /// commits whose Deploy-* trailers are checked (A..B)
        #[arg(long)]
        commits: Option<String>,
        #[arg(long, default_value = "gha:yyc-ai")]
        deployer: String,
        /// an object id that exists in the cluster already (the deployer may patch it by name but not create it)
        #[arg(long)]
        existing: Vec<String>,
    },
    /// plan against the live state; with --trailers, print the trailer lines the plan needs (exit 1 when blocked)
    Plan {
        #[command(flatten)]
        r: Render,
        #[arg(long)]
        inventory: String,
        #[arg(long, default_value = "plan.md")]
        out: PathBuf,
        #[arg(long)]
        trailers: bool,
        /// this run's id (the workflow's run id): another run's live inventory lease refuses the plan
        #[arg(long, default_value = "local")]
        run: String,
        /// plan although another run holds the inventory's lease (only when that run is known to be dead)
        #[arg(long)]
        force: bool,
    },
    /// execute a plan
    Apply {
        #[arg(long, default_value = "plan.md")]
        plan: PathBuf,
        #[arg(long, default_value = "local")]
        run: String,
        #[arg(long, default_value = "15m")]
        rollout_timeout: String,
        #[arg(long, default_value_t = 120)]
        sealed_timeout_secs: u64,
        #[arg(long, default_value = "10m")]
        prune_timeout: String,
        /// added to the longest grace period of a pruned workload's Pods before giving up on their drain
        #[arg(long, default_value_t = 120)]
        drain_margin_secs: u64,
        /// the inventory lease, renewed every quarter of it while the run lives
        #[arg(long, default_value_t = 120)]
        lease_secs: u64,
    },
}

fn ids(v: &[String]) -> Result<Vec<Id>> {
    v.iter().map(|s| Id::parse(s)).collect()
}

#[allow(clippy::print_stdout)]
fn run(cli: Cli) -> Result<ExitCode> {
    let kube = kube::Kube::new(&cli.kubectl);
    match cli.cmd {
        Cmd::Check {
            r,
            access,
            secrets,
            commits,
            deployer,
            existing,
        } => {
            let o = check::Opts {
                repo: r.repo,
                folder: r.folder,
                sealed: r.sealed,
                access,
                secrets,
                commits,
                exclude: ids(&r.exclude)?,
                home: r.namespaces,
                deployer,
                existing: ids(&existing)?,
                sealed_as: r.sealed_as,
                kustomize: cli.kustomize,
            };
            let fails = check::check(&o)?;
            for f in &fails {
                println!("FAIL {f}");
            }
            if fails.is_empty() {
                println!("check: items 9-12 pass ({})", o.folder.display());
                Ok(ExitCode::SUCCESS)
            } else {
                Ok(ExitCode::FAILURE)
            }
        }
        Cmd::Plan {
            r,
            inventory,
            out,
            trailers,
            run,
            force,
        } => {
            let o = plan::Opts {
                repo: r.repo,
                folder: r.folder.clone(),
                sealed: r.sealed,
                inventory: InvRef::parse(&inventory)?,
                exclude: ids(&r.exclude)?,
                home: r.namespaces,
                sealed_as: r.sealed_as,
                kustomize: cli.kustomize,
                run,
                force,
            };
            let p = plan::plan(&kube, &o)?;
            if trailers {
                // what the plan needs, and the Deploy-Prune lines already in range that it uses
                let mut lines = p.trailer_lines();
                lines.extend(
                    p.prune
                        .iter()
                        .filter(|x| x.why == "Deploy-Prune")
                        .map(|x| format!("Deploy-Prune: {}", x.id)),
                );
                for l in lines {
                    println!("{l}");
                }
                for w in &p.warnings {
                    eprintln!("warning: {w}");
                }
                for e in &p.errors {
                    eprintln!("error: {e}");
                }
                if p.blocked() {
                    eprintln!(
                        "the plan is blocked ({} refused, {} errors): add the lines above, or fix the errors",
                        p.refused.len(),
                        p.errors.len()
                    );
                    return Ok(ExitCode::FAILURE);
                }
                return Ok(ExitCode::SUCCESS);
            }
            plan::write(&p, &out, &r.folder.display().to_string())?;
            print!("{}", plan::markdown(&p, &r.folder.display().to_string()));
            Ok(if p.blocked() {
                ExitCode::FAILURE
            } else {
                ExitCode::SUCCESS
            })
        }
        Cmd::Apply {
            plan,
            run,
            rollout_timeout,
            sealed_timeout_secs,
            prune_timeout,
            drain_margin_secs,
            lease_secs,
        } => {
            let p = plan::load(&plan)?;
            let o = apply::Opts {
                run,
                rollout_timeout,
                sealed_timeout: std::time::Duration::from_secs(sealed_timeout_secs),
                prune_timeout,
                drain_margin: std::time::Duration::from_secs(drain_margin_secs),
                lease_secs,
            };
            apply::apply(&kube, &p, &o)?;
            Ok(ExitCode::SUCCESS)
        }
    }
}

fn main() -> ExitCode {
    match run(Cli::parse()).context("yyc-apply") {
        Ok(c) => c,
        Err(e) => {
            eprintln!("yyc-apply: {e:#}");
            ExitCode::FAILURE
        }
    }
}
