'''
* 길드원 목록 카드 (길드원관리 채널) — 등급별 명단 + 경고 횟수
*   버튼: [탈퇴](닉네임 팝업 → 확인 카드) [명예우수집계] [명예우수설정](명예·우수 정원 팝업) [🔄](갱신)
*   🔄 는 누구나, 나머지는 길마·서마. 탈퇴 완료 시 목록 자동 갱신.
* + /길드원목록설치 : 지금 채널에 목록 카드 설치 (이전 카드는 삭제)
* date   2026.10.30
'''
import time
import asyncio

import discord
from discord import app_commands
from discord.ext import commands

from sheet.sheet import (get_dashboard_records, calc_honor_excellent, set_honor_excellent_cnt,
                         list_active_users)
from roleSetting.roleSetting import has_role
from config.guild_config import get_setting, set_setting

TITLE = '<:emoji_13:1486906955011461261> 마커 길드원 목록'
_ACCENT = discord.Colour(0x3FB8F3)
_RANKS = [('길마', '👑'), ('서마', '🛡️'), ('명예', '🌟'), ('우수', '⭐'), ('일반', '🌱')]
_NO_PERM = '길마/서마만 사용 가능합니다!'


def _is_staff(interaction):
    return has_role(interaction, '길마') or has_role(interaction, '서마')


def _int(v):
    try:
        return int(str(v).strip())
    except (ValueError, AttributeError):
        return 0


def _body(records):
    '''등급별 명단 (등급 순서 → 닉 가나다). 경고 있는 사람만 ⚠️ 횟수. 반환: (본문, 총원)'''
    users = records[0]
    groups = {}
    for r in users:
        r = (r + ['', '', ''])[:3]
        name, warn, rank = r[0].strip(), _int(r[1]), r[2].strip()
        if not name or rank == '탈퇴':
            continue
        groups.setdefault(rank or '미지정', []).append((name, warn))
    order = [k for k, _ in _RANKS] + sorted(k for k in groups if k not in dict(_RANKS))
    emoji = dict(_RANKS)
    out, total = [], 0
    for rank in order:
        members = sorted(groups.get(rank, []))
        if not members:
            continue
        total += len(members)
        out.append(f'**{emoji.get(rank, "•")} {rank}** ({len(members)})')
        out += [f'- {discord.utils.escape_markdown(n)}' + (f'  ⚠️ {w}' if w > 0 else '') for n, w in members]
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
        await interaction.response.defer(ephemeral=True, thinking=True)
        ok, honor, excel, n = await asyncio.to_thread(calc_honor_excellent)
        if not ok:
            await interaction.followup.send('시트 오류로 처리하지 못했어요.', ephemeral=True)
            return
        await interaction.followup.send(
            '## 명예/우수 집계 결과\n'
            '**명예**: ' + (', '.join(honor) or '없음') + '\n'
            '**우수**: ' + (', '.join(excel) or '없음') + '\n'
            f'-# {n}개 포인트 반영 완료 (reflection_yn → Y)', ephemeral=True)

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

    @app_commands.command(name='길드원목록설치', description='(길마/서마) 이 채널에 길드원 목록 카드 설치')
    @app_commands.guild_only()
    async def install(self, interaction: discord.Interaction):
        if not _is_staff(interaction):
            await interaction.response.send_message(_NO_PERM, ephemeral=True)
            return
        await interaction.response.defer(ephemeral=True)
        old_ch, old_msg = get_setting(interaction.guild.id, '_member_ch'), get_setting(interaction.guild.id, '_member_msg')
        if old_ch and old_msg:   # 이전 카드 정리
            try:
                ch = interaction.guild.get_channel(int(old_ch)) or await self.bot.fetch_channel(int(old_ch))
                await (await ch.fetch_message(int(old_msg))).delete()
            except Exception:
                pass
        try:
            records = await asyncio.to_thread(get_dashboard_records)
            msg = await interaction.channel.send(view=MemberListCard(records))
        except Exception as e:
            await interaction.followup.send(f'설치 실패: {e}', ephemeral=True)
            return
        set_setting(interaction.guild.id, '_member_ch', interaction.channel.id)
        set_setting(interaction.guild.id, '_member_msg', msg.id)
        await interaction.followup.send('길드원 목록 카드를 설치했어요. 🔄 로 갱신할 수 있어요.', ephemeral=True)


async def setup(bot):
    await bot.add_cog(MemberList(bot))
