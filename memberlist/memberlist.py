'''
* 길드원 목록 카드 (길드원관리 채널) — 등급별 명단 + 경고 횟수
*   버튼: [탈퇴](닉네임 팝업 → 확인 카드) [명예우수집계] [명예우수설정](명예·우수 정원 팝업) [🔄](갱신)
*   🔄 는 누구나, 나머지는 길마·서마. 탈퇴 완료 시 목록 자동 갱신.
* 봇 시작 시 길드원관리 채널에 카드를 자동 설치 (이미 있으면 그 카드를 최신으로 갱신)
* date   2026.10.30
'''
import time
import asyncio

import discord
from discord.ext import commands

from sheet.sheet import (get_dashboard_records, calc_honor_excellent, set_honor_excellent_cnt,
                         list_active_users)
from roleSetting.roleSetting import has_role, sync_rank_roles, rank_change_text
from config.guild_config import set_setting
from halloffame.halloffame import post_hall_of_fame, parse_ym, default_ym

MEMBER_CHANNEL_ID = 1565561284252930119   # 길드원관리 채널
TITLE = '<:emoji_13:1486906955011461261> 마커 길드원 목록'
_ACCENT = discord.Colour(0x3FB8F3)
_RANKS = [('길마', '👑'), ('서마', '🛡️'), ('명예', '🌟'), ('우수', '⭐'), ('일반', '🌱')]
# 닉네임 앞 등급 표시 — 서버 커스텀 이모지. 없는 등급은 글자 배지(`등급`)로 표시
RANK_BADGE = {
    '길마': '<:master:1566481276133703751>',
    '서마': '<:sub_master:1566481372254707923>',
    '명예': '<:honor:1566476386707832883>',
    '우수': '<:excellence:1566476337848520795>',
    '일반': '<:common:1566482086444142653>',
}
_NO_PERM = '길마/서마만 사용 가능합니다!'


def _is_staff(interaction):
    return has_role(interaction, '길마') or has_role(interaction, '서마')


def _int(v):
    try:
        return int(str(v).strip())
    except (ValueError, AttributeError):
        return 0


def _body(records):
    '''한 줄 목록 — 닉네임 앞에 등급 배지(인라인 코드), 등급 순서 → 닉 가나다. 경고 있는 사람만 ⚠️ 횟수.
       반환: (본문, 총원)'''
    users = records[0]
    groups = {}
    for r in users:
        r = (r + ['', '', ''])[:3]
        name, warn, rank = r[0].strip(), _int(r[1]), r[2].strip()
        if not name or rank == '탈퇴':
            continue
        groups.setdefault(rank or '미지정', []).append((name, warn))
    order = [k for k, _ in _RANKS] + sorted(k for k in groups if k not in dict(_RANKS))
    out, total = [], 0
    for rank in order:
        members = sorted(groups.get(rank, []))
        if not members:
            continue
        total += len(members)
        badge = RANK_BADGE.get(rank, f'`{rank}`')
        # '### ' 소제목 크기 — 디스코드는 글자와 함께 쓴 이모지를 글자 크기로 그려서, 줄 전체를 키워 이모지도 크게
        out += [f'### {badge} {discord.utils.escape_markdown(n)}' + (f'  ⚠️ {w}' if w > 0 else '')
                for n, w in members]
    text = '\n'.join(out) or '-# 등록된 길드원이 없어요'
    if len(text) > 3500:
        text = text[:3480].rsplit('\n', 1)[0] + '\n… (이하 생략)'
    return text, total


