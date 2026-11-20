#!/usr/bin/env python3
"""Local-only MOSS-Image layout editor backend. Python stdlib only."""
from http.server import ThreadingHTTPServer, SimpleHTTPRequestHandler
from pathlib import Path
from urllib.request import Request, urlopen
from urllib.error import URLError, HTTPError
import json, math, re, argparse, os, webbrowser

ROOT=Path(__file__).resolve().parent
MAX_BYTES=250_000

def validate_and_compile(d, allow_empty=False):
    if not isinstance(d,dict): raise ValueError('Layout must be an object')
    expected={'width','height','bbox_coord_space','bbox_format','summary','items'}
    if set(d)!=expected: raise ValueError('Layout fields must be: '+', '.join(sorted(expected)))
    w,h=d['width'],d['height']
    if any(type(v)!=int or v<16 or v%16 for v in (w,h)): raise ValueError('Width and height must be positive multiples of 16')
    if w>4096 or h>4096: raise ValueError('Maximum canvas is 4096 pixels per side')
    if d['bbox_coord_space']!='abs' or d['bbox_format']!='xyxy': raise ValueError('Draft must use abs / xyxy')
    s=d['summary']; assert_fields(s,{'caption','meta','palette','canvas'}, {'caption'},'summary')
    nonempty(s['caption'],'summary.caption')
    if 'canvas' in s and s['canvas'] not in ('single','multi_panel','collage'):raise ValueError('Invalid summary.canvas')
    for k in ('meta','palette'):
        if k in s: strings(s[k], 'summary.'+k)
    items=d['items']
    if not isinstance(items,list): raise ValueError('items must be a list')
    if not items and not allow_empty: raise ValueError('配置要素がありません。枠を追加してから変換してください')
    if len(items)>80: raise ValueError('Maximum 80 items')
    converted=[]
    for n,item in enumerate(items):
        tag=f'items[{n}]'
        assert_fields(item, {'label','caption','bbox','text','palette','tags'}, {'label','caption','bbox'},tag)
        if item['label'] not in ('text','object','character'): raise ValueError(tag+' invalid label')
        nonempty(item['caption'],tag+'.caption')
        if item['label']=='text' and 'text' not in item: raise ValueError(tag+' text is required')
        if 'text' in item: nonempty(item['text'],tag+'.text')
        for k in ('tags','palette'):
            if k in item: strings(item[k],tag+'.'+k)
        b=item['bbox']
        if not isinstance(b,list) or len(b)!=4 or any(type(v) not in (int,float) or not math.isfinite(v) for v in b): raise ValueError(tag+' bbox invalid')
        x1,y1,x2,y2=b
        if not (0<=x1<x2<=w and 0<=y1<y2<=h): raise ValueError(tag+' bbox out of bounds')
        c={**item,'bbox':[math.floor(round(y1,9))-h//2, math.floor(round(x1,9))-w//2,math.ceil(round(y2,9))-h//2,math.ceil(round(x2,9))-w//2]}
        converted.append(c)
    return {'summary':s,'items':converted,'bbox_coord_space':'abs_centralized','bbox_format':'yxyx'}

def assert_fields(o, allowed, required, where):
    if not isinstance(o,dict) or set(o)-allowed or required-set(o): raise ValueError(where+' has invalid/missing fields')
def nonempty(v,where):
    if not isinstance(v,str) or not v.strip(): raise ValueError(where+' must be nonempty')
def strings(v,where):
    if not isinstance(v,list) or any(not isinstance(x,str) or not x.strip() for x in v):raise ValueError(where+' must be a list of strings')

def retrieve_models():
    req=Request('http://127.0.0.1:11434/api/tags')
    with urlopen(req,timeout=3) as r: return [m['name'] for m in json.load(r).get('models',[])]

AI_SYSTEM='''You are a professional visual-layout planner for MOSS-Image. Create a clean, accurate, editable layout draft for ANY kind of image (poster, advertisement, illustration, product visual, cover, etc.), following the user's Japanese request. Respond with ONLY one valid JSON object in this precise shape:
{"width":1024,"height":1024,"bbox_coord_space":"abs","bbox_format":"xyxy","summary":{"caption":"concise natural English description of overall atmosphere, background, lighting, palette and artistic style only","canvas":"single"},"items":[{"label":"text","text":"日本語タイトル","caption":"Readable Japanese typography displaying exactly \"日本語タイトル\". Large elegant Mincho-style lettering in deep navy blue, positioned in the designated area.","bbox":[70,50,950,180]},{"label":"character","caption":"One Japanese woman wearing a winter coat, naturally standing with a gentle smile, realistic fashion photography.","bbox":[570,220,950,920]}]}
REQUIREMENTS:
- Use integer pixel bounding boxes [x1,y1,x2,y2] in upper-left-origin canvas coordinates. 0 <= x1 < x2 <= width and 0 <= y1 < y2 <= height. Use the EXACT dimensions requested by the user, divisible by 16. Respect intended top/left/right/bottom placement. Reserve enough space for all text.
- Only create elements directly supported by the user's instructions. Typically 2-5 elements for a simple image. Do NOT force 4-9 items, invent promotional copy, create duplicate text, or add decorative text. A decorative background is normally described in summary.caption, not a text item.
- One separate text item for each explicitly requested visible text phrase. Preserve the original Japanese text EXACTLY (including punctuation and line breaks when specified). Never invent or paraphrase visible text.
- A text box should be broad and horizontal for a headline, and sufficiently tall for readable text. For a Japanese headline of 10+ characters, prefer a box at least 55% of canvas width; for a shorter slogan, at least 30%. Do not make tall narrow vertical boxes for horizontal text. Keep titles away from character faces.
- A phrase requested on the LEFT (including bottom-left) MUST remain in the LEFT part of the canvas, typically x=5%-46% of the canvas; do NOT create a full-width bottom banner for a left-side slogan. Give such copy approximately 18%-26% of canvas height. For text at the top, use a broad banner near the top (y=5%-22%).
- For a standing person requested on the RIGHT, place a tall character bounding box on the right (roughly x=54%-95%, y=23%-91%), with enough height for the requested body crop. Do not place the character inside a horizontal strip. If the user requests a different composition, obey their request instead.
- When a caption says bottom-left, left-side or lower-left, the bbox coordinates must actually be there. When it says standing or knee-up, the character bbox needs enough height. Check those constraints mentally before returning JSON.
- Characters (human beings, animals and mascots) MUST use label=character. Inanimate items, products, architecture and objects use label=object. All visible written words use label=text.
- summary.caption must be concise ENGLISH describing only the overall background, palette, lighting, and visual aesthetic. Do NOT repeat any exact Japanese words or invent extra advertisement text in the summary.
- Every item's caption must be written in concise natural ENGLISH; for text items only, the exact Japanese display text inside the quotation marks is permitted. For text captions: write exactly one 'Readable Japanese typography displaying exactly "<text>".' instruction, followed by 1-2 short English sentences specifying typography, size, color, and alignment. Never repeat that text elsewhere in the caption. Avoid detailed pixel values, additional copy, or generic layout prose.
- For character and object captions: use short, visually meaningful English details about physical appearance, pose, costume, texture, and light, without written copy. A requested standing person must be standing, not seated. Do not introduce additional people.
- Maintain visual balance and avoid substantial overlapping of text with a character or object, unless the user explicitly asks for it. Respect margins and intended order. No extra keys. No markdown.'''

# Only AI-generated drafts are postprocessed. Manual edits and imported drafts remain unchanged.
PEOPLE_PATTERNS = re.compile(r"(?:\b(?:woman|women|man|men|girl|boy|person|people|human|model|portrait|child|children|lady|female|male|character|mascot|cat|dog|animal)\b|女性|男性|人物|女の子|男の子|少女|少年|猫|犬|動物|キャラクター)", re.IGNORECASE)

def improve_ai_layout_draft(draft):
    """Conservative cleanup: subject labels, duplicate title text in summary.

    Never reinterpret coordinates or rewrite user-authored drafts.
    """
    for item in draft['items']:
        if item['label'] == 'object' and PEOPLE_PATTERNS.search(item['caption']):
            item['label'] = 'character'
    # Avoid requesting the same visible text in both summary and text item captions.
    caption = draft['summary']['caption']
    for item in draft['items']:
        if item['label'] == 'text':
            visible = item['text']
            if visible and visible in caption:
                caption = caption.replace(visible, 'the specified headline or slogan')
    draft['summary']['caption'] = caption
    return draft

def refine_ai_bbox_geometry(draft):
    """Conservatively correct obvious Ollama geometry mistakes, AI drafts only.

    Only fixes thin full-width strips that contradict an explicit *left-side*
    text caption. This deliberately does not resize arbitrary user-defined
    captions, or guess crop/framing for character boxes.
    """
    w, h = draft['width'], draft['height']
    for item in draft['items']:
        if item['label'] != 'text':
            continue
        desc = item['caption'].lower()
        left = bool(re.search(r'\b(?:left[- ]side|left[- ]bottom|bottom[- ]left|lower[- ]left|left corner|left portion)\b', desc))
        # Title cases that are deliberately full-width remain untouched.
        if not left:
            continue
        x1, y1, x2, y2 = item['bbox']
        bw, bh = x2-x1, y2-y1
        if bw < w*.63 or bh > h*.13:
            continue
        # Large horizontal footer despite explicit instruction for a left-aligned
        # text region. Preserve the bottom/upper intent but restrict to left.
        margin = max(16, round(w*.055))
        new_x1 = min(x1, margin) if x1 <= w*.16 else margin
        new_x1 = max(0, new_x1)
        new_x2 = min(w, round(w*.45))
        new_height = max(round(h*.17), bh)
        if y1 >= h*.58:
            new_y2 = min(h-round(h*.06), max(y2, round(h*.9)))
            new_y1 = max(0, new_y2-new_height)
        else:
            new_y1 = y1
            new_y2 = min(h, y1+new_height)
        if new_x1 < new_x2 and new_y1 < new_y2:
            item['bbox'] = [new_x1, new_y1, new_x2, new_y2]
    return draft

def ai_layout_warnings(draft):
    """Non-destructive checks reported to the user for manual fine-tuning."""
    warnings=[]
    w,h=draft['width'],draft['height']
    for i,item in enumerate(draft['items']):
        x1,y1,x2,y2=item['bbox']; bw=x2-x1; bh=y2-y1
        if item['label']=='text':
            n=len(item['text'].replace('\n',''))
            if n>=9 and bw < w*0.32:
                warnings.append(f'Text {i+1}: 横幅が狭い可能性があります')
            if bh < h*0.085:
                warnings.append(f'Text {i+1}: 高さが小さい可能性があります')
            if bw > w*.78 and bh < h*.16 and y1 > h*.65:
                warnings.append(f'Text {i+1}: 下部が横長の帯になっています。配置を確認してください')
        if item['label']=='character' and (item['bbox'][3]-item['bbox'][1]) < h*.3:
            warnings.append(f'Character {i+1}: 人物の表示領域が低い可能性があります')
    return warnings

# Japanese scripts outside the quoted visible text indicate an untranslated AI description.
JAPANESE_SCRIPT = re.compile(r'[\u3040-\u30ff\u3400-\u9fff]')
EXACT_PREFIX = 'Readable Japanese typography displaying exactly '

def text_exact_instruction(value):
    return EXACT_PREFIX + json.dumps(value, ensure_ascii=False) + '.'

def english_explanation(caption, visible_text=None):
    # The requested Japanese words are the only permitted Japanese in a text caption.
    if visible_text is not None:
        caption = caption.replace(json.dumps(visible_text, ensure_ascii=False), '')
        caption = caption.replace(visible_text, '')
    return not JAPANESE_SCRIPT.search(caption)

def normalize_ai_text_captions(draft):
    """Keep existing English style details, with one exact-text instruction.

    Run only for AI output; manual editing and imported drafts are untouched.
    """
    for item in draft['items']:
        if item['label'] != 'text':
            continue
        visible = item['text']
        exact = text_exact_instruction(visible)
        caption = item['caption'].strip()
        if caption == visible.strip() or caption == visible.replace('\n', ' ').strip():
            item['caption'] = exact
        elif caption.startswith(EXACT_PREFIX):
            # Existing convention: avoid duplicating the quoted Japanese text.
            item['caption'] = caption
        else:
            item['caption'] = exact + ' ' + caption
    return draft

def reset_generated_text_captions_to_manual_default(draft):
    """AI draft only: use exactly the same caption as a manually added Text Box.

    Keep visible text, layout boxes, tags and all non-text captions unchanged.
    Do not call this on imported, compiled, or manually edited drafts.
    """
    for item in draft.get('items', []):
        if item.get('label') == 'text':
            item['caption'] = text_exact_instruction(item['text'])
    return draft

def request_ollama(model, messages, *, temperature=0.3, num_predict=6000):
    data = {'model': model, 'stream': False, 'format': 'json',
            'options': {'temperature': temperature, 'num_predict': num_predict},
            'messages': messages}
    req = Request('http://127.0.0.1:11434/api/chat',
                  data=json.dumps(data).encode('utf-8'),
                  headers={'Content-Type': 'application/json'}, method='POST')
    with urlopen(req, timeout=240) as response:
        raw = json.load(response)
    return json.loads(raw.get('message', {}).get('content', ''))

def ensure_english_ai_descriptions(draft, model):
    """Repair only AI captions containing explanatory Japanese with a second Ollama call.

    If repair cannot be verified, surface an error instead of accepting mixed-language text.
    """
    normalize_ai_text_captions(draft)
    needs = [i for i, item in enumerate(draft['items'])
             if not english_explanation(item['caption'], item.get('text') if item['label']=='text' else None)]
    if not needs:
        return draft
    targets = [{'index': i, 'label': draft['items'][i]['label'],
                'visible_text': draft['items'][i].get('text', ''),
                'caption': draft['items'][i]['caption']} for i in needs]
    instruction = ("Rewrite only the captions in the supplied JSON into concise natural ENGLISH. "
                   "Translate every Japanese style, location, appearance, and design explanation into English. "
                   "For each label=text caption, use exactly the following pattern at the beginning: "
                   "Readable Japanese typography displaying exactly \"<visible_text>\". "
                   "Insert the supplied visible_text exactly, unchanged, inside the quotes; "
                   "this is the ONLY Japanese allowed in the caption. "
                   "Do not repeat visible_text elsewhere or introduce any other written copy. "
                   "Preserve all genuine clothing, color, pose, and style details when translating. "
                   "For object/character captions, use English ONLY. "
                   "Return JSON of the form {\"captions\":[{\"index\":0,\"caption\":\"...\"}]}; "
                   "include exactly one entry per supplied index. No commentary.")
    correction = request_ollama(model, [
        {'role': 'system', 'content': instruction},
        {'role': 'user', 'content': json.dumps({'items': targets}, ensure_ascii=False)}
    ], temperature=0, num_predict=3500)
    repairs = correction.get('captions') if isinstance(correction, dict) else None
    if not isinstance(repairs, list) or len(repairs) != len(needs):
        raise ValueError('AI Descriptionを英語へ変換できませんでした。もう一度生成してください。')
    by_index = {}
    for entry in repairs:
        if not isinstance(entry, dict) or type(entry.get('index')) is not int or not isinstance(entry.get('caption'), str):
            raise ValueError('AI Descriptionの英語変換形式が不正です。もう一度生成してください。')
        i = entry['index']
        if i in by_index or i not in needs or not entry['caption'].strip():
            raise ValueError('AI Descriptionの英語変換が不完全です。もう一度生成してください。')
        by_index[i] = entry['caption'].strip()
    for i in needs:
        item = draft['items'][i]
        caption = by_index[i]
        if item['label'] == 'text':
            visible = item['text']
            exact = text_exact_instruction(visible)
            if not caption.startswith(exact):
                raise ValueError('AIが日本語の表示文字列を維持できませんでした。再生成してください。')
            if not english_explanation(caption, visible):
                raise ValueError('Descriptionの説明文に日本語が残っています。再生成してください。')
        elif not english_explanation(caption):
            raise ValueError('Descriptionの英語変換が不完全です。再生成してください。')
        item['caption'] = caption
    return draft


# v13: Semantic planning is delegated to Ollama; pixel geometry is calculated locally.
# Unlike the former all-in-one prompt, the model is never asked to calculate coordinates.
SEMANTIC_SYSTEM = """You are an expert visual art director and Japanese-language layout brief interpreter.
Return ONLY a valid JSON object with these main keys (and requested_text_values if requested_text_slots is nonempty):
{"summary_caption":"Detailed ENGLISH description of ONE continuous scene-wide BACKGROUND, environment, lighting and palette; no new visible words", "text_styles":[{"id":0,"style":"Short ENGLISH typography details: hierarchy, color, font, emphasis, alignment; do not repeat the actual Japanese text"}], "subjects":[{"label":"character","placement":"right","caption":"Detailed ENGLISH description of one requested person including appearance, clothes, pose and expression only; no separate background or frame"}]}
RULES:
- The user-visible quoted strings are supplied separately as exact_text_blocks. Preserve them exactly; text_styles IDs reference these blocks by integer id.
- requested_text_slots lists additional VISIBLE text roles that the user expressly requested but did not supply literal wording for. Return a requested_text_values array of objects {"id":<slot id>,"text":"short finished Japanese display copy"}. Write actual content for every requested slot; NEVER a field label, explanation, or placeholder such as 未定/仮/編集可. Do not add unrequested roles. This applies to all genres, not only movie posters.
- summary_caption owns the SINGLE shared background and environment for the entire image. Preserve the Japanese brief's requested setting, colors, atmosphere and lighting in specific ENGLISH. Do not replace a shopping mall by a snowy wilderness. When unspecified, do not invent buildings, brands, logos, promotional words or additional people.
- Unless the user EXPLICITLY asks for a collage, multiple panels, individual cards, or different background regions: use ONE continuous background across all text and subject boxes, consistent light, perspective and palette, with NO rectangular partitions, split photo areas, independent panels, framed portraits or background tiles. Do not use separate panels as a default editorial layout.
- Each text_styles entry controls TYPOGRAPHY ONLY (font, emphasis, color, line hierarchy and alignment). Do NOT introduce background colors, colored plates, cards, translucent panels, frames or box-specific environments.
- Each subject caption describes ONLY the subject (clothes, pose, expression, physical appearance). The subject occupies the same common scene; never prescribe a separate interior, landscape, portrait-photo background, frame or rectangular plate. Describe all environmental features exclusively in summary_caption.
- For each text block create one text_styles entry. Describe typography only in English, without writing the quoted Japanese text again. A block can have multiple lines: describe font hierarchy, especially distinctive headline / subheading or a bold sale/discount line if present.
- Each explicitly numbered image asset (including a landscape/scenery image) MUST be a separate subject. Preserve the exact number and order of requested assets. Provide a short name property in Japanese for matching: name, label, placement, caption. subjects should contain only figures or physical objects explicitly requested outside the text blocks. People, animals and mascots are character; products and objects are object. Describe the person's clothes, pose and expression concretely using user instructions, but no independent background for the person. Do not create a subject for the background.
- placement can be top, upper_left, upper_right, left, right, center, lower_left, lower_right or bottom; placement is a qualitative hint, NOT pixel coordinates.
- All natural language outside Japanese visible text must be ENGLISH. Do not add any extra visible words in any part of the output.
- Prefer succinct but meaningful English (1-3 sentences for captions, 2-4 for the background)."""

QUOTE_BLOCK_RE = re.compile(r'「([^」]+)」', re.S)

def classify_placement(context):
    """Use the user's immediate Japanese placement clause, not model geometry."""
    context = context[-65:]
    if re.search(r'(左下|左側下|下段左|下部左)',context):return 'lower_left'
    if re.search(r'(右下|右側下|下段右|下部右)',context):return 'lower_right'
    if re.search(r'(上部|一番上|最上部|タイトル|上段)',context):return 'top'
    if re.search(r'(下段|最下部|下部|フッター)',context):return 'bottom'
    if re.search(r'(左側|左寄り|左に)',context):return 'left'
    if re.search(r'(右側|右寄り|右に)',context):return 'right'
    if re.search(r'(中央|真ん中)',context):return 'center'
    return 'auto'

def extract_text_blocks(idea):
    """Exact preservation is done by code, not by the language model."""
    results=[]
    for m in QUOTE_BLOCK_RE.finditer(idea):
        raw=m.group(1).replace('\r\n','\n').replace('\r','\n').strip()
        # Exclude exact duplicates if a later sentence repeats the original quotation.
        if not raw or any(b['text']==raw for b in results): continue
        before=idea[max(0,m.start()-100):m.start()]
        # Prefer last clause before the quoted text.
        clause=re.split(r'[。\n]',before)[-1]
        loc=classify_placement(clause)
        results.append({'id':len(results),'text':raw,'placement':loc})
    return results

# Generic requests may ask for visible copy without enclosing it in Japanese quotes.
# Extract requested ROLES without relying on a product/genre name. Their exact copy is
# generated by Ollama, not filled with permanent template text.
GENERIC_COPY_ROLES = (
    ('tagline', re.compile(r'キャッチコピー|スローガン|tagline|短いコピー', re.I), 'top'),
    ('release', re.compile(r'公開(?:日|時期|情報|予定)|発売(?:日|時期)|開催(?:日|日時)|上映(?:日|時期)', re.I), 'bottom'),
    ('cast', re.compile(r'出演(?:者|者名|者情報)?|キャスト', re.I), 'bottom'),
    ('director', re.compile(r'監督(?:名)?', re.I), 'bottom'),
    ('production', re.compile(r'製作(?:名|会社)?|制作(?:会社|名|陣)', re.I), 'bottom'),
    ('venue', re.compile(r'会場(?:名)?|開催場所', re.I), 'bottom'),
)
NEGATED_COPY = re.compile(r'不要|入れない|追加しない|含めない|表示しない|記載しない|省略|なし|除外|必要ない')

def requested_generic_copy_slots(idea, exact_blocks):
    """Capture explicitly requested but unspecified text, independently of genre.

    Only phrases that ask for VISIBLE copy qualify. Descriptive scene text must
    never be converted to an extra text box. Exact user-supplied quotes win.
    """
    slots=[]
    for role,pattern,default_place in GENERIC_COPY_ROLES:
        for match in pattern.finditer(idea):
            clause_start=max(idea.rfind('。',0,match.start()),idea.rfind('\n',0,match.start()))+1
            next_period=idea.find('。',match.end());next_nl=idea.find('\n',match.end())
            endings=[v for v in (next_period,next_nl) if v>=0]
            clause=idea[clause_start:min(endings) if endings else len(idea)]
            if NEGATED_COPY.search(clause):continue
            # Require instructions about placing, writing or generating visible content.
            if not re.search(r'配置|入れ|載せ|表示|記載|考え|作成|生成|書い|文言|情報|下部|上部|画面|ポスター|広告|チラシ|案内|宣伝',clause):
                continue
            # Existing explicit quoted copy is authoritative, so never add a
            # duplicate AI-generated copy for the same role.
            if re.search(r'「[^」]+」',clause):
                continue
            # A clause that refers to an existing, separately specified quote
            # should not create another box for the same role.
            if any((b['text'] in clause) for b in exact_blocks):continue
            position=classify_placement(clause)
            if position=='auto':position=default_place
            slots.append({'id':len(slots),'role':role,'placement':position})
            break
    return slots

def clean_requested_copy(value):
    if not isinstance(value,str):return ''
    val=value.strip().strip('「」『』"\' ')
    if not 2 <= len(val) <= 85:return ''
    if re.search(r'編集可|未定|（仮）|\(仮\)|キャッチコピー（|公開情報（|クレジット（',val):return ''
    if not JAPANESE_SCRIPT.search(val):return ''
    return val

# Explicit numbered visual assets must never be silently folded into the background.
# These patterns are used as *grounding*, not as a general-purpose language parser.
ENUM_VISUAL = re.compile(r'(?:画像要素(?:は|を)?[\s　]*[0-9０-９一二三四五六七八九]+つ?です[。\n]*)?'
                         r'([0-9０-９一二三四五六七八九]+)つ目は[、,\s　]*([^。\n]+)', re.S)
COUNT_VISUAL = re.compile(r'画像要素(?:は|を)?[\s　]*([0-9０-９一二三四五六七八九]+)つ?')
SUBJECT_WORD = re.compile(r'(?:日本人)?(?:男性|女性|男の子|女の子|人物|少女|少年|猫|犬|車|自動車|スポーツカー|バイク|ソファ|テーブル|観葉植物|椅子|商品|和菓子|抹茶|風景|景色|建物|時計|花瓶)')
JP_NUM = {'一':1,'二':2,'三':3,'四':4,'五':5,'六':6,'七':7,'八':8,'九':9}
def count_number(v):
    v=v.translate(str.maketrans('０１２３４５６７８９','0123456789'))
    return int(v) if v.isdigit() else JP_NUM.get(v)

def subject_type(name):
    return 'character' if re.search(r'人物|女性|男性|女の子|男の子|少年|少女|猫|犬|動物|キャラクター',name) else 'object'

# Generic category mentions in instructions are NOT additional subject instances.
# Example: "男性、赤いスポーツカー、女性を配置。人物と車は独立Boxに" has 3 subjects.
CATEGORY_ONLY_CLAUSE = re.compile(r'^(?:人物|人|車|自動車|物体|画像|画像要素|対象|オブジェクト|キャラクター|それぞれ|各要素|全要素|被写体|人物と車|車と人物)(?:[、とや及び・\s]*(?:人物|人|車|物体|画像|画像要素|対象|オブジェクト|キャラクター|それぞれ|各要素|全要素|被写体))*[はをもが]?',re.I)

def subject_key(name):
    n=re.sub(r'[\s　、,。]','',str(name))
    if re.search('スポーツカー|自動車|車',n):return 'car'
    if re.search('女性|女の子|少女',n):return 'female'
    if re.search('男性|男の子|少年',n):return 'male'
    return n

def explicit_subjects(idea):
    """Extract requested instances only; never promote generic explanatory nouns.

    Numbered images are authoritative. Otherwise use the primary creation clause
    (not later sentences explaining how to edit subjects or boxes).
    """
    outside=QUOTE_BLOCK_RE.sub(' ',idea)
    numbered=list(ENUM_VISUAL.finditer(outside))
    if numbered:
        result=[]
        for m in numbered:
            phrase=m.group(2).strip(' 、。')
            phrase=re.sub(r'(?:です|になります)$','',phrase)
            phrase=re.sub(r'^(?:画像|イメージ|写真)(?:は|が)?','',phrase)
            result.append({'name':phrase[:90], 'label':subject_type(phrase),'placement':'auto'})
        return result
    # Explicit individual names in clauses such as '人物1、車1、人物2' are authoritative.
    labeled=re.findall(r'(人物|男性|女性|車|自動車|スポーツカー|猫|犬|商品)([１２３４５６７８９123456789])',outside)
    if labeled:
        return [{'name':n+i,'label':subject_type(n),'placement':'auto'} for n,i in labeled]
    # Read only the scene/asset definition clauses, not subsequent instructions
    # such as '人物と車はそれぞれ独立した画像要素として作成'.
    result=[]; seen=set()
    for m in re.finditer(r'[^。\n]+[。\n]?',outside):
        clause=m.group().strip(' 。\n')
        if not clause:continue
        if re.search(r'(独立した|それぞれ|要素数|Bounding|Box|枠|作成してください|出力|編集|配置修正)',clause) and not re.search(r'(背景に|背景は|背景で|背景として|配置してください|描いてください)',clause):
            continue
        if re.search(r'(人物と車|車と人物|人物や車|画像要素は)',clause) and re.search(r'(独立|要素|枠|Box)',clause):continue
        if not re.search(r'(配置|登場|描いて|並べ|立つ|背景に|背景は|背景で|イメージは|画像は|画像要素)',clause):continue
        # Scene background is summary-only unless explicitly described as a separate image asset.
        is_background=bool(re.search(r'(背景に|背景は|背景で|背景として)',clause))
        for t in SUBJECT_WORD.finditer(clause):
            term=t.group()
            if is_background and term in ('風景','景色','建物'):continue
            # Generic '人物' is not an extra subject if men/women have already been stated.
            if term=='人物' and any(k in seen for k in ('female','male')):continue
            if term in ('車','自動車') and 'car' in seen:continue
            # Do not create a background '街並み/景色' as an object by default.
            if term in ('風景','景色') and is_background:continue
            key=subject_key(term)
            if key in seen:continue
            seen.add(key)
            # Prefer an adjective and noun, but never include a separate second subject.
            prefix=clause[max(0,t.start()-24):t.start()]
            descriptors=re.search(r'((?:赤い|白い|青い|黒い|カジュアルな服装の|スーツ姿の|着物姿の|冬服を着た|伝統的な|高級な|日本人)[\s　]*)+$',prefix)
            name=((descriptors.group() if descriptors else '')+term).strip()
            result.append({'name':name[:90],'label':subject_type(term),'placement':classify_placement(prefix)})
    return result

def infer_subjects_from_request(idea):
    named=explicit_subjects(idea)
    if named:
        return [{'label':x['label'],'placement':x['placement'],
                 'name':x['name'], 'caption':fallback_subject_caption(x['name'],x['label'])} for x in named]
    outside=QUOTE_BLOCK_RE.sub(' ',idea)
    if re.search(r'日本人女性|女性|女の子|少女|人物|男性|男の子|少年|猫|犬',outside):
        pos='right' if re.search(r'右側に[^。\n]{0,45}(女性|人物|女の子|男性|猫|犬)',outside) else ('left' if re.search(r'左側に[^。\n]{0,45}(女性|人物|女の子|男性|猫|犬)',outside) else 'auto')
        return [{'label':'character','placement':pos,'name':'人物',
                 'caption':'One clearly defined person matching the request, natural pose and realistic physical details.'}]
    return []

def fallback_subject_caption(name,typ):
    if typ=='character':return 'One clearly defined '+('Japanese woman' if '女性' in name else 'person or animal')+' matching the requested subject. Natural pose, detailed appearance and consistent lighting.'
    return 'One clearly defined requested object or scene element with realistic shape, detailed texture, natural appearance and consistent lighting.'

def cleaned_english(text, fallback, max_length=1100):
    if not isinstance(text,str) or not text.strip() or JAPANESE_SCRIPT.search(text):return fallback
    # Avoid printing invented display copy carried by the style description.
    return text.strip()[:max_length]

def text_style_for_block(block, style):
    """One exact copy of Japanese visible text, rest English only."""
    if block['placement']=='top':
        default='Large, elegant Japanese headline lettering with strong legibility and balanced line spacing, centered at the top.'
    elif block['placement']=='bottom':
        default='Clear, organized Japanese event information with readable dark navy typography and generous spacing between lines.'
    else:
        default='Readable Japanese promotional typography in dark navy, with clear hierarchy and a prominent emphasis on any sale percentage.'
    style=cleaned_english(style,default,450)
    # Remove suspected copied quote glyphs. Descriptions may specify style but not duplicate text.
    if any(c in style for c in '「」"“”'):
        style=default
    return text_exact_instruction(block['text'])+' '+style

def norm_placement(value):
    aliases={'top-left':'upper_left','top_right':'upper_right','bottom-left':'lower_left',
             'bottom-right':'lower_right','bottom_left':'lower_left','bottom_right':'lower_right',
             'upper-left':'upper_left','upper-right':'upper_right','top-center':'top','bottom-center':'bottom'}
    value=str(value or 'auto').strip().lower().replace(' ','_')
    value=aliases.get(value,value)
    return value if value in ('top','upper_left','upper_right','left','right','center','lower_left','lower_right','bottom') else 'auto'

def layout_items_poster(blocks, subjects, w, h):
    """Allocate responsive rectangles. Semantic requests inform anchors; no guessed LLM pixel math.

    A frequent use case is headline + left copy + right person + footer.
    The algorithm also handles subsets, multiple copy blocks and other canvas ratios.
    """
    margin_x=max(16,round(w*.055));margin_y=max(16,round(h*.055))
    x_l=margin_x; x_r=w-margin_x
    is_wide=w>=h*1.35
    is_tall=h>=w*1.35
    top=[b for b in blocks if b['placement']=='top']
    bottom=[b for b in blocks if b['placement']=='bottom']
    nonedge=[b for b in blocks if b not in top and b not in bottom]
    # Footer height scales with line count; never collapse 3-line event information to a thin ribbon.
    footer_lines=max((len(b['text'].split('\n')) for b in bottom),default=0)
    top_lines=max((len(b['text'].split('\n')) for b in top),default=0)
    top_h=max(round(h*.14),round(h*(.14+.055*max(0,top_lines-1)))) if top else 0
    footer_h=max(round(h*.12),round(h*(.10+.055*max(0,footer_lines-1)))) if bottom else 0
    if top:top_h=min(top_h,round(h*.27))
    if bottom:footer_h=min(footer_h,round(h*.26))
    top_y=margin_y
    body_y=top_y+top_h+(round(h*.035) if top else 0)
    footer_y=h-margin_y-footer_h if bottom else h-margin_y
    body_end=footer_y-(round(h*.035) if bottom else 0)
    if body_end-body_y < round(h*.25):
        # Ensure a reasonable central area even with multiple multiline headers.
        body_y=min(body_y,round(h*.29));body_end=max(body_end,round(h*.68))
    body_height=body_end-body_y
    for i,b in enumerate(top):
        slot=top_h//len(top)
        b['bbox']=[x_l,top_y+i*slot,x_r,top_y+(i+1)*slot]
    for i,b in enumerate(bottom):
        slot=footer_h//len(bottom)
        b['bbox']=[x_l,footer_y+i*slot,x_r,footer_y+(i+1)*slot]
    has_right_subject=any(s['placement'] in ('right','upper_right','lower_right') for s in subjects)
    has_left_subject=any(s['placement'] in ('left','upper_left','lower_left') for s in subjects)
    has_left_copy=any(b['placement'] in ('left','lower_left','upper_left','auto') for b in nonedge)
    # Two-column editorial template when subject and text occupy opposed sides.
    split_right=round(w*.50 if not is_tall else w*.53)
    split_left=round(w*.50 if not is_tall else w*.47)
    gap=max(12,round(w*.035))
    for i,s in enumerate(subjects):
        placement=s['placement']
        if has_right_subject and has_left_copy and placement in ('right','upper_right','lower_right'):
            box=[split_right+gap//2,body_y,x_r,body_end]
        elif has_left_subject and placement in ('left','upper_left','lower_left'):
            box=[x_l,body_y,split_left-gap//2,body_end]
        elif placement in ('right','upper_right','lower_right'):
            box=[round(w*.52),body_y,x_r,body_end]
        elif placement in ('left','upper_left','lower_left'):
            box=[x_l,body_y,round(w*.48),body_end]
        else:
            box=[round(w*.27),body_y,round(w*.74),body_end]
        if len(subjects)>1:
            # Split multiple subjects horizontally inside their assigned column.
            slot=max(1,(box[2]-box[0])//len(subjects))
            box=[box[0]+i*slot,box[1],box[0]+(i+1)*slot,box[3]]
        s['bbox']=box
    # Text column allocation; multiple blocks stack in the same column.
    groups={'left':[],'right':[],'center':[]}
    for b in nonedge:
        pl=b['placement']
        if pl in ('left','lower_left','upper_left'):side='left'
        elif pl in ('right','lower_right','upper_right'):side='right'
        elif pl=='auto':side='left' if has_right_subject else ('right' if has_left_subject else 'center')
        else:side='center'
        groups[side].append(b)
    for side,group in groups.items():
        if not group:continue
        if side=='left':
            a=x_l;b=(split_right-gap//2 if has_right_subject else x_r)
        elif side=='right':
            a=(split_left+gap//2 if has_left_subject else x_l);b=x_r
        else:
            if subjects:
                a=x_l;b=(split_right-gap//2 if has_right_subject else x_r)
            else:a=x_l;b=x_r
        total_lines=sum(max(1,len(t['text'].split('\n'))) for t in group)
        intergap=max(8,round(h*.017))
        available=max(30,body_height-intergap*(len(group)-1))
        y=body_y
        for j,t in enumerate(group):
            lines=max(1,len(t['text'].split('\n')))
            span=available*lines/max(1,total_lines)
            # Consume proportional lines, but preserve the remaining space for subsequent blocks.
            if j==len(group)-1:y2=body_end
            else:y2=y+round(span)
            t['bbox']=[a,y,b,y2]
            y=y2+intergap
    # 0..width, height; stable integer coordinates with >=20px box edges.
    for it in [*blocks,*subjects]:
        x1,y1,x2,y2=map(int,it['bbox'])
        it['bbox']=[max(0,x1),max(0,y1),min(w,max(x1+20,x2)),min(h,max(y1+20,y2))]
    return [*blocks,*subjects]

def layout_items_deterministically(blocks, subjects, w, h, multi_panel=False):
    """Place independent boxes within ONE scene by default, not a photo grid.

    Grids are reserved for explicit collage/multi-panel requests. Boxes are
    editable subject zones, not rectangles that should be visible in the output.
    """
    if multi_panel:
        return layout_items_grid(blocks, subjects, w, h)
    if len(subjects)<=1 and len(blocks)>=2 and any(b['placement']!='auto' for b in blocks):
        return layout_items_poster(blocks,subjects,w,h)
    margin_x=max(16,round(w*.055)); margin_y=max(16,round(h*.055))
    top=[b for b in blocks if b['placement']=='top']
    bottom=[b for b in blocks if b['placement']=='bottom']
    middle=[b for b in blocks if b not in top and b not in bottom]
    def box(it,x1,y1,x2,y2):
        it['bbox']=[max(0,round(x1)),max(0,round(y1)),min(w,round(x2)),min(h,round(y2))]
    title_h=round(h*(.15+.05*max(0,max((len(b['text'].splitlines()) for b in top),default=1)-1))) if top else 0
    foot_h=round(h*(.16+.045*max(0,max((len(b['text'].splitlines()) for b in bottom),default=1)-1))) if bottom else 0
    for idx,item in enumerate(top):
        box(item,margin_x,margin_y+idx*title_h/max(1,len(top)),w-margin_x,margin_y+(idx+1)*title_h/max(1,len(top)))
    for idx,item in enumerate(bottom):
        box(item,margin_x,h-margin_y-foot_h+idx*foot_h/max(1,len(bottom)),w-margin_x,h-margin_y-foot_h+(idx+1)*foot_h/max(1,len(bottom)))
    body_top=margin_y+title_h+(round(h*.025) if top else 0)
    body_bottom=h-margin_y-foot_h-(round(h*.025) if bottom else 0)
    height=max(80,body_bottom-body_top)
    num=len(subjects)
    if num==1:
        specs=[(.46,.05,.96,.95)]
    elif num==2:
        specs=[(.09,.09,.56,.92),(.48,.07,.94,.93)]
    elif num==3:
        # A shared scene: figures flank a foreground vehicle/subject, NOT three tiles.
        if subjects[0]['label']=='character' and subjects[1]['label']=='object' and subjects[2]['label']=='character':
            specs=[(.06,.08,.38,.90),(.27,.40,.76,.94),(.65,.08,.96,.90)]
        else:
            specs=[(.06,.10,.40,.88),(.32,.26,.70,.95),(.64,.08,.96,.88)]
    else:
        # Stagger subjects across the common stage; allow moderate overlap.
        specs=[]
        for k in range(num):
            cx=.13+.74*(k+.5)/num
            span=max(.27,min(.46,1.05/num))
            y0=.08+(.18 if k%2 else .0)
            specs.append((max(.02,cx-span/2),y0,min(.98,cx+span/2),.92))
    for i,it in enumerate(subjects):
        left,top_frac,right,bottom_frac=specs[i]
        placement=norm_placement(it.get('placement'))
        bw=right-left
        if placement in ('left','upper_left','lower_left'):left,right=.05,.05+bw
        elif placement in ('right','upper_right','lower_right'):left,right=.95-bw,.95
        elif placement in ('center','top','bottom'):left,right=.5-bw/2,.5+bw/2
        if placement in ('upper_left','upper_right','top'):top_frac,bottom_frac=.04,.72
        elif placement in ('lower_left','lower_right','bottom'):top_frac,bottom_frac=.28,.96
        box(it,w*left,body_top+height*top_frac,w*right,body_top+height*bottom_frac)
    for idx,it in enumerate(middle):
        nlines=len(it['text'].splitlines()); y1=body_top+height*(.17+idx*.12)
        side=norm_placement(it.get('placement'))
        if side in ('right','upper_right','lower_right'):x1,x2=w*.54,w*.95
        else:x1,x2=w*.05,w*.47
        box(it,x1,y1,x2,min(body_bottom,y1+height*max(.18,min(.7,.15+nlines*.11))))
    return [*blocks,*subjects]


def layout_items_grid(blocks, subjects, w, h):
    """True side-by-side cards only when explicitly requested."""
    import math as _math
    elements=[*blocks,*subjects]
    n=len(elements)
    cols=min(3,n);rows=_math.ceil(n/cols)
    m=round(min(w,h)*.055);gap=round(min(w,h)*.025)
    cw=(w-2*m-gap*(cols-1))/cols;ch=(h-2*m-gap*(rows-1))/rows
    for idx,it in enumerate(elements):
        c=idx%cols;r=idx//cols
        x1=round(m+c*(cw+gap));y1=round(m+r*(ch+gap))
        it['bbox']=[x1,y1,min(w,round(x1+cw)),min(h,round(y1+ch))]
    return elements

# v15: A single shared background is the default. Honour explicit multi-panel requests.
MULTI_PANEL_REQUEST = re.compile(r'(コラージュ|複数パネル|分割画面|画面分割|別々の背景|異なる背景|二分割|三分割|比較パネル|複数のコマ|collage|multi[- ]panel|split[- ]screen)',re.I)
# Captions can be verbose but must not allocate a second backdrop inside a bbox.
BOX_BACKGROUND_SENTENCE = re.compile(
    r'\b(?:in the background|background features?|background is|set against|against a background|'
    r'with (?:a|an|its own) (?:separate|distinct|blurred|softly blurred) (?:background|backdrop)|'
    r'behind (?:her|him|them|the subject)|framed (?:inside|by|within) (?:a|an) (?:panel|frame|photo))\b',re.I)
BOX_PANEL_PHRASE = re.compile(
    r'\b(?:separate (?:background|panel|frame)|isolated photo (?:frame|panel)|'
    r'rectangular (?:background|plate|panel)|colored (?:background|panel|plate)|'
    r'its own background|inside a (?:framed photo|separate panel))\b',re.I)


def unify_ai_background(draft, idea):
    """AI output only: consolidate scene direction in summary; retain user-requested panels.

    Keep the wording of visible text, all bbox geometry, and manual/imported edits intact.
    """
    if MULTI_PANEL_REQUEST.search(idea):
        return draft
    summary=draft['summary']['caption'].strip().rstrip('. ')
    # No fixed shopping mall or snow scene is injected: the LLM's user-derived environment remains.
    shared=('One continuous, unified background across the entire canvas, with consistent '
            'lighting, perspective and color palette. All subjects and text share the same scene; '
            'no separate photo panels, rectangular background blocks, borders, split regions, or repeated subjects. Each requested person or object appears exactly once in this same uninterrupted scene.')
    draft['summary']['caption']=summary+'. '+shared
    for item in draft['items']:
        cap=item['caption']
        # Only remove full sentences that independently prescribe a subject-local backdrop.
        # Do not rewrite or delete the exact Japanese text or text styling itself.
        sentences=re.split(r'(?<=[.!?])\s+(?=[A-Z])',cap)
        kept=[]
        for sentence in sentences:
            if item['label'] in ('character','object') and BOX_BACKGROUND_SENTENCE.search(sentence):
                continue
            if BOX_PANEL_PHRASE.search(sentence):
                continue
            kept.append(sentence)
        cleaned=' '.join(kept).strip()
        if cleaned:
            item['caption']=cleaned
    return draft

# v16: manga/comic pages require page > panel > text hierarchy.
# A comic panel is NOT an object or character bbox. Panel composition belongs in
# summary.caption; only exact printed Japanese text becomes a separate text bbox.
MANGA_INTENT = re.compile(r'(漫画|マンガ|コミック|コマ割り|[0-9０-９一二三四五六七八九]+\s*コマ(?:目|構成|漫画|で))')
MANGA_SCENE = re.compile(r'(?m)(?:^|[\n\r])\s*([0-9０-９一二三四五六七八九]+)\s*コマ目\s*[：:]\s*')
MANGA_COUNT = re.compile(r'([0-9０-９一二三四五六七八九]+)\s*コマ(?:で|構成|の|漫画|すべて|全体|に)')
MANGA_SYSTEM = """You are translating a Japanese manga page plan into concise ENGLISH panel artwork descriptions for MOSS-Image.
Return ONLY a JSON object: {"panels":[{"id":1,"description":"English visual scene for panel 1"},...],"visual_style":"English overall line-art/screentone style"}.
CRITICAL:
- The supplied numbered scene list is authoritative. Output EXACTLY one panel per supplied scene, in its given order. Never create or delete panels.
- Each description must faithfully preserve the events, subjects, actions and setting of the corresponding scene ONLY. Do NOT introduce new characters (such as a boy), animals or settings not specified.
- The same heroine and kitten recur across panels with consistent appearance; they are NOT extra unique subjects.
- Do NOT invent visible words, speech, dialogue, signs or captions. Those are supplied separately as text elements; descriptions focus on artwork only.
- Art direction must produce a multi-panel manga page, NOT a single uninterrupted photo or three-portrait collage.
- Use English only; no Japanese text in output; do not add other keys."""

def manga_scenes(idea):
    matches=list(MANGA_SCENE.finditer(idea))
    scenes=[]
    for i,m in enumerate(matches):
        end=matches[i+1].start() if i+1<len(matches) else len(idea)
        content=idea[m.end():end].strip()
        if i==len(matches)-1:
            content=re.split(r'\n\s*(?:日本の|コマの|登場する|吹き出しの|背景は|指定していない|漫画の|すべてのコマで)',content,maxsplit=1)[0].strip()
        n=count_number(m.group(1))
        if n is None or n != len(scenes)+1:
            raise ValueError('漫画のコマ番号は1コマ目から連番で指定してください。')
        scenes.append(content)
    if not scenes:
        raise ValueError('漫画の各場面が見つかりません。\n「1コマ目：...」の形式でコマの内容を指定してください。')
    count_match=MANGA_COUNT.search(idea)
    if count_match and count_number(count_match.group(1))!=len(scenes):
        raise ValueError(f'漫画の指定コマ数と、実際のシーン数（{len(scenes)}）が一致しません。')
    if len(scenes)>12:raise ValueError('漫画は最大12コマまで対応しています。')
    return scenes

# Short manga requests: a user may supply the story and panel count without
# manually enumerating every panel. Only this manga-specific fallback uses AI
# to propose the art moments; all non-manga routes remain unchanged.
MANGA_SHORT_SYSTEM = """You are a Japanese manga storyboard planner. Return only JSON with this exact shape:
{"panels":[{"id":1,"description":"Concise English visual artwork description for panel 1"}],"dialogue_placements":[]}
Use exactly the requested number of panels with consecutive integer ids starting at 1, up to 12.
Make successive moments of ONE coherent story grounded in the supplied request.
Keep people, animals, clothes and locations consistent across panels.
If the user gives no individual scenes, propose a natural sequence from opening to resolution.
Never invent dialogue, visible text, additional people or animals, narration or lettering.
Never put any visible words, quote marks, title, speech bubbles, labels or sound effects in the descriptions.
Do not rewrite or embellish supplied title text.
The dialogue_placements list is ONLY for dialogue the user explicitly quoted, in this form:
[{"text":"exact supplied user dialogue","panel":3}]. Never add dialogue on your own.
Do not create visible objects outside those suggested by the story; details may be
filled in only as needed to create a coherent storyboard. Output English descriptions only.
"""

def manga_story_count(idea):
    m=MANGA_COUNT.search(idea)
    if not m:
        raise ValueError('漫画のコマ数が指定されていません。「5コマ漫画」のように指定してください。')
    n=count_number(m.group(1))
    if n is None or not 1<=n<=12:
        raise ValueError('漫画のコマ数は1～12で指定してください。')
    return n

def manga_auto_panel_descriptions(model, idea):
    """AI proposes English panel artwork for short unnumbered manga requests.

    Validate all panel ids before creating boxes. Never silently invent scenes
    when Ollama fails; fail with an actionable message instead.
    """
    count=manga_story_count(idea)
    quote_blocks=extract_text_blocks(idea)
    # Explicit text is copied unchanged by our own code, never generated.
    quoted_dialogue=[b['text'] for b in quote_blocks
                     if not manga_is_title_quote(idea,b['text'])
                     and manga_is_explicit_dialogue(idea,b['text'])
                     and not manga_is_character_name_quote(idea,b['text'])]
    payload={'request':idea,'panel_count':count,'explicit_dialogue_only':quoted_dialogue}
    response=request_ollama(model,[
        {'role':'system','content':MANGA_SHORT_SYSTEM},
        {'role':'user','content':json.dumps(payload,ensure_ascii=False)}
    ],temperature=0,num_predict=3200)
    if not isinstance(response,dict) or not isinstance(response.get('panels'),list):
        raise ValueError('短い漫画指示からコマの内容を設計できませんでした。再生成するか、各コマの説明を追加してください。')
    supplied={}
    for panel in response['panels']:
        if not isinstance(panel,dict):continue
        try:i=int(panel.get('id'))
        except (TypeError,ValueError):continue
        if i<1 or i>count or i in supplied:continue
        desc=cleaned_english(panel.get('description'),'',900)
        if desc and not JAPANESE_SCRIPT.search(desc):supplied[i]=desc
    if len(supplied)!=count:
        raise ValueError(f'短い漫画指示で{count}コマ分の場面を取得できませんでした。再生成してください。')
    placements={}
    raw_placements=response.get('dialogue_placements',[])
    if isinstance(raw_placements,list):
        for row in raw_placements:
            if not isinstance(row,dict):continue
            t=row.get('text')
            try:i=int(row.get('panel'))
            except (TypeError,ValueError):continue
            if t in quoted_dialogue and 1<=i<=count and t not in placements:
                placements[t]=i-1
    # Default unspecified speaker/panel locations to the final panel. These
    # are still the USER'S exact words, never fabricated dialogue.
    for t in quoted_dialogue:placements.setdefault(t,count-1)
    return [supplied[i] for i in range(1,count+1)],placements

def manga_is_character_name_quote(idea,text):
    """Suppress only quoted names introduced as character identities, not speech."""
    for match in QUOTE_BLOCK_RE.finditer(idea):
        if match.group(1).strip()!=text:continue
        before=re.split(r'[。\n]',idea[max(0,match.start()-85):match.start()])[-1]
        after=idea[match.end():match.end()+25]
        if re.search(r'(?:名前|名付け|名前は|名は|飼い猫|主人公|呼び名)',before) and not re.search(r'(?:セリフ|台詞|心の声)',before):
            return True
        if re.match(r'\s*(?:という(?:名前|猫|人物)|と(?:名付け|呼ばれる))',after):
            return True
    return False

def manga_is_title_quote(idea,text):
    for match in QUOTE_BLOCK_RE.finditer(idea):
        if match.group(1).strip()!=text:continue
        preceding=idea[max(0,match.start()-55):match.start()]
        clause=re.split(r'[。\n]',preceding)[-1]
        if re.search(r'(?:タイトル|題名|作品名|見出し)',clause):return True
    return False

def manga_panel_rects(n,w,h):
    """Editable manga art frames, Japanese right-to-left reading order.

    For an odd number of panels, the final panel spans the bottom row.
    The successful five-panel test uses two / two / one.
    Geometry is valid for non-square canvas sizes as well.
    """
    rows = (n + 1) // 2
    left = round(w * .055)
    right = w - left
    top = round(h * .13)
    bottom = h - round(h * .052)
    xgap = max(10, round(w * .024))
    ygap = max(10, round(h * .016))
    row_height = (bottom - top - ygap * (rows - 1)) / rows
    rects=[]
    for r in range(rows):
        y1 = round(top + r * (row_height + ygap))
        y2 = round(top + (r + 1) * row_height + r * ygap)
        if n % 2 and r == rows - 1:
            rects.append((left,y1,right,y2))
        else:
            mid = (left + right) // 2
            rects.extend(((mid + xgap // 2,y1,right,y2),
                          (left,y1,mid - (xgap - xgap // 2),y2)))
    return rects

def manga_is_explicit_dialogue(scene, value):
    """Manga-only: quoted names/descriptions must not become printed speech.

    An explicit dialogue cue may precede or immediately follow the quotation;
    a bare quotation in an enumerated scene is accepted unless identified as
    a character name, title or other non-dialogue label.
    """
    pos=scene.find('「'+value+'」')
    if pos < 0:return False
    before=re.split(r'[。\n]',scene[:pos])[-1][-65:]
    after=scene[pos+len(value)+2:][:24]
    if re.search(r'(?:名前|名付け|名は|名前は|呼び名|題名|タイトル|作品名)',before) and not re.search(r'(?:セリフ|台詞|心の声|言う|話す|呼びかけ)',before):
        return False
    if re.search(r'(?:セリフ|台詞|吹き出し|心の声|発言|言葉|鳴き声|喋る|話す|言う|呼びかけ)',before):
        return True
    if re.match(r'\s*(?:と(?:鳴く|言う|話す|つぶやく|叫ぶ|呼ぶ)|と言って|と答える)',after):
        return True
    return True

def manga_text_blocks(idea, scenes):
    """Numbered manga: title only from title clause; speech only from scenes."""
    title=[];dialogue=[]
    for b in extract_text_blocks(idea):
        if manga_is_title_quote(idea,b['text']):
            title.append(b)
            continue
        panel=next((idx for idx,scene in enumerate(scenes)
                    if ('「'+b['text']+'」') in scene and manga_is_explicit_dialogue(scene,b['text'])),None)
        if panel is not None:
            dialogue.append((b,panel))
    return title,dialogue

# v16.2 manga: visible dialogue belongs exclusively to text items.
# Translation and panel composition must never reintroduce dialogue into summary.
MANGA_SPEECH_EN = re.compile(r"(?i)\b(?:she|he|the girl|the boy|the cat|the kitten|the character)\s+(?:asks?|says?|replies?|whispers?|shouts?|exclaims?|utters?)\s*[,：:]?\s*[\"'“‘][^\"'”’]+[\"'”’]\s*\.?|[\"'“‘][^\"'”’]+[\"'”’]")
MANGA_SPEECH_JP = re.compile(r'「[^」]*」|『[^』]*』')

def manga_strip_visible_dialogue(description, visible_lines):
    text=MANGA_SPEECH_EN.sub('',description)
    text=MANGA_SPEECH_JP.sub('',text)
    for line in sorted(visible_lines,key=len,reverse=True):
        if line:text=text.replace(line,'')
    # Do not describe the existence of extra speech bubbles in panels.
    text=re.sub(r'(?i)\b(?:a|one|the)\s+(?:speech|dialogue|word|text)\s+(?:bubble|balloon|caption|box)\b[^.]*\.?','',text)
    text=re.sub(r'\s{2,}',' ',text).strip(' ,.;:')
    return text

def manga_rule_based_scene(scene):
    """Strictly narrow source-grounded English fallback for common manga scene briefs.
    No invented people, panels or speech. Unknown scenes cause a clear error instead.
    """
    if ('駅前' in scene or '駅' in scene) and ('歩' in scene or '傘' in scene):
        return 'At a train station after rain, the high school girl walks holding an umbrella. The wet pavement reflects the surrounding station lights.'
    if ('子猫' in scene or '猫' in scene) and ('段ボール' in scene or '箱' in scene) and ('座' in scene or '濡' in scene):
        return 'A small wet kitten sits inside a cardboard box beside the street.'
    if ('子猫' in scene or '猫' in scene) and ('気づ' in scene or '見つ' in scene) and ('立ち止' in scene or '驚' in scene):
        return 'The high school girl notices the kitten nearby and stops with a surprised expression.'
    if ('子猫' in scene or '猫' in scene) and ('しゃが' in scene or 'かが' in scene) and ('手' in scene or '触' in scene):
        return 'The high school girl crouches down and gently extends her hand toward the kitten.'
    if ('子猫' in scene or '猫' in scene) and ('抱' in scene):
        return 'The high school girl gently lifts and holds the kitten in her arms, smiling.'
    return None

def manga_clean_style(value):
    style=cleaned_english(value,'Monochrome Japanese manga ink lines, screentones, expressive faces and dramatic panel framing.',400)
    # The user asked for monochrome; conflicting model style must not override it.
    return style

def generate_manga_layout(model,idea,w,h):
    """Manga-only editable scene panels, mirroring the successful imported test.

    Important: non-manga generation paths are deliberately untouched.
    Ollama translates scene artwork, but never controls panel count or positions.
    """
    # Explicit numbered panels retain their previously validated behaviour.
    # Short briefs instead get their moments from Ollama, not a hardcoded story.
    has_numbered_scenes=bool(MANGA_SCENE.search(idea))
    auto_placements={}
    if has_numbered_scenes:
        scenes=manga_scenes(idea)
        titles,dialogue=manga_text_blocks(idea,scenes)
    else:
        scenes,auto_placements=manga_auto_panel_descriptions(model,idea)
        quoted=extract_text_blocks(idea)
        titles=[b for b in quoted if manga_is_title_quote(idea,b['text'])]
        dialogue=[(b,auto_placements[b['text']]) for b in quoted
                  if b['text'] in auto_placements and not manga_is_character_name_quote(idea,b['text'])]
    count=len(scenes)
    monochrome=bool(re.search(r'モノクロ|白黒|黒白|black.and.white|monochrome|grayscale',idea,re.I))
    supplied={}
    if has_numbered_scenes:
        payload={'request':idea,'panel_count':count,'scenes':[{'id':i+1,'japanese':s} for i,s in enumerate(scenes)]}
        response={}
        try:
            response=request_ollama(model,[{'role':'system','content':MANGA_SYSTEM},
                 {'role':'user','content':json.dumps(payload,ensure_ascii=False)}],temperature=0,num_predict=4800)
        except (ValueError, KeyError, TypeError, json.JSONDecodeError):
            pass
        if not isinstance(response,dict):response={}
        panels=response.get('panels',[])
        if isinstance(panels,list):
            for panel in panels:
                if not isinstance(panel,dict):continue
                try:i=int(panel.get('id'))
                except (TypeError,ValueError):continue
                if i<1 or i>count or i in supplied:continue
                desc=cleaned_english(panel.get('description'),'',900)
                if desc:supplied[i]=desc
        broken=[i for i in range(1,count+1) if i not in supplied]
        if broken:
            repair_payload={'scenes':[{'id':i,'japanese':scenes[i-1]} for i in broken]}
            try:
                fixed=request_ollama(model,[
                    {'role':'system','content':('Translate ONLY these numbered Japanese manga scenes to natural concise English, with no dialogue or quotations. Keep all stated actions, characters and setting, invent nothing. Return JSON {"panels":[{"id":1,"description":"English scene"}]} with original ids.')},
                    {'role':'user','content':json.dumps(repair_payload,ensure_ascii=False)}
                ],temperature=0,num_predict=1800)
                if isinstance(fixed,dict) and isinstance(fixed.get('panels'),list):
                    for panel in fixed['panels']:
                        if not isinstance(panel,dict):continue
                        try:i=int(panel.get('id'))
                        except (ValueError,TypeError):continue
                        desc=cleaned_english(panel.get('description'),'',900)
                        if i in broken and desc:supplied[i]=desc
            except (ValueError,KeyError,TypeError,json.JSONDecodeError,URLError,HTTPError,TimeoutError):
                pass
        for i in range(1,count+1):
            if i not in supplied:
                fallback=manga_rule_based_scene(scenes[i-1])
                if not fallback:
                    raise ValueError(f'{i}コマ目を英語に変換できませんでした。該当コマの場面説明を短くして再生成してください。')
                supplied[i]=fallback
    else:
        supplied={i+1:desc for i,desc in enumerate(scenes)}

    rects=manga_panel_rects(count,w,h)
    by_panel={}
    for b,idx in dialogue:by_panel.setdefault(idx,[]).append(b)
    title_line='Monochrome manga' if monochrome else 'Japanese manga'
    summary=(
        f'One flat, hand-drawn {title_line} page with exactly {count} sequential rectangular panels. '
        'Panels are read in Japanese order: right to left within each row, then top to bottom. '
        + (f'In the bottom row, panel {count-1} is on the RIGHT and panel {count} is on the LEFT; '
           f'panel {count} is the FINAL ending scene. Never swap their contents. ' if count%2==0 else '') +
        'Each panel depicts a different moment of the same story. Recurring people and animals keep the same identity and appearance. '
        'Delicate manga pen-and-ink outlines, simplified illustrated faces, expressive eyes, clean white gutters, black ink and screentones; not photography. '
        + ('Strict black-and-white grayscale print only. ' if monochrome else '') +
        'The only printed words are the separately supplied title and dialogue text items, each used once. '
        'No extra speech bubbles, lettering, sound effects, signage or logos. All panel subjects remain within their panel frames.'
    )
    items=[]
    title_top=max(8,round(h*.025))
    title_bottom=max(title_top+24,round(h*.108))
    for i,b in enumerate(titles):
        y1=title_top+(title_bottom-title_top)*i//max(1,len(titles))
        y2=title_top+(title_bottom-title_top)*(i+1)//max(1,len(titles))
        items.append({'label':'text','text':b['text'], 'caption':text_exact_instruction(b['text']),
                      'bbox':[round(w*.12),y1,round(w*.88),max(y1+20,y2)]})
    for idx,box in enumerate(rects):
        x1,y1,x2,y2=box
        desc=manga_strip_visible_dialogue(supplied[idx+1],[b['text'] for b in by_panel.get(idx,[])])
        desc=re.sub(r'(?i)\b(?:she|he|the girl|the kitten)\s+(?:asks?|says?|replies?)\s*[,：:]?\s*[^.]*\.?','',desc).strip(' ,.;:')
        if not desc:desc=manga_rule_based_scene(scenes[idx])
        if not desc:raise ValueError(f'{idx+1}コマ目にセリフ以外の場面描写がありません。')
        row=idx//2
        row_count=(count+1)//2
        row_name='upper' if row==0 else 'bottom' if row==row_count-1 else 'middle'
        if count%2 and idx==count-1:
            position=f'{row_name} full-width panel'
        else:
            side='right' if idx%2==0 else 'left'
            position=f'{row_name}-{side} panel'
        role=(' This is the FINAL ending moment, after all earlier panels. '
              if idx==count-1 else
              ' This is the penultimate story moment, immediately BEFORE the final panel. '
              if idx==count-2 else ' ')
        art_caption=(f'Panel {idx+1} artwork only. Exact page position: {position}. '
                     f'Keep this panel in position; do not swap its scene with a neighboring panel.{role}'
                     f'One rectangular Japanese manga panel scene. {desc.rstrip(" .")}. '
                     'Draw inside this panel area with hand-inked manga outlines and screentones, '
                     'separated from neighboring panels by clean white gutters. '
                     'No visible writing, no speech bubbles, no labels, no panel numbering.')
        items.append({'label':'object','caption':art_caption,'bbox':[x1,y1,x2,y2],
                      'tags':[f'panel_{idx+1}']})
        speech=by_panel.get(idx,[])
        pw=x2-x1;ph=y2-y1
        for j,b in enumerate(speech):
            # Top-right inside its OWN panel; nested box can overlap its artwork.
            text_w=min(round(pw*.45),max(85,round(pw*.30)))
            text_h=min(round(ph*.39),max(55,round(ph*.35)))
            right=x2-max(10,round(pw*.04))
            top=y1+max(12,round(ph*.055))+j*(text_h+7)
            if top+text_h>y2-6:
                raise ValueError(f'{idx+1}コマ目のセリフが多すぎます。Text Boxを手動で調整してください。')
            items.append({'label':'text','text':b['text'],'caption':text_exact_instruction(b['text']),
                          'bbox':[right-text_w,top,right,top+text_h]})
    if not items:
        raise ValueError('漫画にタイトルやセリフなど「」で囲んだ文字がありません。少なくとも1つ指定してください。')
    draft={'width':w,'height':h,'bbox_coord_space':'abs','bbox_format':'xyxy',
           'summary':{'caption':summary,'canvas':'single'},'items':items}
    validate_and_compile(draft)
    return draft

# v16.2-catalog: Separate catalog cards are intentionally NOT a shared scene.
# Do not alter the existing comic and single-scene paths.
CATALOG_INTENT = re.compile(r'(カタログ|商品一覧|商品比較|コレクションを紹介|製品ラインナップ|商品ラインナップ|catalogue|catalog|product grid)', re.I)
CATALOG_NUMBERED = re.compile(r'(?m)(?:^|\n)\s*([1-9１-９一二三四五六七八九])\s*つ目\s*(?:は|：|:)\s*')
CATALOG_NAME = re.compile(r'(?m)^\s*([1-9１-９])\s*[.．:：、]\s*([A-Za-z][A-Za-z0-9 &+\-]{1,60})\s*$')

def catalog_requested_assets(idea):
    """Preserve exact numbered assets and associated product names from the user's brief."""
    parts=list(CATALOG_NUMBERED.finditer(idea))
    if len(parts)<2:
        return []
    assets=[]
    for index,m in enumerate(parts):
        start=m.end()
        stop=parts[index+1].start() if index+1<len(parts) else len(idea)
        segment=idea[start:stop]
        # Asset description is a sentence, not later editorial instructions.
        desc=re.split(r'[。\n]',segment,1)[0].strip(' \t\r、。')
        if not desc: raise ValueError(f'カタログの商品{index+1}の説明が空欄です')
        number=count_number(m.group(1))
        if number!=index+1: raise ValueError('カタログの商品番号を1から連番で指定してください')
        assets.append({'description':desc,'number':number})
    if len(assets)>12: raise ValueError('カタログは最大12商品まで対応しています')
    names={}
    for m in CATALOG_NAME.finditer(idea):
        number=count_number(m.group(1))
        if number in names: raise ValueError(f'商品名の番号{number}が重複しています')
        names[number]=m.group(2).strip()
    if names and (set(names)!=set(range(1,len(assets)+1))):
        raise ValueError('商品名の番号と商品数が一致しません')
    for a in assets: a['name']=names.get(a['number'])
    return assets

CATALOG_SYSTEM = """You are a professional commercial catalog photographer and English translator.
The input includes an AUTHORITATIVE ORDERED LIST of numbered Japanese product descriptions.
Return ONLY valid JSON with this shape:
{"summary_caption":"English instructions for an ELEGANT PRINTED PRODUCT CATALOG PAGE with independent product cells, consistent simple design, NO store interior or life-size display showroom", "products":[{"number":1,"caption":"Precise English visual description of product #1: material, case, strap, dial, color, shape"}], "title_style":"Short English description of the title typography", "name_style":"Short English description of small product-label typography"}
Rules:
- Exactly one English product caption per input numbered product, in the same order and numbered 1..N. NO additional products. Do not merge any products.
- Every product must keep its specified colors, materials, type, and distinguishing features. No generic placeholder such as 'one requested object'.
- Catalog page: separate neatly aligned product PHOTO AREAS / cards, equal quality lighting, white/light-gray backdrop, visible gutters and independent product headings. NOT a shared boutique, showroom, giant centerpiece or single-scene photograph.
- No visible text in summary or product captions: names and headlines are supplied separately as exact text items.
- All output prose ENGLISH only; no other fields or markdown."""

def generate_catalog_layout(model,idea,w,h):
    assets=catalog_requested_assets(idea)
    if not assets:
        raise ValueError('カタログ内の商品を抽出できませんでした。「1つ目：…」「2つ目：…」のように商品を列挙してください。')
    title_matches=re.findall(r'タイトル(?:は|：|:)\s*「([^」]+)」',idea)
    if not title_matches:raise ValueError('カタログのタイトルを「」で指定してください')
    title=title_matches[0].strip()
    # Reject silent loss of product names (which were explicitly requested).
    if not all(a['name'] for a in assets):
        raise ValueError('商品名を「1. NAME」のように商品数と同じ数だけ指定してください')
    response=request_ollama(model,[{'role':'system','content':CATALOG_SYSTEM},
        {'role':'user','content':json.dumps({'numbered_products':assets,'original_request':idea,'canvas':{'width':w,'height':h}},ensure_ascii=False)}],
        temperature=0.15,num_predict=4000)
    if not isinstance(response,dict) or not isinstance(response.get('products'),list):
        raise ValueError('Ollamaのカタログ解析結果に商品説明がありません')
    products=response['products']
    if len(products)!=len(assets):
        raise ValueError(f'指定商品は{len(assets)}件ですが、Ollamaは{len(products)}件を返しました。欠落した商品を補完せず処理を中止します')
    captions=[]
    for i,(asset,part) in enumerate(zip(assets,products),1):
        if not isinstance(part,dict) or part.get('number')!=i:
            raise ValueError(f'カタログ商品{i}の順序が不正です')
        caption=part.get('caption','')
        if not isinstance(caption,str) or len(caption.strip())<24 or not english_explanation(caption):
            raise ValueError(f'カタログ商品{i}の英語Descriptionが不正です。商品を省略しないでください')
        if re.search(r'one (?:clearly defined )?requested (?:object|product)|requested subject|scene element',caption,re.I):
            raise ValueError(f'カタログ商品{i}のDescriptionが汎用的すぎます')
        captions.append(caption.strip())
    summary=cleaned_english(response.get('summary_caption'),'',1200)
    # Stable local direction even if an Ollama summary requests a showroom.
    summary=('A single printed premium product catalog page with '+str(len(assets))+
             ' distinct product photography cells, separated by clean white gutters. '
             'Each card shows exactly one different requested product; do not enlarge one product into a central hero or depict a retail showroom. '
             'Consistent studio lighting, white and light-gray editorial design, balanced product scale, photographic texture and generous white space. '
             'Each product name appears only below its own product photograph. '
             'No additional products, logos, captions or text.')
    # Title is reserved top; cards are 2-column for 4 products, otherwise a balanced grid.
    n=len(assets)
    cols=2 if n==4 else (1 if n==1 else (2 if n<=6 else 3))
    rows=math.ceil(n/cols)
    mx=max(24,round(w*.055)); my=max(20,round(h*.045))
    gap=max(14,round(min(w,h)*.025)); title_h=round(h*.14)
    title_box=[mx,my,w-mx,my+title_h]
    title_style=cleaned_english(response.get('title_style'),'Elegant bold serif title typography, centered, dark charcoal ink.',200)
    name_style=cleaned_english(response.get('name_style'),'Readable refined sans-serif product name, centered in dark charcoal.',200)
    items=[{'label':'text','text':title,'caption':text_exact_instruction(title)+' '+title_style,'bbox':title_box}]
    region_top=title_box[3]+gap*2
    region_bottom=h-my
    cell_w=(w-2*mx-gap*(cols-1))/cols
    cell_h=(region_bottom-region_top-gap*(rows-1))/rows
    if cell_w<80 or cell_h<95: raise ValueError('商品数に対してキャンバスが小さすぎます')
    for idx,a in enumerate(assets):
        row,col=divmod(idx,cols)
        x1=round(mx+col*(cell_w+gap));x2=round(x1+cell_w)
        y1=round(region_top+row*(cell_h+gap));y2=round(y1+cell_h)
        label_h=max(24,round(cell_h*.18))
        label_top=y2-label_h
        object_box=[x1,y1,x2,label_top-max(6,round(gap*.3))]
        text_box=[x1,label_top,x2,y2]
        if object_box[3]-object_box[1]<35:raise ValueError('商品画像のBounding Boxが小さすぎます')
        items.append({'label':'object','caption':captions[idx],'bbox':object_box,'tags':[f'商品{idx+1}: {a["description"][:65]}']})
        items.append({'label':'text','text':a['name'],'caption':text_exact_instruction(a['name'])+' '+name_style,
                      'bbox':text_box})
    result={'width':w,'height':h,'bbox_coord_space':'abs','bbox_format':'xyxy',
            'summary':{'caption':summary,'canvas':'multi_panel'},'items':items}
    validate_and_compile(result)
    return result


# Single-model multi-view catalog: distinct photographs of one product, not a
# list of different products. The official Layout draft has flat object/text
# items and global xyxy boxes; there is no nested panel ID/coordinate system.
CAR_PRODUCT_INTENT = re.compile(r'(?:高級車|セダン|SUV|スポーツカー|自動車|車両|\b(?:sedan|luxury car|automotive)\b)', re.I)
CATALOG_VIEW_RULES = [
    ('exterior_three_quarter', re.compile(r'(?:外観(?:画像|写真)?|車全体|ボディ全体|エクステリア|斜め前方)',re.I), 'A full vehicle three-quarter exterior photograph, clearly showing the entire same luxury sedan, consistent paint color and body styling.'),
    ('front', re.compile(r'(?:フロント(?:画像|写真|まわり|正面|フェイス)?|前面|正面外観)',re.I), 'A front exterior view of the same luxury sedan, showing its grille, headlights and hood, consistent design and paint color.'),
    ('rear', re.compile(r'(?:リア(?:画像|写真|まわり|外観|正面)?|後方外観|後ろ姿)',re.I), 'A rear exterior view of the same luxury sedan, showing the trunk and taillights, consistent design and paint color.'),
    ('interior_wide', re.compile(r'(?:内装(?:全体)?(?:画像|写真)?|インテリア(?:全体)?|室内全体)',re.I), 'A wide photograph of the same sedan interior, with dashboard, front seats, rich leather upholstery and consistent trim.'),
    ('cockpit', re.compile(r'(?:コックピット|運転席|ハンドル(?:周辺|まわり)?|ダッシュボード)',re.I), 'A closer cockpit view of the same sedan interior showing steering wheel, instrument panel and center controls.'),
    ('rear_seats', re.compile(r'(?:後部座席|リアシート|シート(?:や|と|・)?後部座席|シートの快適性)',re.I), 'A passenger-compartment view of the same sedan rear seats, showing consistent premium leather upholstery and legroom.'),
]

CAR_VIEW_SYSTEM = '''You translate a Japanese single-model car catalog brief into short descriptions for distinct photographs and editorial copy.
Return ONLY a JSON object like:
{"views":[{"id":"front","caption":"An English visual description of this exact car viewed from the front..."}],"intro_ja":"日本語の短い車両紹介文", "features_ja":"日本語の短い特徴説明文", "specs_ja":"日本語の短い仕様一覧", "tagline_ja":"日本語の短いキャッチコピー"}
- Exactly one view per requested id in the supplied ordered_view_ids list. Preserve id and order; never merge, add, or omit views.
- Each caption describes only that specific camera view, with coherent identical vehicle identity, body shape, paint, lighting, and interior trim across all images. Do not add people.
- Natural English photo captions only. No generic one requested object / scene element placeholder.
- Japanese intro_ja/features_ja: short, substantive, compact editorial language about the fictional vehicle, not an advertising slogan, do not include company logos or other car models.
- tagline_ja: a short Japanese slogan only when requested; otherwise empty string.
- specs_ja must include only requested spec headings; when exact values aren't supplied, use a dash (—), do NOT invent performance figures.
- Do not include visible typography or layouts in the view captions; text is placed separately.
- No markdown, no additional fields.'''

def single_car_catalog_views(idea):
    if not CATALOG_INTENT.search(idea) or not CAR_PRODUCT_INTENT.search(idea):
        return []
    # A distinct catalog of numbered products already has its own tested route.
    if catalog_requested_assets(idea):
        return []
    views=[{'id':key,'baseline':baseline} for key,pat,baseline in CATALOG_VIEW_RULES if pat.search(idea)]
    return views if len(views)>=3 else []

def single_car_catalog_title(idea):
    patterns=[r'車名\s*は?\s*[「\"]([^」\"]{2,90})[」\"]',
              r'車名\s*[:：]\s*([^\n。]{2,90})',
              r'タイトル\s*(?:は|[:：])\s*[「\"]([^」\"]+)[」\"]']
    for pattern in patterns:
        m=re.search(pattern,idea)
        if m:
            return m.group(1).strip(' 。')
    m=re.search(r'車名\s*は\s*(?:\*\*)?([A-Za-z][A-Za-z0-9 .-]{2,65})(?:\*\*)?(?=[。\n]|$)',idea)
    if m:return m.group(1).strip(' 。*')
    return None

def generate_single_car_catalog_layout(model,idea,w,h):
    views=single_car_catalog_views(idea)
    if not views:raise ValueError('車両カタログの画像要素が3種類以上ありません')
    title=single_car_catalog_title(idea)
    if not title:raise ValueError('車両カタログの車名を指定してください（例：車名は「Aurelius LX」）')
    # Views are authoritative; Ollama produces descriptive language, not item count or coordinates.
    required_specs=[]
    for label in ('ボディタイプ','エンジン','駆動方式','最高出力','燃費','乗車定員','ラゲッジ容量'):
        if label in idea:required_specs.append(label)
    if not required_specs and re.search(r'(?:主要スペック|スペック一覧|仕様一覧)',idea):
        required_specs=['ボディタイプ','駆動方式','乗車定員','最高出力']
    response=request_ollama(model,[{'role':'system','content':CAR_VIEW_SYSTEM},
         {'role':'user','content':json.dumps({'title':title,'ordered_view_ids':[v['id'] for v in views],
                        'requested_specs':required_specs,'source_request':idea},ensure_ascii=False)}],
         temperature=.15,num_predict=4500)
    if not isinstance(response,dict) or not isinstance(response.get('views'),list):
        raise ValueError('Ollamaの車両カタログ解析結果が不正です。省略して1枚絵にはしません')
    raw=response['views']
    if len(raw)!=len(views):
        raise ValueError(f'画像は{len(views)}種類必要ですが、Ollamaからは{len(raw)}種類しか返されませんでした')
    descriptions=[]
    for idx,(entry,view) in enumerate(zip(raw,views)):
        if not isinstance(entry,dict) or entry.get('id')!=view['id']:
            raise ValueError(f'車両カタログの画像{idx+1}の種類・順序が一致しません')
        caption=entry.get('caption','')
        if not isinstance(caption,str) or len(caption.strip())<30 or not english_explanation(caption):
            raise ValueError(f'画像{idx+1}の英語Descriptionが不正です')
        if re.search(r'one (?:clearly defined )?requested (?:object|product)|requested subject|scene element',caption,re.I):
            raise ValueError(f'画像{idx+1}が汎用Descriptionです')
        descriptions.append(caption.strip())
    def validated_ja(name,limit):
        value=response.get(name,'')
        if not isinstance(value,str) or len(value.strip())<8 or len(value)>limit:
            raise ValueError(f'Ollamaの{name}が欠落・不正です')
        return value.strip()
    intro=validated_ja('intro_ja',180)
    features=validated_ja('features_ja',230)
    specs=validated_ja('specs_ja',280) if required_specs else ''
    tagline_requested=bool(re.search(r'(?:キャッチコピー|スローガン)',idea))
    tagline=validated_ja('tagline_ja',75) if tagline_requested else ''
    # Short copy rather than 7 invented numeric values on a 1M canvas.
    for spec in required_specs:
        if spec not in specs:
            raise ValueError(f'主要スペック「{spec}」がOllamaの出力から欠落しました')
    # Keep generous outer margins, fixed typography region, photo cells and copy region.
    mx=max(22,round(w*.045)); gap=max(9,round(min(w,h)*.012))
    title_y=max(20,round(h*.032));title_h=max(55,round(h*.095))
    subtitle_h=max(22,round(h*.033)) if tagline_requested else 0
    photo_top=title_y+title_h+subtitle_h+gap
    copy_h=max(130,round(h*.24))
    photo_bottom=h-mx-copy_h-gap
    n=len(views); cols=3 if n>=5 else 2; rows=math.ceil(n/cols)
    cell_w=(w-2*mx-gap*(cols-1))/cols
    cell_h=(photo_bottom-photo_top-gap*(rows-1))/rows
    if cell_w<90 or cell_h<100:
        raise ValueError(f'{w}×{h}には写真{n}点と説明文を読みやすく配置できません。解像度か画像点数を調整してください')
    items=[{'label':'text','text':title,'caption':text_exact_instruction(title)+' Refined large luxury automotive editorial headline, charcoal lettering, centered.',
            'bbox':[mx,title_y,w-mx,title_y+title_h]}]
    if tagline_requested:
        items.append({'label':'text','text':tagline,'caption':text_exact_instruction(tagline)+' Restrained Japanese subheading, smaller gray lettering centered under vehicle name.',
                      'bbox':[mx,title_y+title_h,w-mx,title_y+title_h+subtitle_h]})
    for i,(view,desc) in enumerate(zip(views,descriptions)):
        row,col=divmod(i,cols)
        x1=round(mx+col*(cell_w+gap));x2=round(x1+cell_w)
        y1=round(photo_top+row*(cell_h+gap));y2=round(y1+cell_h)
        items.append({'label':'object','caption':desc,'bbox':[x1,y1,x2,y2],
                      'tags':[view['id']]})
    text_top=photo_bottom+gap
    text_gap=max(8,round(w*.012))
    if required_specs:
        # Intro + features left, specifications right, all exact separately editable items.
        mid=round(w*.60)
        left_w=mid-mx-text_gap
        if left_w<120:raise ValueError('説明文領域が狭すぎます')
        split=text_top+round(copy_h*.47)
        items.extend([
          {'label':'text','text':intro,'caption':text_exact_instruction(intro)+' Small Japanese body copy, left aligned, dark charcoal, readable editorial paragraph.','bbox':[mx,text_top,mid-text_gap,split]},
          {'label':'text','text':features,'caption':text_exact_instruction(features)+' Small Japanese feature description, left aligned, dark charcoal.','bbox':[mx,split+text_gap,mid-text_gap,h-mx]},
          {'label':'text','text':specs,'caption':text_exact_instruction(specs)+' Compact Japanese specifications list, left aligned, clear line spacing.','bbox':[mid,text_top,w-mx,h-mx]}
        ])
    else:
        split=text_top+round(copy_h*.50)
        items.extend([
          {'label':'text','text':intro,'caption':text_exact_instruction(intro)+' Compact Japanese body copy, left aligned, dark charcoal.','bbox':[mx,text_top,w-mx,split]},
          {'label':'text','text':features,'caption':text_exact_instruction(features)+' Compact Japanese features text, left aligned, dark charcoal.','bbox':[mx,split+text_gap,w-mx,h-mx]}
        ])
    summary=(f'A single flat printed luxury automobile catalog page for one fictional model, {title}, '
            f'with exactly {n} distinct editorial automotive photographs in separated rectangular photo cells. '
            'Each photograph shows a different specified angle or interior view of THE SAME vehicle with consistent body design, paint, trim and identity. '
            'Uniform premium white and light-gray editorial page background, restrained typography, clear white gutters, and independent descriptive text and specification areas. '
            'No showroom, model posing by a car, repeated camera views, extra photos, invented logos or extra printed text.')
    result={'width':w,'height':h,'bbox_coord_space':'abs','bbox_format':'xyxy',
            'summary':{'caption':summary,'canvas':'multi_panel'},'items':items}
    validate_and_compile(result)
    return result

# Generic editable draft mode. The quantities in a short Japanese request are
# authoritative; language-model output is never permitted to collapse N assets
# into one. No edits are made to the existing manga or catalog pipelines.
EDITABLE_QUANTITY_RE = re.compile(
    r'(?P<subject>男女|男性|女性|人物|人|猫|犬|腕時計|時計|車|自動車|スポーツカー|バイク|椅子|花瓶|テーブル|商品)'
    r'\s*(?P<count>[0-9０-９一二三四五六七八九十]+)\s*(?P<unit>人|名|匹|台|個|点|本|脚|種類)?')
PREFIX_PEOPLE_COUNT_RE = re.compile(r'(?P<count>[0-9０-９一二三四五六七八九十]+)\s*(?:人|名)\s*の\s*(?:集合写真|グループ|男女|人物)')
EDITABLE_MAX = 12
EDITABLE_GENERIC_SYSTEM = """You write EDITABLE DRAFTS for a local visual layout editor, not final pictures.
Return a JSON object ONLY with: {"summary_caption":"2-3 short English sentences describing ONLY the shared setting and art style", "subjects":[{"index":1,"caption":"one distinct subject in English, with simple appearance/clothes/pose, no independent background"},...]}
The user supplied an EXACT number of physical subjects. Make exactly that many subject captions in order, with index 1..N. Never merge, omit, duplicate, or replace them. Do not add text or unrelated people. Do not invent notable accessories or costumes not requested. Preserve requested age, setting, gender and the group-photo scene. Avoid specifying individual background panels. No visible writing in summary or captions. All prose ENGLISH.""" 

def editable_quantity_targets(idea):
    """Return subject slots only when a short brief explicitly specifies a count."""
    # Catalogs and manga use their previously tested route.
    if MANGA_INTENT.search(idea) or CATALOG_INTENT.search(idea):return []
    clean=QUOTE_BLOCK_RE.sub(' ',idea)
    hits=[]
    for m in EDITABLE_QUANTITY_RE.finditer(clean):
        n=count_number(m.group('count'))
        if n is None or n<1 or n>EDITABLE_MAX: continue
        name=m.group('subject')
        # Only count actual visual subjects, not '5 people' in explanatory instructions.
        clause=clean[max(0,m.start()-90):min(len(clean),m.end()+65)]
        if re.search(r'(?:文字|セリフ|テキスト|スペック|レビュー)\s*'+re.escape(name),clause):continue
        hits.append({'subject':name,'count':n,'start':m.start()})
    if not hits:
        m=PREFIX_PEOPLE_COUNT_RE.search(clean)
        if m:
            n=count_number(m.group('count'))
            if n and 1<=n<=EDITABLE_MAX:hits=[{'subject':'人物','count':n,'start':m.start()}]
    if not hits:return []
    # '男女5人' and individual counts such as 男性3人、女性2人 are distinct.
    # Skip general totals when detailed male/female quantities were also given.
    if any(h['subject'] in ('男性','女性') for h in hits):
        hits=[h for h in hits if h['subject'] not in ('男女','人','人物')]
    slots=[]
    for h in hits:
        subject=h['subject']
        if subject=='男女':
            # Default distribution is a proposal, freely editable afterwards.
            subjects=['男性' if j%2==0 else '女性' for j in range(h['count'])]
        else:subjects=[subject]*h['count']
        for sub in subjects:
            slots.append({'name':sub,'label':subject_type(sub) if sub!='人' else 'character'})
    return slots if 1<=len(slots)<=EDITABLE_MAX else []

def editable_default_caption(name,idea,i):
    elderly=bool(re.search(r'高齢|シニア|60代|６０代|年配|年長',idea))
    age='an older ' if elderly else 'an adult '
    if name in ('男女','人物','人','男性','女性'):
        noun='man' if name=='男性' else 'woman' if name=='女性' else 'person'
        outfit='a comfortable autumn jacket and casual trousers' if re.search('秋|紅葉|旅行|観光',idea) else 'simple casual clothing'
        return f'One distinct {age}{noun}, wearing {outfit}, standing with a relaxed natural posture and a gentle smile. Full body visible.'
    if name=='猫':return 'One distinct domestic cat with natural fur details, standing or sitting in a relaxed pose.'
    if name=='犬':return 'One distinct domestic dog with natural coat details, relaxed posture and realistic anatomy.'
    vehicle='An individual complete vehicle, fully visible in a natural three-quarter view, realistic bodywork and perspective.'
    if re.search('車|バイク',name):return vehicle
    english_noun={"腕時計":"wristwatch","時計":"watch","椅子":"chair","花瓶":"vase","テーブル":"table","商品":"product"}.get(name,"physical object")
    return f'One distinct {english_noun}, clearly visible, with natural materials and realistic shape.'

def generic_group_bbox_positions(slots, blocks, w, h):
    """Editable overlapping subject areas in one scene, never photo cards."""
    n=len(slots); margin=max(16,round(min(w,h)*.055))
    reserved=round(h*.14) if blocks else 0
    if blocks:
        for b in blocks:
            b['bbox']=[margin,margin,w-margin,margin+reserved]
    ytop=margin+reserved+max(8,round(h*.015))
    ybot=h-margin
    region=ybot-ytop
    if n==1: xs=[(.20,.80)]
    else:
        # Moderate overlap allows a group portrait rather than equal photo tiles.
        span=max(.17,min(.35,1.18/n))
        centers=[.12+.76*i/(n-1) for i in range(n)]
        xs=[(max(.02,c-span/2),min(.98,c+span/2)) for c in centers]
    for i,(slot,(a,b)) in enumerate(zip(slots,xs)):
        stagger=.035*(i%2)
        slot['bbox']=[round(w*a),round(ytop+region*(.045+stagger)),round(w*b),round(ybot-region*.035)]
    return [*blocks,*slots]

def generate_editable_quantity_layout(model, idea, w, h, slots):
    """Count is deterministic; language model only supplies editable draft copy."""
    requested=len(slots)
    blocks=extract_text_blocks(idea)
    # The title is inferred only from an explicitly quoted theme or title.
    if len(blocks)>10:raise ValueError('タイトル・文字の指定が多すぎます')
    for b in blocks:
        b.update(label='text',caption=text_style_for_block(b,'Clear Japanese headline lettering, balanced and readable.'))
    payload={'request':idea,'count':requested,
             'slots':[{'index':j+1,'subject':s['name']} for j,s in enumerate(slots)]}
    response=request_ollama(model,[{'role':'system','content':EDITABLE_GENERIC_SYSTEM},
             {'role':'user','content':json.dumps(payload,ensure_ascii=False)}],temperature=.15,num_predict=2200)
    if not isinstance(response,dict):response={}
    returned=response.get('subjects')
    complete=(isinstance(returned,list) and len(returned)==requested and all(
        isinstance(x,dict) and x.get('index')==i+1 and isinstance(x.get('caption'),str)
        and len(x['caption'].strip())>=24 and english_explanation(x['caption'])
        for i,x in enumerate(returned)))
    for i,slot in enumerate(slots):
        cap=returned[i]['caption'].strip() if complete else editable_default_caption(slot['name'],idea,i)
        slot['caption']=cap
        slot['tags']=[f'{slot["name"]} {i+1}']
    # If Ollama fails, preserve count and editable boxes with clearly stated fallback.
    summary=cleaned_english(response.get('summary_caption'),'',1200)
    if not summary:
        summary=('A cohesive autumn travel photograph in Nagano, Japan, with vivid red and golden foliage, '
                 'natural daylight and a single continuous outdoor landscape.' if re.search('長野|紅葉',idea) else
                 'A unified natural scene with cohesive daylight, perspective and atmospheric background.')
    summary=(summary.rstrip('. ') + '. '+
             f'A single continuous composition containing exactly {requested} separately editable subjects. '
             'All subjects share the same environment; no split panels or repeated subjects.')
    placed=generic_group_bbox_positions(slots,blocks,w,h)
    items=[]
    for item in placed:
        out={'label':item['label'],'caption':item['caption'],'bbox':item['bbox']}
        if 'tags' in item:out['tags']=item['tags']
        if item['label']=='text':out['text']=item['text']
        items.append(out)
    draft={'width':w,'height':h,'bbox_coord_space':'abs','bbox_format':'xyxy',
           'summary':{'caption':summary,'canvas':'single'},'items':items}
    validate_and_compile(draft)
    return draft

# Poster-specific editable draft. This is intentionally separate from manga,
# catalog and quantity-based planning; only explicit movie-poster requests use it.
MOVIE_POSTER_INTENT = re.compile(r'(映画(?:の|用|向け|作品)?(?:ポスター|宣伝ポスター)|劇場(?:映画)?ポスター|movie poster|film poster)', re.I)
MOVIE_TITLE_RE = re.compile(r'(?:映画の)?タイトル(?:は|：|:|を)?\s*[「『\"]([^」』\"]+)[」』\"]')
# Explicitly quoted copy takes priority over Ollama-generated sample slogans.
MOVIE_TAGLINE_RE = re.compile(r'(?:キャッチコピー|スローガン)(?:は|として|に|：|:|を)?\s*[「『\"]([^」』\"]+)[」』\"]')

MOVIE_POSTER_SYSTEM = """You are producing an editable layout draft for a Japanese movie poster.
Return ONLY valid JSON with these keys:
{
 "summary_caption": "English description of scenery and cinematic composition, no visible lettering",
 "hero_caption": "English depiction of the protagonist",
 "moon_caption": "English depiction of the moon",
 "temple_caption": "English depiction of temple and stairs",
 "tagline": "Actual short Japanese tagline, not a field name",
 "cast_text": "Actual short fictional Japanese cast credit line",
 "release_text": "Actual short Japanese release announcement",
 "credits_text": "Actual short fictional director/production credit line"
}
Respect the user's brief. These text fields are OPTIONAL: supply them only if that exact kind of information was requested. Never include metadata/placeholder labels such as キャッチコピー（編集可）, 映画タイトル, 公開情報（仮）, クレジット（仮）, 未定, 編集可, or （仮）. Never invent real people's names or real releases; if a cast or credits line is requested without names, invent clearly fictional names as draft copy that can be edited later. Use concise finished-looking text, not explanatory notes. No markdown.
"""

# Optional poster elements follow the user's brief, not a fixed poster template.
MOVIE_CAST_RE = re.compile(r'出演(?:者|者名|者情報|者一覧|キャスト)?|cast', re.I)
MOVIE_RELEASE_RE = re.compile(r'公開(?:日|時期|情報|予定)?|上映(?:日|時期|情報|予定)?|release date', re.I)
MOVIE_CREDITS_RE = re.compile(r'クレジット|監督|製作|制作(?:陣|スタッフ)|credits?', re.I)
MOVIE_METADATA_RE = re.compile(r'編集可|未定|（仮）|\(仮\)|\(編集可\)|キャッチコピー（|映画タイトル$|公開情報（|クレジット（')

def movie_footer_requested(idea, pattern):
    # Negative instructions (including "no unrequested credits") override generic poster expectations.
    for match in pattern.finditer(idea):
        vicinity=idea[max(0, match.start()-10):match.end()+38]
        if re.search(r'(?:不要|入れない|追加しない|含めない|表示しない|記載しない|省略|なし|除外|必要ない)',vicinity):
            continue
        return True
    return False

def movie_credit_text(value, fallback):
    if isinstance(value,str):
        candidate=value.strip().strip('「」『』"')
        if 2 <= len(candidate) <= 90 and not MOVIE_METADATA_RE.search(candidate):
            return candidate
    return fallback


def generate_movie_poster_layout(model, idea, w, h):
    """Stable film-poster scaffold; Ollama proposes copy, never box count/positions."""
    title_match=MOVIE_TITLE_RE.search(idea)
    title=(title_match.group(1).strip() if title_match else '')
    if not title:
        # A user can replace this provisional title in the editor.
        title='映画タイトル（仮）'
    has_moon=bool(re.search(r'月|moon',idea,re.I))
    has_temple=bool(re.search(r'寺|神社|参道|temple|shrine',idea,re.I))
    has_protagonist=bool(re.search(r'主人公|人物|女性|男性|人影|旅人|少女|少年|protagonist',idea,re.I))
    try:
        response=request_ollama(model,[{'role':'system','content':MOVIE_POSTER_SYSTEM},
             {'role':'user','content':json.dumps({'request':idea,'title':title},ensure_ascii=False)}],
             temperature=.2,num_predict=1400)
    except (ValueError,KeyError,TypeError,json.JSONDecodeError):
        response={}
    if not isinstance(response,dict):response={}
    summary_default=('A solemn, mysterious Japanese fantasy film poster set on a late-autumn night. '
                     'A luminous moon hangs over a misty mountain temple and a winding stone approach, '
                     'with drifting maple leaves and deep midnight-blue shadows. One unified dramatic composition, '
                     'cinematic lighting and open negative space for the poster lettering. No extra printed text.')
    summary=cleaned_english(response.get('summary_caption'),summary_default,1100)
    # A completely separate poster-specific grid with editable elements.
    def coords(a,b,c,d):
        return [max(0,round(w*a)),max(0,round(h*b)),min(w,round(w*c)),min(h,round(h*d))]
    def txt(text,bbox,desc,tag):
        return {'label':'text','text':text,'caption':text_exact_instruction(text)+' '+desc,
                'bbox':bbox}
    elements=[]
    # Only explicit visible copy belongs in a text box. No management notes in tags.
    tagline_match=MOVIE_TAGLINE_RE.search(idea)
    tagline=(tagline_match.group(1).strip() if tagline_match else '')
    if not tagline and re.search(r'キャッチコピー|スローガン|tagline',idea,re.I):
        candidate=response.get('tagline','')
        if isinstance(candidate,str):
            candidate=candidate.strip().strip('「」『』"')
            if 2<=len(candidate)<=35 and JAPANESE_SCRIPT.search(candidate) and title not in candidate:
                tagline=candidate
    if tagline:
        elements.append(txt(tagline,coords(.09,.035,.91,.115),
            'Small refined Japanese tagline, centered near the top, restrained and highly readable.', 'tagline'))
    if has_moon:
        elements.append({'label':'object','caption':cleaned_english(response.get('moon_caption'),
            'One enormous luminous late-autumn full moon above the misty mountains, soft halo and natural lunar detail.',550),
            'bbox':coords(.28,.115,.72,.455)})
    if has_temple:
        elements.append({'label':'object','caption':cleaned_english(response.get('temple_caption'),
            'An ancient Japanese mountain temple with steep stone steps, weathered timber and drifting autumn leaves, partially veiled by mist.',550),
            'bbox':coords(.08,.42,.92,.86)})
    if has_protagonist:
        elements.append({'label':'character','caption':cleaned_english(response.get('hero_caption'),
            'One lone Japanese protagonist in elegant late-autumn clothing, seen in three-quarter view looking toward the moon, contemplative and enigmatic.',600),
            'bbox':coords(.42,.31,.86,.77)})
    # Reserve an uncluttered footer only when the user asks for cast/release/credits.
    want_cast=movie_footer_requested(idea,MOVIE_CAST_RE)
    want_release=movie_footer_requested(idea,MOVIE_RELEASE_RE)
    want_credits=movie_footer_requested(idea,MOVIE_CREDITS_RE)
    footer_count=sum((want_cast,want_release,want_credits))
    title_top,title_bottom=(.64,.745) if footer_count else (.695,.805)
    elements.append(txt(title,coords(.07,title_top,.93,title_bottom),
        'Large elegant cinematic Japanese film title in dramatic Mincho-style lettering, centered and legible.', 'title'))
    # For an unspecified name/date, use editable fictional draft copy, never '未定' or UI metadata.
    # Explicitly quoted values can be preserved by Ollama, otherwise generated sample copy.
    if footer_count:
        bottom_rows=[]
        if want_release:
            bottom_rows.append(movie_credit_text(response.get('release_text'),'2027年秋 公開'))
        if want_cast:
            bottom_rows.append(movie_credit_text(response.get('cast_text'),'出演：星野 葵・水城 蓮'))
        if want_credits:
            bottom_rows.append(movie_credit_text(response.get('credits_text'),'監督：青山 澪　製作：月影映画'))
        # Put requested information in independently editable Text Boxes.
        foot_start=.78
        foot_end=.975
        step=(foot_end-foot_start)/len(bottom_rows)
        for j,visible_text in enumerate(bottom_rows):
            y1=foot_start+j*step
            y2=foot_start+(j+1)*step-.008
            elements.append(txt(visible_text,coords(.08,y1,.92,y2),
                'Small readable Japanese film-poster footer typography.', 'footer'))
    draft={'width':w,'height':h,'bbox_coord_space':'abs','bbox_format':'xyxy',
           'summary':{'caption':summary,'canvas':'single'},'items':elements}
    validate_and_compile(draft)
    return draft

# Minimal SINGLE-PRODUCT PRINTED CATALOG route. Isolated from numbered product
# catalogs, automotive multi-view catalogs, manga, posters, and generic scenes.
# The app (not Ollama) owns the required three editable boxes and the product identity.
SINGLE_PRODUCT_CATALOG_KINDS = (
    ('腕時計', re.compile(r'腕時計|高級時計|watch(?:es)?|wristwatch', re.I),
     'A single luxury wristwatch, isolated product-only studio photograph, elegant watch dial, refined case, complete strap visible, realistic metal and leather textures, carefully controlled highlights. No person, no hand, no model, no other products.',
     '高級腕時計コレクション', '洗練されたデザインと上質な素材が魅力の腕時計。日常にも特別な場面にも品格を添えます。'),
    ('バッグ', re.compile(r'バッグ|ハンドバッグ|handbag|\bbag\b', re.I),
     'A single premium handbag, product-only studio photograph, complete bag including handles, refined material texture and elegant craftsmanship. No person, no model, no additional product.',
     'プレミアムバッグコレクション', '上質な素材感と洗練されたフォルムを楽しむバッグ。毎日の装いに美しいアクセントを添えます。'),
    ('靴', re.compile(r'革靴|シューズ|靴|shoes?', re.I),
     'A single pair of elegant premium shoes photographed as the only product on a clean studio backdrop, fully visible from a three-quarter angle. No person, no model, no additional products.',
     'プレミアムシューズコレクション', '上質な素材と洗練されたデザインが魅力のシューズ。日常の装いに品格を添えます。'),
    ('香水', re.compile(r'香水|フレグランス|perfume|fragrance', re.I),
     'One refined perfume bottle as the sole product in elegant studio product photography, crisp glass details and subtle reflected highlights. No person, no model, no extra products.',
     'フレグランスコレクション', '繊細な香りの世界を表現したフレグランス。上品で印象的なひとときを演出します。'),
    ('化粧品', re.compile(r'化粧品|コスメ|cosmetics?', re.I),
     'A single premium cosmetic product in its original container, isolated professional product photography, clean material details. No person, no model, no additional products.',
     'ビューティーコレクション', '洗練された美しさを日常に取り入れるコスメアイテム。上質なひとときを演出します。'),
)

SINGLE_PRODUCT_CATALOG_SYSTEM = """You write short Japanese editorial text for a single-product printed catalog draft.
Return ONLY valid JSON with keys: title_ja, description_ja.
- Product is the user's specified product, never a person or a substitute item.
- If the user explicitly specified the title, copy it EXACTLY; otherwise propose a short simple Japanese title.
- description_ja must be one or two short Japanese sentences about the product, without invented specifications, brands, prices, or logos.
- Do not invent any additional subjects or layout elements. No markdown.
"""

def single_product_catalog_kind(idea):
    if not CATALOG_INTENT.search(idea): return None
    if catalog_requested_assets(idea) or single_car_catalog_views(idea):return None
    if len(re.findall(r'[1-9１-９一二三四五六七八九]\s*つ目(?:は|[:：])',idea)) >= 2:return None
    # Do not hijack movie-poster or manga rendering even if a catalog word occurs.
    if MANGA_INTENT.search(idea) or MOVIE_POSTER_INTENT.search(idea):return None
    for kind in SINGLE_PRODUCT_CATALOG_KINDS:
        if kind[1].search(idea):return kind
    return None

def generate_single_product_catalog_layout(model,idea,w,h):
    kind=single_product_catalog_kind(idea)
    if kind is None:raise ValueError('単一商品カタログの対象商品を認識できませんでした')
    name, _pattern, product_caption, default_title, default_description=kind
    title_match=re.search(r'タイトル\s*(?:は|[:：])?\s*[「『\"]([^」』\"]+)[」』\"]',idea)
    explicit_title=title_match.group(1).strip() if title_match else None
    # Optional Ollama editorial copy: never use its visual subject list or boxes.
    response={}
    try:
        response=request_ollama(model,[{'role':'system','content':SINGLE_PRODUCT_CATALOG_SYSTEM},
             {'role':'user','content':json.dumps({'request':idea,'product_type':name,'fixed_title':explicit_title},ensure_ascii=False)}],
             temperature=0.15,num_predict=600)
        if not isinstance(response,dict):response={}
    except (ValueError, URLError, HTTPError, TimeoutError, OSError, KeyError, TypeError):
        response={}
    title=explicit_title or default_title
    candidate_title=response.get('title_ja')
    if not explicit_title and isinstance(candidate_title,str) and 2<=len(candidate_title.strip())<=35 and JAPANESE_SCRIPT.search(candidate_title) and '\n' not in candidate_title:
        title=candidate_title.strip()
    description=default_description
    candidate_description=response.get('description_ja')
    if (isinstance(candidate_description,str) and 12<=len(candidate_description.strip())<=110
        and JAPANESE_SCRIPT.search(candidate_description)
        and not re.search(r'モデル|人物|男性|女性|スーツ|俳優|ブランド名|価格|\d+円',candidate_description)):
        description=candidate_description.strip()
    mx=max(24,round(w*.065))
    top=max(24,round(h*.045))
    gap=max(16,round(h*.025))
    title_bottom=round(h*.20)
    image_top=title_bottom+gap
    image_bottom=round(h*.73)
    text_top=image_bottom+gap
    items=[
        {'label':'text','text':title,'caption':text_exact_instruction(title),
         'bbox':[mx,top,w-mx,title_bottom]},
        {'label':'object','caption':product_caption,
         'bbox':[round(w*.14),image_top,round(w*.86),image_bottom], 'tags':[name]},
        {'label':'text','text':description,'caption':text_exact_instruction(description),
         'bbox':[mx,text_top,w-mx,h-top]},
    ]
    summary=('One flat printed premium product catalog page with three clearly separated editorial regions: '
             'an elegant title area at the top, one dedicated product-only photograph in the center, '
             'and a concise descriptive Japanese text area at the bottom. '
             'Premium minimalist design, balanced margins, refined studio product photography, and clear typography. '
             'This is a printed editorial catalog layout, not one continuous interior photograph or a showroom. '
             'No people, no extra products, no duplicate product images, no invented brand logos, and no extra visible text.')
    draft={'width':w,'height':h,'bbox_coord_space':'abs','bbox_format':'xyxy',
           'summary':{'caption':summary,'canvas':'multi_panel'},'items':items}
    validate_and_compile(draft)
    return draft

def generate_semantic_layout(model,idea,w,h):
    if MOVIE_POSTER_INTENT.search(idea) and not MANGA_INTENT.search(idea):return generate_movie_poster_layout(model,idea,w,h)
    if MANGA_INTENT.search(idea):return generate_manga_layout(model,idea,w,h)
    if single_car_catalog_views(idea):return generate_single_car_catalog_layout(model,idea,w,h)
    if CATALOG_INTENT.search(idea) and catalog_requested_assets(idea):return generate_catalog_layout(model,idea,w,h)
    if single_product_catalog_kind(idea):return generate_single_product_catalog_layout(model,idea,w,h)
    # Generic short requests with explicitly quantified subjects get editable slots.
    quantity_slots=editable_quantity_targets(idea)
    if quantity_slots:return generate_editable_quantity_layout(model,idea,w,h,quantity_slots)
    blocks=extract_text_blocks(idea)
    requested_slots=requested_generic_copy_slots(idea,blocks)
    if len(blocks)+len(requested_slots)>30:raise ValueError('表示文字の指定が多すぎます（最大30件）')
    payload={'canvas':{'width':w,'height':h},'request':idea,
             'exact_text_blocks':[{'id':b['id'],'text':b['text'],'placement':b['placement']} for b in blocks],
             'requested_text_slots':requested_slots}
    response=request_ollama(model,[{'role':'system','content':SEMANTIC_SYSTEM},
                                   {'role':'user','content':json.dumps(payload,ensure_ascii=False)}],num_predict=5000)
    if not isinstance(response,dict):raise ValueError('Ollamaの設計データが不正です')
    # The optional copy is grounded in the user's requested roles, not a
    # category-based scaffold. If the planning response omits it, ask Ollama
    # once more for ONLY those missing values instead of silently dropping boxes.
    copy_values={}
    raw_copy=response.get('requested_text_values',[])
    if isinstance(raw_copy,list):
        for entry in raw_copy:
            if isinstance(entry,dict) and type(entry.get('id'))==int:
                copy_values[entry['id']]=clean_requested_copy(entry.get('text'))
    missing=[slot for slot in requested_slots if not copy_values.get(slot['id'])]
    if missing:
        copy_system=('Return ONLY JSON: {"values":[{"id":0,"text":"actual Japanese copy"}]}. '
                     'Write finished, short Japanese visible text for EVERY requested slot, '
                     'based on the user brief. Do not output field names, explanatory notes, '
                     'placeholders, 未定, （仮）, or 編集可. Never add roles not requested.')
        try:
            recovered=request_ollama(model,[{'role':'system','content':copy_system},
                {'role':'user','content':json.dumps({'request':idea,'missing_slots':missing},ensure_ascii=False)}],
                temperature=.2,num_predict=700)
            if isinstance(recovered,dict):
                for entry in recovered.get('values',[]):
                    if isinstance(entry,dict) and type(entry.get('id'))==int:
                        copy_values[entry['id']]=clean_requested_copy(entry.get('text')) or copy_values.get(entry['id'],'')
        except (ValueError,KeyError,TypeError,json.JSONDecodeError):pass
    # Qwen 4B occasionally omits one field even after a batched retry. Recover
    # only the missing slot, keeping completed entries and the main layout intact.
    # Accept the requested standard format as well as a single-field object.
    role_labels={'tagline':'キャッチコピー','cast':'出演者名','release':'公開情報',
                 'director':'監督名','production':'製作名','venue':'会場情報'}
    for slot in requested_slots:
        if copy_values.get(slot['id']):
            continue
        field=slot['role']
        single_system=(
            'You are generating ONE short finished Japanese text string for a visual layout. '
            'Return ONLY a valid JSON object with the key "text". '
            'Do not omit this field. Never write placeholders, explanations, or editing labels. '
            'The text should match the supplied creative brief and role.')
        for _ in range(2):
            try:
                response_single=request_ollama(model,[
                    {'role':'system','content':single_system},
                    {'role':'user','content':json.dumps({
                        'request':idea, 'role':field,
                        'role_in_japanese':role_labels.get(field,field),
                        'output_shape':{'text':'完成した日本語の表示文言'}
                    },ensure_ascii=False)},
                ],temperature=.15,num_predict=280)
                if not isinstance(response_single,dict):
                    continue
                candidates=[response_single.get('text'),response_single.get(field)]
                values=response_single.get('values')
                if isinstance(values,list):
                    candidates.extend(entry.get('text') for entry in values
                                      if isinstance(entry,dict) and entry.get('id')==slot['id'])
                for candidate in candidates:
                    val=clean_requested_copy(candidate)
                    if val:
                        copy_values[slot['id']]=val
                        break
                if copy_values.get(slot['id']):
                    break
            except (ValueError,KeyError,TypeError,json.JSONDecodeError):
                continue
    unresolved=[role_labels.get(slot['role'],slot['role']) for slot in requested_slots
                if not copy_values.get(slot['id'])]
    if unresolved:
        raise ValueError('Ollamaが表示文字を生成できませんでした: '+', '.join(unresolved)+'。')
    existing={b['text'] for b in blocks}
    for slot in requested_slots:
        value=copy_values[slot['id']]
        if value in existing:continue
        blocks.append({'id':len(blocks),'text':value,'placement':slot['placement']})
        existing.add(value)
    summary=cleaned_english(response.get('summary_caption'),
        'A clean, balanced composition with natural lighting and a cohesive visual style.',1600)
    styles={}
    for ent in response.get('text_styles',[]):
        if isinstance(ent,dict) and type(ent.get('id'))==int:
            styles[ent['id']]=ent.get('style','')
    for b in blocks:
        b['label']='text'
        b['caption']=text_style_for_block(b,styles.get(b['id']))
    named=explicit_subjects(idea)
    subjects=[]
    model_subjects=response.get('subjects',[])
    if not isinstance(model_subjects,list):model_subjects=[]
    for sub in model_subjects[:24]:
        if not isinstance(sub,dict):continue
        typ=sub.get('label')
        if typ not in ('object','character'):continue
        cap=cleaned_english(sub.get('caption'),'A clearly defined requested visual subject with natural lighting.')
        if typ=='object' and PEOPLE_PATTERNS.search(cap):typ='character'
        subjects.append({'label':typ,'caption':cap,'placement':norm_placement(sub.get('placement')),
                         'name':str(sub.get('name') or '')[:90]})
    if named:
        # User-specified images are authoritative, including landscape image elements.
        # Keep model details only when matching the element can be established. No silent omissions.
        grounded=[]
        used=set()
        for index,element in enumerate(named):
            name=element['name']
            matches=[j for j,sub in enumerate(subjects) if j not in used and sub['label']==element['label'] and
                     (name in sub['name'] or any(k in (sub['name']+' '+sub['caption']).lower() for k in
                       (['woman','female','女性'] if '女性' in name else
                        ['man','male','男性'] if '男性' in name else
                        ['car','vehicle','車'] if '車' in name else
                        ['kyoto','scenery','landscape','sakura','cherry','風景'] if ('風景' in name or '桜' in name) else [name.lower()]))) ]
            match=matches[0] if matches else (index if len(subjects)==len(named) and index not in used else None)
            if match is not None:
                used.add(match);sub=subjects[match]
                cap=sub['caption']
            else:cap=fallback_subject_caption(name,element['label'])
            grounded.append({'label':element['label'],'name':name,'caption':cap,
                             'placement':element['placement'] if element['placement']!='auto' else
                             (subjects[match]['placement'] if match is not None else 'auto')})
        subjects=grounded
    elif not subjects: subjects=infer_subjects_from_request(idea)
    specified=COUNT_VISUAL.search(idea)
    if specified and (expected:=count_number(specified.group(1))) is not None and len(subjects)!=expected:
        raise ValueError(f'画像要素は{expected}個指定されていますが、{len(subjects)}個しか解析できませんでした。要素を「1つ目は…」の形式で列挙してください。')
    # Exact user placement has precedence; Ollama may select an alternate side.
    outside=QUOTE_BLOCK_RE.sub(' ',idea)
    explicit_right=re.search(r'右側に[^。\n]{0,65}(女性|人物|女の子|男性|猫|犬|キャラクター)',outside)
    explicit_left=re.search(r'左側に[^。\n]{0,65}(女性|人物|女の子|男性|猫|犬|キャラクター)',outside)
    if explicit_right:
        for s in subjects:
            if s['label']=='character':s['placement']='right'
    elif explicit_left:
        for s in subjects:
            if s['label']=='character':s['placement']='left'
    if not blocks and not subjects:raise ValueError('文字または配置対象を抽出できませんでした。制作指示を具体的にしてください。')
    positioned=layout_items_deterministically(blocks,subjects,w,h, bool(MULTI_PANEL_REQUEST.search(idea)))
    items=[]
    for i in positioned:
        out={'label':i['label'],'caption':i['caption'],'bbox':i['bbox']}
        if i.get('name'):out['tags']=[i['name']]
        if i['label']=='text':out['text']=i['text']
        items.append(out)
    draft={'width':w,'height':h,'bbox_coord_space':'abs','bbox_format':'xyxy',
           'summary':{'caption':summary,'canvas':'single'},'items':items}
    return unify_ai_background(draft,idea)

# UI-selected style is applied only when AI generates a new draft.
# No style changes are made on manual edits, JSON import or Compile.
VISUAL_STYLE_DIRECTIONS = {
    'photo': (
        'Photorealistic professional color photography with real people, authentic skin and material texture, '
        'natural light and lifelike proportions. This is a real photograph, not a drawing, comic or animation.'
    ),
    'anime': (
        'Full-color Japanese anime artwork, distinctive stylized character designs, vibrant controlled color palette, '
        'clear expressive outlines and cel shading. Not monochrome manga, not screentones, not photography.'
    ),
    'illustration': (
        'Full-color polished editorial illustration for a premium travel brochure, warm and harmonious colors, '
        'smooth painterly shading, gentle textured brushwork, refined illustrated shapes. '
        'Not black-and-white manga, not grayscale, not halftone screentones, not comic ink line art, not photography.'
    ),
    'manga': (
        'A hand-drawn Japanese commercial manga cover illustration, created as original two-dimensional ink artwork. '
        'The people have unmistakably drawn manga faces: expressive illustrated eyes, simplified noses and mouths, '
        'stylized facial proportions and clean varied-width pen outlines, rather than photographic portrait faces. '
        'Hair, clothing folds, foliage and mountain scenery are rendered with detailed pen strokes, crosshatching '
        'and printed halftone screentones. Pure black ink, white paper and neutral gray screentones only; '
        'fully black-and-white printed manga art, not a grayscale photograph. '
        'No color, no realistic photographic skin texture, no photographic shading, no 3D rendering.'
    ),
}

STYLE_CONFLICTS = {
    'photo': re.compile(r'(?i)\b(?:anime[- ]style|manga[- ]style|cartoon[- ]style|illustrative|illustration[- ]style|painted illustration|cel[- ]shad(?:ing|ed))\b'),
    'anime': re.compile(r'(?i)\b(?:photorealistic|realistic photography|professional photograph|DSLR photograph|documentary photography|monochrome manga|grayscale screentones)\b'),
    'illustration': re.compile(r'(?i)\b(?:photorealistic|realistic photography|professional photograph|DSLR photograph|monochrome manga|grayscale screentones|black[- ]and[- ]white manga|black ink line art)\b'),
    'manga': re.compile(r'(?i)\b(?:full[- ]color|color photograph|photorealistic|DSLR photograph|vibrant color palette|warm color wash|colorful|brightly colored|pastel colors|painterly illustration)\b'),
}

# Remove color-only styling (not scene/subject descriptions) that conflicts with strict grayscale manga.
# Replace specific palette claims in-place rather than dropping whole paragraphs or elements.
MANGA_COLOR_REPLACEMENTS = [
    (r'(?i)\b(?:soft|rich|bright|warm|cool|harmonious|vibrant|natural|muted|gentle)\s+(?:autumn\s+)?colou?rs?\b', 'neutral grayscale tones'),
    (r'(?i)\b(?:soft|rich|bright|warm|cool|harmonious|vibrant|natural|muted)\s+colou?r\s+palettes?\b', 'neutral grayscale palette'),
    (r'(?i)\b(?:soft|bright|warm|golden|orange|pink|blue)\s+(?:sunset|sunlight|daylight|lighting|glow|light)\b', 'grayscale lighting'),
]

# The palette vocabulary should not leak back through descriptive captions.
MANGA_COLOR_WORDS = re.compile(r'(?i)\b(?:red|orange|yellow|golden|green|blue|purple|pink|brown|beige|crimson|amber|navy|teal|colorful|coloured|colored|multicolored|multicoloured|pastel|sepia)\b')

def manga_monochrome_description(value):
    value = value.strip()
    for pattern, new in MANGA_COLOR_REPLACEMENTS:
        value = re.sub(pattern, new, value)
    # Keep subject/outfit identity and narrative content; color adjectives are not needed for manga.
    value = MANGA_COLOR_WORDS.sub('gray', value)
    value = re.sub(r'(?i)\b(?:warm|vibrant|harmonious|bright)\s+grayscale\s+tones\b', 'neutral grayscale tones', value)
    value = re.sub(r'\s{2,}', ' ', value)
    return value

def apply_selected_visual_style(draft, mode):
    if mode == 'auto':
        return draft
    prefix = VISUAL_STYLE_DIRECTIONS[mode]
    summary = draft['summary']['caption'].strip()
    # Match complete conflicting style phrases; do not remove scenery or the count of subjects.
    sentences = re.split(r'(?<=[.!?])\s+', summary)
    cleaned = ' '.join(x for x in sentences if not STYLE_CONFLICTS[mode].search(x)).strip()
    if mode == 'manga':
        cleaned = manga_monochrome_description(cleaned)
    draft['summary']['caption'] = prefix + (' ' + cleaned if cleaned else '')
    for item in draft['items']:
        if item['label'] in ('character', 'object'):
            description = item['caption'].strip()
            if mode == 'manga':
                description = manga_monochrome_description(description)
            if mode == 'manga':
                # Short per-item drawing instruction; never echo the long style specification
                # five times (doing so can make the model print style words as visible text).
                item_prefix = ('A hand-drawn 2D manga character with stylized illustrated facial features, '
                               'black pen outlines and grayscale screentone shadows. '
                               if item['label'] == 'character' else
                               'A hand-inked 2D Japanese manga object with expressive pen outlines '
                               'and grayscale screentone shadows. ')
            else:
                item_prefix = prefix + ' '
            if not description.startswith(item_prefix):
                item['caption'] = item_prefix + description
    return draft

class Handler(SimpleHTTPRequestHandler):
    def __init__(self,*a,**kw): super().__init__(*a,directory=str(ROOT),**kw)
    def log_message(self,fmt,*args): print('[local]', fmt%args)
    def end_headers(self):
        self.send_header('Cache-Control', 'no-store, no-cache, must-revalidate')
        return super().end_headers()
    def do_GET(self):
        if self.path=='/api/models':
            try: return self.reply(200,{'models':retrieve_models()})
            except Exception as e: return self.reply(200,{'models':[],'warning':'Ollama is not responding: '+str(e)})
        return super().do_GET()
    def do_POST(self):
        if self.path not in ('/api/compile','/api/validate','/api/generate'):return self.reply(404,{'error':'Not found'})
        try:
            n=int(self.headers.get('Content-Length', '0'))
            if not 0<n<MAX_BYTES: raise ValueError('Invalid request size')
            request=json.loads(self.rfile.read(n))
            if self.path=='/api/validate':
                validate_and_compile(request['draft'],allow_empty=True)
                return self.reply(200,{'valid':True})
            if self.path=='/api/compile':
                output=validate_and_compile(request['draft'])
                return self.reply(200,{'prompt':json.dumps(output,ensure_ascii=False,separators=(',',':'))})
            model=request.get('model','')
            idea=request.get('idea','')
            if not isinstance(model,str) or not re.fullmatch(r'[A-Za-z0-9_.:/-]{1,120}',model):raise ValueError('Select a local Ollama model')
            if not isinstance(idea,str) or not idea.strip() or len(idea)>8000:raise ValueError('制作指示を入力してください')
            w=int(request.get('width',1024));h=int(request.get('height',1024))
            if not (256<=w<=4096 and 256<=h<=4096 and w%16==0 and h%16==0):raise ValueError('Invalid canvas size')
            style_mode=request.get('style_mode', 'auto')
            if style_mode not in ('auto', *VISUAL_STYLE_DIRECTIONS): raise ValueError('Invalid visual style')
            draft = generate_semantic_layout(model, idea, w, h)
            apply_selected_visual_style(draft, style_mode)
            # AI-created text must use the same unstyled Description as manual Text Box.
            # This deliberately removes Ollama-supplied color, font, size and emphasis.
            # Existing saved/manual drafts are not modified by compile or validation.
            reset_generated_text_captions_to_manual_default(draft)
            validate_and_compile(draft)
            mode_notice = ([f'漫画モード：{sum(x["label"]=="object" for x in draft["items"])}コマを個別Object Boxにしました。タイトル・セリフは独立Text Boxです。']
                           if MANGA_INTENT.search(idea) else
                           ([f'カタログモード：Object {sum(x["label"]=="object" for x in draft["items"])}件、Text {sum(x["label"]=="text" for x in draft["items"])}件を生成しました。']
                            if CATALOG_INTENT.search(idea) and catalog_requested_assets(idea) else
                            ([f'車両カタログ：写真Object {sum(x["label"]=="object" for x in draft["items"])}件、Text {sum(x["label"]=="text" for x in draft["items"])}件を生成しました。']
                             if single_car_catalog_views(idea) else [])))
            if single_product_catalog_kind(idea):
                mode_notice=['単一商品カタログ：タイトルText・商品Object・説明文Textを独立Boxで生成しました。']
            if MOVIE_POSTER_INTENT.search(idea) and not MANGA_INTENT.search(idea):
                mode_notice=['映画ポスターたたき台：公開情報・出演者・クレジットは制作指示で求めた場合にだけ追加します。']
            if not mode_notice and editable_quantity_targets(idea):
                mode_notice=[f'編集用たたき台：独立Box {len(editable_quantity_targets(idea))}件を生成しました。位置・サイズ・Descriptionは変更できます。']
            return self.reply(200,{'draft':draft, 'warnings':mode_notice+ai_layout_warnings(draft)})
        except (ValueError, KeyError, TypeError, AssertionError, json.JSONDecodeError) as e:
            return self.reply(400,{'error':str(e)})
        except (URLError,HTTPError,TimeoutError) as e:
            return self.reply(503,{'error':'Ollama connection failed: '+str(e)})
        except Exception as e:
            return self.reply(500,{'error':str(e)})
    def reply(self,code,data):
        b=json.dumps(data,ensure_ascii=False).encode('utf-8'); self.send_response(code);self.send_header('Content-Type','application/json; charset=utf-8');self.send_header('Content-Length',str(len(b)));self.end_headers();self.wfile.write(b)

if __name__=='__main__':
    p=argparse.ArgumentParser();p.add_argument('--port',type=int,default=8765);p.add_argument('--open-browser',action='store_true');a=p.parse_args()
    try:
        server=ThreadingHTTPServer(('127.0.0.1',a.port),Handler)
    except OSError as exc:
        print(f'[ERROR] Port {a.port} is already in use. Close the previous MOSS Layout Studio command window and try again. ({exc})')
        raise SystemExit(1)
    url=f'http://127.0.0.1:{a.port}/'
    print(f'MOSS Layout Studio: {url}',flush=True)
    if a.open_browser: webbrowser.open(url)
    try: server.serve_forever()
    except KeyboardInterrupt: pass
    finally: server.server_close()
