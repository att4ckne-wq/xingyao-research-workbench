"""Academic metadata, bounded local document exports, and editable diagrams."""
from __future__ import annotations

import asyncio
import hashlib
import html
import json
import os
import re
import threading
import uuid
from difflib import SequenceMatcher
from pathlib import Path
from urllib.parse import quote, urlparse

import fitz
import httpx
from docx import Document
from docx.shared import Pt
from docx.oxml.ns import qn

from .services import clean_source_title


class WorkingDraftConflict(ValueError):
    """A new immutable revision already exists; never replace it."""


_working_draft_locks: dict[str, threading.RLock] = {}
_working_draft_locks_guard = threading.Lock()


def _working_draft_lock(root: Path):
    with _working_draft_locks_guard:
        return _working_draft_locks.setdefault(str(root.resolve()), threading.RLock())


def load_working_draft(root: Path) -> dict:
    """Read the latest completely published revision without creating files."""
    with _working_draft_lock(root):
        directory = root / 'drafts' / 'working-draft'
        if not directory.exists():
            return {'content': '', 'version': 0}
        versions = sorted((p for p in directory.iterdir() if re.fullmatch(r'v[0-9]{12}\.json', p.name)), reverse=True)
        if not versions:
            return {'content': '', 'version': 0}
        value = json.loads(versions[0].read_text(encoding='utf-8'))
        version = int(versions[0].stem[1:])
        if (not isinstance(value, dict) or value.get('version') != version or
                not isinstance(value.get('content'), str) or
                value.get('sha256') != hashlib.sha256(value['content'].encode('utf-8')).hexdigest()):
            raise ValueError('工作稿版本损坏，原文件已保留；请检查项目备份。')
        return {'content': value['content'], 'version': version}


def save_working_draft(root: Path, content: str, expected_version: int) -> dict:
    """CAS-save by atomic no-overwrite hard-link publication; retain every file.

    Staging copies also stay available after publication or conflict. No draft,
    prior revision, export, or staging copy is removed or overwritten.
    """
    if not isinstance(content, str) or len(content) > 200000:
        raise ValueError('工作稿最多支持200000个字符。')
    if type(expected_version) is not int or expected_version < 0:
        raise ValueError('工作稿版本必须为非负整数。')
    with _working_draft_lock(root):
        current = load_working_draft(root)
        if expected_version != current['version']:
            raise WorkingDraftConflict('工作稿已有新版本，当前编辑已保留；请先下载当前稿或核对最新已存稿。')
        if content == current['content']:
            return current
        version = current['version'] + 1
        directory = root / 'drafts' / 'working-draft'
        staging = directory / '.staging'
        staging.mkdir(parents=True, exist_ok=True)
        value = {'content': content, 'version': version,
                 'sha256': hashlib.sha256(content.encode('utf-8')).hexdigest()}
        pending = staging / (uuid.uuid4().hex + '.json')
        with pending.open('x', encoding='utf-8') as stream:
            json.dump(value, stream, ensure_ascii=False)
            stream.flush()
            os.fsync(stream.fileno())
        try:
            os.link(pending, directory / f'v{version:012d}.json')
        except FileExistsError as exc:
            raise WorkingDraftConflict('其他窗口已保存新版本，当前编辑已保留，请核对后再保存。') from exc
        return {'content': content, 'version': version}


def bibliography(item: dict) -> dict:
    """Use the existing durable rationale field without changing table shape."""
    try:
        data = json.loads(item.get("rationale") or "")
        return data.get("metadata", {}) if data.get("kind") == "literature" else {}
    except (ValueError, AttributeError, TypeError):
        return {}


def pack_bibliography(metadata: dict, summary: str = "文献元数据；引用不声明图片复用权利") -> str:
    metadata = {key: value for key, value in metadata.items() if key in {"doi", "year", "journal", "verification_status"}}
    return json.dumps({"kind": "literature", "summary": summary, "metadata": metadata}, ensure_ascii=False)


