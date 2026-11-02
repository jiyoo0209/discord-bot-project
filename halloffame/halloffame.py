'''
* 명예의 전당 — 명예의전당 채널에 우주 배경 이미지(render.py)로 이달의 명예/우수 게시
*   - 길드원목록 [명예우수집계] 때 입력한 연월로 자동 게시 (memberlist 에서 post_hall_of_fame 호출)
*   - /명예의전당 [연월] : 지금 user 시트의 명예/우수 등급으로 다시 게시 (자동 게시가 실패했을 때 등)
*   필요: Pillow (pip install pillow)
* date   2026.11.02
'''
import io
import re
import asyncio

import discord
from discord import app_commands
from discord.ext import commands

from sheet.sheet import get_dashboard_records
from roleSetting.roleSetting import has_role
from util.dates import kst_today

HALL_CHANNEL_ID = 1566606856003129534


def default_ym(today=None):
    '''기본 연월 — 15일 이후면 다음 달(일찍 집계하는 경우), 아니면 이번 달'''
    t = today or kst_today()
    y, m = t.year, t.month
    if t.day >= 15:
        y, m = (y + 1, 1) if m == 12 else (y, m + 1)
    return y, m


def parse_ym(text, today=None):
    ''''2026-11' · '2026.11' · '202611' · '2026년 11월' · '11' · '11월' → (연, 월). 형식 오류면 None.
       월만 쓰면 올해 기준, 단 지금보다 6개월 넘게 이전 달이면 내년으로 (12월에 '1' → 내년 1월).'''
    t = today or kst_today()
    nums = re.findall(r'\d+', text or '')
    if len(nums) == 1 and len(nums[0]) == 6:
        nums = [nums[0][:4], nums[0][4:]]
    if len(nums) == 2 and len(nums[0]) == 4:
        y, m = int(nums[0]), int(nums[1])
    elif len(nums) == 1 and len(nums[0]) <= 2:
        y, m = t.year, int(nums[0])
        if t.month - m > 6:
            y += 1
    else:
        return None
    return (y, m) if 1 <= m <= 12 and 2000 <= y <= 2100 else None


def ym_title(y, m):
    return f'{y}년 {m}월 명예의 전당'


async def _avatar_bytes(guild, discord_id):
    if not str(discord_id).isdigit():
        return None
    member = guild.get_member(int(discord_id))
    if member is None:
        try:
            member = await guild.fetch_member(int(discord_id))
        except Exception:
            return None
    try:
        return await member.display_avatar.replace(size=256, format='png').read()
    except Exception:
        return None


async def post_hall_of_fame(guild, y, m, honor_names, excel_names):
    '''명예/우수 닉네임 목록으로 이미지를 그려 명예의전당 채널에 게시. 반환: (성공 여부, 안내 문구)'''
    try:
        from halloffame.render import render   # Pillow 가 없으면 여기서만 실패 (봇 전체는 정상)
    except ImportError:
        return False, '명예의 전당 이미지에 필요한 Pillow 가 설치되지 않았어요 (`pip install pillow`)'
    try:
        users = (await asyncio.to_thread(get_dashboard_records))[0]
        did_of = {r[0].strip(): (r[3].strip() if len(r) > 3 else '') for r in users if r and r[0].strip()}

        async def pack(names):
            return [(n, await _avatar_bytes(guild, did_of.get(n, ''))) for n in names]
        honor, excel = await pack(honor_names), await pack(excel_names)
        png = await asyncio.to_thread(render, ym_title(y, m), honor, excel)
        channel = guild.get_channel(HALL_CHANNEL_ID) or await guild.fetch_channel(HALL_CHANNEL_ID)
        await channel.send(file=discord.File(io.BytesIO(png), filename=f'hall_of_fame_{y}{m:02d}.png'))
        return True, f'<#{HALL_CHANNEL_ID}> 에 {ym_title(y, m)}을 올렸어요.'
    except Exception as e:
        print(f'[halloffame] 게시 실패: {e}')
        return False, f'명예의 전당 게시 실패: {e}'


def current_ranks(users):
    '''user 시트의 지금 명예/우수 등급 멤버 (닉 가나다순)'''
    honor = sorted(r[0].strip() for r in users if len(r) > 2 and r[0].strip() and r[2].strip() == '명예')
    excel = sorted(r[0].strip() for r in users if len(r) > 2 and r[0].strip() and r[2].strip() == '우수')
    return honor, excel


class HallOfFame(commands.Cog):
    def __init__(self, bot):
        self.bot = bot

    @app_commands.command(name='명예의전당', description='(길마/서마) 지금 명예/우수 등급으로 명예의 전당 이미지 게시')
    @app_commands.describe(연월='예: 2026-11 또는 11 (비우면 15일 이후는 다음 달, 그 전은 이번 달)')
    @app_commands.guild_only()
    async def hall(self, interaction: discord.Interaction, 연월: str = None):
        if not (has_role(interaction, '길마') or has_role(interaction, '서마')):
            await interaction.response.send_message('길마/서마만 사용 가능합니다!', ephemeral=True)
            return
        ym = parse_ym(연월) if 연월 else default_ym()
        if ym is None:
            await interaction.response.send_message('연월 형식 오류예요. 예: `2026-11` 또는 `11`', ephemeral=True)
            return
        await interaction.response.defer(ephemeral=True, thinking=True)
        users = (await asyncio.to_thread(get_dashboard_records))[0]
        honor, excel = current_ranks(users)
        ok, msg = await post_hall_of_fame(interaction.guild, ym[0], ym[1], honor, excel)
        await interaction.followup.send(msg, ephemeral=True)


async def setup(bot):
    await bot.add_cog(HallOfFame(bot))