# ── 카드 ────────────────────────────────────────────────────
class _Buttons(discord.ui.ActionRow):
    @discord.ui.button(label='탈퇴', style=discord.ButtonStyle.danger, custom_id='ml_leave')
    async def leave(self, interaction: discord.Interaction, button: discord.ui.Button):
        if not _is_staff(interaction):
            await interaction.response.send_message(_NO_PERM, ephemeral=True)
            return
        await interaction.response.send_modal(LeaveModal(interaction.message))

    @discord.ui.button(label='명예우수집계', style=discord.ButtonStyle.secondary, custom_id='ml_calc')
    async def calc(self, interaction: discord.Interaction, button: discord.ui.Button):
        if not _is_staff(interaction):
            await interaction.response.send_message(_NO_PERM, ephemeral=True)
            return
        # 집계는 되돌릴 수 없으니, 명예의 전당 연월을 먼저 팝업으로 받고 형식이 맞을 때만 집계
        await interaction.response.send_modal(CalcModal(interaction.message))

    @discord.ui.button(label='명예우수설정', style=discord.ButtonStyle.primary, custom_id='ml_set')
    async def set_cnt(self, interaction: discord.Interaction, button: discord.ui.Button):
        if not _is_staff(interaction):
            await interaction.response.send_message(_NO_PERM, ephemeral=True)
            return
        cur = {}
        try:   # 현재 정원 미리 채우기 (캐시만 짧게 기다림 — 팝업은 3초 안에)
            ranks = (await asyncio.wait_for(asyncio.to_thread(get_dashboard_records), 1.5))[3]
            cur = {r[0]: r[1] for r in ranks if len(r) > 1}
        except Exception:
            pass
        await interaction.response.send_modal(RankCntModal(cur.get('명예', ''), cur.get('우수', '')))

    @discord.ui.button(label='🔄', style=discord.ButtonStyle.secondary, custom_id='ml_refresh')
    async def refresh(self, interaction: discord.Interaction, button: discord.ui.Button):
        await interaction.response.defer()
        records = await asyncio.to_thread(get_dashboard_records)
        await interaction.edit_original_response(view=MemberListCard(records))


class MemberListCard(discord.ui.LayoutView):
    '''records 없으면 영속 등록용(버튼만)'''
    def __init__(self, records=None):
        super().__init__(timeout=None)
        if records is None:
            self.add_item(discord.ui.Container(_Buttons()))
            return
        body, total = _body(records)
        self.add_item(discord.ui.Container(
            discord.ui.TextDisplay(f'## {TITLE}'),
            discord.ui.Separator(),
            discord.ui.TextDisplay(body),
            discord.ui.Separator(),
            _Buttons(),
            discord.ui.TextDisplay(f'-# 총 {total}명 · <t:{int(time.time())}:f> 기준'),
            accent_colour=_ACCENT,
        ))


async def _refresh(message):
    try:
        records = await asyncio.to_thread(get_dashboard_records)
        await message.edit(view=MemberListCard(records))
    except Exception as e:
        print(f'[memberlist] 갱신 실패: {e}')


# ── 팝업 ────────────────────────────────────────────────────
class LeaveModal(discord.ui.Modal, title='길드원 탈퇴'):
    닉네임 = discord.ui.TextInput(label='탈퇴할 테런 닉네임', placeholder='예: 카카오톡', max_length=32)

    def __init__(self, card_message):
        super().__init__()
        self.card_message = card_message

    async def on_submit(self, interaction: discord.Interaction):
        from user.user import ConfirmDeleteView   # 지역 import — 확인 카드 재사용 (/탈퇴 와 동일)
        name = str(self.닉네임).strip()
        await interaction.response.defer(ephemeral=True, thinking=True)
        if name not in await asyncio.to_thread(list_active_users):
            await interaction.followup.send(f'{name}은(는) 존재하지 않는 유저입니다!', ephemeral=True)
            return
        view = ConfirmDeleteView(name)
        await interaction.followup.send(view=view, ephemeral=True)
        view.origin = interaction

        async def _after():   # 확인/취소/시간초과로 카드가 닫히면 목록 갱신
            await view.wait()
            await _refresh(self.card_message)
        asyncio.create_task(_after())


