'''
* 서버 입장 알림 + 승인 — 새 멤버 입장 시 입장대기 역할 부여 + 입장 채널에 환영 카드
*   [길드원 승인] 닉네임 팝업 → 시트 등록(/가입 과 동일) · 입장대기→일반 · 서버 닉네임 변경
*   [게스트 승인] 입장대기→게스트 (시트 등록 없음)
*   두 버튼 모두 길마·서마 전용. 승인 후 카드는 결과 + 승인자 + 시각으로 바뀌고 버튼은 사라짐.
* 퇴장 시 같은 채널에 퇴장 카드 (프로필형 · 함께한 기간 · 시각)
*   필요: 개발자 포털 Server Members Intent, 봇 역할이 입장대기/일반/게스트 보다 위.
* date   2026.10.30
'''
import asyncio
import time
from datetime import datetime, timezone

import discord
from discord.ext import commands

from sheet.sheet import add_user
from roleSetting.roleSetting import has_role
from config.guild_config import get_role_id

WELCOME_CHANNEL_ID = 1414425194650406923
SERVER_NAME = '마커'
TITLE_EMOJI = '<:emoji_13:1486906955011461261>'   # 서버 벚꽃 이모지

_ACCENT = discord.Colour(0x3FB8F3)   # 길드 대표색 (다른 카드와 통일)
_LEAVE_ACCENT = discord.Colour(0x888780)   # 퇴장 카드는 회색으로 구분
_NO_PERM = '승인 권한이 없습니다.'
_busy = set()   # 승인 처리 중인 유저 ID (관리자 동시 클릭 방지)


def _is_staff(interaction):
    return has_role(interaction, '길마') or has_role(interaction, '서마')


def _card(member, number, body, footer, buttons=False):
    '''입장 카드 — [제목/환영 + 오른쪽 프로필] · 본문 · 하단 줄 (· 승인 버튼)'''
    head = discord.ui.TextDisplay(
        f'## {TITLE_EMOJI} 새로운 멤버가 도착했어요\n'
        f'{member.mention}님, [ {SERVER_NAME} ]에 오신 걸 환영합니다 ✨')
    items = [
        discord.ui.Section(head, accessory=discord.ui.Thumbnail(member.display_avatar.url)),
        discord.ui.Separator(),
        discord.ui.TextDisplay(body),
        discord.ui.Separator(),
        discord.ui.TextDisplay(f'-# {footer}'),
    ]
    if buttons:
        row = discord.ui.ActionRow()
        row.add_item(MemberApproveButton(member.id, number))
        row.add_item(GuestApproveButton(member.id, number))
        items.append(row)
    v = discord.ui.LayoutView(timeout=None)
    v.add_item(discord.ui.Container(*items, accent_colour=_ACCENT))
    return v


def _welcome_card(member, number):
    return _card(member, number, '관리자가 입장을 승인할 때까지 잠시만 기다려주세요 🙏',
                 f'<t:{int(time.time())}:f>', buttons=True)   # number 는 예전 카드 버튼 호환용(표시 안 함)


def _done_card(member, number, label, approver):
    return _card(member, number, f'✅ {label} 승인 완료',
                 f'승인자 {approver.mention} · <t:{int(time.time())}:f>')


def _stay(joined_at):
    '''서버 입장 시각 → 함께한 기간 문구 (모르면 None)'''
    if joined_at is None:
        return None
    days = (datetime.now(timezone.utc) - joined_at).days
    return f'{days}일' if days >= 1 else '하루 미만'


