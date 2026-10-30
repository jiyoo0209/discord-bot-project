'''
* 점령 참여 투표 — 매주 월요일 자정 배치가 점령투표 채널에 그 주 토요일 투표 카드를 게시
*   [✅ 참여] [❌ 불참] [💬 기타(사유 팝업)] — 누구나, 관리자 확인 전까지 언제든 변경 가능
*   [🔒 관리자 확인] — 길마·서마. 투표 마감 + 참여 인원을 /점령 과 같이 시트 기록(8점, 그 주 토요일)
*   저장: 별도 시트 없음 — 명단은 카드 메시지 자체가 원본. 버튼 custom_id 에 토요일 날짜를 담아 재시작 후에도 동작.
*   이름: 시트 등록자는 테런 닉네임, 미등록자는 서버 닉네임 (미등록자는 시트 기록에서 제외됨)
* + /점령투표 : 이번 주 투표 카드를 지금 수동 게시 (길마·서마)
* date   2026.10.30
'''
import re
import time
import asyncio
from datetime import date

import discord
from discord import app_commands
from discord.ext import commands

from sheet.sheet import get_dashboard_records, add_points
from roleSetting.roleSetting import has_role
from util.dates import this_saturday

VOTE_CHANNEL_ID = 1565600865480609914
OCCUPY_POINT = 8                       # /점령 과 동일
_ACCENT = discord.Colour(0x3FB8F3)     # 길드 대표색

YES, NO, ETC = 'yes', 'no', 'etc'
_HEAD = {YES: '✅ 참여 인원', NO: '❌ 불참', ETC: '💬 기타'}
_HEAD_RE = {k: re.compile(rf'^\*\*{re.escape(v)}\*\*$') for k, v in _HEAD.items()}
_NONE = '-# 아직 없어요'
_locks = {}   # 메시지 ID -> asyncio.Lock (동시 클릭 직렬화)


def _lock(mid):
    return _locks.setdefault(mid, asyncio.Lock())


def _is_staff(interaction):
    return has_role(interaction, '길마') or has_role(interaction, '서마')


def _esc(s):
    return discord.utils.escape_markdown(s)


def _unesc(s):
    return re.sub(r'\\(.)', r'\1', s)


def _title(sat):
    s = str(sat)
    d = date(int(s[:4]), int(s[4:6]), int(s[6:8]))
    return f'{d.year}년 {d.month}월 {d.day}일(토) 점령 참여 여부'


# ── 상태 <-> 카드 텍스트 ─────────────────────────────────────
#   state = {YES: [줄...], NO: [줄...], ETC: [줄...]}  줄 = 이스케이프된 '이름' 또는 '이름: 사유'
def _render(sat, state, closed_by=None, recorded=0):
    counts = f'✅ 참여 {len(state[YES])} · ❌ 불참 {len(state[NO])} · 💬 기타 {len(state[ETC])}'
    body = []
    for k in (YES, NO, ETC):
        body.append(f'**{_HEAD[k]}**')
        body.extend(f'- {ln}' for ln in state[k]) if state[k] else body.append(_NONE)
    items = [
        discord.ui.TextDisplay(f'## ⚔️ {_title(sat)}\n{counts}'),
        discord.ui.Separator(),
        discord.ui.TextDisplay('\n'.join(body)),
        discord.ui.Separator(),
    ]
    if closed_by is None:
        row = discord.ui.ActionRow()
        for item in (VoteButton(YES, sat), VoteButton(NO, sat), VoteButton(ETC, sat), ConfirmButton(sat)):
            row.add_item(item)
        items += [row, discord.ui.TextDisplay('-# 관리자 확인 전까지 언제든 바꿀 수 있어요')]
    else:
        items.append(discord.ui.TextDisplay(
            f'-# 🔒 투표 마감 · 점령 {recorded}명 시트 기록 완료 · 확인자 {closed_by.mention}\n'
            f'-# <t:{int(time.time())}:f>'))
    v = discord.ui.LayoutView(timeout=None)
    v.add_item(discord.ui.Container(*items, accent_colour=_ACCENT))
    return v


