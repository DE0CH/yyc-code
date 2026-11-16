# RomanSearch

The guest search of the name-tag app (design: `nametags/README.md`, "Search"). Swift package, no dependencies,
iOS 17+ / macOS 14+ / Linux.

```swift
public struct GuestSearch {
    public init(_ guests: [[String]])          // each guest = its searchable fields, name first
    public func search(_ query: String) -> [Int] // matching guest indexes, best first; "" -> all, in order
}
```

Build it once per guest list (off the main thread: the first build also loads the character table, 0.27 s on a
shared x86 CPU), then call `search` on every keystroke. With 5,000 guests a keystroke takes 1.8 ms in a release
build (50 ms in a debug build) on that CPU (`testFastEnoughForEveryKeystroke`).

## Behaviour

- Case, diacritics and full-width forms are ignored (`MÜLLER` = `muller`, `ＣＨＩＵ` = `chiu`). Spaces and
  punctuation separate words; every query word must match, in any order.
- A Latin word matches a field word exactly, as a prefix, or (2+ letters) as a substring; the name's initials
  (`kco` → Ki Cheung Oliver); or (3+ letters) a field with its spaces removed (`91234567` → `9123 4567`).
- In the name, a Latin word also matches the spellings of the same syllable in the other systems: Jyutping, HK
  government romanisation, Hanyu Pinyin, Wade-Giles (`zoeng`, `zhang` → Cheung; `gwok` → Kwok; `huang` → Wong;
  `zhao` → Chiu; `xu` → Hsu; `chou` → Chow; `hsieh` → Tse). Apostrophes inside a word are dropped, in queries and
  fields alike (`ch'ang` = `chang`, `O'Brien` = `obrien`); umlauts go with the other diacritics (`hsü` = `hsu`).
  These links go through common characters that have one reading in each language (polyphonic characters'
  readings are not aligned in the data, so they would link unrelated syllables).
- Chinese characters match the same character in any field (simplified and traditional fold together: 陳 finds
  陈) or, through every Cantonese and Mandarin reading, a romanised name word: 黃 → Wong, Huang; 趙 → Chiu, Zhao,
  Chao; 李 → Lee, Li; 葉 → Ip, Yip; 張 → Cheung, Zhang, Chang; 徐 → Tsui, Xu, Hsu; 謝 → Tse, Xie, Hsieh;
  蔡 → Choi, Cai, Tsai; 江 → Kong, Jiang, Chiang; 曾 → Tsang, Zeng, Tseng; 黃靜 → "Jing HUANG" and "Wong Ching". Up to four characters in a row may also spell
  one name word together (子怡 → Ziyi). A Latin word also matches the readings of Chinese characters in a name
  (`chan` → 陈小明).
- Ranking: an exact word beats a prefix, which beats a romanisation link, which beats a substring or initials;
  name matches beat matches in other fields; ties keep the input order.
- Emoji and other symbols must appear literally in a field; odd input (very long strings, control characters,
  unassigned code points) is truncated to 200 scalars / 24 words and never crashes.

## Data

`Sources/RomanSearch/Resources/romanisation.tsv` (664,823 bytes, 272 KB gzipped; 46,980 characters, 542
Jyutping syllables with HK spellings, 202 Pinyin syllables whose Wade-Giles spelling differs) is generated, never
edited: `Scripts/generate.sh` downloads the pinned sources and runs `Scripts/GenerateTable.swift`. Format:
`S<TAB>jyutping<TAB>HK spellings` and `W<TAB>pinyin<TAB>Wade-Giles` lines, then one line per character:
`char<TAB>jyutping<TAB>pinyin<TAB>extra HK spellings<TAB>c if common<TAB>traditional fold`. Wade-Giles spellings
are derived at load time from every Pinyin reading of a character.

| Source | What it gives | Licence |
| --- | --- | --- |
| [Unicode Unihan database 18.0.0](https://www.unicode.org/Public/18.0.0/ucd/Unihan.zip) (UAX #38) | `kCantonese` (Jyutping), `kMandarin` + `kTGHZ2013` + `kXHC1983` (every modern Pinyin reading), `kTraditionalVariant` (simplified → traditional fold), `kGradeLevel` + `kTGHZ2013` ("common") | Unicode License v3, `LICENSES/Unicode-3.0.txt` |
| [chunlaw/cantonese-romanisation](https://github.com/chunlaw/cantonese-romanisation) `dictionary/source.tsv` @ `689db59` | per character: the HK romanisations seen in HK names (周 Chow, 李 Lee, 葉 Ip, 施 Sze, 蔡 Choy) and LSHK Jyutping readings | MIT, `LICENSES/MIT-chunlaw-cantonese-romanisation.txt` |
| [Ostrichbeta/CCCConverter](https://github.com/Ostrichbeta/CCCConverter) `dict/initials.csv`, `dict/finals.csv` @ `0768f9a` | the Jyutping → HK government romanisation table (initials and finals, every attested spelling) from 片岡新 (Kataoka Shin), 〈「香港政府粵語拼音」：一個亂中有序的系統〉, 《中國語文通訊》 93(1), CUHK, Jan 2014 ([PDF](http://www.cuhk.edu.hk/ics/clrc/crcl_93_1/kataoka.pdf)), a study of the system from government publications | BSD-3-Clause, `LICENSES/BSD-3-Clause-CCCConverter.txt` |
| Library of Congress, [New Chinese Romanization Guidelines](https://www.loc.gov/catdir/pinyin/romcover.html), "Correspondence of Wade-Giles to Pinyin" (from 《地名漢字譯音表》 1971, the ALA-LC tables 1997 and 《現代漢語詞典》 1983) | 396 Wade-Giles ↔ Pinyin syllable pairs, aspiration marks dropped (ch`ang → chang). The page's markup changes between fetches, so the pin is the SHA-256 of the extracted table; its ü survives only as U+FFFD and is read as ü. The table is not exhaustive: Pinyin syllables in the data without a Wade-Giles entry include lüe, nüe, gei, shei, o, ei, eng (and rare ones), which then have no Wade-Giles spelling | US Government work, public domain, `LICENSES/LOC-Wade-Giles-public-domain.txt` |

How they combine: a syllable's HK spellings are Kataoka's initial × final spellings (`tsz`/`sze` forms only after
z c s), plus chunlaw's spellings for that syllable where at least two and a quarter of chunlaw's single-reading
characters agree and the spelling fits the syllable's initials and coda (zau → Chow, lei → Lee). chunlaw's other
spellings stay with their character. A character lacking a reading takes it from its simplified/traditional form.

## Tests

`swift test` (on Linux: Swift 6.1.2 from swift.org, `claude-env` lessons/77). Fixtures are synthetic names only.
The real guest list never goes into git; to test it:

```bash
Scripts/export-guests.py ~/artifacts/nametags/guests.xlsx /tmp/guests.json   # python3 + openpyxl
ROMANSEARCH_GUESTS_JSON=/tmp/guests.json swift test                          # each guest found by surname and full name
```