def _leave_card(member):
    '''퇴장 카드 — [제목/작별 인사 + 오른쪽 프로필] · 함께한 기간 · 시각 (나간 사람은 멘션이 깨질 수 있어 이름을 글자로)'''
    name = discord.utils.escape_markdown(member.display_name)
    head = discord.ui.TextDisplay(
        f'## 👋 멤버가 떠났어요\n'
        f'**{name}**님이 [ {SERVER_NAME} ]를 떠났어요.\n그동안 함께해 주셔서 고마웠어요 🌙')
    stay = _stay(member.joined_at)
    footer = (f'함께한 기간 {stay} · ' if stay else '') + f'<t:{int(time.time())}:f>'
    v = discord.ui.LayoutView(timeout=None)
    v.add_item(discord.ui.Container(
        discord.ui.Section(head, accessory=discord.ui.Thumbnail(member.display_avatar.url)),
        discord.ui.Separator(),
        discord.ui.TextDisplay(f'-# {footer}'),
        accent_colour=_LEAVE_ACCENT,
    ))
    return v


async def _get_member(guild, uid):
    m = guild.get_member(uid)
    if m is None:
        try:
            m = await guild.fetch_member(uid)
        except discord.NotFound:
            return None
    return m


async def _swap_roles(member, new_key):
    '''입장대기 제거 → new_key 역할 부여. 반환: 경고 문구(없으면 '')'''
    guild = member.guild
    wait_id, new_id = get_role_id(guild.id, '입장대기'), get_role_id(guild.id, new_key)
    wait_role = guild.get_role(wait_id) if wait_id else None
    new_role = guild.get_role(new_id) if new_id else None
    if new_role is None:
        return f'\n⚠️ `{new_key}` 역할 매핑이 없어 역할은 바꾸지 못했어요 (`/역할설정` 필요)'
    try:
        if wait_role and wait_role in member.roles:
            await member.remove_roles(wait_role, reason='입장 승인')
        await member.add_roles(new_role, reason='입장 승인')
    except discord.Forbidden:
        return f'\n⚠️ 역할 변경 실패 — 봇 역할을 입장대기·{new_key}보다 위로 올려주세요'
    return ''


# ── 길드원 승인: 닉네임 팝업 ────────────────────────────────
class NicknameModal(discord.ui.Modal):
    닉네임 = discord.ui.TextInput(label='테런 닉네임', placeholder='예: 카카오톡', max_length=32)

    def __init__(self, member, number, message):
        super().__init__(title=f'길드원 승인 — {member.display_name}'[:45])
        self.member_id, self.number, self.message = member.id, number, message

    async def on_submit(self, interaction: discord.Interaction):
        nick = str(self.닉네임).strip()
        await interaction.response.defer(ephemeral=True)
        if self.member_id in _busy:
            await interaction.followup.send('다른 관리자가 승인 처리 중이에요.', ephemeral=True)
            return
        _busy.add(self.member_id)
        try:
            member = await _get_member(interaction.guild, self.member_id)
            if member is None:
                await interaction.followup.send('서버에 없는 사용자예요 (이미 나갔을 수 있어요).', ephemeral=True)
                return
            ok, msg = await asyncio.to_thread(add_user, nick, member.id)   # /가입 과 동일한 시트 등록
            if not ok:
                await interaction.followup.send(msg, ephemeral=True)   # 중복 닉 등 → 카드는 그대로
                return
            note = await _swap_roles(member, '일반')
            try:
                await member.edit(nick=nick, reason='길드원 승인')
            except discord.Forbidden:
                note += '\n⚠️ 서버 닉네임 변경 실패 — 서버 소유자이거나 봇보다 높은 역할이에요'
            try:
                await self.message.edit(view=_done_card(member, self.number, '길드원', interaction.user),
                                        allowed_mentions=discord.AllowedMentions.none())
            except Exception as e:
                print(f'[welcome] 카드 갱신 실패: {e}')
            await interaction.followup.send(msg + note, ephemeral=True)
        finally:
            _busy.discard(self.member_id)