def serialize_bibliography(item: dict) -> dict:
    metadata = bibliography(item)
    if metadata:
        item.update(metadata)
        item["rationale"] = "文献来源；可核对题名、DOI 与出版信息后引用"
    return item


def crossref_record(item: dict) -> dict:
    dates = item.get("published", item.get("issued", {})).get("date-parts", [[]])
    year = dates[0][0] if dates and dates[0] else None
    authors = [" ".join(filter(None, (person.get("given"), person.get("family")))) for person in item.get("author", [])[:12]]
    doi = item.get("DOI")
    return {"title": clean_source_title((item.get("title") or [""])[0]),
            "author": "; ".join(filter(None, authors)), "doi": doi,
            "source_url": f"https://doi.org/{doi}" if doi else item.get("URL", ""),
            "year": year, "journal": clean_source_title((item.get("container-title") or [""])[0]),
            "provider": "Crossref", "verification_status": "metadata_found"}


async def search_literature(query: str, limit: int = 12) -> tuple[list[dict], dict]:
    async with httpx.AsyncClient(timeout=16, headers={"User-Agent": "NatureWorkbench/0.1 (local academic research)"}) as client:
        async def crossref():
            response = await client.get("https://api.crossref.org/works", params={"query.bibliographic": query, "rows": min(limit, 8)})
            response.raise_for_status()
            return [crossref_record(item) for item in response.json().get("message", {}).get("items", [])]

        async def europe_pmc():
            response = await client.get("https://www.ebi.ac.uk/europepmc/webservices/rest/search", params={"query": query, "format": "json", "pageSize": min(limit, 6)})
            response.raise_for_status()
            return [{"title": clean_source_title(item.get("title", "")), "author": item.get("authorString"),
                     "doi": item.get("doi"), "year": int(item["pubYear"]) if str(item.get("pubYear", "")).isdigit() else None,
                     "journal": item.get("journalTitle"), "provider": "Europe PMC",
                     "source_url": f"https://europepmc.org/article/{item.get('source', 'MED')}/{item.get('id', '')}",
                     "verification_status": "metadata_found"}
                    for item in response.json().get("resultList", {}).get("result", [])]

        providers = ("Crossref", "Europe PMC")
        results = await asyncio.gather(crossref(), europe_pmc(), return_exceptions=True)
    merged = []; seen = set()
    for payload in results:
        if isinstance(payload, Exception): continue
        for item in payload:
            identity = (item.get("doi") or item.get("source_url") or item["title"]).lower()
            if not item["title"] or not item.get("source_url") or identity in seen: continue
            seen.add(identity); merged.append(item)
    return merged[:limit], {"successful_providers": [name for name, data in zip(providers, results) if not isinstance(data, Exception)],
                            "failed_providers": [name for name, data in zip(providers, results) if isinstance(data, Exception)]}


def normalize_title(value: str) -> str:
    return re.sub(r"[^\w\u4e00-\u9fff]+", "", clean_source_title(value).casefold())


