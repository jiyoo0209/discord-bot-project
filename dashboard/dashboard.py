'''
* 대시보드 (길퀘 남은 횟수 / 누적 경고 / 주간 랭킹 / 명예·우수 예상)
* author HDG
* date   2026.07.01
'''
import re
import asyncio
import discord
from discord import app_commands
from discord.ext import commands
from datetime import date, timedelta
from collections import defaultdict
from sheet.sheet import get_dashboard_records, get_worksheet, _write_lock
from roleSetting.roleSetting import has_role
from config.guild_config import set_setting, get_setting

# ---- 설정값 ----
WEEKLY_QUEST_LIMIT = 2            # 주간 길퀘(=capture_yn N) 최대 횟수
RANK_EXCLUDE = ('길마', '서마')   # 순위/예상에서 제외할 등급
RANK_INACTIVE = '탈퇴'           # 비활성(탈퇴) 등급


# ---- 유틸 ----
def _int(v, default=0):
    '''문자열/빈칸을 안전하게 int로 변환 ('5'->5, ''->0)'''
    try:
        return int(str(v).strip())
    except (ValueError, AttributeError):
        return default

def get_week_range(today=None):
    if today is None:
        today = date.today()
    monday = today - timedelta(days=today.weekday())
    sunday = monday + timedelta(days=6)   # 월(weekday 0) + 6 = 일요일 (월~일 집계)
    return int(monday.strftime('%Y%m%d')), int(sunday.strftime('%Y%m%d'))


def get_month_range(today=None):
    '''이번 달 1일 ~ 말일 (YYYYMMDD int)'''
    if today is None:
        today = date.today()
    first = today.replace(day=1)
    nxt = date(today.year + (today.month == 12), (today.month % 12) + 1, 1)   # 다음 달 1일
    last = nxt - timedelta(days=1)
    return int(first.strftime('%Y%m%d')), int(last.strftime('%Y%m%d'))


def _range_date(tok):
    '''구간 입력 토큰(MMDD/YYYYMMDD) → YYYYMMDD int. MMDD 는 '올해' 기준(소급 아님).'''
    digits = ''.join(c for c in tok if c.isdigit())
    if len(digits) == 8:
        y, m, d = int(digits[:4]), int(digits[4:6]), int(digits[6:8])
    elif len(digits) == 4:
        y, m, d = date.today().year, int(digits[:2]), int(digits[2:4])
    else:
        return None
    try:
        date(y, m, d)
    except ValueError:
        return None
    return y * 10000 + m * 100 + d


def parse_summary_input(s):
    '''요약 구간 입력 → spec 문자열 반환. 'week'|'month'|'YYYYMMDD~YYYYMMDD', 형식오류면 None.
       빈칸/'주' → week, '달'/'월' → month, 그 외 두 날짜(~ 또는 공백 구분) → 커스텀.'''
    s = (s or '').strip()
    if not s or '주' in s:
        return 'week'
    if '달' in s or '월' in s:
        return 'month'
    parts = [p for p in re.split(r'[~\s]+', s) if p]
    if len(parts) != 2:
        return None
    a, b = _range_date(parts[0]), _range_date(parts[1])
    if a is None or b is None:
        return None
    lo, hi = sorted((a, b))
    return f'{lo}~{hi}'


def _resolve_summary_spec(spec):
    '''spec → (date_range 또는 None, 라벨). week 는 date_range=None(기존 누적 예상 유지).'''
    if spec == 'month':
        st, en = get_month_range()
        return (st, en), f'이번 달  {st} ~ {en}'
    if spec and spec != 'week' and '~' in spec:
        a, b = spec.split('~')
        return (int(a), int(b)), f'{a} ~ {b}'
    st, en = get_week_range()
    return None, f'이번 주  {st} ~ {en}  (월~일)'

# ======================================================================
# 데이터
# ======================================================================
def load_roster(users):
    '''활동 길드원만: (rank_of, warning_of) 반환. 탈퇴 제외.'''
    rank_of, warning_of = {}, {}
    for r in users:
        name, warning, rank = (r + ['', '', ''])[:3]   # 짧은 행도 안전하게 언팩
        if not name or rank == RANK_INACTIVE:
            continue
        rank_of[name] = rank
        warning_of[name] = _int(warning)
    return rank_of, warning_of

