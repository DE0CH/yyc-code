//! Text out of uploaded files (PDF, Word, Excel, PowerPoint, plain text) and out of Feishu's JSON cell/field values.

use crate::chunk::Section;
use anyhow::{Context, Result, bail};
use quick_xml::events::Event;
use serde_json::Value;
use std::io::{Cursor, Read};

/// The lower-case extension of a file name, without the dot.
pub fn ext(name: &str) -> String {
    name.rsplit_once('.')
        .map(|(_, e)| e.to_ascii_lowercase())
        .unwrap_or_default()
}

/// Whether an uploaded file of this name has text we can extract.
pub fn supported(name: &str) -> bool {
    matches!(
        ext(name).as_str(),
        "pdf"
            | "docx"
            | "xlsx"
            | "xlsm"
            | "xls"
            | "ods"
            | "pptx"
            | "txt"
            | "md"
            | "csv"
            | "tsv"
            | "json"
            | "html"
            | "htm"
            | "xml"
    )
}

/// Sections of an uploaded file's bytes, chosen by its name's extension.
pub async fn file(name: &str, bytes: Vec<u8>) -> Result<Vec<Section>> {
    match ext(name).as_str() {
        "pdf" => pdf(bytes).await,
        "docx" => docx(&bytes),
        "pptx" => pptx(&bytes),
        "xlsx" | "xlsm" | "xls" | "ods" => spreadsheet(bytes),
        "html" | "htm" | "xml" => Ok(vec![Section::body(lines(&strip_tags(
            &String::from_utf8_lossy(&bytes),
        )))]),
        "txt" | "md" | "csv" | "tsv" | "json" => {
            Ok(vec![Section::body(lines(&String::from_utf8_lossy(&bytes)))])
        }
        e => bail!("no text extraction for .{e}"),
    }
}

pub fn lines(s: &str) -> Vec<String> {
    s.lines().map(|l| l.to_string()).collect()
}

/// pdftotext (poppler-utils, in the image), page by page so a page is never glued to the next.
async fn pdf(bytes: Vec<u8>) -> Result<Vec<Section>> {
    let dir = tempfile::tempdir()?;
    let path = dir.path().join("in.pdf");
    tokio::fs::write(&path, &bytes).await?;
    let out = tokio::process::Command::new("pdftotext")
        .args(["-layout", "-enc", "UTF-8"])
        .arg(&path)
        .arg("-")
        .output()
        .await
        .context("running pdftotext")?;
    if !out.status.success() {
        bail!(
            "pdftotext failed: {}",
            String::from_utf8_lossy(&out.stderr).trim()
        );
    }
    let text = String::from_utf8_lossy(&out.stdout);
    let mut sections = Vec::new();
    for (i, page) in text.split('\u{c}').enumerate() {
        let ls: Vec<String> = page
            .lines()
            .map(|l| l.split_whitespace().collect::<Vec<_>>().join(" "))
            .collect();
        if ls.iter().any(|l| !l.is_empty()) {
            sections.push(Section {
                heading: format!("page {}", i + 1),
                header: None,
                lines: ls,
            });
        }
    }
    Ok(sections)
}

fn zip_entry(bytes: &[u8], name: &str) -> Result<Option<String>> {
    let mut z = zip::ZipArchive::new(Cursor::new(bytes)).context("not a zip archive")?;
    let Ok(mut f) = z.by_name(name) else {
        return Ok(None);
    };
    let mut s = String::new();
    f.read_to_string(&mut s)?;
    Ok(Some(s))
}