class CalcModal(discord.ui.Modal, title='명예/우수 집계'):
    연월 = discord.ui.TextInput(label='명예의 전당 연월', placeholder='예: 2026-11 또는 11', max_length=10)

    def __init__(self, card_message):
        super().__init__()
        self.card_message = card_message
        y, m = default_ym()
        self.연월.default = f'{y}-{m:02d}'

    async def on_submit(self, interaction: discord.Interaction):
        ym = parse_ym(str(self.연월))
        if ym is None:
            await interaction.response.send_message('연월 형식 오류예요. 예: `2026-11` 또는 `11` (집계는 하지 않았어요)',
                                                    ephemeral=True)
            return
        await interaction.response.defer(ephemeral=True, thinking=True)
        # 미리보기: 시트에 쓰지 않고 결과만 계산 → [확정] 을 눌러야 실제 반영
        ok, honor, excel, n, changes = await asyncio.to_thread(calc_honor_excellent, False)
        if not ok:
            await interaction.followup.send('시트 오류로 미리보기를 만들지 못했어요.', ephemeral=True)
            return
        view = PreviewView(self.card_message, ym, honor, excel, n, changes)
        await interaction.followup.send(view=view, ephemeral=True)


def _preview_text(ym, honor, excel, n, changes):
    lines = [f'## 🔍 명예/우수 집계 미리보기',
             f'-# {ym[0]}년 {ym[1]}월 명예의 전당 · 아직 반영되지 않았어요']
    body = [f'**명예** ({len(honor)}): ' + (', '.join(honor) or '없음'),
            f'**우수** ({len(excel)}): ' + (', '.join(excel) or '없음')]
    chg = ['**등급 변경 예정**'] + ([f'- {nm} {o or "미지정"} → {w}' for nm, _d, o, w in changes] or ['- 없음'])
    note = f'-# [확정] 하면 포인트 {n}개가 반영 마감(Y)되고 등급·역할이 바뀌며 명예의 전당이 올라가요. 되돌릴 수 없어요.'
    return '\n'.join(lines), '\n'.join(body), '\n'.join(chg)[:1500], note


class _PreviewButtons(discord.ui.ActionRow):
    def __init__(self, owner):
        super().__init__()
        self._owner = owner

    @discord.ui.button(label='확정', emoji='✅', style=discord.ButtonStyle.success)
    async def confirm(self, interaction: discord.Interaction, button: discord.ui.Button):
        o = self._owner
        if o.done:
            await interaction.response.send_message('이미 처리됐어요.', ephemeral=True)
            return
        o.done = True
        o.stop()
        await interaction.response.edit_message(view=_plain('⏳ 집계 반영 중…'))
        ok, honor, excel, n, changes = await asyncio.to_thread(calc_honor_excellent)
        if not ok:
            await interaction.edit_original_response(view=_plain('⚠️ 시트 오류로 처리하지 못했어요. 다시 시도해주세요.'))
            return
        fails = await sync_rank_roles(interaction.guild, changes)   # 디스코드 역할도 같은 등급으로
        names = lambda xs: [x.rsplit(' (', 1)[0] for x in xs]       # '닉 (30점)' → '닉'
        posted, hall_msg = await post_hall_of_fame(interaction.guild, o.ym[0], o.ym[1], names(honor), names(excel))
        text = ('## 명예/우수 집계 결과\n'
                '**명예**: ' + (', '.join(honor) or '없음') + '\n'
                '**우수**: ' + (', '.join(excel) or '없음') +
                rank_change_text(changes, fails) + '\n'
                f'-# {n}개 포인트 반영 완료 (reflection_yn → Y)\n'
                + ('🏆 ' if posted else '⚠️ ') + hall_msg
                + ('' if posted else ' — `/명예의전당` 으로 다시 올릴 수 있어요'))
        await interaction.edit_original_response(view=_plain(text[:3900]))
        if changes:
            await _refresh(o.card_message)   # 목록 카드 등급 갱신

    @discord.ui.button(label='취소', style=discord.ButtonStyle.secondary)
    async def cancel(self, interaction: discord.Interaction, button: discord.ui.Button):
        self._owner.done = True
        self._owner.stop()
        await interaction.response.edit_message(view=_plain('취소했어요. 아무것도 반영되지 않았어요.'))


def _plain(text):
    v = discord.ui.LayoutView(timeout=None)
    v.add_item(discord.ui.Container(discord.ui.TextDisplay(text), accent_colour=_ACCENT))
    return v