async def verify_reference(title: str, doi: str | None = None) -> dict:
    if doi:
        doi = re.sub(r"^https?://(?:dx\.)?doi\.org/", "", doi.strip(), flags=re.I)
        if not re.fullmatch(r"10\.\d{4,9}/\S+", doi):
            return {"status": "not_found", "message": "DOI 格式不正确，请核对原文。"}
    try:
        async with httpx.AsyncClient(timeout=16, headers={"User-Agent": "NatureWorkbench/0.1"}) as client:
            response = await client.get(f"https://api.crossref.org/works/{quote(doi, safe='')}" if doi else "https://api.crossref.org/works",
                                        params=None if doi else {"query.title": title, "rows": 3})
            if response.status_code == 404:
                return {"status": "not_found", "message": "Crossref 未找到该 DOI；这不能单独证明文献不存在。"}
            response.raise_for_status(); message = response.json()["message"]
            items = [message] if doi else message.get("items", [])
        if not items:
            return {"status": "not_found", "message": "未找到可核对记录；可在知网、百度学术或期刊官网补查。"}
        record = max((crossref_record(item) for item in items), key=lambda item: SequenceMatcher(None, normalize_title(title), normalize_title(item["title"])).ratio())
        similarity = SequenceMatcher(None, normalize_title(title), normalize_title(record["title"])).ratio()
        status = "matched" if similarity >= .95 else "possible_match" if similarity >= .72 else "mismatch"
        return {"status": status, "message": {"matched": "登记题名与输入一致；请继续核对作者、年份和正文支持关系。", "possible_match": "找到近似题名，需人工核对完整条目。", "mismatch": "登记题名与输入不同；可能为译名或误引，请打开记录核对。"}[status], "record": record}
    except (httpx.HTTPError, ValueError, KeyError, TypeError):
        return {"status": "unavailable", "message": "元数据服务暂不可用，尚未完成核对。"}


TASK_RULES = {
    "topic": "帮助科研新手把选题缩小成可回答的问题，分别说明研究对象、变量、可获得数据和可行性；缺失信息先提出少量具体问题。",
    "outline": "根据已有选题输出开题框架：问题、背景证据、研究目标、假设、方法、所需数据、可行性、风险和时间安排。无来源的背景标为待查证，不能编造参考文献或实验结果。",
    "writing": "依据材料起草或续写学术段落，区分已完成结果与计划，不能虚构样本量、数值、方法细节和参考文献。",
    "review": "提出可执行的修改建议：论证、结构、证据、统计和表达。给出对应原句/段落位置，保留学者最终判断，不直接覆盖正文。",
    "reading": "精读原文：研究问题、方法、数据、结论、局限与可迁移之处；解释术语并标注段落ID或页码，区分原文和推断。只读取已提供片段，不声称通读未提供全文或看懂未发送图片。",
    "translation": "逐段准确翻译选中中英文片段，保留数值、公式、引用与限定；先原文后译文，再按需解释术语，不缩写成只有摘要。",
    "diagram": "依据已有研究内容给出可编辑 Mermaid flowchart TD 或 LR，使用简单节点与箭头；不要编造已验证的技术关系。代码放在mermaid围栏中，并说明图中关系依据。",
}

HUMANIZER_RULES = "中文按 humanizer-zh 原则润色：保留独立信息、否定、范围、条件、时间与证据强度；减少空话和模板句；不补造事实、经历、数字或引用；保留学术文体与术语。"


def local_academic_reply(task: str, context: dict) -> str:
    topic = context.get("topic") or context["project"]
    selected = context.get("selection") or (context.get("anchor") or {}).get("text") or ""
    prefix = "本地研究模板（未调用在线模型）\n\n"
    if task in {"topic", "outline"}:
        return prefix + f"# {topic}：开题工作稿\n\n## 1. 研究问题\n用一句话限定研究对象、条件与待解释现象。\n\n## 2. 文献与证据\n先检索近年综述和关键原始研究；分别记录已有结论、争议与不足，逐条核对 DOI 和原文。\n\n## 3. 研究目标与假设\n列出可检验的问题，以及什么结果会支持或否定假设。\n\n## 4. 方法与数据\n写明对照、变量、测量方式、独立样本单位和统计方法；未知参数留待作者补充。\n\n## 5. 可行性与计划\n盘点仪器、材料、时间和安全要求，再划分文献、预实验、正式实验与写作阶段。\n\n## 下一步\n请补充：具体研究对象、当前条件、已有材料和希望回答的问题。此模板未声称已有研究结论。"
    if task == "diagram":
        return prefix + "```mermaid\nflowchart TD\nA[明确研究问题] --> B[检索并核对文献]\nB --> C[设计方法与对照]\nC --> D[采集与记录数据]\nD --> E[分析结果与局限]\nE --> F[撰写和复核]\n```\n这是一份研究流程模板，请按实际方案修改。"
    if task == "translation":
        return prefix + "翻译需使用已配置的在线模型并确认发送预览。本地模式保留原文，不伪造译文。\n\n" + selected
    if task == "reading":
        return prefix + f"已提供片段 {len(selected)} 字符。可按下面顺序做精读笔记：\n1. 作者要回答什么问题？\n2. 使用什么方法和对照？\n3. 哪条数据支持主要结论？\n4. 结论适用的条件与局限是什么？\n5. 与你的课题有什么联系？\n\n可选择具体段落后，在发送预览中确认使用在线助手解释或翻译。"
    return prefix + "请逐段检查：中心论点是否明确、证据是否可定位、方法能否复现、数字与统计是否对应、引用是否真正支持主张。\n\n本地模式提供检查清单，不冒充模型完成正文润色。可确认预览后让在线助手针对所选片段起草、续写或逐句修改。"