/// The text of every `para_tag` element (w:p, a:p) as one line each, from the `text_tag` (w:t, a:t) inside it;
/// w:tab and w:br become a tab and a new line.
fn ooxml_paragraphs(xml: &str, para_tag: &[u8], text_tag: &[u8]) -> Vec<String> {
    let mut r = quick_xml::Reader::from_str(xml);
    let mut out = Vec::new();
    let mut cur = String::new();
    let mut in_text = false;
    loop {
        match r.read_event() {
            Ok(Event::Start(e)) if e.name().as_ref() == text_tag => in_text = true,
            Ok(Event::End(e)) if e.name().as_ref() == text_tag => in_text = false,
            Ok(Event::Text(t)) if in_text => {
                if let Ok(s) = t.unescape() {
                    cur.push_str(&s);
                }
            }
            Ok(Event::Empty(e)) if e.name().as_ref() == b"w:tab" => cur.push('\t'),
            Ok(Event::Empty(e)) if e.name().as_ref() == b"w:br" || e.name().as_ref() == b"a:br" => {
                cur.push('\n')
            }
            Ok(Event::End(e)) if e.name().as_ref() == para_tag => {
                out.extend(cur.split('\n').map(|s| s.to_string()));
                cur.clear();
            }
            Ok(Event::Eof) | Err(_) => break,
            _ => {}
        }
    }
    if !cur.is_empty() {
        out.push(cur);
    }
    out
}

pub fn docx(bytes: &[u8]) -> Result<Vec<Section>> {
    let xml = zip_entry(bytes, "word/document.xml")?.context("no word/document.xml")?;
    Ok(vec![Section::body(ooxml_paragraphs(&xml, b"w:p", b"w:t"))])
}

pub fn pptx(bytes: &[u8]) -> Result<Vec<Section>> {
    let mut z = zip::ZipArchive::new(Cursor::new(bytes)).context("not a zip archive")?;
    let mut slides: Vec<(u32, String)> = z
        .file_names()
        .filter_map(|n| {
            let num = n
                .strip_prefix("ppt/slides/slide")?
                .strip_suffix(".xml")?
                .parse()
                .ok()?;
            Some((num, n.to_string()))
        })
        .collect();
    slides.sort();
    let mut out = Vec::new();
    for (num, name) in slides {
        let mut s = String::new();
        z.by_name(&name)?.read_to_string(&mut s)?;
        out.push(Section {
            heading: format!("slide {num}"),
            header: None,
            lines: ooxml_paragraphs(&s, b"a:p", b"a:t"),
        });
    }
    Ok(out)
}

fn spreadsheet(bytes: Vec<u8>) -> Result<Vec<Section>> {
    use calamine::{Reader, open_workbook_auto_from_rs};
    let mut wb = open_workbook_auto_from_rs(Cursor::new(bytes)).context("opening the workbook")?;
    let mut out = Vec::new();
    for name in wb.sheet_names().to_vec() {
        let Ok(range) = wb.worksheet_range(&name) else {
            continue;
        };
        let mut rows = range.rows().map(|r| {
            r.iter()
                .map(|c| c.to_string().replace(['\t', '\n'], " "))
                .collect::<Vec<_>>()
                .join("\t")
                .trim_end()
                .to_string()
        });
        let header = rows.next().filter(|h| !h.is_empty());
        let lines: Vec<String> = rows.filter(|l| !l.is_empty()).collect();
        if header.is_some() || !lines.is_empty() {
            out.push(Section {
                heading: name,
                header,
                lines,
            });
        }
    }
    Ok(out)
}

pub fn strip_tags(html: &str) -> String {
    let mut out = String::with_capacity(html.len());
    let mut in_tag = false;
    for c in html.chars() {
        match c {
            '<' => in_tag = true,
            '>' => {
                in_tag = false;
                out.push(' ');
            }
            _ if !in_tag => out.push(c),
            _ => {}
        }
    }
    out
}

/// One cell or field value of a Sheet or Base as text: strings and numbers as they are, rich-text segments joined,
/// people/options/links by their name, text or link, nested lists joined with ", ".
pub fn value_text(v: &Value) -> String {
    match v {
        Value::Null => String::new(),
        Value::Bool(b) => b.to_string(),
        Value::Number(n) => n.to_string(),
        Value::String(s) => s.clone(),
        Value::Array(a) => {
            let segs: Vec<&Value> = a.iter().collect();
            // rich text: [{"type":"text","text":"a"},{"type":"url","text":"b","link":"…"}] reads as one string
            if !segs.is_empty()
                && segs
                    .iter()
                    .all(|s| s.get("text").is_some() && s.get("type").is_some())
            {
                return segs
                    .iter()
                    .filter_map(|s| s["text"].as_str())
                    .collect::<String>();
            }
            segs.iter()
                .map(|s| value_text(s))
                .filter(|s| !s.is_empty())
                .collect::<Vec<_>>()
                .join(", ")
        }
        Value::Object(o) => {
            for k in [
                "text",
                "name",
                "en_name",
                "full_name",
                "link",
                "url",
                "value",
            ] {
                if let Some(x) = o.get(k) {
                    let t = value_text(x);
                    if !t.is_empty() {
                        return t;
                    }
                }
            }
            if let Some(x) = o.get("value") {
                return value_text(x);
            }
            String::new()
        }
    }
}

