//! The rule (pure: no I/O, the caller passes the time): a primary that answers reads but has not committed a
//! one-row write for [`STALL_LIMIT`] is replaced, and when its Pod cannot even be stopped, its node is reset.
//! Nothing else counts as a stall: a write that fails with an error, or a database that cannot be reached at all, is
//! a failover or switchover under way, or this pod's own network, and the cluster's operator handles those.
//!
//! Why (YYC, 2026-11-20 08:28-08:46 UTC): pg-1's storage stalled. Reads went on, every commit waited, the operator's
//! probes (`pg_isready`, a read) kept the primary Ready, and both standbys stayed connected to it, so nothing failed
//! over for 18 minutes. The operator replaces a primary whose Pod goes. Deleting the Pod (the Cluster's short
//! `stopDelay` has the kubelet kill a shutdown that waits on the stalled storage) is the first step; a process stuck
//! in the kernel on that storage survives even a kill, and then only resetting the node lets the standbys go (the
//! fencing Patroni's watchdog does by resetting the machine).

use std::time::{Duration, Instant};

/// How long a primary may answer reads while it commits nothing before its Pod is deleted.
pub const STALL_LIMIT: Duration = Duration::from_secs(10);
/// After the deletion, how long the Pod may still be terminating before its node is reset (the Clusters'
/// `stopDelay` of 15 s, and the kill after it).
pub const RESET_AFTER: Duration = Duration::from_secs(20);
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
    /// A planned switchover is under way (`db/switchover.py` or `kubectl cnpg promote`): it stops the primary itself.
    pub switchover: bool,
}

/// The deleted primary's Pod, as read [`RESET_AFTER`] after the deletion.
#[derive(Clone, Debug, PartialEq, Eq)]
pub enum PodState {
    /// Gone, or a new Pod of that name (not terminating): the deletion did its work.
    Gone,
    /// The deleted Pod still terminating, on this node.
    Terminating(String),
}

/// What to do now.
#[derive(Clone, Debug, PartialEq, Eq)]
pub enum Action {
    Nothing,
    /// Read the cluster's state and call [`Watch::confirm`].
    ReadCluster,
    /// Delete this Pod (the stalled primary).
    DeletePod(String),
    /// Read this Pod and call [`Watch::checked`].
    CheckPod(String),
    /// Reset this node (the stalled primary's Pod cannot be stopped).
    ResetNode(String),
}

/// One cluster's watch.
#[derive(Debug, Default)]
pub struct Watch {
    stalled_since: Option<Instant>,
    last_delete: Option<Instant>,
    /// The Pod deleted and when, until it is seen gone or its node is reset.
    deleted: Option<(String, Instant)>,
}

impl Watch {
    /// Takes one heartbeat in.
    pub fn observe(&mut self, beat: Beat, now: Instant) -> Action {
        if beat == Beat::Committed {
            // a primary commits: whatever was deleted is done with
            self.stalled_since = None;
            self.deleted = None;
            return Action::Nothing;
        }
        if let Some((pod, at)) = &self.deleted {
            return if now.duration_since(*at) >= RESET_AFTER {
                Action::CheckPod(pod.clone())
            } else {
                Action::Nothing
            };
        }
        if beat == Beat::Unreachable {
            self.stalled_since = None;
            return Action::Nothing;
        }
        let since = *self.stalled_since.get_or_insert(now);
        let cooled = self
            .last_delete
            .is_none_or(|t| now.duration_since(t) >= COOLDOWN);
        if now.duration_since(since) >= STALL_LIMIT && cooled {
            Action::ReadCluster
        } else {
            Action::Nothing
        }
    }

    /// After [`Action::ReadCluster`]: the primary's Pod goes when another instance is ready to take over and no
    /// planned switchover is under way.
    pub fn confirm(&mut self, cluster: &Cluster, now: Instant) -> Action {
        if cluster.switchover || cluster.ready < 2 || cluster.primary.is_empty() {
            return Action::Nothing;
        }
        self.last_delete = Some(now);
        self.stalled_since = None;
        self.deleted = Some((cluster.primary.clone(), now));
        Action::DeletePod(cluster.primary.clone())
    }

    /// After [`Action::CheckPod`]: a Pod still terminating has its node reset (once).
    pub fn checked(&mut self, pod: &PodState) -> Action {
        self.deleted = None;
        match pod {
            PodState::Gone => Action::Nothing,
            PodState::Terminating(node) => Action::ResetNode(node.clone()),
        }
    }
}

#[cfg(test)]
mod tests {
    use super::*;

    const BEAT: Duration = Duration::from_secs(2);

    fn cluster(ready: u32) -> Cluster {
        Cluster {
            primary: "pg-1".into(),
            ready,
            switchover: false,
        }
    }

    /// Runs `beats` 2 s apart from `t0`; the first action that is not Nothing, with its beat's index.
    fn run(w: &mut Watch, t0: Instant, beats: &[Beat]) -> Option<(usize, Action)> {
        beats.iter().enumerate().find_map(|(i, b)| {
            let a = w.observe(*b, t0 + BEAT * u32::try_from(i).unwrap());
            (a != Action::Nothing).then_some((i, a))
        })
    }

