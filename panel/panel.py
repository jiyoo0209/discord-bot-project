'''
* 인터랙티브 대시보드 패널 — ◀ ▶ 버튼으로 4개 섹션 페이지네이션 (알로항/마커길퀘봇 스타일)
* author ENI
* date   2026.07.01
*
* 영속 View: 버튼에 고정 custom_id 를 주고 bot.add_view 로 등록하면 봇 재시작 후에도 동작한다.
* 페이지 상태(1/4 등)는 View 인스턴스가 아니라 *메시지 footer* 에 담아 stateless 하게 처리 —
* 그래서 한 개의 등록된 View 가 모든 패널 메시지에 대응한다.
'''
import re
import asyncio
import discord
from discord import app_commands
from discord.ext import commands

from dashboard.dashboard import (
    build_sections, build_embed, WEEKLY_QUEST_LIMIT, write_summary_sheet, beautify_headers,
    parse_summary_input,
)
from sheet.sheet import (
    get_user_status, get_user_by_discord, add_deny_reason, init_sheets, get_dashboard_records,
    add_user, add_points, list_active_users, change_user_rank, VALID_RANKS,
    calc_honor_excellent, get_incomplete_members, update_rank_cnt, get_spreadsheet_url,
    process_weekly_warnings,
)
from roleSetting.roleSetting import has_role
from config.guild_config import (
    set_setting, get_setting, get_role_id, set_role_id, get_guild_config, ROLE_KEYS,
)
from util.dates import today_int, this_saturday, parse_date_arg

# 페이지 정의: build_sections()가 (남은횟수, 경고, 순위, 명예우수) 를 주므로 idx 로 매핑
PAGES = [
    {'emoji': '🗡️', 'title': '길퀘 남은 횟수', 'idx': 0},
    {'emoji': '🏆', 'title': '이번주 길퀘포인트 순위', 'idx': 2, 'medals': True},
    {'emoji': '⭐', 'title': '다음 달 명예/우수 예상', 'idx': 3},
    {'emoji': '⚠️', 'title': '누적 경고', 'idx': 1},
]


def _cap_desc(v):
    '''TextDisplay 상한 — 카드 전체(제목+본문+푸터) 4000 여유 위해 본문은 3600 에서 자름'''
    return v if len(v) <= 3600 else v[:3570].rstrip() + '\n… (이하 생략)'


def _dash_body(page, records):
    '''해당 페이지의 (제목emoji, 제목, 본문). 순위 페이지는 1~3위에 메달.'''
    page = page % len(PAGES)
    p = PAGES[page]
    body = build_sections(records=records)[p['idx']]
    if p.get('medals'):
        body = re.sub(r'(?m)^1위 ', '🥇 ', body)   # 줄머리 앵커 — '11위' 안 뭉갬
        body = re.sub(r'(?m)^2위 ', '🥈 ', body)
        body = re.sub(r'(?m)^3위 ', '🥉 ', body)
    return p['emoji'], p['title'], _cap_desc(body)


# 패널 메시지별 현재 페이지 (인메모리). Components V2 카드는 footer 가 없어 dict 로만 추적.
#   연타/동시 클릭은 await 이전 동기 갱신으로 유실 방지. 재시작 시 0페이지부터.
_panel_page = {}


def current_page(message):
    return _panel_page.get(message.id, 0)


# ◀▶ 페이지 버튼 (ActionRow). 콜백은 부모 카드의 페이지를 넘긴다.
class DashRow(discord.ui.ActionRow):
    @discord.ui.button(label='◀', style=discord.ButtonStyle.secondary, custom_id='gq_panel_prev')
    async def prev(self, interaction: discord.Interaction, button: discord.ui.Button):
        await _flip_card(interaction, -1)

    @discord.ui.button(label='▶', style=discord.ButtonStyle.primary, custom_id='gq_panel_next')
    async def next(self, interaction: discord.Interaction, button: discord.ui.Button):
        await _flip_card(interaction, +1)


# 대시보드 페이지 = Components V2 카드. records 없으면(dispatch 등록용) 버튼만 구성(I/O 없음).
#   live=True: /대시보드설치 로 채널에 상주하는 카드 (배치가 매시간 edit) → '자동 갱신' 표기.
#   live=False: /대시보드·버튼의 ephemeral 카드 (배치가 못 건드림) → 표기 안 함.
class DashboardCard(discord.ui.LayoutView):
    def __init__(self, page: int = 0, records=None, live: bool = False):
        super().__init__(timeout=None)
        if records is None:
            self.add_item(discord.ui.Container(DashRow()))
            return
        emoji, title, body = _dash_body(page, records)
        tail = ' · 1시간마다 자동 갱신' if live else ''
        self.add_item(discord.ui.Container(
            discord.ui.TextDisplay(f'## {emoji} {title}'),
            discord.ui.TextDisplay(body or '-'),
            discord.ui.Separator(),
            DashRow(),
            discord.ui.TextDisplay(f'-# {page % len(PAGES) + 1} / {len(PAGES)}{tail}'),
            accent_colour=discord.Colour(0x5865F2),
        ))


async def _flip_card(interaction: discord.Interaction, delta: int):
    mid = interaction.message.id
    cur = _panel_page.get(mid, 0)
    new = (cur + delta) % len(PAGES)
    _panel_page[mid] = new   # await 이전 동기 갱신
    try:
        await interaction.response.defer()  # 컴포넌트 업데이트 defer
        records = await asyncio.to_thread(get_dashboard_records)
        # 상주(설치형) 카드는 공개 메시지, ephemeral 카드는 본인용 — footer 문구 유지
        live = not interaction.message.flags.ephemeral
        await interaction.edit_original_response(view=DashboardCard(new, records, live))
    except Exception as e:
        _panel_page[mid] = cur   # 실패 시 되돌림
        print(f'[panel] flip 오류: {e}')