def _walk(components):
    for c in components or []:
        yield c
        yield from _walk(getattr(c, 'children', None))


def _parse(message):
    '''카드 메시지 → (state, 마감 여부)'''
    state = {YES: [], NO: [], ETC: []}
    closed = True
    cur = None
    for c in _walk(message.components):
        if getattr(c, 'custom_id', None):
            closed = False   # 버튼이 남아 있으면 진행 중
        content = getattr(c, 'content', None)
        if not content:
            continue
        for ln in content.split('\n'):
            hit = next((k for k, rx in _HEAD_RE.items() if rx.match(ln)), None)
            if hit:
                cur = hit
            elif cur and ln.startswith('- '):
                state[cur].append(ln[2:])
            elif ln.startswith('## ') or ln.startswith('-# 🔒'):
                cur = None
    return state, closed


def _owner_of(line):
    '''줄 → 이스케이프된 이름 (기타 줄이면 ': ' 앞)'''
    return line.split(': ', 1)[0]


def _voter_name(user):
    '''시트 등록자면 테런 닉네임, 아니면 서버 닉네임 (캐시된 시트 레코드 사용)'''
    users, _p, _d, _r = get_dashboard_records()
    for r in users:
        if len(r) > 3 and r[3] == str(user.id) and r[0] and (len(r) < 3 or r[2] != '탈퇴'):
            return r[0]
    return user.display_name


async def _apply_vote(interaction, choice, sat, reason=None, msg_ref=None):
    '''현재 카드를 다시 읽어 내 항목을 옮긴 뒤 카드 갱신. 응답(defer)은 호출측에서 끝낸 상태.'''
    name = _esc(await asyncio.to_thread(_voter_name, interaction.user))
    msg_ref = msg_ref or interaction.message
    async with _lock(msg_ref.id):
        message = await msg_ref.channel.fetch_message(msg_ref.id)   # 동시 클릭 대비 최신 상태
        state, closed = _parse(message)
        if closed:
            await interaction.followup.send('이미 마감된 투표예요.', ephemeral=True)
            return
        for k in state:
            state[k] = [ln for ln in state[k] if _owner_of(ln) != name]
        state[choice].append(f'{name}: {_esc(reason)}' if (choice == ETC and reason) else name)
        await message.edit(view=_render(sat, state), allowed_mentions=discord.AllowedMentions.none())


# ── 버튼 ─────────────────────────────────────────────────
_STYLE = {YES: ('✅', '참여', discord.ButtonStyle.success),
          NO: ('❌', '불참', discord.ButtonStyle.danger),
          ETC: ('💬', '기타', discord.ButtonStyle.secondary)}


class ReasonModal(discord.ui.Modal, title='기타 — 사유 입력'):
    사유 = discord.ui.TextInput(label='사유', placeholder='예: 사람 부족하면 참여함', max_length=100)

    def __init__(self, sat, message, current=''):
        super().__init__()
        self.sat, self.message = sat, message
        if current:
            self.사유.default = current

    async def on_submit(self, interaction: discord.Interaction):
        await interaction.response.defer()
        await _apply_vote(interaction, ETC, self.sat, ' '.join(str(self.사유).split()), self.message)


class VoteButton(discord.ui.DynamicItem[discord.ui.Button],
                 template=r'ov_(?P<c>yes|no|etc):(?P<sat>[0-9]{8})'):
    def __init__(self, choice: str, sat: int):
        emoji, label, style = _STYLE[choice]
        super().__init__(discord.ui.Button(label=label, emoji=emoji, style=style,
                                           custom_id=f'ov_{choice}:{sat}'))
        self.choice, self.sat = choice, sat

    @classmethod
    async def from_custom_id(cls, interaction, item, match):
        return cls(match['c'], int(match['sat']))

    async def callback(self, interaction: discord.Interaction):
        if self.choice == ETC:
            # 팝업은 3초 안의 첫 응답이어야 함 → 이름 조회는 짧게만 기다리고, 기존 사유가 있으면 미리 채움
            current = ''
            try:
                name = _esc(await asyncio.wait_for(asyncio.to_thread(_voter_name, interaction.user), 1.5))
                for ln in _parse(interaction.message)[0][ETC]:
                    own, _, why = ln.partition(': ')
                    if own == name:
                        current = _unesc(why)
            except Exception:
                pass
            await interaction.response.send_modal(ReasonModal(self.sat, interaction.message, current))
            return
        await interaction.response.defer()
        await _apply_vote(interaction, self.choice, self.sat)


