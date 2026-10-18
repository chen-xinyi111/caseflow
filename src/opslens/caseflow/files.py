"""Bounded office parsing and literal-text spreadsheet exports (no macros/formulas)."""
import csv
import io
import re
import zipfile
from pathlib import Path
from xml.etree import ElementTree as ET
from xml.sax.saxutils import escape
from opslens.rag.text import extract, MAX_BYTES

NS='http://schemas.openxmlformats.org/spreadsheetml/2006/main'
W='http://schemas.openxmlformats.org/wordprocessingml/2006/main'

def office(raw):
    if not raw or len(raw)>MAX_BYTES:raise ValueError('文件为空或超过5MiB')
    try:
        z=zipfile.ZipFile(io.BytesIO(raw))
        if len(z.infolist())>1000 or sum(i.file_size for i in z.infolist())>20*1024*1024:raise ValueError('Office文件展开后过大')
        if any('vbaProject' in i.filename for i in z.infolist()):raise ValueError('不支持含宏文件')
        return z
    except (zipfile.BadZipFile,RuntimeError):raise ValueError('Office文件损坏') from None

def xml(z,name):
    raw=z.read(name)
    if b'<!DOCTYPE' in raw or b'<!ENTITY' in raw:raise ValueError('文件包含不支持的XML声明')
    return ET.fromstring(raw)

def parse(name,raw):
    if Path(name).suffix.lower()!='.docx':return extract(name,raw).pages
    try:
        with office(raw) as z:
            root=xml(z,'word/document.xml')
            # Retain visible w:t only, not hidden field instructions.
            lines=[''.join(t.text or '' for t in p.findall('.//{'+W+'}t')) for p in root.findall('.//{'+W+'}p')]
            text='\n'.join(lines)
            if not text.strip() or len(text)>100000:raise ValueError('Word没有可提取正文或超过100000字符')
            return [text]
    except (KeyError,ET.ParseError):raise ValueError('无法读取Word正文') from None

def col(n):
    value=''
    while n:n,r=divmod(n-1,26);value=chr(65+r)+value
    return value

def xlsx(headers,rows):
    stream=io.BytesIO()
    values=[headers,*rows]
    body=[]
    for n,row in enumerate(values,1):
        cells=[]
        for k,value in enumerate(row,1):
            text=re.sub(r'[\x00-\x08\x0b\x0c\x0e-\x1f]','',str(value if value is not None else ''))[:32767]
            cells.append(f'<c r="{col(k)}{n}" t="inlineStr" s="{1 if n==1 else 0}"><is><t xml:space="preserve">{escape(text)}</t></is></c>')
        body.append(f'<row r="{n}" ht="{30 if n==1 else 66}" customHeight="1">'+''.join(cells)+'</row>')
    sheet=f'<worksheet xmlns="{NS}"><sheetViews><sheetView workbookViewId="0"><pane ySplit="1" topLeftCell="A2" activePane="bottomLeft" state="frozen"/></sheetView></sheetViews><cols>'+''.join(f'<col min="{i}" max="{i}" width="{18 if i<4 else 42}" customWidth="1"/>' for i in range(1,len(headers)+1))+'</cols><sheetData>'+''.join(body)+f'</sheetData><autoFilter ref="A1:{col(len(headers))}{len(values)}"/></worksheet>'
    with zipfile.ZipFile(stream,'w',zipfile.ZIP_DEFLATED) as z:
        z.writestr('[Content_Types].xml','<Types xmlns="http://schemas.openxmlformats.org/package/2006/content-types"><Default Extension="rels" ContentType="application/vnd.openxmlformats-package.relationships+xml"/><Default Extension="xml" ContentType="application/xml"/><Override PartName="/xl/workbook.xml" ContentType="application/vnd.openxmlformats-officedocument.spreadsheetml.sheet.main+xml"/><Override PartName="/xl/worksheets/sheet1.xml" ContentType="application/vnd.openxmlformats-officedocument.spreadsheetml.worksheet+xml"/><Override PartName="/xl/styles.xml" ContentType="application/vnd.openxmlformats-officedocument.spreadsheetml.styles+xml"/></Types>')
        z.writestr('_rels/.rels','<Relationships xmlns="http://schemas.openxmlformats.org/package/2006/relationships"><Relationship Id="rId1" Type="http://schemas.openxmlformats.org/officeDocument/2006/relationships/officeDocument" Target="xl/workbook.xml"/></Relationships>')
        z.writestr('xl/workbook.xml',f'<workbook xmlns="{NS}" xmlns:r="http://schemas.openxmlformats.org/officeDocument/2006/relationships"><sheets><sheet name="测试用例" sheetId="1" r:id="rId1"/></sheets></workbook>')
        z.writestr('xl/_rels/workbook.xml.rels','<Relationships xmlns="http://schemas.openxmlformats.org/package/2006/relationships"><Relationship Id="rId1" Type="http://schemas.openxmlformats.org/officeDocument/2006/relationships/worksheet" Target="worksheets/sheet1.xml"/><Relationship Id="rId2" Type="http://schemas.openxmlformats.org/officeDocument/2006/relationships/styles" Target="styles.xml"/></Relationships>')
        z.writestr('xl/styles.xml',f'<styleSheet xmlns="{NS}"><fonts count="2"><font><sz val="11"/><name val="Microsoft YaHei"/></font><font><b/><color rgb="FFFFFFFF"/><sz val="11"/><name val="Microsoft YaHei"/></font></fonts><fills count="3"><fill><patternFill patternType="none"/></fill><fill><patternFill patternType="gray125"/></fill><fill><patternFill patternType="solid"><fgColor rgb="FF185C52"/><bgColor indexed="64"/></patternFill></fill></fills><borders count="1"><border/></borders><cellStyleXfs count="1"><xf/></cellStyleXfs><cellXfs count="2"><xf fontId="0" fillId="0" borderId="0" xfId="0" applyAlignment="1"><alignment vertical="top" wrapText="1"/></xf><xf fontId="1" fillId="2" borderId="0" xfId="0" applyAlignment="1"><alignment vertical="center" wrapText="1"/></xf></cellXfs><cellStyles count="1"><cellStyle name="Normal" xfId="0" builtinId="0"/></cellStyles></styleSheet>')
        z.writestr('xl/worksheets/sheet1.xml',sheet)
    return stream.getvalue()