HELP_TEXT = (
    '**📋 길퀘봇 명령어**\n'
    '• `/가입 <닉> <@유저>` · `/탈퇴 <닉>` · `/등급변경 <닉> <등급>` — 길드원 관리\n'
    '• `/증가 · /감소 <등급> <숫자>` — 등급 인원수\n'
    '• `/체크 <점수> <닉들>` · `/점령 <닉들>` — 점수/점령\n'
    '• `/명예우수계산` — 명예/우수 산정\n'
    '• `/대시보드` · `/셋업` · `/초기화` · `/요약` — 현황/설정\n'
    '아래 버튼: 📊 대시보드 · 🙋 내 현황 · 📄 시트 · ❓ 도움말\n'
    '관리자행(길마·서마): 가입 · 탈퇴 · 체크 · 점령 · 관리\n'
    '관리 → 등급 변경 · 역할 관리 · 시트 요약 · 길퀘불가 · 인원수 증감 · 명예우수계산 · 리마인드 · 경고처리'
)


def build_welcome_embed(guild=None):
    # 인라인 필드로 빽빽하게 나열하지 않고, 정렬된 블록쿼트 한 덩어리로 (모던·가독성)
    embed = discord.Embed(
        title='길퀘 관리 센터',
        description=(
            '길드 퀘스트 현황을 한눈에 확인하는 공간이에요. 아래 버튼으로 바로 이용하세요.\n'
            '​'  # 얇은 여백
        ),
        color=0x5865F2)
    embed.add_field(
        name='​',
        value=(
            '> 📊  **대시보드**  ·  전체 현황 (순위·남은 횟수·경고·예상)\n'
            '> 🙋  **내 현황**  ·  내 등급·점수·남은 길퀘\n'
            '> 🚫  **길퀘불가**  ·  이번주 불가 사유 등록\n'
            '> ❓  **도움말**  ·  명령어 안내'
        ),
        inline=False)
    if guild is not None and getattr(guild, 'icon', None):
        embed.set_thumbnail(url=guild.icon.url)   # 길드 아이콘 썸네일
    embed.set_footer(text='관리(체크·점령·가입 등)는 슬래시(/) 명령으로')
    return embed


# ── 셋업 카드 (Components V2) ──────────────────────────────────
# 버튼 콜백은 ActionRow 서브클래스에. 모든 응답 ephemeral — 채널이 안 지저분해짐.
class SetupButtons(discord.ui.ActionRow):
    @discord.ui.button(label='📊 대시보드', style=discord.ButtonStyle.primary, custom_id='gq_setup_dash')
    async def dash(self, interaction: discord.Interaction, button: discord.ui.Button):
        # 본인만 보이는 페이지네이션 대시보드 카드 (◀▶ 로 4섹션 넘김)
        await interaction.response.defer(ephemeral=True)
        records = await asyncio.to_thread(get_dashboard_records)
        await interaction.followup.send(view=DashboardCard(0, records), ephemeral=True)

    @discord.ui.button(label='🙋 내 현황', style=discord.ButtonStyle.success, custom_id='gq_setup_me')
    async def me(self, interaction: discord.Interaction, button: discord.ui.Button):
        await interaction.response.defer(ephemeral=True)
        st = await asyncio.to_thread(get_user_status, interaction.user.id)
        if not st:
            await interaction.followup.send('등록된 길드원이 아닙니다.', ephemeral=True)
            return
        left = max(0, WEEKLY_QUEST_LIMIT - st['quest_done'])
        card = discord.ui.LayoutView(timeout=None)   # 초록 카드 (버튼 색과 통일)
        card.add_item(discord.ui.Container(
            discord.ui.TextDisplay(f"## 🙋 {st['name']} 현황"),
            discord.ui.Separator(),
            discord.ui.TextDisplay(
                f"**등급**   {st['rank']}\n"
                f"**누적 경고**   {st['warning']}회\n"
                f"**이번주 길퀘**   {st['quest_done']}회 · {st['week_points']}점\n"
                f"**남은 길퀘**   {left}회"),
            accent_colour=discord.Colour.green(),
        ))
        await interaction.followup.send(view=card, ephemeral=True)

    @discord.ui.button(label='📄 시트', style=discord.ButtonStyle.secondary, custom_id='gq_setup_sheet')
    async def sheet(self, interaction: discord.Interaction, button: discord.ui.Button):
        # 구글 시트 링크 안내 (V2 금색 카드 — 버튼 자체는 노란색이 디스코드에 없어 accent 로 대신)
        url = get_spreadsheet_url()
        body = (f'## 📄 구글 시트\n[스프레드시트 열기]({url})' if url
                else '## 📄 시트\n시트 링크가 설정되지 않았습니다 (`SPREADSHEET_ID` 확인).')
        card = discord.ui.LayoutView(timeout=None)
        card.add_item(discord.ui.Container(discord.ui.TextDisplay(body), accent_colour=discord.Colour.gold()))
        await interaction.response.send_message(view=card, ephemeral=True)

    @discord.ui.button(label='❓ 도움말', style=discord.ButtonStyle.secondary, custom_id='gq_setup_help')
    async def help(self, interaction: discord.Interaction, button: discord.ui.Button):
        card = discord.ui.LayoutView(timeout=None)
        card.add_item(discord.ui.Container(discord.ui.TextDisplay(HELP_TEXT), accent_colour=_ACCENT))
        await interaction.response.send_message(view=card, ephemeral=True)