def draft_export(root: Path, title: str, content: str, format: str) -> Path:
    directory = root / "exports" / ("draft-" + uuid.uuid4().hex); directory.mkdir(parents=True, exist_ok=False)
    path = directory / f"draft.{format}"
    if format == "md":
        path.write_text(f"# {title}\n\n{content}\n", encoding="utf-8")
    elif format == "docx":
        doc = Document()
        for style_name in ['Normal','Title',*[f'Heading {i}' for i in range(1,5)]]:
            style=doc.styles[style_name];style.font.name='Times New Roman'
            fonts=style.element.get_or_add_rPr().get_or_add_rFonts()
            for slot,name in [('ascii','Times New Roman'),('hAnsi','Times New Roman'),('eastAsia','SimSun')]:fonts.set(qn('w:'+slot),name)
        doc.styles['Normal'].font.size = Pt(11)
        doc.add_heading(title, 0)
        for line in content.splitlines():
            match = re.match(r"^(#{1,6})\s+(.+)", line)
            if match: doc.add_heading(match[2], min(len(match[1]), 4))
            else: doc.add_paragraph(line)
        doc.save(path)
    elif format == "pdf":
        font_root=Path(os.environ.get('WINDIR',r'C:\Windows'))/'Fonts'
        fonts={'DocLatin':font_root/'times.ttf','DocLatinBold':font_root/'timesbd.ttf','DocChinese':font_root/'simsun.ttc'}
        if not all(path.is_file() for path in fonts.values()):raise ValueError("PDF导出需要本机Times New Roman与宋体；未安装或替换字体，请先核对环境")
        document = fitz.open()
        def new_page():
            page=document.new_page()
            for name,path in fonts.items():page.insert_font(fontname=name,fontfile=str(path))
            return page
        page=new_page(); y = 54
        cjk = fitz.Font(fontfile=str(fonts['DocChinese'])); latin = fitz.Font(fontfile=str(fonts['DocLatin'])); bold = fitz.Font(fontfile=str(fonts['DocLatinBold']))
        def draw_line(line: str, size: float, heading: bool):
            nonlocal page, y
            if y > 785: page = new_page(); y = 54
            x = 54
            for run in re.findall(r"[\x00-\x7f]+|[^\x00-\x7f]+", line):
                ascii_run = run.isascii(); name = ("DocLatinBold" if heading else "DocLatin") if ascii_run else "DocChinese"
                page.insert_text((x, y), run, fontname=name, fontsize=size)
                x += ((bold if heading else latin) if ascii_run else cjk).text_length(run, fontsize=size)
            y += size * 1.5
        for index, original in enumerate([title, "", *content.splitlines()]):
            match = re.match(r"^(#{1,6})\s+", original); heading = index == 0 or bool(match)
            size = 18 if index == 0 else 14 if match else 11
            original = re.sub(r"^#{1,6}\s+", "", original); line = ""; width = 0
            if heading and index: y += 8
            for char in original:
                char_width = ((bold if heading else latin) if char.isascii() else cjk).text_length(char, fontsize=size)
                if line and width + char_width > 485:
                    draw_line(line, size, heading); line = ""; width = 0
                line += char; width += char_width
            draw_line(line, size, heading)
        for index, item in enumerate(document, 1): item.insert_text((285, 818), str(index), fontname="DocLatin", fontsize=9, color=(.4,.4,.4))
        document.subset_fonts()
        document.save(path); document.close()
    else: raise ValueError("Unsupported draft format")
    return path


