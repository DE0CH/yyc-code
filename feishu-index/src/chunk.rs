//! Sections (what extraction produces) and chunks (what is indexed and embedded).

/// A part of a file: a document's body, one sheet of a spreadsheet, one table of a Base.
#[derive(Debug, Clone, PartialEq)]
pub struct Section {
    /// e.g. the sheet's or table's name; empty for a document's body
    pub heading: String,
    /// repeated at the top of every chunk of this section (a sheet's header row, a table's field names)
    pub header: Option<String>,
    /// paragraphs or rows, in order
    pub lines: Vec<String>,
}

impl Section {
    pub fn body(lines: Vec<String>) -> Self {
        Section {
            heading: String::new(),
            header: None,
            lines,
        }
    }
}

#[derive(Debug, Clone, PartialEq)]
pub struct Chunk {
    pub heading: String,
    pub body: String,
}

/// Characters per chunk: a target and a hard limit for one line split on its own.
pub const TARGET: usize = 1500;

fn split_long(line: &str, limit: usize) -> Vec<String> {
    let chars: Vec<char> = line.chars().collect();
    chars.chunks(limit).map(|c| c.iter().collect()).collect()
}

/// Packs each section's lines into chunks of at most about `TARGET` characters, never across sections; a section's
/// header starts each of its chunks.
pub fn chunks(sections: &[Section]) -> Vec<Chunk> {
    let mut out = Vec::new();
    for s in sections {
        let header = s.header.clone().unwrap_or_default();
        let hlen = header.chars().count();
        let room = TARGET.saturating_sub(hlen).max(TARGET / 3);
        let mut cur = String::new();
        let mut cur_len = 0usize;
        let flush = |cur: &mut String, cur_len: &mut usize, out: &mut Vec<Chunk>| {
            if *cur_len > 0 {
                let body = if header.is_empty() {
                    cur.clone()
                } else {
                    format!("{header}\n{cur}")
                };
                out.push(Chunk {
                    heading: s.heading.clone(),
                    body,
                });
            }
            cur.clear();
            *cur_len = 0;
        };
        for line in &s.lines {
            let line = line.trim_end();
            if line.trim().is_empty() {
                continue;
            }
            let pieces = if line.chars().count() > room {
                split_long(line, room)
            } else {
                vec![line.to_string()]
            };
            for p in pieces {
                let plen = p.chars().count();
                if cur_len > 0 && cur_len + 1 + plen > room {
                    flush(&mut cur, &mut cur_len, &mut out);
                }
                if cur_len > 0 {
                    cur.push('\n');
                    cur_len += 1;
                }
                cur.push_str(&p);
                cur_len += plen;
            }
        }
        flush(&mut cur, &mut cur_len, &mut out);
    }
    out
}

#[cfg(test)]
mod tests {
    use super::*;

    #[test]
    fn packs_lines_and_keeps_sections_apart() {
        let a = Section::body(vec!["one".into(), "two".into()]);
        let b = Section {
            heading: "Sheet1".into(),
            header: Some("name\tage".into()),
            lines: vec!["x\t1".into()],
        };
        let c = chunks(&[a, b]);
        assert_eq!(c.len(), 2);
        assert_eq!(c[0].body, "one\ntwo");
        assert_eq!(c[1].heading, "Sheet1");
        assert_eq!(c[1].body, "name\tage\nx\t1");
    }

    #[test]
    fn splits_at_the_target_and_repeats_the_header() {
        let rows: Vec<String> = (0..400).map(|i| format!("row {i:04} 香港數據")).collect();
        let s = Section {
            heading: "T".into(),
            header: Some("H".into()),
            lines: rows,
        };
        let c = chunks(&[s]);
        assert!(c.len() > 1);
        for ch in &c {
            assert!(ch.body.starts_with("H\n"));
            assert!(ch.body.chars().count() <= TARGET + 2);
        }
        let all: usize = c.iter().map(|ch| ch.body.matches("row ").count()).sum();
        assert_eq!(all, 400);
    }

    #[test]
    fn a_long_line_is_split() {
        let s = Section::body(vec!["字".repeat(TARGET * 2 + 10)]);
        let c = chunks(&[s]);
        assert_eq!(c.len(), 3);
        assert_eq!(
            c.iter().map(|ch| ch.body.chars().count()).sum::<usize>(),
            TARGET * 2 + 10
        );
    }

    #[test]
    fn empty_sections_give_no_chunks() {
        assert!(chunks(&[Section::body(vec!["  ".into(), String::new()])]).is_empty());
    }
}