class PreviewView(discord.ui.LayoutView):
    '''명예우수집계 미리보기 카드 (본인만 보임) — [확정] 시 실제 집계·등급·역할·명예의 전당'''
    def __init__(self, card_message, ym, honor, excel, n, changes):
        super().__init__(timeout=600)
        self.card_message, self.ym, self.done = card_message, ym, False
        head, body, chg, note = _preview_text(ym, honor, excel, n, changes)
        self.add_item(discord.ui.Container(
            discord.ui.TextDisplay(head), discord.ui.Separator(),
            discord.ui.TextDisplay(body), discord.ui.Separator(),
            discord.ui.TextDisplay(chg), discord.ui.Separator(),
            discord.ui.TextDisplay(note), _PreviewButtons(self),
            accent_colour=_ACCENT))


class RankCntModal(discord.ui.Modal, title='명예/우수 정원 설정'):
    명예 = discord.ui.TextInput(label='명예 인원', placeholder='예: 3', max_length=4)
    우수 = discord.ui.TextInput(label='우수 인원', placeholder='예: 5', max_length=4)

    def __init__(self, honor='', excel=''):
        super().__init__()
        if str(honor).strip():
            self.명예.default = str(honor).strip()
        if str(excel).strip():
            self.우수.default = str(excel).strip()

    async def on_submit(self, interaction: discord.Interaction):
        try:
            honor, excel = int(str(self.명예).strip()), int(str(self.우수).strip())
            if honor < 0 or excel < 0:
                raise ValueError
        except ValueError:
            await interaction.response.send_message('0 이상의 숫자로 입력해주세요.', ephemeral=True)
            return
        await interaction.response.defer(ephemeral=True, thinking=True)
        ok, msg = await asyncio.to_thread(set_honor_excellent_cnt, honor, excel)
        await interaction.followup.send(('✅ ' if ok else '⚠️ ') + msg, ephemeral=True)


class MemberList(commands.Cog):
    def __init__(self, bot):
        self.bot = bot

    async def cog_load(self):
        self.bot.add_view(MemberListCard())   # 재시작 후에도 버튼 동작

    # 봇 시작 시 자동 설치: 채널에 이미 목록 카드가 있으면 그걸 갱신(새로 올리지 않음), 없을 때만 게시.
    #   저장된 메시지 ID 가 없어도(설정 파일 교체 등) 채널 최근 메시지에서 봇의 목록 카드를 찾아 재사용하고,
    #   예전에 중복으로 올라간 목록 카드는 정리한다.
    @commands.Cog.listener()
    async def on_ready(self):
        if getattr(self, '_installed', False):
            return
        self._installed = True
        try:
            channel = self.bot.get_channel(MEMBER_CHANNEL_ID) or await self.bot.fetch_channel(MEMBER_CHANNEL_ID)
            records = await asyncio.to_thread(get_dashboard_records)
            cards = [m async for m in channel.history(limit=100)
                     if m.author.id == self.bot.user.id and _is_list_card(m)]   # 최신순
            if cards:
                keep = cards[0]
                for old in cards[1:]:
                    try:
                        await old.delete()
                    except Exception:
                        pass
                await keep.edit(view=MemberListCard(records))
            else:
                keep = await channel.send(view=MemberListCard(records))
                print('[memberlist] 길드원 목록 카드 설치')
            set_setting(channel.guild.id, '_member_ch', MEMBER_CHANNEL_ID)
            set_setting(channel.guild.id, '_member_msg', keep.id)
        except Exception as e:
            print(f'[memberlist] 자동 설치 실패: {e}')


def _is_list_card(message):
    '''봇 메시지가 길드원 목록 카드인지 (🔄 버튼 custom_id 로 판별)'''
    def walk(cs):
        for c in cs or []:
            yield c
            yield from walk(getattr(c, 'children', None))
    return any(getattr(c, 'custom_id', None) == 'ml_refresh' for c in walk(message.components))


async def setup(bot):
    await bot.add_cog(MemberList(bot))