# ── 관리자 전용 버튼(가입/탈퇴/체크/점령) — 버튼→모달/유저선택 ──────────────
def _points_result(prefix, known, unknown):
    def _join(names, limit=40):
        s = ', '.join(names[:limit])
        return s + (f' 외 {len(names) - limit}명' if len(names) > limit else '')
    msg = f'{prefix}\n✅ {_join(known) if known else "없음"}'
    if unknown:
        msg += f'\n⚠️ 미등록(건너뜀): {_join(unknown)}'
    return msg[:1900]   # Discord 2000자 한도 방어


class CheckModal(discord.ui.Modal, title='길퀘 점수 체크'):
    점수 = discord.ui.TextInput(label='점수', placeholder='예: 5', max_length=6)
    길드원들 = discord.ui.TextInput(label='길드원들 (공백 구분)', style=discord.TextStyle.paragraph,
                                placeholder='바보1 바보2 바보3')
    날짜 = discord.ui.TextInput(label='날짜 (선택 · 예 0701 · 비우면 오늘)', required=False, max_length=10)

    async def on_submit(self, interaction: discord.Interaction):
        try:
            pt = int(str(self.점수).strip())
        except ValueError:
            await interaction.response.send_message('점수는 숫자여야 합니다.', ephemeral=True)
            return
        names = str(self.길드원들).split()
        if not names:
            await interaction.response.send_message('길드원을 1명 이상 입력하세요.', ephemeral=True)
            return
        pdate = today_int()
        if str(self.날짜).strip():
            pdate = parse_date_arg(str(self.날짜))
            if pdate is None:
                await interaction.response.send_message('날짜 형식 오류 (예: 0701)', ephemeral=True)
                return
        await interaction.response.defer(ephemeral=True)
        ok, known, unknown = await asyncio.to_thread(add_points, names, pt, 'N', pdate)
        if not ok:
            await interaction.followup.send('시트 오류로 처리하지 못했습니다. 잠시 후 다시 시도하세요.', ephemeral=True)
            return
        await interaction.followup.send(_points_result(f'**{pt}점** 체크', known, unknown), ephemeral=True)


class OccupyModal(discord.ui.Modal, title='점령 참여 기록 (8점)'):
    길드원들 = discord.ui.TextInput(label='길드원들 (공백 구분)', style=discord.TextStyle.paragraph,
                                placeholder='바보1 바보2 바보3')

    async def on_submit(self, interaction: discord.Interaction):
        names = str(self.길드원들).split()
        if not names:
            await interaction.response.send_message('길드원을 1명 이상 입력하세요.', ephemeral=True)
            return
        await interaction.response.defer(ephemeral=True)
        ok, known, unknown = await asyncio.to_thread(add_points, names, 8, 'Y', this_saturday())
        if not ok:
            await interaction.followup.send('시트 오류로 처리하지 못했습니다. 잠시 후 다시 시도하세요.', ephemeral=True)
            return
        await interaction.followup.send(_points_result('**점령** 8점', known, unknown), ephemeral=True)


# 탈퇴 — 직접 타이핑 대신 시트의 활동 길드원을 드롭다운으로 고른다. 고르면 확인 카드로 이어짐.
class RemoveSelect(discord.ui.Select):
    def __init__(self, names):
        super().__init__(placeholder='탈퇴할 길드원 선택', min_values=1, max_values=1,
                         options=[discord.SelectOption(label=n) for n in names[:25]])

    async def callback(self, interaction: discord.Interaction):
        from user.user import ConfirmDeleteView  # 지역 import (순환 방지) — 확인 카드 재사용
        view = ConfirmDeleteView(self.values[0])
        await interaction.response.send_message(view=view, ephemeral=True)
        view.origin = interaction


class RemoveSelectView(discord.ui.View):
    def __init__(self, names):
        super().__init__(timeout=180)
        self.origin = None   # 원본 상호작용 — 타임아웃 시 안내 갱신용
        self.add_item(RemoveSelect(names))

    async def on_timeout(self):
        if self.origin is not None:
            try:
                await self.origin.edit_original_response(
                    content='⏱ 시간 초과 — 탈퇴 버튼을 다시 눌러주세요.', view=None)
            except Exception:
                pass


class RegisterModal(discord.ui.Modal, title='길드원 가입'):
    닉 = discord.ui.TextInput(label='테런 닉네임')

    def __init__(self, member):
        super().__init__()
        self.member = member

    async def on_submit(self, interaction: discord.Interaction):
        if self.member.bot:
            await interaction.response.send_message('봇은 길드원으로 가입할 수 없습니다.', ephemeral=True)
            return
        await interaction.response.defer(ephemeral=True)
        ok, msg = await asyncio.to_thread(add_user, str(self.닉).strip(), self.member.id)
        if not ok:
            await interaction.followup.send(msg, ephemeral=True)
            return
        wait_id = get_role_id(interaction.guild.id, '입장대기')
        normal_id = get_role_id(interaction.guild.id, '일반')
        wait_role = interaction.guild.get_role(wait_id) if wait_id else None
        normal_role = interaction.guild.get_role(normal_id) if normal_id else None
        try:
            if wait_role:
                await self.member.remove_roles(wait_role)
            if normal_role:
                await self.member.add_roles(normal_role)
        except Exception as e:   # Forbidden·429 등 모두 — 시트는 됐으니 안내라도
            await interaction.followup.send(
                msg + f'\n⚠️ 시트는 됐지만 역할 부여 실패 (봇 역할 위치/권한 확인): {e}', ephemeral=True)
            return
        await interaction.followup.send(msg, ephemeral=True)


class RegisterSelect(discord.ui.UserSelect):
    def __init__(self):
        super().__init__(placeholder='가입할 디스코드 유저 선택', min_values=1, max_values=1)

    async def callback(self, interaction: discord.Interaction):
        await interaction.response.send_modal(RegisterModal(self.values[0]))