def collect_points(points, wk_start, wk_end):
    '''user_point 1회 순회로 집계 반환. (여기서 wk_start~wk_end 는 '집계 구간')
    quest_cnt   : 구간 내 길퀘 횟수 (capture_yn=N 만) — 남은 횟수용
    week_points : 구간 내 길퀘 포인트 합 (capture_yn=N) — 순위용
    refl_points : reflection_yn=N 포인트 합 (날짜 무관 누적) — 기본 예상용
    range_total : 구간 내 총 포인트 합 (길퀘+점령) — 구간 지정 예상용'''
    quest_cnt, week_points, refl_points, range_total = (
        defaultdict(int), defaultdict(int), defaultdict(int), defaultdict(int))
    for r in points:
        name, d, pt, capture, reflect = (r + ['', '', '', '', ''])[:5]
        if not name:
            continue
        d, pt = _int(d), _int(pt)
        if wk_start <= d <= wk_end:
            range_total[name] += pt                # 길퀘+점령 전부
            if capture.strip().upper() == 'N':     # 순위/남은횟수는 점령(Y) 제외
                week_points[name] += pt
                quest_cnt[name] += 1
        if reflect.strip().upper() == 'N':
            refl_points[name] += pt
    return quest_cnt, week_points, refl_points, range_total

def this_week_denied(denies, wk_start, wk_end):
    '''이번주 길퀘 불가 사유를 등록한 사람 집합'''
    denied = set()
    for r in denies:
        name, d = (r + ['', ''])[:2]
        if name and wk_start <= _int(d) <= wk_end:
            denied.add(name)
    return denied

# ======================================================================
# 섹션별 함수
# ======================================================================
def section_remaining(rank_of, quest_cnt, denied):
    '''1. 길퀘 남은 횟수 — 전체 길드원, 남은>0, 이번주 불가사유자 제외'''
    remain = []
    for name in rank_of:
        if name in denied:
            continue
        left = WEEKLY_QUEST_LIMIT - quest_cnt.get(name, 0)
        if left > 0:
            remain.append((left, name))
    remain.sort(key=lambda x: (-x[0], x[1]))          # 남은 많은 순, 이름 순
    return '\n'.join(f'- {n} ({c})' for c, n in remain) or '- 모두 완료'

def section_warnings(warning_of):
    '''2. 누적 경고 수 — warning_point > 0, 많은 순'''
    warns = sorted(((w, n) for n, w in warning_of.items() if w > 0),
                   key=lambda x: (-x[0], x[1]))
    return '\n'.join(f'- {n} {w}회' for w, n in warns) or '- 없음'

def section_ranking(rank_of, week_points):
    '''3. 이번주 길퀘포인트 순위 — 길마/서마 제외, 동점=같은 순위'''
    ranking = sorted(
        ((p, n) for n, p in week_points.items()
         if p > 0 and n in rank_of and rank_of[n] not in RANK_EXCLUDE),
        key=lambda x: (-x[0], x[1]))
    lines, prev, place = [], None, 0
    for pt, n in ranking:
        if pt != prev:                 # 점수 바뀔 때만 순위 +1 (동점은 같은 순위)
            place += 1
            prev = pt
        lines.append(f'{place}위 {n} ({pt})')
    return '\n'.join(lines) or '- 기록 없음'

def section_forecast(rank_of, refl_points, rank_cnt):
    '''4. 다음 달 명예/우수 예상 — reflection_yn=N 포인트 합, 길마/서마 제외.
    명예/우수 인원수는 user_rank의 rank_cnt 만큼 자름.'''
    honor_n = rank_cnt.get('명예', 0)
    excel_n = rank_cnt.get('우수', 0)
    cand = sorted(
        ((p, n) for n, p in refl_points.items()
         if p > 0 and n in rank_of and rank_of[n] not in RANK_EXCLUDE),
        key=lambda x: (-x[0], x[1]))
    honor = [n for _, n in cand[:honor_n]]
    excel = [n for _, n in cand[honor_n:honor_n + excel_n]]
    return ('명예\n' + ('\n'.join(f'- {n}' for n in honor) or '- 없음') +
            '\n우수\n' + ('\n'.join(f'- {n}' for n in excel) or '- 없음'))

