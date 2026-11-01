'''
* 명예의 전당 이미지 렌더러 — 우주 배경 위에 명예/우수 멤버 프로필 + 닉네임을 한 장으로 그림 (Pillow)
*   render(title, honor, excel) -> PNG bytes
*   honor / excel : [(닉네임, 프로필 이미지 bytes 또는 None)]
*   폰트: fonts/Pretendard-*.otf (SIL OFL 1.1)
'''
import io
import os
import random

from PIL import Image, ImageDraw, ImageFilter, ImageFont

_DIR = os.path.dirname(__file__)
_FONT_BOLD = os.path.join(_DIR, 'fonts', 'Pretendard-Bold.otf')
_FONT_MED = os.path.join(_DIR, 'fonts', 'Pretendard-Medium.otf')

W = 1200
GOLD = (250, 199, 117)
SKY = (181, 212, 244)
WHITE = (255, 255, 255)
MUTED = (143, 183, 215)


def _font(path, size):
    return ImageFont.truetype(path, size)


def _background(h, seed=7):
    '''짙은 남색 그라데이션 + 성운(흐린 원) + 별'''
    top, bottom = (6, 10, 49), (11, 21, 70)
    bg = Image.new('RGB', (W, h), top)
    px = ImageDraw.Draw(bg)
    for y in range(h):
        t = y / max(1, h - 1)
        px.line([(0, y), (W, y)], fill=tuple(int(a + (b - a) * t) for a, b in zip(top, bottom)))
    rnd = random.Random(seed)
    neb = Image.new('RGBA', (W, h), (0, 0, 0, 0))
    nd = ImageDraw.Draw(neb)
    for colour in ((83, 74, 183, 70), (63, 184, 243, 55), (212, 83, 126, 40), (65, 85, 131, 80)):
        cx, cy = rnd.randint(0, W), rnd.randint(0, h)
        r = rnd.randint(220, 420)
        nd.ellipse([cx - r, cy - r * 0.7, cx + r, cy + r * 0.7], fill=colour)
    neb = neb.filter(ImageFilter.GaussianBlur(90))
    bg = Image.alpha_composite(bg.convert('RGBA'), neb)
    stars = ImageDraw.Draw(bg)
    for _ in range(int(W * h / 2600)):
        x, y = rnd.randint(0, W), rnd.randint(0, h)
        r = rnd.choice((0.6, 0.8, 1, 1, 1.4, 2))
        a = rnd.randint(120, 255)
        stars.ellipse([x - r, y - r, x + r, y + r], fill=(207, 236, 247, a))
    for _ in range(6):   # 반짝이는 큰 별
        x, y = rnd.randint(40, W - 40), rnd.randint(30, h - 30)
        for d, a in ((10, 90), (5, 200)):
            stars.line([(x - d, y), (x + d, y)], fill=(255, 255, 255, a), width=1)
            stars.line([(x, y - d), (x, y + d)], fill=(255, 255, 255, a), width=1)
    return bg


def _avatar(data, size, name):
    '''동그란 프로필 (이미지 없으면 첫 글자)'''
    if data:
        try:
            im = Image.open(io.BytesIO(data)).convert('RGBA').resize((size, size), Image.LANCZOS)
        except Exception:
            im = None
    else:
        im = None
    if im is None:
        im = Image.new('RGBA', (size, size), (65, 85, 131, 255))
        d = ImageDraw.Draw(im)
        f = _font(_FONT_BOLD, size // 2 - 4)
        ch = (name or '?')[0]
        bw = d.textbbox((0, 0), ch, font=f)
        d.text(((size - (bw[2] - bw[0])) / 2 - bw[0], (size - (bw[3] - bw[1])) / 2 - bw[1]), ch,
               font=f, fill=WHITE)
    mask = Image.new('L', (size * 4, size * 4), 0)
    ImageDraw.Draw(mask).ellipse([0, 0, size * 4, size * 4], fill=255)
    mask = mask.resize((size, size), Image.LANCZOS)
    out = Image.new('RGBA', (size, size), (0, 0, 0, 0))
    out.paste(im, (0, 0), mask)
    return out


def _fit(draw, text, font, max_w):
    if draw.textlength(text, font=font) <= max_w:
        return text
    while text and draw.textlength(text + '…', font=font) > max_w:
        text = text[:-1]
    return text + '…'


def _section_height(n, per_row, size):
    rows = max(1, (n + per_row - 1) // per_row)
    return 70 + rows * (size + 80)


def _draw_section(img, y, label, colour, members, size, per_row):
    d = ImageDraw.Draw(img)
    lf = _font(_FONT_BOLD, 34)
    lw = d.textlength(label, font=lf)
    d.text(((W - lw) / 2, y), label, font=lf, fill=colour)
    d.line([((W - lw) / 2 - 90, y + 22), ((W - lw) / 2 - 20, y + 22)], fill=colour + (160,), width=2)
    d.line([((W + lw) / 2 + 20, y + 22), ((W + lw) / 2 + 90, y + 22)], fill=colour + (160,), width=2)
    y += 70
    nf = _font(_FONT_MED, 26 if size >= 140 else 23)
    gap = 44
    if not members:
        t = '이번 달은 없어요'
        d.text(((W - d.textlength(t, font=nf)) / 2, y + 20), t, font=nf, fill=MUTED)
        return y + size + 80
    for i in range(0, len(members), per_row):
        row = members[i:i + per_row]
        total = len(row) * size + (len(row) - 1) * gap
        x = (W - total) / 2
        for name, data in row:
            ring = 6
            glow = Image.new('RGBA', (size + 60, size + 60), (0, 0, 0, 0))
            ImageDraw.Draw(glow).ellipse([18, 18, size + 42, size + 42], fill=colour + (110,))
            glow = glow.filter(ImageFilter.GaussianBlur(14))
            img.alpha_composite(glow, (int(x - 30), int(y - 30)))
            d.ellipse([x - ring, y - ring, x + size + ring, y + size + ring], fill=colour)
            img.alpha_composite(_avatar(data, size, name), (int(x), int(y)))
            label_txt = _fit(d, name, nf, size + gap - 6)
            tw = d.textlength(label_txt, font=nf)
            d.text((x + (size - tw) / 2, y + size + 18), label_txt, font=nf, fill=WHITE)
            x += size + gap
        y += size + 80
    return y


def render(title, honor, excel):
    honor_size, honor_row = 150, 5
    excel_size, excel_row = 116, 6
    h = 230 + _section_height(len(honor), honor_row, honor_size) + 30 + \
        _section_height(len(excel), excel_row, excel_size) + 70
    img = _background(h)
    d = ImageDraw.Draw(img)
    sub = 'MARKER  HALL  OF  FAME'
    sf = _font(_FONT_MED, 24)
    d.text(((W - d.textlength(sub, font=sf)) / 2, 70), sub, font=sf, fill=MUTED)
    tf = _font(_FONT_BOLD, 56)
    d.text(((W - d.textlength(title, font=tf)) / 2, 108), title, font=tf, fill=WHITE)
    y = 230
    y = _draw_section(img, y, '명예', GOLD, honor, honor_size, honor_row) + 30
    y = _draw_section(img, y, '우수', SKY, excel, excel_size, excel_row)
    buf = io.BytesIO()
    img.convert('RGB').save(buf, 'PNG', optimize=True)
    return buf.getvalue()