class RegisterSelectView(discord.ui.View):
    def __init__(self):
        super().__init__(timeout=180)
        self.origin = None
        self.add_item(RegisterSelect())

    async def on_timeout(self):
        if self.origin is not None:
            try:
                await self.origin.edit_original_response(
                    content='⏱ 시간 초과 — 가입 버튼을 다시 눌러주세요.', view=None)
            except Exception:
                pass


def _is_staff(interaction: discord.Interaction) -> bool:
    '''길마 또는 서마 — 관리자 버튼(가입/탈퇴/체크/점령/관리)의 공통 게이트'''
    return has_role(interaction, '길마') or has_role(interaction, '서마')


# ── 관리 서브메뉴 (Components V2 카드) ─────────────────────────────────────
#   관리 → 각 섹션. 모든 임시 View 는 LayoutView(V2). 한 메시지에서 V1↔V2 는 못 섞으므로 전부 V2.
#   [◀ 뒤로]는 언제나 맨 왼쪽. edit_message(view=...) 로 같은 메시지를 다른 V2 카드로 교체하며 이동.
_ACCENT = discord.Colour(0x5865F2)


def _card(text, *rows, accent=_ACCENT):
    '''TextDisplay + (ActionRow…) 를 accent 바 컨테이너로 감싼 V2 LayoutView(timeout 180)'''
    v = discord.ui.LayoutView(timeout=180)
    v.add_item(discord.ui.Container(
        discord.ui.TextDisplay(text), discord.ui.Separator(), *rows, accent_colour=accent))
    return v


class _ManageBackButton(discord.ui.Button):
    def __init__(self):
        super().__init__(label='◀ 뒤로', style=discord.ButtonStyle.secondary)

    async def callback(self, interaction: discord.Interaction):
        await interaction.response.edit_message(view=ManageMenuView())


class _RoleBackButton(discord.ui.Button):
    def __init__(self):
        super().__init__(label='◀ 뒤로', style=discord.ButtonStyle.secondary)

    async def callback(self, interaction: discord.Interaction):
        await interaction.response.edit_message(view=RoleMenuView())


def _result(text, back_cls=_ManageBackButton, accent=_ACCENT):
    '''결과 텍스트 + [◀ 뒤로] 한 줄 카드 (뒤로가 맨 왼쪽)'''
    row = discord.ui.ActionRow()
    row.add_item(back_cls())
    return _card(text, row, accent=accent)


# ── 등급 변경 — ① 닉네임 ② 새 등급 → [◀ 뒤로][변경 적용] (V2 카드) ──────────────────
class _RankNameSelect(discord.ui.Select):
    def __init__(self, names):
        super().__init__(placeholder='① 닉네임 선택', min_values=1, max_values=1,
                         options=[discord.SelectOption(label=n) for n in names[:25]])

    async def callback(self, interaction: discord.Interaction):
        self.view.chosen_name = self.values[0]
        await interaction.response.defer()   # 선택만 기억, 메시지는 그대로


class _RankSelect(discord.ui.Select):
    def __init__(self):
        super().__init__(placeholder='② 새 등급 선택', min_values=1, max_values=1,
                         options=[discord.SelectOption(label=r) for r in VALID_RANKS])

    async def callback(self, interaction: discord.Interaction):
        self.view.chosen_rank = self.values[0]
        await interaction.response.defer()


class _RankButtonsRow(discord.ui.ActionRow):
    @discord.ui.button(label='◀ 뒤로', style=discord.ButtonStyle.secondary)
    async def back(self, interaction: discord.Interaction, button: discord.ui.Button):
        await interaction.response.edit_message(view=ManageMenuView())

    @discord.ui.button(label='변경 적용', style=discord.ButtonStyle.success)
    async def apply(self, interaction: discord.Interaction, button: discord.ui.Button):
        v = self.view
        if not v.chosen_name or not v.chosen_rank:
            await interaction.response.send_message('닉네임과 등급을 모두 선택하세요.', ephemeral=True)
            return
        await interaction.response.defer()   # 컴포넌트 메시지 갱신 defer (V2 카드 교체)
        ok, msg = await asyncio.to_thread(change_user_rank, v.chosen_name, v.chosen_rank)
        v.stop()
        await interaction.edit_original_response(
            view=_result(msg, accent=discord.Colour.green() if ok else discord.Colour.red()))


class RankChangeView(discord.ui.LayoutView):
    def __init__(self, names):
        super().__init__(timeout=180)
        self.chosen_name = None
        self.chosen_rank = None
        note = '\n-# 25명까지만 표시 — 나머지는 `/등급변경`' if len(names) > 25 else ''
        row_name = discord.ui.ActionRow(); row_name.add_item(_RankNameSelect(names))
        row_rank = discord.ui.ActionRow(); row_rank.add_item(_RankSelect())
        self.add_item(discord.ui.Container(
            discord.ui.TextDisplay(f'## 등급 변경\n닉네임과 새 등급을 고른 뒤 [변경 적용].{note}'),
            discord.ui.Separator(), row_name, row_rank, _RankButtonsRow(),
            accent_colour=_ACCENT))


# ── 역할 관리 — [◀ 뒤로][역할 설정][역할 확인] (V2 카드) ──────────────────────────
class _RoleMenuRow(discord.ui.ActionRow):
    @discord.ui.button(label='◀ 뒤로', style=discord.ButtonStyle.secondary)
    async def back(self, interaction: discord.Interaction, button: discord.ui.Button):
        await interaction.response.edit_message(view=ManageMenuView())

    @discord.ui.button(label='역할 설정', style=discord.ButtonStyle.primary)
    async def do_set(self, interaction: discord.Interaction, button: discord.ui.Button):
        await interaction.response.edit_message(view=RoleMapView())

    @discord.ui.button(label='역할 확인', style=discord.ButtonStyle.secondary)
    async def do_check(self, interaction: discord.Interaction, button: discord.ui.Button):
        cfg = get_guild_config(interaction.guild.id)
        lines = []
        for k in ROLE_KEYS:
            rid = cfg.get(k)
            role = interaction.guild.get_role(rid) if rid else None
            lines.append(f'- {k}: {role.mention if role else "❌ 미설정"}')
        await interaction.response.edit_message(
            view=_result('## 역할 매핑 현황\n' + '\n'.join(lines), back_cls=_RoleBackButton))


