//! Search text: the hidden search copy of a chunk and of a query, built here with no Postgres text-search parser,
//! dictionary or extension (YYC's Postgres image has no Chinese parser).
//!
//! - `norm`: NFKC (full-width to half-width), Traditional to Simplified (zhconv, so 計劃 finds 计划 and the HK variants
//!   merge), lower case.
//! - `tokens`: a run of letters/digits outside the CJK scripts is one word; every CJK character is a unigram at its own
//!   position and starts a bigram with the next CJK character at the same position.
//! - `tsvector`: the tokens as a tsvector literal (cast with `::tsvector`).
//! - `tsquery`: each whitespace-separated term of a query as a phrase over its tokens, the terms ANDed. A CJK run of two
//!   or more characters is matched by its bigrams; a lone CJK character by its unigram.

use std::collections::BTreeMap;
use unicode_normalization::UnicodeNormalization;

/// Bump when norm/tokens change in a way that needs every chunk re-indexed.
pub const RULES: i32 = 1;
const MAX_POS: u32 = 16383; // Postgres' highest tsvector position
const MAX_LEXEME: usize = 60; // characters; a longer word is not indexed as a word

pub fn norm(s: &str) -> String {
    let nfkc: String = s.nfkc().collect();
    zhconv::zhconv(&nfkc, zhconv::Variant::ZhHans).to_lowercase()
}

pub fn is_cjk(c: char) -> bool {
    matches!(c as u32,
        0x3040..=0x30FF      // Hiragana, Katakana
        | 0x3400..=0x4DBF    // CJK Extension A
        | 0x4E00..=0x9FFF    // CJK Unified Ideographs
        | 0xAC00..=0xD7AF    // Hangul syllables
        | 0xF900..=0xFAFF    // CJK Compatibility Ideographs
        | 0x20000..=0x3134F  // CJK Extensions B-G
    )
}

#[derive(Debug, PartialEq)]
pub enum Tok {
    Word(String),
    /// A run of CJK characters (already normalised).
    Cjk(Vec<char>),
}

/// Splits normalised text into words and CJK runs, in order.
pub fn runs(normed: &str) -> Vec<Tok> {
    let mut out = Vec::new();
    let mut word = String::new();
    let mut cjk: Vec<char> = Vec::new();
    for c in normed.chars() {
        if is_cjk(c) {
            if !word.is_empty() {
                out.push(Tok::Word(std::mem::take(&mut word)));
            }
            cjk.push(c);
        } else if c.is_alphanumeric() {
            if !cjk.is_empty() {
                out.push(Tok::Cjk(std::mem::take(&mut cjk)));
            }
            word.push(c);
        } else {
            if !word.is_empty() {
                out.push(Tok::Word(std::mem::take(&mut word)));
            }
            if !cjk.is_empty() {
                out.push(Tok::Cjk(std::mem::take(&mut cjk)));
            }
        }
    }
    if !word.is_empty() {
        out.push(Tok::Word(word));
    }
    if !cjk.is_empty() {
        out.push(Tok::Cjk(cjk));
    }
    out
}

fn quote(lex: &str) -> String {
    let mut q = String::with_capacity(lex.len() + 2);
    q.push('\'');
    for c in lex.chars() {
        if c == '\'' || c == '\\' {
            q.push(c);
        }
        q.push(c);
    }
    q.push('\'');
    q
}

/// The tsvector literal of `text` (normalised here).
pub fn tsvector(text: &str) -> String {
    let mut lex: BTreeMap<String, Vec<u32>> = BTreeMap::new();
    let mut pos: u32 = 0;
    let add = |l: String, p: u32, lex: &mut BTreeMap<String, Vec<u32>>| {
        let v = lex.entry(l).or_default();
        if v.len() < 255 && v.last() != Some(&p) {
            v.push(p);
        }
    };
    for t in runs(&norm(text)) {
        match t {
            Tok::Word(w) => {
                pos = (pos + 1).min(MAX_POS);
                if w.chars().count() <= MAX_LEXEME {
                    add(w, pos, &mut lex);
                }
            }
            Tok::Cjk(cs) => {
                for i in 0..cs.len() {
                    pos = (pos + 1).min(MAX_POS);
                    add(cs[i].to_string(), pos, &mut lex);
                    if i + 1 < cs.len() {
                        add(format!("{}{}", cs[i], cs[i + 1]), pos, &mut lex);
                    }
                }
            }
        }
    }
    lex.into_iter()
        .map(|(l, ps)| {
            let ps: Vec<String> = ps.iter().map(|p| p.to_string()).collect();
            format!("{}:{}", quote(&l), ps.join(","))
        })
        .collect::<Vec<_>>()
        .join(" ")
}