class MemberApproveButton(discord.ui.DynamicItem[discord.ui.Button],
                          template=r'wc_member:(?P<uid>[0-9]+):(?P<n>[0-9]+)'):
    def __init__(self, uid: int, number: int):
        super().__init__(discord.ui.Button(label='길드원 승인', style=discord.ButtonStyle.success,
                                           custom_id=f'wc_member:{uid}:{number}'))
        self.uid, self.number = uid, number

    @classmethod
    async def from_custom_id(cls, interaction, item, match):
        return cls(int(match['uid']), int(match['n']))

    async def callback(self, interaction: discord.Interaction):
        if not _is_staff(interaction):
            await interaction.response.send_message(_NO_PERM, ephemeral=True)
            return
        member = interaction.guild.get_member(self.uid)   # 팝업은 3초 안에 띄워야 해서 캐시만 조회
        if member is None:
            await interaction.response.send_message('서버에 없는 사용자예요 (이미 나갔을 수 있어요).',
                                                    ephemeral=True)
            return
        await interaction.response.send_modal(NicknameModal(member, self.number, interaction.message))


class GuestApproveButton(discord.ui.DynamicItem[discord.ui.Button],
                         template=r'wc_guest:(?P<uid>[0-9]+):(?P<n>[0-9]+)'):
    def __init__(self, uid: int, number: int):
        super().__init__(discord.ui.Button(label='게스트 승인', style=discord.ButtonStyle.secondary,
                                           custom_id=f'wc_guest:{uid}:{number}'))
        self.uid, self.number = uid, number

    @classmethod
    async def from_custom_id(cls, interaction, item, match):
        return cls(int(match['uid']), int(match['n']))

    async def callback(self, interaction: discord.Interaction):
        if not _is_staff(interaction):
            await interaction.response.send_message(_NO_PERM, ephemeral=True)
            return
        await interaction.response.defer(ephemeral=True, thinking=True)
        if self.uid in _busy:
            await interaction.followup.send('다른 관리자가 승인 처리 중이에요.', ephemeral=True)
            return
        _busy.add(self.uid)
        try:
            member = await _get_member(interaction.guild, self.uid)
            if member is None:
                await interaction.followup.send('서버에 없는 사용자예요 (이미 나갔을 수 있어요).', ephemeral=True)
                return
            note = await _swap_roles(member, '게스트')
            if note.startswith('\n⚠️ `게스트` 역할 매핑'):
                await interaction.followup.send(note.strip(), ephemeral=True)   # 역할을 못 줬으면 카드 유지
                return
            await interaction.message.edit(view=_done_card(member, self.number, '게스트', interaction.user),
                                           allowed_mentions=discord.AllowedMentions.none())
            await interaction.followup.send(f'{member.display_name}님을 게스트로 승인했어요.{note}',
                                            ephemeral=True)
        finally:
            _busy.discard(self.uid)


class Welcome(commands.Cog):
    def __init__(self, bot):
        self.bot = bot

    async def cog_load(self):
        self.bot.add_dynamic_items(MemberApproveButton, GuestApproveButton)   # 재시작 후에도 버튼 동작

    @commands.Cog.listener()
    async def on_member_join(self, member: discord.Member):
        if member.bot:
            return
        wait_id = get_role_id(member.guild.id, '입장대기')
        wait_role = member.guild.get_role(wait_id) if wait_id else None
        if wait_role:
            try:
                await member.add_roles(wait_role, reason='서버 입장')
            except Exception as e:
                print(f'[welcome] 입장대기 부여 실패({member}): {e}')
        channel = member.guild.get_channel(WELCOME_CHANNEL_ID)
        if channel is None:
            return   # 다른 서버 입장 등
        try:
            await channel.send(view=_welcome_card(member, member.guild.member_count or 0),
                               allowed_mentions=discord.AllowedMentions(users=[member]))
        except Exception as e:
            print(f'[welcome] 입장 카드 발송 실패: {e}')


    @commands.Cog.listener()
    async def on_member_remove(self, member: discord.Member):
        if member.bot:
            return
        channel = member.guild.get_channel(WELCOME_CHANNEL_ID)
        if channel is None:
            return
        try:
            await channel.send(view=_leave_card(member), allowed_mentions=discord.AllowedMentions.none())
        except Exception as e:
            print(f'[welcome] 퇴장 카드 발송 실패: {e}')


async def setup(bot):
    await bot.add_cog(Welcome(bot))