class RoleMenuView(discord.ui.LayoutView):
    def __init__(self):
        super().__init__(timeout=180)
        self.add_item(discord.ui.Container(
            discord.ui.TextDisplay('## 역할 관리\n설정 또는 확인을 선택하세요.'),
            discord.ui.Separator(), _RoleMenuRow(), accent_colour=_ACCENT))


# ── 역할 설정 — ① 종류 ② 서버 역할 → [◀ 뒤로][매핑 저장] (V2 카드) ───────────────────
class _RoleKeySelect(discord.ui.Select):
    def __init__(self):
        super().__init__(placeholder='① 역할 종류 (길마/서마/…)', min_values=1, max_values=1,
                         options=[discord.SelectOption(label=k) for k in ROLE_KEYS])

    async def callback(self, interaction: discord.Interaction):
        self.view.chosen_key = self.values[0]
        await interaction.response.defer()


class _ServerRoleSelect(discord.ui.RoleSelect):
    def __init__(self):
        super().__init__(placeholder='② 서버 역할 선택', min_values=1, max_values=1)

    async def callback(self, interaction: discord.Interaction):
        self.view.chosen_role = self.values[0]
        await interaction.response.defer()


class _RoleMapButtonsRow(discord.ui.ActionRow):
    @discord.ui.button(label='◀ 뒤로', style=discord.ButtonStyle.secondary)
    async def back(self, interaction: discord.Interaction, button: discord.ui.Button):
        await interaction.response.edit_message(view=RoleMenuView())

    @discord.ui.button(label='매핑 저장', style=discord.ButtonStyle.success)
    async def apply(self, interaction: discord.Interaction, button: discord.ui.Button):
        v = self.view
        if not v.chosen_key or not v.chosen_role:
            await interaction.response.send_message('역할 종류와 서버 역할을 모두 선택하세요.', ephemeral=True)
            return
        # 파일 쓰기라 실패 가능(락/읽기전용/디스크풀). 잡지 않으면 '상호작용 실패' 로만 뜬다.
        try:
            await asyncio.to_thread(set_role_id, interaction.guild.id, v.chosen_key, v.chosen_role.id)
        except Exception as e:
            await interaction.response.send_message(f'저장 실패 — 잠시 후 다시 시도하세요: {e}', ephemeral=True)
            return
        v.stop()
        await interaction.response.edit_message(
            view=_result(f'✅ `{v.chosen_key}` → {v.chosen_role.mention} 저장됨.', back_cls=_RoleBackButton))


class RoleMapView(discord.ui.LayoutView):
    def __init__(self):
        super().__init__(timeout=180)
        self.chosen_key = None
        self.chosen_role = None
        row_key = discord.ui.ActionRow(); row_key.add_item(_RoleKeySelect())
        row_role = discord.ui.ActionRow(); row_role.add_item(_ServerRoleSelect())
        self.add_item(discord.ui.Container(
            discord.ui.TextDisplay('## 역할 설정\n매핑할 역할 종류와 서버 역할을 고른 뒤 [매핑 저장].'),
            discord.ui.Separator(), row_key, row_role, _RoleMapButtonsRow(),
            accent_colour=_ACCENT))


class AdminButtons(discord.ui.ActionRow):
    @discord.ui.button(label='가입', style=discord.ButtonStyle.secondary, custom_id='gq_admin_join')
    async def join(self, interaction: discord.Interaction, button: discord.ui.Button):
        if not _is_staff(interaction):
            await interaction.response.send_message('길마/서마 전용입니다.', ephemeral=True)
            return
        v = RegisterSelectView()
        await interaction.response.send_message('가입할 유저를 선택하세요:', view=v, ephemeral=True)
        v.origin = interaction   # on_timeout 안내용

    @discord.ui.button(label='탈퇴', style=discord.ButtonStyle.secondary, custom_id='gq_admin_leave')
    async def leave(self, interaction: discord.Interaction, button: discord.ui.Button):
        if not _is_staff(interaction):
            await interaction.response.send_message('길마/서마 전용입니다.', ephemeral=True)
            return
        # 시트 읽기(콜드 캐시=네트워크 왕복)가 3초 ack 창을 넘길 수 있어 먼저 defer 로 ack.
        #   컴포넌트에서 ephemeral 응답을 만들려면 thinking=True 필수 (없으면 공개 패널을 대상으로 잡아 실패).
        await interaction.response.defer(ephemeral=True, thinking=True)
        names = await asyncio.to_thread(list_active_users)
        if not names:
            await interaction.edit_original_response(content='등록된 길드원이 없습니다.')
            return
        content = '탈퇴할 길드원을 선택하세요:'
        if len(names) > 25:
            content += '\n-# 25명까지만 표시됩니다 — 나머지는 `/탈퇴` 명령을 쓰세요.'
        v = RemoveSelectView(names)
        await interaction.edit_original_response(content=content, view=v)
        v.origin = interaction

    @discord.ui.button(label='체크', style=discord.ButtonStyle.secondary, custom_id='gq_admin_check')
    async def check(self, interaction: discord.Interaction, button: discord.ui.Button):
        if not _is_staff(interaction):
            await interaction.response.send_message('길마/서마 전용입니다.', ephemeral=True)
            return
        await interaction.response.send_modal(CheckModal())

    @discord.ui.button(label='점령', style=discord.ButtonStyle.secondary, custom_id='gq_admin_occupy')
    async def occupy(self, interaction: discord.Interaction, button: discord.ui.Button):
        if not _is_staff(interaction):
            await interaction.response.send_message('길마/서마 전용입니다.', ephemeral=True)
            return
        await interaction.response.send_modal(OccupyModal())

    @discord.ui.button(label='관리', style=discord.ButtonStyle.secondary, custom_id='gq_admin_manage')
    async def manage(self, interaction: discord.Interaction, button: discord.ui.Button):
        if not _is_staff(interaction):
            await interaction.response.send_message('길마/서마 전용입니다.', ephemeral=True)
            return
        # 관리 액션이 많아 한 줄에 다 못 넣어 '관리' 하나로 묶음 → V2 서브메뉴 카드로 분기 (content 없이 view 만)
        await interaction.response.send_message(view=ManageMenuView(), ephemeral=True)


