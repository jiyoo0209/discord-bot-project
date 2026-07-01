'''
* 대시보드 (길퀘 남은 횟수 / 누적 경고 / 주간 랭킹 / 명예·우수 예상)
* author HDG
* date   2026.07.01
'''
import discord
from discord import app_commands
from discord.ext import commands
from datetime import date, timedelta
from collections import defaultdict
from sheet.sheet import get_dashboard_records

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
    saturday = monday + timedelta(days=6)
    return int(monday.strftime('%Y%m%d')), int(saturday.strftime('%Y%m%d'))

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
    '''user_point 1회 순회로 3개 집계 반환.
    quest_cnt   : 이번주 길퀘 횟수 (capture_yn=N 만)
    week_points : 이번주 포인트 합 (길퀘+점령 전부)
    refl_points : reflection_yn=N 포인트 합 (다음 달 예상용)'''
    quest_cnt, week_points, refl_points = defaultdict(int), defaultdict(int), defaultdict(int)
    for r in points:
        name, d, pt, capture, reflect = (r + ['', '', '', '', ''])[:5]
        if not name:
            continue
        d, pt = _int(d), _int(pt)
        if wk_start <= d <= wk_end:
            week_points[name] += pt                # 점령 점수도 주간 점수엔 포함
            if capture.strip().upper() == 'N':     # 점령(Y)은 제외 길퀘 횟수에서 제한
                quest_cnt[name] += 1
        if reflect.strip().upper() == 'N':
            refl_points[name] += pt
    return quest_cnt, week_points, refl_points

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
    medals = {1: '🥇', 2: '🥈', 3: '🥉'}    # 상위 3명 메달, 그 외 'N위'
    lines, prev, place = [], None, 0
    for pt, n in ranking:
        if pt != prev:                 # 점수 바뀔 때만 순위 +1 (동점은 같은 순위)
            place += 1
            prev = pt
        badge = medals.get(place, f'{place}위')
        lines.append(f'{badge} {n} ({pt})')
    return '\n\n'.join(lines) or '- 기록 없음'

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
    return ('🏅 명예\n' + ('\n'.join(f'- {n}' for n in honor) or '- 없음') +
            '\n⭐ 우수\n' + ('\n'.join(f'- {n}' for n in excel) or '- 없음'))

# ======================================================================
# 출력
# ======================================================================
def build_sections(today=None, records=None):
    # 4개 섹션 문자열 (sec1..sec4) 반환
    users, points, denies, ranks = records if records is not None else get_dashboard_records()
    wk_start, wk_end = get_week_range(today)

    rank_of, warning_of = load_roster(users)
    rank_cnt = {r[0]: _int(r[1]) for r in ranks if r and r[0]}
    quest_cnt, week_points, refl_points = collect_points(points, wk_start, wk_end)
    denied = this_week_denied(denies, wk_start, wk_end)

    sec1 = section_remaining(rank_of, quest_cnt, denied)
    sec2 = section_warnings(warning_of)
    sec3 = section_ranking(rank_of, week_points)
    sec4 = section_forecast(rank_of, refl_points, rank_cnt)
    return sec1, sec2, sec3, sec4

def build_embed(today=None, records=None):
    s1, s2, s3, s4 = build_sections(today, records)
    wk_start, wk_end = get_week_range(today)

    embed = discord.Embed(title='📊 길퀘 대시보드', color=0x7ABDFF)
    divider = '─' * 18   # 가로 구분선 문자
    # 2x2 + 구분선: 두 칸(inline) 뒤 전체폭 구분선(inline=False)이 줄바꿈까지 담당
    embed.add_field(name='🗡️ 길퀘 남은 횟수', value=s1, inline=True)
    embed.add_field(name='⚠️ 누적 경고', value=s2, inline=True)
    embed.add_field(name='\u200b', value=divider, inline=False)      # 1행/2행 사이 구분선
    embed.add_field(name='🏆 이번주 길퀘포인트 순위', value=s3, inline=True)
    embed.add_field(name='🎖️ 다음 달 명예/우수 예상', value=s4, inline=True)
    embed.set_footer(text=f'매일 00시 업데이트 예정')
    return embed

# ======================================================================
# 페이지네이션 (섹션당 1페이지, 버튼으로 넘기기)
# ======================================================================
def build_pages(today=None, records=None):
    '''섹션별 임베드 4개(리스트) 반환 — 페이지 넘김용'''
    s1, s2, s3, s4 = build_sections(today, records)
    wk_start, wk_end = get_week_range(today)
    footer = f'매일 00시 업데이트 예정'
    data = [
        ('🗡️ 길퀘 남은 횟수', s1),
        ('🏆 이번주 길퀘포인트 순위', s3),
        ('🎖️ 다음 달 명예/우수 예상', s4),
        ('⚠️ 누적 경고', s2),
    ]
    pages = []
    for i, (title, body) in enumerate(data):
        e = discord.Embed(title=title, description=body, color=0x7ABDFF)
        e.set_footer(text=f'{i + 1} / {len(data)}   |   {footer}')
        pages.append(e)
    return pages


class DashboardView(discord.ui.View):
    '''◀ ▶ 버튼으로 페이지를 넘기는 View. 명령어 사용자만 조작 가능.'''
    def __init__(self, pages, timeout=120):
        super().__init__(timeout=timeout)
        self.pages = pages
        self.index = 0
        self.message = None
        self._sync()

    def _sync(self):
        # 현재 페이지에 맞춰 버튼 상태/표시 갱신
        self.prev_page.disabled = self.index == 0
        self.next_page.disabled = self.index == len(self.pages) - 1
        self.indicator.label = f'{self.index + 1} / {len(self.pages)}'

    async def _show(self, interaction):
        self._sync()
        await interaction.response.edit_message(embed=self.pages[self.index], view=self)

    @discord.ui.button(label='◀', style=discord.ButtonStyle.secondary)
    async def prev_page(self, interaction: discord.Interaction, button: discord.ui.Button):
        self.index = max(0, self.index - 1)
        await self._show(interaction)

    @discord.ui.button(label='1 / 4', style=discord.ButtonStyle.secondary, disabled=True)
    async def indicator(self, interaction: discord.Interaction, button: discord.ui.Button):
        pass  # 표시 전용 (비활성)

    @discord.ui.button(label='▶', style=discord.ButtonStyle.primary)
    async def next_page(self, interaction: discord.Interaction, button: discord.ui.Button):
        self.index = min(len(self.pages) - 1, self.index + 1)
        await self._show(interaction)

    async def on_timeout(self):
        # 타임아웃되면 버튼 비활성화
        for child in self.children:
            child.disabled = True
        if self.message:
            try:
                await self.message.edit(view=self)
            except discord.HTTPException:
                pass


class Dashboard(commands.Cog):
    def __init__(self, bot):
        self.bot = bot

    @app_commands.command(name='대시보드', description='길퀘 현황 대시보드 조회')
    async def dashboard(self, interaction: discord.Interaction):
        await interaction.response.defer()  # 처리 중 메시지 표시
        try:
            pages = build_pages()
            view = DashboardView(pages, interaction.user.id)
            view.message = await interaction.followup.send(embed=pages[0], view=view)
        except Exception as e:
            await interaction.followup.send(f'오류: {e}')

async def setup(bot):
    await bot.add_cog(Dashboard(bot))