# ======================================================================
# 출력
# ======================================================================
def build_sections(today=None, records=None, date_range=None):
    '''4개 섹션 문자열 (sec1..sec4) 반환.
    date_range=(start,end) 를 주면 **순위·명예우수 예상**을 그 구간으로 집계한다.
    **남은 횟수·경고·등급현황은 항상 이번 주/현재** (상태 성격 — 구간 무관).
    date_range=None(대시보드 기본)이면 순위=이번주, 예상=reflection 누적 (기존 동작 유지).'''
    users, points, denies, ranks = records if records is not None else get_dashboard_records()
    wk_start, wk_end = get_week_range(today)                       # 남은 횟수/제외용 = 항상 이번 주
    agg_start, agg_end = date_range if date_range else (wk_start, wk_end)   # 순위/예상용 = 집계 구간

    rank_of, warning_of = load_roster(users)
    rank_cnt = {r[0]: _int(r[1]) for r in ranks if r and r[0]}
    wk_quest_cnt, _wp, refl_points, _rt = collect_points(points, wk_start, wk_end)
    _qc, agg_week_points, _rp, agg_total = collect_points(points, agg_start, agg_end)
    denied = this_week_denied(denies, wk_start, wk_end)

    sec1 = section_remaining(rank_of, wk_quest_cnt, denied)        # 이번 주 남은 횟수
    sec2 = section_warnings(warning_of)                            # 현재 누적 경고
    sec3 = section_ranking(rank_of, agg_week_points)              # 구간 길퀘포인트 순위
    forecast_pts = agg_total if date_range else refl_points        # 구간 지정 시 구간 총점, 아니면 누적
    sec4 = section_forecast(rank_of, forecast_pts, rank_cnt)
    return sec1, sec2, sec3, sec4

def _cap(value):
    '''Discord embed 필드 값은 1024자 상한 — 넘으면 잘라서 전송 실패(빈 대시보드) 방지'''
    if len(value) <= 1024:
        return value
    return value[:1000].rstrip() + '\n… (이하 생략)'


def build_embed(today=None, records=None):
    s1, s2, s3, s4 = build_sections(today, records)
    wk_start, wk_end = get_week_range(today)

    embed = discord.Embed(title='길퀘 대시보드', color=0x5865F2)
    embed.add_field(name='길퀘 남은 횟수', value=_cap(s1), inline=False)
    embed.add_field(name='누적 경고', value=_cap(s2), inline=False)
    embed.add_field(name='이번주 길퀘포인트 순위', value=_cap(s3), inline=False)
    embed.add_field(name='다음 달 명예/우수 예상', value=_cap(s4), inline=False)
    embed.set_footer(text=f'집계 주간 {wk_start} ~ {wk_end} (월~일)')
    return embed

# ======================================================================
# 사람용 "요약" 시트 탭 — 원본 데이터 탭은 봇이 읽으므로 못 바꾸고, 별도 한글 탭에 사람이
#   읽기 쉬운 현황을 봇이 자동으로 채운다. build_sections(검증된 로직) 재사용.
# ======================================================================
def _rank_line(records):
    _u, _p, _d, ranks = records
    cnt = {r[0]: (r[1] if len(r) > 1 else '0') for r in ranks if r and r[0]}
    order = ['길마', '서마', '명예', '우수', '일반', '전체']
    return '   '.join(f'{k} {cnt[k]}명' for k in order if k in cnt) or '(등급 데이터 없음)'


# 요약 탭 색 (RGB 0~1)
_SUM_NAVY = {'red': 0.17, 'green': 0.24, 'blue': 0.47}
_SUM_BLUE = {'red': 0.36, 'green': 0.49, 'blue': 0.80}
_SUM_GRAY = {'red': 0.93, 'green': 0.94, 'blue': 0.97}
_SUM_WHITE = {'red': 1, 'green': 1, 'blue': 1}
_SUM_INK = {'red': 0.15, 'green': 0.15, 'blue': 0.15}