# ── 인원수 증감 — [◀ 뒤로][증가][감소] → 등급·숫자 모달 (V2 카드) ──────────────────
class AdjustModal(discord.ui.Modal):
    등급 = discord.ui.TextInput(label='등급 (길마/서마/명예/우수/일반)', placeholder='명예', max_length=10)
    숫자 = discord.ui.TextInput(label='인원 수', placeholder='예: 3', max_length=6)

    def __init__(self, sign):
        super().__init__(title='인원수 ' + ('증가' if sign > 0 else '감소'))
        self.sign = sign

    async def on_submit(self, interaction: discord.Interaction):
        rank = str(self.등급).strip()
        if rank not in VALID_RANKS:
            await interaction.response.send_message(
                f'등급은 {", ".join(VALID_RANKS)} 중 하나여야 합니다.', ephemeral=True)
            return
        try:
            n = int(str(self.숫자).strip())
        except ValueError:
            await interaction.response.send_message('인원 수는 숫자여야 합니다.', ephemeral=True)
            return
        if n < 1:
            await interaction.response.send_message('1 이상의 숫자를 입력하세요.', ephemeral=True)
            return
        await interaction.response.defer(ephemeral=True)
        ok, msg = await asyncio.to_thread(update_rank_cnt, rank, self.sign * n, 'delta')
        await interaction.followup.send(msg, ephemeral=True)


class _RankCountRow(discord.ui.ActionRow):
    @discord.ui.button(label='◀ 뒤로', style=discord.ButtonStyle.secondary)
    async def back(self, interaction: discord.Interaction, button: discord.ui.Button):
        await interaction.response.edit_message(view=ManageMenuView())

    @discord.ui.button(label='증가', style=discord.ButtonStyle.success)
    async def inc(self, interaction: discord.Interaction, button: discord.ui.Button):
        await interaction.response.send_modal(AdjustModal(+1))

    @discord.ui.button(label='감소', style=discord.ButtonStyle.danger)
    async def dec(self, interaction: discord.Interaction, button: discord.ui.Button):
        await interaction.response.send_modal(AdjustModal(-1))


class RankCountMenuView(discord.ui.LayoutView):
    def __init__(self):
        super().__init__(timeout=180)
        self.add_item(discord.ui.Container(
            discord.ui.TextDisplay('## 인원수 증감\n방향을 고르면 등급·숫자 입력창이 떠요.'),
            discord.ui.Separator(), _RankCountRow(), accent_colour=_ACCENT))


# ── 길퀘불가 대리 등록 — 길드원 선택 → 사유 모달 (add_deny_reason: 주1회·일요일 규칙 그대로) ──
class DenyProxyModal(discord.ui.Modal, title='길퀘 불가 사유 대리 등록'):
    사유 = discord.ui.TextInput(label='사유', placeholder='예: 개인사정', max_length=100)

    def __init__(self, member_name):
        super().__init__()
        self.member_name = member_name

    async def on_submit(self, interaction: discord.Interaction):
        await interaction.response.defer(ephemeral=True)
        ok, msg = await asyncio.to_thread(add_deny_reason, self.member_name, str(self.사유))
        await interaction.followup.send(f'**{self.member_name}** — {msg}', ephemeral=True)


class DenyProxySelect(discord.ui.Select):
    def __init__(self, names):
        super().__init__(placeholder='불가 사유를 등록할 길드원', min_values=1, max_values=1,
                         options=[discord.SelectOption(label=n) for n in names[:25]])

    async def callback(self, interaction: discord.Interaction):
        await interaction.response.send_modal(DenyProxyModal(self.values[0]))


class _DenyBackRow(discord.ui.ActionRow):
    @discord.ui.button(label='◀ 뒤로', style=discord.ButtonStyle.secondary)
    async def back(self, interaction: discord.Interaction, button: discord.ui.Button):
        await interaction.response.edit_message(view=ManageMenuView())


class DenyProxyView(discord.ui.LayoutView):
    def __init__(self, names):
        super().__init__(timeout=180)
        row_sel = discord.ui.ActionRow(); row_sel.add_item(DenyProxySelect(names))
        note = '\n-# 25명까지만 표시' if len(names) > 25 else ''
        self.add_item(discord.ui.Container(
            discord.ui.TextDisplay(f'## 길퀘불가 대리 등록\n사유를 등록할 길드원을 선택하세요.{note}'),
            discord.ui.Separator(), row_sel, _DenyBackRow(), accent_colour=_ACCENT))