/// The phrase tsquery of one term, or None when it has no searchable token.
fn term_query(term: &str) -> Option<String> {
    // (lexeme, distance from the previous lexeme)
    let mut parts: Vec<(String, u32)> = Vec::new();
    let mut gap: u32 = 0; // positions consumed since the last emitted lexeme
    for t in runs(&norm(term)) {
        match t {
            Tok::Word(w) => {
                if w.chars().count() > MAX_LEXEME {
                    return None;
                }
                parts.push((w, gap.max(1)));
                gap = 1;
            }
            Tok::Cjk(cs) if cs.len() == 1 => {
                parts.push((cs[0].to_string(), gap.max(1)));
                gap = 1;
            }
            Tok::Cjk(cs) => {
                for i in 0..cs.len() - 1 {
                    parts.push((format!("{}{}", cs[i], cs[i + 1]), gap.max(1)));
                    gap = 1;
                }
                gap = 2; // the run's last character has a position but starts no bigram
            }
        }
    }
    if parts.is_empty() {
        return None;
    }
    let mut q = quote(&parts[0].0);
    for (l, d) in &parts[1..] {
        if *d == 1 {
            q.push_str(" <-> ");
        } else {
            q.push_str(&format!(" <{d}> "));
        }
        q.push_str(&quote(l));
    }
    Some(if parts.len() > 1 { format!("({q})") } else { q })
}

/// The tsquery of a search query (terms split on whitespace, ANDed), or None when nothing is searchable.
pub fn tsquery(query: &str) -> Option<String> {
    let terms: Vec<String> = query.split_whitespace().filter_map(term_query).collect();
    if terms.is_empty() {
        None
    } else {
        Some(terms.join(" & "))
    }
}

#[cfg(test)]
mod tests {
    use super::*;

    #[test]
    fn norm_merges_traditional_simplified_and_widths() {
        assert_eq!(norm("新資本投資者入境計劃"), norm("新资本投资者入境计划"));
        assert_eq!(norm("ＡＢＣ１２３"), "abc123");
    }

    #[test]
    fn runs_split_words_and_cjk() {
        assert_eq!(
            runs("hk 政策2026年"),
            vec![
                Tok::Word("hk".into()),
                Tok::Cjk(vec!['政', '策']),
                Tok::Word("2026".into()),
                Tok::Cjk(vec!['年'])
            ]
        );
    }

    #[test]
    fn tsvector_has_unigrams_and_bigrams_at_shared_positions() {
        let v = tsvector("入境計劃 AI");
        assert!(v.contains("'入':1"), "{v}");
        assert!(v.contains("'入境':1"), "{v}");
        assert!(v.contains("'计划':3"), "{v}");
        assert!(v.contains("'划':4"), "{v}");
        assert!(v.contains("'ai':5"), "{v}");
    }

    #[test]
    fn tsquery_phrases() {
        assert_eq!(
            tsquery("入境計劃").unwrap(),
            "('入境' <-> '境计' <-> '计划')"
        );
        assert_eq!(tsquery("港").unwrap(), "'港'");
        assert_eq!(tsquery("政策 AI").unwrap(), "'政策' & 'ai'");
        // a CJK run then a word in one term: the word sits two positions after the last bigram
        assert_eq!(tsquery("政策AI").unwrap(), "('政策' <2> 'ai')");
        assert_eq!(tsquery("AI政策").unwrap(), "('ai' <-> '政策')");
        assert!(tsquery("  ,, !! ").is_none());
    }

    #[test]
    fn quotes_are_escaped() {
        assert!(tsvector("it's").contains("'it'"));
        assert_eq!(quote("a'b\\c"), "'a''b\\\\c'");
    }
}