def _beautify_summary(ws, header_rows):
    '''요약 탭 서식 — 제목 밴드 + 섹션 헤더 색 + A열 넓힘 + 상단 2행 고정.
       실패해도 데이터엔 영향 없게 write_summary_sheet 에서 try 로 감싼다.'''
    fmts = [
        # ① 전체 리셋(이전 실행 서식 잔재 제거) → 아래 스타일이 순서대로 덮어씀
        {'range': 'A1:H200', 'format': {'backgroundColor': _SUM_WHITE,
            'textFormat': {'bold': False, 'italic': False, 'fontSize': 10, 'foregroundColor': _SUM_INK}}},
        # ② 제목 밴드 (짙은 남색 + 흰 굵은 글씨)
        {'range': 'A1:H1', 'format': {'backgroundColor': _SUM_NAVY, 'verticalAlignment': 'MIDDLE',
            'textFormat': {'bold': True, 'fontSize': 13, 'foregroundColor': _SUM_WHITE}}},
        # ③ 부제 (연회색 + 작은 이탤릭)
        {'range': 'A2:H2', 'format': {'backgroundColor': _SUM_GRAY,
            'textFormat': {'italic': True, 'fontSize': 9, 'foregroundColor': _SUM_INK}}},
    ]
    # ④ 각 ■ 섹션 헤더 (파랑 + 흰 굵은 글씨)
    for r in header_rows:
        fmts.append({'range': f'A{r}:H{r}', 'format': {'backgroundColor': _SUM_BLUE,
            'textFormat': {'bold': True, 'foregroundColor': _SUM_WHITE}}})
    ws.batch_format(fmts)
    ws.freeze(rows=2)
    # A열 넓게 (섹션 텍스트가 길다)
    ws.spreadsheet.batch_update({'requests': [{
        'updateDimensionProperties': {
            'range': {'sheetId': ws.id, 'dimension': 'COLUMNS', 'startIndex': 0, 'endIndex': 1},
            'properties': {'pixelSize': 340}, 'fields': 'pixelSize'}}]})


def write_summary_sheet(spec=None):
    '''요약 탭을 최신 현황으로 다시 씀 (clear + append + 서식). 값만 쓰므로 안전.
       spec(집계 구간)을 안 주면 저장된 설정('_summary_range', 기본 week)을 읽는다 →
       배치가 매시간 불러도 사용자가 지정한 구간이 유지된다. 순위·예상만 구간 적용.'''
    if spec is None:
        spec = get_setting('_global', '_summary_range', 'week')
    date_range, label = _resolve_summary_spec(spec)
    records = get_dashboard_records()
    s1, s2, s3, s4 = build_sections(records=records, date_range=date_range)
    rows = [['📋  길퀘 관리 요약'],
            [f'집계 구간  {label}   ·   봇이 자동으로 채웁니다 · 직접 수정하지 마세요'],
            ['']]
    header_rows = []   # 섹션 제목이 놓이는 1-based 행 (서식용)

    def section(title, body):
        header_rows.append(len(rows) + 1)
        rows.append([title])
        rows.extend([ln] for ln in body.split('\n'))
        rows.append([''])

    section('■ 등급 현황', _rank_line(records))
    section('■ 길퀘포인트 순위 (집계 구간)', s3)
    section('■ 길퀘 남은 횟수 (이번 주)', s1)
    section('■ 누적 경고', s2)
    section('■ 명예/우수 예상 (집계 구간)', s4)

    ws = get_worksheet('요약')
    # clear+append 는 원자적이지 않다 → 동시 실행(/요약 + 배치) 시 중복/뒤섞임.
    #   시트 전역 쓰기 락으로 직렬화한다.
    with _write_lock:
        ws.clear()
        ws.append_rows(rows, value_input_option='RAW')
    try:
        _beautify_summary(ws, header_rows)
    except Exception as e:
        print(f'[summary] 서식 건너뜀: {e}')
    return True


def beautify_headers():
    '''데이터 4탭 헤더를 1행 고정 + 굵게 + 파란 배경/흰 글씨로. 값은 안 바꿈.'''
    hdr_fmt = {'backgroundColor': {'red': 0.22, 'green': 0.28, 'blue': 0.55},
               'textFormat': {'bold': True,
                              'foregroundColor': {'red': 1, 'green': 1, 'blue': 1}},
               'horizontalAlignment': 'CENTER'}
    for name in ('user', 'user_rank', 'user_point', 'quest_deny_reason'):
        try:
            ws = get_worksheet(name)
            ws.freeze(rows=1)
            ws.format('1:1', hdr_fmt)
        except Exception as e:
            print(f'[beautify] {name} 서식 오류: {e}')