# 시트 요약 — 집계 구간 입력 모달. 구간(week/month/YYYYMMDD~YYYYMMDD)은 저장돼 자동 갱신에도 유지.
class SummaryRangeModal(discord.ui.Modal, title='시트 요약 — 집계 구간'):
    구간 = discord.ui.TextInput(
        label='집계 구간 (비우면 이번주)', required=False, max_length=30,
        placeholder='이번주 · 이번달 · 0701~0731')

    async def on_submit(self, interaction: discord.Interaction):
        spec = parse_summary_input(str(self.구간))
        if spec is None:
            await interaction.response.send_message(
                '구간 형식 오류. 예: `이번주` · `이번달` · `0701~0731`', ephemeral=True)
            return
        await interaction.response.defer(ephemeral=True)   # 먼저 ack — 이후 쓰기 실패는 followup 로 안내
        try:
            # 저장(파일쓰기)+시트쓰기는 실패 가능 → 전부 try+to_thread 안에서. 저장 → 배치 자동 갱신도 이 구간 유지
            await asyncio.to_thread(set_setting, '_global', '_summary_range', spec)
            await asyncio.to_thread(write_summary_sheet, spec)
            await asyncio.to_thread(beautify_headers)
        except Exception as e:
            await interaction.followup.send(f'요약 실패: {e}', ephemeral=True)
            return
        await interaction.followup.send(
            '✅ 시트 **요약** 탭 갱신 완료. 집계 구간이 저장돼 자동 갱신에도 유지됩니다.', ephemeral=True)


# ── 관리 메뉴 (V2 카드, 8버튼 4+4행). 전부 파랑, 길퀘불가만 빨강 ─────────────────────
#   1행: 등급 변경 · 역할 관리 · 시트 요약 · 길퀘불가 / 2행: 인원수 증감 · 명예우수계산 · 리마인드 · 경고처리
class _ManageRow1(discord.ui.ActionRow):
    @discord.ui.button(label='등급 변경', style=discord.ButtonStyle.primary)
    async def rank(self, interaction: discord.Interaction, button: discord.ui.Button):
        await interaction.response.defer()   # ephemeral V2 메시지 → 등급 카드로 교체
        names = await asyncio.to_thread(list_active_users)
        if not names:
            await interaction.edit_original_response(view=_result('등록된 길드원이 없습니다.'))
            return
        await interaction.edit_original_response(view=RankChangeView(names))

    @discord.ui.button(label='역할 관리', style=discord.ButtonStyle.primary)
    async def role(self, interaction: discord.Interaction, button: discord.ui.Button):
        await interaction.response.edit_message(view=RoleMenuView())

    @discord.ui.button(label='시트 요약', style=discord.ButtonStyle.primary)
    async def summary(self, interaction: discord.Interaction, button: discord.ui.Button):
        await interaction.response.send_modal(SummaryRangeModal())   # 집계 구간 입력받아 갱신

    @discord.ui.button(label='길퀘불가', style=discord.ButtonStyle.danger)
    async def deny(self, interaction: discord.Interaction, button: discord.ui.Button):
        await interaction.response.defer()
        names = await asyncio.to_thread(list_active_users)
        if not names:
            await interaction.edit_original_response(view=_result('등록된 길드원이 없습니다.'))
            return
        await interaction.edit_original_response(view=DenyProxyView(names))


class _ManageRow2(discord.ui.ActionRow):
    @discord.ui.button(label='인원수 증감', style=discord.ButtonStyle.primary)
    async def counts(self, interaction: discord.Interaction, button: discord.ui.Button):
        await interaction.response.edit_message(view=RankCountMenuView())

    @discord.ui.button(label='명예우수계산', style=discord.ButtonStyle.primary)
    async def honor(self, interaction: discord.Interaction, button: discord.ui.Button):
        await interaction.response.defer()
        ok, honor, excel, n = await asyncio.to_thread(calc_honor_excellent)
        if not ok:
            await interaction.edit_original_response(
                view=_result('시트 오류로 처리하지 못했습니다.', accent=discord.Colour.red()))
            return
        text = ('## 명예/우수 계산 결과\n'
                '**명예**: ' + (', '.join(honor) or '없음') + '\n'
                '**우수**: ' + (', '.join(excel) or '없음') + '\n'
                f'-# {n}개 포인트 반영 완료 (reflection_yn → Y)')
        await interaction.edit_original_response(view=_result(text[:3900]))

    @discord.ui.button(label='리마인드', style=discord.ButtonStyle.primary)
    async def remind(self, interaction: discord.Interaction, button: discord.ui.Button):
        await interaction.response.defer()
        members = await asyncio.to_thread(get_incomplete_members, WEEKLY_QUEST_LIMIT)
        if members is None:
            await interaction.edit_original_response(
                view=_result('시트 조회 실패 — 잠시 후 다시 시도하세요.', accent=discord.Colour.red()))
            return
        if not members:
            await interaction.edit_original_response(
                view=_result('🎉 이번주 모두 길퀘 완료!', accent=discord.Colour.green()))
            return
        tags = [(f"<@{m['discord_id']}>" if m['discord_id'] else m['name']) + f"({m['left']})"
                for m in members]
        pings = discord.AllowedMentions(users=True)
        cur = '⏰ **이번주 남은 길퀘가 있어요!**'
        for t in tags:                       # 2000자 초과 방지 분할, 채널에 공개 발송
            if len(cur) + 1 + len(t) > 1900:
                await interaction.channel.send(cur, allowed_mentions=pings)
                cur = t
            else:
                cur += ' ' + t
        await interaction.channel.send(cur, allowed_mentions=pings)
        await interaction.edit_original_response(
            view=_result(f'✅ 리마인드 발송 완료 ({len(members)}명).', accent=discord.Colour.green()))

    @discord.ui.button(label='경고처리', style=discord.ButtonStyle.primary)
    async def warning(self, interaction: discord.Interaction, button: discord.ui.Button):
        await interaction.response.defer()   # /주간경고처리 와 동일 — 지난 주 경고 정산(부여/차감)
        ok, msg = await asyncio.to_thread(process_weekly_warnings, WEEKLY_QUEST_LIMIT)
        await interaction.edit_original_response(
            view=_result(msg[:3900], accent=discord.Colour.green() if ok else discord.Colour.red()))