class ConfirmButton(discord.ui.DynamicItem[discord.ui.Button], template=r'ov_ok:(?P<sat>[0-9]{8})'):
    def __init__(self, sat: int):
        super().__init__(discord.ui.Button(label='관리자 확인', emoji='🔒', style=discord.ButtonStyle.primary,
                                           custom_id=f'ov_ok:{sat}'))
        self.sat = sat

    @classmethod
    async def from_custom_id(cls, interaction, item, match):
        return cls(int(match['sat']))

    async def callback(self, interaction: discord.Interaction):
        if not _is_staff(interaction):
            await interaction.response.send_message('확인 권한이 없습니다.', ephemeral=True)
            return
        await interaction.response.defer(ephemeral=True, thinking=True)
        msg_ref = interaction.message
        async with _lock(msg_ref.id):
            message = await msg_ref.channel.fetch_message(msg_ref.id)
            state, closed = _parse(message)
            if closed:
                await interaction.followup.send('이미 마감된 투표예요.', ephemeral=True)
                return
            names = [_unesc(ln) for ln in state[YES]]
            known, unknown = [], []
            if names:
                ok, known, unknown = await asyncio.to_thread(add_points, names, OCCUPY_POINT, 'Y', self.sat)
                if not ok:
                    await interaction.followup.send('시트 오류로 기록하지 못했어요. 잠시 후 다시 눌러주세요.',
                                                    ephemeral=True)
                    return   # 카드는 그대로 (재시도 가능)
            await message.edit(view=_render(self.sat, state, closed_by=interaction.user, recorded=len(known)),
                               allowed_mentions=discord.AllowedMentions.none())
        note = f'점령 {len(known)}명을 시트에 기록했어요.'
        if unknown:
            note += '\n⚠️ 시트 미등록이라 제외: ' + ', '.join(unknown)
        await interaction.followup.send(note, ephemeral=True)


class OccupyVote(commands.Cog):
    def __init__(self, bot):
        self.bot = bot

    async def cog_load(self):
        self.bot.add_dynamic_items(VoteButton, ConfirmButton)   # 재시작 후에도 버튼 동작

    async def post_vote(self, sat=None):
        '''이번 주 토요일 투표 카드 게시. 성공 여부 반환 (배치 가드용).'''
        sat = sat or this_saturday()
        try:
            channel = self.bot.get_channel(VOTE_CHANNEL_ID) or await self.bot.fetch_channel(VOTE_CHANNEL_ID)
            await channel.send(view=_render(sat, {YES: [], NO: [], ETC: []}))
            return True
        except Exception as e:
            print(f'[vote] 점령 투표 게시 실패: {e}')
            return False

    @app_commands.command(name='점령투표', description='(길마/서마) 이번 주 토요일 점령 참여 투표를 지금 게시')
    @app_commands.guild_only()
    async def post_now(self, interaction: discord.Interaction):
        if not _is_staff(interaction):
            await interaction.response.send_message('길마/서마만 사용 가능합니다!', ephemeral=True)
            return
        await interaction.response.defer(ephemeral=True)
        ok = await self.post_vote()
        await interaction.followup.send(f'<#{VOTE_CHANNEL_ID}> 에 투표를 올렸어요.' if ok
                                        else '투표 게시에 실패했어요. 봇 권한을 확인하세요.', ephemeral=True)


async def setup(bot):
    await bot.add_cog(OccupyVote(bot))