@app_commands.guild_only()
class Dashboard(commands.Cog):
    def __init__(self, bot):
        self.bot = bot

    @app_commands.command(name='대시보드', description='길퀘 현황 대시보드 조회')
    async def dashboard(self, interaction: discord.Interaction):
        # 시트 4장 조회는 3초를 넘길 수 있어 먼저 defer, 블로킹 I/O 는 스레드로 분리
        # ephemeral — 조회 결과가 채널을 지저분하게 하지 않도록 본인만 보이게
        # 버튼 '대시보드'와 동일한 ◀▶ 카드로 통일 (지역 import 로 panel↔dashboard 순환 방지)
        await interaction.response.defer(ephemeral=True)
        try:
            from panel.panel import DashboardCard
            records = await asyncio.to_thread(get_dashboard_records)
            await interaction.followup.send(view=DashboardCard(0, records), ephemeral=True)
        except Exception as e:
            await interaction.followup.send(f'오류: {e}', ephemeral=True)

    # 이 채널에 대시보드를 상주시키고 1시간마다 자동 갱신 (배치가 이 메시지를 edit)
    #   /대시보드 와 동일한 ◀▶ 페이지 카드로 게시 — 임베드/카드 두 모양이 섞여 혼란스럽지 않게 통일.
    #   ⚠️ Discord 는 보낸 뒤 일반 메시지 ↔ V2 카드 전환(edit)이 불가 → 이전 설치분은 삭제 후 새로 게시.
    @app_commands.command(name='대시보드설치', description='(길마/서마) 이 채널에 자동 갱신 대시보드 설치')
    async def install_dashboard(self, interaction: discord.Interaction):
        if not (has_role(interaction, '길마') or has_role(interaction, '서마')):
            await interaction.response.send_message('길마/서마만 사용 가능합니다!', ephemeral=True)
            return
        await interaction.response.defer(ephemeral=True)
        # 이전 설치 메시지 정리 (고아 방지 — 구버전 embed 설치분 포함)
        old_ch = get_setting(interaction.guild.id, '_dash_ch')
        old_msg = get_setting(interaction.guild.id, '_dash_msg')
        if old_ch and old_msg:
            try:
                ch = (interaction.guild.get_channel(int(old_ch))
                      or await self.bot.fetch_channel(int(old_ch)))
                old = await ch.fetch_message(int(old_msg))
                await old.delete()
            except Exception:
                pass
        try:
            from panel.panel import DashboardCard   # 지역 import — panel↔dashboard 순환 방지
            records = await asyncio.to_thread(get_dashboard_records)
            msg = await interaction.channel.send(view=DashboardCard(0, records, live=True))
        except discord.Forbidden:
            await interaction.followup.send(
                '이 채널에 메시지를 보낼 권한이 없습니다. 봇 권한을 확인하세요.', ephemeral=True)
            return
        except Exception as e:
            await interaction.followup.send(f'설치 실패: {e}', ephemeral=True)
            return
        set_setting(interaction.guild.id, '_dash_ch', interaction.channel.id)
        set_setting(interaction.guild.id, '_dash_msg', msg.id)
        await interaction.followup.send(
            '페이지 대시보드를 설치했습니다. ◀▶ 로 넘겨 보고, 1시간마다 자동 갱신됩니다.', ephemeral=True)

    @app_commands.command(name='요약', description='(길마/서마) 시트 요약 탭 갱신 + 헤더 보기 좋게 정리')
    async def summary(self, interaction: discord.Interaction):
        if not (has_role(interaction, '길마') or has_role(interaction, '서마')):
            await interaction.response.send_message('길마/서마만 사용 가능합니다!', ephemeral=True)
            return
        await interaction.response.defer(ephemeral=True)
        try:
            await asyncio.to_thread(write_summary_sheet)
            await asyncio.to_thread(beautify_headers)
        except Exception as e:
            await interaction.followup.send(f'오류: {e}', ephemeral=True)
            return
        await interaction.followup.send(
            '시트 **요약** 탭을 갱신하고 헤더를 정리했습니다. 길드원은 요약 탭만 보면 돼요.', ephemeral=True)


async def setup(bot):
    await bot.add_cog(Dashboard(bot))