    #[test]
    fn a_primary_that_commits_nothing_for_ten_seconds_while_it_reads_is_replaced() {
        let (mut w, t0) = (Watch::default(), Instant::now());
        let mut beats = vec![Beat::Committed];
        beats.extend([Beat::Stalled; 6]);
        assert_eq!(
            run(&mut w, t0, &beats),
            Some((6, Action::ReadCluster)),
            "10 s after the first stalled beat"
        );
        assert_eq!(
            w.confirm(&cluster(3), t0 + BEAT * 6),
            Action::DeletePod("pg-1".into())
        );
    }

    #[test]
    fn errors_and_lost_connections_are_not_a_stall() {
        let (mut w, t0) = (Watch::default(), Instant::now());
        let mut beats = vec![Beat::Stalled; 4];
        beats.push(Beat::Unreachable);
        beats.extend([Beat::Stalled; 5]);
        beats.push(Beat::Committed);
        beats.extend([Beat::Stalled; 5]);
        assert_eq!(
            run(&mut w, t0, &beats),
            None,
            "a stall must last 10 s without a break"
        );
        assert_eq!(
            run(&mut Watch::default(), t0, &[Beat::Unreachable; 100]),
            None
        );
    }

    #[test]
    fn nothing_is_deleted_without_a_ready_standby_or_during_a_planned_switchover() {
        let (mut w, t0) = (Watch::default(), Instant::now());
        assert_eq!(w.confirm(&cluster(1), t0), Action::Nothing);
        let empty = Cluster {
            primary: String::new(),
            ..cluster(3)
        };
        assert_eq!(w.confirm(&empty, t0), Action::Nothing);
        let switching = Cluster {
            switchover: true,
            ..cluster(3)
        };
        assert_eq!(w.confirm(&switching, t0), Action::Nothing);
        // and it is asked again at the next stalled beat
        assert_eq!(
            run(&mut w, t0, &[Beat::Stalled; 7]),
            Some((5, Action::ReadCluster))
        );
    }

    #[test]
    fn a_pod_still_terminating_twenty_seconds_after_its_deletion_has_its_node_reset_once() {
        let (mut w, t0) = (Watch::default(), Instant::now());
        assert_eq!(
            run(&mut w, t0, &[Beat::Stalled; 6]),
            Some((5, Action::ReadCluster))
        );
        let deleted_at = t0 + BEAT * 5;
        assert_eq!(
            w.confirm(&cluster(3), deleted_at),
            Action::DeletePod("pg-1".into())
        );
        // the database is away meanwhile (the failover): the check still comes, 20 s after the deletion
        let after = deleted_at + BEAT;
        let mut beats = vec![Beat::Stalled; 3];
        beats.extend([Beat::Unreachable; 7]);
        assert_eq!(
            run(&mut w, after, &beats),
            Some((9, Action::CheckPod("pg-1".into())))
        );
        assert_eq!(
            w.checked(&PodState::Terminating("yyc-talos-db1".into())),
            Action::ResetNode("yyc-talos-db1".into())
        );
        // once: then the cooldown holds any further deletion
        assert_eq!(run(&mut w, after + BEAT * 10, &[Beat::Stalled; 60]), None);
    }

    #[test]
    fn a_pod_that_went_or_a_primary_that_commits_ends_the_check() {
        let (mut w, t0) = (Watch::default(), Instant::now());
        run(&mut w, t0, &[Beat::Stalled; 6]);
        let deleted_at = t0 + BEAT * 5;
        w.confirm(&cluster(3), deleted_at);
        assert_eq!(
            run(&mut w, deleted_at + BEAT, &[Beat::Unreachable; 10]),
            Some((9, Action::CheckPod("pg-1".into())))
        );
        assert_eq!(w.checked(&PodState::Gone), Action::Nothing);
        assert_eq!(
            run(&mut w, deleted_at + BEAT * 11, &[Beat::Unreachable; 30]),
            None,
            "no check twice"
        );
        // a new primary that commits before the check: no check at all
        let (mut w2, t2) = (Watch::default(), Instant::now());
        run(&mut w2, t2, &[Beat::Stalled; 6]);
        w2.confirm(&cluster(3), t2 + BEAT * 5);
        let mut beats = vec![Beat::Unreachable; 4];
        beats.extend([Beat::Committed; 20]);
        assert_eq!(run(&mut w2, t2 + BEAT * 6, &beats), None);
    }

    #[test]
    fn a_stall_through_the_cooldown_is_acted_on_when_it_ends() {
        let (mut w, t0) = (Watch::default(), Instant::now());
        run(&mut w, t0, &[Beat::Stalled; 6]);
        let deleted_at = t0 + BEAT * 5;
        w.confirm(&cluster(3), deleted_at);
        // the check finds it gone; a new primary stalls too: nothing within the cooldown
        run(&mut w, deleted_at + BEAT, &[Beat::Unreachable; 10]);
        w.checked(&PodState::Gone);
        assert_eq!(
            run(&mut w, deleted_at + BEAT * 11, &[Beat::Stalled; 200]),
            None
        );
        let t2 = deleted_at + COOLDOWN;
        assert_eq!(
            run(&mut w, t2, &[Beat::Stalled; 2]),
            Some((0, Action::ReadCluster))
        );
    }
}