RESULT_HEADERS=['用例编号','用例修订','执行结果','备注']
def result_rows(name,raw):
    if len(raw)>MAX_BYTES:raise ValueError('导入文件超过5MiB')
    if Path(name).suffix.lower()=='.csv':
        try:rows=list(csv.reader(io.StringIO(raw.decode('utf-8-sig'))))
        except UnicodeError:raise ValueError('CSV需要UTF-8编码') from None
    elif Path(name).suffix.lower()=='.xlsx':
        with office(raw) as z:
            shared=[]
            if 'xl/sharedStrings.xml' in z.namelist():
                shared=[''.join(e.itertext()) for e in xml(z,'xl/sharedStrings.xml').findall('{'+NS+'}si')]
            rows=[]
            for r in xml(z,'xl/worksheets/sheet1.xml').findall('.//{'+NS+'}row'):
                values={}
                for c in r.findall('{'+NS+'}c'):
                    if c.find('{'+NS+'}f') is not None:raise ValueError('执行结果文件不能包含公式')
                    address=c.get('r','A1');letters=re.match('[A-Z]+',address).group();index=0
                    for ch in letters:index=index*26+ord(ch)-64
                    if index>4:continue
                    value=c.findtext('{'+NS+'}v','')
                    if c.get('t')=='s':value=shared[int(value)]
                    elif c.get('t')=='inlineStr':value=''.join(c.find('{'+NS+'}is').itertext())
                    values[index-1]=value
                rows.append([values.get(i,'') for i in range(4)])
    else:raise ValueError('仅支持Excel或UTF-8 CSV执行结果')
    if not rows or rows[0]!=RESULT_HEADERS:raise ValueError('请使用执行结果模板，保留四列标题')
    if len(rows)>2001:raise ValueError('一次最多导入2000条结果')
    output=[]
    for row in rows[1:]:
        if not any(row):continue
        if len(row)!=4:raise ValueError('执行结果必须包含四列')
        cid,revision,status,note=row
        if status not in ('通过','失败','阻塞','未执行'):raise ValueError('执行结果需为通过、失败、阻塞或未执行')
        try:revision=int(revision)
        except ValueError:raise ValueError('用例修订必须是整数') from None
        output.append({'case_id':cid,'revision':revision,'status':status,'note':note[:2000]})
    if not output:raise ValueError('没有执行结果')
    if len({r['case_id'] for r in output})!=len(output):raise ValueError('同一批次不能重复填写同一用例')
    return output
