//! The clusters this watch guards finish its job: the watch only deletes a stalled primary's Pod, and the Cluster's
//! settings decide how fast that Pod is gone and how fast dead client connections let go of their locks. Pinned here
//! (YYC, 2026-11-20): with the operator's defaults the Pod of a primary whose storage stalled was stopped by the
//! kubelet at 08:28:16 and kept serving as primary until 08:46:38: the pre-shutdown CHECKPOINT waited on the stalled
//! storage, then 180 s of smart shutdown, under a 1800 s grace period; and sessions on dead connections held their row
//! locks for the 15 minutes TCP took to give up.

use std::path::PathBuf;

fn clusters() -> Vec<(String, serde_yaml::Value)> {
    let talos = PathBuf::from(env!("CARGO_MANIFEST_DIR")).join("../news-stream/deploy/clusters/talos");
    let mut out = Vec::new();
    for f in ["pg/pg.yaml", "db/magpie-pg.yaml"] {
        let text = std::fs::read_to_string(talos.join(f)).unwrap_or_else(|e| panic!("{f}: {e}"));
        for doc in serde_yaml::Deserializer::from_str(&text) {
            let v = serde_yaml::Value::deserialize(doc).unwrap();
            if v["kind"] == "Cluster" && v["apiVersion"] == "postgresql.cnpg.io/v1" {
                out.push((v["metadata"]["name"].as_str().unwrap().to_owned(), v));
            }
        }
    }
    out
}

use serde::Deserialize as _;

fn int(v: &serde_yaml::Value) -> Option<i64> {
    v.as_i64().or_else(|| v.as_str().and_then(|s| s.parse().ok()))
}

#[test]
fn a_stalled_primary_is_gone_within_a_minute_of_its_pod_being_deleted() {
    let cs = clusters();
    let names: Vec<&str> = cs.iter().map(|(n, _)| n.as_str()).collect();
    assert_eq!(names, ["pg", "magpie-pg"], "the two clusters the watch guards");
    for (name, c) in &cs {
        let spec = &c["spec"];
        let stop = int(&spec["stopDelay"]).unwrap_or(1800);
        let smart = int(&spec["smartShutdownTimeout"]).unwrap_or(180);
        assert!(stop <= 60, "{name}: stopDelay {stop} s: the kubelet must kill a stuck shutdown within a minute");
        assert!(
            smart < stop,
            "{name}: smartShutdownTimeout {smart} s must leave room for the fast shutdown under stopDelay {stop} s"
        );
        assert!(smart <= 15, "{name}: smartShutdownTimeout {smart} s: new connections are refused all that time");
        let ms = int(&spec["postgresql"]["parameters"]["tcp_user_timeout"]).unwrap_or(0);
        assert!(
            (1..=15_000).contains(&ms),
            "{name}: tcp_user_timeout {ms} ms: a session whose client is gone must let go of its locks in seconds"
        );
    }
}