#[cfg(test)]
mod tests {
    use super::*;
    use serde_json::json;
    use std::io::Write;

    pub fn make_zip(entries: &[(&str, &str)]) -> Vec<u8> {
        let mut buf = Cursor::new(Vec::new());
        {
            let mut z = zip::ZipWriter::new(&mut buf);
            for (n, c) in entries {
                z.start_file(*n, zip::write::SimpleFileOptions::default())
                    .unwrap();
                z.write_all(c.as_bytes()).unwrap();
            }
            z.finish().unwrap();
        }
        buf.into_inner()
    }

    #[test]
    fn docx_paragraphs() {
        let xml = r#"<w:document><w:body><w:p><w:r><w:t>第一段</w:t></w:r><w:r><w:t xml:space="preserve"> more</w:t></w:r></w:p><w:p><w:r><w:t>a</w:t><w:tab/><w:t>b &amp; c</w:t></w:r></w:p></w:body></w:document>"#;
        let s = docx(&make_zip(&[("word/document.xml", xml)])).unwrap();
        assert_eq!(s[0].lines, vec!["第一段 more", "a\tb & c"]);
    }

    #[test]
    fn pptx_slides_in_order() {
        let sl = |t: &str| format!("<p:sld><a:p><a:r><a:t>{t}</a:t></a:r></a:p></p:sld>");
        let z = make_zip(&[
            ("ppt/slides/slide10.xml", &sl("ten")),
            ("ppt/slides/slide2.xml", &sl("two")),
        ]);
        let s = pptx(&z).unwrap();
        assert_eq!(
            s.iter().map(|x| x.heading.as_str()).collect::<Vec<_>>(),
            vec!["slide 2", "slide 10"]
        );
        assert_eq!(s[1].lines, vec!["ten"]);
    }

    #[tokio::test]
    async fn plain_text_and_html() {
        let s = file("a.MD", "x\ny".into()).await.unwrap();
        assert_eq!(s[0].lines, vec!["x", "y"]);
        let s = file("a.html", "<p>hi</p><b>there</b>".into())
            .await
            .unwrap();
        assert!(s[0].lines[0].contains("hi") && s[0].lines[0].contains("there"));
        assert!(file("a.exe", vec![]).await.is_err());
    }

    #[tokio::test]
    async fn pdf_pages() {
        // a one-page PDF with "Hello PDF" (pdftotext must be installed, as in the image)
        let pdf = include_bytes!("../tests/hello.pdf").to_vec();
        let s = file("x.pdf", pdf).await.unwrap();
        assert_eq!(s[0].heading, "page 1");
        assert!(s[0].lines.join(" ").contains("Hello PDF"));
    }

    #[test]
    fn values() {
        assert_eq!(value_text(&json!("a")), "a");
        assert_eq!(value_text(&json!(3.5)), "3.5");
        assert_eq!(
            value_text(&json!([{"type":"text","text":"ab"},{"type":"url","text":"cd","link":"x"}])),
            "abcd"
        );
        assert_eq!(
            value_text(&json!([{"name":"陳大文","id":"ou_1"},{"name":"Li"}])),
            "陳大文, Li"
        );
        assert_eq!(value_text(&json!(["opt1", "opt2"])), "opt1, opt2");
        assert_eq!(
            value_text(&json!({"link":"https://x","text":"site"})),
            "site"
        );
        assert_eq!(value_text(&json!(null)), "");
    }

    #[test]
    fn supported_names() {
        assert!(supported("Report.PDF"));
        assert!(supported("a.xlsx"));
        assert!(!supported("photo.jpg"));
        assert!(!supported("noext"));
    }
}
