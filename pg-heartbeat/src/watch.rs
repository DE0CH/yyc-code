//! The rule (pure: no I/O, the caller passes the time): a primary that answers reads but has not committed a
//! one-row write for [`STALL_LIMIT`] is replaced. Nothing else counts as a stall: a write that fails with an error,
//! or a database that cannot be reached at all, is a failover or a switchover under way, or this pod's own network,
//! and the cluster's operator handles those.
//!
//! Why (YYC, 2026-11-20 08:28-08:46 UTC): pg-1's storage stalled. Reads went on, every commit waited, the operator's
//! probes (`pg_isready`, a read) kept the primary Ready, and both standbys stayed connected to it, so nothing failed
//! over for 18 minutes. The operator replaces a primary whose Pod goes; deleting the Pod (with the Cluster's short
//! `stopDelay`, which kills a shutdown that waits on the same storage) is all this does.

use std::time::{Duration, Instant};

/// How long a primary may answer reads while it commits nothing before its Pod is deleted.
pub const STALL_LIMIT: Duration = Duration::from_secs(30);
/// After a deletion, the next one waits at least this long (the new primary's own start, a slow promotion).
pub const COOLDOWN: Duration = Duration::from_secs(600);

/// One heartbeat's outcome.
#[derive(Clone, Copy, Debug, PartialEq, Eq)]
pub enum Beat {
    /// The write committed.
    Committed,
    /// Connected, the write got no answer within its deadline, and a read on a new connection answered.
    Stalled,
    /// Anything else: no connection, an error answer, or reads that do not answer either.
    Unreachable,
}

/// What the cluster's operator says, read only when a replacement is due.
#[derive(Clone, Debug, PartialEq, Eq)]
pub struct Cluster {
    /// `status.currentPrimary`.
    pub primary: String,
    /// `status.readyInstances` (the primary among them).
    pub ready: u32,
}

/// What to do now.
#[derive(Clone, Debug, PartialEq, Eq)]
pub enum Action {
    Nothing,
    /// Read the cluster's state and call [`Watch::confirm`].
    ReadCluster,
    /// Delete this Pod (the stalled primary).
    DeletePod(String),
}

/// One cluster's watch.
#[derive(Debug, Default)]
pub struct Watch {
    stalled_since: Option<Instant>,
    last_delete: Option<Instant>,
}

impl Watch {
    /// Takes one heartbeat in.
    pub fn observe(&mut self, beat: Beat, now: Instant) -> Action {
        match beat {
            Beat::Committed | Beat::Unreachable => {
                self.stalled_since = None;
                Action::Nothing
            }
            Beat::Stalled => {
                let since = *self.stalled_since.get_or_insert(now);
                let cooled = self.last_delete.is_none_or(|t| now.duration_since(t) >= COOLDOWN);
                if now.duration_since(since) >= STALL_LIMIT && cooled {
                    Action::ReadCluster
                } else {
                    Action::Nothing
                }
            }
        }
    }

    /// After [`Action::ReadCluster`]: the primary's Pod goes when another instance is ready to take over.
    pub fn confirm(&mut self, cluster: &Cluster, now: Instant) -> Action {
        if cluster.ready < 2 || cluster.primary.is_empty() {
            return Action::Nothing;
        }
        self.last_delete = Some(now);
        self.stalled_since = None;
        Action::DeletePod(cluster.primary.clone())
    }
}

#[cfg(test)]
mod tests {
    use super::*;

    const BEAT: Duration = Duration::from_secs(10);

    fn cluster(ready: u32) -> Cluster {
        Cluster {
            primary: "pg-1".into(),
            ready,
        }
    }

    /// Runs `beats` 10 s apart from `t0`; the first action that is not Nothing, with its beat's index.
    fn run(w: &mut Watch, t0: Instant, beats: &[Beat]) -> Option<(usize, Action)> {
        beats.iter().enumerate().find_map(|(i, b)| {
            let a = w.observe(*b, t0 + BEAT * u32::try_from(i).unwrap());
            (a != Action::Nothing).then_some((i, a))
        })
    }

    #[test]
    fn a_primary_that_commits_nothing_for_thirty_seconds_while_it_reads_is_replaced() {
        let (mut w, t0) = (Watch::default(), Instant::now());
        let got = run(&mut w, t0, &[Beat::Committed, Beat::Stalled, Beat::Stalled, Beat::Stalled, Beat::Stalled]);
        assert_eq!(got, Some((4, Action::ReadCluster)), "the 4th stalled beat is 30 s after the first");
        assert_eq!(w.confirm(&cluster(3), t0 + BEAT * 4), Action::DeletePod("pg-1".into()));
    }

    #[test]
    fn errors_and_lost_connections_are_not_a_stall() {
        let (mut w, t0) = (Watch::default(), Instant::now());
        let mut beats = vec![Beat::Stalled, Beat::Stalled, Beat::Unreachable];
        beats.extend([Beat::Stalled; 3]);
        beats.extend([Beat::Committed, Beat::Stalled, Beat::Stalled]);
        assert_eq!(run(&mut w, t0, &beats), None, "a stall must last 30 s without a break");
        let all_away = [Beat::Unreachable; 20];
        assert_eq!(run(&mut Watch::default(), t0, &all_away), None);
    }

    #[test]
    fn nothing_is_deleted_without_a_ready_standby() {
        let (mut w, t0) = (Watch::default(), Instant::now());
        assert_eq!(w.confirm(&cluster(1), t0), Action::Nothing);
        let empty = Cluster {
            primary: String::new(),
            ready: 3,
        };
        assert_eq!(w.confirm(&empty, t0), Action::Nothing);
        // and it is asked again at the next stalled beat
        let got = run(&mut w, t0, &[Beat::Stalled; 5]);
        assert_eq!(got, Some((3, Action::ReadCluster)));
    }

    #[test]
    fn one_deletion_then_a_cooldown() {
        let (mut w, t0) = (Watch::default(), Instant::now());
        assert_eq!(run(&mut w, t0, &[Beat::Stalled; 4]), Some((3, Action::ReadCluster)));
        let deleted_at = t0 + BEAT * 3;
        assert_eq!(w.confirm(&cluster(3), deleted_at), Action::DeletePod("pg-1".into()));
        // the new primary stalls too: not again within the cooldown
        let t1 = deleted_at + BEAT;
        assert_eq!(run(&mut w, t1, &[Beat::Stalled; 30]), None, "within 10 min of the deletion");
        // a stall that lasted through the cooldown is acted on as soon as it ends
        let t2 = deleted_at + COOLDOWN;
        assert_eq!(run(&mut w, t2, &[Beat::Stalled; 4]), Some((0, Action::ReadCluster)));
    }
}
