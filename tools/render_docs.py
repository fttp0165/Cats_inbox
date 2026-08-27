#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""
render_docs.py — 由 Markdown 權威版產生 light 主題 HTML 發布版(憲法第四條)。

為什麼存在:
    第四條4 要求正式文件 md+HTML 並存且內容同步。人工維護兩版必然漂移,
    所以 HTML 一律由本工具自 md 產生;測試以 --check 模式驗證「重產 == 現存」,
    讓漂移在 CI 就被擋下,而不是幾週後才被讀者發現。

設計限制(刻意):
    - 只用標準函式庫——本工具要能在 CI、開發者本機、VM 上直接跑,
      不能因為缺一個 pip 套件而讓「文件同步」這條紅線失效。
    - 輸出必須是「輸入的純函數」(不嵌時間戳、不嵌隨機值),
      否則 --check 的 diff 驗證會永遠失敗。
    - 支援的 Markdown 子集以本 repo 文件實際用到的語法為準,
      不追求完整 CommonMark——多支援一種語法就多一種漂移的可能。

圖示規則(第四條5):
    md 內以獨立一行 `<!--SVG:名稱-->` 標記圖示;產生 HTML 時抽換為
    docs/assets/名稱.svg 的內嵌 SVG。若標記的下一個非空行是 fenced code
    (md 版的 ASCII 文字圖),該區塊視為「僅 md 顯示」而自 HTML 略去,
    避免同一張圖在 HTML 出現兩次。

用法:
    python3 tools/render_docs.py            # 重產所有登記的正式文件 HTML
    python3 tools/render_docs.py --check    # 只比對不寫檔;漂移則 exit 1(給 CI)
    python3 tools/render_docs.py a.md b.md  # 只處理指定檔(輸出同名 .html)
"""

import html
import re
import sys
from pathlib import Path

# repo 根目錄:本檔固定位於 tools/ 之下,據此定位,與執行時的 cwd 無關
ROOT = Path(__file__).resolve().parent.parent
ASSETS = ROOT / "docs" / "assets"

# 正式文件登記表(第四條4 的「兩版並存」適用範圍)。
# 開發日誌等內部紀錄可僅 md,故不在此表。新增正式文件時只改這裡。
TARGETS = [
    ROOT / "docs" / "開發計畫書.md",
    ROOT / "docs" / "任務表.md",
    ROOT / "docs" / "TDD測試計畫表.md",
    ROOT / "docs" / "SSO接入申請.md",
    ROOT / "docs" / "契約對齊盤點_v3.3.md",
    ROOT / "docs" / "架構說明.md",
    ROOT / "docs" / "發版SOP.md",
    ROOT / "docs" / "進度表.md",
    ROOT / "README.md",
]

# light 主題樣式(第四條1/2):白底深字、不含 prefers-color-scheme: dark、
# 零外部資源(無 CDN 字型/腳本),單檔離線可開。
CSS = """
:root { color-scheme: light; }
body { background:#ffffff; color:#1f2328; margin:0 auto; padding:2rem 1.2rem 4rem;
       max-width:60rem; line-height:1.65;
       font-family:-apple-system,"Segoe UI","Noto Sans TC","Microsoft JhengHei",
                   "PingFang TC",sans-serif; }