def render_mermaid(root: Path, title: str, source: str) -> tuple[Path, Path]:
    source = re.sub(r"^```(?:mermaid)?\s*|\s*```$", "", source.strip(), flags=re.I)
    lines = [part.strip() for part in re.split(r"[\n;]", source) if part.strip()]
    if not lines or not re.fullmatch(r"(?:flowchart|graph)\s+(?:TD|TB|LR)", lines[0]):
        raise ValueError("本地预览支持 Mermaid flowchart TD/TB/LR；其他语法请保留源码在 Mermaid 编辑器渲染。")
    nodes = {}; edges = []
    token = r'([A-Za-z][A-Za-z0-9_]{0,39})(?:\[([^\]\n]{1,80})\])?'
    for line in lines[1:]:
        if line.startswith('%%'): continue
        edge = re.fullmatch(token + r'\s*-->\s*' + token, line)
        node = re.fullmatch(token, line)
        if edge:
            a, label_a, b, label_b = edge.groups(); nodes[a] = label_a or nodes.get(a, a); nodes[b] = label_b or nodes.get(b, b); edges.append((a, b))
        elif node: nodes[node[1]] = node[2] or node[1]
        else: raise ValueError("图示预览支持 A[标题] --> B[标题] 的单条连线；源码保留为可编辑 Mermaid。")
    if not nodes or len(nodes) > 24: raise ValueError("每张图支持1—24个节点，请拆分过大的图示。")
    horizontal = lines[0].endswith('LR'); width = max(760, len(nodes)*200) if horizontal else 760; height = 200 if horizontal else 100+len(nodes)*105
    positions = {key: ((50+index*200,80) if horizontal else (260,55+index*105)) for index,key in enumerate(nodes)}
    svg = [f'<svg xmlns="http://www.w3.org/2000/svg" width="{width}" height="{height}" viewBox="0 0 {width} {height}"><rect width="100%" height="100%" fill="#faf9f5"/><defs><marker id="arrow" markerWidth="10" markerHeight="10" refX="8" refY="3" orient="auto"><path d="M0,0 L0,6 L8,3 z" fill="#315b50"/></marker></defs>', f'<title>{html.escape(title)}</title>']
    for a,b in edges:
        x1,y1=positions[a];x2,y2=positions[b]
        if horizontal:x1+=160;y1+=25;y2+=25
        else:x1+=80;x2+=80;y1+=50
        svg.append(f'<path d="M{x1},{y1} L{x2},{y2}" stroke="#315b50" fill="none" stroke-width="2" marker-end="url(#arrow)"/>')
    for key,label in nodes.items():
        x,y=positions[key];svg.append(f'<rect x="{x}" y="{y}" width="160" height="50" rx="10" fill="#e4eee6" stroke="#315b50"/><text x="{x+80}" y="{y+30}" text-anchor="middle" font-family="Microsoft YaHei,sans-serif" font-size="13">{html.escape(label.strip(chr(34)))}</text>')
    svg.append('</svg>')
    directory=root/'charts'/('diagram-'+uuid.uuid4().hex);directory.mkdir(parents=True,exist_ok=False)
    preview=directory/'diagram.svg';preview.write_text(''.join(svg),encoding='utf-8');code=directory/'diagram.mmd';code.write_text(source,encoding='utf-8')
    return preview,code