class ManageMenuView(discord.ui.LayoutView):
    def __init__(self):
        super().__init__(timeout=180)
        self.add_item(discord.ui.Container(
            discord.ui.TextDisplay('## 🛠 관리\n항목을 선택하세요.'),
            discord.ui.Separator(), _ManageRow1(), _ManageRow2(),
            accent_colour=_ACCENT))


# 컨테이너/섹션/구분선으로 짠 카드형 레이아웃 (embed 대신 메시지 전체가 컴포넌트).
#   영속 등록(add_view)은 인자 없는 SetupLayout() 으로 하고, 실제 게시 시 봇 아바타를 넣는다.
class SetupLayout(discord.ui.LayoutView):
    def __init__(self, avatar_url: str = None):
        super().__init__(timeout=None)
        title = discord.ui.TextDisplay(
            '## 길퀘 관리 센터\n길드 퀘스트 현황을 한눈에 확인하는 공간이에요.')
        header = (discord.ui.Section(title, accessory=discord.ui.Thumbnail(avatar_url))
                  if avatar_url else title)
        self.add_item(discord.ui.Container(
            header,
            discord.ui.Separator(),
            discord.ui.TextDisplay(
                '> **대시보드**   ·   전체 현황 (순위·남은 횟수·경고·예상)\n'
                '> **내 현황**   ·   내 등급·점수·남은 길퀘\n'
                '> **시트**   ·   구글 스프레드시트 열기\n'
                '> **도움말**   ·   명령어 안내'),
            discord.ui.Separator(),
            SetupButtons(),
            discord.ui.Separator(),
            discord.ui.TextDisplay('-# 👑 관리자 전용 (길마·서마)'),
            AdminButtons(),
            accent_colour=discord.Colour(0x5865F2),
        ))


@app_commands.guild_only()
class Panel(commands.Cog):
    def __init__(self, bot):
        self.bot = bot

    async def cog_load(self):
        # 영속 View 등록 — 재시작 후에도 기존 패널/셋업 버튼이 동작
        self.bot.add_view(DashboardCard())
        self.bot.add_view(SetupLayout())

    # 채널 자동 생성 + 액션 버튼 패널 게시 (알로항식 원클릭 셋업)
    @app_commands.command(name='셋업', description='(길마) 관리 채널 자동 생성 + 버튼 패널 설치')
    async def setup_center(self, interaction: discord.Interaction):
        if not has_role(interaction, '길마'):
            await interaction.response.send_message('길드마스터만 사용 가능합니다!', ephemeral=True)
            return
        await interaction.response.defer(ephemeral=True)

        # 대상 채널 결정 — 이전에 만든 채널 ID 를 최우선 재사용(이름을 손댔어도 그대로 씀).
        #   없으면 이름으로 탐색, 그래도 없으면 새로 생성 → /셋업 을 여러 번 해도 채널이 안 늘어남.
        # 이름의 전각 세로바(｜)는 Discord 가 공백을 하이픈으로 치환하는 걸 피해 깔끔하게 보이려는 것.
        ch_name = '🗒️｜길퀘관리'
        old_ch = get_setting(interaction.guild.id, '_setup_ch')
        old_msg = get_setting(interaction.guild.id, '_setup_msg')
        channel = interaction.guild.get_channel(int(old_ch)) if old_ch else None
        if channel is not None and old_msg:
            try:   # 이전 패널 메시지 정리(고아 방지)
                old = await channel.fetch_message(int(old_msg))
                await old.delete()
            except Exception:
                pass
        if channel is None:
            channel = discord.utils.get(interaction.guild.text_channels, name=ch_name)
        if channel is None:
            try:
                channel = await interaction.guild.create_text_channel(ch_name)
            except discord.Forbidden:
                await interaction.followup.send(
                    '채널을 만들 권한이 없습니다. 봇에게 **채널 관리(Manage Channels)** 권한을 주세요.',
                    ephemeral=True)
                return

        try:
            avatar = self.bot.user.display_avatar.url if self.bot.user else None
            msg = await channel.send(view=SetupLayout(avatar))  # Components V2 카드 (embed 없이 컴포넌트만)
        except discord.Forbidden:
            await interaction.followup.send(
                f'{channel.mention} 에 메시지를 보낼 권한이 없습니다.', ephemeral=True)
            return
        set_setting(interaction.guild.id, '_setup_ch', channel.id)
        set_setting(interaction.guild.id, '_setup_msg', msg.id)
        await interaction.followup.send(
            f'{channel.mention} 에 관리 패널을 설치했습니다. 버튼으로 조회·등록하세요.', ephemeral=True)

    # 시트 원클릭 준비 (새 서버 배포용): 4탭 + user_rank 등급행/공식 자동 생성
    @app_commands.command(name='초기화', description='(서버 소유자/관리자) 시트 4탭 + 등급 자동 준비')
    async def init_sheet_cmd(self, interaction: discord.Interaction):
        is_owner = interaction.user.id == interaction.guild.owner_id
        if not (is_owner or interaction.user.guild_permissions.administrator):
            await interaction.response.send_message('서버 소유자 또는 관리자만 사용 가능합니다!', ephemeral=True)
            return
        await interaction.response.defer(ephemeral=True)
        ok, msg = await asyncio.to_thread(init_sheets)
        # 초기화 김에 요약 탭 생성 + 헤더 서식까지 (사람이 보기 좋게)
        try:
            await asyncio.to_thread(write_summary_sheet)
            await asyncio.to_thread(beautify_headers)
            msg += ' · 요약 탭/서식도 정리됨'
        except Exception as e:
            msg += f' (요약/서식은 건너뜀: {e})'
        await interaction.followup.send(msg, ephemeral=True)


async def setup(bot):
    await bot.add_cog(Panel(bot))