h1 { font-size:1.7rem; border-bottom:2px solid #d0d7de; padding-bottom:.4rem; }
h2 { font-size:1.35rem; border-bottom:1px solid #d8dee4; padding-bottom:.3rem; margin-top:2.2rem; }
h3 { font-size:1.12rem; margin-top:1.8rem; }
h4 { font-size:1rem; margin-top:1.4rem; }
p, li { overflow-wrap:break-word; }
a { color:#0b57d0; }
code { background:#f6f8fa; border:1px solid #e4e8ec; border-radius:4px;
       padding:.08em .35em; font-size:.92em;
       font-family:ui-monospace,SFMono-Regular,Consolas,"Noto Sans Mono CJK TC",monospace; }
pre { background:#f6f8fa; border:1px solid #e4e8ec; border-radius:6px;
      padding: .8rem 1rem; overflow-x:auto; line-height:1.5; }
pre code { background:none; border:none; padding:0; }
blockquote { margin:1rem 0; padding:.5rem 1rem; border-left:4px solid #c9d1d9;
             background:#f8f9fb; color:#4b535d; }
blockquote p { margin:.35rem 0; }
.tablewrap { overflow-x:auto; margin:1rem 0; }
table { border-collapse:collapse; font-size:.95em; min-width:50%; }
th, td { border:1px solid #d0d7de; padding:.4rem .65rem; text-align:left; vertical-align:top; }
th { background:#f6f8fa; }
tr:nth-child(even) td { background:#fbfcfd; }
hr { border:none; border-top:1px solid #d0d7de; margin:2rem 0; }
figure.svg { margin:1.2rem 0; text-align:center; }
figure.svg svg { max-width:100%; height:auto; }
""".strip()


def render_inline(text: str) -> str:
    """行內語法轉換:跳脫 HTML 後依序處理 code span、粗體、斜體、連結。

    參數: text — 一行(或一格)未跳脫的 Markdown 純文字
    回傳: 安全的 HTML 片段
    副作用: 無
    先跳脫再套規則:使用者文字裡的 < > & 一律以實體呈現(XSS 紅線的文件版)。
    """
    s = html.escape(text, quote=False)
    # code span 先做,並以佔位符保護,避免其內容再被粗體/連結規則改寫
    codes: list = []

    def _stash(m):
        codes.append(m.group(1))
        return f"\x00{len(codes) - 1}\x00"

    s = re.sub(r"`([^`]+)`", _stash, s)
    s = re.sub(r"\*\*(.+?)\*\*", r"<strong>\1</strong>", s)
    s = re.sub(r"(?<!\*)\*([^*\n]+)\*(?!\*)", r"<em>\1</em>", s)
    s = re.sub(r"\[([^\]]+)\]\(([^)\s]+)\)", r'<a href="\2">\1</a>', s)
    s = re.sub(r"\x00(\d+)\x00", lambda m: f"<code>{codes[int(m.group(1))]}</code>", s)
    return s


def _inline_svg(name: str) -> str:
    """讀入 docs/assets/<name>.svg 並包成 figure;找不到檔案時直接失敗。

    刻意不做靜默 fallback:圖示缺檔若只是「HTML 少一張圖」,
    漂移就從這裡開始——寧可讓產生器當場報錯。
    """
    p = ASSETS / f"{name}.svg"
    svg = p.read_text(encoding="utf-8")
    svg = re.sub(r"^<\?xml[^>]*\?>\s*", "", svg)  # 內嵌時不需 XML 宣告
    return f'<figure class="svg">{svg}</figure>'


def render_blocks(lines: list) -> str:
    """區塊層解析:將 md 行序列轉為 HTML 區塊序列。

    參數: lines — md 內容逐行(無行尾換行符)
    回傳: HTML 字串
    副作用: 無(讀 SVG 檔除外)
    支援:標題、fenced code、表格、blockquote(遞迴)、清單(含巢狀與勾選框)、
          水平線、段落(段內換行保留為 <br>,配合 metadata 短行群)。
    """
    out: list = []
    i = 0
    n = len(lines)

    while i < n:
        line = lines[i]

        if not line.strip():
            i += 1
            continue

        # ── 圖示標記:抽換為內嵌 SVG;緊接的 fenced code 視為 md 版 ASCII 圖,略去
        m = re.match(r"^<!--SVG:([\w\-\u4e00-\u9fff]+)-->\s*$", line)
        if m:
            out.append(_inline_svg(m.group(1)))
            i += 1
            while i < n and not lines[i].strip():
                i += 1
            if i < n and lines[i].startswith("```"):
                i += 1
                while i < n and not lines[i].startswith("```"):
                    i += 1
                i += 1  # 收尾的 ```
            continue

        # ── 其他單行 HTML 註解:不輸出
        if re.match(r"^<!--.*-->\s*$", line):
            i += 1
            continue

        # ── fenced code
        if line.startswith("```"):
            i += 1
            buf = []
            while i < n and not lines[i].startswith("```"):
                buf.append(lines[i])
                i += 1
            i += 1
            code = html.escape("\n".join(buf), quote=False)
            out.append(f"<pre><code>{code}</code></pre>")
            continue

        # ── 標題
        m = re.match(r"^(#{1,6})\s+(.*)$", line)
        if m:
            lvl = len(m.group(1))
            out.append(f"<h{lvl}>{render_inline(m.group(2).strip())}</h{lvl}>")
            i += 1
            continue

        # ── 水平線
        if re.match(r"^-{3,}\s*$", line):
            out.append("<hr>")
            i += 1
            continue

        # ── 表格:目前行以 | 起頭、下一行是分隔列
        if line.lstrip().startswith("|") and i + 1 < n \
                and re.match(r"^\s*\|[\s:\-|]+\|\s*$", lines[i + 1]):
            header = _split_row(line)
            i += 2
            rows = []
            while i < n and lines[i].lstrip().startswith("|"):
                rows.append(_split_row(lines[i]))
                i += 1
            thead = "".join(f"<th>{render_inline(c)}</th>" for c in header)
            body = "".join(
                "<tr>" + "".join(f"<td>{render_inline(c)}</td>" for c in r) + "</tr>"
                for r in rows
            )
            out.append(
                f'<div class="tablewrap"><table><thead><tr>{thead}</tr></thead>'
                f"<tbody>{body}</tbody></table></div>"
            )
            continue

        # ── blockquote:收集連續 > 行,剝一層後遞迴解析
        if line.lstrip().startswith(">"):
            buf = []
            while i < n and lines[i].lstrip().startswith(">"):
                buf.append(re.sub(r"^\s*> ?", "", lines[i]))
                i += 1
            out.append(f"<blockquote>{render_blocks(buf)}</blockquote>")
            continue

        # ── 清單(- / * / 1.),縮排 2 空白為一層;空行即結束清單區塊
        if re.match(r"^\s*([-*]|\d+\.)\s+", line):
            items = []
            while i < n and lines[i].strip():
                m = re.match(r"^(\s*)([-*]|\d+\.)\s+(.*)$", lines[i])
                if m:
                    items.append((len(m.group(1)) // 2,
                                  "ol" if m.group(2)[0].isdigit() else "ul",
                                  m.group(3)))
                elif items:
                    # 縮排續行:併入前一個項目(保留換行)
                    prev = items[-1]
                    items[-1] = (prev[0], prev[1], prev[2] + "<br>" + lines[i].strip())
                else:
                    break
                i += 1
            out.append(_render_list(items))
            continue

        # ── 段落:連續非空行合為一段,段內換行保留(metadata 短行群靠這個)
        buf = []
        while i < n and lines[i].strip() and not _is_block_start(lines[i], lines[i + 1] if i + 1 < n else ""):
            buf.append(render_inline(lines[i].strip()))
            i += 1
        if buf:
            out.append("<p>" + "<br>".join(buf) + "</p>")
        else:
            i += 1  # 防呆:理論上到不了,避免任何情況下的死迴圈

    return "\n".join(out)


def _is_block_start(line: str, nxt: str) -> bool:
    """段落收集的煞車:遇到下一個區塊的起始行就停,讓主迴圈接手。"""
    ls = line.lstrip()
    if line.startswith(("#", "```", "<!--")) or re.match(r"^-{3,}\s*$", line):
        return True
    if ls.startswith(">") or re.match(r"^\s*([-*]|\d+\.)\s+", line):
        return True
    if ls.startswith("|") and re.match(r"^\s*\|[\s:\-|]+\|\s*$", nxt):
        return True
    return False


def _split_row(line: str) -> list:
    """把 | a | b | 形式的表格列切成欄;首尾空欄為語法殘留,予以剔除。"""
    cells = [c.strip() for c in line.strip().split("|")]
    if cells and cells[0] == "":
        cells = cells[1:]
    if cells and cells[-1] == "":
        cells = cells[:-1]
    return cells


def _render_list(items: list) -> str:
    """由 (層級, 標籤, 內文) 序列組出巢狀 ul/ol;勾選框以 ☐/☑ 呈現。"""
    out = []
    stack = []  # 元素: (level, tag)
    for lvl, tag, text in items:
        while stack and stack[-1][0] > lvl:
            out.append(f"</{stack.pop()[1]}>")
        if not stack or lvl > stack[-1][0]:
            stack.append((lvl, tag))
            out.append(f"<{tag}>")
        elif stack[-1][1] != tag:
            out.append(f"</{stack.pop()[1]}>")
            stack.append((lvl, tag))
            out.append(f"<{tag}>")
        text = re.sub(r"^\[ \]\s*", "☐ ", text)
        text = re.sub(r"^\[[xX]\]\s*", "☑ ", text)
        out.append(f"<li>{render_inline(text)}</li>")
    while stack:
        out.append(f"</{stack.pop()[1]}>")
    return "".join(out)


def render_file(md_path: Path) -> str:
    """整檔轉換:取首個 H1 為 <title>,包進 light 主題的完整 HTML 骨架。"""
    text = md_path.read_text(encoding="utf-8")
    lines = text.split("\n")
    title = md_path.stem
    for ln in lines:
        m = re.match(r"^#\s+(.*)$", ln)
        if m:
            title = re.sub(r"[*`]", "", m.group(1)).strip()
            break
    body = render_blocks(lines)
    return (
        "<!DOCTYPE html>\n"
        '<html lang="zh-TW">\n<head>\n<meta charset="utf-8">\n'
        '<meta name="viewport" content="width=device-width, initial-scale=1">\n'
        f"<title>{html.escape(title)}</title>\n"
        f"<style>\n{CSS}\n</style>\n</head>\n<body>\n{body}\n</body>\n</html>\n"
    )


def main(argv: list) -> int:
    """入口:預設重產登記表內全部文件;--check 只比對(給 CI 當同步閘門)。"""
    check = "--check" in argv
    args = [a for a in argv if a != "--check"]
    targets = [Path(a).resolve() for a in args] if args else TARGETS

    drifted = []
    for md in targets:
        if not md.exists():
            print(f"❌ {md} 不存在")
            drifted.append(md)
            continue
        out = md.with_suffix(".html")
        rendered = render_file(md)
        if check:
            if not out.exists() or out.read_text(encoding="utf-8") != rendered:
                print(f"❌ 漂移: {out.name}(重跑 python3 tools/render_docs.py)")
                drifted.append(out)
            else:
                print(f"✅ 同步: {out.name}")
        else:
            out.write_text(rendered, encoding="utf-8")
            print(f"✍ 已產生: {out}")
    return 1 if drifted else 0


if __name__ == "__main__":
    sys.exit(main(sys.argv[1:]))
